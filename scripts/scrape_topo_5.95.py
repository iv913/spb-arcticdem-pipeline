#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""5.95 — Скрейпер выкопировок: nakarte.me (Soviet topo maps, AtloMaps, слой T).

Для каждой ячейки сетки: навигация на вид (z=15), ожидание тайлов,
скриншот 1600x900, кроп карты без UI-панелей -> workdir/topo95_<lat>_<lon>.png.

Кроп: x[340..1345], y[50..885] ( viewport 1600x900; центр viewport = центр
вида из URL). Распознавание отметок выполняется отдельно (VLM/оператор),
px кропа -> WGS84: см. transcribe script.

Запуск: python3 scripts/scrape_topo_5.95.py --batch A|B|C --workdir <dir>
"""
import argparse
import json
import os
import subprocess
import sys

VIEW_W, VIEW_H = 1600, 900
ZOOM = 15
CROP = (340, 50, 1345, 885)  # x0,y0,x1,y1 (UI-свободная зона)

# Сетка 5.95: периферийные зоны + дозатарка (город уже покрыт otmetki 200)
CELLS = {
    "A_west": [  # Стрельна/Петергоф/Ломоносов/юго-запад
        (59.80, 29.95), (59.83, 29.80), (59.85, 29.65),
        (59.86, 29.80), (59.88, 29.90),
    ],
    "B_kurort_kolpino": [  # Курортный + Колпино/Пушкин восток
        (60.02, 29.75), (60.05, 29.65), (60.08, 29.80),
        (60.10, 29.95), (60.06, 30.05),
        (59.75, 30.50), (59.72, 30.45), (59.78, 30.55),
    ],
    "C_south_city": [  # юг (дозатарка) + город
        (59.72, 30.20), (59.73, 30.35), (59.71, 30.10),
        (59.95, 30.25), (59.93, 30.35), (59.88, 30.30), (59.85, 30.45),
    ],
}


def run(cmd, timeout=60):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)}: {r.stderr.strip()[:200]}")
    return r.stdout.strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", required=True, choices=list(CELLS.keys()))
    ap.add_argument("--workdir", default="/tmp/my-project/topo95")
    a = ap.parse_args()
    os.makedirs(a.workdir, exist_ok=True)
    run(["agent-browser", "set", "viewport", str(VIEW_W), str(VIEW_H)])
    manifest = []
    from PIL import Image
    for lat, lon in CELLS[a.batch]:
        url = f"https://nakarte.me/#m={ZOOM}/{lat:.4f}/{lon:.4f}&l=T"
        try:
            run(["agent-browser", "open", url], timeout=45)
            run(["agent-browser", "wait", "5500"], timeout=30)
            png = os.path.join(a.workdir, f"raw_{lat:.4f}_{lon:.4f}.png")
            run(["agent-browser", "screenshot", png], timeout=45)
            img = Image.open(png)
            if img.size != (VIEW_W, VIEW_H):
                img = img.resize((VIEW_W, VIEW_H))
            crop = img.crop(CROP)
            out = os.path.join(a.workdir, f"topo95_{lat:.4f}_{lon:.4f}.png")
            crop.save(out)
            manifest.append({"lat": lat, "lon": lon, "zoom": ZOOM,
                             "png": os.path.basename(out),
                             "crop_origin": [CROP[0], CROP[1]],
                             "viewport": [VIEW_W, VIEW_H]})
            print(f"  {lat} {lon} -> {os.path.basename(out)}", flush=True)
        except Exception as e:
            print(f"  {lat} {lon} FAIL: {e}", flush=True)
    mpath = os.path.join(a.workdir, f"manifest_{a.batch}.json")
    with open(mpath, "w") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    print(f"batch {a.batch}: {len(manifest)} ячеек -> {mpath}")


if __name__ == "__main__":
    sys.exit(main())
