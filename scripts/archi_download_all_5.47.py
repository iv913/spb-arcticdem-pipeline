#!/usr/bin/env python3
"""5.47 A3-этап2: скачивание полноразмерных изображений archi.ru.

Из results/archi_catalog_gallery_5.47.json собираем уникальные ID,
качаем https://i.archi.ru/i/<ID>.<ext> (PNG-известные — .png, иначе .jpg).
Чекпойнт-директория: пропускаем скачанное.

Запуск: python3 scripts/archi_download_all_5.47.py [start end]
"""
import glob
import json
import os
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

REPO = "/home/z/my-project/spb45"
GAL = os.path.join(REPO, "results/archi_catalog_gallery_5.47.json")
DEST = "/home/z/my-project/wr/archi3/full"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"


def fetch(url, path, min_size=3000):
    if os.path.exists(path) and os.path.getsize(path) > min_size:
        return True
    tmp = path + ".part"
    for attempt in range(2):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = r.read()
            if len(data) > min_size:
                with open(tmp, "wb") as f:
                    f.write(data)
                os.replace(tmp, path)
                return True
        except Exception:
            time.sleep(1)
    return False


def fetch_one(img_id):
    for ext in (".png", ".jpg"):
        url = f"https://i.archi.ru/i/{img_id}{ext}"
        path = os.path.join(DEST, f"{img_id}{ext}")
        if fetch(url, path):
            return True
    return False


def have_ids():
    have = set()
    for p in glob.glob(os.path.join(DEST, "*.*")):
        m = re.search(r"/(\d+)\.(png|jpg|jpeg)$", p)
        if m:
            have.add(m.group(1))
    # + уже скачанное в spb45/scripts/data/archi_plans (там имена archi_<pid>_<n>)
    for p in glob.glob("/home/z/my-project/spb45/scripts/data/archi_plans/*.p*"):
        b = os.path.basename(p)
        m = re.match(r"archi_(\d+)\.png", b)
        if m:
            have.add(m.group(1))
        m = re.search(r"_(\d{4,7})\.(png|jpg)", b)
        if m and b.startswith(("plan_", "dunaisky_", "scan_", "proj_")):
            have.add(m.group(1))
    return have


def main():
    os.makedirs(DEST, exist_ok=True)
    db = json.load(open(GAL))
    uniq = set()
    for pid, d in db.items():
        for u in d.get("images", []):
            m = re.search(r"/i/(?:\d+/)?(\d+)\.(?:png|jpg|jpeg)", u)
            if m:
                uniq.add(m.group(1))
    ids = sorted(uniq)
    have = have_ids()
    todo = [i for i in ids if i not in have]
    i0, i1 = 0, len(todo)
    if len(sys.argv) >= 3:
        i0, i1 = int(sys.argv[1]), int(sys.argv[2])
    chunk = todo[i0:i1]
    print(f"уникальных: {len(ids)}, уже есть: {len(have)}, качаем: {len(chunk)} "
          f"[{i0}:{i1}]")

    ok = fail = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=12) as ex:
        for j, good in enumerate(ex.map(fetch_one, chunk), i0 + 1):
            if good:
                ok += 1
            else:
                fail += 1
            if (ok + fail) % 100 == 0:
                rate = (ok + fail) / max(time.time() - t0, 1)
                print(f"[{j}] ok={ok} fail={fail} ({rate:.1f}/s)", flush=True)
    print(f"готово: ok={ok} fail={fail} за {time.time()-t0:.0f} с")


if __name__ == "__main__":
    main()
