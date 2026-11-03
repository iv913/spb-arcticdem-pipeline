#!/usr/bin/env python3
"""6.74/C1 — точки с генпланов (19 проектов, 764 отметки): канонизация + приёмка.

Вход (upload):
  spot_heights_all.geojson     — 764 точки (id, plan, h_m, confidence)
  spot_heights_all (3).csv     — тот же экспорт (сверка байт-в-байт по id)
  spot_heights_296457.csv      — план 296457 с px/py (проверка происхождения)
  plans_index.csv              — 19 планов: адреса, счётчики, диапазоны h,
                                 метод геопривязки, точность 1–3 м

Выход (repo results/plan_points_v1/):
  plan_points_v1.csv/.geojson  — канон в конвенции пула v9
                                 (source/kind/weight/tier/t_obs/datum…)
  metrics_plan_points_v1.json  — разрезы d = surf − z по планам, confidence,
                                 точности геопривязки, тирам conf поверхности
  accept_plan_points_v1.json   — приёмка accept.py (§1 handoff) по пулу планов

Конвенции:
  datum = BS77 (высоты планов — абсолютные отметки БС-77); поверхность —
  канон fusion 6.00 A2 (datum BS77_EGM2008_D+0.004, D=+0.004 из datum_D.json).
  Расхождение d, статистически неотличимое от нуля, — независимое
  подтверждение датума B3 на новом материале.
  t_obs пусто: даты генпланов не установлены (штампы не читались) —
  в разрез по возрасту (B6) точки не идут, это честно фиксируется.
  weight = 0.3/0.2/0.1 по confidence (high/medium/low): одиночное чтение
  плана, горизонтальная точность 1–3 м — слабее atayanc_v2 (1.0),
  на уровне archi.ru Yandex-expanded (0.3).
"""
import csv
import json
import sys
from pathlib import Path

import numpy as np

REPO = Path('/tmp/my-project/gh-clone')
UP = Path('/home/z/my-project/upload')
OUT = REPO / 'results' / 'plan_points_v1'
OUT.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(REPO / 'scripts' / 'validation'))
from accept import run_accept, _sample, _conf_tier, _stats, _mad  # noqa: E402

SURF = '/tmp/my-project/download/mosaic_6.00/spb_terra_fusion_6.00_A2.tif'
CONF = '/tmp/my-project/gh-clone/download/mosaic_6.00/spb_terra_conf_6.00_A2.tif'
ANCHORS = str(REPO / 'results' / 'marks_val_5.95.csv')
SCALE = 0.01  # Int16 см -> м

SHORT = {  # короткие имена источников (конвенция «<plan_id> <имя>»)
    '287970': 'Прилукская',
    '296457': 'Ветеранов 91',
    '314539': 'ЖК Amo (В.О.)',
    '314550': 'пр. Тореза 44',
    '322679': 'Политехническая МФК',
    '322684': 'Политехническая генплан',
    '328595': 'Avenue Page',
    '331143': 'наб. Мартынова',
    '341906': 'iD Московский',
    '345639': 'Гаванская 7',
    '349338': 'пр. Энергетиков',
    '360916': 'Стартовая (Пулково)',
    '370747': 'Курляндская 10',
    '371998': 'Фрунзенская вестиб.',
    '386481': 'пл. Ленина',
    '391398': 'Маршала Казакова',
    '456577': 'Римского-Корсакова',
    '487567': 'AQUATORI',
    '493731': 'пл. Конституции',
}
WEIGHT = {'high': 0.3, 'medium': 0.2, 'low': 0.1}
HOW_DATED = ('дата генплана не установлена (штампы не читались); '
             'визуальная проверка по листам — pending')
KIND_OF_H = 'отметка существующей поверхности на генплане (БС-77)'

# ---------------------------------------------------------------- 1. загрузка
gj = json.load(open(UP / 'spot_heights_all.geojson'))
feats = gj['features']
rows3 = {r['point_id']: r for r in csv.DictReader(
    open(UP / 'spot_heights_all (3).csv', encoding='utf-8-sig'))}
idx = {r['plan_id']: r for r in csv.DictReader(
    open(UP / 'plans_index.csv', encoding='utf-8-sig'))}
px296 = {r['point_id']: (r['plan_px_x'], r['plan_px_y'])
         for r in csv.DictReader(
             open(UP / 'spot_heights_296457.csv', encoding='utf-8-sig'))}

