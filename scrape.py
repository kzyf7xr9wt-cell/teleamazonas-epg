import re
import requests
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone

URL = "https://www.teleamazonas.com/programacion/"
OUT_FILE = "teleamazonas.xml"

CHANNEL_ID = "teleamazonas.ec"
CHANNEL_NAME = "Teleamazonas"

# Ecuador time (UTC-5)
TZ_EC = timezone(timedelta(hours=-5))

HEADERS = {"User-Agent": "Mozilla/5.0 (teleamazonas-epg)"}

# Spanish month mapping
MONTHS_ES = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}

# Matches "14 de enero de 2026" anywhere in a line
DATE_RE = re.compile(
    r"(?P<day>\d{1,2})\s+de\s+(?P<month>[a-záéíóúñ]+)\s+de\s+(?P<year>\d{4})",
    re.IGNORECASE,
)

# Times like 05:00, 21:30, 9:30
TIME_RE = re.compile(r"^(?:\d|[01]\d|2[0-3]):[0-5]\d$")


def fetch_html() -> str:
    r = requests.get(URL, headers=HEADERS, timeout=25)
    r.raise_for_status()
    return r.text


def html_to_lines(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n")
    lines = [ln.strip() for ln in text.splitlines()]
    return [ln for ln in lines if ln]


def parse_page_date(lines: list[str]):
    # Find first "DD de <mes> de YYYY"
    for ln in lines:
        m = DATE_RE.search(ln.lower())
        if m:
            day = int(m.group("day"))
            month_name = m.group("month").lower()
            year = int(m.group("year"))
            month = MONTHS_ES.get(month_name)
            if month:
                return datetime(year, month, day, tzinfo=TZ_EC).date()
    # Fallback (should be rare)
    return datetime.now(TZ_EC).date()


def extract_pairs_from_parrilla(lines: list[str]) -> list[tuple[str, str]]:
    # Start from the parrilla section to avoid other page junk
    start_idx = None
    for i, ln in enumerate(lines):
        if "PARRILLA DE PROGRAM" in ln.upper():
            start_idx = i
            break
    if start_idx is None:
        return []

    chunk = lines[start_idx:start_idx + 2500]

    pairs = []
    i = 0
    while i < len(chunk) - 1:
        if TIME_RE.match(chunk[i]):
            t = chunk[i]
            j = i + 1
            # title is the next non-time line
            while j < len(chunk) and TIME_RE.match(chunk[j]):
                j += 1
            if j < len(chunk):
                title = chunk[j].strip()
                # Skip obvious non-show junk
                bad = {"quito", "guayaquil", "lunes", "martes", "miércoles", "miercoles",
                       "jueves", "viernes", "sábado", "sabado", "domingo"}
                if title and title.lower() not in bad:
                    pairs.append((t, title))
            i = j + 1
        else:
            i += 1

    # Deduplicate while preserving order
    seen = set()
    out = []
    for t, title in pairs:
        key = (t, title)
        if key not in seen:
            seen.add(key)
            out.append((t, title))
    return out


def build_programmes(tv: ET.Element, base_date, pairs: list[tuple[str, str]]):
    """
    Builds programme entries using Ecuador time and handles midnight rollover:
    if times go backward, we move to next day.
    """
    current_date = base_date
    prev_minutes = None
    starts: list[tuple[datetime, str]] = []

    for hhmm, title in pairs:
        hh, mm = map(int, hhmm.split(":"))
        minutes = hh * 60 + mm

        # midnight rollover
        if prev_minutes is not None and minutes < prev_minutes:
            current_date = current_date + timedelta(days=1)

        start_dt = datetime(
            current_date.year, current_date.month, current_date.day,
            hh, mm, tzinfo=TZ_EC
        )
        starts.append((start_dt, title))
        prev_minutes = minutes

    for idx, (start_dt, title) in enumerate(starts):
        stop_dt = starts[idx + 1][0] if idx + 1 < len(starts) else start_dt + timedelta(minutes=30)
        prog = ET.SubElement(tv, "programme", channel=CHANNEL_ID)
        prog.set("start", start_dt.strftime("%Y%m%d%H%M%S %z"))
        prog.set("stop", stop_dt.strftime("%Y%m%d%H%M%S %z"))
        ET.SubElement(prog, "title").text = title


def main():
    html = fetch_html()
    lines = html_to_lines(html)

    page_date = parse_page_date(lines)
    pairs = extract_pairs_from_parrilla(lines)

    print(f"Page date parsed: {page_date.isoformat()}")
    print(f"Programmes extracted: {len(pairs)}")

    tv = ET.Element("tv", attrib={"generator-info-name": "teleamazonas-parrilla"})
    ch = ET.SubElement(tv, "channel", id=CHANNEL_ID)
    ET.SubElement(ch, "display-name").text = CHANNEL_NAME

    build_programmes(tv, page_date, pairs)

    ET.ElementTree(tv).write(OUT_FILE, encoding="utf-8", xml_declaration=True)


if __name__ == "__main__":
    main()
