import requests
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
from datetime import datetime, timedelta

BASE_URL = "https://www.gatotv.com/canal/teleamazonas/"
CHANNEL_ID = "teleamazonas.ec"
DAYS = 7

def fetch_day_html(date_str):
    url = f"{BASE_URL}{date_str}"
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
    r.raise_for_status()
    return r.text

def parse_schedule(html, date_obj):
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select("table tr")

    schedule = []
    for row in rows:
        cols = row.find_all("td")
        if len(cols) >= 2:
            time_text = cols[0].get_text(strip=True)
            title_text = cols[1].get_text(strip=True)
            if time_text and title_text:
                # normalize time format like "22:30"
                schedule.append((time_text, title_text))
    return schedule

def build_xml():
    tv = ET.Element("tv", attrib={"generator-info-name": "gatotv-scraper"})
    ch = ET.SubElement(tv, "channel", id=CHANNEL_ID)
    ET.SubElement(ch, "display-name").text = "Teleamazonas"

    today = datetime.utcnow().date()

    for d in range(DAYS):
        date_obj = today + timedelta(days=d)
        date_str = date_obj.strftime("%Y-%m-%d")
        html = fetch_day_html(date_str)
        schedule = parse_schedule(html, date_obj)

        # Build start times + stops
        prev_dt = None
        for i, (time_text, title) in enumerate(schedule):
            hh, mm = map(int, time_text.split(":"))
            start_dt = datetime(
                date_obj.year, date_obj.month, date_obj.day, hh, mm
            )

            if i + 1 < len(schedule):
                next_hh, next_mm = map(int, schedule[i+1][0].split(":"))
                stop_dt = datetime(date_obj.year, date_obj.month, date_obj.day, next_hh, next_mm)
            else:
                stop_dt = start_dt + timedelta(minutes=30)

            prog = ET.SubElement(tv, "programme", channel=CHANNEL_ID)
            prog.set("start", start_dt.strftime("%Y%m%d%H%M%S"))
            prog.set("stop", stop_dt.strftime("%Y%m%d%H%M%S"))
            ET.SubElement(prog, "title").text = title

    # Write XML
    ET.ElementTree(tv).write("teleamazonas.xml", encoding="utf-8", xml_declaration=True)

if __name__ == "__main__":
    build_xml()
