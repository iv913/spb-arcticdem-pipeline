#!/usr/bin/env python3
"""
5.80 диагностика: разложение открытого смещения — стек или prior?

resid = p25/blend − prior = −0.38..−0.52 в открытых ячейках после blend.
Если prior сам смещён ВВЕРХ в открытых ячейках (signed med(prior−icesat) >
0), то отрицательный resid = стек КОРРЕКТИРУЕТ приора вниз к истине
(и потому H2_blend бьёт prior на MAD в пилоте). Если prior там честен —
стек смещён вниз (offsets/квантиль).
Выход: results/diag_prior_signed_5.80.json
"""
import json
from pathlib import Path
import numpy as np
import pandas as pd
import rasterio
from rasterio.windows import Window
from rasterio.warp import reproject, Resampling

HOME = Path(__file__).resolve().parents[1]
FAB_TIF = HOME / 'scripts/data/consensus_dtm_5.71/bareearth_bs77_cm_5.71.tif'
TMP = HOME / 'scripts/dem_cache/_glo30_tmp'
PAD30 = 40 * 0.0002777777778
OUTJ = HOME / 'results/diag_prior_signed_5.80.json'
ZONES = {
    'pilot': dict(bbox=(30.24, 60.01, 30.34, 60.06),
                  cache=HOME / 'scripts/dem_cache/strips_pilot_5.75',
                  tag='5.76', glo=['N59_00_E030_00', 'N60_00_E030_00']),
    'xzone': dict(bbox=(30.12, 60.01, 30.24, 60.06),
                  cache=HOME / 'scripts/dem_cache/strips_zone_5.79',
                  tag='5.79', glo=['N60_00_E030_00'],
                  prior=HOME / 'scripts/dem_cache/strips_zone_5.77/prior_v2_2m_5.77.tif'),
}


def read_tif(p):
    with rasterio.open(p) as s:
        a = s.read(1).astype('float32')
        a[a == s.nodata] = np.nan
        return a, s.transform


def main():
    out = {}
    for name, zone in ZONES.items():
        prior, trf = read_tif(zone.get('prior') or
                              zone['cache'] / f'prior_v2_2m_{zone["tag"]}.tif')
        # канопи 30м в точках
        B = zone['bbox']
        with rasterio.open(FAB_TIF) as src:
            full = src.read(1).astype('float32')
            nd = src.nodata
            gtr, gcrs = src.transform, src.crs
            GH, GW = src.shape
        fab_full = full / 100.0
        fab_full[full == nd] = np.nan
        r_lo, c_lo = rasterio.transform.rowcol(gtr, B[0] - PAD30, B[3] + PAD30)
        r_hi, c_hi = rasterio.transform.rowcol(gtr, B[2] + PAD30, B[1] - PAD30)
        r0, r1 = max(int(r_lo) - 2, 0), min(int(r_hi) + 2, GH)
        c0, c1 = max(int(c_lo) - 2, 0), min(int(c_hi) + 2, GW)
        fab = fab_full[r0:r1, c0:c1]
        wtr = rasterio.windows.transform(Window(c0, r0, c1 - c0, r1 - r0), gtr)
        dsm = np.full(fab.shape, np.nan, dtype='float32')
        for t in zone['glo']:
            with rasterio.open(TMP / f"{t}.tif") as s:
                a = s.read(1).astype('float32')
                buf = np.full(fab.shape, np.nan, dtype='float32')
                reproject(a, buf, src_transform=s.transform, src_crs=s.crs,
                          dst_transform=wtr, dst_crs=gcrs,
                          resampling=Resampling.bilinear, dst_nodata=np.nan)
                dsm = np.where(np.isfinite(buf) & ~np.isfinite(dsm), buf, dsm)
        canopy = dsm - fab

        ic = pd.read_csv(HOME / 'scripts/data/icesat2_clean.csv')
        ic = ic[(ic.lon >= B[0]) & (ic.lon <= B[2]) &
                (ic.lat >= B[1]) & (ic.lat <= B[3])]
        blk = (np.floor(ic.lat / 0.02).astype(int) +
               np.floor(ic.lon / 0.0127).astype(int))
        tr_mask = ((blk % 2) == 0).values
        r, c = rasterio.transform.rowcol(trf, ic.lon.values, ic.lat.values)
        r = np.clip(r, 0, prior.shape[0] - 1)
        c = np.clip(c, 0, prior.shape[1] - 1)
        r30, c30 = rasterio.transform.rowcol(wtr, ic.lon.values, ic.lat.values)
        r30 = np.clip(r30, 0, canopy.shape[0] - 1)
        c30 = np.clip(c30, 0, canopy.shape[1] - 1)
        d_prior = prior[r, c] - ic.h_icesat_baltic.values
        v = ~tr_mask
        bk = pd.cut(canopy[r30, c30][v], [0, 0.5, 1, 2, 4, 30],
                    labels=['0-0.5', '0.5-1', '1-2', '2-4', '4+'])
        g = pd.DataFrame(dict(b=bk, d=d_prior[v])).groupby(
            'b', observed=True).d.agg(
            [('n', 'size'), ('signed_med', lambda x: round(float(np.median(x)), 3))])
        out[name] = g.reset_index().to_dict('records')
        print(f"--- {name}: signed med(prior − ICESat) по канопи ---")
        print(g.to_string())
    OUTJ.write_text(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
