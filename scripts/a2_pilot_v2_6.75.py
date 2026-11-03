#!/usr/bin/env python3
"""6.75/A2 — пилот качества слепого чтения v2 (x4, кольцо r=3).

Мера: доля точных совпадений VLM с известной истиной на confirmed-точках
(два прежних чтения совпали — истина надёжна). Порог годности 0.75.
"""
import csv
import json
import random
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageDraw

REPO = Path('/tmp/my-project/gh-clone')
POOL = REPO / 'results' / 'atayanc_pool_v2' / 'atayanc_marks_pool_v2.csv'
SHEET = Path('/tmp/my-project/upload/atayanc-2013-01.jpg')
OUTD = REPO / 'results' / 'atayanc_a2_6.75' / 'pilot_v2'
OUTD.mkdir(parents=True, exist_ok=True)

HALF_W, HALF_H = 110, 80      # окно 220x160 исходника
SCALE = 4                     # -> 880x640
RING_R = 3

PROMPT = ("Fragment of an old scanned topographic plan (pale small decimal "
          "numbers like 2,36 or 1,98 are ground elevations). A thin RED RING "
          "marks the point; the ring may slightly overlap the digits of its "
          "nearest number. Transcribe the elevation number printed CLOSEST "
          "to the ring centre, digit by digit, keeping its decimal separator "
          "(comma or dot). Digits are small, pale and may be partially "
          "covered by the ring — read carefully (e.g. distinguish 2 vs 3, "
          "6 vs 8). Do NOT round. If two numbers are equally close, output "
          "both separated by ';'. If unreadable, output NA. "
          "Then '|' and one word: clear, pale, cut. Output only: NUMBER|clarity")


def make_crop(pid, px, py):
    fp = OUTD / f'{pid}.png'
    if fp.exists():
        return fp
    x0 = max(0, min(W - 2 * HALF_W, int(px) - HALF_W))
    y0 = max(0, min(H - 2 * HALF_H, int(py) - HALF_H))
    crop = img.crop((x0, y0, x0 + 2 * HALF_W, y0 + 2 * HALF_H)).copy()
    mpx, mpy = int(px) - x0, int(py) - y0
    d = ImageDraw.Draw(crop)
    d.ellipse([mpx - RING_R, mpy - RING_R, mpx + RING_R, mpy + RING_R],
              outline=(255, 0, 0), width=1)
    crop = crop.resize((2 * HALF_W * SCALE, 2 * HALF_H * SCALE), Image.LANCZOS)
    crop.save(fp)
    return fp


def read_one(job):
    pid, fp = job
    out = OUTD / f'{pid}.json'
    if out.exists():
        try:
            return pid, json.load(open(out))['choices'][0]['message']['content']
        except Exception:
            pass
    for attempt in range(5):
        subprocess.run(['z-ai', 'vision', '-p', PROMPT, '-i', str(fp),
                        '-o', str(out)], capture_output=True, text=True,
                       timeout=180)
        if out.exists():
            try:
                return pid, json.load(open(out))['choices'][0]['message']['content']
            except Exception:
                out.unlink()
        if '429' in (None or ''):
            time.sleep(3)
        time.sleep(2)
    return pid, 'ERR'


img = Image.open(SHEET).convert('RGB')
W, H = img.size
P = list(csv.DictReader(open(POOL)))
conf = [r for r in P if r['status'] == 'confirmed']
test_ids = sys.argv[1].split(',') if len(sys.argv) > 1 else []
rng = random.Random(777)
sample = rng.sample(conf, 15) + [r for r in P if r['id'] in test_ids]
jobs = [(r['id'], make_crop(r['id'], float(r['px']), float(r['py'])))
        for r in sample]
with ThreadPoolExecutor(max_workers=4) as ex:
    res = dict(ex.map(read_one, jobs))


def norm(txt):
    if '|' not in txt:
        return None, '?'
    num = txt.split('|')[0].strip().replace(',', '.')
    try:
        return float(num), txt.split('|')[1].strip()
    except Exception:
        return None, '?'


ok = tot = 0
for r in sample:
    pid = r['id']
    truth = float(r['h_claude'])
    v, cl = norm(res.get(pid, 'ERR'))
    mark = ''
    if r['status'] == 'confirmed':
        tot += 1
        hit = (v is not None and abs(v - truth) <= 0.005)
        ok += hit
        mark = 'OK' if hit else 'MISS'
    print(f"{pid} truth={truth:.2f} vlm={v} ({cl}) {mark}")
print(f"\nconfirmed-точки: {ok}/{tot} = {ok/tot:.0%}" if tot else 'нет')
