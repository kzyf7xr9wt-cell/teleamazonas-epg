import re
import requests
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone

BASE_URL = "https://www.gatotv.com/canal/teleamazonas/"
CHANNEL_ID = "teleamazonas.ec"
DAYS = 7

TIME_RE = re.compile(r"^(?:[01]?\d|2[0-3]):[0-5]\d$")  # HH:MM

HEADERS = {
    "User-Agent": "Mozilla/5.0 (EPG Bot)"
}

def fetch_day_html(date_obj):
    date_str = date_obj.strftime("%Y-%m-%d")
    url = f"{BASE_URL}{date_str}"
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.text


def parse_schedule(html):
    soup = BeautifulSoup(html, "html.parser")
    schedule = []

    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 2:
            continue

        time_text = tds[0].get_text(" ", strip=True)
        title_text = tds[1].get_text(" ", strip=True)

        if TIME_RE.match(time_text) and title_text:
            schedule.append((time_text, title_text))

    return schedule


def build_xml():
    tv = ET.Element("tv", attrib={"generator-info-name": "gatotv-scraper"})

    channel = ET.SubElement(tv, "channel", id=CHANNEL_ID)
    ET.SubElement(channel, "display-name").text = "Teleamazonas"

    today = datetime.now(timezone.utc).date()

    for d in range(DAYS):
        date_obj = today + timedelta(days=d)
        html = fetch_day_html(date_obj)
        schedule = parse_schedule(html)

        for i, (time_text, title) in enumerate(schedule):
            hh, mm = map(int, time_text.split(":"))
            start_dt = datetime(
                date_obj.year, date_obj.month, date_obj.day,
                hh, mm, tzinfo=timezone.utc
            )

            if i + 1 < len(schedule):
                nh, nm = map(int, schedule[i+1][0].split(":"))
                stop_dt = datetime(
                    date_obj.year, date_obj.month, date_obj.day,
                    nh, nm, tzinfo=timezone.utc
                )
            else:
                stop_dt = start_dt + timedelta(minutes=30)

            prog = ET.SubElement(tv, "programme", channel=CHANNEL_ID)
            prog.set("start", start_dt.strftime("%Y%m%d%H%M%S +0000"))
            prog.set("stop", stop_dt.strftime("%Y%m%d%H%M%S +0000"))
            ET.SubElement(prog, "title").text = title

    tree = ET.ElementTree(tv)
    tree.write("teleamazonas.xml", encoding="utf-8", xml_declaration=True)


if __name__ == "__main__":
    build_xml()
