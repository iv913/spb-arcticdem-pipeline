#!/usr/bin/env python3
"""
5.80: адаптивная квантиль-фьюжия (пре-регистрация из 5.79 п.3).

Гипотеза: p25 смещён на -0.674*sigma_scatter в низкодисперсных ячейках
(наблюдение: resid_med открытых -0.58 при leaf-off; предсказание
-0.34..-0.54). Фикс: blended fusion по ячейкам
    w = clip((1.5 - sigma)/1.0, 0, 1)
    fused = w*p50 + (1-w)*p25
(sigma>=1.5 -> p25 [крона], sigma<=0.5 -> p50 [несмещённая медиана]).
Пере-фьюжия из СОХРАНЁННЫХ fused_q_p25/p50 tif + sigma npy обеих зон —
без скачиваний.

Проверка: resid_med по канопи-бакетам blend vs p25 (ожидание: открытые
-> 0±0.15). Оценка: H0 prior / H0b p25 / BLEND raw / H2-p25 / H2-blend /
composite-blend (маска 5.79: n>=6, sigma<=1.0, canopy<=1.0, отбор 5.79
фиксирован). Промоция композита: MAD<=prior И p95<=prior в обеих зонах.
Выход: results/adaptive_quantile_5.80.json
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
PAD30 = 40 * 0.0002777777778
OUTJ = HOME / 'results/adaptive_quantile_5.80.json'
ZONES = {
    'pilot': dict(bbox=(30.24, 60.01, 30.34, 60.06),
                  cache=HOME / 'scripts/dem_cache/strips_pilot_5.75',
                  tag='5.76', glo=['N59_00_E030_00', 'N60_00_E030_00']),
    'xzone': dict(bbox=(30.12, 60.01, 30.24, 60.06),
                  cache=HOME / 'scripts/dem_cache/strips_zone_5.79',
                  tag='5.79', glo=['N60_00_E030_00'],
                  prior=HOME / 'scripts/dem_cache/strips_zone_5.77/prior_v2_2m_5.77.tif'),
}
W0_SIG, W1_SIG = 0.5, 1.5   # w=1 при sigma<=0.5 (p50), w=0 при sigma>=1.5 (p25)
MASK_CFG = dict(n_min=6, sigma_max=1.0, canopy_max=1.0)  # фикс отбора 5.79


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
    return dsm - fab, wtr


def patch_med(resid_f, r, c, s=31, clip=1.0):
    h = s // 2
    pad = np.pad(np.clip(resid_f, -clip, clip), h, mode='edge')
    out = np.empty(len(r), dtype='float32')
    for i, (ri, ci) in enumerate(zip(r, c)):
        out[i] = np.median(pad[ri:ri + 2 * h + 1, ci:ci + 2 * h + 1])
    return out


def zone_data(name, zone):
    p25, trf = read_tif(zone['cache'] / f'fused_q_p25_{zone["tag"]}.tif')
    p50, _ = read_tif(zone['cache'] / f'fused_q_p50_{zone["tag"]}.tif')
    prior, _ = read_tif(zone.get('prior') or
                        zone['cache'] / f'prior_v2_2m_{zone["tag"]}.tif')
    sigma = np.load(zone['cache'] / f'sigma_{zone["tag"]}.npy')
    n_cov = np.load(zone['cache'] / f'n_cov_{zone["tag"]}.npy')
    w = np.clip((W1_SIG - sigma) / (W1_SIG - W0_SIG), 0, 1).astype('float32')
    blend = (w * np.nan_to_num(p50) + (1 - w) * np.nan_to_num(p25))
    blend = np.where(np.isfinite(p25) | np.isfinite(p50), blend, np.nan)
    H, W = p25.shape
    ic = pd.read_csv(HOME / 'scripts/data/icesat2_clean.csv')
    B = zone['bbox']
    ic = ic[(ic.lon >= B[0]) & (ic.lon <= B[2]) &
            (ic.lat >= B[1]) & (ic.lat <= B[3])]
    blk = (np.floor(ic.lat / 0.02).astype(int) +
           np.floor(ic.lon / 0.0127).astype(int))
    tr_mask = ((blk % 2) == 0).values
    r, c = rasterio.transform.rowcol(trf, ic.lon.values, ic.lat.values)
    r = np.clip(r, 0, H - 1); c = np.clip(c, 0, W - 1)
    canopy, wtr = canopy_at_points(zone)
    r30, c30 = rasterio.transform.rowcol(wtr, ic.lon.values, ic.lat.values)
    r30 = np.clip(r30, 0, canopy.shape[0] - 1)
    c30 = np.clip(c30, 0, canopy.shape[1] - 1)
    return dict(name=name, prior=prior, p25=p25, blend=blend,
                sigma=sigma, n_cov=n_cov, ic=ic, tr_mask=tr_mask, r=r, c=c,
                can_pt=canopy[r30, c30].astype('float32'),
                h_ref=ic.h_icesat_baltic.values,
                resid_p25=p25 - prior, resid_blend=blend - prior)


def bucket_resid(z, resid):
    v = ~z['tr_mask']
    bk = pd.cut(z['can_pt'][v], [0, 0.5, 1, 2, 4, 30],
                labels=['0-0.5', '0.5-1', '1-2', '2-4', '4+'])
    g = pd.DataFrame(dict(b=bk, rr=resid[z['r'], z['c']][v])).groupby(
        'b', observed=True).rr.agg(
        [('n', 'size'), ('resid_med', lambda x: round(float(np.nanmedian(x)), 3))])
    return g


def main():
    t00 = datetime.now()
    out = dict(recipe=dict(w='clip((1.5-sigma)/1.0,0,1)', blend='w*p50+(1-w)*p25',
                           w0=W0_SIG, w1=W1_SIG, mask=MASK_CFG,
                           mask_from='5.79 отбор, фикс'), zones={})
    hold_all = {}
    for name, zone in ZONES.items():
        z = zone_data(name, zone)
        pr = z['prior'][z['r'], z['c']] - z['h_ref']
        rp25 = z['resid_p25'][z['r'], z['c']]
        rbl = z['resid_blend'][z['r'], z['c']]
        v = z['tr_mask']
        # H2-рецепт на p25 и на blend (медианы residual по патчам 31x31)
        def m31(rr):
            return patch_med(np.where(np.isfinite(rr), rr, 0.0).astype('float32'),
                             z['r'], z['c'], 31, clip=1.0)
        m_p25, m_bl = m31(rp25), m31(rbl)
        sg = z['sigma'][z['r'], z['c']]
        nc = z['n_cov'][z['r'], z['c']]
        cn = z['can_pt']
        mk = ((nc >= MASK_CFG['n_min']) & (sg <= MASK_CFG['sigma_max']) &
              (cn <= MASK_CFG['canopy_max'])).astype('float32')
        hv = ~v
        hold = dict(
            H0_prior=metrics(pr[hv]),
            H0b_p25=metrics((pr + rp25 * 0)[hv] + (z['p25'][z['r'], z['c']] - z['h_ref'])[hv]),
            H2_p25=metrics((pr + m_p25)[hv]),
            H2_blend=metrics((pr + m_bl)[hv]),
            composite_blend=metrics((pr + m_bl * mk)[hv]))
        hold_all[name] = hold
        out['zones'][name] = dict(
            holdout=hold,
            resid_by_canopy=dict(
                p25=bucket_resid(z, z['resid_p25']).reset_index().to_dict('records'),
                blend=bucket_resid(z, z['resid_blend']).reset_index().to_dict('records')))
        log(f"[{name}] HOLDOUT:")
        for n, m in hold.items():
            log(f"  {n}: MAD {m['mad']:.3f} p95 {m['p95']:.2f} off {m['resid_off']:+.2f}")

    hp, hx = hold_all['pilot'], hold_all['xzone']
    ok_p = (hp['composite_blend']['mad'] <= hp['H0_prior']['mad'] and
            hp['composite_blend']['p95'] <= hp['H0_prior']['p95'])
    ok_x = (hx['composite_blend']['mad'] <= hx['H0_prior']['mad'] and
            hx['composite_blend']['p95'] <= hx['H0_prior']['p95'])
    ok_h2x = (hx['H2_blend']['mad'] <= hx['H0_prior']['mad'] and
              hx['H2_blend']['p95'] <= hx['H0_prior']['p95'])
    ok_h2p = (hp['H2_blend']['mad'] <= hp['H0_prior']['mad'] and
              hp['H2_blend']['p95'] <= hp['H0_prior']['p95'])
    out['decision'] = dict(
        hypothesis='p25 bias открытых ячеек; blend -> resid_med ~0',
        h2_blend_beats_prior=dict(pilot=bool(ok_h2p), xzone=bool(ok_h2x)),
        composite_promotion=dict(rule='MAD<=prior И p95<=prior в обеих зонах',
                                 pilot=bool(ok_p), xzone=bool(ok_x),
                                 promoted=bool(ok_p and ok_x)))
    OUTJ.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    log(f"H2-blend vs prior: пилот {ok_h2p}, кросс {ok_h2x}")
    log(f"ПРОМОЦИЯ композита: {'ДА' if ok_p and ok_x else 'НЕТ'}")
    log(f"done {(datetime.now()-t00).total_seconds()/60:.1f} min")


if __name__ == '__main__':
    main()
