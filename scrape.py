#!/usr/bin/env python3
# scrape.py
#
# Teleamazonas EPG scraper (from teleamazonas.com programación tabs)
# - Pulls the *visible* day (active tab) OR (recommended) builds a full 7-day EPG
# - Uses 24-hour time as shown on the site (no AM/PM prefixes)
# - Generates standard XMLTV
#
# Requirements:
#   pip install requests beautifulsoup4 lxml
#
# GitHub Actions tip:
#   python scrape.py
#   commits output xml

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

PROGRAMACION_URL = "https://www.teleamazonas.com/programacion/"

# ✅ NEW: choose which city feed to emit from the same page.
# The page includes a Quito/Guayaquil toggle and both sets of 7 day-panels exist in the HTML.  [oai_citation:2‡Teleamazonas News](https://www.teleamazonas.com/programacion/)
CITY = "guayaquil"  # "quito" or "guayaquil"

# XMLTV channel id — must match what your IPTV app expects.
# (Change these if your IPTV uses separate IDs per city.)
CHANNEL_ID = "teleamazonas.ec.guayaquil"
CHANNEL_DISPLAY_NAME = "Teleamazonas Guayaquil"

# Ecuador time is UTC-5, no DST.
ECUADOR_TZ = timezone(timedelta(hours=-5))

BUILD_FULL_WEEK = True

# Output file
OUTPUT_XML = "teleamazonas-guayaquil.xml"

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
        sections_wrap = soup.select_one(".c-list-tv__tabs__sections")
    articles = sections_wrap.select("article") if sections_wrap else []
    return tabs, articles


def extract_programmes_from_article(article, base_date: datetime) -> List[Tuple[datetime, str]]:
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
            continue

        dt = datetime(base_date.year, base_date.month, base_date.day, hh, mm, tzinfo=ECUADOR_TZ)
        points.append((dt, title_text))

    dedup: List[Tuple[datetime, str]] = []
    seen = set()
    for dt, title in points:
        key = (dt.isoformat(), title.lower())
        if key not in seen:
            seen.add(key)
            dedup.append((dt, title))

    dedup.sort(key=lambda x: x[0])
    return dedup


def points_to_programmes(points: List[Tuple[datetime, str]]) -> List[Programme]:
    if not points:
        return []

    fixed: List[Tuple[datetime, str]] = []
    last_dt = points[0][0]
    fixed.append((last_dt, points[0][1]))

    for dt, title in points[1:]:
        if dt < last_dt:
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
            stop_dt = start_dt + timedelta(minutes=30)

        if stop_dt <= start_dt:
            stop_dt = start_dt + timedelta(minutes=30)

        progs.append(Programme(start=start_dt, stop=stop_dt, title=title))

    return progs


def get_active_tab_day_index(tabs) -> Optional[int]:
    for li in tabs:
        classes = li.get("class") or []
        if "active" in classes:
            name = clean_text(li.get_text(" ", strip=True)).lower()
            return DAY_NAME_TO_INDEX.get(name)
    return None


def get_visible_article(articles):
    for a in articles:
        classes = a.get("class") or []
        if "visible" in classes:
            return a
    return None


# ✅ NEW: choose the correct 7-day block (Quito vs Guayaquil) from the same page
def select_city_articles(articles, city: str) -> List:
    """
    Teleamazonas renders BOTH city schedules in the HTML on /programacion/.
    We pick the set of 7 day-articles where titles mention the city.
    (Guayaquil schedule includes items like "24 Horas Guayaquil I/II".  [oai_citation:3‡Teleamazonas News](https://www.teleamazonas.com/programacion/))
    """
    city = city.strip().lower()
    if city not in ("quito", "guayaquil"):
        raise ValueError("CITY must be 'quito' or 'guayaquil'")

    # Keep only articles that actually have show cards
    candidate_articles = [a for a in articles if a.select_one("div.c-list-tv-simple__txt")]

    if len(candidate_articles) < 7:
        return candidate_articles

    # The page typically includes 14 (7 Quito + 7 Guayaquil). If it changes, this still tries to work.
    blocks = [candidate_articles[i:i + 7] for i in range(0, len(candidate_articles), 7)]
    best_block: Optional[List] = None
    best_hits = -1

    for block in blocks:
        hits = 0
        for a in block:
            txt = a.get_text(" ", strip=True).lower()
            # city keyword present anywhere in the article text
            if city in txt:
                hits += 1
        if hits > best_hits:
            best_hits = hits
            best_block = block

    return best_block or candidate_articles[:7]


# -----------------------------
# XMLTV OUTPUT
# -----------------------------

def dt_to_xmltv(dt: datetime) -> str:
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
    out.append("")
    return "\n".join(out)


# -----------------------------
# MAIN
# -----------------------------

def main():
    html = fetch_html(PROGRAMACION_URL)
    soup = BeautifulSoup(html, "html.parser")

    tabs, articles = find_tabs_and_sections(soup)
    if not articles:
        raise RuntimeError("Could not find schedule articles. Page structure may have changed.")

    # ✅ NEW: pick Guayaquil (or Quito) from the combined page
    articles = select_city_articles(articles, CITY)

    today_ec = datetime.now(ECUADOR_TZ).date()
    all_programmes: List[Programme] = []

    if BUILD_FULL_WEEK:
        if len(articles) < 7:
            raise RuntimeError(f"Expected 7 day articles for {CITY}, found {len(articles)}")

        active_idx = get_active_tab_day_index(tabs)
        if active_idx is None:
            active_idx = datetime.now(ECUADOR_TZ).weekday()

        for i in range(7):
            day_date = today_ec + timedelta(days=(i - active_idx))
            base_dt = datetime(day_date.year, day_date.month, day_date.day, 0, 0, tzinfo=ECUADOR_TZ)

            points = extract_programmes_from_article(articles[i], base_dt)
            progs = points_to_programmes(points)
            all_programmes.extend(progs)

    else:
        visible = get_visible_article(articles)
        if not visible:
            visible = next((a for a in articles if a.select_one("div.c-list-tv-simple__txt")), None)
        if not visible:
            raise RuntimeError("No visible schedule article found.")

        base_dt = datetime(today_ec.year, today_ec.month, today_ec.day, 0, 0, tzinfo=ECUADOR_TZ)
        points = extract_programmes_from_article(visible, base_dt)
        all_programmes = points_to_programmes(points)

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
