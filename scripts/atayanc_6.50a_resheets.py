#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""6.50a — листы сплошной перепроверки всех отметок каталога 6.40.
Каждая ячейка: метка (id + значение каталога), сверху EDSR x4, снизу LANCZOS x5
с контраст-стретчем; крест = координата (px,py) из каталога."""
import csv, os
import numpy as np
import cv2
from cv2 import dnn_superres
from PIL import Image, ImageDraw, ImageFont

SRC = '/home/z/my-project/upload/atayanc-2013-01.jpg'
CAT = '/home/z/my-project/download/atayanc_6.40/marks_catalog_6.40.csv'
OUTD = '/home/z/my-project/download/atayanc_6.50/resheets'
os.makedirs(OUTD, exist_ok=True)

rows = list(csv.DictReader(open(CAT)))
print('каталог:', len(rows))

img = np.asarray(Image.open(SRC).convert('RGB'))
H, W = img.shape[:2]
img_bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)

sr = dnn_superres.DnnSuperResImpl_create()
sr.readModel('/home/z/my-project/models/EDSR_x4.pb')
sr.setModel('edsr', 4)

def edsr4(crop):
    h, w = crop.shape[:2]
    ph, pw = (4 - h % 4) % 4, (4 - w % 4) % 4
    if ph or pw:
        crop = cv2.copyMakeBorder(crop, 0, ph, 0, pw, cv2.BORDER_REPLICATE)
    up = sr.upsample(crop)
    return up[:h * 4, :w * 4]

def crop_at(px, py):
    x0, x1 = px - 32, px + 34
    y0, y1 = py - 16, py + 17
    cx0, cy0 = max(0, x0), max(0, y0)
    cx1, cy1 = min(W, x1), min(H, y1)
    c = img_bgr[cy0:cy1, cx0:cx1].copy()
    if c.shape[0] < 33 or c.shape[1] < 66:
        c = cv2.copyMakeBorder(c, cy0 - y0, y1 - cy1, cx0 - x0, x1 - cx1,
                               cv2.BORDER_REPLICATE)
    return c, (px - x0, py - y0)  # crop 66x33, позиция креста

def stretch(u8):
    a, b = np.percentile(u8, 2), np.percentile(u8, 98)
    if b <= a:
        return u8
    return np.clip((u8.astype(np.float32) - a) * 255.0 / (b - a), 0, 255).astype(np.uint8)

CW, CH, LBL = 300, 292, 16
COLS, ROWS = 5, 4
PER = COLS * ROWS
try:
    font = ImageFont.load_default(size=13)
    font2 = ImageFont.load_default(size=12)
except TypeError:
    font = font2 = ImageFont.load_default()

def make_cell(r, idx):
    px, py = int(r['px']), int(r['py'])
    val = r['h_bs77_m']
    label = f"{r['id']} cat={val} {r['conf']}"
    crop, (kx, ky) = crop_at(px, py)
    cell = np.full((CH, CW, 3), 255, np.uint8)
    # Row A: EDSR x4
    up = edsr4(crop)  # 264x132
    uh, uw = up.shape[:2]
    scale = min(288 / uw, 150 / uh)
    up2 = cv2.resize(up, (int(uw * scale), int(uh * scale)), interpolation=cv2.INTER_AREA)
    aw, ah = up2.shape[1], up2.shape[0]
    ax, ay = (CW - aw) // 2, LBL
    cell[ay:ay + ah, ax:ax + aw] = up2
    cax = ax + int(kx * scale)
    cay = ay + int(ky * scale)
    cv2.drawMarker(cell, (cax, cay), (255, 0, 255), cv2.MARKER_CROSS, 9, 1)
    # Row B: LANCZOS x5 + stretch
    big = cv2.resize(stretch(crop), None, fx=5, fy=5, interpolation=cv2.INTER_LANCZOS4)
    bh, bw = big.shape[:2]
    scale2 = min(288 / bw, 108 / bh)
    big2 = cv2.resize(big, (int(bw * scale2), int(bh * scale2)), interpolation=cv2.INTER_AREA)
    bw2, bh2 = big2.shape[1], big2.shape[0]
    bx, by = (CW - bw2) // 2, LBL + 152
    cell[by:by + bh2, bx:bx + bw2] = big2
    cbx = bx + int(kx * 5 * scale2)
    cby = by + int(ky * 5 * scale2)
    cv2.drawMarker(cell, (cbx, cby), (255, 0, 255), cv2.MARKER_CROSS, 9, 1)
    # рамка + метка
    cell[0:2, :] = (120, 120, 120); cell[-2:, :] = (120, 120, 120)
    cell[:, 0:2] = (120, 120, 120); cell[:, -2:] = (120, 120, 120)
    pil = Image.fromarray(cv2.cvtColor(cell, cv2.COLOR_BGR2RGB))
    d = ImageDraw.Draw(pil)
    d.text((4, 1), label, fill=(180, 0, 0), font=font)
    return np.asarray(pil)[:, :, ::-1].copy()

ns = 0
for si in range(0, len(rows), PER):
    chunk = rows[si:si + PER]
    pth = f'{OUTD}/rs_{ns:02d}.png'
    if os.path.exists(pth):
        print(f'{pth}: skip (exists)')
        ns += 1
        continue
    sheet = np.full((ROWS * CH, COLS * CW, 3), 245, np.uint8)
    for j, r in enumerate(chunk):
        cx, cy = (j % COLS) * CW, (j // COLS) * CH
        sheet[cy:cy + CH, cx:cx + CW] = make_cell(r, j)
    pth = f'{OUTD}/rs_{ns:02d}.png'
    Image.fromarray(cv2.cvtColor(sheet, cv2.COLOR_BGR2RGB)).save(pth)
    first, last = chunk[0]['id'], chunk[-1]['id']
    print(f'{pth}: {first}..{last} ({len(chunk)})')
    ns += 1
print('листов:', ns)
