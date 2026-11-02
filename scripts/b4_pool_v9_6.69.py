#!/usr/bin/env python3
"""6.69/B4 — валидационный пул v9 (handoff 2026-11 §3-B4).

Две НЕЗАВИСИМЫЕ части (§1: земля и ICESat никогда не смешиваются):

  земля — наземные отметки вне якорей/поправок, БЕЗ VLM-фильтра:
    * independent_pool_honest_6.66 (846 строк) минус «atayanc V.O.» 94
      (устарело одиночное чтение эпохи 5.x — заменено пулом v2);
    * atayanc_pool_v2: ТОЛЬКО use=ground_truth (160) и ground_truth_0.1
      (28) — два совпавших чтения; flag_vs_neighbours=1 — после ручной
      проверки (в пул не входит, помечается отдельно);
    * «Тихая гавань» (96) — блокер, отсутствует (см. 6.66).
  ICESat — по трекам: ATL06 кэш юга города (8 тайлов, 255 246 сегмента,
  6 RGT), высота h_ell пересчитана в БС-77 одним преобразованием
  (h − N_EGM2008 − D); никаких фильтров по dH (§1.1).

Веса — конвенции 6.63: Атаянс confirmed 1.0 / near 0.9; паспорт
detail21 1.0 / detail24 0.7 / detail23 0.5 / прочие 0.3; Купчино
(vlm_read) 1.0; адресная геопривязка и одиночные чтения 0.3.

Колонки для приёмки: dist_to_anchor_m (до якорей marks_val_5.95) и
track_near_anchor (RGT с точкой в 30 м от якоря) — фильтрует accept.py
с отчётом до/после (§1). Никаких фильтров при сборке.

Выход: results/validation_pool_v9/validation_pool_v9.csv (+ summary.json).
Детерминизм: сортировка (kind, source, id); повторная сборка со
случайно перемешанным порядком входов даёт байт-в-байт тот же файл.
"""

import csv
import glob
import json
import random
import time
from collections import Counter
from pathlib import Path

import numpy as np

GH = Path('/tmp/my-project/gh-clone')
OUT = GH / 'results' / 'validation_pool_v9'
OUT.mkdir(parents=True, exist_ok=True)
CSV_OUT = OUT / 'validation_pool_v9.csv'
SUM_OUT = OUT / 'pool_v9_summary.json'

ANCHORS = GH / 'results' / 'marks_val_5.95.csv'
HONEST = GH / 'scripts' / 'data' / 'independent_pool_honest_6.66.csv'
PASSPORT = GH / 'scripts' / 'data' / 'spb_passport_validation_v7.csv'
POOLV2 = GH / 'results' / 'atayanc_pool_v2' / 'atayanc_marks_pool_v2.csv'
DIST = OUT / 'districts_osm.geojson'
TILES = sorted(glob.glob(str(GH / 'scripts' / 'dem_cache' /
                             'atl06_tiles_south' / '*.csv')))

COLS = ['id', 'kind', 'source', 'district', 'lat', 'lon', 'z', 'weight',
        'tier', 'use', 'flag_vs_neighbours', 'h_ell', 'rgt', 'cycle', 'gt',
        'h_sigma', 'n_fit', 'dist_to_anchor_m', 'track_near_anchor',
        't_obs', 'sigma_t', 'how_dated', 'kind_of_height',
        'datum', 'note']

# Реестр эпох (B6, METHOD_temporal_epochs §3): дата съёмки, не публикации.
# Формат t_obs: 'YYYY..YYYY' интервал / '<=YYYY' / 'YYYY-MM-DD' / 'time_ns'.
EPOCHS = {
    'archi.ru Yandex-expanded': (
        '2000..2020', 10.0,
        'подоснова проектного листа archi.ru; дата съёмки не установлена',
        'отметка существующего покрытия (предположительно)'),
    'archi.ru 514913': (
        '2000..2020', 8.0,
        'подоснова листа; штамп не читался (конвенция 6.63)',
        'отметка существующего покрытия'),
    '456577 Римского-Корсакова': (
        '2000..2020', 8.0,
        'подоснова листа; дата съёмки не установлена',
        'отметка существующего покрытия'),
    # 6.72/B6: ответ пользователя (02.11) — «не считая намывов это 2000-е,
    # и их надо смотреть визуально»: даты нивелирования марок — 2000-е годы,
    # кроме марок на намывных территориях; по каждой марке — визуальная
    # проверка даты по листам паспорта (действие pending).
    'Passport holdout': (
        '2000..2009', 3.0,
        'сообщение пользователя (02.11): нивелирование марок — 2000-е годы, '
        'кроме марок на намывах; визуальная проверка даты по листам — pending',
        'стенная марка (высотная сеть)'),
    'atayanc_v2': (
        '<=1990', 10.0,
        'штамп листа (ГДР/ЛГУ): топооснова до 1990; здания 2013 — '
        'проектные, в землю не идут (METHOD §3)',
        'отметка земли (тротуар, проезд, набережная)'),
}
ICE_EPOCH = ('time_ns', 0.0, 'time_ns точки ATL06 — точная дата',
             'ATL06 40-м сегмент (поверхность; может включать крыши)')

