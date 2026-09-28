#!/usr/bin/env python3
"""5.47: VLM-извлечение отметок с геопланов (по одному за вызов).

Для каждого geoplan: ВСЕ чёрные отметки рельефа (value, x, y∈[0,1],
context), ТИП каждой (ground/structural), видимые улицы (для гео-
привязки), масштаб. Кэш: wr/archi3/extract/<img>.json
"""
import json
import os
import re
import subprocess
import sys
import time

REPO = "/home/z/my-project/spb45"
IMGDIR = "/home/z/my-project/wr/archi3/full"
CLSDIR = os.path.join(REPO, "results")
CLS = os.path.join(CLSDIR, "archi_vlm_classify_5.47.json")
OUTDIR = "/home/z/my-project/wr/archi3/extract"
SUM = os.path.join(REPO, "results/archi_vlm_extract_5.47.json")

PROMPT = (
    "Это изображение-план с archi.ru (Санкт-Петербург). Извлеки из него:\n"
    "1. marks: ВСЕ чёрные отметки (числа вида 2.74, 3.05, 13.83, обычно "
    "короткие, 1-2 знака после точки, часто рядом с точкой/крестиком). "
    "Для каждой: value (число), x и y ∈ [0,1] (доли от ширины/высоты "
    "картинки), context (что рядом: улица, угол, бровка, ось дороги), "
    "kind: ground (отметка рельефа/земли/планировки) или structural "
    "(конструктив, парапет, кровля, потолок, глубина котлована).\n"
    "2. streets: все различимые названия улиц/проспектов/набережных.\n"
    "3. scale: численный масштаб если указан (например 1:500).\n"
    "НЕ включай: площади помещений, размеры в мм (длинные числа), номера "
    "домов/участков, отметки >80 м.\n"
    'Ответ строго JSON: {"marks":[{"value":3.05,"x":0.42,"y":0.6,'
    '"context":"...","kind":"ground"}],"streets":["..."],"scale":"..."}'
)


def parse_json_loose(s):
    m = re.search(r"\{.*\}", s.strip(), re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        try:
            return json.loads(m.group(0).replace("'", '"'))
        except Exception:
            return None


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    cls = json.load(open(CLS))
    geos = [k for k, v in cls.items() if v.get("type") == "geoplan"]
    if len(sys.argv) > 1:
        geos = geos[:int(sys.argv[1])]
    print(f"геопланов к извлечению: {len(geos)}")
    summ = {}
    if os.path.exists(SUM):
        summ = json.load(open(SUM))
    errs = 0
    for i, img in enumerate(geos, 1):
        cache = os.path.join(OUTDIR, img.rsplit(".", 1)[0] + ".json")
        if os.path.exists(cache):
            try:
                p = json.load(open(cache))
                print(f"[{i}] {img}: CACHED n={len(p.get('marks', []))}", flush=True)
                continue
            except Exception:
                pass
        r = subprocess.run(["z-ai", "vision", "-p", PROMPT,
                            "-i", os.path.join(IMGDIR, img), "-o", cache],
                           capture_output=True, text=True, timeout=240)
        if r.returncode != 0 or not os.path.exists(cache):
            errs += 1
            print(f"[{i}] {img}: ERR {r.stderr[:80]}", flush=True)
            if errs >= 4:
                print("rate limit — стоп")
                break
            time.sleep(30)
            continue
        errs = 0
        try:
            d = json.load(open(cache))
            p = parse_json_loose(d["choices"][0]["message"]["content"])
            if p is None:
                raise ValueError("json")
            json.dump(p, open(cache, "w", encoding="utf-8"),
                      ensure_ascii=False, indent=1)
            marks = p.get("marks", [])
            g = sum(1 for m in marks if m.get("kind") == "ground")
            print(f"[{i}] {img}: n={len(marks)} ground={g} "
                  f"streets={p.get('streets', [])[:4]} scale={p.get('scale')}",
                  flush=True)
            summ[img] = {"n_marks": len(marks), "n_ground": g,
                         "streets": p.get("streets", []),
                         "scale": p.get("scale", "")}
        except Exception as e:
            print(f"[{i}] {img}: parse err {e}", flush=True)
            os.remove(cache)
        json.dump(summ, open(SUM, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        time.sleep(3)
    print(f"итог в {SUM}")


if __name__ == "__main__":
    main()
