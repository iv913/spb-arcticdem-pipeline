#!/usr/bin/env python3
"""
5.68-A/B: Перепроверка аномальных якорей archi-набора с использованием
локального эталона ICESat-2 (36620 точек, канон aaeed88).

Логика:
  dH = h_model - value (value = отметка плана).
  Для каждого якоря:
    1) диапазон отметок плана (value) и h_model;
    2) локальный эталон: медиана h_icesat_baltic в радиусе R вокруг якоря,
       только точки с |dH_corrected|<=3 м (наземный фон);
       R сканируется 100/150/300/500 м (по плотности треков);
    3) bias_model = h_model_med - icesat_med;
    4) диагноз: bias_model ~ 0 -> проблема в отметках плана (датум/пол/OCR);
       bias_model объясняет dH -> проблема геолокации/модели DEM;
       иначе комбинированная.
Вывод: results/anomaly_recheck_5.68.json + печать.
"""
import csv
import json
import numpy as np
import pandas as pd
from collections import defaultdict

ARCHI = "scripts/data/archi_final_expanded_v2.csv"
ICESAT = "scripts/data/icesat2_corrected.csv"
OUT = "results/anomaly_recheck_5.68.json"

rows = list(csv.DictReader(open(ARCHI)))
ic = pd.read_csv(ICESAT)
ic_bg = ic[ic.abs_dH_corrected <= 3.0].copy()  # наземный фон
print(f"archi: {len(rows)} отм.; ICESat фон (|dH|<=3м): {len(ic_bg)} из {len(ic)}")

by_proj = defaultdict(list)
for r in rows:
    by_proj[r["project_name"]].append(r)

ANOMALIES = [
    "АНО Футбольный клуб «Коломяги»",
    "Многофункциональный комплекс на территории Электростанции N2",
    "Деловой центр",
    "БЦ на Поклонной горе",
    "ID Moskovskiy",
]

def local_ground(lat, lon, radius_deg):
    d2 = ((ic_bg.lat - lat) * 111.32 * np.cos(np.radians(lat))) ** 2 + \
         ((ic_bg.lon - lon) * 55.0) ** 2
    m = d2 <= radius_deg ** 2
    n = int(m.sum())
    if n < 3:
        return None, n, None
    return float(ic_bg.loc[m, "h_icesat_baltic"].median()), n, None

results = []
for proj in ANOMALIES:
    rs = by_proj.get(proj)
    if not rs:
        continue
    lat = float(rs[0]["lat"]); lon = float(rs[0]["lon"])
    vals = sorted(float(r["value"]) for r in rs)
    hms = sorted(float(r["h_model"]) for r in rs)
    dh = sorted(float(r["dH"]) for r in rs)
    n = len(rs)
    med_val, med_hm, med_dh = vals[n // 2], hms[n // 2], dh[n // 2]
    for R in (100.0, 150.0, 300.0, 500.0):
        med_ic, nic, _ = local_ground(lat, lon, R / 111320.0)
        if med_ic is not None:
            break
    bias_model = (med_hm - med_ic) if med_ic is not None else None
    diag = None
    if bias_model is not None:
        if abs(bias_model) < 1.0:
            diag = "модель на месте -> проблема в отметках плана (датум/пол/OCR)"
        else:
            frac = bias_model / med_dh if abs(med_dh) > 0.2 else 0
            if 0.5 <= frac <= 1.5:
                diag = f"модель смещена {bias_model:+.1f}м — объясняет dH (геолокация/DEM)"
            else:
                diag = f"комбинированная: bias_model {bias_model:+.1f}м при dH {med_dh:+.1f}м"
    rec = dict(project=proj, n=n, lat=lat, lon=lon,
               value_min=vals[0], value_med=med_val, value_max=vals[-1],
               h_model_med=med_hm, dH_med=med_dh,
               icesat_med=med_ic, icesat_n=nic, icesat_R=R,
               bias_model=bias_model, diag=diag)
    results.append(rec)
    print(json.dumps(rec, ensure_ascii=False, indent=1))

json.dump(results, open(OUT, "w"), ensure_ascii=False, indent=1)
print("OK:", OUT)