# ------------------------------------------------------- 2. канонизация + QC
pts, qc = [], []
for f in feats:
    p = f['properties']
    lon, lat = f['geometry']['coordinates']
    pid = p['id']
    plan_id = p['plan'].replace('.jpg', '')
    r3 = rows3.get(pid)
    if r3 is None or abs(float(r3['h_m']) - float(p['h_m'])) > 1e-9 or \
       abs(float(r3['lat_wgs84']) - lat) > 1e-7 or \
       abs(float(r3['lon_wgs84']) - lon) > 1e-7:
        qc.append(f'{pid}: расхождение geojson vs csv(3)')
    meta = idx[plan_id]
    h = float(p['h_m'])
    if not (float(meta['h_min']) - 1e-9 <= h <= float(meta['h_max']) + 1e-9):
        qc.append(f'{pid}: h={h} вне диапазона листа '
                  f'[{meta["h_min"]}, {meta["h_max"]}]')
    pts.append(dict(
        id=pid, kind='ground', source=f'{plan_id} {SHORT[plan_id]}',
        plan_id=plan_id, plan_name=meta['project_address'],
        lat=f'{lat:.7f}', lon=f'{lon:.7f}', z=f'{h:.2f}',
        confidence=p['confidence'],
        horiz_accuracy_m=meta['horiz_accuracy_m'],
        georef_method=meta['georef_method'],
        weight=f"{WEIGHT[p['confidence']]:.1f}", tier='plan_single_read',
        use='', t_obs='', sigma_t='', how_dated=HOW_DATED,
        kind_of_height=KIND_OF_H, datum='BS77',
        note=f'генплан {p["plan"]}, одно чтение',
        _px=px296.get(pid, ('', '')),
    ))

# дубли координат внутри плана
seen = {}
for r in pts:
    key = (r['plan_id'], r['lat'], r['lon'])
    if key in seen:
        qc.append(f'{r["id"]} и {seen[key]}: одинаковые координаты '
                  f'(вероятно, та же отметка; h {r["z"]} м)')
    else:
        seen[key] = r['id']
qc = sorted(set(qc))

# счётчики против plans_index
cnt = {}
for r in pts:
    cnt[r['plan_id']] = cnt.get(r['plan_id'], 0) + 1
for pl, meta in idx.items():
    if cnt.get(pl, 0) != int(meta['n_points']):
        qc.append(f'{pl}: счётчик {cnt.get(pl, 0)} != plans_index '
                  f'{meta["n_points"]}')

# ------------------------------------------------ 3. район (полигоны 5.85)
dist_polys = json.load(open(UP / 'spb-coverage-5.85' /
                            'spb_districts_5.85.json'))


def in_poly(lon, lat, poly):
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > lat) != (y2 > lat):
            xin = (x2 - x1) * (lat - y1) / (y2 - y1) + x1
            if lon < xin:
                inside = not inside
    return inside


for r in pts:
    lon, lat = float(r['lon']), float(r['lat'])
    for dname, d in dist_polys.items():
        g = d.get('geojson') or {}
        geoms = ([g['coordinates']] if g.get('type') == 'Polygon'
                 else g.get('coordinates', []))
        if any(in_poly(lon, lat, poly[0]) for poly in geoms):
            r['district'] = dname
            break
    else:
        r['district'] = ''

# ------------------------------------------------------- 4. запись канона
COLS = ['id', 'kind', 'source', 'plan_id', 'plan_name', 'district',
        'lat', 'lon', 'z', 'confidence', 'horiz_accuracy_m',
        'georef_method', 'weight', 'tier', 'use', 't_obs', 'sigma_t',
        'how_dated', 'kind_of_height', 'datum', 'note']
with open(OUT / 'plan_points_v1.csv', 'w', newline='', encoding='utf-8') as fh:
    w = csv.DictWriter(fh, fieldnames=COLS, extrasaction='ignore')
    w.writeheader()
    for r in pts:
        w.writerow({k: r[k] for k in COLS})

gj_out = {'type': 'FeatureCollection',
          'crs': {'type': 'name',
                  'properties': {'name': 'EPSG:4326'}},
          'features': [
              {'type': 'Feature',
               'geometry': {'type': 'Point',
                            'coordinates': [float(r['lon']), float(r['lat'])]},
               'properties': {k: r[k] for k in COLS}}
              for r in pts]}
json.dump(gj_out, open(OUT / 'plan_points_v1.geojson', 'w',
                       encoding='utf-8'), ensure_ascii=False, indent=1)

# ------------------------------------------------------------- 5. метрики
lat = np.array([float(r['lat']) for r in pts])
lon = np.array([float(r['lon']) for r in pts])
z = np.array([float(r['z']) for r in pts])
src = np.array([r['source'] for r in pts])
cf = np.array([r['confidence'] for r in pts])
acc = np.array([r['horiz_accuracy_m'] for r in pts])

zs = _sample(SURF, lon, lat, scale=SCALE)
tiers = _conf_tier(CONF, lon, lat)
d = zs - z
finite = np.isfinite(d) & np.isfinite(z)
outside = [pts[i]['id'] for i in range(len(pts))
           if not finite[i] or not (59.6 < lat[i] < 60.1
                                    and 29.7 < lon[i] < 30.9)]

