#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""6.40f — оверлей найденных отметок на LapSRN-тайлы для финального
визуального скана (ищем отметки БЕЗ зелёной точки)."""
import json, os
import numpy as np
import cv2

OUTD = '/home/z/my-project/download/atayanc_6.40'
TILES = f'{OUTD}/tiles_lapsrn'

# свод найденных отметок: (px_x, px_y, value, conf)
MARKS = [
    (4, 1.08, 'H'), (5, 2.26, 'H'), (7, 2.42, 'M'), (19, 2.93, 'M'), (33, 2.49, 'H'),
    (40, 2.13, 'M'), (43, 2.27, 'M'), (45, 2.10, 'L'), (46, 2.00, 'M'), (54, 2.77, 'M'),
    (55, 1.40, 'M'), (60, 2.85, 'M'), (66, 2.20, 'M'), (68, 5.05, 'L'), (70, 1.25, 'M'),
    (72, 0.84, 'M'), (89, 2.20, 'M'), (91, 2.27, 'M'), (94, 2.06, 'M'), (100, 1.38, 'H'),
    (110, 2.87, 'H'), (125, 2.51, 'M'), (140, 2.04, 'H'), (143, 2.16, 'M'), (147, 2.73, 'M'),
    (149, 4.60, 'M'), (152, 2.53, 'M'), (154, 2.52, 'M'), (155, 2.55, 'M'), (156, 2.45, 'M'),
    (194, 2.13, 'M'), (232, 2.15, 'M'), (238, 2.25, 'M'), (258, 6.85, 'M'), (270, 2.46, 'M'),
    (271, 3.05, 'M'), (290, 2.31, 'M'), (293, 2.60, 'H'), (301, 1.05, 'M'), (328, 2.17, 'M'),
    (337, 3.25, 'M'), (388, 2.27, 'H'), (388, 2.71, 'H'), (388, 3.08, 'H'), (388, 3.18, 'H'),
    (389, 2.41, 'M'), (391, 2.57, 'M'), (395, 2.94, 'M'), (396, 6.05, 'L'), (407, 2.12, 'M'),
    (444, 2.15, 'M'), (473, 2.83, 'M'), (479, 2.83, 'M'), (484, 2.02, 'M'), (486, 2.03, 'H'),
    (495, 2.52, 'M'), (498, 2.43, 'M'), (498, 2.38, 'M'), (499, 2.55, 'M'), (504, 2.55, 'M'),
    (528, 2.39, 'M'), (542, 2.76, 'M'), (562, 1.05, 'M'), (567, 5.24, 'L'), (568, 2.00, 'H'),
    (578, 3.00, 'M'), (595, 2.86, 'M'),
]
ZADD = {  # доп. из релакс-зон: zlabel -> (idx_in_zone, value, conf)
    ('z0', 21): (2.89, 'M'), ('z0', 25): (4.07, 'L'),
    ('z1', 1): (1.00, 'L'), ('z1', 3): (2.55, 'M'), ('z1', 8): (2.06, 'L'),
    ('z2', 34): (1.21, 'L'), ('z2', 44): (2.52, 'M'),
    ('z3', 39): (4.64, 'M'), ('z3', 40): (4.58, 'L'),
    ('z4', 1): (1.43, 'M'), ('z4', 38): (5.54, 'L'), ('z4', 41): (1.27, 'M'), ('z4', 47): (6.47, 'L'),
    ('z5', 28): (2.44, 'M'),
}

cands = {c['id']: c['box'] for c in json.load(open(f'{OUTD}/candidates_6.40.json'))}
zones = json.load(open(f'{OUTD}/zones_6.40.json'))
ZKEY = {'z0': 55, 'z1': 100, 'z2': 147, 'z3': 154, 'z4': 301, 'z5': 498}

pts = []
for mid, val, conf in MARKS:
    x0, y0, x1, y1 = cands[mid]
    pts.append((int((x0 + x1) / 2), int((y0 + y1) / 2), val, conf, str(mid)))
for (zp, j), (val, conf) in ZADD.items():
    box = zones[str(ZKEY[zp])][j]['box']
    x0, y0, x1, y1 = box
    pts.append((int((x0 + x1) / 2), int((y0 + y1) / 2), val, conf, zp + 'm' + str(j)))

json.dump(pts, open(f'{OUTD}/marks_px_6.40.json', 'w'))
print('отметок с px:', len(pts))

for f in sorted(os.listdir(TILES)):
    if not f.endswith('.png'):
        continue
    # имя tile_r{gy}c{gx}_x{Y}y{X}.png  (x=Y0 y=X0 в моём форматировании: f'x{y0}y{x0}')
    tag = f[:-4].split('_')[2]  # x{y0}y{x0}
    y0s, x0s = tag[1:].split('y')
    ty, tx = int(y0s), int(x0s)
    img = cv2.imread(f'{TILES}/{f}')
    for px, py, val, conf, lab in pts:
        X = (px - tx) * 4
        Y = (py - ty) * 4
        if 0 <= X < img.shape[1] and 0 <= Y < img.shape[0]:
            col = (0, 255, 0) if conf != 'L' else (0, 200, 255)
            cv2.circle(img, (X, Y), 26, col, 3)
            cv2.circle(img, (X, Y), 3, col, -1)
    cv2.imwrite(f'{OUTD}/tiles_overlay/{f}', img)
print('оверлеи готовы:', len(os.listdir(f'{OUTD}/tiles_overlay')))
