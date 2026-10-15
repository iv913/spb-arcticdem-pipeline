#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""6.30n — локальная аффина СЗ-фрагмента (стадион + ледовый СК), QA-оверлей."""
import json
import numpy as np
from shapely.geometry import shape, Point
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from PIL import Image

OUTD = '/tmp/my-project/results/atayanc_6.30'
bjson = json.load(open(f'{OUTD}/osm_buildings_6.30.geojson'))

# поиск реальных полигонов: круглый ЛСК у (30.293,59.9507) и стадион у (30.286,59.9512)
cands = []
for ft in bjson['features']:
    g = shape(ft['geometry'])
    c = g.centroid
    a_m2 = g.area * 56050 * 111320
    cands.append((c.x, c.y, a_m2, ft))
arena = min((c for c in cands if 12000 <= c[2] <= 40000 and
             Point(c[0], c[1]).distance(Point(30.2930, 59.9507)) < 0.0012),
            key=lambda c: Point(c[0], c[1]).distance(Point(30.2930, 59.9507)), default=None)
stad = min((c for c in cands if 6000 <= c[2] <= 60000 and
            Point(c[0], c[1]).distance(Point(30.2860, 59.9512)) < 0.0015),
           key=lambda c: Point(c[0], c[1]).distance(Point(30.2860, 59.9512)), default=None)
for nm, c in (('ЛСК (круг)', arena), ('стадион', stad)):
    if c:
        print(f'{nm}: ({c[0]:.5f},{c[1]:.5f}) area {c[2]:.0f} м2')
    else:
        print(f'{nm}: НЕ НАЙДЕН')

# план-пиксы центров (по qa_nw2/nw_boundary); реальные координаты из OSM-матча
stad = (30.28625, 59.95192)          # стадион им. Ленина (OSM, 11308 м2)
arena = (30.29204, 59.95028)         # Спорткомплекс «Юбилейный» (OSM)
PLAN = {'stad': (245, 137), 'arena': (885, 365)}

MLON = 111320.0 * np.cos(np.radians(59.95))
MLAT = 111132.0
if stad and arena:
    P = np.array([PLAN['stad'], PLAN['arena']], float)
    Lm = np.array([[stad[0] * MLON, stad[1] * MLAT],
                   [arena[0] * MLON, arena[1] * MLAT]], float)
    # similarity: E = a x + b y + c ; N = -b x + a y + f  (прямое решение 2x2)
    dP = P[1] - P[0]
    dL = Lm[1] - Lm[0]
    D = dP[0] ** 2 + dP[1] ** 2
    a = (dP[0] * dL[0] - dP[1] * dL[1]) / D
    b = (dP[1] * dL[0] + dP[0] * dL[1]) / D
    c_ = Lm[0, 0] - a * P[0, 0] - b * P[0, 1]
    f_ = Lm[0, 1] + b * P[0, 0] - a * P[0, 1]
    ca, sa, s = a, b, np.hypot(a, b)
    print(f'СЗ-фрагмент: scale {s:.4f} м/px, rot {np.degrees(np.arctan2(b, a)):+.2f}°')
    json.dump({'scale_mpx': float(s), 'rot_deg': float(np.degrees(np.arctan2(b, a))),
               'aff_m': [float(a), float(b), float(c_), float(-b), float(a), float(f_)],
               'gcp_plan': PLAN, 'gcp_real': {'stad': [stad[0], stad[1]], 'arena': [arena[0], arena[1]]}},
              open(f'{OUTD}/nw_affine_6.30.json', 'w'), indent=1)

    # QA: план СЗ-угла + OSM через ЛОКАЛЬНУЮ аффину
    geo = json.load(open(f'{OUTD}/georef_6.30.json'))
    a0, b0, c0, d0, e0, f0 = geo['affine_deg']
    img = Image.open('/home/z/my-project/upload/atayanc-2013-01.jpg')
    fig, ax = plt.subplots(figsize=(16, 10), constrained_layout=True)
    ax.imshow(np.asarray(img)[:640, 0:1500])
    for ft in bjson['features']:
        g = np.array(ft['geometry']['coordinates'][0], float)
        cx, cy = g[:, 0].mean(), g[:, 1].mean()
        em, nm_ = ca * (cx * MLON - 0) * 0 + (cx * MLON), cy * MLAT  # заглушка
        # переводим реальные -> px через обратную локальной аффины
        M = np.array([[ca, sa], [-sa, ca]])
        inv = np.linalg.inv(M)
        dx, dy = inv @ np.array([cx * MLON - c_, cy * MLAT - f_])
        if -150 < dx < 1650 and -150 < dy < 790:
            px, py = inv @ np.vstack([g[:, 0] * MLON - c_, g[:, 1] * MLAT - f_])
            ax.plot(px, py, color='lime', lw=1.0, alpha=0.9)
    ax.grid(color='red', alpha=0.3, lw=0.4)
    ax.set_xticks(range(0, 1500, 100)); ax.set_yticks(range(0, 640, 100))
    ax.set_title('СЗ-фрагмент: план + OSM через ЛОКАЛЬНУЮ аффину', fontsize=12)
    fig.savefig(f'{OUTD}/qa_nw_local.png', dpi=120)
    print('qa_nw_local.png готов')
