
#!/usr/bin/env python3
# Teleamazonas EPG Scraper
# Output: teleamazonas.xml
# Requirements: requests, beautifulsoup4
#
# Automatically builds a weekly XMLTV guide.
# Ecuador timezone: UTC-5.

from __future__ import annotations

import os
import re
import tempfile
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

import requests
from bs4 import BeautifulSoup

# CONFIGURATION

URL = "https://www.teleamazonas.com/programacion/"
OUTPUT = Path("teleamazonas.xml")

CHANNEL_ID = "teleamazonas.ec"
CHANNEL_NAME = "Teleamazonas"

EC_TZ = timezone(timedelta(hours=-5))

WEEKDAYS = {
    "lunes": 0,
    "martes": 1,
    "miercoles": 2,
    "jueves": 3,
    "viernes": 4,
    "sabado": 5,
    "domingo": 6,
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/131.0 Safari/537.36"
    )
}


def normalize(value):
    value = unicodedata.normalize("NFKD", value.lower())
    return "".join(
        c for c in value
        if not unicodedata.combining(c)
    ).strip()


def day_index(text):
    for token in re.findall(r"[a-z]+", normalize(text)):
        if token in WEEKDAYS:
            return WEEKDAYS[token]
    return None


def time_value(text):
    text = " ".join(text.split()).upper()

    match = re.search(
        r"\b(\d{1,2}):(\d{2})\s*(AM|PM)?\b",
        text
    )

    if not match:
        return None

    hour = int(match[1])
    minute = int(match[2])
    suffix = match[3]

    if suffix:
        if not 1 <= hour <= 12:
            return None

        hour = hour % 12 + (
            12 if suffix == "PM" else 0
        )

    if hour > 23 or minute > 59:
        return None

    return hour, minute


def extract(article, day):
    rows = []

    for item in article.select(
        ".c-list-tv-simple__txt"
    ):
        span = item.find("span")
        title = item.find("p")

        if not span or not title:
            continue

        hm = time_value(
            span.get_text(" ", strip=True)
        )

        name = " ".join(
            title.get_text(
                " ", strip=True
            ).split()
        )

        if hm and name:
            dt = datetime(
                day.year,
                day.month,
                day.day,
                *hm,
                tzinfo=EC_TZ
            )

            rows.append((dt, name))

    rows.sort(key=lambda p: p[0])

    unique = {}

    for dt, name in rows:
        unique.setdefault(dt, name)

    return sorted(unique.items())


def find_schedule(soup):
    groups = []

    for tablist in soup.select(
        "ul.c-list-tv__tabs"
    ):
        tabs = tablist.select(
            "li.c-list-tv__tabs-item"
        )

        days = [
            day_index(
                t.get_text(" ", strip=True)
            )
            for t in tabs
        ]

        if len(days) < 7:
            continue

        if len(set(
            x for x in days
            if x is not None
        )) != 7:
            continue

        parent = tablist.parent

        wrapper = (
            parent.select_one(
                ".c-list-tv__tabs__sections"
            )
            if parent else None
        )

        if wrapper is None:
            grandparent = (
                parent.parent
                if parent else None
            )

            wrapper = (
                grandparent.select_one(
                    ".c-list-tv__tabs__sections"
                )
                if grandparent else None
            )

        if not wrapper:
            continue

        articles = [
            a for a in wrapper.select("article")
            if a.select_one(
                ".c-list-tv-simple__txt"
            )
        ]

        if len(articles) != len(tabs):
            continue

        groups.append((days, articles))

    if not groups:
        raise RuntimeError(
            "Could not match seven weekday tabs "
            "with seven schedule panels. "
            "Website structure may have changed."
        )

    return groups[0]


def make_programmes(days, articles, today):
    monday = today - timedelta(
        days=today.weekday()
    )

    daily = {}

    for weekday, article in zip(
        days, articles
    ):
        date = monday + timedelta(
            days=weekday
        )

        points = extract(article, date)

        if len(points) < 5:
            raise RuntimeError(
                f"Only {len(points)} shows "
                f"found for {date}."
            )

        daily[weekday] = points

    if set(daily) != set(range(7)):
        raise RuntimeError(
            "Not all seven weekdays were found."
        )

    points = sorted(
        (
            p
            for rows in daily.values()
            for p in rows
        ),
        key=lambda p: p[0]
    )

    programmes = []

    for i, (start, title) in enumerate(
        points
    ):
        if i + 1 < len(points):
            next_start = points[i + 1][0]
        else:
            next_start = (
                start + timedelta(minutes=30)
            )

        stop = min(
            next_start,
            start + timedelta(hours=8)
        )

        if stop > start:
            programmes.append(
                (start, stop, title)
            )

    if len(programmes) < 70:
        raise RuntimeError(
            "Incomplete schedule. "
            "Existing XML will not be overwritten."
        )

    return programmes


def main():
    print("Downloading Teleamazonas schedule...")

    response = requests.get(
        URL,
        headers=HEADERS,
        timeout=35
    )

    response.raise_for_status()

    # Built-in parser. No lxml required.
    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    days, articles = find_schedule(soup)

    today = datetime.now(EC_TZ).date()

    programmes = make_programmes(
        days,
        articles,
        today
    )

    tv = ET.Element(
        "tv",
        {
            "generator-info-name":
            "teleamazonas-scraper"
        }
    )

    channel = ET.SubElement(
        tv,
        "channel",
        {"id": CHANNEL_ID}
    )

    ET.SubElement(
        channel,
        "display-name"
    ).text = CHANNEL_NAME

    for start, stop, title in programmes:
        pr = ET.SubElement(
            tv,
            "programme",
            {
                "channel": CHANNEL_ID,
                "start": start.strftime(
                    "%Y%m%d%H%M%S %z"
                ),
                "stop": stop.strftime(
                    "%Y%m%d%H%M%S %z"
                )
            }
        )

        ET.SubElement(
            pr,
            "title"
        ).text = title

    ET.indent(tv, space="  ")

    payload = ET.tostring(
        tv,
        encoding="utf-8",
        xml_declaration=True
    )

    with tempfile.NamedTemporaryFile(
        mode="wb",
        dir=OUTPUT.parent,
        delete=False
    ) as temp:
        temp.write(payload + b"\n")
        temp_path = temp.name

    os.replace(
        temp_path,
        OUTPUT
    )

    print(
        f"SUCCESS: Wrote {OUTPUT} "
        f"with {len(programmes)} programmes."
    )

    print(
        "Week starting:",
        today - timedelta(
            days=today.weekday()
        )
    )


if __name__ == "__main__":
    main()
