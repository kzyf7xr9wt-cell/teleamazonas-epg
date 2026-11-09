
#!/usr/bin/env python3
"""Teleamazonas weekly XMLTV scraper. pip install requests beautifulsoup4 lxml"""
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

URL = 'https://www.teleamazonas.com/programacion/'
OUTPUT = Path('teleamazonas.xml')
CHANNEL_ID = 'teleamazonas.ec'
EC_TZ = timezone(timedelta(hours=-5))
WEEKDAYS = {'lunes': 0, 'martes': 1, 'miercoles': 2, 'jueves': 3,
            'viernes': 4, 'sabado': 5, 'domingo': 6}
HEADERS = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36'}


def normalize(value):
    value = unicodedata.normalize('NFKD', value.lower())
    return ''.join(c for c in value if not unicodedata.combining(c)).strip()


def day_index(text):
    for token in re.findall(r'[a-z]+', normalize(text)):
        if token in WEEKDAYS:
            return WEEKDAYS[token]
    return None


def time_value(text):
    text = ' '.join(text.split()).upper()
    match = re.search(r'\b(\d{1,2}):(\d{2})\s*(AM|PM)?\b', text)
    if not match:
        return None
    hour, minute = int(match[1]), int(match[2])
    suffix = match[3]
    if suffix:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if suffix == 'PM' else 0)
    if hour > 23 or minute > 59:
        return None
    return hour, minute


def extract(article, day):
    rows = []
    for item in article.select('.c-list-tv-simple__txt'):
        span, title = item.find('span'), item.find('p')
        if not span or not title:
            continue
        hm = time_value(span.get_text(' ', strip=True))
        name = ' '.join(title.get_text(' ', strip=True).split())
        if hm and name:
            dt = datetime(day.year, day.month, day.day, *hm, tzinfo=EC_TZ)
            rows.append((dt, name))
    # The site's midnight entries already have a 00:xx clock value. Sorting
    # chronologically is correct; don't add a second day to them.
    rows.sort(key=lambda p: p[0])
    unique = {}
    for dt, name in rows:
        unique.setdefault(dt, name)
    return sorted(unique.items())


def find_schedule(soup):
    """Prefer a self-contained seven-tab group; avoid mixing city schedules."""
    groups = []
    for tablist in soup.select('ul.c-list-tv__tabs'):
        tabs = tablist.select('li.c-list-tv__tabs-item')
        days = [day_index(t.get_text(' ', strip=True)) for t in tabs]
        if len(days) < 7 or len(set(x for x in days if x is not None)) != 7:
            continue
        parent = tablist.parent
        wrapper = parent.select_one('.c-list-tv__tabs__sections') if parent else None
        if wrapper is None:
            grandparent = parent.parent if parent else None
            wrapper = grandparent.select_one('.c-list-tv__tabs__sections') if grandparent else None
        if not wrapper:
            continue
        articles = [a for a in wrapper.select('article') if a.select_one('.c-list-tv-simple__txt')]
        if len(articles) < 7:
            continue
        if len(articles) != len(tabs):
            continue
        groups.append((days, articles))
    if not groups:
        raise RuntimeError('Could not match seven weekday tabs with seven schedule panels; website markup may have changed.')
    return groups[0]


def make_programmes(days, articles, today):
    monday = today - timedelta(days=today.weekday())
    daily = {}
    for weekday, article in zip(days, articles):
        date = monday + timedelta(days=weekday)
        points = extract(article, date)
        if len(points) < 5:
            raise RuntimeError(f'Only {len(points)} shows found for {date}; refusing incomplete guide.')
        daily[weekday] = points
    if set(daily) != set(range(7)):
        raise RuntimeError('Not all seven weekdays were found.')
    points = sorted((p for rows in daily.values() for p in rows), key=lambda p: p[0])
    programmes = []
    for i, (start, title) in enumerate(points):
        next_start = points[i + 1][0] if i + 1 < len(points) else start + timedelta(minutes=30)
        stop = min(next_start, start + timedelta(hours=8))
        if stop > start:
            programmes.append((start, stop, title))
    if len(programmes) < 70:
        raise RuntimeError(f'Only {len(programmes)} programmes found; refusing to overwrite existing guide.')
    return programmes


def main():
    response = requests.get(URL, headers=HEADERS, timeout=35)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
days, articles = find_schedule(soup)
    today = datetime.now(EC_TZ).date()
    programmes = make_programmes(days, articles, today)

    tv = ET.Element('tv', {'generator-info-name': 'teleamazonas-scraper'})
    channel = ET.SubElement(tv, 'channel', {'id': CHANNEL_ID})
    ET.SubElement(channel, 'display-name').text = 'Teleamazonas'
    for start, stop, title in programmes:
        pr = ET.SubElement(tv, 'programme', {
            'channel': CHANNEL_ID,
            'start': start.strftime('%Y%m%d%H%M%S %z'),
            'stop': stop.strftime('%Y%m%d%H%M%S %z'),
        })
        ET.SubElement(pr, 'title').text = title
    ET.indent(tv, space='  ')
    payload = ET.tostring(tv, encoding='utf-8', xml_declaration=True)
    with tempfile.NamedTemporaryFile(mode='wb', dir=OUTPUT.parent, delete=False) as temp:
        temp.write(payload + b'\n')
        temp_path = temp.name
    os.replace(temp_path, OUTPUT)
    print(f'Wrote {OUTPUT}: {len(programmes)} programmes, week of {today - timedelta(days=today.weekday())}')
    print('Days:', ', '.join(str((today - timedelta(days=today.weekday())) + timedelta(days=d)) for d in sorted(days)))


if __name__ == '__main__':
    main()
