#!/usr/bin/env python3
import re
import sys
from datetime import datetime, timedelta, timezone, date, time
import requests
from bs4 import BeautifulSoup

URL = "https://www.teleamazonas.com/programacion/"
OUT_XML = "teleamazonas.xml"

# Ecuador time is UTC-5 year-round
EC_TZ = timezone(timedelta(hours=-5))

UA = "Mozilla/5.0 (compatible; teleamazonas-epg/2.0)"

# Website order is always: Lunes..Domingo
DAY_INDEX_TO_NAME_ES = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]

def clean_text(s: str) -> str:
    if not s:
        return ""
    s = s.replace("\xa0", " ")
    s = re.sub(r"\s+", " ", s).strip()
    # remove duplicated AM/PM tokens if they ever appear
    s = re.sub(r"\b(AM|PM)\b(\s+\b(AM|PM)\b)+", r"\1", s, flags=re.IGNORECASE)
    return s

def parse_hhmm(t: str):
    t = clean_text(t)
    m = re.match(r"^(\d{1,2}):(\d{2})$", t)
    if not m:
        return None
    hh = int(m.group(1))
    mm = int(m.group(2))
    if 0 <= hh <= 23 and 0 <= mm <= 59:
        return hh, mm
    return None

def xml_escape(s: str) -> str:
    return (s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;")
             .replace('"', "&quot;")
             .replace("'", "&apos;"))

def dt_to_xmltv(dt: datetime) -> str:
    # XMLTV format: YYYYMMDDhhmmss ±ZZZZ
    return dt.strftime("%Y%m%d%H%M%S %z")

def monday_of_current_week_ec() -> date:
    now = datetime.now(EC_TZ)
    today = now.date()
    # Monday=0..Sunday=6
    return today - timedelta(days=now.weekday())

def get_schedule_containers(soup: BeautifulSoup):
    """
    Teleamazonas embeds both city schedules in the HTML.
    Both contain a div.c-list-tv__tabs__sections with 7 <article> blocks.
    Sometimes there are other matching divs; we select those that actually contain cards.
    """
    candidates = soup.select("div.c-list-tv__tabs__sections")
    good = []
    for sec in candidates:
        # Must contain at least one schedule card
        if sec.select_one("div.c-list-tv-simple__txt span"):
            good.append(sec)

    # Usually: [Quito, Guayaquil]
    if len(good) < 2:
        raise RuntimeError(f"Could not find both city schedule containers (found {len(good)}).")

    return good[0], good[1]

def extract_title_from_card(card) -> str:
    """
    Robust extraction:
    - Prefer .c-list-tv-simple__txt p
    - If empty/missing, use the text content of .c-list-tv-simple__txt minus the time
    """
    txt = card.select_one(".c-list-tv-simple__txt")
    if not txt:
        return ""

    # Preferred title element
    p = txt.select_one("p")
    if p:
        title = clean_text(p.get_text(" ", strip=True))
        if title:
            return title

    # Fallback: grab all text inside txt, then remove the time token
    raw = clean_text(txt.get_text(" ", strip=True))
    # raw often looks like "08:00 24 Horas Guayaquil II"
    # remove leading time
    raw = re.sub(r"^\d{1,2}:\d{2}\s*", "", raw).strip()
    return raw

def parse_city_container(container) -> list[list[tuple[str, str]]]:
    """
    Returns 7 day lists, each list is list of (HH:MM, title).
    """
    articles = container.select("article")
    if not articles:
        raise RuntimeError("No <article> day sections found under a city container.")
    # Take first 7
    articles = articles[:7]

    days = []
    for art in articles:
        items = []
        cards = art.select("div.c-list-tv-simple")
        for card in cards:
            t_el = card.select_one(".c-list-tv-simple__txt span")
            if not t_el:
                continue
            t_str = clean_text(t_el.get_text(" ", strip=True))
            if not parse_hhmm(t_str):
                continue

            title = extract_title_from_card(card)
            title = clean_text(title)
            if not title:
                # If still empty, label it so the slot exists instead of blank
                title = "TBA"

            items.append((t_str, title))
        days.append(items)

    # Ensure exactly 7 lists
    while len(days) < 7:
        days.append([])

    return days

