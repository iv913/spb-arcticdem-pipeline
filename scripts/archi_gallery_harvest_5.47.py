#!/usr/bin/env python3
"""5.47 A3-этап1: сбор галерей оставшихся проектов каталога archi.ru.

Для каждого проекта (agent-browser проходит JS-challenge):
- title, адрес, координаты (из блока «информация»)
- ВСЕ полноразмерные URL изображений (из 80_80-превью восстанавливаем полный)
Чекпойнт: results/archi_catalog_gallery_5.47.json (дозаполнение).

Запуск: python3 scripts/archi_gallery_harvest_5.47.py [start end]
"""
import csv
import json
import os
import re
import subprocess
import sys
import time

REPO = "/home/z/my-project/spb45"
OUT = os.path.join(REPO, "results/archi_catalog_gallery_5.47.json")
CAT_JSON = os.path.join(REPO, "results/archi_ru_spb_catalog_5.39.json")
CAT_CSV = os.path.join(REPO, "archi/spb_catalog.csv")
# уже пройденные VLM-пройдами проекты (кэш scripts/data/archi_plans)
DONE_PROJECTS = {
    "21070", "21120", "15000", "11000", "7000", "11001", "11002", "11003",
    "11004", "11005", "11006", "11007", "11008", "11020", "11045", "11090",
    "6990", "20590", "20710", "21370", "21359", "21383", "16034", "17153",
    "17893", "18132", "18569", "18913", "19715", "19943", "20516", "6887",
    "21050", "21060",
}


def ab(*args, timeout=45):
    r = subprocess.run(["agent-browser", *args], capture_output=True,
                       text=True, timeout=timeout)
    return r.stdout.strip(), r.returncode


def open_page(pid):
    url = f"https://archi.ru/projects/russia/{pid}/"
    out, rc = ab("open", url, "--timeout", "30000", timeout=60)
    if rc != 0 or "launched" in out and False:
        return False
    # дать челленджу пройти
    time.sleep(3)
    title, _ = ab("eval", "document.title")
    if "Verification" in title or "роверка" in title:
        time.sleep(8)
        title, _ = ab("eval", "document.title")
    return title


def get_info():
    out, rc = ab("eval", "document.body.innerText.substring(0, 6000)", timeout=30)
    return out.strip().strip('"').replace("\\n", "\n") if rc == 0 else ""


def get_images():
    # ВСЕ url из src/data-src/data-original/href (галерея лениво грузится,
    # но полный список лежит в HTML страницы)
    out, rc = ab(
        "eval",
        '(function(){var ids=[];var els=document.querySelectorAll("img, source, a, [data-src]");'
        'for(var i=0;i<els.length;i++){["src","data-src","data-original","href"].forEach(function(a){'
        'var v=els[i].getAttribute(a); if(v&&v.indexOf("i.archi.ru")>=0){'
        'v=v.replace(/i\\.archi\\.ru\\/i\\/\\d+_\\d+\\//, "i.archi.ru/i/");'
        'if(ids.indexOf(v)<0)ids.push(v)}})}return ids})()',
        timeout=30)
    if rc != 0:
        return []
    try:
        return json.loads(out)
    except Exception:
        return []


def parse_coords(text):
    m = re.search(r"координаты\s*\n([0-9.]+),?\s+([0-9.]+)", text)
    if m:
        return float(m.group(1)), float(m.group(2))
    return None, None


def main():
    ids = []
    seen = set()
    for p in json.load(open(CAT_JSON)):
        if p["id"] not in seen:
            seen.add(p["id"])
            ids.append(p["id"])
    for r in csv.DictReader(open(CAT_CSV)):
        if r["project_id"] not in seen:
            seen.add(r["project_id"])
            ids.append(r["project_id"])

    work = [i for i in ids if i not in DONE_PROJECTS]
    i0, i1 = 0, len(work)
    if len(sys.argv) >= 3:
        i0, i1 = int(sys.argv[1]), int(sys.argv[2])
    work = work[i0:i1]

    db = json.load(open(OUT)) if os.path.exists(OUT) else {}
    print(f"всего проектов: {len(ids)}, к обработке: {len(work)} "
          f"(в базе уже {len(db)})")

    subprocess.run(["agent-browser", "close"], capture_output=True, timeout=10)
    time.sleep(2)
    opened = False

    for k, pid in enumerate(work, i0 + 1):
        if pid in db and db[pid].get("images"):
            continue
        title = open_page(pid)
        if not title:
            print(f"[{k}] {pid}: OPEN FAIL", flush=True)
            continue
        opened = True
        text = get_info()
        imgs = get_images()
        lat, lon = parse_coords(text)
        addr = ""
        m = re.search(r"место\nСанкт-Петербург\n([^\n]+)", text)
        if m:
            addr = m.group(1).strip()
        db[pid] = {"title": title.strip('"'), "address": addr,
                   "lat": lat, "lon": lon, "n_images": len(imgs),
                   "images": imgs}
        print(f"[{k}] {pid}: {title.strip(chr(34))[:40]:42s} "
              f"coords={lat},{lon} imgs={len(imgs)}", flush=True)
        json.dump(db, open(OUT, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        time.sleep(1)

    print(f"готово, в базе {len(db)} проектов -> {OUT}")


if __name__ == "__main__":
    main()
