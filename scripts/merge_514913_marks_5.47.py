#!/usr/bin/env python3
"""5.47: сводный набор марок плана 514913 (Дунайский, 27).

- объединяет VLM-пройды (старый кэш 5.44/71 марка + новый промпт 5.47/108)
- фильтр правдоподобия БС-77 для района Дунайского: 5..20 м
  (5.44: конструктив и местные системы дают 40-45 и >80; 884 - шум)
- дедуп по (value, близость rel-координат)
- кросс-чек с OCR-токенами (archi_514913 из archi_ocr_5.47.json)

Выход: results/archi_514913_marks_merged_5.47.csv
"""
import csv
import json
import re

REPO = "/home/z/my-project/spb45"
VAL_MIN, VAL_MAX = 5.0, 20.0


def load_new():
    summ = json.load(open(f"{REPO}/results/archi_vlm_5.47.json"))
    out = []
    for s in summ:
        src = {"dunaisky_514913": "vlm_5.47", "archi_514913": "vlm_5.44"}.get(s["img"])
        if not src:
            continue
        for m in s["marks"]:
            try:
                v = float(m["value"])
            except (TypeError, ValueError):
                continue
            out.append({"val": v, "x": float(m.get("x", -1)), "y": float(m.get("y", -1)),
                        "ctx": (m.get("context") or "").strip(), "src": src})
    return out


def load_ocr():
    rows = json.load(open(f"{REPO}/results/archi_ocr_5.47.json"))
    out = []
    for r in rows:
        if r["img"] not in ("archi_514913.png", "dunaisky_514913.png"):
            continue
        w, h = r["size"]
        for m in r["marks"]:
            out.append({"val": m["val"], "x": m["x"] / w, "y": m["y"] / h,
                        "ctx": "", "src": "ocr"})
    return out


def main():
    pool = load_new() + load_ocr()
    kept = [p for p in pool if VAL_MIN <= p["val"] <= VAL_MAX]
    dropped = [p for p in pool if p not in kept]
    # дедуп: val равные (±0.01) и rel-дистанция < 0.01
    kept.sort(key=lambda p: (p["val"], p["x"], p["y"]))
    dedup = []
    for p in kept:
        for d in dedup:
            if abs(d["val"] - p["val"]) <= 0.011 and \
               (d["x"] - p["x"]) ** 2 + (d["y"] - p["y"]) ** 2 < 1e-4:
                # приоритет источника: vlm_5.47 (с контекстом) > vlm_5.44 > ocr
                if p["src"] == "vlm_5.47" and d["ctx"] == "" and p["ctx"]:
                    d.update(ctx=p["ctx"], src="vlm_5.47")
                break
        else:
            dedup.append(dict(p))
    print(f"pool={len(pool)} (vlm 5.44+5.47+ocr), в диапазоне {VAL_MIN}-{VAL_MAX}: {len(kept)}, "
          f"после дедупа: {len(dedup)}")
    srcs = {}
    for d in dedup:
        srcs[d["src"]] = srcs.get(d["src"], 0) + 1
    print("по источникам:", srcs)
    vals = sorted(d["val"] for d in dedup)
    print(f"диапазон значений: {vals[0]}..{vals[-1]}, медиана {vals[len(vals)//2]}")
    with open(f"{REPO}/results/archi_514913_marks_merged_5.47.csv", "w",
              newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["val", "x", "y", "ctx", "src"])
        w.writeheader()
        w.writerows(dedup)
    n_ctx = sum(1 for d in dedup if d["ctx"])
    print(f"с контекстом (улицей): {n_ctx}; сохранено в archi_514913_marks_merged_5.47.csv")
    # уникальные значения (как в 5.46 проверка |h1-h2|)
    uv = sorted({round(v, 2) for v in vals})
    print(f"уникальных значений: {len(uv)}")


if __name__ == "__main__":
    main()
