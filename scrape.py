#!/usr/bin/env python3
# ecuador_epg.py
#
# Multi-channel XMLTV generator:
# - Teleamazonas (https://www.teleamazonas.com/programacion/) 7-day schedule
# - ECDF (https://elcanaldelfutbol.com/envivo/) today's guide-of-programming
#
# Requirements:
#   pip install requests beautifulsoup4
#
# Notes:
# - Ecuador time is UTC-5 (no DST).
# - ECDF page is a live/portal page; we extract the "Guía de Programación" text block
#   and pair each TITLE line with the following "HH:MM - HH:MM" line.

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

ECUADOR_TZ = timezone(timedelta(hours=-5))  # UTC-5, no DST

OUTPUT_XML = "ecuador.xml"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}

# Teleamazonas
TA_URL = "https://www.teleamazonas.com/programacion/"
TA_CHANNEL_ID = "teleamazonas.ec"
TA_DISPLAY_NAME = "Teleamazonas"
TA_BUILD_FULL_WEEK = True

# ECDF
ECDF_URL = "https://elcanaldelfutbol.com/envivo/"
ECDF_CHANNEL_ID = "ecdf.ec"
ECDF_DISPLAY_NAME = "ECDF"

# -----------------------------
# HELPERS
# -----------------------------

WS_RE = re.compile(r"\s+")
TIME_HHMM_RE = re.compile(r"^(\d{1,2}):(\d{2})$")
TIME_RANGE_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*$", re.I)

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


