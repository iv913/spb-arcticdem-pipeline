#!/usr/bin/env python3
"""
5.79 шаг B: КОМПОЗИТ «prior 30м + 2м-деталь по маске применимости».

Архитектура (5.78 п.4): деталь применяется ТОЛЬКО в ячейках, где стек
доказанно чист:
  mask = (n_cov >= n_min) & (sigma <= s_max) & (canopy30 <= c_max)
  composite = prior + med31_clip1(residual) * mask
Отбор параметров маски ТОЛЬКО на train пилота (score = MAD + 0.25*p95):
  n_min in {4,6}, s_max in {0.8,1.0,1.5}, c_max in {0.5,1.0}.
Валидация фикс. рецепта: holdout пилота + holdout кросс-зоны на
LEAF-OFF стеке 5.79 (месяцы 11-04, 7 стрипов).
Промоция (пре-рег. 5.78): MAD(comp) <= MAD(prior) И p95(comp) <=
p95(prior) в ОБЕИХ зонах.
Диагностика в отчёте: resid_med по канопи-бакетам leaf-off стека
(проверка: ушла ли -0.55 м bias открытых участков 5.77).
Выход: results/composite_mask_5.79.json
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
OUTJ = HOME / 'results/composite_mask_5.79.json'
ZONES = {
    'pilot': dict(bbox=(30.24, 60.01, 30.34, 60.06),
                  cache=HOME / 'scripts/dem_cache/strips_pilot_5.75',
                  tag='5.76', glo=['N59_00_E030_00', 'N60_00_E030_00']),
    'xzone': dict(bbox=(30.12, 60.01, 30.24, 60.06),
                  cache=HOME / 'scripts/dem_cache/strips_zone_5.79',
                  tag='5.79', glo=['N60_00_E030_00']),
}
GRID = [(n, s, c) for n in (4, 6) for s in (0.8, 1.0, 1.5)
        for c in (0.5, 1.0)]


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
    p25, _ = read_tif(zone['cache'] / f'fused_q_p25_{zone["tag"]}.tif')
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
    r, c = rasterio.transform.rowcol(prior.transform if hasattr(
        prior, 'transform') else _tr_of(zone['cache'], zone['tag']),
        ic.lon.values, ic.lat.values)
    r = np.clip(r, 0, H - 1); c = np.clip(c, 0, W - 1)
    canopy, wtr = canopy_at_points(zone)
    r30, c30 = rasterio.transform.rowcol(wtr, ic.lon.values, ic.lat.values)
    r30 = np.clip(r30, 0, canopy.shape[0] - 1)
    c30 = np.clip(c30, 0, canopy.shape[1] - 1)
    resid = p25 - prior
    resid_f = np.where(np.isfinite(resid), resid, 0.0).astype('float32')
    return dict(
        name=name, prior=prior, sigma=sigma, n_cov=n_cov, ic=ic,
        tr_mask=tr_mask, r=r, c=c,
        can_pt=canopy[r30, c30].astype('float32'),
        m31=patch_med(resid_f, r, c, 31, clip=1.0),
        resid=resid, h_ref=ic.h_icesat_baltic.values)


def _tr_of(cache, tag):
    with rasterio.open(cache / f'fused_q_p25_{tag}.tif') as s:
        return s.transform


def main():
    t00 = datetime.now()
    zp = zone_data('pilot', ZONES['pilot'])
    pr = zp['prior'][zp['r'], zp['c']] - zp['h_ref']
    m31 = zp['m31']
    nc, sg, cn = (zp['n_cov'][zp['r'], zp['c']],
                  zp['sigma'][zp['r'], zp['c']], zp['can_pt'])
    tr = zp['tr_mask']
    log("пилот загружен; отбор маски на train:")
    scores = []
    for n_min, s_max, c_max in GRID:
        mk = ((nc >= n_min) & (sg <= s_max) & (cn <= c_max)).astype('float32')
        m = metrics((pr + m31 * mk)[tr])
        scores.append((m['mad'] + 0.25 * m['p95'], (n_min, s_max, c_max), m,
                       float(mk.mean())))
    scores.sort()
    for sc, cfg, m, cov in scores[:6]:
        log(f"  mask n>={cfg[0]} sig<={cfg[1]} can<={cfg[2]}: score {sc:.3f} "
            f"(MAD {m['mad']}, p95 {m['p95']}, покр.точек {cov:.1%})")
    win_cfg = scores[0][1]
    log(f"победитель маски: {win_cfg}")

    hold_p = dict(H0_prior=metrics(pr[~tr]),
                  H2_unmasked=metrics((pr + m31)[~tr]))
    mk_p = ((nc >= win_cfg[0]) & (sg <= win_cfg[1]) &
            (cn <= win_cfg[2])).astype('float32')
    hold_p['composite'] = metrics((pr + m31 * mk_p)[~tr])

    # кросс-зона на LEAF-OFF стеке 5.79, фикс. маска
    zx = zone_data('xzone', ZONES['xzone'])
    prx = zx['prior'][zx['r'], zx['c']] - zx['h_ref']
    m31x = zx['m31']
    ncx, sgx, cnx = (zx['n_cov'][zx['r'], zx['c']],
                     zx['sigma'][zx['r'], zx['c']], zx['can_pt'])
    mkx = ((ncx >= win_cfg[0]) & (sgx <= win_cfg[1]) &
           (cnx <= win_cfg[2])).astype('float32')
    hold_x = dict(H0_prior=metrics(prx[~zx['tr_mask']]),
                  H2_unmasked=metrics((prx + m31x)[~zx['tr_mask']]),
                  composite=metrics((prx + m31x * mkx)[~zx['tr_mask']]))

    log("HOLDOUT пилота:")
    for n, m in hold_p.items():
        log(f"  {n}: MAD {m['mad']:.3f} p95 {m['p95']:.2f} "
            f"off {m['resid_off']:+.2f}")
    log("HOLDOUT кросс-зоны (leaf-off стек):")
    for n, m in hold_x.items():
        log(f"  {n}: MAD {m['mad']:.3f} p95 {m['p95']:.2f} "
            f"off {m['resid_off']:+.2f}")

    # диагностика: bias открытых участков leaf-off стека
    v = ~zx['tr_mask']
    resx = zx['resid'][zx['r'], zx['c']]
    bk = pd.cut(zx['can_pt'][v], [0, 0.5, 1, 2, 4, 30],
                labels=['0-0.5', '0.5-1', '1-2', '2-4', '4+'])
    diag = pd.DataFrame(dict(b=bk, resid=resx[v])).groupby(
        'b', observed=True).resid.agg(
        [('n', 'size'), ('resid_med', lambda x: round(float(np.nanmedian(x)), 3))])
    log("resid_med по канопи (leaf-off):\n" + diag.to_string())

    ok_p = (hold_p['composite']['mad'] <= hold_p['H0_prior']['mad'] and
            hold_p['composite']['p95'] <= hold_p['H0_prior']['p95'])
    ok_x = (hold_x['composite']['mad'] <= hold_x['H0_prior']['mad'] and
            hold_x['composite']['p95'] <= hold_x['H0_prior']['p95'])
    res = dict(
        mask=dict(n_min=win_cfg[0], sigma_max=win_cfg[1], canopy_max=win_cfg[2],
                  selected_on='pilot train', detail='med31_clip1'),
        selection_table=[dict(score=round(sc, 3), n_min=c[0], sigma_max=c[1],
                              canopy_max=c[2], mad=m['mad'], p95=m['p95'],
                              point_cover=round(cv, 3))
                         for sc, c, m, cv in scores],
        pilot_holdout=hold_p, xzone_holdout_leafoff=hold_x,
        diag_xzone_resid_by_canopy=diag.reset_index().to_dict('records'),
        promotion=dict(rule='MAD<=prior AND p95<=prior в обеих зонах',
                       pilot=bool(ok_p), xzone=bool(ok_x),
                       promoted=bool(ok_p and ok_x)),
        elapsed_min=round((datetime.now() - t00).total_seconds() / 60, 1))
    OUTJ.write_text(json.dumps(res, ensure_ascii=False, indent=1))
    log(f"ПРОМОЦИЯ композита: {'ДА' if ok_p and ok_x else 'НЕТ'} "
        f"(пилот {ok_p}, кросс {ok_x})")


if __name__ == '__main__':
    main()
