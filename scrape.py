import re
import requests
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone

URL = "https://www.teleamazonas.com/programacion/"
CHANNEL_ID = "teleamazonas.ec"
CHANNEL_NAME = "Teleamazonas"
OUT_FILE = "teleamazonas.xml"

TZ_EC = timezone(timedelta(hours=-5))
HEADERS = {"User-Agent": "Mozilla/5.0 (teleamazonas-epg)"}

TIME_RE = re.compile(r"^(?:\d|[01]\d|2[0-3]):[0-5]\d$")

def fetch_html() -> str:
    r = requests.get(URL, headers=HEADERS, timeout=25)
    r.raise_for_status()
    return r.text

def clean_title(t: str) -> str:
    t = re.sub(r"\s+", " ", t).strip()
    bad = {
        "quito","guayaquil","programación","programacion","parrilla","parrilla de programación","parrilla de programacion",
        "lunes","martes","miércoles","miercoles","jueves","viernes","sábado","sabado","domingo"
    }
    if t.lower() in bad:
        return ""
    return t

def find_active_schedule_root(soup: BeautifulSoup):
    """
    Try multiple patterns to locate only the currently selected day's schedule panel.
    Returns a Tag or None.
    """
    # Pattern A: tabs w/ aria-selected=true
    active_tab = soup.select_one('[aria-selected="true"]')
    if active_tab:
        # If tab points to a panel via href="#panelid" or aria-controls="panelid"
        href = active_tab.get("href", "")
        aria_controls = active_tab.get("aria-controls", "")
        target_id = ""
        if href.startswith("#"):
            target_id = href[1:]
        elif aria_controls:
            target_id = aria_controls

        if target_id:
            panel = soup.find(id=target_id)
            if panel:
                return panel

        # Otherwise, walk up/down a bit and look for a nearby panel that is marked active
        parent = active_tab.parent
        if parent:
            candidate = parent.find_next(lambda tag: tag.name in ("section","div") and (
                "active" in " ".join(tag.get("class", [])).lower()
                or "is-active" in " ".join(tag.get("class", [])).lower()
            ))
            if candidate:
                return candidate

    # Pattern B: active panel class
    panel = soup.select_one(".active, .is-active, .tab-pane.active, .tabs-content .active")
    if panel:
        return panel

    # Pattern C: schedule heading container
    # Find the "PARRILLA DE PROGRAMACIÓN" heading, then take the next large container
    heading = soup.find(string=re.compile(r"parrilla\s+de\s+program", re.IGNORECASE))
    if heading:
        htag = heading.parent
        if htag:
            nxt = htag.find_next("div")
            if nxt:
                return nxt

    return None

def extract_pairs_from_root(root) -> list[tuple[str, str]]:
    """
    Extract (HH:MM, title) pairs from ONLY the active schedule root.
    """
    tokens = [s.strip() for s in root.stripped_strings if s and s.strip()]

    pairs = []
    i = 0
    while i < len(tokens) - 1:
        if TIME_RE.match(tokens[i]):
            time_str = tokens[i]
            j = i + 1
            # next non-time token is title
            while j < len(tokens) and TIME_RE.match(tokens[j]):
                j += 1
            if j < len(tokens):
                title = clean_title(tokens[j])
                if title:
                    pairs.append((time_str, title))
            i = j + 1
        else:
            i += 1

    # Dedup preserve order
    seen = set()
    out = []
    for t, title in pairs:
        key = (t, title)
        if key not in seen:
            seen.add(key)
            out.append((t, title))
    return out

def build_programmes(tv, base_date, pairs):
    current_date = base_date
    prev_minutes = None
    starts = []

    for hhmm, title in pairs:
        hh, mm = map(int, hhmm.split(":"))
        minutes = hh * 60 + mm

        # midnight rollover
        if prev_minutes is not None and minutes < prev_minutes:
            current_date = current_date + timedelta(days=1)

        dt = datetime(current_date.year, current_date.month, current_date.day, hh, mm, tzinfo=TZ_EC)
        starts.append((dt, title))
        prev_minutes = minutes

    for idx, (start_dt, title) in enumerate(starts):
        stop_dt = starts[idx + 1][0] if idx + 1 < len(starts) else start_dt + timedelta(minutes=30)
        prog = ET.SubElement(tv, "programme", channel=CHANNEL_ID)
        prog.set("start", start_dt.strftime("%Y%m%d%H%M%S %z"))
        prog.set("stop", stop_dt.strftime("%Y%m%d%H%M%S %z"))
        ET.SubElement(prog, "title").text = title

def main():
    html = fetch_html()
    soup = BeautifulSoup(html, "html.parser")

    root = find_active_schedule_root(soup)
    if not root:
        # Fallback: whole page (not ideal), but at least doesn't crash
        root = soup

    pairs = extract_pairs_from_root(root)
    print(f"Programmes extracted: {len(pairs)}")

    tv = ET.Element("tv", attrib={"generator-info-name": "teleamazonas-active-day"})
    ch = ET.SubElement(tv, "channel", id=CHANNEL_ID)
    ET.SubElement(ch, "display-name").text = CHANNEL_NAME

    today_ec = datetime.now(TZ_EC).date()
    base_dt = datetime(today_ec.year, today_ec.month, today_ec.day, tzinfo=TZ_EC)

    build_programmes(tv, base_dt, pairs)

    ET.ElementTree(tv).write(OUT_FILE, encoding="utf-8", xml_declaration=True)

if __name__ == "__main__":
    main()
