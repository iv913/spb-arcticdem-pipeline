#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""6.30o — отметки земли: фильтр кандидатов (светлое окружение), батч-OCR,
контактные листы для визуальной верификации."""
import json, re, subprocess
import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

SRC = '/home/z/my-project/upload/atayanc-2013-01.jpg'
OUTD = '/tmp/my-project/results/atayanc_6.30'
g = Image.open(SRC).convert('L')
im = np.asarray(g)

# светлый фон проверяем по кольцу вокруг СТРОКИ (после группировки)
dark = im < 135
lab, n = ndimage.label(dark)
sl = ndimage.find_objects(lab)
boxes = []
for i, s in enumerate(sl):
    h = s[0].stop - s[0].start
    w = s[1].stop - s[1].start
    if 5 <= h <= 16 and 2 <= w <= 26:
        boxes.append((i + 1, s[1].start, s[0].start, w, h))
print(f'глифов-кандидатов: {len(boxes)}')

# группировка в строки (как в 6.30i)
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

cand = [s for s in strings if 8 <= s[5] <= 15 and 10 <= (s[2] - s[0]) <= 55 and 2 <= s[4] <= 7]
# фильтр: светлое кольцо вокруг строки (фон улицы/двора, не внутри застройки)
def ring_light(x0, y0, x1, y1):
    pad = 6
    X0, Y0 = max(0, x0 - pad), max(0, y0 - pad)
    X1, Y1 = min(im.shape[1], x1 + pad), min(im.shape[0], y1 + pad)
    sub = np.ones((Y1 - Y0, X1 - X0), bool)
    sub[y0 - Y0:y1 - Y0, x0 - X0:x1 - X0] = False
    vals = im[Y0:Y1, X0:X1][sub]
    return np.mean(vals) > 150
cand = [s for s in cand if ring_light(*s[:4])]
print(f'строк-кандидатов отметок (светлое кольцо): {len(cand)}')

RE = re.compile(r'^[+-]?\d[.,]\d{2}$')
kept = []
for k, (x0, y0, x1, y1, ng, gh) in enumerate(cand):
    c = g.crop((x0 - 3, y0 - 3, x1 + 3, y1 + 3))
    c = c.resize((c.width * 8, c.height * 8), Image.LANCZOS)
    a = np.asarray(c)
    b = np.where(a < 155, 0, 255).astype(np.uint8)
    b = np.asarray(Image.fromarray(b).filter(__import__('PIL.ImageFilter', fromlist=['MinFilter']).MinFilter(3)))
    p = f'{OUTD}/tmp_mark.png'
    Image.fromarray(b).save(p)
    best = ''
    for psm in ('7', '8', '13'):
        r = subprocess.run(['tesseract', p, 'stdout', '--psm', psm,
                            '-c', 'tessedit_char_whitelist=+-.0123456789'],
                           capture_output=True, text=True).stdout.strip().replace(' ', '')
        if RE.match(r):
            best = r
            break
        if not best and r:
            best = ''
    if best:
        kept.append({'px': [int((x0 + x1) / 2), int((y0 + y1) / 2)], 'box': [x0, y0, x1, y1], 'text': best})
print(f'OCR распознал: {len(kept)} из {len(cand)}')

json.dump(kept, open(f'{OUTD}/marks_ocr_6.30.json', 'w'), ensure_ascii=False)
vals = [m['text'] for m in kept]
print('значения:', vals[:60])

# контактные листы ВСЕХ кандидатов для верификации/дочитки
per = 48; cols = 8; cw, ch = 78, 34
for si in range(0, len(cand), per):
    chunk = cand[si:si + per]
    rows = (len(chunk) + cols - 1) // cols
    sh = Image.new('L', (cols * cw, rows * ch), 255)
    d = ImageDraw.Draw(sh)
    for j, (x0, y0, x1, y1, ng, gh) in enumerate(chunk):
        c = g.crop((x0 - 3, y0 - 3, x1 + 3, y1 + 3))
        c = c.resize((int(c.width * 3.5), int(c.height * 3.5)), Image.LANCZOS)
        px, py = (j % cols) * cw, (j // cols) * ch
        sh.paste(c, (px + 2, py + 2))
        lab = f'#{si + j}'
        oc = any(m['box'] == [x0, y0, x1, y1] for m in kept)
        d.text((px + 2, py + ch - 11), lab + ('*' if oc else ''), fill=160 if not oc else 0)
    sh.save(f'{OUTD}/mark_sheet_{si // per}.png')
print('листов:', (len(cand) + per - 1) // per)
