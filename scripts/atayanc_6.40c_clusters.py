#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""6.40c — разбор кластеров отметок: вокруг кластерного бокса локально
пере-детектируем строки глифов, EDSR x4 кроп каждой -> лист cluster_*.png."""
import json, os
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage
import cv2
from cv2 import dnn_superres

SRC = '/home/z/my-project/upload/atayanc-2013-01.jpg'
OUTD = '/home/z/my-project/download/atayanc_6.40'
MODELS = '/home/z/my-project/models'
PAD = 60  # окно вокруг кластера

CLUSTERS = [55, 100, 125, 147, 154, 258, 296, 301, 388, 498]

imc = np.asarray(Image.open(SRC).convert('RGB'))
img = cv2.cvtColor(imc, cv2.COLOR_RGB2BGR)
gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
H, W = gray.shape

cands = {c['id']: c['box'] for c in json.load(open(f'{OUTD}/candidates_6.40.json'))}

sr = dnn_superres.DnnSuperResImpl_create()
sr.readModel(f'{MODELS}/EDSR_x4.pb')
sr.setModel('edsr', 4)

def edsr4(crop):
    h, w = crop.shape[:2]
    ph, pw = (4 - h % 4) % 4, (4 - w % 4) % 4
    if ph or pw:
        crop = cv2.copyMakeBorder(crop, 0, ph, 0, pw, cv2.BORDER_REPLICATE)
    up = sr.upsample(crop)
    return up[:h * 4, :w * 4]

try:
    font = ImageFont.load_default(size=13)
except TypeError:
    font = ImageFont.load_default()

out = {}
for cid in CLUSTERS:
    x0, y0, x1, y1 = cands[cid]
    wx0, wy0 = max(0, x0 - PAD), max(0, y0 - PAD)
    wx1, wy1 = min(W, x1 + PAD), min(H, y1 + PAD)
    sub = gray[wy0:wy1, wx0:wx1]
    dark = sub < 135
    lab, n = ndimage.label(dark)
    sl = ndimage.find_objects(lab)
    boxes = []
    for i, s in enumerate(sl):
        h = s[0].stop - s[0].start
        w = s[1].stop - s[1].start
        if 4 <= h <= 18 and 2 <= w <= 26:
            boxes.append((i + 1, s[1].start, s[0].start, w, h))
    boxes.sort(key=lambda b: (b[2] // 6, b[1]))
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
        sx0 = min(x[1] for x in grp) + wx0; sy0 = min(x[2] for x in grp) + wy0
        sx1 = max(x[1] + x[3] for x in grp) + wx0; sy1 = max(x[2] + x[4] for x in grp) + wy0
        strings.append((sx0, sy0, sx1, sy1, len(grp)))
    keep = [s for s in strings if 2 <= s[4] <= 9 and 6 <= (s[2] - s[0]) <= 70 and 5 <= (s[3] - s[1]) <= 18]
    keep.sort(key=lambda s: (s[1], s[0]))
    out[cid] = [{'box': [int(v) for v in s[:4]]} for s in keep]
    print(f'кластер #{cid}: строк {len(keep)}')

json.dump(out, open(f'{OUTD}/clusters_6.40.json', 'w'))

# лист: по строке на ячейку, подпись c<cluster>.<j>
CW, CH, LBL, COLS = 420, 260, 16, 4
cells = []
for cid, lst in out.items():
    for j, s in enumerate(lst):
        x0, y0, x1, y1 = s['box']
        mx0 = max(0, x0 - 6); my0 = max(0, y0 - 6)
        mx1 = min(W, x1 + 6); my1 = min(H, y1 + 6)
        crop = img[my0:my1, mx0:mx1]
        up = edsr4(crop)
        if up.shape[1] > CW - 8:
            up = cv2.resize(up, (CW - 8, int(up.shape[0] * (CW - 8) / up.shape[1])), interpolation=cv2.INTER_AREA)
        if up.shape[0] > CH - LBL - 4:
            up = cv2.resize(up, (int(up.shape[1] * (CH - LBL - 4) / up.shape[0]), CH - LBL - 4), interpolation=cv2.INTER_AREA)
        cells.append((cid, j, up))

PER = COLS * 3
np_ = 0
for si in range(0, len(cells), PER):
    chunk = cells[si:si + PER]
    rows = (len(chunk) + COLS - 1) // COLS
    sh = np.full((rows * CH, COLS * CW, 3), 255, np.uint8)
    sim = Image.fromarray(cv2.cvtColor(sh, cv2.COLOR_BGR2RGB))
    d = ImageDraw.Draw(sim)
    for j, (cid, jx, up) in enumerate(chunk):
        px, py = (j % COLS) * CW, (j // COLS) * CH
        sim.paste(Image.fromarray(cv2.cvtColor(up, cv2.COLOR_BGR2RGB)), (px + 2, py + LBL))
        d.text((px + 4, py + 1), f'c{cid}m{jx}', fill=(200, 0, 0), font=font)
    sim.save(f'{OUTD}/sheets_edsr/cluster_{np_:02d}.png')
    np_ += 1
print(f'cluster-ячеек: {len(cells)} -> {np_} листов')
