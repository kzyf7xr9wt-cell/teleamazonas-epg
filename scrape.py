import requests
import xml.etree.ElementTree as ET

SOURCE_XML = "https://epgshare01.online/epgshare01/epg_ripper_EC1.xml"

TARGET_CHANNELS = {
    "Teleamazonas.ec": {
        "quito": "teleamazonas.ec.quito",
        "guayaquil": "teleamazonas.ec.guayaquil",
    }
}

def main():
    r = requests.get(SOURCE_XML, timeout=30)
    r.raise_for_status()

    root = ET.fromstring(r.content)

    out_tv = ET.Element("tv", attrib={"generator-info-name": "teleamazonas-filter"})

    # Create channels
    for _, mapping in TARGET_CHANNELS.items():
        for display_id in mapping.values():
            ch = ET.SubElement(out_tv, "channel", id=display_id)
            ET.SubElement(ch, "display-name").text = display_id

    # Copy programmes that match Teleamazonas
    for prog in root.findall("programme"):
        ch = prog.get("channel", "")
        if "teleamazonas" in ch.lower():
            for display_id in TARGET_CHANNELS["Teleamazonas.ec"].values():
                new_prog = ET.SubElement(out_tv, "programme", attrib=prog.attrib)
                for child in list(prog):
                    new_prog.append(child)

    ET.ElementTree(out_tv).write(
        "teleamazonas.xml", encoding="utf-8", xml_declaration=True
    )

if __name__ == "__main__":
    main()
