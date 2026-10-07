#!/usr/bin/env python3
"""
5.77 диагностика: почему гибрид проигрывает prior на MAD в кросс-зоне.

Гипотеза: sigma-гейт ловит только НЕСОГЛАСОВАННЫЙ шум стека; крона,
присутствующая во ВСЕХ сценах (leaf-on 2013/2018/2023 в отборе),
систематична -> низкая sigma -> гейт пропускает -> residual несёт.bias
кроны. Проверка: корреляция |residual| и вклада residual с канопи
(GLO-30 - FABDEM) и sigma в точках holdout; группировка по канопи.
Только диагностика — рецепт не меняем (без нарушения протокола).
"""
import json
from pathlib import Path
import numpy as np
import pandas as pd
import rasterio
import scipy.ndimage as ndi

HOME = Path('/home/z/my-project/spb-arcticdem-pipeline')
ZONE = (30.12, 60.01, 30.24, 60.06)
CACHE = HOME / 'scripts/dem_cache/strips_zone_5.77'
OUTJ = HOME / 'results/diag_canopy_gate_5.77.json'

FAB_TIF = HOME / 'scripts/data/consensus_dtm_5.71/bareearth_bs77_cm_5.71.tif'
GLO = HOME / 'scripts/dem_cache/_glo30_tmp/N60_00_E030_00.tif'


def read_tif(p):
    with rasterio.open(p) as s:
        a = s.read(1).astype('float32')
        a[a == s.nodata] = np.nan
        return a, s.transform


def main():
    p25, tr = read_tif(CACHE / 'fused_q_p25_5.77.tif')
    prior, _ = read_tif(CACHE / 'prior_v2_2m_5.77.tif')
    sigma = np.load(CACHE / 'sigma_5.77.npy')
    H, W = p25.shape

    # канопи 30м на окне зоны (быстрая репликация шага B)
    with rasterio.open(FAB_TIF) as src:
        full = src.read(1).astype('float32')
        nd = src.nodata
        gtr, gcrs = src.transform, src.crs
        GH, GW = src.shape
    fab_full = full / 100.0
    fab_full[full == nd] = np.nan
    W0, S0, E0, N0 = ZONE
    P = 40 * 0.0002777777778
    r_lo, c_lo = rasterio.transform.rowcol(gtr, W0 - P, N0 + P)
    r_hi, c_hi = rasterio.transform.rowcol(gtr, E0 + P, S0 - P)
    r0, r1 = max(int(r_lo) - 2, 0), min(int(r_hi) + 2, GH)
    c0, c1 = max(int(c_lo) - 2, 0), min(int(c_hi) + 2, GW)
    fab = fab_full[r0:r1, c0:c1]
    wtr = rasterio.windows.transform(
        Window(c0, r0, c1 - c0, r1 - r0), gtr)
    os_env = dict(AWS_NO_SIGN_REQUEST='YES')
    dsm = np.full(fab.shape, np.nan, dtype='float32')
    with rasterio.open(GLO) as s:
        a = s.read(1).astype('float32')
        buf = np.full(fab.shape, np.nan, dtype='float32')
        from rasterio.warp import reproject, Resampling
        reproject(a, buf, src_transform=s.transform, src_crs=s.crs,
                  dst_transform=wtr, dst_crs=gcrs,
                  resampling=Resampling.bilinear, dst_nodata=np.nan)
        dsm = np.where(np.isfinite(buf) & ~np.isfinite(dsm), buf, dsm)
    canopy30 = dsm - fab  # 30 м, окно зоны

    ic = pd.read_csv(HOME / 'scripts/data/icesat2_clean.csv')
    ic = ic[(ic.lon >= ZONE[0]) & (ic.lon <= ZONE[2]) &
            (ic.lat >= ZONE[1]) & (ic.lat <= ZONE[3])].copy()
    blk = (np.floor(ic.lat / 0.02).astype(int) +
           np.floor(ic.lon / 0.0127).astype(int))
    tr_mask = ((blk % 2) == 0).values
    rr, cc = rasterio.transform.rowcol(tr, ic.lon.values, ic.lat.values)
    rr = np.clip(rr, 0, H - 1); cc = np.clip(cc, 0, W - 1)
    r30, c30 = rasterio.transform.rowcol(wtr, ic.lon.values, ic.lat.values)
    r30 = np.clip(r30, 0, fab.shape[0] - 1)
    c30 = np.clip(c30, 0, fab.shape[1] - 1)

    h_ref = ic.h_icesat_baltic.values
    resid = (p25 - prior)[rr, cc]
    sig = sigma[rr, cc]
    can = canopy30[r30, c30]
    d_prior = prior[rr, cc] - h_ref
    v = ~tr_mask

    out = dict(
        n_holdout=int(v.sum()),
        corr_absresid_canopy=round(float(np.corrcoef(
            np.abs(resid[v]), can[v])[0, 1]), 3),
        corr_absresid_sigma=round(float(np.corrcoef(
            np.abs(resid[v]), sig[v])[0, 1]), 3),
        corr_absdH_prior_canopy=round(float(np.corrcoef(
            np.abs(d_prior[v]), can[v])[0, 1]), 3))
    # группировка по канопи-бакетам: где prior лучше/хуже p25
    bins = [0, 0.5, 1, 2, 4, 30]
    lab = ['0-0.5', '0.5-1', '1-2', '2-4', '4+']
    bk = pd.cut(can[v], bins, labels=lab)
    g = pd.DataFrame(dict(bucket=bk,
                          mad_prior=np.abs(d_prior[v] - np.median(d_prior[v])),
                          mad_p25=np.abs(resid[v] - np.nanmedian(resid[v])),
                          sigma=sig[v], resid=resid[v]))
    tab = g.groupby('bucket', observed=True).agg(
        n=('sigma', 'size'),
        mad_prior=('mad_prior', 'median'),
        mad_p25=('mad_p25', 'median'),
        sigma_med=('sigma', 'median'),
        resid_med=('resid', 'median'))
    out['by_canopy'] = tab.round(3).reset_index().to_dict('records')
    # доля leaf-on сцен в отборе (из strip_stats)
    st = pd.read_csv(CACHE / 'strip_stats_5.77.csv')
    keep = st[st.mad <= 1.5]
    out['kept_scenes'] = keep[['date', 'off', 'mad', 'n']].to_dict('records')
    OUTJ.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
