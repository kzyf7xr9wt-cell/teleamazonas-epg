import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import xml.etree.ElementTree as ET

BASE_URL = "https://www.teleamazonas.com/programacion/"

CHANNELS = {
    "teleamazonas.ec.quito": "Quito",
    "teleamazonas.ec.guayaquil": "Guayaquil"
}

def fetch_schedule(city):
    r = requests.get(BASE_URL, timeout=15)
    soup = BeautifulSoup(r.text, "html.parser")

    items = soup.select(".programacion-item")
    schedule = []

    for item in items:
        time = item.select_one(".hora")
        title = item.select_one(".titulo")

        if not time or not title:
            continue

        schedule.append((time.text.strip(), title.text.strip()))

    return schedule

def build_xml():
    tv = ET.Element("tv")

    for cid, name in CHANNELS.items():
        ch = ET.SubElement(tv, "channel", id=cid)
        ET.SubElement(ch, "display-name").text = f"Teleamazonas ({name})"

    now = datetime.utcnow()

    for cid, city in CHANNELS.items():
        schedule = fetch_schedule(city)

        start = now.replace(hour=5, minute=0, second=0)

        for t, title in schedule:
            prog = ET.SubElement(tv, "programme", channel=cid)
            prog.set("start", start.strftime("%Y%m%d%H%M%S -0500"))
            start += timedelta(minutes=30)
            prog.set("stop", start.strftime("%Y%m%d%H%M%S -0500"))
            ET.SubElement(prog, "title").text = title

    tree = ET.ElementTree(tv)
    tree.write("teleamazonas.xml", encoding="utf-8", xml_declaration=True)

if __name__ == "__main__":
    build_xml()
