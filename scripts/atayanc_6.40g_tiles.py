#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""6.40g — достройка LapSRN x4 тайлов 5x5 (докрывает недостающие)."""
import os
import cv2
from cv2 import dnn_superres

SRC = '/home/z/my-project/upload/atayanc-2013-01.jpg'
OUTD = '/home/z/my-project/download/atayanc_6.40/tiles_lapsrn'
MODELS = '/home/z/my-project/models'

img = cv2.imread(SRC)
H, W = img.shape[:2]
sr = dnn_superres.DnnSuperResImpl_create()
sr.readModel(f'{MODELS}/LapSRN_x4.pb')
sr.setModel('lapsrn', 4)

GX, GY, OVL = 5, 5, 24
tw, th = W // GX, H // GY
for gy in range(GY):
    for gx in range(GX):
        x0 = max(0, gx * tw - OVL); y0 = max(0, gy * th - OVL)
        x1 = min(W, (gx + 1) * tw + OVL); y1 = min(H, (gy + 1) * th + OVL)
        p = f'{OUTD}/tile_r{gy}c{gx}_x{y0}y{x0}.png'
        if os.path.exists(p):
            continue
        up = sr.upsample(img[y0:y1, x0:x1])
        cv2.imwrite(p, up)
        print('готов:', f'r{gy}c{gx}')
print('всего тайлов:', len(os.listdir(OUTD)))
