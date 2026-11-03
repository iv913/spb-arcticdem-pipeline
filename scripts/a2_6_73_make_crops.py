#!/usr/bin/env python3
"""6.73/A2 — слепые перечтения отметок Атаянс: нарезка контакт-листов.

Вход: results/atayanc_pool_v2/atayanc_marks_pool_v2.csv
  (use = third_read 94 + candidate_second_read 427 = 521 точек)
Лист: upload/atayanc-2013-01.jpg (3000x2121).
Выход: scripts/atayanc_a2_6.73/sheets/sheet_NN.png — сетки 4x6 кропов
  260x180 px (x2 увеличение), ID крупно над каждым кропом.
Никаких высот на листах не печатается (слепота чтения, handoff A2).
"""
import csv
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO = Path('/tmp/my-project/gh-clone')
POOL = REPO / 'results' / 'atayanc_pool_v2' / 'atayanc_marks_pool_v2.csv'
SHEET = Path('/tmp/my-project/upload/atayanc-2013-01.jpg')
OUT = REPO / 'scripts' / 'atayanc_a2_6.73' / 'sheets'
OUT.mkdir(parents=True, exist_ok=True)

HALF_W, HALF_H = 130, 90          # окно 260x180 вокруг точки
SCALE = 2                          # увеличение кропа
COLS, ROWS = 4, 6                  # 24 кропа на лист
LABEL_H = 30

rows = [r for r in csv.DictReader(open(POOL))
        if r['use'] in ('third_read', 'candidate_second_read')]
rows.sort(key=lambda r: (int(r['nw']), r['id']))  # NW-фрагмент + порядок id
print('точек на перечтение:', len(rows))

img = Image.open(SHEET).convert('RGB')
W, H = img.size
cell_w, cell_h = HALF_W * 2, HALF_H * 2
canvas = Image.new('RGB', (COLS * (cell_w + 8) + 8,
                           ROWS * (cell_h + LABEL_H + 8) + 8), 'white')
draw = ImageDraw.Draw(canvas)
try:
    font = ImageFont.truetype(
        '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 22)
except Exception:
    font = ImageFont.load_default()

manifest = []
for idx, r in enumerate(rows):
    px, py = float(r['px']), float(r['py'])
    x0 = max(0, min(W - cell_w, int(px) - HALF_W))
    y0 = max(0, min(H - cell_h, int(py) - HALF_H))
    crop = img.crop((x0, y0, x0 + cell_w, y0 + cell_h)).copy()
    # красный маркер ТОЧНО в точке отметки (снимает неоднозначность,
    # если в окне несколько чисел); не перекрывает число рядом
    mpx, mpy = int(px) - x0, int(py) - y0
    md = ImageDraw.Draw(crop)
    md.ellipse([mpx - 5, mpy - 5, mpx + 5, mpy + 5], outline=(255, 0, 0),
               width=1)
    crop = crop.resize((cell_w * SCALE, cell_h * SCALE), Image.LANCZOS)
    col, row = idx % COLS, (idx // COLS) % ROWS
    page = idx // (COLS * ROWS)
    cx = 8 + col * (cell_w + 8)
    cy = 8 + row * (cell_h + LABEL_H + 8)
    canvas.paste(crop, (cx, cy + LABEL_H))
    draw.rectangle([cx, cy, cx + cell_w, cy + LABEL_H], fill='#222')
    draw.text((cx + 6, cy + 3), f"{r['id']}  ({int(px)},{int(py)})",
              fill='#fff', font=font)
    draw.rectangle([cx, cy, cx + cell_w + 8 - 8, cy + LABEL_H + cell_h],
                   outline='#999', width=2)
    manifest.append(dict(id=r['id'], sheet=page + 1, px=px, py=py,
                         crop_x0=x0, crop_y0=y0))
    if (idx + 1) % (COLS * ROWS) == 0 or idx + 1 == len(rows):
        canvas.save(OUT / f'sheet_{page + 1:02d}.png')
        if (idx + 1) % (COLS * ROWS) == 0 and idx + 1 < len(rows):
            canvas = Image.new('RGB', canvas.size, 'white')
            draw = ImageDraw.Draw(canvas)

with open(REPO / 'scripts' / 'atayanc_a2_6.73' / 'manifest.csv', 'w',
          newline='') as fh:
    w = csv.DictWriter(fh, fieldnames=list(manifest[0].keys()))
    w.writeheader()
    w.writerows(manifest)
n_sheets = (len(rows) + COLS * ROWS - 1) // (COLS * ROWS)
print(f'листов: {n_sheets} -> {OUT}')
print('манифест: scripts/atayanc_a2_6.73/manifest.csv')
