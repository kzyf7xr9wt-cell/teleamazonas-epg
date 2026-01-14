#!/usr/bin/env python3
# scrape.py
#
# Teleamazonas EPG scraper (from teleamazonas.com programación tabs)
# - Pulls the *visible* day (active tab) OR (recommended) builds a full 7-day EPG
# - Uses 24-hour time as shown on the site (no AM/PM prefixes)
# - Generates standard XMLTV: teleamazonas.xml
#
# Requirements:
#   pip install requests beautifulsoup4 lxml
#
# GitHub Actions tip:
#   python scrape.py
#   commits teleamazonas.xml

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

# -----------------------------
# CONFIG
# -----------------------------

# This is the page that contains the weekly tabs + sections you pasted.
PROGRAMACION_URL = "https://www.teleamazonas.com/programacion/"

# XMLTV channel id — must match what your IPTV app expects.
CHANNEL_ID = "teleamazonas.ec"
CHANNEL_DISPLAY_NAME = "Teleamazonas"

# Ecuador time is UTC-5, no DST.
ECUADOR_TZ = timezone(timedelta(hours=-5))

# Build full week (recommended). If False, only scrape the currently visible day panel.
BUILD_FULL_WEEK = True

# Output file
OUTPUT_XML = "teleamazonas.xml"

# User-Agent helps avoid simple blocks
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
}


# -----------------------------
# HELPERS
# -----------------------------

DAY_NAME_TO_INDEX = {
    "lunes": 0,
    "martes": 1,
    "miércoles": 2,
    "miercoles": 2,
    "jueves": 3,
    "viernes": 4,
    "sábado": 5,
    "sabado": 5,
    "domingo": 6,
}

WS_RE = re.compile(r"\s+")


