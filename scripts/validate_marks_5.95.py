#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""5.95 — Валидация выкопировок (независимых отметок высот, БС-77) против
мозаики 5.90 и гейта M-CLF-v3.

Входы:
  scripts/marks_new_5.95.json                  — новые отметки 5.95 (nakarte/ГШ)
  scripts/data/topo_marks_clean.csv            — ГШ 1:10000 пилот (юг, 67)
  scripts/data/otmetki_independent.csv         — полевые отметки (200)
  scripts/data/topo_v2.csv                     — DXF-съёмка Дачное (2216)
  download/mosaic_5.90/spb_hybrid_2m_mosaic_5.90.tif
  download/mclf_v3/mclf_v3_mask_5.90.tif       — классы гейта {0,1,2,3}

Конвенции 5.90/5.91: round-сэмпл, ND=65000 (Int16), bbox
[29.36, 59.700018, 30.599983, 60.1183], px=1.78e-05.

Выход: results/nspd_marks_val_5.95.json, results/marks_val_5.95.csv,
results/marks_val_5.95.png. dH = z_mark - dem2m (м). Вердикт: med~0
=> независимое подтверждение датума БС-77 мозаики.
"""
import json
import math
import os
import numpy as np

import rasterio

REPO = "/tmp/my-project/gh-clone"
WORKDIR = "/tmp/my-project/topo95"
BBOX = (29.36, 59.700018, 30.599983, 60.1183)  # lon0, lat0(south), lon1, lat1(north)
PX = 1.78e-05
ZOOM = 15


def px_to_ll(cell_lat, cell_lon, px_crop):
    """Кроп-пиксель -> WGS84 (Mercator, z15)."""
    vx = px_crop[0] + 340.0
    vy = px_crop[1] + 50.0
    mpp = 156543.03392 * math.cos(math.radians(cell_lat)) / (2 ** ZOOM)
    dlat = -(vy - 450.0) * mpp / 111320.0
    dlon = (vx - 800.0) * mpp / (111320.0 * math.cos(math.radians(cell_lat)))
    return cell_lat + dlat, cell_lon + dlon


def load_marks():
    rows = []
    with open(os.path.join(REPO, "scripts", "marks_new_5.95.json")) as f:
        new = json.load(f)
    for m in new["marks"]:
        lat, lon = px_to_ll(m["cell"][0], m["cell"][1], m["px_crop"])
        rows.append({"lat": lat, "lon": lon, "z": m["value"],
                     "src": f"topo95_{m['type']}", "note": m["note"]})
    t = np.genfromtxt(os.path.join(REPO, "scripts", "data", "topo_marks_clean.csv"),
                      delimiter=",", names=True, dtype=None, encoding="utf-8",
                      usecols=(0, 1, 2))
    for r in t:
        rows.append({"lat": float(r[0]), "lon": float(r[1]), "z": float(r[2]),
                     "src": "topo_pilot", "note": "ГШ 1:10000 пилот 5.44-эра, юг"})
    o = np.genfromtxt(os.path.join(REPO, "scripts", "data", "otmetki_independent.csv"),
                      delimiter=",", names=True, dtype=None, encoding="utf-8",
                      usecols=(0, 1, 2))
    for r in o:
        rows.append({"lat": float(r[0]), "lon": float(r[1]), "z": float(r[2]),
                     "src": "otmetki_field", "note": "полевые отметки"})
    v = np.genfromtxt(os.path.join(REPO, "scripts", "data", "topo_v2.csv"),
                      delimiter=",", names=True, dtype=None, encoding="utf-8",
                      usecols=(5, 6, 4))
    for r in v:
        rows.append({"lat": float(r[0]), "lon": float(r[1]), "z": float(r[2]),
                     "src": "topo_dxf_dachnoe", "note": "DXF-съёмка Дачное"})
    return rows


def dedup(rows, tol_deg=0.001):
    seen, out, dropped = set(), [], 0
    for r in sorted(rows, key=lambda x: -len(x["src"])):
        key = (round(r["lat"] / tol_deg), round(r["lon"] / tol_deg))
        if key in seen:
            dropped += 1
            continue
        seen.add(key)
        out.append(r)
    return out, dropped


def sample(dataset, lon, lat, scale=1.0, nodata=(-32768,)):
    lon0, lat0, lon1, lat1 = BBOX
    col = int(round((lon - lon0) / PX))
    row = int(round((lat1 - lat) / PX))
    h, w = dataset.height, dataset.width
    if not (0 <= row < h and 0 <= col < w):
        return None
    v = float(dataset.read(1, window=((row, row + 1), (col, col + 1)))[0, 0])
    if v in nodata or v >= 25000:
        return None
    return v if scale == 1.0 else v / scale


def stats(dh):
    dh = np.asarray(dh, dtype=float)
    if len(dh) == 0:
        return None
    med = float(np.median(dh))
    mad = float(np.median(np.abs(dh - med)))
    return {"n": int(len(dh)), "med": round(med, 3), "mad": round(mad, 3),
            "p95": round(float(np.percentile(np.abs(dh), 95)), 3)}


DISTRICTS = {
    "Дудергоф-Красное (Красносельский юг)": (59.68, 59.78, 29.85, 30.25),
    "Кировский": (59.82, 59.88, 30.10, 30.32),
    "Красносельский": (59.78, 59.86, 30.05, 30.25),
    "Пушкинский": (59.70, 59.85, 30.30, 30.50),
    "Колпинский": (59.70, 59.79, 30.45, 30.61),
    "Петродворцовый": (59.84, 59.91, 29.60, 30.00),
    "Курортный": (60.01, 60.12, 29.55, 30.10),
    "Город (кольцо)": (59.86, 60.05, 30.05, 30.60),
}


def district_of(lat, lon):
    for name, (a, b, c, d) in DISTRICTS.items():
        if a <= lat <= b and c <= lon <= d:
            return name
    return "вне"

def main():
    rows = load_marks()
    n_raw = len(rows)
    rows, n_dup = dedup(rows)
    print(f"отметок: {n_raw} raw -> {len(rows)} (дедуп 0.001°: -{n_dup})")

    mos = rasterio.open(os.path.join(REPO, "download/mosaic_5.90/spb_hybrid_2m_mosaic_5.90.tif"))
    msk = rasterio.open(os.path.join(REPO, "download/mclf_v3/mclf_v3_mask_5.90.tif"))

    for r in rows:
        r["district"] = district_of(r["lat"], r["lon"])
        dem = sample(mos, r["lon"], r["lat"], scale=100.0)
        cls = sample(msk, r["lon"], r["lat"], nodata=(255,))
        r["dem2m"] = dem
        r["gate_cls"] = int(cls) if cls is not None else -1
        r["dH"] = None if dem is None else round(r["z"] - dem, 3)

    val = [r for r in rows if r["dH"] is not None and abs(r["dH"]) < 15]
    out = [r for r in rows if r["dH"] is None]
    big = [r for r in rows if r["dH"] is not None and abs(r["dH"]) >= 15]
    print(f"валидж: {len(val)}, вне мозаики: {len(out)}, |dH|>=15 (триаж): {len(big)}")

    res = {"stage": "5.95", "n_raw": n_raw, "n_dedup": len(rows),
           "n_valid": len(val), "n_outside": len(out), "n_triage": len(big)}

    res["by_source"] = {}
    for src in sorted(set(r["src"] for r in val)):
        res["by_source"][src] = stats([r["dH"] for r in val if r["src"] == src])
    res["by_type"] = {}
    for typ in ["spot", "urez", "contour", "topo_pilot", "otmetki_field",
                "topo_dxf_dachnoe"]:
        sel = [r["dH"] for r in val
               if r["src"] == f"topo95_{typ}" or r["src"] == typ]
        if sel:
            res["by_type"][typ] = stats(sel)
    res["by_district"] = {}
    for d in sorted(set(r["district"] for r in val)):
        res["by_district"][d] = stats([r["dH"] for r in val if r["district"] == d])
    res["by_gate"] = {}
    for g, gname in [(3, "gold"), (2, "ok"), (1, "rest"), (0, "excl")]:
        sel = [r["dH"] for r in val if r["gate_cls"] == g]
        if sel:
            res["by_gate"][gname] = stats(sel)
    res["pooled"] = stats([r["dH"] for r in val])

    # новые отметки 5.95 — детально
    res["topo95_detail"] = [
        {"lat": round(r["lat"], 5), "lon": round(r["lon"], 5), "z": r["z"],
         "type": r["src"].replace("topo95_", ""), "district": r["district"],
         "gate_cls": r["gate_cls"], "dem2m": r["dem2m"], "dH": r["dH"],
         "note": r["note"]}
        for r in rows if r["src"].startswith("topo95_")]

    os.makedirs(os.path.join(REPO, "results"), exist_ok=True)
    with open(os.path.join(REPO, "results/nspd_marks_val_5.95.json"), "w") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    import csv
    with open(os.path.join(REPO, "results/marks_val_5.95.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["lat", "lon", "z", "src", "district",
                                          "gate_cls", "dem2m", "dH", "note"])
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in w.fieldnames})

    # PNG: dH по широте + гистограмма
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams['font.family'] = 'DejaVu Sans'
    plt.rcParams['axes.unicode_minus'] = False
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 4.2), constrained_layout=True)
    colors = {"topo95_spot": "tab:red", "topo95_urez": "tab:orange",
              "topo95_contour": "tab:purple", "topo_pilot": "tab:blue",
              "otmetki_field": "tab:green", "topo_dxf_dachnoe": "gray"}
    for src in sorted(set(r["src"] for r in val)):
        xs = [r["lon"] for r in val if r["src"] == src]
        ys = [r["dH"] for r in val if r["src"] == src]
        a1.scatter(xs, ys, s=9, alpha=0.5, label=f"{src} (n={len(xs)})", c=colors.get(src, "k"))
    a1.axhline(0, color="k", lw=0.7)
    a1.set_xlabel("долгота"); a1.set_ylabel("dH = отметка − dem2m, м")
    a1.set_title("Выкопировки vs мозаика 5.90"); a1.legend(fontsize=7)
    a1.set_ylim(-6, 6)
    dh = [r["dH"] for r in val]
    a2.hist(dh, bins=60, range=(-4, 4), color="tab:blue", alpha=0.75)
    a2.axvline(0, color="k", lw=0.7)
    p = res["pooled"]
    a2.set_title(f"pooled: n={p['n']}, med={p['med']}, MAD={p['mad']}, p95={p['p95']}")
    fig.savefig(os.path.join(REPO, "results/marks_val_5.95.png"), dpi=140)

    print(json.dumps({k: res[k] for k in ["pooled", "by_source", "by_type", "by_gate"]},
                     ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
