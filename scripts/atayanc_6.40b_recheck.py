#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""6.40b — перепроверка неоднозначных кандидатов: большие EDSR-кропы,
контактные листы 3x5 (крупные ячейки)."""
import json, os
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import cv2
from cv2 import dnn_superres

SRC = '/home/z/my-project/upload/atayanc-2013-01.jpg'
OUTD = '/home/z/my-project/download/atayanc_6.40'
MODELS = '/home/z/my-project/models'

RECHECK = [
    7, 8, 40, 45, 55,
    66, 68, 100, 101,
    125, 140, 143, 147, 154, 172, 173, 177,
    194, 195, 232, 238,
    258, 270, 271, 273, 290, 293, 296,
    301, 328, 337, 339, 353,
    388, 389, 391, 396,
    427, 430, 444, 460, 464, 469, 470, 473,
    484, 498, 513,
    542, 550, 567, 595,
]

imc = np.asarray(Image.open(SRC).convert('RGB'))
img = cv2.cvtColor(imc, cv2.COLOR_RGB2BGR)
H, W = img.shape[:2]

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

CW, CH, LBL = 560, 340, 18
COLS, ROWS = 3, 5
cells = []
for k in RECHECK:
    x0, y0, x1, y1 = cands[k]
    mx0 = max(0, x0 - 15); my0 = max(0, y0 - 15)
    mx1 = min(W, x1 + 15); my1 = min(H, y1 + 15)
    crop = img[my0:my1, mx0:mx1]
    up = edsr4(crop)
    if up.shape[1] > CW - 8:
        up = cv2.resize(up, (CW - 8, int(up.shape[0] * (CW - 8) / up.shape[1])), interpolation=cv2.INTER_AREA)
    if up.shape[0] > CH - LBL - 4:
        up = cv2.resize(up, (int(up.shape[1] * (CH - LBL - 4) / up.shape[0]), CH - LBL - 4), interpolation=cv2.INTER_AREA)
    cells.append((k, up))

ns = 0
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
    sim.save(f'{OUTD}/sheets_edsr/recheck_{ns:02d}.png')
    ns += 1
print(f'recheck листов: {ns} (кандидатов {len(cells)})')