W_DETAIL = {'21': 1.0, '24': 0.7, '23': 0.5}


def load_anchors():
    A = list(csv.DictReader(open(ANCHORS)))
    return (np.array([float(r['lon']) for r in A]),
            np.array([float(r['lat']) for r in A]))


def dist_to_anchor(lon, lat, alon, alat, chunk=20000):
    lon = np.asarray(lon); lat = np.asarray(lat)
    out = np.empty(len(lon))
    for i in range(0, len(lon), chunk):
        dx = (alon[None, :] - lon[i:i + chunk, None]) * 55700.0
        dy = (alat[None, :] - lat[i:i + chunk, None]) * 111133.0
        out[i:i + chunk] = np.sqrt(dx ** 2 + dy ** 2).min(axis=1)
    return out


def load_districts():
    fc = json.loads(DIST.read_text(encoding='utf-8'))
    from shapely.geometry import shape
    from shapely.prepared import prep
    from shapely.strtree import STRtree
    l5, l8 = [], []
    for f in fc['features']:
        p = f['properties']
        g = shape(f['geometry'])
        (l5 if p['admin_level'] == '5' else l8).append((p['name'], g))
    return l5, l8, prep, STRtree


def district_of(lon, lat, l5, l8, prep, STRtree):
    from shapely.geometry import Point
    pt = Point(lon, lat)
    for name, g in l5:
        if prep(g).contains(pt):
            return name
    for name, g in l8:
        if prep(g).contains(pt):
            return 'округ: ' + name
    return ''


