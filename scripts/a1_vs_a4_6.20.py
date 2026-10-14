#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""6.20c — Сравнение Гавань/о. Декабристов: A1 vs A4 (разница = экстраполяция).

Боксы из sources_diag_5.96e. Считаются площади fill-зон (клип 6.20b) внутри
боксов по уровням; разница A4−A1 = территории, попадающие в продукт только
за счёт дальнего допущения (экстраполяция).

Выход: results/fusion_6.20/a1_vs_a4_gavan_decabr_6.20.json (+ печать).
"""
import json
import math
import os

from shapely.geometry import shape as shp, box as shbox

FUS = '/tmp/my-project/results/fusion_6.20'
KM2 = 12392.0 * math.cos(math.radians(59.9))

BOXES = {
    'decabristov': ('Остров Декабристов (ядро)', (30.205, 59.900, 30.280, 59.9415)),
    'gavan': ('Гавань В.О.', (30.200, 59.931, 30.260, 59.956)),
    'bolshoy': ('Большой проспект В.О. (эталон)', (30.239249, 59.926349, 30.290829, 59.941507)),
}

res = {'stage': '6.20c', 'note': 'площади fill-зон в боксе, км2 (клип 6.20b)'}
for tag, (name, bb) in BOXES.items():
    b = shbox(*bb)
    res[tag] = {'name': name, 'bbox': bb, 'box_km2': round(b.area * KM2, 2),
                'levels': {}}
    perA = {}
    for A in (1, 4):
        with open(os.path.join(FUS, f'fill_zones_6.20_A{A}_clip.geojson')) as f:
            fc = json.load(f)
        m = {}
        for ft in fc['features']:
            lv = ft['properties']['level_m']
            if lv > 5.0:
                continue
            g = shp(ft['geometry'])
            m[f"{lv:.1f}"] = round(g.intersection(b).area * KM2, 2)
        perA[A] = m
    for lv in sorted(set(perA[1]) | set(perA[4]), key=float):
        a1 = perA[1].get(lv, 0.0)
        a4 = perA[4].get(lv, 0.0)
        res[tag]['levels'][lv] = {'A1': a1, 'A4': a4, 'extrap': round(a4 - a1, 2)}

out = os.path.join(FUS, 'a1_vs_a4_gavan_decabr_6.20.json')
with open(out, 'w') as f:
    json.dump(res, f, ensure_ascii=False, indent=1)

for tag, r in res.items():
    if not isinstance(r, dict):
        continue
    print(f"\n== {r['name']} (бокс {r['box_km2']} км2)")
    print('  уровень |   A1    |   A4    | экстрап (A4-A1)')
    for lv, v in r['levels'].items():
        print(f"  +{lv} м   | {v['A1']:6.2f} | {v['A4']:6.2f} | {v['extrap']:+6.2f}")
print('\n', out)
