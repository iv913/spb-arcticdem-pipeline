#!/usr/bin/env python3
"""5.47 A3-этап3: фильтр «план vs фото» + OCR для всех скачанных изображений.

Экономия времени: OCR (мультипроход) только для планоподобных (white>0.45).
Чекпойнт: results/archi_scan_5.47.ckpt.json (дозаполнение по имени файла).

Запуск: python3 scripts/archi_scan_all_5.47.py [start end]
"""
import csv
import glob
import json
import os
import re
import signal
import subprocess
import sys

from PIL import Image, ImageFilter

REPO = "/home/z/my-project/spb45"
IMGDIR = "/home/z/my-project/wr/archi3/full"
CKPT = os.path.join(REPO, "results/archi_scan_5.47.ckpt.json")
OUT_JSON = os.path.join(REPO, "results/archi_scan_5.47.json")
OUT_CSV = os.path.join(REPO, "results/archi_scan_marks_5.47.csv")
MARK_RE = re.compile(r"^[+]?\d{1,2}[.,]\d{1,2}$")


def plan_score(im):
    g = im.convert("L")
    small = g.resize((min(g.width, 1200), min(g.height, 1200)))
    px = list(small.getdata())
    n = len(px)
    white = sum(1 for v in px if v > 235) / n
    edges = small.filter(ImageFilter.FIND_EDGES)
    ed = sum(1 for v in edges.getdata() if v > 60) / n
    return {"white_frac": round(white, 3), "edge_frac": round(ed, 3),
            "planish": white > 0.45}


def ocr_tsv(path, psm):
    try:
        out = subprocess.run(
            ["tesseract", path, "stdout", "-l", "rus+eng", "--psm", str(psm), "tsv"],
            capture_output=True, text=True, timeout=150).stdout
    except Exception:
        return []
    marks = []
    for line in out.splitlines()[1:]:
        p = line.split("\t")
        if len(p) < 12:
            continue
        tok = p[11].strip().replace(" ", "")
        if MARK_RE.match(tok):
            try:
                val = float(tok.replace(",", ".").replace("+", ""))
            except ValueError:
                continue
            if 0.5 <= val <= 80.0:
                marks.append({"val": val, "x": int(p[6]), "y": int(p[7]),
                              "w": int(p[8]), "h": int(p[9]), "conf": p[10]})
    return marks


def ocr_multipass(path):
    allm = []
    for psm in (11, 6):
        for m in ocr_tsv(path, psm):
            m["pass"] = f"psm{psm}"
            allm.append(m)
    try:
        im = Image.open(path)
        if max(im.size) <= 4000:
            big = im.convert("L").resize((im.width * 2, im.height * 2), Image.LANCZOS)
            tmp = path + ".up2x.png"
            big.save(tmp)
            for m in ocr_tsv(tmp, 11):
                m["pass"] = "up2x"
                m["x"] //= 2
                m["y"] //= 2
                allm.append(m)
            os.remove(tmp)
    except Exception:
        pass
    dedup = []
    for m in sorted(allm, key=lambda m: -float(m.get("conf") or 0)):
        for d in dedup:
            if abs(d["val"] - m["val"]) < 0.005 and \
               (d["x"] - m["x"]) ** 2 + (d["y"] - m["y"]) ** 2 < 144:
                break
        else:
            dedup.append(m)
    return dedup


def main():
    killed = {"flag": False}

    def on_term(sig, frm):
        killed["flag"] = True
    signal.signal(signal.SIGTERM, on_term)
    paths = sorted(glob.glob(os.path.join(IMGDIR, "*.*")))
    i0, i1 = 0, len(paths)
    if len(sys.argv) >= 3:
        i0, i1 = int(sys.argv[1]), int(sys.argv[2])
    chunk = paths[i0:i1]
    rows = json.load(open(CKPT)) if os.path.exists(CKPT) else []
    done = {r["img"] for r in rows}
    print(f"файлов: {len(paths)}, в чекпойнте {len(done)}, чанк {len(chunk)}")

    try:
        for k, path in enumerate(chunk, i0 + 1):
            name = os.path.basename(path)
            if name in done:
                continue
            try:
                im = Image.open(path)
                im.load()
            except Exception as e:
                rows.append({"img": name, "error": str(e)})
                continue
            ps = plan_score(im)
            marks = ocr_multipass(path) if ps["planish"] else []
            vals = sorted({m["val"] for m in marks})
            rows.append({"img": name, "size": list(im.size), **ps,
                         "n_marks": len(marks), "vals": vals, "marks": marks})
            if marks:
                print(f"[{k}/{len(paths)}] {name} MARKS={len(marks)} vals={vals[:10]}",
                      flush=True)
            if k % 100 == 0:
                json.dump(rows, open(CKPT, "w", encoding="utf-8"),
                          ensure_ascii=False, indent=1)
                print(f"[{k}/{len(paths)}] ckpt {len(rows)}", flush=True)
            if killed["flag"]:
                print("SIGTERM — сохраняем и выходим", flush=True)
                break
    finally:
        json.dump(rows, open(CKPT, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print(f"ckpt saved: {len(rows)}", flush=True)

    if i1 >= len(paths):
        rows.sort(key=lambda r: -r.get("n_marks", 0))
        json.dump(rows, open(OUT_JSON, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["image", "val", "x", "y", "conf", "pass"])
            w.writeheader()
            for r in rows:
                for m in r.get("marks", []):
                    w.writerow({"image": r["img"], "val": m["val"], "x": m["x"],
                                "y": m["y"], "conf": m.get("conf", ""),
                                "pass": m["pass"]})
        planish_n = sum(1 for r in rows if r.get("planish"))
        mark_imgs = [r for r in rows if r.get("n_marks")]
        print(f"== ИТОГ: файлов {len(rows)}, планоподобных {planish_n}, "
              f"с отметками {len(mark_imgs)}")
        for r in mark_imgs:
            print(f"  {r['img']}: n={r['n_marks']} vals={r['vals'][:12]}")


if __name__ == "__main__":
    main()
