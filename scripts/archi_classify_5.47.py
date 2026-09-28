#!/usr/bin/env python3
"""5.47: классификатор «настоящий геоплан vs поэтажный план vs карта».

Признаки ПОЭТАЖНОГО плана (площади квартир — главный источник ЛОЖНЫХ
отметок): цветные заливки (сатурация), слова «Студия/комнат/Спальня/
Гостиная/Прихожая/Кухня/Экспликация/помещени/ЭТАЖ/Лестнич», повторы
одинаковых значений.
Признаки СТАРОЙ КАРТЫ: сепия, плотная городская сетка, «ул/пер/просп».
Признаки ГЕОПЛАНА: ч/б, мало повторов значений, слова «геоподоснова/
вертикальная планировка/рельеф/отметк/уклон/горизонталь/план земляных».

Выход: results/archi_scan_classified_5.47.json
"""
import json
import os
import re
import subprocess

from PIL import Image

REPO = "/home/z/my-project/spb45"
IMGDIR = "/home/z/my-project/wr/archi3/full"
CKPT = os.path.join(REPO, "results/archi_scan_5.47.ckpt.json")
OUT = os.path.join(REPO, "results/archi_scan_classified_5.47.json")

FLOOR_KW = re.compile(
    r"студи|комнат|спальн|гостин[ао]|прихож|кухн|экспликаци|помещени|этаж|"
    r"лестнич|корпус|мол|санузл|жил[аоы]|типолог", re.I)
GEO_KW = re.compile(
    r"геоподоснов|вертикальн|рельеф|отметк|уклон|горизонтал|план земляных|"
    r"генплан|красн[аы][яе] лин|бурени|сваи|геолог", re.I)
MAP_KW = re.compile(r"\bул\.|\bпер\.|просп\.|наб\.", re.I)


def colorfulness(im):
    small = im.convert("RGB").resize((400, 400))
    hsv = small.convert("HSV")
    data = list(hsv.getdata())
    n = len(data)
    sat_sum = 0
    sat_cnt = 0
    for h, s, v in data:
        if v > 60 and v < 250:  # не белое и не чёрное
            sat_sum += s
            if s > 60:
                sat_cnt += 1
    return {"sat_mean": round(sat_sum / n, 1),
            "sat_frac": round(sat_cnt / n, 3)}


def ocr_text(path):
    try:
        r = subprocess.run(["tesseract", path, "stdout", "-l", "rus+eng",
                            "--psm", "3"], capture_output=True, text=True,
                           timeout=90)
        return r.stdout
    except Exception:
        return ""


def main():
    rows = json.load(open(CKPT))
    cand = [r for r in rows if r.get("n_marks", 0) >= 5 and not r.get("error")]
    print(f"кандидатов (>=5 отметок): {len(cand)}")
    out = []
    for k, r in enumerate(cand, 1):
        path = os.path.join(IMGDIR, r["img"])
        try:
            im = Image.open(path)
            im.load()
        except Exception:
            continue
        col = colorfulness(im)
        txt = ocr_text(path)
        floor_hit = bool(FLOOR_KW.search(txt))
        geo_hit = bool(GEO_KW.search(txt))
        map_hit = bool(MAP_KW.search(txt)) and col["sat_mean"] > 25
        # повторы значений
        vals = [m["val"] for m in r["marks"]]
        uniq = len(set(vals))
        rep = 1 - uniq / max(len(vals), 1)
        verdict = "geoplan?"
        if floor_hit or col["sat_frac"] > 0.25:
            verdict = "floor_plan"
        elif map_hit:
            verdict = "old_map"
        elif geo_hit:
            verdict = "geoplan_STRONG"
        elif rep > 0.5:
            verdict = "repeat_noise"
        out.append({"img": r["img"], "n_marks": r["n_marks"],
                    "uniq_vals": uniq, "repeat": round(rep, 2), **col,
                    "floor_hit": floor_hit, "geo_hit": geo_hit,
                    "map_hit": map_hit, "verdict": verdict,
                    "vals": r["vals"][:15]})
        print(f"[{k}/{len(cand)}] {r['img']:14s} n={r['n_marks']:3d} "
              f"sat={col['sat_frac']:.2f} floor={int(floor_hit)} geo={int(geo_hit)} "
              f"rep={rep:.2f} -> {verdict}", flush=True)
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    from collections import Counter
    print("\nвердикты:", Counter(o["verdict"] for o in out))
    strong = [o for o in out if o["verdict"] in ("geoplan_STRONG", "geoplan?")]
    strong.sort(key=lambda o: -o["n_marks"])
    print("топ геопланов:")
    for o in strong[:25]:
        print(f"  {o['img']:14s} n={o['n_marks']:3d} uniq={o['uniq_vals']:3d} "
              f"vals={o['vals'][:8]}")


if __name__ == "__main__":
    main()