def build(shuffle_seed=None):
    rows = []
    alon, alat = load_anchors()
    l5, l8, prep, STRtree = load_districts()

    def add_ground(src, district, lat, lon, z, weight, tier, use='', flag='',
                   note=''):
        to, st, hd, kh = EPOCHS[src]
        rows.append({
            'id': '', 'kind': 'ground', 'source': src, 'district': district,
            'lat': round(float(lat), 7), 'lon': round(float(lon), 7),
            'z': round(float(z), 3), 'weight': round(float(weight), 2),
            'tier': tier, 'use': use, 'flag_vs_neighbours': flag,
            'h_ell': '', 'rgt': '', 'cycle': '', 'gt': '', 'h_sigma': '',
            'n_fit': '', 'dist_to_anchor_m': '', 'track_near_anchor': '',
            't_obs': to, 'sigma_t': st, 'how_dated': hd, 'kind_of_height': kh,
            'datum': 'BS77', 'note': note})

    # ---- 1. честный пул 6.66 (минус устаревший 'atayanc V.O.')
    H = list(csv.DictReader(open(HONEST)))
    if shuffle_seed is not None:
        random.Random(shuffle_seed).shuffle(H)
    # паспортные detail для весов: join по округлённым координатам
    P = list(csv.DictReader(open(PASSPORT)))
    pmap = {(round(float(r['lat']), 6), round(float(r['lon']), 6)): r['detail']
            for r in P}
    n_pass_detail = 0
    for r in H:
        src = r['source']
        if src == 'atayanc V.O.':
            continue  # заменён atayanc_pool_v2 (6.69)
        lat, lon, z = float(r['lat']), float(r['lon']), float(r['z_survey'])
        dist = district_of(lon, lat, l5, l8, prep, STRtree)
        if src == 'archi.ru Yandex-expanded':
            add_ground(src, dist, lat, lon, z, 0.3, 'address_georef',
                       note='геопривязка по адресу — слабое звено (5.67 §5)')
        elif src == 'archi.ru 514913':
            add_ground(src, dist, lat, lon, z, 1.0, 'vlm_read',
                       note='обработанный лист, лучший наземный источник')
        elif src == '456577 Римского-Корсакова':
            add_ground(src, dist, lat, lon, z, 0.3, 'single_read',
                       note='лист, одно чтение — конвенция 6.63')
        elif src == 'Passport holdout':
            det = pmap.get((round(lat, 6), round(lon, 6)), '')
            if det:
                n_pass_detail += 1
            w = W_DETAIL.get(det, 0.3)
            add_ground(src, dist, lat, lon, z, w,
                       f'detail{det or "?"}_holdout',
                       note='holdout паспорта, вне якорей')
        else:
            raise ValueError(f'неожиданный источник в честном пуле: {src}')

    # ---- 2. Атаянс pool v2: только два совпавших чтения
    V2 = list(csv.DictReader(open(POOLV2)))
    if shuffle_seed is not None:
        random.Random(shuffle_seed + 1).shuffle(V2)
    n_v2_in = n_v2_flag = 0
    W_STATUS = {'confirmed': 1.0, 'near': 0.9}
    for r in V2:
        if r['use'] not in ('ground_truth', 'ground_truth_0.1'):
            continue
        if r.get('flag_vs_neighbours') == '1':
            n_v2_flag += 1
            continue  # ручная проверка pending (A2); в пул не входит
        n_v2_in += 1
        dist = district_of(float(r['lon']), float(r['lat']), l5, l8, prep, STRtree)
        h = float(r['h_best'] or r['h_claude'])
        add_ground('atayanc_v2', dist, r['lat'], r['lon'], h,
                   W_STATUS.get(r['status'], 0.3), r['use'], r['use'],
                   r.get('flag_vs_neighbours', ''),
                   note=f'статус {r["status"]}: два чтения <=0.01 м'
                   if r['status'] == 'confirmed' else
                   f'статус {r["status"]}: чтения в 0.1 м')

    # ---- 3. ICESat: ATL06 кэш юга, целиком, по трекам
    import sys
    sys.path.insert(0, str(GH / 'scripts'))
    from geoid_egm2008 import Geoid
    g = Geoid('/tmp/my-project/data/us_nga_egm08_25.tif')
    # 6.71/B3: канон results/datum_D.json (D = +0.004, бюджет 0.08)
    dpath = GH / 'results' / 'datum_D.json'
    if not dpath.exists():
        dpath = GH / 'results' / 'datum_6.61' / 'datum_D.json'  # история 6.65
    D = float(json.loads(dpath.read_text(encoding='utf-8'))['D_m'])
    n_ice = 0
    track_min_dist = {}
    meta = {'passport_detail_matched': n_pass_detail,
            'atayanc_v2_in': n_v2_in,
            'atayanc_v2_flag_excluded': n_v2_flag,
            'icesat_n': 0}
    for t in TILES:
        R = list(csv.DictReader(open(t)))
        if shuffle_seed is not None:
            random.Random(shuffle_seed + 2).shuffle(R)
        la = np.array([float(r['lat']) for r in R])
        lo = np.array([float(r['lon']) for r in R])
        dmin = dist_to_anchor(lo, la, alon, alat)
        for r, dmin_i in zip(R, dmin):
            key = r['rgt']
            track_min_dist[key] = min(track_min_dist.get(key, 1e18), dmin_i)
        h_ell = np.array([float(r['h_mean']) for r in R])
        z_bs = h_ell - g.n(lo, la) - D
        for r, z_i, he, dmin_i in zip(R, z_bs, h_ell, dmin):
            dist = district_of(float(r['lon']), float(r['lat']),
                               l5, l8, prep, STRtree)
            dstr = time.strftime('%Y-%m-%d',
                                 time.gmtime(int(r['time_ns']) / 1e9))
            rows.append({
                'id': '', 'kind': 'icesat',
                'source': 'atl06_south_cache', 'district': dist,
                'lat': round(float(r['lat']), 7),
                'lon': round(float(r['lon']), 7),
                'z': round(float(z_i), 3), 'weight': 1.0,
                'tier': 'atl06', 'use': '', 'flag_vs_neighbours': '',
                'h_ell': round(float(he), 3), 'rgt': r['rgt'],
                'cycle': r['cycle'], 'gt': r['gt'],
                'h_sigma': r['h_sigma'], 'n_fit': r['n_fit'],
                'dist_to_anchor_m': round(float(dmin_i), 1),
                'track_near_anchor': '',
                't_obs': dstr, 'sigma_t': ICE_EPOCH[1],
                'how_dated': ICE_EPOCH[2], 'kind_of_height': ICE_EPOCH[3],
                'datum': f'BS77_EGM2008_D{D:+.3f}',
                'note': f'{dstr} ATL06 40-м сегмент; без фильтров (§1.1)'})
            n_ice += 1

    for row in rows:
        if row['kind'] == 'icesat':
            row['track_near_anchor'] = \
                '1' if track_min_dist.get(row['rgt'], 1e18) <= 30.0 else '0'

    # ---- расстояния до якорей для земли + сортировка (детерминизм)
    gidx = [i for i, r in enumerate(rows) if r['kind'] == 'ground']
    glon = [rows[i]['lon'] for i in gidx]
    glat = [rows[i]['lat'] for i in gidx]
    gd = dist_to_anchor(np.array(glon), np.array(glat), alon, alat)
    for i, dmin_i in zip(gidx, gd):
        rows[i]['dist_to_anchor_m'] = round(float(dmin_i), 1)

    # дедуп по СОДЕРЖИМОМУ (одинаковый сегмент в перекрывающихся тайлах —
    # одно наблюдение, не фильтр §1) -> сортировка по ключу -> id по позиции
    key_cols = [c for c in COLS if c != 'id']
    seen, uniq = set(), []
    n_dup = 0
    for r in rows:
        k = tuple(r[c] for c in key_cols)
        if k in seen:
            n_dup += 1
            continue
        seen.add(k)
        uniq.append(r)
    uniq.sort(key=lambda r: tuple(r[c] for c in key_cols))
    for i, r in enumerate(uniq):
        r['id'] = f'{r["kind"]}_{i:06d}'
    meta['cache_duplicates_removed'] = n_dup
    return uniq, meta


