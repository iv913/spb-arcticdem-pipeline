#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""6.30p — мини-OCR отметок: кластеризация глифов -> разметка представителей
-> декод строк. Выход: glyph_clusters.png (для разметки), marks_decoded_6.30.json"""
import json
import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

SRC = '/home/z/my-project/upload/atayanc-2013-01.jpg'
OUTD = '/tmp/my-project/results/atayanc_6.30'
im = np.asarray(Image.open(SRC).convert('L'))
dark = im < 135
lab, n = ndimage.label(dark)
sl = ndimage.find_objects(lab)
glyphs = []
for i, s in enumerate(sl):
    h = s[0].stop - s[0].start
    w = s[1].stop - s[1].start
    if 7 <= h <= 15 and 2 <= w <= 22:
        sub = (lab[s] == i + 1)
        if sub.sum() < 8:
            continue
        glyphs.append((s[1].start, s[0].start, w, h, sub))
print(f'глифов (7-15 px): {len(glyphs)}')

# нормализация в 16x24 (сохраняя аспект по центру)
def norm(sub):
    ys, xs = np.where(sub)
    if len(ys) == 0:
        return None
    g = sub[ys.min():ys.max() + 1, xs.min():xs.max() + 1].astype(float)
    hh, ww = g.shape
    sc = min(22 / hh, 14 / ww)
    from scipy.ndimage import zoom as z2
    g2 = z2(g, sc, order=1)
    canvas = np.zeros((24, 16))
    y0 = (24 - g2.shape[0]) // 2; x0 = (16 - g2.shape[1]) // 2
    canvas[y0:y0 + g2.shape[0], x0:x0 + g2.shape[1]] = g2
    return canvas

feats = []
for gx, gy, w, h, sub in glyphs:
    v = norm(sub)
    if v is not None:
        feats.append(v)
F = np.array([f.ravel() for f in feats])
F /= (np.linalg.norm(F, axis=1, keepdims=True) + 1e-9)
print('признаков:', F.shape)

# жадная кластеризация по NCC
reps = [0]
labels = np.full(len(F), -1)
labels[0] = 0
for i in range(1, len(F)):
    sims = F[i] @ F[reps].T
    j = int(np.argmax(sims))
    if sims[j] >= 0.62:
        labels[i] = j
    else:
        reps.append(i)
        labels[i] = len(reps) - 1
print(f'кластеров: {len(reps)}')

# размеры кластеров
import collections
cnt = collections.Counter(labels.tolist())
big = [c for c, k in cnt.items() if k >= 8]
print(f'кластеров >=8: {len(big)}')

# контактный лист представителей (крупно)
cols, cw, ch = 12, 70, 62
rows = (len(big) + cols - 1) // cols
sh = Image.new('L', (cols * cw, rows * ch), 255)
d = ImageDraw.Draw(sh)
big_sorted = sorted(big, key=lambda c: -cnt[c])
for j, c in enumerate(big_sorted):
    idx = int(np.where(labels == c)[0][0])
    g16 = feats[idx]
    img16 = Image.fromarray((255 - g16 * 255 / max(1, g16.max())).astype(np.uint8)).resize((48, 48), Image.NEAREST)
    px, py = (j % cols) * cw, (j // cols) * ch
    sh.paste(img16, (px + 8, py + 4))
    d.text((px + 8, py + 52), f'{c}:{cnt[c]}', fill=0)
sh.save(f'{OUTD}/glyph_clusters.png')
np.savez_compressed(f'{OUTD}/glyphs_6.30.npz', F=F, labels=labels,
                    gx=np.array([g[0] for g in glyphs]), gy=np.array([g[1] for g in glyphs]),
                    gw=np.array([g[2] for g in glyphs]), gh=np.array([g[3] for g in glyphs]),
                    reps=np.array(reps))
print('лист представителей: glyph_clusters.png; кластеры сохранены')
