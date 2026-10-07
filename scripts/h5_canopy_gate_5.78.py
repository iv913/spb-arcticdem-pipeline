#!/usr/bin/env python3
"""
5.78: H5 — гейт residual по КАНОПИ (пре-регистрация из 5.77 п.5/диагностики).

Обоснование: corr(|residual|, канопи)=0.395 vs corr(|residual|, sigma)=0.057
в кросс-зоне; sigma слепа к системному leaf-on bias кроны; «мёртвая зона»
гейта — канопи 1-4 м. Канопи (GLO-30 − FABDEM, 30 м) уже есть в пайплайне
приора — гейт бесплатный.

Протокол:
  ЭТАП 1 (пилот «Северный пояс», 30.24-30.34E 60.01-60.06N):
    отбор ТОЛЬКО на train (зебра 2км, score = MAD + 0.25*p95):
      H5a: prior + med31_clip2(r) * exp(-(canopy/sigma_c)^2), sigma_c in {0.5,1,2}
      H5b: H5a * exp(-(sigma/1)^2)  (канопи+сигма гейты)
    vs baselines H0 (prior), H2 (протокол 5.76), H3 (кандидат 5.76).
    Холдаут пилота — отчёт победителя.
  ЭТАП 2 (кросс-зона «Сестрорецк-запад», фикс. рецепт без пере-подбора):
    тот же sigma_c на холдауте кросс-зоны.
  ПРОМОЦИЯ (правило 5.77 усилено): MAD(H5)<=MAD(H0) И p95(H5)<=p95(H0)
    в ОБЕИХ зонах.
Выход: results/h5_canopy_gate_5.78.json
"""
import json
from pathlib import Path
from datetime import datetime
import numpy as np
import pandas as pd
import rasterio
from rasterio.windows import Window
from rasterio.warp import reproject, Resampling

HOME = Path(__file__).resolve().parents[1]
FAB_TIF = HOME / 'scripts/data/consensus_dtm_5.71/bareearth_bs77_cm_5.71.tif'
TMP = HOME / 'scripts/dem_cache/_glo30_tmp'
ZONES = {
    'pilot': dict(bbox=(30.24, 60.01, 30.34, 60.06),
                  cache=HOME / 'scripts/dem_cache/strips_pilot_5.75',
                  tag='5.76', glo=['N59_00_E030_00', 'N60_00_E030_00']),
    'xzone': dict(bbox=(30.12, 60.01, 30.24, 60.06),
                  cache=HOME / 'scripts/dem_cache/strips_zone_5.77',
                  tag='5.77', glo=['N60_00_E030_00']),
}
PAD30 = 40 * 0.0002777777778
OUTJ = HOME / 'results/h5_canopy_gate_5.78.json'


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


def canopy_at_points(zone):
    """канопи 30м (GLO-30 − FABDEM) в точках зоны + окно FabDEM"""
    W0, S0, E0, N0 = zone['bbox']
    with rasterio.open(FAB_TIF) as src:
        full = src.read(1).astype('float32')
        nd = src.nodata
        gtr, gcrs = src.transform, src.crs
        GH, GW = src.shape
    fab_full = full / 100.0
    fab_full[full == nd] = np.nan
    r_lo, c_lo = rasterio.transform.rowcol(gtr, W0 - PAD30, N0 + PAD30)
    r_hi, c_hi = rasterio.transform.rowcol(gtr, E0 + PAD30, S0 - PAD30)
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
    return canopy, wtr


def patch_med(resid_f, r, c, s=31, clip=2.0):
    h = s // 2
    pad = np.pad(np.clip(resid_f, -clip, clip), h, mode='edge')
    out = np.empty(len(r), dtype='float32')
    for i, (ri, ci) in enumerate(zip(r, c)):
        out[i] = np.median(pad[ri:ri + 2 * h + 1, ci:ci + 2 * h + 1])
    return out


def zone_data(name, zone):
    p25, tr = read_tif(zone['cache'] / f'fused_q_p25_{zone["tag"]}.tif')
    prior, _ = read_tif(zone['cache'] / f'prior_v2_2m_{zone["tag"]}.tif')
    sigma = np.load(zone['cache'] / f'sigma_{zone["tag"]}.npy')
    n_cov = np.load(zone['cache'] / f'n_cov_{zone["tag"]}.npy')
    H, W = p25.shape
    ic = pd.read_csv(HOME / 'scripts/data/icesat2_clean.csv')
    W0, S0, E0, N0 = zone['bbox']
    ic = ic[(ic.lon >= W0) & (ic.lon <= E0) & (ic.lat >= S0) & (ic.lat <= N0)]
    blk = (np.floor(ic.lat / 0.02).astype(int) +
           np.floor(ic.lon / 0.0127).astype(int))
    tr_mask = ((blk % 2) == 0).values
    r, c = rasterio.transform.rowcol(tr, ic.lon.values, ic.lat.values)
    r = np.clip(r, 0, H - 1); c = np.clip(c, 0, W - 1)
    canopy, wtr = canopy_at_points(zone)
    r30, c30 = rasterio.transform.rowcol(wtr, ic.lon.values, ic.lat.values)
    r30 = np.clip(r30, 0, canopy.shape[0] - 1)
    c30 = np.clip(c30, 0, canopy.shape[1] - 1)
    can_pt = canopy[r30, c30].astype('float32')
    resid = p25 - prior
    resid_f = np.where(np.isfinite(resid), resid, 0.0).astype('float32')
    g_sig = np.where(n_cov > 0, np.exp(-(sigma / 1.0) ** 2), 1.0).astype('float32')
    g_sig[np.isnan(g_sig)] = 1.0
    return dict(name=name, p25=p25, prior=prior, sigma=sigma, ic=ic,
                tr_mask=tr_mask, r=r, c=c, can_pt=can_pt, resid_f=resid_f,
                g_sig_pt=g_sig[r, c], h_ref=ic.h_icesat_baltic.values)


