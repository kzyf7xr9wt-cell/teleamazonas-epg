import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

TZ_EC = timezone(timedelta(hours=-5))

# Teleamazonas internal schedule API (used by their site)
API_URL = "https://www.teleamazonas.com/api/programacion"

CHANNELS = {
    "teleamazonas.ec.quito": "Quito",
    "teleamazonas.ec.guayaquil": "Guayaquil",
}

DAYS = 7


def fetch_day(city, date_str):
    params = {
        "ciudad": city.lower(),   # quito / guayaquil
        "fecha": date_str,        # YYYY-MM-DD
    }
    r = requests.get(API_URL, params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def build_xml():
    tv = ET.Element("tv", attrib={"generator-info-name": "teleamazonas-api"})

    # Channel definitions
    for cid, city in CHANNELS.items():
        ch = ET.SubElement(tv, "channel", id=cid)
        ET.SubElement(ch, "display-name").text = f"Teleamazonas ({city.capitalize()})"

    today = datetime.now(TZ_EC).date()

    for cid, city in CHANNELS.items():
        for d in range(DAYS):
            date = today + timedelta(days=d)
            date_str = date.isoformat()

            try:
                data = fetch_day(city, date_str)
            except Exception as e:
                print("Fetch failed:", city, date_str, e)
                continue

            programs = data.get("programacion", [])

            for p in programs:
                start_str = p.get("hora_inicio")
                title = p.get("titulo")

                if not start_str or not title:
                    continue

                hh, mm = map(int, start_str.split(":"))
                start_dt = datetime(
                    date.year, date.month, date.day, hh, mm, tzinfo=TZ_EC
                )

                duration_min = int(p.get("duracion", 30))
                stop_dt = start_dt + timedelta(minutes=duration_min)

                prog = ET.SubElement(tv, "programme", channel=cid)
                prog.set("start", start_dt.strftime("%Y%m%d%H%M%S %z"))
                prog.set("stop", stop_dt.strftime("%Y%m%d%H%M%S %z"))
                ET.SubElement(prog, "title").text = title

    ET.ElementTree(tv).write(
        "teleamazonas.xml", encoding="utf-8", xml_declaration=True
    )


if __name__ == "__main__":
    build_xml()
