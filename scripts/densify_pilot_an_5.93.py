#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""5.93a-анализ: прирост ячеек 0.001° от medium-точек + разбивка prior/strip.

Сравнение: канон v3 (icesat2_canon_v3_5.92.csv.gz, base-точки с ячейками)
vs новые medium/low окна пилота (base-маска |dH|<=3 & can30<100).
"""
import json
import glob

import numpy as np
import pandas as pd

R = "/tmp/my-project/gh-clone"
TILES = {
    "city":   (30.20, 59.8905, 30.32, 60.0045),
    "forest": (30.44, 60.0045, 30.56, 60.1183),
    "south":  (30.36, 59.70, 30.49, 59.80),
}

can = pd.read_csv(f"{R}/scripts/data/icesat2_canon_v3_5.92.csv.gz",
                  usecols=["lat", "lon"])
can["cell"] = can.lat.round(3).astype(str) + "_" + can.lon.round(3).astype(str)
can_cells = set(can.cell)
print(f"канон v3 base: {len(can)}, уникальных ячеек {len(can_cells)}",
      flush=True)

out = {}
for tname, (x0, y0, x1, y1) in TILES.items():
    zb = ((can.lat >= y0) & (can.lat < y1) & (can.lon >= x0) & (can.lon < x1))
    zcells = set(can.cell[zb])
    row = dict(v3_base=int(zb.sum()), v3_cells=len(zcells))
    for v in ("medium", "low"):
        fs = glob.glob(f"{R}/scripts/dem_cache/densify_pilot_5.93/"
                       f"{tname}_{v}.csv.gz")
        if not fs:
            continue
        d = pd.read_csv(fs[0])
        fin = d.dH.notna()
        bmask = fin & (d.dH.abs() <= 3.0) & (d.can30 < 100)
        d = d[bmask].copy()
        d["cell"] = d.lat.round(3).astype(str) + "_" + \
            d.lon.round(3).astype(str)
        prior = d.n_cov.isna()
        strip = (~prior) & d.n_cov.notna()
        gold_s = strip & (d.n_cov >= 2) & (d.sigma <= 50) & (d.can30 < 200)
        gold_p = prior & (d.can30 < 50)
        row[v] = dict(
            base=len(d),
            new_cells=int((~d.cell.isin(can_cells)).sum()),
            new_base_pts=int((~d.cell.isin(can_cells)).sum() and
                             (~d.cell.isin(zcells)).sum()),
            gold_strip=json.loads(json.dumps(
                _m(d.dH[gold_s]))) if False else None,
            mad_strip=round(float(np.median(np.abs(
                d.dH[gold_s] - d.dH[gold_s].median()))), 3) if
            gold_s.sum() >= 30 else None,
            n_strip=int(gold_s.sum()),
            mad_prior=round(float(np.median(np.abs(
                d.dH[gold_p] - d.dH[gold_p].median()))), 3) if
            gold_p.sum() >= 30 else None,
            n_prior=int(gold_p.sum()),
            med_strip=round(float(d.dH[gold_s].median()), 3) if
            gold_s.sum() else None,
            med_prior=round(float(d.dH[gold_p].median()), 3) if
            gold_p.sum() else None)
    out[tname] = row
    print(tname, json.dumps(row, ensure_ascii=False), flush=True)

# pooled medium
parts = []
for tname in TILES:
    fs = glob.glob(f"{R}/scripts/dem_cache/densify_pilot_5.93/"
                   f"{tname}_medium.csv.gz")
    if fs:
        parts.append(pd.read_csv(fs[0]))
a = pd.concat(parts, ignore_index=True)
fin = a.dH.notna()
bmask = fin & (a.dH.abs() <= 3.0) & (a.can30 < 100)
a = a[bmask].copy()
a["cell"] = a.lat.round(3).astype(str) + "_" + a.lon.round(3).astype(str)
nb = (~a.cell.isin(can_cells))
prior = a.n_cov.isna()
strip = (~prior) & a.n_cov.notna()
gold_s = strip & (a.n_cov >= 2) & (a.sigma <= 50) & (a.can30 < 200)
gold_p = prior & (a.can30 < 50)


def ms(x):
    x = np.asarray(x, float)
    off = np.median(x)
    return dict(n=len(x), med=round(float(off), 3),
                mad=round(float(np.median(np.abs(x - off))), 3),
                p95=round(float(np.percentile(np.abs(x - off), 95)), 3))


pooled = dict(base_pts=int(len(a)), base_new_cells=int(nb.sum()),
              share_new_cells=round(float(nb.mean()), 3),
              gold_strip=ms(a.dH[gold_s]), gold_prior=ms(a.dH[gold_p]),
              leaf_off_new=ms(a.dH[(a.season == "leaf_off")]),
              leaf_on_new=ms(a.dH[(a.season == "leaf_on")]))
print("POOLED medium (base):", json.dumps(pooled, ensure_ascii=False),
      flush=True)
out["_pooled_medium"] = pooled

json.dump(out, open(f"{R}/results/densify_pilot_5.93_cells.json", "w"),
          ensure_ascii=False, indent=1)
print("сохранено: results/densify_pilot_5.93_cells.json", flush=True)
