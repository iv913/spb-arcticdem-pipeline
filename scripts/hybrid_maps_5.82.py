#!/usr/bin/env python3
"""
5.82: финальные карты гибрида H2-blend-openoff (промотированный рецепт
5.80-81) в обеих зонах + roundtrip-контроль tif<->JSON.

Рецепт (фиксирован, без пере-подбора):
  1) стек из кэша клипов, фильтр MAD<=1.5 & n>=80 (stats зоны);
  2) per-strip offsets по ОТКРЫТЫМ train-точкам (canopy<1 м) — 5.81;
  3) фьюжия: p25/p50/sigma -> blend w=clip((1.5-sigma)/1,0,1) — 5.80;
  4) H2: prior_v2-30м + med31(clip(residual,±1)) — 5.76.

Карта: Int16, СМ, БС-77, nodata -32768 (конвенция продукта M-CLF).
Quality-канал (uint16 x3): n_cov, sigma_cm, canopy30_cm.

Roundtrip (пре-регистрация 5.82):
  RT-1: |MAD_tif - MAD_mem| <= 0.01 м и |p95_tif - p95_mem| <= 0.02 м
        (квантование см);
  RT-2: |MAD_tif - MAD_5.81| <= 0.01 м (детерминизм пере-фьюжии).
Запуск: python hybrid_maps_5.82.py pilot   (или xzone, или all)
Выход: dem_cache/<zone>/hybrid_final_2m_5.82_<zone>.tif,
       dem_cache/<zone>/quality_5.82_<zone>.tif,
       results/hybrid_maps_5.82.json
"""
import os, sys, json
from pathlib import Path
from datetime import datetime
import numpy as np
import pandas as pd
import rasterio
from rasterio.windows import Window
from rasterio.warp import reproject, Resampling
import geopandas as gpd
import scipy.ndimage as ndi

HOME = Path(__file__).resolve().parents[1]
FAB_TIF = HOME / 'scripts/data/consensus_dtm_5.71/bareearth_bs77_cm_5.71.tif'
TMP = HOME / 'scripts/dem_cache/_glo30_tmp'
PAD30 = 40 * 0.0002777777778
PX = 0.0000178
OUTJ = HOME / 'results/hybrid_maps_5.82.json'
J581 = HOME / 'results/openoffset_5.81.json'
MAD_TOL, N_MIN = 1.5, 80
CAN_OPEN = 1.0
W0_SIG, W1_SIG = 0.5, 1.5
ZONES = {
    'pilot': dict(bbox=(30.24, 60.01, 30.34, 60.06),
                  cache=HOME / 'scripts/dem_cache/strips_pilot_5.75',
                  stats='strip_stats_5.76.csv',
                  prior='prior_v2_2m_5.76.tif',
                  glo=['N59_00_E030_00', 'N60_00_E030_00']),
    'xzone': dict(bbox=(30.12, 60.01, 30.24, 60.06),
                  cache=HOME / 'scripts/dem_cache/strips_zone_5.79',
                  stats='strip_stats_5.79.csv',
                  prior='prior_v2_2m_5.77.tif',  # перенесён из 5.77
                  glo=['N60_00_E030_00']),
}


def log(m):
    print(f"[{datetime.now():%H:%M:%S}] {m}", flush=True)


def read_tif(p):
    with rasterio.open(p) as s:
        a = s.read(1).astype('float32')
        a[a == s.nodata] = np.nan
        return a, s.transform


def metrics(d):
    d = d[np.isfinite(d)]
    off = float(np.median(d))
    dd = d - off
    return dict(resid_off=round(off, 3),
                mad=round(float(np.median(np.abs(dd))), 3),
                p95=round(float(np.percentile(np.abs(dd), 95)), 3),
                n=int(d.size))


def canopy_base(zone):
    """30м сетка канопи (GLO30 - FABDEM) в окне зоны + PAD30."""
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
    return wtr, gcrs, dsm - fab


def canopy_at_points(zone, lons, lats):
    wtr, gcrs, canopy = canopy_base(zone)
    r30, c30 = rasterio.transform.rowcol(wtr, lons, lats)
    r30 = np.clip(r30, 0, canopy.shape[0] - 1)
    c30 = np.clip(c30, 0, canopy.shape[1] - 1)
    return canopy[r30, c30].astype('float32')


def filtered(resid_f, size=31, clip=1.0, ch=512):
    """med31 по блокам строк (mode='nearest' == pad edge), стыкуемо."""
    H, W = resid_f.shape
    h = size // 2
    out = np.empty((H, W), dtype='float32')
    for r0 in range(0, H, ch):
        r1 = min(H, r0 + ch)
        lo, hi = max(r0 - h, 0), min(r1 + h, H)
        src = np.clip(resid_f[lo:hi], -clip, clip)
        out[r0:r1] = ndi.median_filter(src, size=size, mode='nearest')[
            r0 - lo:r1 - lo]
        if r0 % 1536 == 0:
            log(f"  medf {size}px: {r0}/{H}")
    return out


