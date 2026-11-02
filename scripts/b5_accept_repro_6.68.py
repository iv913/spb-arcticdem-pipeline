#!/usr/bin/env python3
"""Приёмка B5 (handoff 2026-11 §3-B5): accept.py воспроизводит числа
аудита 30.10 (NOTES_fusion_audit_6.53 §3) на релизе v1.1.

Числа для воспроизведения (fusion 6.00 A2, модель − отметка):

  паспорт в измеренной области (v4-эпохи, 202 марки, в 15 м от якорей,
  т.е. в измеренной области поправка не трогает поверхность):
      rest  (conf 11): n=73,  median −0.09, |d|>1м 48%
      gold  (conf 13): n=59,  median +1.06, |d|>1м 59%
  план Атаянс 2013 (В.О.):
      rest: n=107, +2.14, 75%   gold: n=45, +1.65, 96%
  archi 514913 (Купчино):
      rest: n=135, −0.28, 2%    gold: n=40, −0.81, 28%

Запуск: python3 scripts/b5_accept_repro_6.68.py
Выход:  results/validation/accept_repro_v1.1_6.68.json (+ отчёт accept.py)
"""

import csv
import json
import sys
from pathlib import Path

import numpy as np
import rasterio

GH = Path('/tmp/my-project/gh-clone')
sys.path.insert(0, str(GH / 'scripts'))
sys.path.insert(0, str(GH / 'scripts' / 'validation'))
from accept import run_accept  # noqa: E402

FUS = '/tmp/my-project/download/mosaic_6.00/spb_terra_fusion_6.00_A2.tif'
CONF = '/tmp/my-project/download/mosaic_6.00/spb_terra_conf_6.00_A2.tif'
ANCHORS = GH / 'results' / 'marks_val_5.95.csv'
OUT_DIR = GH / 'results' / 'validation'
OUT_DIR.mkdir(parents=True, exist_ok=True)
POOL = OUT_DIR / 'pool_audit_repro_6.68.csv'
REPORT = OUT_DIR / 'accept_repro_v1.1_6.68.json'

PASSPORT_CANDIDATES = [
    GH / 'scripts' / 'data' / 'spb_passport_validation_v7.csv',   # 202 марки
    GH / 'scripts' / 'data' / 'spb_passport_validation_v11.csv',
    GH / 'scripts' / 'data' / 'spb_passport_validation_v8.csv',
]


def build_pool() -> Path:
    rows = []
    # паспорт: файл эпохи аудита (202 марки, колонка hp)
    used = None
    for f in PASSPORT_CANDIDATES:
        if f.exists():
            used = f
            break
    P = list(csv.DictReader(open(used)))
    print(f"паспорт: {used.name}, {len(P)} марок")
    for i, r in enumerate(P):
        if not r['lat'] or not r['lon']:
            continue
        rows.append({'id': f'pass_{i}', 'source': 'passport_202',
                     'kind': 'ground', 'district': 'город',
                     'lat': r['lat'], 'lon': r['lon'], 'z': r['hp'],
                     'rgt': ''})
    # план Атаянс 2013 (каталог одного чтения, как в аудите 30.10)
    A = list(csv.DictReader(open(GH / 'results' / 'atayanc_6.50' /
                                 'marks_catalog_6.50.csv')))
    for i, r in enumerate(A):
        rows.append({'id': f'atay_{i}', 'source': 'atayanc_2013_VO',
                     'kind': 'ground', 'district': 'V.O.',
                     'lat': r['lat'], 'lon': r['lon'],
                     'z': r['h_bs77_m'], 'rgt': ''})
    # archi 514913 (Купчино)
    R = list(csv.DictReader(open(GH / 'scripts' / 'data' /
                                 'archi_plans_vlm_FINAL.csv')))
    n514 = 0
    for i, r in enumerate(R):
        if not (r.get('lon') and r.get('lat')):
            continue
        n514 += 1
        rows.append({'id': f'archi_{i}', 'source': 'archi_514913',
                     'kind': 'ground', 'district': 'Купчино',
                     'lat': r['lat'], 'lon': r['lon'],
                     'z': r['value'], 'rgt': ''})
    print(f"атаянс: {len(A)}, archi 514913: {n514}")
    cols = ['id', 'source', 'kind', 'district', 'lat', 'lon', 'z', 'rgt']
    with open(POOL, 'w', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    print(f"пул: {POOL.name}, {len(rows)} строк")
    return used


def main():
    used_passport = build_pool()
    rep = run_accept(FUS, POOL, REPORT, conf=CONF, anchors=str(ANCHORS),
                     anchor_r=15.0, scale=0.01,
                     title='B5 приёмка: воспроизведение аудита 30.10 на v1.1')

    ps = rep['by_source']
    checks = [
        ('паспорт rest  (near, conf 11)', n := ps['passport_202'].get('conf_11_rest', {}).get('near_anchor', {}),
         {'n': 73, 'median': -0.09, 'gt1': 48}),
        ('паспорт gold  (near, conf 13)', ps['passport_202'].get('conf_13_gold', {}).get('near_anchor', {}),
         {'n': 59, 'median': 1.06, 'gt1': 59}),
        ('атаянс rest   (conf 11)', ps['atayanc_2013_VO'].get('conf_11_rest', {}).get('all', {}),
         {'n': 107, 'median': 2.14, 'gt1': 75}),
        ('атаянс gold   (conf 13)', ps['atayanc_2013_VO'].get('conf_13_gold', {}).get('all', {}),
         {'n': 45, 'median': 1.65, 'gt1': 96}),
        ('купчино rest  (conf 11)', ps['archi_514913'].get('conf_11_rest', {}).get('all', {}),
         {'n': 135, 'median': -0.28, 'gt1': 2}),
        ('купчино gold  (conf 13)', ps['archi_514913'].get('conf_13_gold', {}).get('all', {}),
         {'n': 40, 'median': -0.81, 'gt1': 28}),
    ]
    print(f"\n{'набор':32s} {'n':>5s} {'median':>8s} {'|d|>1м':>7s}   аудитор")
    ok_all = True
    for name, got, exp in checks:
        if not got:
            print(f"{name:32s}  ПУСТО"); ok_all = False; continue
        ok = (got['n'] == exp['n']
              and abs(got['median'] - exp['median']) <= 0.02
              and abs(got['gt1'] - exp['gt1']) <= 3.0)
        ok_all &= ok
        print(f"{name:32s} {got['n']:5d} {got['median']:+8.2f} "
              f"{got['gt1']:6.0f}%   n={exp['n']} {exp['median']:+.2f} "
              f"{exp['gt1']:.0f}%  {'OK' if ok else 'РАСХОЖДЕНИЕ'}")

    rep['acceptance'] = {
        'target': 'NOTES_fusion_audit_6.53 §3 (аудит 30.10), релиз v1.1',
        'passport_file': used_passport.name,
        'checks_ok': ok_all,
        'tolerance': 'n точно; median ±0.02; |d|>1м ±3 п.п.',
    }
    from pathlib import Path as _P
    _P(REPORT).write_text(json.dumps(rep, ensure_ascii=False, indent=1),
                          encoding='utf-8')
    print(f"\nотчёт: {REPORT}")
    if not ok_all:
        print("B5 ПРИЁМКА НЕ СХОДИТСЯ — см. таблицу выше")
        sys.exit(1)
    print("B5 ПРИЁМКА ЗЕЛЁНАЯ: числа аудита 30.10 воспроизведены")


if __name__ == '__main__':
    main()
