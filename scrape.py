import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

TZ_EC = timezone(timedelta(hours=-5))

BASE_API = "https://www.teleamazonas.com/api/v1/programacion"

CHANNELS = {
    "teleamazonas.ec.quito": "quito",
    "teleamazonas.ec.guayaquil": "guayaquil",
}

DAYS = 7


def fetch(city, date):
    params = {
        "city": city,
        "date": date.strftime("%Y-%m-%d"),
    }
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
        "Referer": "https://www.teleamazonas.com/programacion/",
    }
    r = requests.get(BASE_API, params=params, headers=headers, timeout=20)
    r.raise_for_status()
    return r.json()


def main():
    tv = ET.Element("tv", attrib={"generator-info-name": "teleamazonas-api"})

    # Channels
    for cid in CHANNELS:
        ch = ET.SubElement(tv, "channel", id=cid)
        ET.SubElement(ch, "display-name").text = cid

    today = datetime.now(TZ_EC).date()

    for cid, city in CHANNELS.items():
        for d in range(DAYS):
            day = today + timedelta(days=d)

            try:
                data = fetch(city, day)
            except Exception as e:
                print("Fetch error:", city, day, e)
                continue

            programs = data.get("programacion") or data.get("data") or []

            for p in programs:
                title = p.get("titulo") or p.get("title")
                start = p.get("hora_inicio") or p.get("start")

                if not title or not start:
                    continue

                hh, mm = map(int, start.split(":"))
                start_dt = datetime(
                    day.year, day.month, day.day, hh, mm, tzinfo=TZ_EC
                )

                duration = int(p.get("duracion", 30))
                stop_dt = start_dt + timedelta(minutes=duration)

                prog = ET.SubElement(tv, "programme", channel=cid)
                prog.set("start", start_dt.strftime("%Y%m%d%H%M%S %z"))
                prog.set("stop", stop_dt.strftime("%Y%m%d%H%M%S %z"))
                ET.SubElement(prog, "title").text = title

    ET.ElementTree(tv).write(
        "teleamazonas.xml", encoding="utf-8", xml_declaration=True
    )


if __name__ == "__main__":
    main()
