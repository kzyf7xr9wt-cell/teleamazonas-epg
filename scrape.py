import re
import requests
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone

# GatoTV Teleamazonas daily schedule URL format:
# https://www.gatotv.com/canal/teleamazonas/YYYY-MM-DD
BASE_URL = "https://www.gatotv.com/canal/teleamazonas/"

CHANNEL_ID = "teleamazonas.ec"
CHANNEL_NAME = "Teleamazonas"
DAYS = 7

# Match HH:MM (24-hour)
TIME_RE = re.compile(r"(?:[01]?\d|2[0-3]):[0-5]\d")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (EPG Bot)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Use Ecuador time for the guide times (UTC-5)
TZ_EC = timezone(timedelta(hours=-5))


def fetch_day_html(date_obj):
    date_str = date_obj.strftime("%Y-%m-%d")
    url = f"{BASE_URL}{date_str}"
    r = requests.get(url, headers=HEADERS, timeout=25)
    r.raise_for_status()
    return r.text


def parse_schedule_from_html(html):
    """
    Flexible parser:
    - Looks through table rows first (most common)
    - If no rows found, falls back to scanning blocks of text
    Returns list of (time_str "HH:MM", title_str)
    """
    soup = BeautifulSoup(html, "html.parser")

    schedule = []

    # 1) Try table rows
    rows = soup.find_all("tr")
    for tr in rows:
        row_text = tr.get_text(" ", strip=True)
        if not row_text:
            continue

        m = TIME_RE.search(row_text)
        if not m:
            continue

        time_str = m.group(0)

        # Attempt to isolate title by removing times and common headers
        title = row_text
        title = title.replace(time_str, " ").strip()
        title = re.sub(r"\s+", " ", title)

        # Remove extra times if present (e.g. start + end)
        title = TIME_RE.sub(" ", title).strip()
        title = re.sub(r"\s+", " ", title)

        # Filter obvious non-program junk
        junk_phrases = [
            "Horarios", "Programación", "Hora Inicio", "Hora Fin", "Canal", "Teleamazonas"
        ]
        if any(j.lower() in title.lower() for j in junk_phrases) and len(title) > 40:
            # If it's a long header line, skip it
            continue

        # Titles should be reasonably short/meaningful
        if len(title) < 2:
            continue

        schedule.append((time_str, title))

    # 2) Fallback: if table parsing finds nothing, scan visible text lines
    if not schedule:
        text = soup.get_text("\n")
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        # Look for patterns: time line followed by a title line
        for i in range(len(lines) - 1):
            m = re.fullmatch(r"(?:[01]?\d|2[0-3]):[0-5]\d", lines[i])
            if m:
                t = lines[i]
                title = lines[i + 1]
                if title and not re.fullmatch(r"(?:[01]?\d|2[0-3]):[0-5]\d", title):
                    schedule.append((t, title))

    # Deduplicate while preserving order
    seen = set()
    clean = []
    for t, title in schedule:
        key = (t, title)
        if key not in seen:
            seen.add(key)
            clean.append((t, title))

    return clean


def build_programmes(tv, date_obj, schedule):
    """
    Build programme entries for a given day.
    Stop time is next programme's start, else +30min.
    Handles crossing midnight if schedule wraps.
    """
    starts = []
    current_date = date_obj

    prev_minutes = None
    for time_str, title in schedule:
        hh, mm = map(int, time_str.split(":"))
        minutes = hh * 60 + mm

        # If times go backward, assume we've crossed midnight into next day
        if prev_minutes is not None and minutes < prev_minutes:
            current_date = current_date + timedelta(days=1)

        start_dt = datetime(
            current_date.year, current_date.month, current_date.day,
            hh, mm, tzinfo=TZ_EC
        )
        starts.append((start_dt, title))
        prev_minutes = minutes

    for idx, (start_dt, title) in enumerate(starts):
        if idx + 1 < len(starts):
            stop_dt = starts[idx + 1][0]
        else:
            stop_dt = start_dt + timedelta(minutes=30)

        prog = ET.SubElement(tv, "programme", channel=CHANNEL_ID)
        prog.set("start", start_dt.strftime("%Y%m%d%H%M%S %z"))
        prog.set("stop", stop_dt.strftime("%Y%m%d%H%M%S %z"))
        ET.SubElement(prog, "title").text = title


def main():
    tv = ET.Element("tv", attrib={"generator-info-name": "gatotv-scraper"})

    ch = ET.SubElement(tv, "channel", id=CHANNEL_ID)
    ET.SubElement(ch, "display-name").text = CHANNEL_NAME

    # Use Ecuador date (UTC-5)
    today = datetime.now(TZ_EC).date()

    total_items = 0

    for d in range(DAYS):
        day = today + timedelta(days=d)
        html = fetch_day_html(day)
        schedule = parse_schedule_from_html(html)

        # If no items found, continue (but you'll see small XML)
        total_items += len(schedule)
        build_programmes(tv, day, schedule)

    # Helpful debug line in Actions logs
    print(f"Total programmes captured: {total_items}")

    ET.ElementTree(tv).write("teleamazonas.xml", encoding="utf-8", xml_declaration=True)


if __name__ == "__main__":
    main()
