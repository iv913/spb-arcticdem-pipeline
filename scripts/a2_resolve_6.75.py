#!/usr/bin/env python3
"""6.75/A2 — сверка слепых перечтений, пул Атаянс v3.

Правила (консервативные, §A2 handoff):
  third_read (94 конфликта claude vs agent):
    vlm совпал с claude или agent (<=0.005)      -> resolved_2of3 (в землю, w=1.0)
    vlm в 0.10 от claude или agent               -> resolved_2of3_near (w=0.9)
    иначе                                        -> unresolved (вне земли)
  candidate_second_read (427):                    -> второй этап (reads r1/r2),
    здесь не трогается (v3 = v2 по этим строкам).
"""
import csv
import json
import re
from pathlib import Path

import numpy as np

REPO = Path('/tmp/my-project/gh-clone')
POOL = REPO / 'results' / 'atayanc_pool_v2' / 'atayanc_marks_pool_v2.csv'
READS = REPO / 'results' / 'atayanc_a2_6.75' / 'reads2'
OUTD = REPO / 'results' / 'atayanc_pool_v3'
OUTD.mkdir(parents=True, exist_ok=True)

TOL = 0.005
TOL_NEAR = 0.10


def parse_read(pid):
    fp = READS / f'{pid}_r1.json'
    if not fp.exists():
        return None, 'no_file', ''
    txt = json.load(open(fp))['choices'][0]['message']['content'].strip()
    m = re.match(r'\s*([0-9]+[.,][0-9]+|[0-9]+)\s*\|?\s*(clear|pale|cut)?',
                 txt.replace(';', ' '))
    nums = re.findall(r'[0-9]+[.,][0-9]+|[0-9]+', txt.split('|')[0])
    vals = []
    for s in nums:
        try:
            vals.append(float(s.replace(',', '.')))
        except Exception:
            pass
    clarity = txt.split('|')[-1].strip() if '|' in txt else ''
    if not vals:
        return None, 'NA', clarity
    return vals, 'ok', clarity


P = list(csv.DictReader(open(POOL)))
res_rows, log = [], []
for r in P:
    r = dict(r)
    if r['use'] == 'third_read':
        vals, st, cl = parse_read(r['id'])
        claude, agent = float(r['h_claude']), float(r['h_agent'])
        if st != 'ok':
            r['use'], r['status'] = '', 'unresolved_no_read'
            log.append((r['id'], claude, agent, None, st, 'unresolved'))
        else:
            hit = None
            for v in vals:
                if abs(v - claude) <= TOL:
                    hit = ('claude', v, TOL)
                    break
                if abs(v - agent) <= TOL:
                    hit = ('agent', v, TOL)
                    break
            if hit is None:
                for v in vals:
                    if abs(v - claude) <= TOL_NEAR:
                        hit = ('claude', v, TOL_NEAR)
                        break
                    if abs(v - agent) <= TOL_NEAR:
                        hit = ('agent', v, TOL_NEAR)
                        break
            if hit:
                who, v, tol = hit
                truth = claude if who == 'claude' else agent
                r['h_best'] = f'{truth:.2f}'
                r['dH'] = f"{float(r['surface_6_00']) - truth:.2f}"
                if tol == TOL:
                    r['status'] = f'resolved_2of3({who})'
                    r['use'] = 'ground_truth'
                    r['tier'] = 'resolved_2of3'
                else:
                    r['status'] = f'resolved_2of3_near({who})'
                    r['use'] = 'ground_truth_0.1'
                    r['tier'] = 'resolved_2of3_near'
                log.append((r['id'], claude, agent, v, cl, r['status']))
            else:
                r['use'], r['status'] = '', 'unresolved_3diff'
                log.append((r['id'], claude, agent, vals[0], cl,
                            'unresolved'))
        r['h_vlm'] = ('' if st != 'ok' else
                      ';'.join(f'{v:g}' for v in vals))
    else:
        r['h_vlm'] = ''
    res_rows.append(r)

# колонки: v2 + h_vlm + tier
cols = list(P[0].keys()) + ['h_vlm', 'tier']
for r in res_rows:
    r.setdefault('tier', '')
with open(OUTD / 'atayanc_marks_pool_v3.csv', 'w', newline='',
          encoding='utf-8') as fh:
    w = csv.DictWriter(fh, fieldnames=cols)
    w.writeheader()
    w.writerows(res_rows)

# статистика
import collections
st = collections.Counter(r['status'] for r in res_rows
                         if r['status'].startswith(('resolved', 'unresolved')))
print('исходы 94 конфликтов:', dict(st))

# сравнение источников v2/v3 на поверхности (ground-only разрез)
surf = {}
for r in res_rows:
    if r['use'].startswith('ground_truth') and r.get('dH'):
        surf.setdefault('v3_' + r['use'], []).append(float(r['dH']))
for r in P:
    if r['use'].startswith('ground_truth') and r.get('dH'):
        surf.setdefault('v2_' + r['use'], []).append(float(r['dH']))


def stats(x):
    x = np.asarray(x)
    return (len(x), round(float(np.median(x)), 3),
            round(float(np.median(np.abs(x - np.median(x)))), 3))


print('\nразрез dH (поверхность − отметка) на каноне:')
for k in sorted(surf):
    n, med, mad = stats(surf[k])
    print(f'  {k:28s} n={n:4d} median {med:+.2f} MAD {mad:.2f}')

with open(OUTD / 'a2_third_read_log.csv', 'w', newline='',
          encoding='utf-8') as fh:
    w = csv.writer(fh)
    w.writerow(['id', 'h_claude', 'h_agent', 'h_vlm', 'clarity', 'verdict'])
    w.writerows(log)
print(f'\nпул v3: {OUTD / "atayanc_marks_pool_v3.csv"}')
