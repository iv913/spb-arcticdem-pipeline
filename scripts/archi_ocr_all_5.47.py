#!/usr/bin/env python3
"""A1 (5.47): локальный OCR-проход ВСЕХ кэшированных изображений archi.ru.

- фильтр «план vs фото» (white_frac + edges)
- tesseract rus+eng, PSM 11 и 6, +2x-апскейл кросс-чек (как в 5.34)
- отметки [+]N[,.]NN в диапазоне 0.5..80 м БС-77, с пиксельными координатами
- без rate limit'ов (всё локально)

Выход: results/archi_ocr_5.47.json + results/archi_ocr_marks_5.47.csv
"""
import csv
import glob
import json
import os
import re
import subprocess
import sys

from PIL import Image, ImageFilter

REPO = "/home/z/my-project/spb45"
IMGDIR = os.path.join(REPO, "scripts/data/archi_plans")
OUT_JSON = os.path.join(REPO, "results/archi_ocr_5.47.json")
OUT_CSV = os.path.join(REPO, "results/archi_ocr_marks_5.47.csv")
CKPT = os.path.join(REPO, "results/archi_ocr_5.47.ckpt.json")
MARK_RE = re.compile(r"^[+]?\d{1,2}[.,]\d{1,2}$")
UA = "tesseract-local"


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
    """PSM 11 + PSM 6 + 2x upscale PSM 11; кросс-чек: токен считается
    подтверждённым если встречен в >=1 проходе (дедуп по (val, округл. коорд.))."""
    all_marks = []
    for psm in (11, 6):
        for m in ocr_tsv(path, psm):
            m["pass"] = f"psm{psm}"
            all_marks.append(m)
    # 2x upscale pass
    try:
        im = Image.open(path)
        big = im.convert("L").resize((im.width * 2, im.height * 2), Image.LANCZOS)
        tmp = path + ".up2x.png"
        big.save(tmp)
        for m in ocr_tsv(tmp, 11):
            m["pass"] = "up2x"
            m["x"] //= 2
            m["y"] //= 2
            all_marks.append(m)
        os.remove(tmp)
    except Exception:
        pass
    # dedup: val equal, pixel distance < 12
    dedup = []
    for m in sorted(all_marks, key=lambda m: -float(m.get("conf") or 0)):
        for d in dedup:
            if abs(d["val"] - m["val"]) < 0.005 and \
               (d["x"] - m["x"]) ** 2 + (d["y"] - m["y"]) ** 2 < 144:
                break
        else:
            dedup.append(m)
    return dedup


def main():
    paths = sorted(glob.glob(os.path.join(IMGDIR, "*.png"))) + \
            sorted(glob.glob(os.path.join(IMGDIR, "*.jpg")))
    # chunking: argv[1], argv[2] = start end (для укладки в таймаут shell)
    i0, i1 = 0, len(paths)
    if len(sys.argv) >= 3:
        i0, i1 = int(sys.argv[1]), int(sys.argv[2])
    chunk = paths[i0:i1]
    rows = json.load(open(CKPT)) if os.path.exists(CKPT) else []
    done = {r["img"] for r in rows}
    csv_rows = []
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
        marks = ocr_multipass(path)
        vals = sorted({m["val"] for m in marks})
        rows.append({"img": name, "size": list(im.size), **ps,
                     "n_marks": len(marks), "vals": vals, "marks": marks})
        for m in marks:
            csv_rows.append({"image": name, "val": m["val"], "x": m["x"],
                             "y": m["y"], "conf": m.get("conf", ""),
                             "pass": m["pass"]})
        flag = " <== MARKS" if marks else ""
        print(f"[{k}/{len(paths)}] {name:32s} {im.size[0]}x{im.size[1]} "
              f"w={ps['white_frac']:.2f} planish={int(ps['planish'])} "
              f"marks={len(marks)}{flag}", flush=True)

    json.dump(rows, open(CKPT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    if i1 >= len(paths):  # финальный чанк — пишем итоговые артефакты
        rows.sort(key=lambda r: -r.get("n_marks", 0))
        with open(OUT_JSON, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=1)
        for r in rows:
            for m in r.get("marks", []):
                csv_rows.append({"image": r["img"], "val": m["val"], "x": m["x"],
                                 "y": m["y"], "conf": m.get("conf", ""),
                                 "pass": m["pass"]})
        with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["image", "val", "x", "y", "conf", "pass"])
            w.writeheader()
            w.writerows(csv_rows)
        planish_n = sum(1 for r in rows if r.get("planish"))
        mark_imgs = [r for r in rows if r.get("n_marks")]
        print(f"\n== ИТОГ: изображений {len(rows)}, планоподобных {planish_n}, "
              f"с OCR-отметками {len(mark_imgs)}, всего токенов {len(csv_rows)}")
        for r in mark_imgs:
            print(f"  {r['img']}: n={r['n_marks']} vals={r['vals'][:12]}")
    else:
        print(f"ckpt updated: {len(rows)} done (chunk {i0}:{i1})")


if __name__ == "__main__":
    main()
