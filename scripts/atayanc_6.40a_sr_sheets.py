#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""6.40a — SR-апскейл плана Атаянц и контактные листы отметок.
1) Детектор кандидатов-отметок (релакс фильтров 6.30o).
2) EDSR x4 кроп вокруг каждого кандидата -> контактные листы 6x10.
3) LapSRN x4 тайлы всего листа 5x5 (визуальный скан-проход).
"""
import json, os, time
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage
import cv2
from cv2 import dnn_superres

SRC = '/home/z/my-project/upload/atayanc-2013-01.jpg'
OUTD = '/home/z/my-project/download/atayanc_6.40'
MODELS = '/home/z/my-project/models'
os.makedirs(f'{OUTD}/sheets_edsr', exist_ok=True)
os.makedirs(f'{OUTD}/tiles_lapsrn', exist_ok=True)

imc = np.asarray(Image.open(SRC).convert('RGB'))
img = cv2.cvtColor(imc, cv2.COLOR_RGB2BGR)
gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
H, W = gray.shape
print(f'лист: {W}x{H}')

# ---------- 1. кандидаты ----------
dark = gray < 135
lab, n = ndimage.label(dark)
sl = ndimage.find_objects(lab)
boxes = []
for i, s in enumerate(sl):
    h = s[0].stop - s[0].start
    w = s[1].stop - s[1].start
    if 4 <= h <= 18 and 2 <= w <= 26:
        boxes.append((i + 1, s[1].start, s[0].start, w, h))
print(f'глифов: {len(boxes)}')

boxes.sort(key=lambda b: (b[2] // 5, b[1]))
used = [False] * len(boxes)
strings = []
for i, b in enumerate(boxes):
    if used[i]:
        continue
    grp = [b]; used[i] = True; changed = True
    while changed:
        changed = False
        gy0 = min(x[2] for x in grp); gy1 = max(x[2] + x[4] for x in grp)
        gx1 = max(x[1] + x[3] for x in grp)
        for j, c in enumerate(boxes):
            if used[j]:
                continue
            cy0, cy1 = c[2], c[2] + c[4]
            ov = min(gy1, cy1) - max(gy0, cy0)
            if ov > 0.4 * min(gy1 - gy0, cy1 - cy0) and -2 < c[1] - gx1 <= 10:
                grp.append(c); used[j] = True; changed = True
    x0 = min(x[1] for x in grp); y0 = min(x[2] for x in grp)
    x1 = max(x[1] + x[3] for x in grp); y1 = max(x[2] + x[4] for x in grp)
    strings.append((x0, y0, x1, y1, len(grp), max(x[4] for x in grp)))

cand = [s for s in strings if 3 <= s[4] <= 9 and 6 <= (s[2] - s[0]) <= 70 and 6 <= s[5] <= 18]

def ring_light(x0, y0, x1, y1, thr=140):
    pad = 6
    X0, Y0 = max(0, x0 - pad), max(0, y0 - pad)
    X1, Y1 = min(W, x1 + pad), min(H, y1 + pad)
    sub = np.ones((Y1 - Y0, X1 - X0), bool)
    sub[y0 - Y0:y1 - Y0, x0 - X0:x1 - X0] = False
    vals = gray[Y0:Y1, X0:X1][sub]
    return np.mean(vals) > thr

cand = [s for s in cand if ring_light(*s[:4])]
cand.sort(key=lambda s: (s[1], s[0]))
print(f'кандидатов-отметок: {len(cand)}')

cands_json = [{'id': k, 'box': [int(v) for v in s[:4]], 'nglyphs': s[4], 'gh': s[5]}
              for k, s in enumerate(cand)]
json.dump(cands_json, open(f'{OUTD}/candidates_6.40.json', 'w'), ensure_ascii=False)

# ---------- 2. EDSR x4 контактные листы ----------
sr = dnn_superres.DnnSuperResImpl_create()
sr.readModel(f'{MODELS}/EDSR_x4.pb')
sr.setModel('edsr', 4)

def edsr4(crop_bgr):
    h, w = crop_bgr.shape[:2]
    ph, pw = (4 - h % 4) % 4, (4 - w % 4) % 4
    if ph or pw:
        crop_bgr = cv2.copyMakeBorder(crop_bgr, 0, ph, 0, pw, cv2.BORDER_REPLICATE)
    up = sr.upsample(crop_bgr)
    return up[:h * 4, :w * 4]

CW, CH, LBL = 324, 136, 16
COLS, ROWS = 6, 10
try:
    font = ImageFont.load_default(size=12)
except TypeError:
    font = ImageFont.load_default()

t0 = time.time(); n_done = 0
sheets = []
cells = []
for k, (x0, y0, x1, y1, ng, gh) in enumerate(cand):
    mx0 = max(0, x0 - 5); my0 = max(0, y0 - 5)
    mx1 = min(W, x1 + 5); my1 = min(H, y1 + 5)
    if mx1 - mx0 > 74: mx1 = mx0 + 74
    if my1 - my0 > 30: my1 = my0 + 30
    crop = img[my0:my1, mx0:mx1].copy()
    up = edsr4(crop)
    up = cv2.resize(up, (min(308, up.shape[1]), min(112, up.shape[0])),
                    interpolation=cv2.INTER_AREA if up.shape[1] > 308 else cv2.INTER_NEAREST)
    cells.append((k, up))
    n_done += 1
    if n_done % 60 == 0:
        print(f'  EDSR {n_done}/{len(cand)} ({time.time()-t0:.0f} c)')

def flush_sheets(cells, prefix):
    nsheet = 0
    for si in range(0, len(cells), COLS * ROWS):
        chunk = cells[si:si + COLS * ROWS]
        sh = np.full((ROWS * CH, COLS * CW, 3), 255, np.uint8)
        for j, (k, up) in enumerate(chunk):
            px, py = (j % COLS) * CW, (j // COLS) * CH
            sh[py + LBL:py + LBL + up.shape[0], px + 2:px + 2 + up.shape[1]] = up
        sim = Image.fromarray(cv2.cvtColor(sh, cv2.COLOR_BGR2RGB))
        d = ImageDraw.Draw(sim)
        for j, (k, up) in enumerate(chunk):
            px, py = (j % COLS) * CW, (j // COLS) * CH
            d.text((px + 4, py + 1), f'#{k}', fill=(200, 0, 0), font=font)
        p = f'{OUTD}/sheets_edsr/{prefix}_{nsheet:02d}.png'
        sim.save(p)
        nsheet += 1
    return nsheet

ns = flush_sheets(cells, 'edsr')
print(f'EDSR листов: {ns} за {time.time()-t0:.0f} c')

# ---------- 3. LapSRN x4 тайлы 5x5 ----------
sr2 = dnn_superres.DnnSuperResImpl_create()
sr2.readModel(f'{MODELS}/LapSRN_x4.pb')
sr2.setModel('lapsrn', 4)

GX, GY, OVL = 5, 5, 24
tw, th = W // GX, H // GY
t0 = time.time(); nt = 0
for gy in range(GY):
    for gx in range(GX):
        x0 = max(0, gx * tw - OVL); y0 = max(0, gy * th - OVL)
        x1 = min(W, (gx + 1) * tw + OVL); y1 = min(H, (gy + 1) * th + OVL)
        tile = img[y0:y1, x0:x1]
        up = sr2.upsample(tile)
        cv2.imwrite(f'{OUTD}/tiles_lapsrn/tile_r{gy}c{gx}_x{y0}y{x0}.png', up)
        nt += 1
print(f'LapSRN тайлов: {nt} за {time.time()-t0:.0f} c')
print('Готово.')
