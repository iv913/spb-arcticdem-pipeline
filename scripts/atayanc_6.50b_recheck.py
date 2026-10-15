#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""6.50b — адресная перепроверка сомнительных отметок: широкий контекст (120x56
нативных px, EDSR x4) + плотный зум x8. Крупнее и с окружением, чем rs-листы."""
import csv, os
import numpy as np
import cv2
from cv2 import dnn_superres
from PIL import Image, ImageDraw, ImageFont

SRC = '/home/z/my-project/upload/atayanc-2013-01.jpg'
CAT = '/home/z/my-project/download/atayanc_6.40/marks_catalog_6.40.csv'
OUTD = '/home/z/my-project/download/atayanc_6.50/resheets'
os.makedirs(OUTD, exist_ok=True)

FLAG = ['m001','m013','m014','m017','m020','m023','m027','m039','m041','m046',
        'm051','m063','m064','m065','m068','m081','m086','m089','m095','m104',
        'm105','m111','m122','m135','m157','m174','m178','m183','m184','m188',
        'm201','m208','m217','m218','m219','m221','m225','m227','m232','m233',
        'm235','m244','m246','m253','m254','m255','m258','m262','m263','m265',
        'm270','m275','m276','m277','m279','m284','m287','m288','m293','m297',
        'm302','m304','m307','m312','m313','m314','m317','m326','m331','m337',
        'm340','m346','m347','m348','m349','m351']

rows = {r['id']: r for r in csv.DictReader(open(CAT))}
print('флагов:', len(FLAG))

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

def stretch(u8):
    a, b = np.percentile(u8, 2), np.percentile(u8, 98)
    if b <= a:
        return u8
    return np.clip((u8.astype(np.float32) - a) * 255.0 / (b - a), 0, 255).astype(np.uint8)

CW, CH, LBL = 500, 470, 18
COLS, ROWS = 3, 3
try:
    font = ImageFont.load_default(size=15)
except TypeError:
    font = ImageFont.load_default()

def make_cell(r):
    px, py = int(r['px']), int(r['py'])
    x0, y0 = px - 60, py - 28
    cx0, cy0 = max(0, x0), max(0, y0)
    cx1, cy1 = min(W, x0 + 120), min(H, y0 + 56)
    crop = img_bgr[cy0:cy1, cx0:cx1].copy()
    if crop.shape[0] < 56 or crop.shape[1] < 120:
        crop = cv2.copyMakeBorder(crop, cy0 - y0, y0 + 56 - cy1, cx0 - x0,
                                  x0 + 120 - cx1, cv2.BORDER_REPLICATE)
    cell = np.full((CH, CW, 3), 255, np.uint8)
    up = edsr4(crop)  # 480x224
    cell[LBL:LBL + 224, 10:10 + 480] = up
    kx = 10 + (px - x0) * 4
    ky = LBL + (py - y0) * 4
    cv2.drawMarker(cell, (int(kx), int(ky)), (255, 0, 255), cv2.MARKER_CROSS, 13, 1)
    # плотный зум x8 из центра 40x18
    tx0, ty0 = px - 20, py - 9
    tc = img_bgr[max(0, ty0):max(0, ty0) + 18, max(0, tx0):max(0, tx0) + 40].copy()
    if tc.shape[0] < 18 or tc.shape[1] < 40:
        tc = cv2.copyMakeBorder(tc, 0, 18 - tc.shape[0], 0, 40 - tc.shape[1],
                                cv2.BORDER_REPLICATE)
    big = cv2.resize(stretch(tc), (320, 144), interpolation=cv2.INTER_LANCZOS4)
    by = LBL + 234
    cell[by:by + 144, (CW - 320) // 2:(CW - 320) // 2 + 320] = big
    cv2.drawMarker(cell, ((CW - 320) // 2 + (px - tx0) * 8, by + (py - ty0) * 8),
                   (255, 0, 255), cv2.MARKER_CROSS, 13, 1)
    cell[0:2, :] = (120, 120, 120); cell[-2:, :] = (120, 120, 120)
    cell[:, 0:2] = (120, 120, 120); cell[:, -2:] = (120, 120, 120)
    pil = Image.fromarray(cv2.cvtColor(cell, cv2.COLOR_BGR2RGB))
    d = ImageDraw.Draw(pil)
    d.text((5, 1), f"{r['id']} cat={r['h_bs77_m']} {r['conf']} {r['src']}",
           fill=(180, 0, 0), font=font)
    return np.asarray(pil)[:, :, ::-1].copy()

ns = 0
PER = COLS * ROWS
for si in range(0, len(FLAG), PER):
    chunk = FLAG[si:si + PER]
    pth = f'{OUTD}/rc_{ns:02d}.png'
    if os.path.exists(pth):
        print(f'{pth}: skip')
        ns += 1
        continue
    sheet = np.full((ROWS * CH, COLS * CW, 3), 245, np.uint8)
    for j, mid in enumerate(chunk):
        cx, cy = (j % COLS) * CW, (j // COLS) * CH
        sheet[cy:cy + CH, cx:cx + CW] = make_cell(rows[mid])
    Image.fromarray(cv2.cvtColor(sheet, cv2.COLOR_BGR2RGB)).save(pth)
    print(f'{pth}: {chunk[0]}..{chunk[-1]} ({len(chunk)})')
    ns += 1
print('листов rc:', ns)
