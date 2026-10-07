#!/usr/bin/env python3
"""
5.75 шаг 1: отбор 2м-стрип ArcticDEM для пилота «Невский восток»
(59.845-59.895N, 30.43-30.52E — Октябрьская наб./Уткина Заводь,
зона пере-диагностированных аномалий 5.74).

Критерии: valid>=0.5, cloud<=0.2, не xtrack при наличии достатка,
ранжирование по (valid desc, cloud asc, дата desc); берём топ-N=24
с дифференциацией по годам. Выход: scripts/data/strips_pilot_5.75.csv
"""
import json
from pathlib import Path
from datetime import datetime
import pandas as pd
import pystac_client

PILOT = (30.43, 59.845, 30.52, 59.895)   # W, S, E, N
N_MAX = 24
OUT = Path('/home/z/my-project/repo/scripts/data/strips_pilot_5.75.csv')

cat = pystac_client.Client.open("https://stac.pgc.umn.edu/api/v1/")
items = list(cat.search(collections=["arcticdem-strips-s2s041-2m"],
                        bbox=PILOT).items())
print(f"items: {len(items)}")

rows = []
for it in items:
    p = it.properties
    rows.append(dict(
        id=it.id[:80],
        dt=(p.get('datetime') or '')[:10],
        year=(p.get('datetime') or '')[:4],
        pairname=(p.get('pgc:pairname') or ''),
        cloud=p.get('pgc:cloud_area_percent'),
        valid=p.get('pgc:valid_area_percent'),
        water=p.get('pgc:water_area_percent'),
        rmse=p.get('pgc:rmse'),
        xtrack=p.get('pgc:is_xtrack', False),
        matchtag=p.get('pgc:masked_matchtag_density'),
        conv=p.get('pgc:avg_convergence_angle'),
        dem_href=it.assets['dem'].href if 'dem' in it.assets else ''))
df = pd.DataFrame(rows)
df = df[(df.valid >= 0.5) & (df.cloud <= 0.2)].copy()
print(f"после фильтра: {len(df)}")

# ранжирование: валидность ↑, облака ↓, дата ↓; штраф xtrack лёгкий
df['score'] = (df.valid * 10 - df.cloud * 5 -
               df.xtrack.astype(float) * 2 - df.rmse.fillna(3) * 0.3)
df = df.sort_values(['score'], ascending=False)

# дедуп по pairname (лучший стрип пары)
df = df.drop_duplicates('pairname', keep='first')

# ограничение N с сохранением годового разнообразия
sel, per_year = [], {}
for _, r in df.iterrows():
    y = r.year
    if per_year.get(y, 0) >= 8:
        continue
    per_year[y] = per_year.get(y, 0) + 1
    sel.append(r)
    if len(sel) >= N_MAX:
        break
sel = pd.DataFrame(sel)
sel.to_csv(OUT, index=False)
print(sel[['id', 'dt', 'valid', 'cloud', 'rmse', 'xtrack']].to_string(index=False))
print(f"\nвыбрано {len(sel)} стрипов, годы: {sorted(sel.year.unique())}")
meta = dict(pilot_bbox=PILOT, n_items=len(items), n_filtered=int(len(df)),
            n_selected=len(sel), years=sorted(sel.year.unique()))
Path('/home/z/my-project/repo/scripts/data/strips_pilot_meta_5.75.json').write_text(
    json.dumps(meta, ensure_ascii=False, indent=1))
