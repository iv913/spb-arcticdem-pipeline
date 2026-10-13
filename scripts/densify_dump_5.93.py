#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""5.93b — Полный дамп ATL06 cnf=MEDIUM (3) «уплотнение съёмки».

Протокол 5.86e/5.89b, единственное изменение cnf HIGH(4)->MEDIUM(3)
(решение по пилоту 5.93a: +16.6% окон, gold 0.298, 43% base-точек в новых
ячейках 0.001°, почти все в prior-ветви; cnf=2 LOW отбракован — gold 0.50).

Тайлы: север 20 (bbox 5.86: 29.36-30.56 / 59.8905-60.1183, шаг 0.12x0.114)
     + юг 8 (bbox 5.89: 30.10-30.62 / 59.70-59.90, шаг 0.13x0.10).
Возобновляемо по тайлам; TILE_LIMIT тайлов за прогон.

Выход:
  scripts/dem_cache/densify_tiles_5.93/{N|S}_tile_XX.csv.gz
  scripts/data/icesat2_atl06_dump_med_5.93.csv.gz (после всех тайлов)
  results/dump_med_5.93.json
"""
import os
import json
import glob
import time

import numpy as np
import pandas as pd

R = "/tmp/my-project/gh-clone"
TILEDIR = f"{R}/scripts/dem_cache/densify_tiles_5.93"
OUT = f"{R}/scripts/data/icesat2_atl06_dump_med_5.93.csv.gz"
RESJ = f"{R}/results/dump_med_5.93.json"
DELTA = 16.04
CNF = int(os.environ.get("DENS_CNF", "3"))
LIMIT = int(os.environ.get("TILE_LIMIT", "999"))
os.makedirs(TILEDIR, exist_ok=True)

# --- тайлы: (region, x0, y0, x1, y1)
tiles = []
for reg, (lo0, lo1, la0, la1, dx, dy) in {
        "N": (29.36, 30.56, 59.8905, 60.1183, 0.12, 0.114),
        "S": (30.10, 30.62, 59.70, 59.90, 0.13, 0.10)}.items():
    x = lo0
    while x < lo1 - 1e-9:
        y = la0
        while y < la1 - 1e-9:
            tiles.append((reg, x, y, min(x + dx, lo1), min(y + dy, la1)))
            y += dy
        x += dx
print(f"cnf={CNF}, тайлов: {len(tiles)}", flush=True)

from sliderule import icesat2
icesat2.init("slideruleearth.io", verbose=False)

parm_base = dict(t0="2019-01-01", t1="2026-12-31", srt=icesat2.SRT_LAND,
                 cnf=CNF, ats=10.0, cnt=5, len=40.0, res=20.0, maxi=1)

new_cnt = 0
t_start = time.time()
for i, (reg, x0, y0, x1, y1) in enumerate(tiles):
    fp = f"{TILEDIR}/{reg}_tile_{i:02d}.csv.gz"
    if os.path.exists(fp):
        continue
    if new_cnt >= LIMIT:
        print(f"лимит {LIMIT} новых тайлов за прогон — перезапустить",
              flush=True)
        break
    parm = dict(parm_base)
    parm["poly"] = [{"lon": x0, "lat": y0}, {"lon": x1, "lat": y0},
                    {"lon": x1, "lat": y1}, {"lon": x0, "lat": y1}]
    gdf = None
    for att in range(3):
        try:
            gdf = icesat2.atl06p(parm)
            break
        except Exception as e:
            print(f"  [{reg}{i}] retry {att+1}: {str(e)[:70]}", flush=True)
            time.sleep(5)
    if gdf is not None and len(gdf):
        g = gdf.geometry
        pd.DataFrame(dict(
            lat=g.y.values, lon=g.x.values, h_mean=gdf.h_mean.values,
            h_sigma=gdf.h_sigma.values, n_fit=gdf.n_fit_photons.values,
            time_ns=gdf.index.astype("int64").values, rgt=gdf.rgt.values,
            cycle=gdf.cycle.values, gt=gdf["gt"].values)).to_csv(
            fp, index=False, compression="gzip")
        n = len(gdf)
    else:
        pd.DataFrame(columns=["lat", "lon", "h_mean", "h_sigma", "n_fit",
                              "time_ns", "rgt", "cycle", "gt"]).to_csv(
            fp, index=False, compression="gzip")
        n = 0
    new_cnt += 1
    print(f"  [{i+1}/{len(tiles)}] {reg} {x0:.2f}/{y0:.2f}: {n} seg "
          f"(t={time.time()-t_start:.0f}s)", flush=True)

files = sorted(glob.glob(f"{TILEDIR}/*.csv.gz"))
if len(files) < len(tiles):
    print(f"готово {len(files)}/{len(tiles)} тайлов — продолжение при "
          f"следующем запуске", flush=True)
    raise SystemExit(0)

# ---------- merge ----------
frames = []
regs = []
for f in files:
    d = pd.read_csv(f)
    regs.extend([os.path.basename(f)[0]] * len(d))
    frames.append(d)
dump = pd.concat(frames, ignore_index=True)
dump["region"] = regs
dump["h_baltic"] = dump.h_mean - DELTA
dump["date"] = pd.to_datetime(dump.time_ns).dt.strftime("%Y%m%d")
dump = dump.sort_values(["lat", "lon"]).reset_index(drop=True)
dump.to_csv(OUT, index=False, compression="gzip")

res = dict(protocol=f"atl06p len=40 res=20 cnf={CNF} srt=LAND 2019-2026, "
                    "delta_baltic=16.04; север 29.36-30.56/59.8905-60.1183 "
                    "+ юг 30.10-30.62/59.70-59.90",
           cnf=CNF, tiles=len(tiles), raw_segments=int(len(dump)),
           date_min=str(dump.date.min()), date_max=str(dump.date.max()),
           rgt=int(dump.rgt.nunique()),
           region_n=int((dump.region == "N").sum()),
           region_s=int((dump.region == "S").sum()))
json.dump(res, open(RESJ, "w"), ensure_ascii=False, indent=1)
print(json.dumps(res, ensure_ascii=False, indent=1), flush=True)
print("ГОТОВО", flush=True)
