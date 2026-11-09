
#!/usr/bin/env python3
"""GatoTV Teleamazonas -> XMLTV. Dependencies: requests, beautifulsoup4."""
from __future__ import annotations

import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

import requests
from bs4 import BeautifulSoup

BASE = 'https://www.gatotv.com/canal/teleamazonas'
OUTPUT = Path('teleamazonas.xml')
CHANNEL = 'teleamazonas.ec'
ECUADOR = timezone(timedelta(hours=-5))
HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 Chrome/131.0 Safari/537.36'
    )
}

TIME = r'(?:1[0-2]|0?[1-9]):[0-5][0-9]\s*[AP]\.?M\.?'
ROW = re.compile(
    r'^\s*(' + TIME + r')\s+(' + TIME + r')\s+(.+?)\s*$',
    re.I
)


def parse_time(s):
    s = re.sub(r'\.', '', s).upper().replace(' ', '')
    return datetime.strptime(s, '%I:%M%p').time()


def extract_rows(soup):
    rows = []

    for tr in soup.select('tr'):
        cells = tr.find_all(['td', 'th'], recursive=False)

        if len(cells) < 3:
            continue

        first = cells[0].get_text(' ', strip=True)
        second = cells[1].get_text(' ', strip=True)

        if not re.fullmatch(TIME, first, re.I):
            continue
        if not re.fullmatch(TIME, second, re.I):
            continue

        title = cells[2].get_text(' ', strip=True)
        link = cells[2].find('a')

        if link and link.get_text(' ', strip=True):
            title = link.get_text(' ', strip=True)

        if title:
            rows.append((first, second, title))

    if rows:
        return rows

    text = soup.get_text('\n', strip=True)
    lines = [
        re.sub(r'\s+', ' ', x).strip()
        for x in text.splitlines()
    ]

    for line in lines:
        match = ROW.match(line)
        if match:
            rows.append(match.groups())

    if rows:
        return rows

    for i in range(len(lines) - 2):
        if (
            re.fullmatch(TIME, lines[i], re.I)
            and re.fullmatch(TIME, lines[i + 1], re.I)
        ):
            title = lines[i + 2]
            if title and not re.fullmatch(TIME, title, re.I):
                rows.append((lines[i], lines[i + 1], title))

    return rows


def fetch_day(session, date):
    url = f'{BASE}/{date.isoformat()}'
    response = session.get(url, timeout=35)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, 'html.parser')

    if date.isoformat() not in response.url:
        raise ValueError(
            f'Date page redirected: {url} -> {response.url}'
        )

    rows = extract_rows(soup)

    if len(rows) < 10:
        raise ValueError(
            f'Only {len(rows)} programs found for {date}: {url}'
        )

    programmes = []

    for row_number, (start_text, stop_text, title) in enumerate(rows):
        start = datetime.combine(
            date, parse_time(start_text), ECUADOR
        )
        stop = datetime.combine(
            date, parse_time(stop_text), ECUADOR
        )

        if stop <= start:
            if row_number == 0 and start.hour >= 18:
                continue
            if start.hour >= 18:
                stop += timedelta(days=1)
            else:
                continue

        title = re.sub(r'\s+', ' ', title).strip()

        if title and stop > start:
            programmes.append((start, stop, title))

    if len(programmes) < 10:
        raise ValueError(
            f'Only {len(programmes)} valid programs for {date}'
        )

    return programmes


def main():
    today = datetime.now(ECUADOR).date()
    session = requests.Session()
    session.headers.update(HEADERS)

    all_items = []

    for offset in range(7):
        date = today + timedelta(days=offset)

        try:
            items = fetch_day(session, date)
        except (requests.RequestException, ValueError) as exc:
            if offset == 0:
                raise RuntimeError(
                    f'Current-day guide unavailable: {exc}'
                ) from exc

            print(f'Future day unavailable ({date}): {exc}')
            break

        print(f'{date}: {len(items)} programmes')
        all_items.extend(items)

    if len(all_items) < 10:
        raise RuntimeError(
            'Insufficient data; existing XML left untouched'
        )

    unique = {}

    for start, stop, title in all_items:
        unique.setdefault(start, (stop, title))

    starts = sorted(unique)
    cleaned = []

    for i, start in enumerate(starts):
        stop, title = unique[start]

        if i + 1 < len(starts):
            stop = min(stop, starts[i + 1])

        if stop > start:
            cleaned.append((start, stop, title))

    root = ET.Element(
        'tv',
        {'generator-info-name': 'gatotv-teleamazonas-scraper'}
    )

    channel = ET.SubElement(
        root, 'channel', {'id': CHANNEL}
    )

    ET.SubElement(
        channel, 'display-name'
    ).text = 'Teleamazonas'

    for start, stop, title in cleaned:
        programme = ET.SubElement(
            root,
            'programme',
            {
                'channel': CHANNEL,
                'start': start.strftime('%Y%m%d%H%M%S %z'),
                'stop': stop.strftime('%Y%m%d%H%M%S %z')
            }
        )

        ET.SubElement(programme, 'title').text = title

    ET.indent(root, space='  ')

    data = ET.tostring(
        root,
        encoding='utf-8',
        xml_declaration=True
    ) + b'\n'

    with tempfile.NamedTemporaryFile(
        mode='wb',
        dir=OUTPUT.parent,
        delete=False
    ) as temp:
        temp.write(data)
        tmp = temp.name

    os.replace(tmp, OUTPUT)

    print(
        f'SUCCESS: {len(cleaned)} programmes written to {OUTPUT}'
    )


if __name__ == '__main__':
    main()
