#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""6.40e — EDSR x4 кропы всех строк зон (релакс-детекция 6.40d) -> листы 6x10."""
import json, os
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import cv2
from cv2 import dnn_superres

SRC = '/home/z/my-project/upload/atayanc-2013-01.jpg'
OUTD = '/home/z/my-project/download/atayanc_6.40'
MODELS = '/home/z/my-project/models'

zones = json.load(open(f'{OUTD}/zones_6.40.json'))
imc = np.asarray(Image.open(SRC).convert('RGB'))
img = cv2.cvtColor(imc, cv2.COLOR_RGB2BGR)
H, W = img.shape[:2]

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
    font = ImageFont.load_default(size=12)
except TypeError:
    font = ImageFont.load_default()

CW, CH, LBL, COLS, ROWS = 324, 136, 14, 6, 10
cells = []
ZIDX = {55: 0, 100: 1, 147: 2, 154: 3, 301: 4, 498: 5}
for cid_s, lst in zones.items():
    cid = int(cid_s)
    for j, s in enumerate(lst):
        x0, y0, x1, y1 = s['box']
        mx0 = max(0, x0 - 4); my0 = max(0, y0 - 4)
        mx1 = min(W, x1 + 4); my1 = min(H, y1 + 4)
        crop = img[my0:my1, mx0:mx1].copy()
        up = edsr4(crop)
        if up.shape[1] > 308:
            up = cv2.resize(up, (308, int(up.shape[0] * 308 / up.shape[1])), interpolation=cv2.INTER_AREA)
        if up.shape[0] > 112:
            up = cv2.resize(up, (int(up.shape[1] * 112 / up.shape[0]), 112), interpolation=cv2.INTER_AREA)
        cells.append((f'z{ZIDX[cid]}m{j}', up))

ns = 0
for si in range(0, len(cells), COLS * ROWS):
    chunk = cells[si:si + COLS * ROWS]
    sh = np.full((ROWS * CH, COLS * CW, 3), 255, np.uint8)
    for j, (lab, up) in enumerate(chunk):
        px, py = (j % COLS) * CW, (j // COLS) * CH
        sh[py + LBL:py + LBL + up.shape[0], px + 2:px + 2 + up.shape[1]] = up
    sim = Image.fromarray(cv2.cvtColor(sh, cv2.COLOR_BGR2RGB))
    d = ImageDraw.Draw(sim)
    for j, (lab, up) in enumerate(chunk):
        px, py = (j % COLS) * CW, (j // COLS) * CH
        d.text((px + 3, py + 0), lab, fill=(200, 0, 0), font=font)
    sim.save(f'{OUTD}/sheets_edsr/zonecrop_{ns:02d}.png')
    ns += 1
print(f'ячеек {len(cells)} -> {ns} листов zonecrop_*.png')