def clean_text(s: str) -> str:
    s = s.replace("\xa0", " ")
    s = WS_RE.sub(" ", s).strip()
    # Remove old bug prefixes like "AM AM", "PM PM", etc.
    s = re.sub(r"^(AM|PM)\s+(AM|PM)\s+", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^(AM|PM)\s+", "", s, flags=re.IGNORECASE)
    return s.strip()


def parse_hhmm(hhmm: str) -> Tuple[int, int]:
    hhmm = clean_text(hhmm)
    m = TIME_HHMM_RE.match(hhmm)
    if not m:
        raise ValueError(f"Bad time format: {hhmm!r}")
    return int(m.group(1)), int(m.group(2))


def xml_escape(s: str) -> str:
    return (s.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&apos;"))


def fetch_html(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text


def dt_to_xmltv(dt: datetime) -> str:
    # XMLTV: YYYYMMDDHHMMSS ±ZZZZ
    return dt.strftime("%Y%m%d%H%M%S %z")


@dataclass
class Programme:
    channel_id: str
    start: datetime
    stop: datetime
    title: str


@dataclass
class Channel:
    channel_id: str
    display_name: str


# -----------------------------
# TELEAMAZONAS SCRAPER
# -----------------------------

def find_ta_tabs_and_sections(soup: BeautifulSoup):
    """
    Tabs: <li class="c-list-tv__tabs-item ...">Miércoles</li>
    Sections wrapper: <div class="c-list-tv__tabs__sections"> ... <article ...> ... </article> ...
    """
    tabs = soup.select("ul.c-list-tv__tabs li.c-list-tv__tabs-item")
    sections_wrap = soup.select_one("div.c-list-tv__tabs__sections") or soup.select_one(".c-list-tv__tabs__sections")
    articles = sections_wrap.select("article") if sections_wrap else []
    return tabs, articles


def ta_extract_points_from_article(article, base_date: datetime) -> List[Tuple[datetime, str]]:
    """
    Extract (datetime, title) points from a day's article.
    Each show item looks like:
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

    dedup.sort(key=lambda x: x[0])
    return dedup


def points_to_programmes(channel_id: str, points: List[Tuple[datetime, str]]) -> List[Programme]:
    """
    Convert timepoints into Programme blocks by using the next show's start as stop.
    Handles midnight rollover: if times go backwards, add +1 day.
    """
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

        progs.append(Programme(channel_id=channel_id, start=start_dt, stop=stop_dt, title=title))

    return progs


def ta_get_active_tab_day_index(tabs) -> Optional[int]:
    for li in tabs:
        classes = li.get("class") or []
        if "active" in classes:
            name = clean_text(li.get_text(" ", strip=True)).lower()
            return DAY_NAME_TO_INDEX.get(name)
    return None


def scrape_teleamazonas() -> List[Programme]:
    html = fetch_html(TA_URL)
    soup = BeautifulSoup(html, "html.parser")

    tabs, articles = find_ta_tabs_and_sections(soup)
    if not articles:
        raise RuntimeError("Teleamazonas: could not find schedule articles. Page structure may have changed.")

    today_ec = datetime.now(ECUADOR_TZ).date()

    all_programmes: List[Programme] = []

    if TA_BUILD_FULL_WEEK:
        if len(articles) < 7:
            candidate_articles = [a for a in articles if a.select_one("div.c-list-tv-simple__txt")]
            articles = candidate_articles

        if len(articles) < 7:
            raise RuntimeError(f"Teleamazonas: expected 7 day articles, found {len(articles)}")

        active_idx = ta_get_active_tab_day_index(tabs)
        if active_idx is None:
            active_idx = datetime.now(ECUADOR_TZ).weekday()

        for i in range(7):
            day_date = today_ec + timedelta(days=(i - active_idx))
            base_dt = datetime(day_date.year, day_date.month, day_date.day, 0, 0, tzinfo=ECUADOR_TZ)

            points = ta_extract_points_from_article(articles[i], base_dt)
            progs = points_to_programmes(TA_CHANNEL_ID, points)
            all_programmes.extend(progs)
    else:
        # Visible-day-only mode (kept for completeness)
        visible = next((a for a in articles if "visible" in (a.get("class") or [])), None)
        if not visible:
            visible = next((a for a in articles if a.select_one("div.c-list-tv-simple__txt")), None)
        if not visible:
            raise RuntimeError("Teleamazonas: no visible schedule article found.")

        base_dt = datetime(today_ec.year, today_ec.month, today_ec.day, 0, 0, tzinfo=ECUADOR_TZ)
        points = ta_extract_points_from_article(visible, base_dt)
        all_programmes = points_to_programmes(TA_CHANNEL_ID, points)

    # Sort and remove exact duplicates
    all_programmes.sort(key=lambda p: (p.start, p.stop, p.title.lower()))
    dedup: List[Programme] = []
    last_key = None
    for p in all_programmes:
        key = (p.start, p.stop, p.title.lower())
        if key != last_key:
            dedup.append(p)
        last_key = key

    return dedup


# -----------------------------
# ECDF SCRAPER (TEXT-BASED, ROBUST)
# -----------------------------

def _meaningful_line(s: str) -> bool:
    s = clean_text(s)
    if not s:
        return False
    bad = {
        "en vivo",
        "quedan:",
        "guía de programación",
        "guia de programación",
        "canales",
        "todos los canales fútbol hípica golf",
        "suscríbete",
        "suscríbete para continuar",
        "este contenido requiere una suscripción activa. mejora tu plan para verlo.",
        "empty !!",
    }
    return s.lower() not in bad


def scrape_ecdf() -> List[Programme]:
    html = fetch_html(ECDF_URL)
    soup = BeautifulSoup(html, "html.parser")

    # We extract text lines and focus on the block after "Guía de Programación"
    lines = [clean_text(x) for x in soup.get_text("\n").split("\n")]
    lines = [x for x in lines if x]  # keep non-empty

    # Find where the guide starts
    start_idx = None
    for i, line in enumerate(lines):
        if line.lower() in ("guía de programación", "guia de programación"):
            start_idx = i + 1
            break

    if start_idx is None:
        raise RuntimeError("ECDF: could not find 'Guía de Programación' in page text.")

    # End near subscription gate if present
    end_idx = len(lines)
    for i in range(start_idx, len(lines)):
        if lines[i].lower().startswith("suscríbete para continuar") or lines[i].lower().startswith("suscribete para continuar"):
            end_idx = i
            break

    guide_lines = lines[start_idx:end_idx]

    today_ec = datetime.now(ECUADOR_TZ).date()

    programmes: List[Programme] = []

    # We look for a pattern:
    #   TITLE
    #   HH:MM - HH:MM
    # (There may be extra noise lines like "En vivo", images, "quedan:", etc.)
    last_title: Optional[str] = None

    for line in guide_lines:
        if not line:
            continue

        m = TIME_RANGE_RE.match(line)
        if m:
            if not last_title:
                # If there's no title, skip
                continue

            sh, sm, eh, em = map(int, m.groups())

            start_dt = datetime(today_ec.year, today_ec.month, today_ec.day, sh, sm, tzinfo=ECUADOR_TZ)
            stop_dt = datetime(today_ec.year, today_ec.month, today_ec.day, eh, em, tzinfo=ECUADOR_TZ)

            # Handle midnight rollover
            if stop_dt <= start_dt:
                stop_dt = stop_dt + timedelta(days=1)

            programmes.append(Programme(
                channel_id=ECDF_CHANNEL_ID,
                start=start_dt,
                stop=stop_dt,
                title=last_title
            ))

            # reset title so we don't accidentally reuse it
            last_title = None
            continue

        # Update last_title when we see a meaningful line that isn't time-range
        if _meaningful_line(line) and not TIME_RANGE_RE.match(line):
            last_title = line

    # Dedup + sort
    programmes.sort(key=lambda p: (p.start, p.stop, p.title.lower()))
    dedup: List[Programme] = []
    seen = set()
    for p in programmes:
        key = (p.channel_id, p.start, p.stop, p.title.lower())
        if key not in seen:
            seen.add(key)
            dedup.append(p)

    if not dedup:
        raise RuntimeError("ECDF: parsed 0 programmes. Page structure/content may have changed.")

    return dedup


# -----------------------------
# XMLTV OUTPUT (MULTI-CHANNEL)
# -----------------------------

def build_xml(channels: List[Channel], programmes: List[Programme]) -> str:
    out: List[str] = []
    out.append('<?xml version="1.0" encoding="UTF-8"?>')
    out.append('<tv generator-info-name="ecuador-multi-scraper">')

    for ch in channels:
        out.append(f'  <channel id="{xml_escape(ch.channel_id)}">')
        out.append(f'    <display-name>{xml_escape(ch.display_name)}</display-name>')
        out.append('  </channel>')

    for pr in programmes:
        out.append(
            f'  <programme channel="{xml_escape(pr.channel_id)}" '
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
    channels = [
        Channel(channel_id=TA_CHANNEL_ID, display_name=TA_DISPLAY_NAME),
        Channel(channel_id=ECDF_CHANNEL_ID, display_name=ECDF_DISPLAY_NAME),
    ]

    all_programmes: List[Programme] = []
    all_programmes.extend(scrape_teleamazonas())
    all_programmes.extend(scrape_ecdf())

    # Global sort + dedup (just in case)
    all_programmes.sort(key=lambda p: (p.channel_id, p.start, p.stop, p.title.lower()))
    final: List[Programme] = []
    last_key = None
    for p in all_programmes:
        key = (p.channel_id, p.start, p.stop, p.title.lower())
        if key != last_key:
            final.append(p)
        last_key = key

    xml = build_xml(channels, final)

    with open(OUTPUT_XML, "w", encoding="utf-8") as f:
        f.write(xml)

    print(f"Wrote {OUTPUT_XML} with {len(final)} programmes across {len(channels)} channels.")


if __name__ == "__main__":
    main()
