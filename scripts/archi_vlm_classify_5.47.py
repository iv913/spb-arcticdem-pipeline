#!/usr/bin/env python3
"""5.47: VLM-классификация кандидатов пачками по 5 изображений.

130 кандидатов (>=5 OCR-отметок) -> тип: floor_plan / old_map / geoplan /
render / other. Чекпойнт: results/archi_vlm_classify_5.47.json.
"""
import json
import os
import re
import subprocess
import sys
import time

REPO = "/home/z/my-project/spb45"
IMGDIR = "/home/z/my-project/wr/archi3/full"
CLS_IN = os.path.join(REPO, "results/archi_scan_classified_5.47.json")
OUT = os.path.join(REPO, "results/archi_vlm_classify_5.47.json")
BATCH = 5

PROMPT = (
    "Перед тобой {N} изображений с archi.ru (нумеруй 1..{N} в порядке подачи). "
    "Для КАЖДОГО определи тип строго одной фразой:\n"
    "- floor_plan: поэтажный план квартир/комнат/номеров (площади помещений, "
    "Спальня/Гостиная/Кухня/Студия, экспликация, оси А-Б-В и 1-2-3)\n"
    "- old_map: фрагмент старой карты города с номерами домов\n"
    "- geoplan: геоподоснова, схема вертикальной планировки, генплан, "
    "план земляных масс с ЧЁРНЫМИ отметками рельефа (числа с плюсом/минусом)\n"
    "- render: визуализация/фото/аксонометрия\n"
    "- other\n"
    'Ответ строго JSON без markdown: {{"results":[{{"n":1,"type":"...","note":"кратко"}}...]}}'
)


def parse_json_loose(s):
    s = s.strip()
    m = re.search(r"\{.*\}", s, re.S)
    if not m:
        return None
    t = m.group(0)
    for cand in (t, t.replace("'", '"')):
        try:
            return json.loads(cand)
        except Exception:
            pass
    return None


def main():
    cands = json.load(open(CLS_IN))
    todo = [c for c in cands if c["n_marks"] >= 5]
    db = json.load(open(OUT)) if os.path.exists(OUT) else {}
    batches = [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)]
    print(f"кандидатов: {len(todo)}, пачек: {len(batches)}")
    errs = 0
    for bi, batch in enumerate(batches):
        keys = [b["img"] for b in batch]
        if all(k in db for k in keys):
            continue
        args = ["z-ai", "vision", "-p", PROMPT.format(N=len(batch))]
        for k in keys:
            args += ["-i", os.path.join(IMGDIR, k)]
        r = subprocess.run(args, capture_output=True, text=True, timeout=240)
        if r.returncode != 0:
            errs += 1
            print(f"[batch {bi}] ERR: {r.stderr[:100]}", flush=True)
            if errs >= 4:
                print("rate limit — стоп")
                break
            time.sleep(30)
            continue
        errs = 0
        try:
            js = r.stdout[r.stdout.index("{"):]
            d = json.loads(js)
            content = d["choices"][0]["message"]["content"]
            p = parse_json_loose(content)
        except Exception as e:
            print(f"[batch {bi}] parse err {e}", flush=True)
            continue
        if not p or "results" not in p:
            print(f"[batch {bi}] bad json: {str(p)[:100]}", flush=True)
            continue
        for item in p["results"]:
            n = item.get("n")
            if isinstance(n, int) and 1 <= n <= len(keys):
                db[keys[n - 1]] = {"type": item.get("type", "?"),
                                   "note": item.get("note", "")[:120]}
        print(f"[batch {bi+1}/{len(batches)}] " +
              " ".join(f"{k[:10]}:{db.get(k,{}).get('type','?')}" for k in keys),
              flush=True)
        json.dump(db, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        time.sleep(2)
    json.dump(db, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    from collections import Counter
    print("итог:", Counter(v["type"] for v in db.values()))
    for k, v in sorted(db.items()):
        if v["type"] == "geoplan":
            print("  GEOfPLAN:", k, v["note"][:60])


if __name__ == "__main__":
    main()
