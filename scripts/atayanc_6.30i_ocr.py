#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""6.30i — целевой OCR плана: (1) высотные отметки земли (+3.63) с точкой,
(2) жирные высоты зданий (28/32...). Выход: ocr_marks_6.30.json, ocr_heights_6.30.json"""
import json, re, subprocess, time
import numpy as np
from PIL import Image
from scipy import ndimage

SRC = '/home/z/my-project/upload/atayanc-2013-01.jpg'
OUTD = '/tmp/my-project/results/atayanc_6.30'
im = np.asarray(Image.open(SRC).convert('L'))
H, W = im.shape
dark = im < 135

lab, n = ndimage.label(dark)
sl = ndimage.find_objects(lab)
boxes = []
for i, s in enumerate(sl):
    h = s[0].stop - s[0].start
    w = s[1].stop - s[1].start
    if 5 <= h <= 34 and 2 <= w <= 30:
        boxes.append((i + 1, s[1].start, s[0].start, w, h))
print(f'кандидатов-глифов: {len(boxes)}')

# группировка в строки: близкие по вертикали и горизонтали
boxes.sort(key=lambda b: (b[2] // 6, b[1]))
used = [False] * len(boxes)
strings = []
for i, b in enumerate(boxes):
    if used[i]:
        continue
    grp = [b]
    used[i] = True
    changed = True
    while changed:
        changed = False
        gy0 = min(g[2] for g in grp); gy1 = max(g[2] + g[4] for g in grp)
        gx1 = max(g[1] + g[3] for g in grp)
        for j, c in enumerate(boxes):
            if used[j]:
                continue
            cy0, cy1 = c[2], c[2] + c[4]
            ov = min(gy1, cy1) - max(gy0, cy0)
            if ov > 0.45 * min(gy1 - gy0, cy1 - cy0) and 0 < c[1] - gx1 <= 9:
                grp.append(c); used[j] = True; changed = True
    x0 = min(g[1] for g in grp); y0 = min(g[2] for g in grp)
    x1 = max(g[1] + g[3] for g in grp); y1 = max(g[2] + g[4] for g in grp)
    strings.append((x0, y0, x1, y1, len(grp), max(g[4] for g in grp)))
print(f'строк: {len(strings)}')

marks = [s for s in strings if 8 <= s[5] <= 15 and 12 <= (s[2] - s[0]) <= 60]
bignum = [s for s in strings if 18 <= s[5] <= 32 and 10 <= (s[2] - s[0]) <= 46 and s[4] <= 4]
print(f'кандидатов отметок: {len(marks)}, жирных чисел: {len(bignum)}')

def ocr_str(x0, y0, x1, y1, tag):
    pad = 3
    c = Image.open(SRC).convert('L').crop((x0 - pad, y0 - pad, x1 + pad, y1 + pad))
    c = c.resize((c.width * 6, c.height * 6), Image.LANCZOS)
    arr = np.asarray(c)
    thr = (arr < 150).astype(np.uint8) * 255
    p = f'{OUTD}/tmp_ocr_{tag}.png'
    Image.fromarray(255 - thr).save(p)  # чёрный текст на белом
    r = subprocess.run(['tesseract', p, 'stdout', '--psm', '7',
                        '-c', 'tessedit_char_whitelist=+-.0123456789'],
                       capture_output=True, text=True)
    return r.stdout.strip().replace('\n', ' ')

RE_MARK = re.compile(r'^[+-]?\d{1}[.,]\d{2}$')
RE_BIG = re.compile(r'^\d{2,3}$')

t0 = time.time()
out_marks, out_big = [], []
for k, (x0, y0, x1, y1, ng, gh) in enumerate(marks):
    s = ocr_str(x0, y0, x1, y1, f'm{k}')
    s2 = s.replace(' ', '')
    if RE_MARK.match(s2):
        # точка-пикет: первый глиф (точка) слева
        out_marks.append({'px': (x0, (y0 + y1) // 2), 'text': s2, 'h': gh})
print(f'отметок распознано: {len(out_marks)} за {time.time()-t0:.0f}с')

t0 = time.time()
for k, (x0, y0, x1, y1, ng, gh) in enumerate(bignum):
    s = ocr_str(x0, y0, x1, y1, f'b{k}')
    s2 = s.replace(' ', '')
    if RE_BIG.match(s2):
        out_big.append({'px': ((x0 + x1) // 2, (y0 + y1) // 2), 'text': s2, 'h': gh, 'box': [x0, y0, x1, y1]})
print(f'жирных чисел распознано: {len(out_big)} за {time.time()-t0:.0f}с')

json.dump(out_marks, open(f'{OUTD}/ocr_marks_6.30.json', 'w'), ensure_ascii=False)
json.dump(out_big, open(f'{OUTD}/ocr_heights_6.30.json', 'w'), ensure_ascii=False)
print('пример отметок:', [m['text'] for m in out_marks[:25]])
print('пример чисел:', [m['text'] for m in out_big[:25]])
