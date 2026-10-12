#!/usr/bin/env python3
"""5.90-prep: план южных зон для runner'а 5.83 (южная ветка).

Адаптация stage_plan batch_rollout_5.83.py на юг:
  - bbox юга: 59.70-59.90N, 30.10-30.62E (4 района, вне footprint 5.84);
  - окна 0.10x0.05 на выровненной сетке (шаг 0.05x0.025 -> 5x4 = 20 окон);
  - train-база: icesat2_clean.csv (8,545 точек на юге), порог >=150/окно;
  - плотность стрипов: один STAC-запрос arcticdem-strips-s2s041-2m,
    valid>=0.5, cloud<=0.3; dmax>=6 = стек возможен (канон 5.79),
    dmax<6 -> окно-кандидат остаётся, но будет prior-only (правило 5.78);
  - жадное подавление перекрытий, топ-12 по числу точек;
  - привязка окна к району по dz из dump_south_5.89.py.
Выход: results/rollout_plan_south_5.90.json + консоль.
"""
import json
import os

import numpy as np
import pandas as pd

HOME = '/tmp/my-project/gh-clone'
IC_CSV = f'{HOME}/scripts/data/icesat2_clean.csv'
OUTJ = f'{HOME}/results/rollout_plan_south_5.90.json'

SB = (30.10, 59.70, 30.62, 59.90)          # W,S,E,N юга
WIN_W, WIN_H = 0.10, 0.05
STEP_X, STEP_Y = 0.05, 0.025
IC_MIN, DENS_MIN = 150, 6
MAX_ZONES = 12

DZ = {"Кировский":      (59.83, 30.14, 59.90, 30.32),
      "Красносельский": (59.79, 30.10, 59.88, 30.30),
      "Пушкинский":     (59.70, 30.22, 59.80, 30.45),
      "Колпинский":     (59.70, 30.42, 59.80, 30.62)}


def district_of(w, s, e, n):
    best, bmax = '—', 0.0
    for k, (ds, dw, dn, de) in DZ.items():
        ov = max(0, min(e, de) - max(w, dw)) * max(0, min(n, dn) - max(s, ds))
        if ov > bmax:
            best, bmax = k, ov
    return best if bmax > 0 else '—'


def main():
    ic = pd.read_csv(IC_CSV)
    ic = ic[(ic.lon >= SB[0]) & (ic.lon <= SB[2]) &
            (ic.lat >= SB[1]) & (ic.lat <= SB[3])]
    print(f"юг: icesat2_clean {len(ic)} точек")

    # STAC: стрипы по всему югу, один запрос
    import requests
    from shapely.geometry import shape
    r = requests.post("https://stac.pgc.umn.edu/api/v1/search", json={
        "collections": ["arcticdem-strips-s2s041-2m"],
        "bbox": list(SB), "limit": 1000}, timeout=180)
    r.raise_for_status()
    feats = r.json().get('features', [])
    print(f"STAC стрипов в bbox юга: {len(feats)}")
    # плотностная сетка 0.05x0.025 (ячейка = шаг сетки окон)
    gx = np.arange(SB[0], SB[2] + 1e-9, STEP_X)
    gy = np.arange(SB[1], SB[3] + 1e-9, STEP_Y)
    dens = np.zeros((len(gy), len(gx)), dtype='int16')
    n_strips_ok = 0
    for it in feats:
        p = it['properties']
        v = p.get('pgc:valid_area_percent') or 0
        c = p.get('pgc:cloud_area_percent') or 1
        if v < 0.5 or c > 0.3:
            continue
        n_strips_ok += 1
        b = shape(it['geometry']).bounds
        i0 = max(int((b[0] - SB[0]) / STEP_X), 0)
        i1 = min(int((b[2] - SB[0]) / STEP_X) + 1, len(gx))
        j0 = max(int((b[1] - SB[1]) / STEP_Y), 0)
        j1 = min(int((b[3] - SB[1]) / STEP_Y) + 1, len(gy))
        if i1 > i0 and j1 > j0:
            dens[j0:j1, i0:i1] += 1
    print(f"стрипов после гейта valid/cloud: {n_strips_ok}")

    # окна на выровненной сетке
    cand = []
    for jy in range(len(gy)):
        for ix in range(len(gx)):
            w = round(gx[ix], 4)
            s = round(gy[jy], 4)
            e, n = round(w + WIN_W, 4), round(s + WIN_H, 4)
            if e > SB[2] + 1e-9 or n > SB[3] + 1e-9:
                continue
            m = ic[(ic.lon >= w) & (ic.lon <= e) &
                   (ic.lat >= s) & (ic.lat <= n)]
            if len(m) < IC_MIN:
                continue
            dmax = int(dens[jy, ix])
            cand.append(dict(bbox=[w, s, e, n], icesat=len(m), dens=dmax,
                             district=district_of(w, s, e, n)))
    cand.sort(key=lambda d: -d['icesat'])

    picked = []
    for c in cand:
        b = c['bbox']
        if any(not (b[2] <= p['bbox'][0] or b[0] >= p['bbox'][2] or
                    b[3] <= p['bbox'][1] or b[1] >= p['bbox'][3])
               for p in picked):
            continue
        picked.append(c)
        if len(picked) >= MAX_ZONES:
            break
    plan = dict(date=__import__("datetime").datetime.now().isoformat(), south_bbox=list(SB),
                n_candidates=len(cand), strips_total=n_strips_ok,
                dens_min=DENS_MIN, zones=picked)
    with open(OUTJ, 'w') as f:
        json.dump(plan, f, ensure_ascii=False, indent=1)
    print(f"кандидатов {len(cand)} -> зон {len(picked)}")
    for k, p in enumerate(picked):
        stack = 'стек' if p['dens'] >= DENS_MIN else 'prior-only'
        print(f"  {k+1:2d}. {p['district']:16s} bbox {p['bbox']} "
              f"icesat {p['icesat']:5d} strips {p['dens']:3d} -> {stack}")
    tot = sum(p['icesat'] for p in picked)
    print(f"сумма icesat по зонам: {tot}")


if __name__ == '__main__':
    main()
