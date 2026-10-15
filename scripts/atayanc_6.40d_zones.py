#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""6.40d — целевые зоны: большой EDSR-кроп окна + зелёные боксы детектированных
строк с индексами. Позволяет сопоставить прочитанное значение конкретному боксу."""
import json, os
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage
import cv2
from cv2 import dnn_superres

SRC = '/home/z/my-project/upload/atayanc-2013-01.jpg'
OUTD = '/home/z/my-project/download/atayanc_6.40'
MODELS = '/home/z/my-project/models'
PAD = 90

ZONES = [55, 100, 147, 154, 301, 498]

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

def local_strings(wx0, wy0, wx1, wy1):
    sub = gray[wy0:wy1, wx0:wx1]
    dark = sub < 135
    lab, n = ndimage.label(dark)
    sl = ndimage.find_objects(lab)
    boxes = []
    for i, s in enumerate(sl):
        h = s[0].stop - s[0].start
        w = s[1].stop - s[1].start
        if 3 <= h <= 20 and 2 <= w <= 30:
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
    return [s for s in strings if 1 <= s[4] <= 9 and 4 <= (s[2] - s[0]) <= 85 and 4 <= (s[3] - s[1]) <= 20]

try:
    font = ImageFont.load_default(size=15)
except TypeError:
    font = ImageFont.load_default()

result = {}
for zi, cid in enumerate(ZONES):
    x0, y0, x1, y1 = cands[cid]
    wx0, wy0 = max(0, x0 - PAD), max(0, y0 - PAD)
    wx1, wy1 = min(W, x1 + PAD), min(H, y1 + PAD)
    crop = img[wy0:wy1, wx0:wx1]
    up = edsr4(crop)
    strs = local_strings(wx0, wy0, wx1, wy1)
    strs.sort(key=lambda s: (s[1], s[0]))
    result[cid] = [{'box': [int(v) for v in s[:4]]} for s in strs]
    vis = up.copy()
    for j, (sx0, sy0, sx1, sy1, ng) in enumerate(strs):
        p0 = (int((sx0 - wx0) * 4) - 2, int((sy0 - wy0) * 4) - 2)
        p1 = (int((sx1 - wx0) * 4) + 2, int((sy1 - wy0) * 4) + 2)
        cv2.rectangle(vis, p0, p1, (0, 200, 0), 2)
        pil = Image.fromarray(cv2.cvtColor(vis, cv2.COLOR_BGR2RGB))
        d = ImageDraw.Draw(pil)
        d.text((p0[0], max(0, p0[1] - 17)), f'm{j}', fill=(220, 0, 0), font=font)
        vis = cv2.cvtColor(np.asarray(pil), cv2.COLOR_RGB2BGR)
    if vis.shape[1] > 1500:
        vis = cv2.resize(vis, (1500, int(vis.shape[0] * 1500 / vis.shape[1])), interpolation=cv2.INTER_AREA)
    cv2.imwrite(f'{OUTD}/sheets_edsr/zone_{zi}_{cid}.png', vis)
    print(f'зона #{cid}: строк {len(strs)} -> zone_{zi}_{cid}.png')

json.dump(result, open(f'{OUTD}/zones_6.40.json', 'w'))