def main():
    t00 = datetime.now()
    z_p = zone_data('pilot', ZONES['pilot'])
    log("пилот загружен")
    pr_p = z_p['prior'][z_p['r'], z_p['c']] - z_p['h_ref']
    m31 = patch_med(z_p['resid_f'], z_p['r'], z_p['c'], 31, clip=2.0)
    tr = z_p['tr_mask']

    # --- ЭТАП 1: отбор на train ПИЛОТА ---
    cands = {'H0_prior30m': pr_p.copy(),
             'H2_med31_clip1': pr_p + patch_med(
                 z_p['resid_f'], z_p['r'], z_p['c'], 31, clip=1.0),
             'H3_med31_clip2_gsig1': pr_p + m31 * z_p['g_sig_pt']}
    for sc in (0.5, 1.0, 2.0):
        g_can = np.exp(-(z_p['can_pt'] / sc) ** 2).astype('float32')
        cands[f'H5a_c{sc:g}'] = pr_p + m31 * g_can
        cands[f'H5b_c{sc:g}_gsig'] = pr_p + m31 * g_can * z_p['g_sig_pt']
    scores = []
    for n, d in cands.items():
        m = metrics(d[tr])
        scores.append((m['mad'] + 0.25 * m['p95'], n, m))
    scores.sort()
    log("train пилота (отбор):")
    for sc, n, m in scores:
        log(f"  {n}: score {sc:.3f} (MAD {m['mad']}, p95 {m['p95']})")
    win = scores[0][1]
    log(f"победитель train: {win}")

    hold_p = {n: metrics(d[~tr]) for n, d in cands.items()}
    log("HOLDOUT пилота:")
    for n in (win, 'H0_prior30m', 'H2_med31_clip1', 'H3_med31_clip2_gsig1'):
        m = hold_p[n]
        log(f"  {n}: MAD {m['mad']:.3f} p95 {m['p95']:.2f} off {m['resid_off']:+.2f}")

    # --- ЭТАП 2: кросс-зона, фикс. рецепт победителя ---
    z_x = zone_data('xzone', ZONES['xzone'])
    pr_x = z_x['prior'][z_x['r'], z_x['c']] - z_x['h_ref']
    m31_x = patch_med(z_x['resid_f'], z_x['r'], z_x['c'], 31, clip=2.0)
    sc_key = win.split('_c')[-1].split('_')[0]
    g_can_x = np.exp(-(z_x['can_pt'] / float(sc_key)) ** 2).astype('float32')
    d_x = {'H0_prior30m': pr_x.copy()}
    if win.startswith('H5a'):
        d_x[win] = pr_x + m31_x * g_can_x
    elif win.startswith('H5b'):
        d_x[win] = pr_x + m31_x * g_can_x * z_x['g_sig_pt']
    else:
        d_x[win] = pr_x + m31_x * z_x['g_sig_pt']
    hold_x = {n: metrics(d[~z_x['tr_mask']]) for n, d in d_x.items()}
    log("HOLDOUT кросс-зоны (фикс. рецепт):")
    for n, m in hold_x.items():
        log(f"  {n}: MAD {m['mad']:.3f} p95 {m['p95']:.2f} off {m['resid_off']:+.2f}")

    h0p, h5p = hold_p['H0_prior30m'], hold_p[win]
    h0x, h5x = hold_x['H0_prior30m'], hold_x[win]
    ok_p = h5p['mad'] <= h0p['mad'] and h5p['p95'] <= h0p['p95']
    ok_x = h5x['mad'] <= h0x['mad'] and h5x['p95'] <= h0x['p95']
    res = dict(
        selection=dict(zone='pilot', score='MAD+0.25*p95, train only',
                       winner=win,
                       train_table={n: m for _, n, m in scores}),
        pilot_holdout=hold_p, xzone_holdout=hold_x,
        recipe=dict(win=win, sigma_c=float(sc_key) if 'H5' in win else None,
                    med=31, clip=2.0,
                    gates=('canopy' if win.startswith('H5a') else
                           'canopy+sigma' if win.startswith('H5b') else 'sigma')),
        promotion=dict(rule='MAD<=H0 AND p95<=H0 в обеих зонах',
                       pilot=bool(ok_p), xzone=bool(ok_x),
                       promoted=bool(ok_p and ok_x)),
        elapsed_min=round((datetime.now() - t00).total_seconds() / 60, 1))
    OUTJ.write_text(json.dumps(res, ensure_ascii=False, indent=1))
    log(f"ПРОМОЦИЯ H5: {'ДА' if ok_p and ok_x else 'НЕТ'} "
        f"(пилот {ok_p}, кросс-зона {ok_x}) -> {OUTJ}")


if __name__ == '__main__':
    main()
