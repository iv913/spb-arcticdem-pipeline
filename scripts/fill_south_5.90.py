#!/usr/bin/env python3
"""5.90: дозаполнение южной сетки зон до полного покрытия bbox юга.

Сетка 5.90-prep (63 окна 0.10x0.05, шаг 0.05x0.025) минус 12 отобранных;
критерий дозаполнения: в окне есть точки южного дампа (>=200) ИЛИ
старого канона (>=50) — вода/пустые пропускаются. Прогон runner'ом 5.83
(prior-only, S13..), кэш зоны удаляется после успеха (экономия диска).
"""
import json
import os
import shutil
import subprocess
import sys

import numpy as np
import pandas as pd

HOME = '/tmp/my-project/gh-clone'
SB = (30.10, 59.70, 30.62, 59.90)
WIN_W, WIN_H, STEP_X, STEP_Y = 0.10, 0.05, 0.05, 0.025
DUMP_MIN, IC_MIN = 200, 50
PROG = f'{HOME}/results/batch_progress_5.83.jsonl'


def done_bboxes():
    out = []
    if os.path.exists(PROG):
        for line in open(PROG):
            try:
                out.append(tuple(round(float(x), 4)
                                 for x in json.loads(line)['bbox']))
            except Exception:
                pass
    return out


def main():
    dump = pd.read_csv(f'{HOME}/scripts/data/icesat2_atl06_dump_south_5.89.csv.gz')
    ic = pd.read_csv(f'{HOME}/scripts/data/icesat2_clean.csv')
    done = done_bboxes()
    plan = json.load(open(f'{HOME}/results/rollout_plan_south_5.90.json'))
    plan_bb = {tuple(round(float(x), 4) for x in z['bbox'])
               for z in plan['zones']}

    gx = np.arange(SB[0], SB[2] + 1e-9, STEP_X)
    gy = np.arange(SB[1], SB[3] + 1e-9, STEP_Y)
    fill = []
    n_existing = 0
    for jy, s0 in enumerate(gy):
        for ix, w0 in enumerate(gx):
            w, s = round(w0, 4), round(s0, 4)
            e, n = round(w + WIN_W, 4), round(s + WIN_H, 4)
            if e > SB[2] + 1e-9 or n > SB[3] + 1e-9:
                continue
            bb = (w, s, e, n)
            if bb in done or bb in plan_bb:
                n_existing += 1
                continue
            nd = int(((dump.lon >= w) & (dump.lon <= e) &
                      (dump.lat >= s) & (dump.lat <= n)).sum())
            ni = int(((ic.lon >= w) & (ic.lon <= e) &
                      (ic.lat >= s) & (ic.lat <= n)).sum())
            if nd < DUMP_MIN and ni < IC_MIN:
                continue
            tag = f"S{jy * 10 + ix + 13}"     # детерминированный тег окна
            fill.append((tag, bb, nd, ni))
    print(f"существующих зон: {n_existing}; дозаполнение: {len(fill)}")
    for tag, bb, nd, ni in fill:
        b = ' '.join(str(x) for x in bb)
        print(f"  {tag} {b} dump={nd} icesat={ni}")
    json.dump([dict(tag=t, bbox=list(bb), dump=nd, icesat=ni)
               for t, bb, nd, ni in fill],
              open(f'{HOME}/results/rollout_fill_south_5.90.json', 'w'),
              ensure_ascii=False, indent=1)
    if '--run' not in sys.argv:
        print("сухой прогон (--run для исполнения)")
        return
    for tag, bb, nd, ni in fill:
        b = [str(x) for x in bb]
        print(f"--- {tag} ---", flush=True)
        r = subprocess.run(['python3', f'{HOME}/scripts/batch_rollout_5.83.py',
                            'zone', *b, tag], capture_output=True, text=True,
                           timeout=560)
        tail = [l for l in r.stdout.splitlines()
                if 'HOLDOUT' in l or 'done' in l or 'prior 2м' in l]
        print('\n'.join(tail[-3:]), flush=True)
        if r.returncode != 0:
            print(f"FAIL {tag}: {r.stderr[-300:]}", flush=True)
            continue
        cache = f'{HOME}/scripts/dem_cache/strips_{tag}'
        if os.path.exists(cache):
            shutil.rmtree(cache)          # продукт и вердикт уже записаны
    print("ДОЗАПОЛНЕНИЕ ГОТОВО", flush=True)


if __name__ == '__main__':
    main()
