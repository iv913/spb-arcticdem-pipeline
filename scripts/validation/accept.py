#!/usr/bin/env python3
"""accept.py — протокол приёмки как код (B5, handoff 2026-11 §3-B5).

На вход: растр поверхности (+ опц. растр conf / растр базы) и пул v9.
На выход: JSON с метриками по §1 handoff:

  * земля и ICESat ОТДЕЛЬНО; ICESat — по трекам (n_eff = число RGT);
  * разрезы: по источникам, по районам, по полосе высот 0–5 м;
  * разрез по тирам conf (10 excl / 11 rest / 12 ok / 13 gold), если
    дан растр conf;
  * у КАЖДОГО фильтра отчёт до и после (n_before → n_after);
  * парный тест «новое против базы» через stats_toolkit.paired_verdict
    (земля — по точкам, ICESat — по трекам).

Вердикт по точечному MAD не выносится (§1.6) — только парные тесты.

Формат пула (CSV, колонки):
  source, kind (ground|icesat), lat, lon, z; опц. rgt (обязателен для
  icesat), district, id.

usage:
  python3 scripts/validation/accept.py SURFACE.tif [--conf CONF.tif] \
      --pool POOL.csv --out REPORT.json [--base BASE.tif] \
      [--anchors ANCHORS.csv --anchor-r 15.0] [--band-lo 0 --band-hi 5] \
      [--title STR]

Владелец: агент B (§2). Агент A вызывает на каждом этапе (§5).
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import json
import sys
from pathlib import Path

import numpy as np
import rasterio

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # scripts/
from stats_toolkit.paired_tests import paired_verdict  # noqa: E402

CONF_TIERS = {10: 'excl', 11: 'rest', 12: 'ok', 13: 'gold'}

AGE_BINS = (('0-5л', 0.0, 5.0), ('5-15л', 5.0, 15.0),
            ('15-30л', 15.0, 30.0), ('>30л', 30.0, 1e9))


def parse_t_obs(s):
    """t_obs -> год (float). Форматы: 'YYYY..YYYY' (середина), '<=YYYY',
    'YYYY-MM-DD' (с дробной частью), 'YYYY'. Пусто/неразборливо -> None."""
    s = (s or '').strip()
    if not s:
        return None
    try:
        if '..' in s:
            a, b = s.split('..', 1)
            return (float(a) + float(b)) / 2.0
        if s.startswith('<='):
            return float(s[2:])
        if len(s) >= 10 and s[4] == '-':
            import datetime as _dt
            d = _dt.date.fromisoformat(s[:10])
            j = d.timetuple().tm_yday
            return d.year + (j - 1) / 365.25
        return float(s)
    except Exception:
        return None


# ------------------------------------------------------------------ утилиты

def _mad(x):
    x = np.asarray(x, float)
    return float(np.median(np.abs(x - np.median(x)))) if x.size else float('nan')


def _stats(d):
    """Метрики одного набора разностей (модель − истина), м."""
    d = np.asarray(d, float)
    d = d[np.isfinite(d)]
    if d.size == 0:
        return {'n': 0, 'median': None, 'mad': None, 'p95': None,
                'gt1': None, 'rmse': None}
    return {'n': int(d.size),
            'median': round(float(np.median(d)), 4),
            'mad': round(_mad(d), 4),
            'p95': round(float(np.percentile(np.abs(d), 95)), 4),
            'gt1': round(float(100 * (np.abs(d) > 1).mean()), 2),
            'rmse': round(float(np.sqrt((d ** 2).mean())), 4)}


def _sample(raster, lon, lat, scale=1.0):
    """Высоты поверхности в точках; NaN вне растра и в nodata.
    scale — множитель единиц растра к метрам (fusion 6.x — Int16 см,
    scale=0.01; конвенция строки в аудиторском indep.py: z*0.01)."""
    lon = np.asarray(lon, float)
    lat = np.asarray(lat, float)
    with rasterio.open(raster) as s:
        xy = list(zip(lon, lat))
        z = np.array([v[0] for v in s.sample(xy)], float)
        nd = s.nodata
        tr, w, h_ = s.transform, s.width, s.height
    ok = np.isfinite(z)
    if nd is not None:
        ok &= (z != nd)
    # точки вне растра: sample вернёт крайние — проверим явно
    inv = ~tr
    for i, (x, y) in enumerate(zip(lon, lat)):
        col, row = inv * (x, y)
        if not (0 <= col < w and 0 <= row < h_):
            ok[i] = False
    z = z.astype(float) * scale
    z[~ok] = np.nan
    return z


def _conf_tier(conf_raster, lon, lat):
    lon = np.asarray(lon, float)
    lat = np.asarray(lat, float)
    with rasterio.open(conf_raster) as s:
        xy = list(zip(lon, lat))
        c = np.array([v[0] for v in s.sample(xy)], int)
    return c


def _near(lon, lat, alon, alat, r_m):
    dx = (np.asarray(alon)[None, :] - np.asarray(lon)[:, None]) * 55700.0
    dy = (np.asarray(alat)[None, :] - np.asarray(lat)[:, None]) * 111133.0
    return np.sqrt(dx ** 2 + dy ** 2).min(axis=1) <= r_m


# ------------------------------------------------------------------ main

def run_accept(surface, pool_csv, out_json, conf=None, base=None,
               anchors=None, anchor_r=15.0, band_lo=0.0, band_hi=5.0,
               title='', scale=1.0, base_scale=1.0, t_ref=2026.0):
    P = list(csv.DictReader(open(pool_csv)))
    need = {'source', 'kind', 'lat', 'lon', 'z'}
    if not need <= set(P[0].keys()):
        raise ValueError(f'пул без колонок {need - set(P[0])}: {pool_csv}')
    for p in P:
        p['kind'] = p['kind'].strip().lower()
    kinds = sorted({p['kind'] for p in P})
    unknown = set(kinds) - {'ground', 'icesat'}
    if unknown:
        raise ValueError(f'kind вне {{ground, icesat}}: {unknown}')

    filters = [{'name': 'pool_as_given',
                'n_before': len(P), 'n_after': len(P),
                'desc': 'пул v9 как подан; фильтровать по |dH| запрещено §1.1'}]
    lat = np.array([float(p['lat']) for p in P])
    lon = np.array([float(p['lon']) for p in P])
    z = np.array([float(p['z']) for p in P])
    kind = np.array([p['kind'] for p in P])
    src = np.array([p['source'] for p in P])
    dist = np.array([(p.get('district') or '').strip() for p in P])
    rgt = np.array([(p.get('rgt') or '').strip() for p in P])
    t_obs_arr = [parse_t_obs(p.get('t_obs')) for p in P]
    kind_h = np.array([(p.get('kind_of_height') or '').strip() for p in P])

    zs = _sample(surface, lon, lat, scale=scale)
    d = zs - z
    finite = np.isfinite(d) & np.isfinite(z)
    filters.append({'name': 'sample_finite',
                    'n_before': len(P), 'n_after': int(finite.sum()),
                    'desc': 'точка внутри растра, значение не nodata'})

    use = finite.copy()
    if anchors:
        A = list(csv.DictReader(open(anchors)))
        alon = np.array([float(r['lon']) for r in A])
        alat = np.array([float(r['lat']) for r in A])
        na = np.zeros(len(P), bool)
        na[finite] = _near(lon[finite], lat[finite], alon, alat, anchor_r)
        filters.append({'name': 'near_anchor_marked',
                        'n_before': int(finite.sum()),
                        'n_after': int((finite & ~na).sum()),
                        'desc': f'пометка «в {anchor_r:g} м от якорной '
                                f'отметки» ({int(na[finite].sum())} шт.); '
                                'метрики даются ДО и ПОСЛЕ удаления'})
    else:
        na = np.zeros(len(P), bool)

    tiers = None
    if conf:
        tiers = _conf_tier(conf, lon, lat)

    report = {'meta': {
        'surface': str(surface), 'conf': str(conf) if conf else None,
        'base': str(base) if base else None, 'pool': str(pool_csv),
        'anchors': str(anchors) if anchors else None,
        'anchor_r_m': anchor_r if anchors else None,
        'band': [band_lo, band_hi], 'title': title, 'scale': scale,
        't_ref': t_ref,
        'date': _dt.date.today().isoformat(),
        'rules': 'handoff 2026-11 §1: земля/ICESat раздельно, ICESat по '
                 'трекам, парные тесты, фильтры — с отчётом до/после',
    }, 'filters': filters}

    def block(mask):
        """Полный разрез для маски пула: фильтры, полоса, тиры conf."""
        b = {'all': _stats(d[mask & use])}
        if anchors:
            b['near_anchor'] = _stats(d[mask & use & na])
            b['not_near_anchor'] = _stats(d[mask & use & ~na])
        inb = (z >= band_lo) & (z < band_hi)
        b['band_0_5' if (band_lo, band_hi) == (0.0, 5.0) else
          f'band_{band_lo:g}_{band_hi:g}'] = _stats(d[mask & use & inb])
        b['band_other'] = _stats(d[mask & use & ~inb])
        if tiers is not None:
            for t, lbl in CONF_TIERS.items():
                mt = (tiers == t)
                if not (mask & use & mt).any():
                    continue
                tb = {'all': _stats(d[mask & use & mt])}
                if anchors:
                    tb['near_anchor'] = _stats(d[mask & use & mt & na])
                    tb['not_near_anchor'] = _stats(d[mask & use & mt & ~na])
                b[f'conf_{t}_{lbl}'] = tb
        return b

    # ---- по видам наблюдений
    by_kind = {}
    for k in ('ground', 'icesat'):
        m = kind == k
        if not m.any():
            continue
        kb = block(m)
        if k == 'icesat':
            mm = m & use & ~na & np.isfinite(d)
            trk = sorted({r for r in rgt[mm] if r})
            tmed = [float(np.median(d[mm & (rgt == t)])) for t in trk]
            kb['n_eff'] = len(trk)
            kb['by_track_median'] = {
                'min': round(min(tmed), 3) if tmed else None,
                'p25': round(float(np.percentile(tmed, 25)), 3) if tmed else None,
                'p75': round(float(np.percentile(tmed, 75)), 3) if tmed else None,
                'max': round(max(tmed), 3) if tmed else None}
            kb['note'] = ('метрики all/not_near_anchor считаются по точкам '
                          'для справки; вердикты — только по трекам (§1.2)')
        by_kind[k] = kb
    report['by_kind'] = by_kind

    # ---- разрезы: источник, район, тир conf
    by_source = {}
    for s in sorted(set(src)):
        by_source[s] = block(src == s)
    report['by_source'] = by_source

    by_dist = {}
    for s in sorted({x for x in dist if x}):
        by_dist[s] = block(dist == s)
    if by_dist:
        report['by_district'] = by_dist

    if tiers is not None:
        by_tier = {}
        for t, lbl in CONF_TIERS.items():
            mt = (tiers == t)
            if (mt & use).any():
                by_tier[f'{t} {lbl}'] = {
                    'all': _stats(d[mt & use]),
                    'not_near_anchor': _stats(d[mt & use & ~na])
                    if anchors else None,
                }
        report['by_conf_tier'] = by_tier

    # ---- dH против возраста отметки (B6, METHOD §3): по типам высоты
    ages = np.array([ (t_ref - t) if t is not None else np.nan
                      for t in t_obs_arr ])
    if np.isfinite(ages).any():
        by_age = {}
        for kh in sorted({k for k in kind_h if k}):
            mk = (kind_h == kh) & use & np.isfinite(ages)
            if not mk.any():
                continue
            grp = {}
            for lbl, lo_, hi_ in AGE_BINS:
                mb = mk & (ages >= lo_) & (ages < hi_)
                if mb.any():
                    grp[lbl] = _stats(d[mb])
            if grp:
                by_age[kh] = grp
        if by_age:
            report['by_age_vs_tref'] = {
                't_ref': t_ref,
                'note': 'возраст = T_ref − t_obs; середина интервала для '
                        'неточных дат; σ_t — в пуле, в разрез не входит',
                'groups': by_age}

    # ---- парный тест «новое против базы» (если задана база)
    if base:
        zb = _sample(base, lon, lat, scale=base_scale)
        dd = zs - zb                      # новая − база, те же точки
        pv = {}
        for k in ('ground', 'icesat'):
            m = (kind == k) & np.isfinite(dd) & use & ~na
            if not m.any():
                continue
            if k == 'icesat':
                trk = np.array([r if r else f'pt{i}'
                                for i, r in enumerate(rgt)])
                v = paired_verdict(dd[m], rgt=trk[m])
            else:
                v = paired_verdict(dd[m])
            pv[k] = v.as_dict()
        report['paired_vs_base'] = pv

    Path(out_json).write_text(json.dumps(report, ensure_ascii=False,
                                         indent=1), encoding='utf-8')
    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('surface')
    ap.add_argument('--conf')
    ap.add_argument('--pool', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--base')
    ap.add_argument('--anchors')
    ap.add_argument('--anchor-r', type=float, default=15.0)
    ap.add_argument('--band-lo', type=float, default=0.0)
    ap.add_argument('--band-hi', type=float, default=5.0)
    ap.add_argument('--title', default='')
    ap.add_argument('--scale', type=float, default=1.0,
                    help='множитель единиц растра к метрам (Int16 см → 0.01)')
    ap.add_argument('--base-scale', type=float, default=1.0)
    ap.add_argument('--t-ref', type=float, default=2026.0,
                    help='опорная дата для разреза dH против возраста (B6)')
    a = ap.parse_args()
    rep = run_accept(a.surface, a.pool, a.out, conf=a.conf, base=a.base,
                     anchors=a.anchors, anchor_r=a.anchor_r,
                     band_lo=a.band_lo, band_hi=a.band_hi, title=a.title,
                     scale=a.scale, base_scale=a.base_scale)
    for k, b in rep['by_kind'].items():
        s = b['all']
        extra = f", n_eff={b['n_eff']}" if 'n_eff' in b else ''
        print(f"{k:7s} n={s['n']}{extra} median {s['median']:+.2f} "
              f"MAD {s['mad']:.2f} |d|>1м {s['gt1']:.0f}%")
    print(f"отчёт: {a.out}")


if __name__ == '__main__':
    main()
