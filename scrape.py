import re
import requests
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone

# ---------------- CONFIG ----------------

URL = "https://www.teleamazonas.com/programacion/"
CHANNEL_ID = "teleamazonas.ec"
CHANNEL_NAME = "Teleamazonas"
OUT_FILE = "teleamazonas.xml"

# Ecuador time (UTC-5, no DST)
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

# Date header like: "Martes, 13 de enero de 2026"
DATE_RE = re.compile(
    r"(?P<day>\d{1,2})\s+de\s+(?P<month>[a-záéíóúñ]+)\s+de\s+(?P<year>\d{4})",
    re.IGNORECASE,
)

# Times like 19:00 or 9:00
TIME_24_RE = re.compile(r"^(?:\d|[01]\d|2[0-3]):[0-5]\d$")


# ---------------- HELPERS ----------------

def fetch_html() -> str:
    r = requests.get(URL, headers=HEADERS, timeout=25)
    r.raise_for_status()
    return r.text


def parse_page_date(tokens: list[str]) -> datetime.date:
    """
    Find the page's displayed date in Spanish (e.g., "13 de enero de 2026")
    and return it as a date object. We ignore the weekday name.
    """
    for t in tokens:
        m = DATE_RE.search(t.lower())
        if m:
            day = int(m.group("day"))
            month_name = m.group("month").lower()
            year = int(m.group("year"))
            month = MONTHS_ES.get(month_name)
            if not month:
                continue
            return datetime(year, month, day, tzinfo=TZ_EC).date()
    # Fallback: use Ecuador "today" if header not found
    return datetime.now(TZ_EC).date()


def clean_title(title: str) -> str:
    title = re.sub(r"\s+", " ", title).strip()
    # drop obvious non-show labels
    if title.lower() in {
        "quito", "guayaquil",
        "lunes", "martes", "miércoles", "miercoles", "jueves",
        "viernes", "sábado", "sabado", "domingo",
        "programación", "programacion", "parrilla", "parrilla de programación", "parrilla de programacion"
    }:
        return ""
    return title


def extract_time_title_pairs(tokens: list[str]) -> list[tuple[str, str]]:
    """
    Walk tokens; when token is a time, next non-time token is the title.
    """
    pairs: list[tuple[str, str]] = []
    i = 0
    while i < len(tokens) - 1:
        t = tokens[i]
        if TIME_24_RE.match(t):
            # find next non-time token as title
            j = i + 1
            while j < len(tokens) and TIME_24_RE.match(tokens[j]):
                j += 1
            if j < len(tokens):
                title = clean_title(tokens[j])
                if title:
                    pairs.append((t, title))
            i = j + 1
        else:
            i += 1

    # Deduplicate while preserving order
    seen = set()
    out = []
    for tm, ti in pairs:
        key = (tm, ti)
        if key not in seen:
            seen.add(key)
            out.append((tm, ti))
    return out


def build_programmes(tv: ET.Element, base_date: datetime.date, pairs: list[tuple[str, str]]):
    """
    Build XMLTV programmes, handling midnight rollover.
    Times are treated as Ecuador time and written with -0500 offset.
    """
    current_date = base_date
    prev_minutes = None
    starts: list[tuple[datetime, str]] = []

    for hhmm, title in pairs:
        hh, mm = map(int, hhmm.split(":"))
        minutes = hh * 60 + mm

        # Midnight rollover: if times go backward, we moved to next date
        if prev_minutes is not None and minutes < prev_minutes:
            current_date = current_date + timedelta(days=1)

        dt = datetime(
            current_date.year, current_date.month, current_date.day,
            hh, mm, tzinfo=TZ_EC
        )
        starts.append((dt, title))
        prev_minutes = minutes

    for idx, (start_dt, title) in enumerate(starts):
        stop_dt = starts[idx + 1][0] if idx + 1 < len(starts) else start_dt + timedelta(minutes=30)
        prog = ET.SubElement(tv, "programme", channel=CHANNEL_ID)
        prog.set("start", start_dt.strftime("%Y%m%d%H%M%S %z"))
        prog.set("stop", stop_dt.strftime("%Y%m%d%H%M%S %z"))
        ET.SubElement(prog, "title").text = title


def main():
    html = fetch_html()
    soup = BeautifulSoup(html, "html.parser")
    tokens = [s.strip() for s in soup.stripped_strings if s and s.strip()]

    page_date = parse_page_date(tokens)
    pairs = extract_time_title_pairs(tokens)

    print(f"Page date parsed: {page_date.isoformat()}")
    print(f"Programmes extracted: {len(pairs)}")

    tv = ET.Element("tv", attrib={"generator-info-name": "teleamazonas-site"})

    ch = ET.SubElement(tv, "channel", id=CHANNEL_ID)
    ET.SubElement(ch, "display-name").text = CHANNEL_NAME

    build_programmes(tv, page_date, pairs)

    ET.ElementTree(tv).write(OUT_FILE, encoding="utf-8", xml_declaration=True)


if __name__ == "__main__":
    main()