def build_programmes_for_city(city_days: list[list[tuple[str, str]]], city_id: str):
    """
    Convert weekly tab lists into XMLTV programme entries.
    We map Lunes..Domingo to the CURRENT week in Ecuador.
    We also handle midnight rollover within each day.
    """
    week_monday = monday_of_current_week_ec()
    programmes = []

    for day_idx in range(7):
        day_date = week_monday + timedelta(days=day_idx)
        items = city_days[day_idx]
        if not items:
            continue

        starts = []
        last_dt = None
        rollover = 0

        for (t_str, title) in items:
            hhmm = parse_hhmm(t_str)
            if not hhmm:
                continue
            hh, mm = hhmm
            dt = datetime.combine(day_date, time(hh, mm), EC_TZ)

            # If the times go backwards, we crossed midnight into the next calendar day
            if last_dt and dt < last_dt:
                rollover += 1
                dt = dt + timedelta(days=rollover)

            starts.append((dt, title))
            last_dt = dt

        # Sort (in case HTML is out of order)
        starts.sort(key=lambda x: x[0])

        # Create stop times
        for i, (start_dt, title) in enumerate(starts):
            if i + 1 < len(starts):
                stop_dt = starts[i + 1][0]
            else:
                stop_dt = start_dt + timedelta(minutes=60)
            if stop_dt <= start_dt:
                stop_dt = start_dt + timedelta(minutes=30)

            programmes.append((city_id, start_dt, stop_dt, title))

    return programmes

def write_xml(channels, programmes):
    """
    channels: list of (id, display_name)
    programmes: list of (channel_id, start_dt, stop_dt, title)
    """
    lines = []
    lines.append('<?xml version="1.0" encoding="UTF-8"?>')
    lines.append('<tv generator-info-name="teleamazonas-scraper">')

    for cid, cname in channels:
        lines.append(f'  <channel id="{xml_escape(cid)}">')
        lines.append(f'    <display-name>{xml_escape(cname)}</display-name>')
        lines.append('  </channel>')

    # Sort programmes by channel then time
    programmes.sort(key=lambda x: (x[0], x[1]))

    for cid, start_dt, stop_dt, title in programmes:
        lines.append(
            f'  <programme channel="{xml_escape(cid)}" '
            f'start="{dt_to_xmltv(start_dt)}" stop="{dt_to_xmltv(stop_dt)}">'
        )
        lines.append(f'    <title lang="es">{xml_escape(title)}</title>')
        lines.append('  </programme>')

    lines.append('</tv>')
    xml = "\n".join(lines) + "\n"

    with open(OUT_XML, "w", encoding="utf-8") as f:
        f.write(xml)

def main():
    r = requests.get(URL, headers={"User-Agent": UA, "Accept-Language": "es-EC,es;q=0.9,en;q=0.7"}, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    quito_container, guaya_container = get_schedule_containers(soup)

    quito_days = parse_city_container(quito_container)
    guaya_days = parse_city_container(guaya_container)

    channels = [
        ("teleamazonas.quito", "Teleamazonas (Quito)"),
        ("teleamazonas.guayaquil", "Teleamazonas (Guayaquil)"),
    ]

    programmes = []
    programmes.extend(build_programmes_for_city(quito_days, "teleamazonas.quito"))
    programmes.extend(build_programmes_for_city(guaya_days, "teleamazonas.guayaquil"))

    write_xml(channels, programmes)

    # Helpful log
    print("OK: wrote teleamazonas.xml")
    print(f"  Quito entries: {sum(1 for p in programmes if p[0]=='teleamazonas.quito')}")
    print(f"  Guayaquil entries: {sum(1 for p in programmes if p[0]=='teleamazonas.guayaquil')}")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("SCRAPER FAILED:", str(e), file=sys.stderr)
        raise