def run_zone(name):
    zone = ZONES[name]
    B, cache = zone['bbox'], zone['cache']
    t0 = datetime.now()
    ic = pd.read_csv(HOME / 'scripts/data/icesat2_clean.csv')
    ic = ic[(ic.lon >= B[0]) & (ic.lon <= B[2]) &
            (ic.lat >= B[1]) & (ic.lat <= B[3])].copy()
    blk = (np.floor(ic.lat / 0.02).astype(int) +
           np.floor(ic.lon / 0.0127).astype(int))
    ic['half'] = blk % 2
    tr_mask = (ic.half == 0).values
    ic['can'] = canopy_at_points(zone, ic.lon.values, ic.lat.values)
    pts = gpd.GeoSeries(gpd.points_from_xy(ic.lon, ic.lat), crs='EPSG:4326')
    open_tr = tr_mask & (ic.can.values < CAN_OPEN)

    st = pd.read_csv(cache / zone['stats'])
    st['clip_path'] = st['clip'].apply(lambda s: cache / s)
    keep = st[(st.mad <= MAD_TOL) & (st.n >= N_MIN)].reset_index(drop=True)
    offs_new = []
    for _, row in keep.iterrows():
        with rasterio.open(row.clip_path) as s:
            a = s.read(1).astype('float32')
            a[a == -32768] = np.nan
            px = pts.to_crs(s.crs)
            r2, c2 = rasterio.transform.rowcol(
                s.transform, px.x.values, px.y.values)
            inb = (r2 >= 0) & (r2 < s.height) & (c2 >= 0) & (c2 < s.width)
            r2 = np.clip(r2, 0, s.height - 1)
            c2 = np.clip(c2, 0, s.width - 1)
            hs = np.where(inb, a[r2, c2], np.nan)
        d = hs[open_tr] - ic.h_icesat_baltic.values[open_tr]
        d = d[np.isfinite(d)]
        offs_new.append(float(np.median(d)) if len(d) else float(row.off))
    offs_new = np.array(offs_new)
    log(f"[{name}] kept {len(keep)}, открытых train {int(open_tr.sum())}")

    # --- фьюжия (recipe 5.80/5.81, детерминизм) ---
    W = int((B[2] - B[0]) / PX)
    H = int((B[3] - B[1]) / PX)
    trf = rasterio.transform.from_origin(B[0], B[3], PX, PX)
    layers = np.full((len(keep), H, W), np.nan, dtype='float32')
    for k, (_, row) in enumerate(keep.iterrows()):
        with rasterio.open(row.clip_path) as s:
            a = s.read(1).astype('float32')
            a[a == -32768] = np.nan
            if not np.isfinite(a).any():
                continue
            buf = np.full((H, W), np.nan, dtype='float32')
            reproject(a - offs_new[k], buf,
                      src_transform=s.transform, src_crs=s.crs,
                      dst_transform=trf, dst_crs='EPSG:4326',
                      resampling=Resampling.bilinear, dst_nodata=np.nan)
            layers[k] = buf
    b = np.where(np.isfinite(layers), layers, np.inf).astype('float32')
    b.sort(axis=0)
    fin = np.isfinite(layers)
    n_valid = fin.sum(axis=0)
    n_layers = layers.shape[0]
    outs = {}
    for p in (25, 50):
        rank = np.clip(np.floor((n_valid - 1) * p / 100.0), 0,
                       n_layers - 1).astype('int64')
        outs[p] = np.take_along_axis(b, rank[None], axis=0)[0]
        outs[p][n_valid == 0] = np.nan
    cnt = np.maximum(n_valid, 1).astype('float32')
    s1 = np.where(fin, layers, 0.0).sum(axis=0)
    s2 = np.where(fin, layers * layers, 0.0).sum(axis=0)
    sigma = np.sqrt(np.maximum(s2 / cnt - (s1 / cnt) ** 2, 0))
    sigma[n_valid == 0] = np.nan
    del layers, b, fin, s1, s2
    w = np.clip((W1_SIG - sigma) / (W1_SIG - W0_SIG), 0, 1).astype('float32')
    blend = np.where(np.isfinite(outs[25]) | np.isfinite(outs[50]),
                     w * np.nan_to_num(outs[50]) +
                     (1 - w) * np.nan_to_num(outs[25]), np.nan)
    del outs

    # --- H2 карта ---
    prior, _ = read_tif(cache / zone['prior'])
    r, c = rasterio.transform.rowcol(trf, ic.lon.values, ic.lat.values)
    r = np.clip(r, 0, H - 1); c = np.clip(c, 0, W - 1)
    pr = prior[r, c] - ic.h_icesat_baltic.values
    resid = blend - prior
    resid_f = np.where(np.isfinite(resid), resid, 0.0).astype('float32')
    del resid
    m1 = filtered(resid_f, 31, 1.0)
    del resid_f
    hybrid = prior + m1
    del m1
    hv = ~tr_mask
    hold_mem = dict(H0_prior=metrics(pr[hv]),
                    H2_blend_openoff=metrics((hybrid[r, c] -
                                              ic.h_icesat_baltic.values)[hv]))

    # --- запись карты (Int16, СМ, БС-77) ---
    prof = dict(driver='GTiff', width=W, height=H, count=1, dtype='int16',
                crs='EPSG:4326', transform=trf, compress='deflate',
                nodata=-32768)
    map_p = cache / f'hybrid_final_2m_5.82_{name}.tif'
    arr16 = np.where(np.isfinite(hybrid), np.rint(hybrid * 100.0),
                     -32768).astype('int16')
    with rasterio.open(map_p, 'w', **prof) as d:
        d.write(arr16, 1)
    del arr16

    # --- quality-канал (uint16 x3: n_cov, sigma_cm, canopy30_cm) ---
    wtr30, gcrs30, canopy30 = canopy_base(zone)
    can2m = np.full((H, W), np.nan, dtype='float32')
    reproject(canopy30, can2m, src_transform=wtr30, src_crs=gcrs30,
              dst_transform=trf, dst_crs='EPSG:4326',
              resampling=Resampling.bilinear, dst_nodata=np.nan)
    nd16 = 65535
    q = np.full((3, H, W), nd16, dtype='uint16')
    q[0] = np.where(n_valid > 0, np.minimum(n_valid, nd16 - 1), nd16)
    q[1] = np.where(np.isfinite(sigma),
                    np.minimum(np.rint(sigma * 100), nd16 - 1), nd16)
    q[2] = np.where(np.isfinite(can2m),
                    np.clip(np.rint(can2m * 100), 0, nd16 - 1), nd16)
    qprof = dict(driver='GTiff', width=W, height=H, count=3, dtype='uint16',
                 crs='EPSG:4326', transform=trf, compress='deflate',
                 nodata=nd16)
    with rasterio.open(cache / f'quality_5.82_{name}.tif', 'w', **qprof) as d:
        d.write(q)
    del q, can2m, canopy30, sigma, n_valid, w, blend, prior

    # --- roundtrip: перечитать tif и пере-оценить holdout ---
    with rasterio.open(map_p) as s:
        a16 = s.read(1)
    val = np.where(a16 == -32768, np.nan, a16 / 100.0).astype('float32')
    hold_rt = dict(H2_blend_openoff=metrics(
        (val[r, c] - ic.h_icesat_baltic.values)[hv]))
    hm = hold_mem['H2_blend_openoff']; hr = hold_rt['H2_blend_openoff']
    d_mad = abs(hm['mad'] - hr['mad'])
    d_p95 = abs(hm['p95'] - hr['p95'])
    rt1 = bool(d_mad <= 0.01 and d_p95 <= 0.02)
    j581 = json.loads(J581.read_text())
    h581 = j581['zones'][name]['holdout']['H2_blend_openoff']
    rt2 = bool(abs(h581['mad'] - hr['mad']) <= 0.01 and
               abs(h581['p95'] - hr['p95']) <= 0.02)
    log(f"[{name}] RT-1 tif<->mem: dMAD {d_mad:.3f} dp95 {d_p95:.3f} "
        f"-> {'OK' if rt1 else 'FAIL'}; "
        f"RT-2 vs 5.81: MAD {h581['mad']}->{hr['mad']} "
        f"p95 {h581['p95']}->{hr['p95']} -> {'OK' if rt2 else 'FAIL'}; "
        f"{(datetime.now()-t0).total_seconds()/60:.1f} min")
    return dict(cache=str(map_p.relative_to(HOME)),
                quality=str((cache / f'quality_5.82_{name}.tif')
                            .relative_to(HOME)),
                n_kept=int(len(keep)), grid=[H, W],
                holdout_mem=hold_mem, holdout_tif=hold_rt,
                roundtrip=dict(rt1_tif_mem=bool(rt1), d_mad=round(d_mad, 4),
                               d_p95=round(d_p95, 4), rt2_vs581=bool(rt2),
                               json581=h581),
                minutes=round((datetime.now() - t0).total_seconds() / 60, 1))


def main():
    os.environ['AWS_NO_SIGN_REQUEST'] = 'YES'
    which = sys.argv[1] if len(sys.argv) > 1 else 'all'
    names = list(ZONES) if which == 'all' else [which]
    res = {'recipe': 'leaf-off стек зоны -> open-offsets(5.81) -> blend '
                     'p25/p50(5.80) -> H2 med31_clip1; карта Int16 см БС-77',
           'rt_rule': 'RT-1 |dMAD|<=0.01, |dp95|<=0.02; RT-2 vs 5.81 |dMAD|'
                      '<=0.01, |dp95|<=0.02',
           'zones': {}}
    for n in names:
        res['zones'][n] = run_zone(n)
        OUTJ.write_text(json.dumps(res, ensure_ascii=False, indent=1))
    ok = all(z['roundtrip']['rt1_tif_mem'] and z['roundtrip']['rt2_vs581']
             for z in res['zones'].values())
    log(f"ROUNDTRIP: {'OK' if ok else 'FAIL'} — "
        f"{', '.join(res['zones'])}")


if __name__ == '__main__':
    main()
