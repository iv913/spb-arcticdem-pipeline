#!/usr/bin/env python3
"""
5.81: per-strip offsets только по ОТКРЫТЫМ train-точкам (канопи<1 м).

Механизм (диагностика 5.80/diag_prior_signed): стрипы ArcticDEM — DSM;
offset = med(strip − ICESat) по всем train-точкам смещён ВВЕРХ лесными
точками (крона в стрипе против земли в ICESat) -> после вычитания offset
открытые участки занижены (-0.38..-0.52 при честном prior -0.03..-0.11).
Фикс: off_new = med(strip − ICESat | train & canopy<1) — целевой датум
продукта bare-earth считается по открытым участкам.

Пайплайн: клипы из кэшей (без скачиваний), off_new, пере-фьюжия
(сортированный стек -> p25/p50/sigma) -> blend (w=clip((1.5-σ)/1.0,0,1))
-> H2 (med31_clip1 residual) -> holdout обеих зон.
ПРОМОЦИЯ (пре-рег.): H2_blend MAD<=prior И p95<=prior в ОБЕИХ зонах.
Выход: results/openoffset_5.81.json
"""
import os, json
from pathlib import Path
from datetime import datetime
import numpy as np
import pandas as pd
import rasterio
from rasterio.windows import Window
from rasterio.warp import reproject, Resampling
import geopandas as gpd

HOME = Path(__file__).resolve().parents[1]
FAB_TIF = HOME / 'scripts/data/consensus_dtm_5.71/bareearth_bs77_cm_5.71.tif'
TMP = HOME / 'scripts/dem_cache/_glo30_tmp'
PAD30 = 40 * 0.0002777777778
PX = 0.0000178
OUTJ = HOME / 'results/openoffset_5.81.json'
ZONES = {
    'pilot': dict(bbox=(30.24, 60.01, 30.34, 60.06),
                  cache=HOME / 'scripts/dem_cache/strips_pilot_5.75',
                  stats='strip_stats_5.76.csv', tag='5.76',
                  glo=['N59_00_E030_00', 'N60_00_E030_00']),
    'xzone': dict(bbox=(30.12, 60.01, 30.24, 60.06),
                  cache=HOME / 'scripts/dem_cache/strips_zone_5.79',
                  stats='strip_stats_5.79.csv', tag='5.79',
                  glo=['N60_00_E030_00'],
                  prior=HOME / 'scripts/dem_cache/strips_zone_5.77/prior_v2_2m_5.77.tif'),
}
MAD_TOL, N_MIN = 1.5, 80
CAN_OPEN = 1.0
W0_SIG, W1_SIG = 0.5, 1.5


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


def canopy_at_points(zone, lons, lats):
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
    r30, c30 = rasterio.transform.rowcol(wtr, lons, lats)
    r30 = np.clip(r30, 0, canopy.shape[0] - 1)
    c30 = np.clip(c30, 0, canopy.shape[1] - 1)
    return canopy, canopy[r30, c30].astype('float32')


def patch_med(resid_f, r, c, s=31, clip=1.0):
    h = s // 2
    pad = np.pad(np.clip(resid_f, -clip, clip), h, mode='edge')
    out = np.empty(len(r), dtype='float32')
    for i, (ri, ci) in enumerate(zip(r, c)):
        out[i] = np.median(pad[ri:ri + 2 * h + 1, ci:ci + 2 * h + 1])
    return out