def clean_text(s: str) -> str:
    s = s.replace("\xa0", " ")
    s = WS_RE.sub(" ", s).strip()
    # Remove old bug prefixes like "AM AM", "PM PM", etc.
    s = re.sub(r"^(AM|PM)\s+(AM|PM)\s+", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^(AM|PM)\s+", "", s, flags=re.IGNORECASE)
    return s.strip()


def parse_hhmm(hhmm: str) -> Tuple[int, int]:
    hhmm = clean_text(hhmm)
    m = re.match(r"^(\d{1,2}):(\d{2})$", hhmm)
    if not m:
        raise ValueError(f"Bad time format: {hhmm!r}")
    return int(m.group(1)), int(m.group(2))


def xml_escape(s: str) -> str:
    return (s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;")
             .replace('"', "&quot;")
             .replace("'", "&apos;"))


@dataclass
class Programme:
    start: datetime
    stop: datetime
    title: str


# -----------------------------
# SCRAPING
# -----------------------------

def fetch_html(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text


def find_tabs_and_sections(soup: BeautifulSoup):
    """
    Tabs: <li class="c-list-tv__tabs-item ...">Miércoles</li>
    Sections wrapper: <div class="c-list-tv__tabs__sections"> ... <article ...> ... </article> ...
    """
    tabs = soup.select("ul.c-list-tv__tabs li.c-list-tv__tabs-item")
    sections_wrap = soup.select_one("div.c-list-tv__tabs__sections")
    if not sections_wrap:
        # Fallback: sometimes it’s nested differently, but class remains
        sections_wrap = soup.select_one(".c-list-tv__tabs__sections")
    articles = sections_wrap.select("article") if sections_wrap else []
    return tabs, articles


def extract_programmes_from_article(article, base_date: datetime) -> List[Tuple[datetime, str]]:
    """
    Extract (datetime, title) points from a day's article.
    Each show item is like:
      <div class="c-list-tv-simple__txt"><span>05:00</span><p>El Chapulín Colorado</p></div>
    """
    items = article.select("div.c-list-tv-simple__txt")
    points: List[Tuple[datetime, str]] = []

    for it in items:
        span = it.find("span")
        p = it.find("p")
        if not span or not p:
            continue

        time_text = clean_text(span.get_text(" ", strip=True))
        title_text = clean_text(p.get_text(" ", strip=True))

        if not time_text or not title_text:
            continue

        try:
            hh, mm = parse_hhmm(time_text)
        except ValueError:
            # Skip anything that isn't HH:MM
            continue

        dt = datetime(base_date.year, base_date.month, base_date.day, hh, mm, tzinfo=ECUADOR_TZ)
        points.append((dt, title_text))

    # Deduplicate exact repeats (sometimes site repeats cards)
    dedup: List[Tuple[datetime, str]] = []
    seen = set()
    for dt, title in points:
        key = (dt.isoformat(), title.lower())
        if key not in seen:
            seen.add(key)
            dedup.append((dt, title))

    # Sort by time
    dedup.sort(key=lambda x: x[0])
    return dedup


def points_to_programmes(points: List[Tuple[datetime, str]]) -> List[Programme]:
    """
    Convert timepoints into Programme blocks by using the next show's start as stop.
    Handles midnight rollover: if times go backwards, add +1 day.
    """
    if not points:
        return []

    # Fix rollover by ensuring non-decreasing times
    fixed: List[Tuple[datetime, str]] = []
    last_dt = points[0][0]
    fixed.append((last_dt, points[0][1]))

    for dt, title in points[1:]:
        if dt < last_dt:
            # crossed midnight; move forward a day (or multiple days if needed)
            while dt < last_dt:
                dt = dt + timedelta(days=1)
        fixed.append((dt, title))
        last_dt = dt

    progs: List[Programme] = []
    for i in range(len(fixed)):
        start_dt, title = fixed[i]
        if i + 1 < len(fixed):
            stop_dt = fixed[i + 1][0]
        else:
            # last programme: give it a default 30 minutes
            stop_dt = start_dt + timedelta(minutes=30)

        # Guard: never allow zero/negative duration
        if stop_dt <= start_dt:
            stop_dt = start_dt + timedelta(minutes=30)

        progs.append(Programme(start=start_dt, stop=stop_dt, title=title))

    return progs


def get_active_tab_day_index(tabs) -> Optional[int]:
    """
    Determine which day tab is active.
    <li class="... active">Miércoles</li>
    """
    for li in tabs:
        classes = li.get("class") or []
        if "active" in classes:
            name = clean_text(li.get_text(" ", strip=True)).lower()
            return DAY_NAME_TO_INDEX.get(name)
    return None


def get_visible_article(articles):
    """
    Article is visible when class contains 'visible' based on your HTML snippet.
    """
    for a in articles:
        classes = a.get("class") or []
        if "visible" in classes:
            return a
    return None


# -----------------------------
# XMLTV OUTPUT
# -----------------------------

def dt_to_xmltv(dt: datetime) -> str:
    # XMLTV: YYYYMMDDHHMMSS ±ZZZZ
    return dt.strftime("%Y%m%d%H%M%S %z")


def build_xml(programmes: List[Programme]) -> str:
    out = []
    out.append('<?xml version="1.0" encoding="UTF-8"?>')
    out.append('<tv generator-info-name="teleamazonas-scraper">')
    out.append(f'  <channel id="{xml_escape(CHANNEL_ID)}">')
    out.append(f'    <display-name>{xml_escape(CHANNEL_DISPLAY_NAME)}</display-name>')
    out.append('  </channel>')

    for pr in programmes:
        out.append(
            f'  <programme channel="{xml_escape(CHANNEL_ID)}" '
            f'start="{dt_to_xmltv(pr.start)}" stop="{dt_to_xmltv(pr.stop)}">'
        )
        out.append(f'    <title>{xml_escape(pr.title)}</title>')
        out.append('  </programme>')

    out.append('</tv>')
    out.append("")  # final newline
    return "\n".join(out)


# -----------------------------
# MAIN
# -----------------------------

def main():
    html = fetch_html(PROGRAMACION_URL)
    soup = BeautifulSoup(html, "lxml")

    tabs, articles = find_tabs_and_sections(soup)
    if not articles:
        raise RuntimeError("Could not find schedule articles. Page structure may have changed.")

    today_ec = datetime.now(ECUADOR_TZ).date()

    all_programmes: List[Programme] = []

    if BUILD_FULL_WEEK:
        # Map each tab index to the article in order.
        # Teleamazonas appears to render days in order in the same sequence as tabs.
        # We'll pair them by index: 0..6
        if len(articles) < 7:
            # Sometimes there are extra non-day articles; filter to those that contain show cards
            candidate_articles = [a for a in articles if a.select_one("div.c-list-tv-simple__txt")]
            articles = candidate_articles

        if len(articles) < 7:
            raise RuntimeError(f"Expected 7 day articles, found {len(articles)}")

        # Find the active day index so we can anchor the week correctly to actual dates.
        active_idx = get_active_tab_day_index(tabs)
        if active_idx is None:
            # Fallback: assume "today" tab corresponds to today's weekday in Ecuador
            active_idx = datetime.now(ECUADOR_TZ).weekday()

        # Compute the calendar date for each day panel:
        # panel_index i corresponds to date = today + (i - active_idx)
        for i in range(7):
            day_date = today_ec + timedelta(days=(i - active_idx))
            base_dt = datetime(day_date.year, day_date.month, day_date.day, 0, 0, tzinfo=ECUADOR_TZ)

            points = extract_programmes_from_article(articles[i], base_dt)
            progs = points_to_programmes(points)
            all_programmes.extend(progs)

    else:
        # Only scrape visible article (active day)
        visible = get_visible_article(articles)
        if not visible:
            # fallback: use first article with content
            visible = next((a for a in articles if a.select_one("div.c-list-tv-simple__txt")), None)
        if not visible:
            raise RuntimeError("No visible schedule article found.")

        base_dt = datetime(today_ec.year, today_ec.month, today_ec.day, 0, 0, tzinfo=ECUADOR_TZ)
        points = extract_programmes_from_article(visible, base_dt)
        all_programmes = points_to_programmes(points)

    # Sort and remove exact duplicates across days
    all_programmes.sort(key=lambda p: (p.start, p.stop, p.title.lower()))
    dedup: List[Programme] = []
    last_key = None
    for p in all_programmes:
        key = (p.start, p.stop, p.title.lower())
        if key != last_key:
            dedup.append(p)
        last_key = key

    xml = build_xml(dedup)

    with open(OUTPUT_XML, "w", encoding="utf-8") as f:
        f.write(xml)

    print(f"Wrote {OUTPUT_XML} with {len(dedup)} programmes.")


if __name__ == "__main__":
    main()
