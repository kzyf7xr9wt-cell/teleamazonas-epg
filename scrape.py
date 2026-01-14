import re
import requests
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# ---------------- CONFIG ----------------

BASE_URL = "https://www.gatotv.com/canal/teleamazonas/"
CHANNEL_ID = "teleamazonas.ec"
CHANNEL_NAME = "Teleamazonas"
DAYS = 7

# Ecuador timezone (always UTC-5)
TZ_EC = timezone(timedelta(hours=-5))

# Your local timezone (adjust if ever needed)
LOCAL_TZ = ZoneInfo("America/New_York")

# Match HH:MM (24-hour format)
TIME_RE = re.compile(r"(?:[01]?\d|2[0-3]):[0-5]\d")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (EPG Bot)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# ---------------- HELPERS ----------------

def fetch_day_html(date_obj):
    date_str = date_obj.strftime("%Y-%m-%d")
    url = f"{BASE_URL}{date_str}"
    r = requests.get(url, headers=HEADERS, timeout=25)
    r.raise_for_status()
    return r.text


def clean_title(raw_title):
    """
    Remove duplicated prefixes and junk like:
    'AM AM Noticias' -> 'Noticias'
    'PM PM Sed de Venganza' -> 'Sed de Venganza'
    """
    title = raw_title.strip()

    # Remove multiple spaces
    title = re.sub(r"\s+", " ", title)

    # Remove leading AM / PM duplicates repeatedly
    while True:
        parts = title.split(" ")
        if len(parts) >= 2 and parts[0].lower() == parts[1].lower():
            title = " ".join(parts[1:])
        elif parts[0].lower() in ("am", "pm"):
            title = " ".join(parts[1:])
        else:
            break

    # Final cleanup
    title = title.strip()
    return title


def parse_schedule_from_html(html):
    """
    Flexible parser:
    - Searches table rows for any HH:MM
    - Extracts remaining text as title
    """
    soup = BeautifulSoup(html, "html.parser")
    schedule = []

    for tr in soup.find_all("tr"):
        row_text = tr.get_text(" ", strip=True)
        if not row_text:
            continue

        m = TIME_RE.search(row_text)
        if not m:
            continue

        time_str = m.group(0)

        # Remove all times from the row text
        title = TIME_RE.sub(" ", row_text)
        title = re.sub(r"\s+", " ", title).strip()

        # Filter obvious junk headers
        junk = ["Horarios", "Programación", "Hora", "Canal"]
        if any(j.lower() in title.lower() for j in junk):
            continue

        title = clean_title(title)

        if len(title) < 2:
            continue

        schedule.append((time_str, title))

    # Deduplicate while preserving order
    seen = set()
    clean = []
    for item in schedule:
        if item not in seen:
            seen.add(item)
            clean.append(item)

    return clean


def build_programmes(tv, date_obj, schedule):
    """
    Builds XMLTV programme entries.
    Handles midnight rollover and timezone conversion.
    """
    starts = []
    current_date = date_obj
    prev_minutes = None

    for time_str, title in schedule:
        hh, mm = map(int, time_str.split(":"))
        minutes = hh * 60 + mm

        # Detect midnight rollover
        if prev_minutes is not None and minutes < prev_minutes:
            current_date = current_date + timedelta(days=1)

        # Ecuador time
        start_ec = datetime(
            current_date.year,
            current_date.month,
            current_date.day,
            hh,
            mm,
            tzinfo=TZ_EC,
        )

        # Convert to local timezone for IPTV compatibility
        start_local = start_ec.astimezone(LOCAL_TZ)

        starts.append((start_local, title))
        prev_minutes = minutes

    for idx, (start_dt, title) in enumerate(starts):
        if idx + 1 < len(starts):
            stop_dt = starts[idx + 1][0]
        else:
            stop_dt = start_dt + timedelta(minutes=30)

        prog = ET.SubElement(tv, "programme", channel=CHANNEL_ID)
        prog.set("start", start_dt.strftime("%Y%m%d%H%M%S"))
        prog.set("stop", stop_dt.strftime("%Y%m%d%H%M%S"))
        ET.SubElement(prog, "title").text = title


# ---------------- MAIN ----------------

def main():
    tv = ET.Element("tv", attrib={"generator-info-name": "gatotv-scraper"})

    ch = ET.SubElement(tv, "channel", id=CHANNEL_ID)
    ET.SubElement(ch, "display-name").text = CHANNEL_NAME

    today = datetime.now(TZ_EC).date()
    total_items = 0

    for d in range(DAYS):
        day = today + timedelta(days=d)
        html = fetch_day_html(day)
        schedule = parse_schedule_from_html(html)
        total_items += len(schedule)
        build_programmes(tv, day, schedule)

    print(f"Total programmes captured: {total_items}")

    ET.ElementTree(tv).write(
        "teleamazonas.xml",
        encoding="utf-8",
        xml_declaration=True
    )


if __name__ == "__main__":
    main()
