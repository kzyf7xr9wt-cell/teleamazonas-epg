import re
import requests
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone

URL = "https://www.teleamazonas.com/programacion/"
TZ_EC = timezone(timedelta(hours=-5))

CH_QUITO = "teleamazonas.ec.quito"
CH_GUAYAQUIL = "teleamazonas.ec.guayaquil"

def html_to_lines(html: str):
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n")  # <-- KEY FIX: convert HTML to visible text
    lines = [ln.strip() for ln in text.splitlines()]
    return [ln for ln in lines if ln]

def extract_pairs(lines):
    # Find the schedule area
    start = None
    for i, ln in enumerate(lines):
        if "PARRILLA DE PROGRAMACIÓN" in ln:
            start = i
            break
    if start is None:
        return []

    chunk = lines[start:start+2500]
    time_re = re.compile(r"^\d{2}:\d{2}$")

    pairs = []
    i = 0
    while i < len(chunk) - 1:
        if time_re.match(chunk[i]):
            t = chunk[i]
            # next non-time line is title
            j = i + 1
            while j < len(chunk) and (time_re.match(chunk[j]) or chunk[j] == ""):
                j += 1
            if j < len(chunk):
                title = chunk[j]
                # Skip obvious non-titles
                if title not in {"Quito", "Guayaquil", "Programación", "Parrilla"}:
                    pairs.append((t, title))
            i = j + 1
        else:
            i += 1
    return pairs

def split_quito_guayaquil(pairs):
    # Split when Guayaquil-specific titles begin
    for idx, (_, title) in enumerate(pairs):
        if "Guayaquil" in title:
            return pairs[:idx], pairs[idx:]
    # fallback: if not found, copy same list to both
    return pairs, pairs

def add_programmes(tv, channel_id, pairs, base_date):
    # Build start datetimes and infer stops from next start
    starts = []
    current_date = base_date
    prev_minutes = None

    for hhmm, title in pairs:
        hh, mm = map(int, hhmm.split(":"))
        minutes = hh * 60 + mm
        if prev_minutes is not None and minutes < prev_minutes:
            current_date += timedelta(days=1)  # crossed midnight
        dt = datetime(current_date.year, current_date.month, current_date.day, hh, mm, tzinfo=TZ_EC)
        starts.append((dt, title))
        prev_minutes = minutes

    for k, (dt, title) in enumerate(starts):
        stop = starts[k+1][0] if k+1 < len(starts) else dt + timedelta(minutes=30)
        prog = ET.SubElement(tv, "programme", channel=channel_id)
        prog.set("start", dt.strftime("%Y%m%d%H%M%S %z"))
        prog.set("stop", stop.strftime("%Y%m%d%H%M%S %z"))
        ET.SubElement(prog, "title").text = title

def main():
    r = requests.get(URL, timeout=25, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()

    lines = html_to_lines(r.text)
    pairs = extract_pairs(lines)
    quito_pairs, guayaquil_pairs = split_quito_guayaquil(pairs)

    tv = ET.Element("tv", attrib={"generator-info-name": "teleamazonas-html"})

    # Channels
    ch = ET.SubElement(tv, "channel", id=CH_QUITO)
    ET.SubElement(ch, "display-name").text = "Teleamazonas (Quito)"
    ch = ET.SubElement(tv, "channel", id=CH_GUAYAQUIL)
    ET.SubElement(ch, "display-name").text = "Teleamazonas (Guayaquil)"

    today = datetime.now(TZ_EC).date()
    base_date = datetime(today.year, today.month, today.day, tzinfo=TZ_EC)

    # Programmes
    add_programmes(tv, CH_QUITO, quito_pairs, base_date)
    add_programmes(tv, CH_GUAYAQUIL, guayaquil_pairs, base_date)

    ET.ElementTree(tv).write("teleamazonas.xml", encoding="utf-8", xml_declaration=True)

if __name__ == "__main__":
    main()