M = {'meta': {
    'surface': SURF, 'conf': CONF, 'scale': SCALE,
    'n_points': len(pts), 'n_plans': len(idx),
    'datum_surface': 'BS77_EGM2008_D+0.004 (results/datum_D.json, B3/6.71)',
    'datum_points': 'BS77 (абсолютные отметки генпланов)',
    't_obs': 'не установлено — в разрез по возрасту не идут',
    'date': '2026-11-03'}, 'qc': qc, 'outside_raster': outside}

M['all'] = _stats(d[finite])
M['by_plan'] = {s: _stats(d[finite & (src == s)]) for s in sorted(set(src))}
M['by_confidence'] = {c: _stats(d[finite & (cf == c)])
                      for c in ('high', 'medium', 'low')}
M['by_georef_accuracy'] = {a: _stats(d[finite & (acc == a)])
                           for a in sorted(set(acc))}
M['by_conf_tier_surface'] = {
    f'tier_{t}': _stats(d[finite & (tiers == t)])
    for t in sorted(set(tiers.tolist())) if (finite & (tiers == t)).any()}

# земля v9 на той же поверхности (справочно, без фильтров)
P9 = [r for r in csv.DictReader(open(REPO / 'results' /
                                     'validation_pool_v9' /
                                     'validation_pool_v9.csv'))
      if r['kind'] == 'ground']
z9 = np.array([float(r['z']) for r in P9])
la9 = np.array([float(r['lat']) for r in P9])
lo9 = np.array([float(r['lon']) for r in P9])
zs9 = _sample(SURF, lo9, la9, scale=SCALE)
d9 = zs9 - z9
M['ground_v9_reference_same_surface'] = _stats(d9[np.isfinite(d9)])

# сверка 456577: новый канон против строк пула v9 (одного плана)
old = [(float(r['lon']), float(r['lat']), float(r['z']))
       for r in P9 if r['source'] == '456577 Римского-Корсакова']
pairs = []
for r in pts:
    if r['plan_id'] != '456577':
        continue
    lonr, latr = float(r['lon']), float(r['lat'])
    best = min(old, key=lambda o: ((o[0]-lonr)*55700)**2 +
               ((o[1]-latr)*111133)**2)
    dist = (((best[0]-lonr)*55700)**2 + ((best[1]-latr)*111133)**2) ** 0.5
    if dist <= 5.0:
        pairs.append(float(r['z']) - best[2])
M['check_456577_vs_pool_v9'] = {
    'new_points': sum(1 for r in pts if r['plan_id'] == '456577'),
    'pool_v9_points': len(old), 'matched<=5m': len(pairs),
    'dz_new_minus_old': _stats(pairs) if pairs else None}

# ------------------------------------------- 5b. диагностика по планам
# Локальная изменчивость поверхности: std высот в ~10 м окрестности точки
# (центр + 9 смещений). Если MAD невязки плана << локального std — сдвиг
# единый (уровень площадки/эпоха), а не ошибка геопривязки на склоне.
OFFS = [(0.0, 0.0), (-8, 0), (8, 0), (0, -8), (0, 8),
        (-6, -6), (-6, 6), (6, -6), (6, 6)]
zloc = np.full((len(pts), len(OFFS)), np.nan)
for j, (dx, dy) in enumerate(OFFS):
    zloc[:, j] = _sample(SURF, lon + dx / 55700.0, lat + dy / 111133.0,
                         scale=SCALE)
loc_std = np.nanstd(zloc, axis=1)

diag = {}
for s in sorted(set(src)):
    m = finite & (src == s) & np.isfinite(loc_std)
    if not m.any():
        continue
    mad_r = _mad(d[m])
    med_loc = float(np.median(loc_std[m]))
    diag[s] = {'resid_mad': round(mad_r, 3),
               'surface_local_std_med': round(med_loc, 3),
               'ratio_mad_to_local': round(mad_r / med_loc, 2)
               if med_loc > 0 else None,
               'h_min': round(float(z[m].min()), 2),
               'h_max': round(float(z[m].max()), 2)}
M['diagnosis_per_plan'] = diag

# страты: A — площадка без крупного смещения (|median|<=0.75 и MAD<=0.45),
# B — систематическое смещение площадки (стройка/снос/уровень планировки)
strat_a = [s for s, v in M['by_plan'].items()
           if abs(v['median']) <= 0.75 and v['mad'] <= 0.45]
strat_b = [s for s in M['by_plan'] if s not in strat_a]
ma = finite & np.isin(src, strat_a)
mb = finite & np.isin(src, strat_b)
M['strata'] = {
    'A_site_unchanged': {'sources': strat_a, 'stats': _stats(d[ma])},
    'B_site_shifted': {'sources': strat_b, 'stats': _stats(d[mb])}}

