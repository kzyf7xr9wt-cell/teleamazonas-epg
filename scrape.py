#!/usr/bin/env python3
"""
Teleamazonas EPG scraper (Teleamazonas programación page)
- Builds a 7-day XMLTV file with TWO channels:
    1) teleamazonas.ec            -> Teleamazonas (Quito / Main)  (keeps your existing ID)
    2) teleamazonas.ec.guayaquil  -> Teleamazonas (Guayaquil)

Routing:
- Title contains "Guayaquil" -> Guayaquil ONLY
- Title contains "Quito"     -> Quito/Main ONLY
- Otherwise                 -> BOTH

Notes:
- Uses built-in BeautifulSoup parser ("html.parser") so it runs on GitHub Actions without lxml.
- Ecuador timezone is UTC-05:00 (no DST).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Tuple, Iterable, Dict

import requests
from bs4 import BeautifulSoup

# -----------------------------
# CONFIG
# -----------------------------

PROGRAMACION_URL = "https://www.teleamazonas.com/programacion/"

# Keep your existing channel id for Quito/Main so you don't break UHF mapping
CHANNEL_ID_QUITO = "teleamazonas.ec"
CHANNEL_NAME_QUITO = "Teleamazonas (Quito / Main)"

CHANNEL_ID_GYE = "teleamazonas.ec.guayaquil"
CHANNEL_NAME_GYE = "Teleamazonas (Guayaquil)"

OUTPUT_XML = "teleamazonas.xml"

# Ecuador time (UTC-5)
TZ_EC = timezone(timedelta(hours=-5))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (teleamazonas-epg; +github-actions)"
}

# Map Spanish day tabs to weekday index (Mon=0..Sun=6)
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


# -----------------------------
# DATA
# -----------------------------

@dataclass(frozen=True)
class Programme:
    channel_id: str
    start: datetime
    stop: datetime
    title: str


# -----------------------------
# UTILS
# -----------------------------

def clean_text(s: str) -> str:
    s = (s or "").replace("\xa0", " ")
    s = WS_RE.sub(" ", s).strip()
    # Strip any stray AM/PM prefixes if ever present
    s = re.sub(r"^(AM|PM)\s+(AM|PM)\s+", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^(AM|PM)\s+", "", s, flags=re.IGNORECASE)
    return s.strip()


def parse_hhmm(hhmm: str) -> Tuple[int, int]:
    hhmm = clean_text(hhmm)
    m = re.match(r"^(\d{1,2}):(\d{2})$", hhmm)
    if not m:
        raise ValueError(f"Bad time format: {hhmm!r}")
    hh = int(m.group(1))
    mm = int(m.group(2))
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ValueError(f"Invalid time: {hhmm!r}")
    return hh, mm


def xml_escape(s: str) -> str:
    s = s or ""
    return (s.replace("&", "&amp;")
              .replace("<", "&lt;")
              .replace(">", "&gt;")
              .replace('"', "&quot;")
              .replace("'", "&apos;"))


def dt_to_xmltv(dt: datetime) -> str:
    # XMLTV datetime: YYYYMMDDHHMMSS ±ZZZZ
    return dt.strftime("%Y%m%d%H%M%S %z")


def fetch_html(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text


def route_channels(title: str) -> List[str]:
    """
    Route a title to Quito/Main, Guayaquil, or both.
    """
    t = clean_text(title).lower()
    if "guayaquil" in t:
        return [CHANNEL_ID_GYE]
    if "quito" in t:
        return [CHANNEL_ID_QUITO]
    return [CHANNEL_ID_QUITO, CHANNEL_ID_GYE]


# -----------------------------
# SCRAPING LOGIC
# -----------------------------

def get_tabs_and_day_articles(soup: BeautifulSoup):
    """
    Tabs: <li class="c-list-tv__tabs-item ...">Miércoles</li>
    Articles wrapper: <div class="c-list-tv__tabs__sections"> ... <article ...> ... </article> ...
    """
    tabs = soup.select("li.c-list-tv__tabs-item")
    sections_wrap = soup.select_one("div.c-list-tv__tabs__sections")
    if not sections_wrap:
        sections_wrap = soup.select_one(".c-list-tv__tabs__sections")
    if not sections_wrap:
        raise RuntimeError("Could not find sections wrapper (.c-list-tv__tabs__sections).")

    # Articles for each day (usually 7)
    # Some have class js-sections-tabs-item; the active/visible one has class "visible"
    articles = sections_wrap.select("article.c-list-tv__sections-item")
    if not articles:
        # Fallback: any article in wrapper
        articles = sections_wrap.select("article")
    if not articles:
        raise RuntimeError("Could not find day articles inside sections wrapper.")

    # Keep only those that actually contain schedule cards
    articles = [a for a in articles if a.select_one("div.c-list-tv-simple__txt")]
    if len(articles) < 7:
        raise RuntimeError(f"Expected 7 schedule day articles; found {len(articles)}.")

    # In case there are more than 7, keep first 7 (site is Monday..Sunday)
    return tabs, articles[:7]


def get_active_tab_index(tabs) -> int:
    """
    Which day tab is active? <li class="... active">Miércoles</li>
    Returns Mon=0..Sun=6.
    """
    for li in tabs:
        classes = li.get("class") or []
        if "active" in classes:
            day_name = clean_text(li.get_text(" ", strip=True)).lower()
            if day_name in DAY_NAME_TO_INDEX:
                return DAY_NAME_TO_INDEX[day_name]

    # Fallback: Ecuador's current weekday
    return datetime.now(TZ_EC).weekday()


def extract_points_from_article(article, base_date: datetime) -> List[Tuple[datetime, str]]:
    """
    From one day's <article>, extract list of (datetime, title) using base_date for date.
    """
    points: List[Tuple[datetime, str]] = []
    blocks = article.select("div.c-list-tv-simple__txt")

    for b in blocks:
        span = b.find("span")
        p = b.find("p")
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

        dt = datetime(base_date.year, base_date.month, base_date.day, hh, mm, tzinfo=TZ_EC)
        points.append((dt, title_text))

    # Dedup exact repeats
    seen = set()
    out: List[Tuple[datetime, str]] = []
    for dt, title in points:
        key = (dt.isoformat(), title.lower())
        if key not in seen:
            seen.add(key)
            out.append((dt, title))

    out.sort(key=lambda x: x[0])
    return out


def points_to_programmes(points: List[Tuple[datetime, str]]) -> List[Tuple[datetime, datetime, str]]:
    """
    Convert (start,title) points into (start, stop, title).
    Handles midnight rollover: if a time goes backwards, add a day.
    """
    if not points:
        return []

    fixed: List[Tuple[datetime, str]] = []
    last_dt = points[0][0]
    fixed.append(points[0])

    for dt, title in points[1:]:
        if dt < last_dt:
            while dt < last_dt:
                dt = dt + timedelta(days=1)
        fixed.append((dt, title))
        last_dt = dt

    progs: List[Tuple[datetime, datetime, str]] = []
    for i, (start_dt, title) in enumerate(fixed):
        if i + 1 < len(fixed):
            stop_dt = fixed[i + 1][0]
        else:
            stop_dt = start_dt + timedelta(minutes=30)

        if stop_dt <= start_dt:
            stop_dt = start_dt + timedelta(minutes=30)

        progs.append((start_dt, stop_dt, title))

    return progs


def build_week_programmes(tabs, day_articles) -> List[Programme]:
    """
    Build 7 days worth of programmes for both channels using routing rules.
    Anchors day indices to *today in Ecuador* based on active tab.
    """
    today_ec = datetime.now(TZ_EC).date()
    active_idx = get_active_tab_index(tabs)

    programmes: List[Programme] = []

    for day_idx in range(7):
        day_date = today_ec + timedelta(days=(day_idx - active_idx))
        base_dt = datetime(day_date.year, day_date.month, day_date.day, 0, 0, tzinfo=TZ_EC)

        points = extract_points_from_article(day_articles[day_idx], base_dt)
        blocks = points_to_programmes(points)

        for start_dt, stop_dt, title in blocks:
            for ch_id in route_channels(title):
                programmes.append(Programme(channel_id=ch_id, start=start_dt, stop=stop_dt, title=title))

    # Sort and dedup (some programmes may be identical duplicates)
    programmes.sort(key=lambda p: (p.channel_id, p.start, p.stop, p.title.lower()))
    deduped: List[Programme] = []
    last_key = None
    for p in programmes:
        key = (p.channel_id, p.start, p.stop, p.title.lower())
        if key != last_key:
            deduped.append(p)
        last_key = key

    return deduped


# -----------------------------
# XML OUTPUT
# -----------------------------

def write_xml(programmes: List[Programme], path: str) -> None:
    lines: List[str] = []
    lines.append('<?xml version="1.0" encoding="UTF-8"?>')
    lines.append('<tv generator-info-name="teleamazonas-epg">')

    # Channels
    lines.append(f'  <channel id="{xml_escape(CHANNEL_ID_QUITO)}">')
    lines.append(f'    <display-name>{xml_escape(CHANNEL_NAME_QUITO)}</display-name>')
    lines.append('  </channel>')

    lines.append(f'  <channel id="{xml_escape(CHANNEL_ID_GYE)}">')
    lines.append(f'    <display-name>{xml_escape(CHANNEL_NAME_GYE)}</display-name>')
    lines.append('  </channel>')

    # Programmes
    for p in programmes:
        lines.append(
            f'  <programme channel="{xml_escape(p.channel_id)}" '
            f'start="{dt_to_xmltv(p.start)}" stop="{dt_to_xmltv(p.stop)}">'
        )
        lines.append(f'    <title>{xml_escape(p.title)}</title>')
        lines.append('  </programme>')

    lines.append('</tv>')
    lines.append("")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main():
    html = fetch_html(PROGRAMACION_URL)
    soup = BeautifulSoup(html, "html.parser")

    tabs, day_articles = get_tabs_and_day_articles(soup)
    programmes = build_week_programmes(tabs, day_articles)

    write_xml(programmes, OUTPUT_XML)

    # Helpful log line for Actions
    q_count = sum(1 for p in programmes if p.channel_id == CHANNEL_ID_QUITO)
    g_count = sum(1 for p in programmes if p.channel_id == CHANNEL_ID_GYE)
    print(f"Wrote {OUTPUT_XML}: Quito/Main={q_count} programmes, Guayaquil={g_count} programmes (total {len(programmes)})")


if __name__ == "__main__":
    main()
