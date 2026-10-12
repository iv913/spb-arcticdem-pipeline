#!/usr/bin/env python3
"""5.90: валидация южного расширения мозаики 5.90 на дампе ATL06 255k.

Сравнение (все высоты БС-77):
  - продукт 5.90 (2м, юг = prior v2 FABDEM+model, n_cov=0);
  - prior 30м (bareearth_bs77_cm_5.71 = FABDEM+0.008).
Разрезы: все | сезон leaf-off (11-04) | канопи-тир M-CLF (can30<50 / >=50)
| 4 района (dz из dump_south_5.89) | по зонам.
Выходы: results/validate_south_5.90.json, results/south_dump_val_5.90.csv.gz.
"""
import json
from datetime import datetime

import numpy as np
import pandas as pd
import rasterio
import rasterio.windows as W

HOME = '/tmp/my-project/gh-clone'
DUMP = f'{HOME}/scripts/data/icesat2_atl06_dump_south_5.89.csv.gz'
MOS = f'{HOME}/download/mosaic_5.90/spb_hybrid_2m_mosaic_5.90.tif'
MOSQ = f'{HOME}/download/mosaic_5.90/spb_quality_5.90.tif'
FAB = f'{HOME}/scripts/data/consensus_dtm_5.71/bareearth_bs77_cm_5.71.tif'
OUTJ = f'{HOME}/results/validate_south_5.90.json'
OUTC = f'{HOME}/results/south_dump_val_5.90.csv.gz'

DZ = {"Кировский":      (59.83, 30.14, 59.90, 30.32),
      "Красносельский": (59.79, 30.10, 59.88, 30.30),
      "Пушкинский":     (59.70, 30.22, 59.80, 30.45),
      "Колпинский":     (59.70, 30.42, 59.80, 30.62)}


def metrics(dh):
    dh = dh[np.isfinite(dh)]
    if len(dh) == 0:
        return dict(n=0)
    off = float(np.median(dh))
    r = dh - off
    return dict(n=int(dh.size), med=round(off, 3),
                mad=round(float(np.median(np.abs(r))), 3),
                p95=round(float(np.percentile(np.abs(r), 95)), 3),
                share_le_3=round(float((np.abs(dh) <= 3.0).mean()), 3))


def sample_tif(path, lons, lats, band=1, chunk=40000):
    out = np.full(len(lons), np.nan, dtype='float64')
    with rasterio.open(path) as s:
        inv = ~s.transform
        for i0 in range(0, len(lons), chunk):
            i1 = min(i0 + chunk, len(lons))
            cols, rows = inv * (lons[i0:i1], lats[i0:i1])
            cols = np.round(cols).astype('int64')
            rows = np.round(rows).astype('int64')
            inb = ((rows >= 0) & (rows < s.height) &
                   (cols >= 0) & (cols < s.width))
            r = np.clip(rows, 0, s.height - 1)
            c = np.clip(cols, 0, s.width - 1)
            win = W.Window(int(c.min()), int(r.min()),
                           int(c.max() - c.min() + 1),
                           int(r.max() - r.min() + 1))
            a = s.read(band, window=win)
            rr = r[inb] - int(r.min())
            cc = c[inb] - int(c.min())
            vals = a[rr, cc].astype('float64')
            nod = s.nodata
            if nod is not None:
                vals[vals == nod] = np.nan
            out[i0:i1][inb] = vals
    return out


def main():
    t0 = datetime.now()
    d = pd.read_csv(DUMP)
    print(f"дамп: {len(d)}")
    lons = d.lon.values
    lats = d.lat.values

    dem2m = sample_tif(MOS, lons, lats) / 100.0
    can30 = sample_tif(MOSQ, lons, lats, band=3)
    can30 = np.where(can30 >= 65535, np.nan, can30)   # см -> NaN nodata
    # prior 30м (FABDEM, без смещения — как в каноне 5.71/5.83prep)
    fab30 = sample_tif(FAB, lons, lats) / 100.0

    res = dict(date=datetime.now().isoformat(), n_dump=int(len(d)),
               n_covered=int(np.isfinite(dem2m).sum()))
    d['dH2m'] = d.h_baltic.values - dem2m
    d['dH30'] = d.h_baltic.values - fab30
    d['can30_cm'] = can30

    res['product_2m'] = metrics(d.dH2m.values)
    res['prior_30m'] = metrics(d.dH30.values)

    # сезон (11,12,01..04 = leaf-off; канон 5.87)
    m = (d.date.values // 100) % 100
    lo = np.isin(m, [11, 12, 1, 2, 3, 4])
    res['leaf_off'] = metrics(d.dH2m.values[lo])
    res['leaf_on'] = metrics(d.dH2m.values[~lo])

    # канопи-тир (аналог M-CLF: can30<50 -> OK-тир)
    ck = np.isfinite(can30)
    res['can_lt_50'] = metrics(d.dH2m.values[ck & (can30 < 50)])
    res['can_ge_50'] = metrics(d.dH2m.values[ck & (can30 >= 50)])
    res['can_nodata'] = metrics(d.dH2m.values[~ck])

    # районы
    dz_res = {}
    for k, (s_, w_, n_, e_) in DZ.items():
        msk = ((lats >= s_) & (lats <= n_) & (lons >= w_) & (lons <= e_))
        dz_res[k] = dict(product=metrics(d.dH2m.values[msk]),
                         prior30=metrics(d.dH30.values[msk]),
                         leaf_off=metrics(d.dH2m.values[msk & lo]),
                         can_lt_50=metrics(
                             d.dH2m.values[msk & ck & (can30 < 50)]))
    res['districts'] = dz_res

    # по зонам (для отчёта): тег зоны по bbox из журнала
    zres = {}
    for line in open(f'{HOME}/results/batch_progress_5.83.jsonl'):
        try:
            rec = json.loads(line)
        except Exception:
            continue
        tag = rec.get('tag', '')
        if not (len(tag) == 3 and tag[0] == 'S' and tag[1:].isdigit()):
            continue
        w_, s_, e_, n_ = rec['bbox']
        msk = ((lats >= s_) & (lats <= n_) & (lons >= w_) & (lons <= e_))
        mm = metrics(d.dH2m.values[msk])
        if mm.get('n', 0):
            zres[tag] = mm
    res['zones'] = zres
    mads = [v['mad'] for v in zres.values() if 'mad' in v]
    if mads:
        res['zones_mad_summary'] = dict(
            n_zones=len(mads), med=round(float(np.median(mads)), 3),
            min=round(min(mads), 3), max=round(max(mads), 3))

    d[['lat', 'lon', 'date', 'h_baltic', 'h_sigma', 'n_fit', 'rgt', 'cycle',
       'gt', 'dH2m', 'dH30', 'can30_cm']].to_csv(OUTC, index=False,
                                                 compression='gzip')
    json.dump(res, open(OUTJ, 'w'), ensure_ascii=False, indent=1)
    print(json.dumps({k: v for k, v in res.items()
                      if k not in ('zones', 'districts')},
                     ensure_ascii=False, indent=1))
    for k, v in dz_res.items():
        print(f"  {k}: 2m MAD {v['product'].get('mad')} | "
              f"30m {v['prior30'].get('mad')} | can<50 "
              f"{v['can_lt_50'].get('mad')}")
    print(f"ГОТОВО {(datetime.now() - t0).total_seconds():.0f}s")


if __name__ == '__main__':
    main()
