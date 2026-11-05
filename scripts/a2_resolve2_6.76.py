#!/usr/bin/env python3
"""6.76/A2 часть 2 — сверка вторых слепых чтений 427 кандидатов, пул v4.

Правило (worklog 6.75, консервативное): у кандидата есть исходное одиночное
чтение o (h_claude для new_single_read, h_agent для agent_only) и два новых
слепых чтения r1/r2 (кропы 220x160 x4, промпты как в 6.75).
  d1 = min|v-o| по значениям r1, d2 = min|v-o| по r2.
  d1<=0.005 и d2<=0.005                 -> ground_truth        (w=1.0)
  (d1<=0.005 и d2<=0.10) или наоборот   -> ground_truth_0.1    (w=0.9)
  иначе -> вне земли:
    оба чтения NA/нет файлов            -> unresolved_2read_no_data
    оба <=0.10 (но не случай выше)      -> unresolved_2read_near
    r1 и r2 совпали между собой (<=0.005), но оба >0.10 от o
                                        -> vlm2_agree_vs_orig (подозрение
                                           на исходное чтение; в землю НЕ идёт)
    прочее                              -> unresolved_2read_diff

Выход: results/atayanc_pool_v4/atayanc_marks_pool_v4.csv
       (все 709 строк; колонки v3 + h_vlm2 + tier2),
       results/atayanc_pool_v4/a2_second_read_log.csv
"""
import csv
import json
import re
from collections import Counter
from pathlib import Path

import numpy as np

REPO = Path('/tmp/my-project/gh-clone')
POOL = REPO / 'results' / 'atayanc_pool_v3' / 'atayanc_marks_pool_v3.csv'
READS = REPO / 'results' / 'atayanc_a2_6.75' / 'reads2'
OUTD = REPO / 'results' / 'atayanc_pool_v4'
OUTD.mkdir(parents=True, exist_ok=True)

TOL = 0.005
TOL_NEAR = 0.10


def parse_read(pid, k):
    fp = READS / f'{pid}_r{k}.json'
    if not fp.exists():
        return None, 'no_file', ''
    try:
        txt = json.load(open(fp))['choices'][0]['message']['content'].strip()
    except Exception:
        return None, 'bad_json', ''
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
    r.setdefault('h_vlm2', '')
    r.setdefault('tier2', '')
    if r['use'] != 'candidate_second_read':
        res_rows.append(r)
        continue
    o_src = 'claude' if r['h_claude'] else 'agent'
    o = float(r['h_claude'] or r['h_agent'])
    v1, s1, c1 = parse_read(r['id'], 1)
    v2, s2, c2 = parse_read(r['id'], 2)
    r['h_vlm2'] = ';'.join(f'{v:g}' for v in (v1 or [])) + ' / ' + \
                  ';'.join(f'{v:g}' for v in (v2 or []))
    d1 = min((abs(v - o) for v in v1), default=float('inf')) if v1 else \
        float('inf')
    d2 = min((abs(v - o) for v in v2), default=float('inf')) if v2 else \
        float('inf')
    e1 = d1 <= TOL
    e2 = d2 <= TOL
    n1 = d1 <= TOL_NEAR
    n2 = d2 <= TOL_NEAR

    if e1 and e2:
        verdict = 'resolved_2read_exact'
        r['use'], r['tier2'] = 'ground_truth', 'resolved2_exact'
    elif (e1 and n2) or (e2 and n1):
        verdict = 'resolved_2read_exact_near'
        r['use'], r['tier2'] = 'ground_truth_0.1', 'resolved2_exact_near'
    elif s1 != 'ok' and s2 != 'ok':
        verdict = 'unresolved_2read_no_data'
        r['use'] = ''
    elif n1 and n2:
        verdict = 'unresolved_2read_near'
        r['use'] = ''
    elif (v1 and v2 and
          min(abs(a - b) for a in v1 for b in v2) <= TOL and
          d1 > TOL_NEAR and d2 > TOL_NEAR):
        verdict = 'vlm2_agree_vs_orig'
        r['use'] = ''
    else:
        verdict = 'unresolved_2read_diff'
        r['use'] = ''

    if r['use'].startswith('ground_truth'):
        r['h_best'] = f'{o:.2f}'
        r['dH'] = f"{float(r['surface_6_00']) - o:.2f}"
        r['status'] = verdict
    elif r['status'] in ('new_single_read', 'agent_only'):
        r['status'] = verdict
    log.append([r['id'], o_src, f'{o:.2f}',
                r['h_vlm2'].replace(' / ', '|'), c1, c2,
                '' if d1 == float('inf') else f'{d1:.3f}',
                '' if d2 == float('inf') else f'{d2:.3f}', verdict])
    res_rows.append(r)

