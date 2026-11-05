#!/usr/bin/env python3
"""6.76/A2 часть 2 — шардированный запуск слепых вторых чтений.

То же, что a2_blind_read_6.75.py в режиме second, но задания делятся
между NSHARDS процессов по (hash(id)+k) % NSHARDS == SHARD — файлы
не пересекаются, параллельный запуск безопасен. Резюмабельно.
Запуск: python3 a2_read_shard_6.76.py SHARD NSHARDS [max_seconds]
"""
import csv
import json
import subprocess
import sys
import time
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageDraw

REPO = Path('/tmp/my-project/gh-clone')
POOL = REPO / 'results' / 'atayanc_pool_v2' / 'atayanc_marks_pool_v2.csv'
SHEET = Path('/tmp/my-project/upload/atayanc-2013-01.jpg')
OUTD = REPO / 'results' / 'atayanc_a2_6.75' / 'reads2'
CROPS = REPO / 'results' / 'atayanc_a2_6.75' / 'crops2'
OUTD.mkdir(parents=True, exist_ok=True)
CROPS.mkdir(parents=True, exist_ok=True)

HALF_W, HALF_H = 110, 80
SCALE = 4
RING_R = 3
MAX_WORKERS = 2

PROMPT_R1 = ("Fragment of an old scanned topographic plan; small pale "
             "decimal numbers (like 2,36 or 1,98) are ground elevations. "
             "A thin RED RING marks the point; the ring may slightly overlap "
             "the digits of its nearest number. Find the elevation number "
             "printed CLOSEST to the ring centre. Read it twice internally "
             "and verify every digit (the FIRST digit is often faint — "
             "check it especially: 2 vs 3, 3 vs 1, 4 vs 2; also 6 vs 8) "
             "before answering. Transcribe it digit by digit, keeping its "
             "decimal separator (comma or dot). Do NOT round. If two numbers "
             "are equally close, output both separated by ';'. If unreadable, "
             "output NA. Then '|' and one word: clear, pale, cut. "
             "Output only: NUMBER|clarity")

PROMPT_R2 = ("Old topographic plan scan. Tiny decimal elevation labels "
             "(e.g. 2,36; 1,98; 3,22) are printed across the map. The thin "
             "red ring marks a survey point. Which printed decimal number is "
             "nearest to the ring? It may touch or slightly intersect the "
             "ring. The first digit can be very faint - inspect it twice "
             "before answering. Transcribe exactly (keep comma/dot), no "
             "rounding. Two equally near numbers -> both, ';'-separated. "
             "Nothing readable -> NA. End with '|' plus one word: clear, "
             "pale or cut. Output only: NUMBER|clarity")


def make_crop(pid, px, py):
    fp = CROPS / f'{pid}.png'
    if fp.exists():
        return fp
    x0 = max(0, min(W - 2 * HALF_W, int(px) - HALF_W))
    y0 = max(0, min(H - 2 * HALF_H, int(px) - HALF_H))
    crop = img.crop((x0, y0, x0 + 2 * HALF_W, y0 + 2 * HALF_H)).copy()
    mpx, mpy = int(px) - x0, int(py) - y0
    d = ImageDraw.Draw(crop)
    d.ellipse([mpx - RING_R, mpy - RING_R, mpx + RING_R, mpy + RING_R],
              outline=(255, 0, 0), width=1)
    crop = crop.resize((2 * HALF_W * SCALE, 2 * HALF_H * SCALE), Image.LANCZOS)
    crop.save(fp)
    return fp


def read_one(job):
    pid, fp, out, prompt = job
    if out.exists():
        try:
            json.load(open(out))
            return pid, 'cached'
        except Exception:
            out.unlink()
    for attempt in range(7):
        r = subprocess.run(['z-ai', 'vision', '-p', prompt, '-i', str(fp),
                            '-o', str(out)], capture_output=True, text=True,
                           timeout=180)
        if out.exists():
            try:
                json.load(open(out))
                return pid, 'ok'
            except Exception:
                out.unlink()
        time.sleep(12 + attempt * 10)   # 429: терпеливые паузы
    return pid, 'ERR'


img = Image.open(SHEET).convert('RGB')
W, H = img.size
SHARD = int(sys.argv[1])
NSHARDS = int(sys.argv[2])
n_read = 2

rows = [r for r in csv.DictReader(open(POOL))
        if r['use'] == 'candidate_second_read']
jobs = []
for r in rows:
    for k in (1, 2):
        if zlib.crc32(f"{r['id']}#{k}".encode()) % NSHARDS == SHARD:
            fp = make_crop(r['id'], float(r['px']), float(r['py']))
            jobs.append((r['id'], fp,
                         OUTD / f'{r["id"]}_r{k}.json',
                         PROMPT_R1 if k == 1 else PROMPT_R2))

max_seconds = float(sys.argv[3]) if len(sys.argv) > 3 else 1e9
t0 = time.time()
todo = [j for j in jobs if not j[2].exists()]
print(f'shard {SHARD}/{NSHARDS}: заданий {len(jobs)}, к чтению {len(todo)}',
      flush=True)
n_ok = n_err = 0
with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
    futs = {ex.submit(read_one, j): j for j in todo}
    for fut, j in futs.items():
        if time.time() - t0 > max_seconds:
            ex.shutdown(wait=False, cancel_futures=True)
            break
        try:
            pid, st = fut.result()
        except Exception:
            pid, st = j[0], 'EXC'
        n_ok += st in ('ok', 'cached')
        n_err += st not in ('ok', 'cached')
done_after = sum(1 for j in jobs if j[2].exists())
print(f'shard {SHARD}: успешно {n_ok}, ошибок {n_err}, '
      f'готово {done_after}/{len(jobs)}')
print('DONE' if done_after == len(jobs) else 'PARTIAL')
