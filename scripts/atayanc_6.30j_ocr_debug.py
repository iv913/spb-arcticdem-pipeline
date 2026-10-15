#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""6.30j — дебаг OCR: лист кандидатов + сырые ответы."""
import re, subprocess
import numpy as np
from PIL import Image
from scipy import ndimage

SRC = '/home/z/my-project/upload/atayanc-2013-01.jpg'
OUTD = '/tmp/my-project/results/atayanc_6.30'
g = Image.open(SRC).convert('L')
im = np.asarray(g)

# повторяю детект из 6.30i
dark = im < 135
lab, n = ndimage.label(dark)
sl = ndimage.find_objects(lab)
boxes = []
for i, s in enumerate(sl):
    h = s[0].stop - s[0].start
    w = s[1].stop - s[1].start
    if 5 <= h <= 34 and 2 <= w <= 30:
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
            if ov > 0.45 * min(gy1 - gy0, cy1 - cy0) and 0 < c[1] - gx1 <= 9:
                grp.append(c); used[j] = True; changed = True
    x0 = min(x[1] for x in grp); y0 = min(x[2] for x in grp)
    x1 = max(x[1] + x[3] for x in grp); y1 = max(x[2] + x[4] for x in grp)
    strings.append((x0, y0, x1, y1, len(grp), max(x[4] for x in grp)))

marks = [s for s in strings if 8 <= s[5] <= 15 and 12 <= (s[2] - s[0]) <= 60]
bignum = [s for s in strings if 18 <= s[5] <= 32 and 10 <= (s[2] - s[0]) <= 46 and s[4] <= 4]
print('marks', len(marks), 'bignum', len(bignum))

# контактный лист: 60 отметок + 40 чисел
def sheet(items, name, cols=10, cw=90, ch=36):
    rows = (len(items) + cols - 1) // cols
    sh = Image.new('L', (cols * cw, rows * ch), 255)
    from PIL import ImageDraw
    d = ImageDraw.Draw(sh)
    for k, (x0, y0, x1, y1, ng, gh) in enumerate(items[:rows * cols]):
        c = g.crop((x0 - 3, y0 - 3, x1 + 3, y1 + 3))
        c = c.resize((int(c.width * 4), int(c.height * 4)), Image.LANCZOS)
        px, py = (k % cols) * cw, (k // cols) * ch
        sh.paste(c, (px + 2, py + 2))
        d.text((px + 2, py + ch - 12), f'#{k}', fill=180)
    sh.save(f'{OUTD}/{name}')
    print(name, sh.size)

sheet(marks[:80], 'sheet_marks.png')
sheet(bignum[:60], 'sheet_bignum.png')

# сырые ответы tesseract на первых 30 чисел (2 варианта препроцессинга)
for k, (x0, y0, x1, y1, ng, gh) in enumerate(bignum[:30]):
    c = g.crop((x0 - 3, y0 - 3, x1 + 3, y1 + 3)).resize(((x1 - x0 + 6) * 6, (y1 - y0 + 6) * 6), Image.LANCZOS)
    p1 = f'{OUTD}/d_raw.png'; c.save(p1)
    a = np.asarray(c); b = np.where(a < 150, 0, 255).astype(np.uint8)
    p2 = f'{OUTD}/d_bin.png'; Image.fromarray(b).save(p2)
    r1 = subprocess.run(['tesseract', p1, 'stdout', '--psm', '7'], capture_output=True, text=True).stdout.strip()
    r2 = subprocess.run(['tesseract', p2, 'stdout', '--psm', '7'], capture_output=True, text=True).stdout.strip()
    r3 = subprocess.run(['tesseract', p2, 'stdout', '--psm', '8'], capture_output=True, text=True).stdout.strip()
    print(f'#{k} box=({x0},{y0},{x1},{y1}) raw={r1!r} bin7={r2!r} bin8={r3!r}')
