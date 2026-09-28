#!/usr/bin/env python3
"""5.47: сводный CSV всех отметок archi.ru (5.47) + отчёт."""
import csv
import json
import os

REPO = "/home/z/my-project/spb45"
EXT = "/home/z/my-project/wr/archi3/extract"
OUT_CSV = os.path.join(REPO, "results/archi_marks_all_5.47.csv")

# источник проекта для каждого изображения
PROJ = {
    "314154.jpg": ("15315", "Жилой дом на Октябрьской набережной", 59.872340, 30.469802),
    "448114.jpg": ("19557", "ЖК на Русановской улице", 59.864641, 30.502834),
    "456577.jpg": ("19768", "Дом на проспекте Римского-Корсакова 69", 59.921389, 30.287699),
    "422695.jpg": ("18834", "МФК 101-й остров (ВО)", 59.930571, 30.234707),
    "257941.jpg": ("10234", "Яхт-клуб, Невская губа", 59.977440, 30.228290),
    "493731.jpg": ("20773", "Деловой центр, пл. Конституции", 59.850605, 30.304640),
    "456638.jpg": ("19769", "ЖК на Евгеньевской", 59.931591, 30.378868),
    "317703.jpg": ("15443", "БЦ на Поклонной горе", 60.030858, 30.320734),
    "434384.jpg": ("19155", "ЖК на Матисовом острове", 59.924408, 30.275709),
    "465048.jpg": ("19996", "Санаторный комплекс, Приморское шоссе", 60.149544, 29.932849),
    "465070.jpg": ("19996", "Санаторный комплекс, Приморское шоссе", 60.149544, 29.932849),
    "465064.jpg": ("19996", "Санаторный комплекс, Приморское шоссе", 60.149544, 29.932849),
    "465065.jpg": ("19996", "Санаторный комплекс, Приморское шоссе", 60.149544, 29.932849),
    "465066.jpg": ("19996", "Санаторный комплекс, Приморское шоссе", 60.149544, 29.932849),
    "465067.jpg": ("19996", "Санаторный комплекс, Приморское шоссе", 60.149544, 29.932849),
    "465068.jpg": ("19996", "Санаторный комплекс, Приморское шоссе", 60.149544, 29.932849),
    "483622.jpg": ("20478", "К-30 летняя веранда", None, None),
    "428282.jpg": ("18974", "ЖК Шпалерная 51", 59.947769, 30.384216),
    "514913.png": ("21359", "Дунайский 27 (эталон 5.34/5.46)", 59.832799, 30.362699),
}


def main():
    rows = []
    # 1. merged 514913 (уже с геопривязкой)
    for r in csv.DictReader(open(f"{REPO}/results/archi_514913_marks_georef_5.47.csv")):
        rows.append({"image": "514913.png", "project_id": "21359", "val": r["val"],
                     "rel_x": r["x"], "rel_y": r["y"], "lat": r["lat"], "lon": r["lon"],
                     "ctx": r["ctx"], "kind": "ground", "src": r["src"], "georef": "affine_peer"})
    # 2. VLM-extraction geoplans
    summ = json.load(open(f"{REPO}/results/archi_vlm_extract_5.47.json"))
    for img, meta in summ.items():
        pid, pname, lat, lon = PROJ.get(img, ("", "", None, None))
        cache = os.path.join(EXT, img.rsplit(".", 1)[0] + ".json")
        if not os.path.exists(cache):
            continue
        p = json.load(open(cache))
        for m in p.get("marks", []):
            try:
                v = float(m["value"])
            except (TypeError, ValueError):
                continue
            g = 1 if m.get("kind") == "ground" else 0
            rows.append({"image": img, "project_id": pid, "val": v,
                         "rel_x": m.get("x", ""), "rel_y": m.get("y", ""),
                         "lat": "", "lon": "", "ctx": (m.get("context") or "")[:80],
                         "kind": m.get("kind", "?"), "src": "vlm_5.47",
                         "georef": "site_frame"})
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["image", "project_id", "val", "rel_x",
                                          "rel_y", "lat", "lon", "ctx", "kind",
                                          "src", "georef"])
        w.writeheader()
        w.writerows(rows)
    n_ground = sum(1 for r in rows if r["kind"] == "ground")
    print(f"всего строк: {len(rows)}, kind=ground: {n_ground}")
    from collections import Counter
    print(Counter(r["image"] for r in rows if r["kind"] == "ground").most_common())


if __name__ == "__main__":
    main()
