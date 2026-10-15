#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""6.30h — тест OCR (tesseract) на высотах зданий и отметках земли."""
import subprocess, os
from PIL import Image

SRC = '/home/z/my-project/upload/atayanc-2013-01.jpg'
OUTD = '/tmp/my-project/results/atayanc_6.30'
im = Image.open(SRC).convert('L')

def ocr(box, scale, name, psm='11'):
    c = im.crop(box)
    c = c.resize((c.width * scale, c.height * scale), Image.LANCZOS)
    p = f'{OUTD}/{name}.png'
    c.save(p)
    r = subprocess.run(['tesseract', p, 'stdout', '--psm', psm,
                        '-c', 'tessedit_char_whitelist=+-.0123456789'],
                       capture_output=True, text=True)
    print(f'--- {name} box={box} psm={psm}')
    print(' | '.join(t for t in r.stdout.splitlines() if t.strip()))

# зона NEVAHAUS: высоты 28/32/23/16/13
ocr((731, 300, 1461, 700), 3, 'ocr_heights_c1', '11')
# отметки у стрелки (g1 зона)
ocr((1300, 880, 1780, 1260), 3, 'ocr_marks_g1', '11')
# маленький тест: только два числа
ocr((1080, 380, 1280, 500), 4, 'ocr_small', '11')