def main():
    rows, meta = build()
    with open(CSV_OUT, 'w', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=COLS)
        w.writeheader()
        w.writerows(rows)
    print(f"пул v9: {CSV_OUT.name} — {len(rows)} строк")

    # детерминизм: пересборка с другим порядком входов
    rows2, _ = build(shuffle_seed=2026)
    same = rows == rows2
    print(f"детерминизм (перемешанные входы -> тот же пул): {same}")

    comp_g = Counter((r['source'], r['tier']) for r in rows
                     if r['kind'] == 'ground')
    comp_i = Counter(r['source'] for r in rows if r['kind'] == 'icesat')
    dists = Counter(r['district'].split(':')[0] or '(вне границ)'
                    for r in rows if r['kind'] == 'ground')
    ice_rgt = sorted({r['rgt'] for r in rows if r['kind'] == 'icesat'})
    ice_trk_na = sorted({r['rgt'] for r in rows
                         if r['kind'] == 'icesat'
                         and r['track_near_anchor'] == '1'})
    summary = {
        'n_total': len(rows),
        'n_ground': sum(1 for r in rows if r['kind'] == 'ground'),
        'n_icesat': sum(1 for r in rows if r['kind'] == 'icesat'),
        'icesat_n_rgt': len(ice_rgt), 'icesat_rgt': ice_rgt,
        'icesat_tracks_near_anchor': ice_trk_na,
        'determinism_check': same,
        'meta': meta,
        'ground_by_source_tier': {f'{s} | {t}': n
                                  for (s, t), n in sorted(comp_g.items())},
        'ground_by_district': dict(sorted(dists.items(),
                                          key=lambda kv: -kv[1])),
        'excluded': {
            'atayanc_v2 flag_vs_neighbours=1 (ручная проверка pending, A2)':
                meta['atayanc_v2_flag_excluded'],
            'atayanc V.O. одиночное чтение (заменено v2)': 94,
            'Тихая гавань 95211 (блокер, archi.ru недоступен)': 96,
            'atayanc use=candidate_second_read (до второго чтения, A2)': 427,
            'atayanc use=third_read (до третьего чтения, A2)': 94,
        },
    }
    SUM_OUT.write_text(json.dumps(summary, ensure_ascii=False, indent=1),
                       encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=1)[:1800])


if __name__ == '__main__':
    main()
