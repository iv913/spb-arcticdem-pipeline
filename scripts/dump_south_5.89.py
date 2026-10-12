#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""5.89b — Южный ATL06-дамп (SlideRule) по 4 южным районам (Кировский,
Красносельский, Пушкинский, Колпинский): «снятие большего количества точек»
на территорию вне footprint мозаики 5.84 — задел под южное расширение тиража.

Протокол = 5.86e: сегменты 40 м/шаг 20 м, cnf HIGH, land, 2019-2026,
h_baltic = h_mean − 16.04 (δ 5.68). Возобновляемо по тайлам.

Выход:
  scripts/dem_cache/atl06_tiles_south/tile_XX.csv   (чанки)
  scripts/data/icesat2_atl06_dump_south_5.89.csv.gz (объединённый дамп)
  results/dump_south_5.89.json
"""
import numpy as np
import pandas as pd
import json, os, time, glob

R = "/tmp/my-project/gh-clone"
TILEDIR = f"{R}/scripts/dem_cache/atl06_tiles_south"
OUT = f"{R}/scripts/data/icesat2_atl06_dump_south_5.89.csv.gz"
RESJ = f"{R}/results/dump_south_5.89.json"
DELTA_BALTIC = 16.04
os.makedirs(TILEDIR, exist_ok=True)

from sliderule import icesat2
icesat2.init("slideruleearth.io", verbose=False)

# южный bbox: 4 района
lon0, lon1 = 30.10, 30.62
lat0, lat1 = 59.70, 59.90
dx, dy = 0.13, 0.10
tiles = []
x = lon0
while x < lon1 - 1e-9:
    y = lat0
    while y < lat1 - 1e-9:
        tiles.append((x, y, min(x + dx, lon1), min(y + dy, lat1)))
        y += dy
    x += dx
print(f"тайлов: {len(tiles)}", flush=True)

parm_base = dict(t0="2019-01-01", t1="2026-12-31", srt=icesat2.SRT_LAND,
                 cnf=icesat2.CNF_SURFACE_HIGH, ats=10.0, cnt=5, len=40.0,
                 res=20.0, maxi=1)

LIMIT = int(os.environ.get("TILE_LIMIT", "999"))
new_cnt = 0
t_start = time.time()
for i, (x0, y0, x1, y1) in enumerate(tiles):
    fp = f"{TILEDIR}/tile_{i:02d}.csv"
    if os.path.exists(fp):
        continue
    if new_cnt >= LIMIT:
        print(f"лимит {LIMIT} новых тайлов за прогон достигнут — перезапустить",
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
            print(f"  [{i+1}/{len(tiles)}] retry {att+1}: {str(e)[:70]}",
                  flush=True)
            time.sleep(5)
    if gdf is not None and len(gdf):
        g = gdf.geometry
        pd.DataFrame(dict(
            lat=g.y.values, lon=g.x.values, h_mean=gdf.h_mean.values,
            h_sigma=gdf.h_sigma.values, n_fit=gdf.n_fit_photons.values,
            time_ns=gdf.index.astype("int64").values, rgt=gdf.rgt.values,
            cycle=gdf.cycle.values, gt=gdf["gt"].values)).to_csv(fp, index=False)
    else:
        pd.DataFrame(columns=["lat", "lon", "h_mean", "h_sigma", "n_fit",
                              "time_ns", "rgt", "cycle", "gt"]).to_csv(fp,
                                                                       index=False)
    new_cnt += 1
    print(f"  [{i+1}/{len(tiles)}] {x0:.2f}/{y0:.2f}: "
          f"{0 if gdf is None else len(gdf)} seg (t={time.time()-t_start:.0f}s)",
          flush=True)

files = sorted(glob.glob(f"{TILEDIR}/tile_*.csv"))
if len(files) < len(tiles):
    print(f"готово {len(files)}/{len(tiles)} тайлов — продолжение при "
          f"следующем запуске", flush=True)
    raise SystemExit(0)

# ---------- merge + метрики
dump = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
dump["h_baltic"] = dump.h_mean - DELTA_BALTIC
dump["date"] = pd.to_datetime(dump.time_ns).dt.strftime("%Y%m%d")
dump.to_csv(OUT, index=False, compression="gzip")

dz = {"Кировский":      (59.83, 30.14, 59.90, 30.32),
      "Красносельский": (59.79, 30.10, 59.88, 30.30),
      "Пушкинский":     (59.70, 30.22, 59.80, 30.45),
      "Колпинский":     (59.70, 30.42, 59.80, 30.62)}
per_d = {}
for name, (a, b, c, d) in dz.items():
    m = dump[(dump.lat >= a) & (dump.lat < c) & (dump.lon >= b) & (dump.lon < d)]
    per_d[name] = dict(n=int(len(m)),
                       h_baltic_med=(round(float(m.h_baltic.median()), 2)
                                     if len(m) else None))

res = dict(protocol="atl06p len=40 res=20 cnf=HIGH srt=LAND 2019-2026, "
                     "delta_baltic=16.04; южный bbox 30.10-30.62 / 59.70-59.90",
           tiles=len(tiles), raw_segments=int(len(dump)),
           date_min=str(dump.date.min()), date_max=str(dump.date.max()),
           rgt=int(dump.rgt.nunique()), per_district=per_d)
json.dump(res, open(RESJ, "w"), ensure_ascii=False, indent=1)
print(json.dumps(res, ensure_ascii=False, indent=1), flush=True)
print("ГОТОВО", flush=True)