# страты × полоса высот (0–5 м — зона интереса карты затоплений)
inb = (z >= 0.0) & (z < 5.0)
M['strata_bands'] = {
    st: {'band_0_5': _stats(d[finite & mask & inb]),
         'band_other': _stats(d[finite & mask & ~inb])}
    for st, mask in (('A_site_unchanged', ma), ('B_site_shifted', mb))}

# регистрация источников в реестре эпох (docs/source_epochs.csv)
EPOCHS = REPO / 'docs' / 'source_epochs.csv'
existing = {r['source'] for r in csv.DictReader(open(EPOCHS))}
new_rows = []
for pl in sorted(idx):
    sname = f'{pl} {SHORT[pl]}'
    if sname in existing:
        continue
    strat = ('A' if sname in strat_a else 'B')
    new_rows.append({'source': sname, 't_obs': '', 'sigma_t': '',
                     'how_dated': 'дата генплана не установлена; '
                                  'визуальная проверка по листам — pending',
                     'kind_of_height': KIND_OF_H,
                     'notes': f'генплан застройки; вес по confidence '
                              f'0.3/0.2/0.1; страта {strat} по медиане '
                              f'6.74 (results/plan_points_v1)'})
if new_rows:
    with open(EPOCHS, 'a', newline='', encoding='utf-8') as fh:
        w = csv.DictWriter(fh, fieldnames=['source', 't_obs', 'sigma_t',
                                           'how_dated', 'kind_of_height',
                                           'notes'])
        w.writerows(new_rows)
print(f'реестр source_epochs.csv: добавлено {len(new_rows)} источников')

json.dump(M, open(OUT / 'metrics_plan_points_v1.json', 'w',
                  encoding='utf-8'), ensure_ascii=False, indent=1)

# ------------------------------------------------ 6. приёмка accept.py (§1)
pool_csv = OUT / 'plan_points_v1.csv'
run_accept(SURF, str(pool_csv), str(OUT / 'accept_plan_points_v1.json'),
           conf=CONF, anchors=ANCHORS, anchor_r=15.0,
           title='C1: точки с генпланов (19 планов, 764 отметки) '
                 'против канона fusion 6.00 A2 (D=+0.004)',
           scale=SCALE, t_ref=2026.0)

# --------------------------------------------------------------- 7. отчёт
print(f'точек: {len(pts)}, планов: {len(idx)}')
print(f'QC-замечаний: {len(qc)}')
for q in qc:
    print('  QC:', q)
print(f'вне растра/диапазона: {len(outside)} {outside[:8]}')
s = M['all']
print(f"\nALL         n={s['n']:4d} median {s['median']:+.3f} "
      f"MAD {s['mad']:.3f} p95 {s['p95']:.2f} |d|>1м {s['gt1']:.1f}%")
s9 = M['ground_v9_reference_same_surface']
print(f"ground v9   n={s9['n']:4d} median {s9['median']:+.3f} "
      f"MAD {s9['mad']:.3f}  (та же поверхность, справочно)")
print('\nпо планам (median | MAD | локальный std поверхности | отношение):')
for k, v in sorted(M['by_plan'].items(), key=lambda kv: kv[1]['median']):
    dg = M['diagnosis_per_plan'].get(k, {})
    print(f"  {k:32s} n={v['n']:3d} median {v['median']:+.2f} "
          f"MAD {v['mad']:.2f} | loc_std {dg.get('surface_local_std_med', float('nan')):.2f} "
          f"ratio {dg.get('ratio_mad_to_local')}")
print('\nстраты:')
for k, v in M['strata'].items():
    s = v['stats']
    print(f"  {k:20s} n={s['n']:3d} median {s['median']:+.3f} MAD {s['mad']:.3f} "
          f"({len(v['sources'])} планов)")
print('страты × полоса 0–5 м:')
for k, v in M['strata_bands'].items():
    b = v['band_0_5']; o = v['band_other']
    print(f"  {k:20s} 0-5м n={b['n']:3d} median {b['median']:+.2f} MAD {b['mad']:.2f}"
          f" | прочие n={o['n']:3d} median {o['median']:+.2f} MAD {o['mad']:.2f}")
print('\nпо confidence:')
for k, v in M['by_confidence'].items():
    print(f"  {k:8s} n={v['n']:3d} median {v['median']:+.2f} MAD {v['mad']:.2f}")
print('\nпо точности геопривязки:')
for k, v in M['by_georef_accuracy'].items():
    print(f"  {k:6s} n={v['n']:3d} median {v['median']:+.2f} MAD {v['mad']:.2f}")
print('\n456577 vs пул v9:', json.dumps(M['check_456577_vs_pool_v9'],
                                        ensure_ascii=False))
