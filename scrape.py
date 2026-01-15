#!/usr/bin/env python3
"""
Teleamazonas EPG scraper (Teleamazonas programación page)
✅ Builds a 7-day XMLTV with TWO channels:
   1) teleamazonas.ec             -> Teleamazonas (Quito / Main)  (keeps your existing ID)
   2) teleamazonas.ec.guayaquil   -> Teleamazonas (Guayaquil)

✅ Uses the site's weekly tabs/panels and anchors dates using the ACTIVE tab
✅ Ecuador timezone (UTC-05:00)
✅ Uses BeautifulSoup "html.parser" (no lxml needed on GitHub Actions)
✅ Safer midnight handling (prevents random "blank" holes when the site lists items slightly out of order)

Routing:
- Title contains "Guayaquil" -> Guayaquil only
- Title contains "Quito"     -> Quito/Main only
- Otherwise                 -> BOTH
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Tuple

import requests
from bs4 import BeautifulSoup

# -----------------------------
# CONFIG
# -----------------------------

PROGRAMACION_URL = "https://www.teleamazonas.com/programacion/"
OUTPUT_XML = "teleamazonas.xml"

# Keep your existing Quito/Main channel-id so UHF mapping doesn't break
CHANNEL_ID_QUITO = "teleamazonas.ec"
CHANNEL_NAME_QUITO = "Teleamazonas (Quito / Main)"

CHANNEL_ID_GYE = "teleamazonas.ec.guayaquil"
CHANNEL_NAME_GYE = "Teleamazonas (Guayaquil)"

# Ecuador time is UTC-5 (no DST)
TZ_EC = timezone(timedelta(hours=-5))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (teleamazonas-epg; +github-actions)"
}

# Spanish day tabs to weekday index (Mon=0..Sun=6)
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
TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


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
    # Remove any accidental AM/PM prefixes if present
    s = re.sub(r"^(AM|PM)\s+(AM|PM)\s+", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^(AM|PM)\s+", "", s, flags=re.IGNORECASE)
    return s.strip()


def parse_hhmm(hhmm: str) -> Tuple[int, int]:
    hhmm = clean_text(hhmm)
    m = TIME_RE.match(hhmm)
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
    # XMLTV datetime format: YYYYMMDDHHMMSS ±ZZZZ
    return dt.strftime("%Y%m%d%H%M%S %z")


def fetch_html(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text


def route_channels(title: str) -> List[str]:
    """
    Route each programme into Quito/Main, Guayaquil, or both.
    """
    t = clean_text(title).lower()
    if "guayaquil" in t:
        return [CHANNEL_ID_GYE]
    if "quito" in t:
        return [CHANNEL_ID_QUITO]
    return [CHANNEL_ID_QUITO, CHANNEL_ID_GYE]


# -----------------------------
# SCRAPING
# -----------------------------

def get_tabs_and_day_articles(soup: BeautifulSoup):
    """
    Tabs: <li class="c-list-tv__tabs-item ...">Miércoles</li>
    Articles wrapper: <div class="c-list-tv__tabs__sections"> ... <article ...> ... </article> ...
    """
    tabs = soup.select("li.c-list-tv__tabs-item")

    sections_wrap = soup.select_one("div.c-list-tv__tabs__sections") or soup.select_one(".c-list-tv__tabs__sections")
    if not sections_wrap:
        raise RuntimeError("Could not find sections wrapper (.c-list-tv__tabs__sections).")

    # Day articles live here
    articles = sections_wrap.select("article.c-list-tv__sections-item")
    if not articles:
        articles = sections_wrap.select("article")
    if not articles:
        raise RuntimeError("Could not find day articles in the schedule wrapper.")

    # Keep only those that actually contain schedule items
    articles = [a for a in articles if a.select_one("div.c-list-tv-simple__txt")]
    if len(articles) < 7:
        raise RuntimeError(f"Expected 7 schedule day articles; found {len(articles)}.")

    return tabs, articles[:7]


def get_active_tab_index(tabs) -> int:
    """
    Find active day tab: <li class="... active">Miércoles</li>
    Returns weekday index Mon=0..Sun=6. Fallback: Ecuador current weekday.
    """
    for li in tabs:
        classes = li.get("class") or []
        if "active" in classes:
            day_name = clean_text(li.get_text(" ", strip=True)).lower()
            if day_name in DAY_NAME_TO_INDEX:
                return DAY_NAME_TO_INDEX[day_name]
    return datetime.now(TZ_EC).weekday()


def extract_points_from_article(article, base_date: datetime) -> List[Tuple[datetime, str]]:
    """
    Extract (datetime, title) points from a day's article.
    Each item looks like:
      <div class="c-list-tv-simple__txt"><span>05:00</span><p>Show</p></div>
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

    # Deduplicate exact repeats
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

    IMPORTANT:
    The Teleamazonas HTML is sometimes slightly out-of-order.
    Old logic treated ANY backwards time as "midnight rollover" and pushed items
    into the next day -> caused holes (blank slots like 06:00/08:00/14:00).

    Fix:
    - Only treat a backwards jump as "midnight rollover" if it is LARGE ( > 8 hours ).
    - Small backwards jumps are assumed to be ordering glitches and kept same-day.
    """
    if not points:
        return []

    fixed: List[Tuple[datetime, str]] = []
    last_dt = points[0][0]
    fixed.append(points[0])

    for dt, title in points[1:]:
        if dt < last_dt:
            backwards = last_dt - dt
            # Only roll to next day if it REALLY looks like midnight crossover
            if backwards > timedelta(hours=8):
                while dt < last_dt:
                    dt = dt + timedelta(days=1)
            # else: minor ordering issue; keep dt as-is

        fixed.append((dt, title))

        # Track the max timestamp so small glitches don't cascade
        if dt > last_dt:
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
    Build 7 days worth of programmes for both channels.
    Dates are anchored using the ACTIVE tab (stable, matches what you click on site).
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

    # Sort & dedup
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
    lines.append("")  # newline at EOF

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# -----------------------------
# MAIN
# -----------------------------

def main() -> None:
    html = fetch_html(PROGRAMACION_URL)
    soup = BeautifulSoup(html, "html.parser")

    tabs, day_articles = get_tabs_and_day_articles(soup)
    programmes = build_week_programmes(tabs, day_articles)

    write_xml(programmes, OUTPUT_XML)

    # Helpful for GitHub Actions logs
    q_count = sum(1 for p in programmes if p.channel_id == CHANNEL_ID_QUITO)
    g_count = sum(1 for p in programmes if p.channel_id == CHANNEL_ID_GYE)
    print(f"Wrote {OUTPUT_XML}: Quito/Main={q_count} programmes, Guayaquil={g_count} programmes (total {len(programmes)})")


if __name__ == "__main__":
    main()
