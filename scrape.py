import re
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

URL = "https://www.teleamazonas.com/programacion/"

CHANNELS = [
    ("teleamazonas.ec.quito", "Teleamazonas (Quito)"),
    ("teleamazonas.ec.guayaquil", "Teleamazonas (Guayaquil)"),
]

TZ_EC = timezone(timedelta(hours=-5))  # Ecuador is typically UTC-5


def extract_time_title_pairs(page_text: str):
    """
    Pulls (HH:MM, Title) pairs from the 'PARRILLA DE PROGRAMACIÓN' section.
    """
    # Make a clean line list
    lines = [ln.strip() for ln in page_text.splitlines()]
    lines = [ln for ln in lines if ln]

    # Find the schedule section
    try:
        start_idx = lines.index("PARRILLA DE PROGRAMACIÓN")
    except ValueError:
        # Fallback if spacing/case changes slightly
        start_idx = next(
            (i for i, ln in enumerate(lines) if "PARRILLA DE PROGRAMACIÓN" in ln),
            None,
        )
        if start_idx is None:
            return []

    # Take a big slice after the header
    chunk = lines[start_idx : start_idx + 1200]

    pairs = []
    time_re = re.compile(r"^\d{2}:\d{2}$")

    i = 0
    while i < len(chunk) - 1:
        if time_re.match(chunk[i]):
            t = chunk[i]
            # title is usually the next non-time line
            j = i + 1
            while j < len(chunk) and (not chunk[j] or time_re.match(chunk[j])):
                j += 1
            if j < len(chunk):
                title = chunk[j]
                # ignore obvious non-titles
                if title not in {"Quito", "Guayaquil", "Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"}:
                    pairs.append((t, title))
            i = j + 1
        else:
            i += 1

    return pairs


def split_quito_guayaquil(pairs):
    """
    Teleamazonas page often contains Quito list then Guayaquil list.
    We'll split at the first occurrence of a clearly Guayaquil-tagged title.
    """
    split_idx = None
    for idx, (_, title) in enumerate(pairs):
        if "Guayaquil" in title:
            split_idx = idx
            break

    if split_idx is None:
        # If we can't find a split marker, treat as Quito-only and copy to both (better than empty)
        return pairs, pairs

    quito = pairs[:split_idx]
    guayaquil = pairs[split_idx:]
    return quito, guayaquil


def build_programmes(tv, channel_id, pairs, base_date):
    """
    Convert (HH:MM, Title) into XMLTV programme items with rolling date across midnight.
    """
    # Build datetimes
    starts = []
    current_date = base_date
    prev_minutes = None

    for hhmm, title in pairs:
        hh, mm = map(int, hhmm.split(":"))
        minutes = hh * 60 + mm

        if prev_minutes is not None and minutes < prev_minutes:
            # crossed midnight -> next day
            current_date += timedelta(days=1)

        dt = datetime(
            current_date.year, current_date.month, current_date.day, hh, mm, 0, tzinfo=TZ_EC
        )
        starts.append((dt, title))
        prev_minutes = minutes

    # Create programme entries (stop = next start; last gets +30min)
    for k, (dt, title) in enumerate(starts):
        if k + 1 < len(starts):
            stop = starts[k + 1][0]
        else:
            stop = dt + timedelta(minutes=30)

        prog = ET.SubElement(tv, "programme", channel=channel_id)
        prog.set("start", dt.strftime("%Y%m%d%H%M%S %z"))
        prog.set("stop", stop.strftime("%Y%m%d%H%M%S %z"))
        ET.SubElement(prog, "title").text = title


def main():
    r = requests.get(URL, timeout=25)
    r.raise_for_status()

    # Use soup-less approach: we only need text.
    text = r.text

    # Extract time/title pairs
    pairs = extract_time_title_pairs(text)
    quito_pairs, guayaquil_pairs = split_quito_guayaquil(pairs)

    # Base date = "today" in Ecuador time
    now_ec = datetime.now(TZ_EC)
    base_date = now_ec.date()
    base_date = datetime(base_date.year, base_date.month, base_date.day, tzinfo=TZ_EC)

    tv = ET.Element("tv", attrib={"generator-info-name": "teleamazonas-scraper"})

    # Channels
    for cid, display in CHANNELS:
        ch = ET.SubElement(tv, "channel", id=cid)
        ET.SubElement(ch, "display-name").text = display

    # Programmes
    build_programmes(tv, "teleamazonas.ec.quito", quito_pairs, base_date)
    build_programmes(tv, "teleamazonas.ec.guayaquil", guayaquil_pairs, base_date)

    tree = ET.ElementTree(tv)
    tree.write("teleamazonas.xml", encoding="utf-8", xml_declaration=True)


if __name__ == "__main__":
    main()
