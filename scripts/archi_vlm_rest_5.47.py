#!/usr/bin/env python3
"""A2 (5.47): VLM-проход необработанных изображений archi_ru с чекпойнтом.

Порядок: сначала планоподобные (по локальному OCR-фильтру), потом фото.
Кэш: <img>.vlm.json рядом с картинкой — при повторном запуске пропускается.
Rate limit: скрипт останавливается после N ошибок подряд / по --max.

Выход: results/archi_vlm_5.47.json (сводка по всем vlm.json)
"""
import glob
import json
import os
import re
import subprocess
import sys
import time

REPO = "/home/z/my-project/spb45"
IMGDIR = os.path.join(REPO, "scripts/data/archi_plans")
OCR_JSON = os.path.join(REPO, "results/archi_ocr_5.47.json")
OUT = os.path.join(REPO, "results/archi_vlm_5.47.json")

PROMPT = (
    "Это изображение с archi.ru (Санкт-Петербург). Если это инженерный план/"
    "геоподоснова/схема вертикальной планировки с чёрными отметками рельефа — "
    "извлеки ВСЕ точечные отметки уровня земли (БС-77, числа вида 2.74, 3.05, "
    "13.83, обычно с плюсом). Для каждой: value, x∈[0,1], y∈[0,1] (пиксельные "
    "доли), context (улица/подпись рядом). НЕ включай конструктив (парапет, "
    "окно, потолок), трубы, колодцы, 0.000, размеры и отметки >80 м. "
    'Верни JSON: {"category":"engineering_plan","marks":[{"value":...,'
    '"x":...,"y":...,"context":"..."}]}. '
    'Если чертёж без отметок рельефа: {"category":"architectural_plan","marks":[]}. '
    'Если фото/рендер: {"category":"photo","marks":[]}'
)


def parse_vlm(content):
    c = content.strip()
    if c.startswith("```"):
        lines = c.split("\n")
        c = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])
    try:
        return json.loads(c)
    except Exception:
        m = re.search(r"\{.*\}", c, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
    return None


def main():
    max_n = int(sys.argv[1]) if len(sys.argv) > 1 else 1000
    ocr = {r["img"]: r for r in json.load(open(OCR_JSON))}
    pngs = sorted(glob.glob(os.path.join(IMGDIR, "*.png")))
    todo, done_names = [], set()
    for p in pngs:
        name = os.path.basename(p)
        cache = p.replace(".png", ".vlm.json")
        if os.path.exists(cache):
            done_names.add(name)
            continue
        r = ocr.get(name, {})
        todo.append((0 if r.get("planish") else 1, r.get("n_marks", 0) * -1, name, p))
    todo.sort()
    print(f"todo: {len(todo)} (done: {len(done_names)})")
    errs, processed = 0, 0
    for rank, negm, name, path in todo:
        if processed >= max_n:
            print(f"--max {max_n} reached, stop")
            break
        cache = path.replace(".png", ".vlm.json")
        r = subprocess.run(["z-ai", "vision", "-p", PROMPT, "-i", path, "-o", cache],
                           capture_output=True, text=True, timeout=180)
        if r.returncode != 0 or not os.path.exists(cache):
            errs += 1
            print(f"  ERR {name}: {r.stderr[:80]}", flush=True)
            if errs >= 5:
                print("5 ошибок подряд — похоже rate limit, стоп")
                break
            time.sleep(20)
            continue
        errs = 0
        processed += 1
        try:
            d = json.load(open(cache))
            p = parse_vlm(d["choices"][0]["message"]["content"])
            cat = (p or {}).get("category", "parse_err")
            marks = (p or {}).get("marks", [])
        except Exception:
            cat, marks = "read_err", []
        flag = f" <== {len(marks)} MARKS" if marks else ""
        print(f"[{processed}] {name:30s} {cat}{flag}", flush=True)
        time.sleep(3)

    # сводка
    summ = []
    for f in glob.glob(os.path.join(IMGDIR, "*.vlm.json")):
        name = os.path.basename(f).replace(".vlm.json", "")
        try:
            d = json.load(open(f))
            p = parse_vlm(d["choices"][0]["message"]["content"])
            summ.append({"img": name, "category": (p or {}).get("category", "?"),
                         "n_marks": len((p or {}).get("marks", [])),
                         "marks": (p or {}).get("marks", [])})
        except Exception:
            summ.append({"img": name, "category": "read_err", "n_marks": 0, "marks": []})
    json.dump(summ, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    from collections import Counter
    print("сводка:", Counter(s["category"] for s in summ),
          "| с отметками:", [s["img"] for s in summ if s["n_marks"]])


if __name__ == "__main__":
    main()