def main():
    t00 = datetime.now()
    os.environ['AWS_NO_SIGN_REQUEST'] = 'YES'
    res = dict(recipe='offsets по открытым train-точкам (canopy<1м); '
                      'blend w=clip((1.5-sigma)/1,0,1); H2 med31_clip1',
               zones={})
    hold_all = {}
    for name, zone in ZONES.items():
        cache = zone['cache']
        ic = pd.read_csv(HOME / 'scripts/data/icesat2_clean.csv')
        B = zone['bbox']
        ic = ic[(ic.lon >= B[0]) & (ic.lon <= B[2]) &
                (ic.lat >= B[1]) & (ic.lat <= B[3])].copy()
        blk = (np.floor(ic.lat / 0.02).astype(int) +
               np.floor(ic.lon / 0.0127).astype(int))
        ic['half'] = blk % 2
        tr_mask = (ic.half == 0).values
        _, can_pt = canopy_at_points(zone, ic.lon.values, ic.lat.values)
        ic['can'] = can_pt
        pts = gpd.GeoSeries(gpd.points_from_xy(ic.lon, ic.lat), crs='EPSG:4326')
        open_tr = tr_mask & (ic.can.values < CAN_OPEN)
        log(f"[{name}] train {tr_mask.sum()}, открытых train "
            f"{open_tr.sum()} (canopy<{CAN_OPEN})")

        st = pd.read_csv(cache / zone['stats'])
        st['clip_path'] = st['clip'].apply(lambda s: cache / s)
        keep = st[(st.mad <= MAD_TOL) & (st.n >= N_MIN)].reset_index(drop=True)
        # старые offsets (по всем train) из stats
        offs_old = keep.off.values.astype('float64')

        # off_new: медиана (strip − icesat) на открытых train-точках
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
        shift = offs_old - offs_new
        log(f"[{name}] shift off_old-off_new: med {np.median(shift):+.2f} "
            f"м (min {shift.min():+.2f}, max {shift.max():+.2f})")

        # пере-фьюжия kept клипов с off_new (raw = старый слой + off_old)
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
        del layers, b
        w = np.clip((W1_SIG - sigma) / (W1_SIG - W0_SIG), 0, 1).astype('float32')
        blend = np.where(np.isfinite(outs[25]) | np.isfinite(outs[50]),
                         w * np.nan_to_num(outs[50]) +
                         (1 - w) * np.nan_to_num(outs[25]), np.nan)

        # оценка
        prior, _ = read_tif(zone.get('prior') or
                            cache / f'prior_v2_2m_{zone["tag"]}.tif')
        r, c = rasterio.transform.rowcol(trf, ic.lon.values, ic.lat.values)
        r = np.clip(r, 0, H - 1); c = np.clip(c, 0, W - 1)
        pr = prior[r, c] - ic.h_icesat_baltic.values
        resid = blend - prior
        m_bl = patch_med(np.where(np.isfinite(resid), resid, 0.0)
                         .astype('float32'), r, c, 31, clip=1.0)
        hv = ~tr_mask.values if hasattr(tr_mask, 'values') else ~tr_mask
        hold = dict(H0_prior=metrics(pr[hv]),
                    H2_blend_openoff=metrics((pr + m_bl)[hv]))
        hold_all[name] = hold
        res['zones'][name] = dict(
            n_kept=int(len(keep)), open_train=int(open_tr.sum()),
            shift_med=round(float(np.median(shift)), 3),
            holdout=hold,
            resid_by_canopy=None)
        # resid по бакетам (blend, новые offsets)
        v = ~tr_mask
        bk = pd.cut(ic.can.values[v], [0, 0.5, 1, 2, 4, 30],
                    labels=['0-0.5', '0.5-1', '1-2', '2-4', '4+'])
        g = pd.DataFrame(dict(b=bk, rr=resid[r, c][v])).groupby(
            'b', observed=True).rr.agg(
            [('n', 'size'),
             ('resid_med', lambda x: round(float(np.nanmedian(x)), 3))])
        res['zones'][name]['resid_by_canopy'] = g.reset_index().to_dict('records')
        log(f"[{name}] HOLDOUT: " +
            " | ".join(f"{n} MAD {m['mad']:.3f} p95 {m['p95']:.2f} "
                       f"off {m['resid_off']:+.2f}" for n, m in hold.items()))

    hp, hx = hold_all['pilot'], hold_all['xzone']
    ok_p = (hp['H2_blend_openoff']['mad'] <= hp['H0_prior']['mad'] and
            hp['H2_blend_openoff']['p95'] <= hp['H0_prior']['p95'])
    ok_x = (hx['H2_blend_openoff']['mad'] <= hx['H0_prior']['mad'] and
            hx['H2_blend_openoff']['p95'] <= hx['H0_prior']['p95'])
    res['decision'] = dict(rule='MAD<=prior И p95<=prior в обеих зонах',
                           pilot=bool(ok_p), xzone=bool(ok_x),
                           promoted=bool(ok_p and ok_x))
    OUTJ.write_text(json.dumps(res, ensure_ascii=False, indent=1))
    log(f"ПРОМОЦИЯ H2-blend(open-offsets): {'ДА' if ok_p and ok_x else 'НЕТ'} "
        f"(пилот {ok_p}, кросс {ok_x}); "
        f"{(datetime.now()-t00).total_seconds()/60:.1f} min")


if __name__ == '__main__':
    main()