cols = list(P[0].keys()) + ['h_vlm2', 'tier2']
for r in res_rows:
    for c in ('h_vlm2', 'tier2'):
        r.setdefault(c, '')
with open(OUTD / 'atayanc_marks_pool_v4.csv', 'w', newline='',
          encoding='utf-8') as fh:
    w = csv.DictWriter(fh, fieldnames=cols)
    w.writeheader()
    w.writerows(res_rows)

with open(OUTD / 'a2_second_read_log.csv', 'w', newline='',
          encoding='utf-8') as fh:
    w = csv.writer(fh)
    w.writerow(['id', 'orig_src', 'orig_h', 'vlm2', 'clarity1', 'clarity2',
                'd1', 'd2', 'verdict'])
    w.writerows(log)

st = Counter(r['status'] for r in res_rows
             if r['status'].startswith(('resolved_2read', 'unresolved_2read',
                                        'vlm2_agree')))
print('исходы 427 кандидатов:', dict(st))

# статусные разрезы
for grp, key in (('use', 'use'), ('tier2', 'tier2')):
    print(grp, dict(Counter(r[key] for r in res_rows if r[key])))

ground_old = sum(1 for r in P if r['use'].startswith('ground_truth'))
ground_new = sum(1 for r in res_rows if r['use'].startswith('ground_truth'))
print(f'земля: v3 {ground_old} -> v4 {ground_new}')

# dH-разрезы по земле (поверхность − отметка)
def cut(rows, sel, label):
    x = np.array([float(r['dH']) for r in rows
                  if sel(r) and r.get('dH') not in ('', None)])
    if len(x) == 0:
        print(f'  {label}: n=0')
        return
    print(f'  {label}: n={len(x)} median {np.median(x):+.2f} '
          f'MAD {np.median(np.abs(x - np.median(x))):.2f}')

print('\ndH (поверхность − отметка) на каноне 6.00 A2:')
cut(res_rows, lambda r: r['use'].startswith('ground_truth'), 'v4 вся земля')
cut(res_rows, lambda r: r['use'] == 'ground_truth', '  exact (w=1.0)')
cut(res_rows, lambda r: r['use'] == 'ground_truth_0.1', '  exact+near (w=0.9)')
cut(res_rows, lambda r: r['use'].startswith('ground_truth') and
    r['id'].startswith('c'), '  new_single_read (c*)')
cut(res_rows, lambda r: r['use'].startswith('ground_truth') and
    not r['id'].startswith('c'), '  agent_only и confirmed (m*)')
old = [r for r in P if r['tier'] == '']
cut(old, lambda r: r['use'].startswith('ground_truth'),
    'v3 земля (до части 2, та же поверхность)')

# совпадение новых чтений между собой (кросс-проверка движка)
agree = sum(1 for r in res_rows if r['status'] == 'vlm2_agree_vs_orig')
print(f'\nvlm2_agree_vs_orig (оба новых против исходного, в землю не идёт): '
      f'{agree}')
print(f'пул v4: {OUTD / "atayanc_marks_pool_v4.csv"}')
