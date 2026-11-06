#!/usr/bin/env python3
"""6.76/A2 часть 2 — приёмка 427 кандидатов по двум слепым перечтениям.

Источник: results/atayanc_pool_v3/atayanc_marks_pool_v3.csv
(часть 1 — 94 конфликта — уже влита в v3; часть 2 здесь).

Правило приёмки (worklog, директива пользователя):
  оба новых слепых чтения r1, r2 «за исходное»:
    - точное совпадение с h_claude  (|v - h|     <= 0.005), ИЛИ
    - совпадение с h_claude + 0.10  (|v - (h+0.10)| <= 0.005).
  Каждое из двух чтений проверяется независимо; смешанный случай
  (одно точное, другое +0.10) принимается в консервативный тир w=0.9.
  Совпадение с h-0.10 приёмки НЕ даёт (правило допускает только +0.10).

Исходы по кандидату:
  confirmed_exact   оба чтения точные        -> ground_truth     (w=1.0)
  confirmed_+0.10   есть хотя бы одно +0.10  -> ground_truth_0.1 (w=0.9)
  rejected_2reads   оба читаемы, но мимо     -> вне земли (use='')
  not_confirmed_*   r1/r2 нет/нечитаемо      -> вне земли (use='')
"""
import csv
import collections
import json
import re
from pathlib import Path

import numpy as np

REPO = Path('/tmp/my-project/gh-clone')
POOL = REPO / 'results' / 'atayanc_pool_v3' / 'atayanc_marks_pool_v3.csv'
READS = REPO / 'results' / 'atayanc_a2_6.75' / 'reads2'
OUTD = REPO / 'results' / 'atayanc_pool_v10'
OUTD.mkdir(parents=True, exist_ok=True)

TOL = 0.005
OFF = 0.10


def parse_read(pid, k):
    """-> (vals|None, status, clarity); status: ok/no_file/bad_json/NA."""
    fp = READS / f'{pid}_r{k}.json'
    if not fp.exists():
        return None, 'no_file', ''
    try:
        txt = json.load(open(fp))['choices'][0]['message']['content'].strip()
    except Exception:
        return None, 'bad_json', ''
    head = txt.split('|')[0]
    vals = []
    for s in re.findall(r'[0-9]+[.,][0-9]+|[0-9]+', head):
        try:
            vals.append(float(s.replace(',', '.')))
        except Exception:
            pass
    clarity = txt.split('|')[-1].strip() if '|' in txt else ''
    if not vals:
        return None, 'NA', clarity
    return vals, 'ok', clarity


def hit(vals, target):
    return any(abs(v - target) <= TOL for v in vals)


P = list(csv.DictReader(open(POOL)))
res_rows, log = [], []
cnt = collections.Counter()

for r in P:
    r = dict(r)
    if r['use'] != 'candidate_second_read':
        res_rows.append(r)
        continue

    pid = r['id']
    h = float(r['h_best'])  # исходное: h_claude (new_single_read) или h_agent (agent_only)
    v1, s1, c1 = parse_read(pid, 1)
    v2, s2, c2 = parse_read(pid, 2)
    r['h_vlm'] = ';'.join(
        (('|'.join(f'{v:g}' for v in vv) + f'|{c}') if vv else f'NA|{s1}')
        if k == 1 else
        (('|'.join(f'{v:g}' for v in vv) + f'|{c}') if vv else f'NA|{s2}')
        for k, vv, c, s1, s2 in ((1, v1, c1, s1, s2), (2, v2, c2, s1, s2)))

    ok1 = s1 == 'ok' and (hit(v1, h) or hit(v1, h + OFF))
    ok2 = s2 == 'ok' and (hit(v2, h) or hit(v2, h + OFF))

    if ok1 and ok2:
        if hit(v1, h) and hit(v2, h):
            tier = 'confirmed_exact'
            r['use'] = 'ground_truth'
        else:
            tier = 'confirmed_+0.10'
            r['use'] = 'ground_truth_0.1'
        r['tier'] = tier
        r['h_best'] = f'{h:.2f}'
        r['dH'] = f"{float(r['surface_6_00']) - h:.2f}"
        cnt[tier] += 1
        log.append((pid, f'{h:.2f}', r['h_vlm'], tier))
    elif s1 != 'ok' or s2 != 'ok':
        r['use'], r['tier'] = '', ''
        r['status'] = f'not_confirmed_{s1 if s1 != "ok" else s2}'
        cnt[r['status']] += 1
        log.append((pid, f'{h:.2f}', r['h_vlm'], r['status']))
    else:
        r['use'], r['tier'] = '', ''
        r['status'] = 'rejected_2reads'
        cnt[r['status']] += 1
        log.append((pid, f'{h:.2f}', r['h_vlm'], 'rejected_2reads'))
    res_rows.append(r)

# колонки: v3 + tier (h_vlm у не-кандидатов остаётся пустым)
cols = list(P[0].keys())
if 'tier' not in cols:
    cols.append('tier')
for r in res_rows:
    r.setdefault('tier', '')
with open(OUTD / 'atayanc_marks_pool_v10.csv', 'w', newline='',
          encoding='utf-8') as fh:
    w = csv.DictWriter(fh, fieldnames=cols)
    w.writeheader()
    w.writerows(res_rows)

print('исходы 427 кандидатов:', dict(cnt))

# размер земля-пула v3 -> v10
for name, rows in (('v3', P), ('v10', res_rows)):
    n = sum(1 for r in rows if r['use'].startswith('ground_truth'))
    print(f'земля-пул {name}: {n}')

# разрез dH (поверхность − отметка) на каноне
surf = collections.defaultdict(list)


def stats(x):
    x = np.asarray(x)
    return (len(x), round(float(np.median(x)), 3),
            round(float(np.median(np.abs(x - np.median(x)))), 3))


for r in res_rows:
    if r['use'].startswith('ground_truth') and r.get('dH'):
        key = ('new_' + r['tier']) if r['tier'].startswith('confirmed') \
            else 'prior'
        surf[key].append(float(r['dH']))
print('\nразрез dH (поверхность − отметка), пул v10:')
for k in sorted(surf):
    n, med, mad = stats(surf[k])
    print(f'  {k:20s} n={n:4d} median {med:+.2f} MAD {mad:.2f}')
allv = [v for vs in surf.values() for v in vs]
n, med, mad = stats(allv)
print(f'  {"ALL":20s} n={n:4d} median {med:+.2f} MAD {mad:.2f}')

with open(OUTD / 'a2_second_read_log.csv', 'w', newline='',
          encoding='utf-8') as fh:
    w = csv.writer(fh)
    w.writerow(['id', 'h_orig', 'h_vlm(r1;r2)', 'verdict'])
    w.writerows(log)
print(f'\nпул v10: {OUTD / "atayanc_marks_pool_v10.csv"}')
