#!/usr/bin/env python3
"""
5.77 шаг C: КРОСС-ЗОННАЯ проверка H3 — «Сестрорецк-запад».

Ключевое отличие от 5.76 (отбор вариантов): РЕЦЕПТ ФИКСИРОВАН по пилоту,
никакого пере-подбора в кросс-зоне:
  H0  prior v2 30м (baseline)
  H0b p25 стек 2м (baseline)
  H2  prior + medf(clip(residual,±1), 31)          — победитель протокола 5.76
  H3  H2(clip=2) * gate_sigma exp(-(sigma/1м)^2)   — гейт-кандидат 5.76
Медианы residual считаем по патчам 31x31 вокруг точек (урок 5.76:
полносеточные median_filter в sandbox гибнут; патчи = эквивалент center
median_filter mode='nearest' — секунды вместо минут).

Критерий промоции H3 (пре-регистрация до расчёта):
  MAD(H3) < MAD(H2)  И  p95(H3) <= 1.1 * p95(H2)   [на holdout зоны]
Выход: results/hybrid_crosszone_5.77.json
"""
import json
from pathlib import Path
from datetime import datetime
import numpy as np
import pandas as pd
import rasterio

HOME = Path('/home/z/my-project/spb-arcticdem-pipeline')
ZONE = (30.12, 60.01, 30.24, 60.06)
CACHE = HOME / 'scripts/dem_cache/strips_zone_5.77'
OUTJ = HOME / 'results/hybrid_crosszone_5.77.json'


def log(m):
    print(f"[{datetime.now():%H:%M:%S}] {m}", flush=True)


def metrics(d):
    d = d[np.isfinite(d)]
    off = float(np.median(d))
    dd = d - off
    return dict(resid_off=round(off, 3),
                mad=round(float(np.median(np.abs(dd))), 3),
                p95=round(float(np.percentile(np.abs(dd), 95)), 3),
                p99=round(float(np.percentile(np.abs(dd), 99)), 3),
                n=int(d.size))


def read_tif(p):
    with rasterio.open(p) as s:
        a = s.read(1).astype('float32')
        a[a == s.nodata] = np.nan
        return a, s.transform, s.crs


def patch_med(resid_f, r, c, s=31, clip=None):
    """медиана residual в окне s x s вокруг каждой точки (pad=edge)"""
    h = s // 2
    src = np.clip(resid_f, -clip, clip) if clip else resid_f
    pad = np.pad(src, h, mode='edge')
    out = np.empty(len(r), dtype='float32')
    for i, (ri, ci) in enumerate(zip(r, c)):
        out[i] = np.median(pad[ri:ri + 2 * h + 1, ci:ci + 2 * h + 1])
    return out


def main():
    t00 = datetime.now()
    p25, tr, crs = read_tif(CACHE / 'fused_q_p25_5.77.tif')
    prior, _, _ = read_tif(CACHE / 'prior_v2_2m_5.77.tif')
    if p25.shape != prior.shape:
        raise RuntimeError(f"сетки не совпали: {p25.shape} vs {prior.shape}")
    sigma = np.load(CACHE / 'sigma_5.77.npy')
    n_cov = np.load(CACHE / 'n_cov_5.77.npy')
    H, W = p25.shape

    ic = pd.read_csv(HOME / 'scripts/data/icesat2_clean.csv')
    ic = ic[(ic.lon >= ZONE[0]) & (ic.lon <= ZONE[2]) &
            (ic.lat >= ZONE[1]) & (ic.lat <= ZONE[3])].copy()
    blk = (np.floor(ic.lat / 0.02).astype(int) +
           np.floor(ic.lon / 0.0127).astype(int))
    tr_mask = ((blk % 2) == 0).values
    r, c = rasterio.transform.rowcol(tr, ic.lon.values, ic.lat.values)
    r = np.clip(r, 0, H - 1); c = np.clip(c, 0, W - 1)
    h_ref = ic.h_icesat_baltic.values

    resid = p25 - prior
    resid_f = np.where(np.isfinite(resid), resid, 0.0).astype('float32')
    g_sig = np.where(n_cov > 0, np.exp(-(sigma / 1.0) ** 2),
                     1.0).astype('float32')
    g_sig[np.isnan(g_sig)] = 1.0
    log(f"residual: med {np.nanmedian(resid):+.2f} м, p95|.| "
        f"{np.nanpercentile(np.abs(resid), 95):.2f} м; покрытие "
        f"{np.mean(n_cov > 0):.1%}, sigma med {np.nanmedian(sigma):.2f} м")

    pr = prior[r, c] - h_ref
    m31_c1 = patch_med(resid_f, r, c, 31, clip=1.0)
    m31_c2 = patch_med(resid_f, r, c, 31, clip=2.0)
    variants = {
        'H0_prior30m': pr,
        'H0b_p25_2m': p25[r, c] - h_ref,
        'H2_med31_clip1': pr + m31_c1,
        'H3_med31_clip2_gsig1': pr + m31_c2 * g_sig[r, c],
    }
    res = dict(zone=list(ZONE), train_n=int(tr_mask.sum()),
               valid_n=int((~tr_mask).sum()), variants={},
               protocol=dict(
                   recipe='FIXED из пилота 5.76 (без пере-подбора): '
                          'H2=med31_clip1, H3=med31_clip2 x gate_sigma(1м)',
                   split='zebra 2km (как 5.75/5.76)',
                   promotion='MAD(H3)<MAD(H2) И p95(H3)<=1.1*p95(H2) '
                             '[holdout кросс-зоны]'),
               residual_stats=dict(
                   med=round(float(np.nanmedian(resid)), 3),
                   p95abs=round(float(np.nanpercentile(np.abs(resid), 95)), 3),
                   cover=round(float(np.mean(n_cov > 0)), 4),
                   sigma_med=round(float(np.nanmedian(sigma)), 3)))
    log("HOLDOUT кросс-зоны (Сестрорецк-запад):")
    for name, d in variants.items():
        m = metrics(d[~tr_mask])
        res['variants'][name] = {'holdout': m, 'train': metrics(d[tr_mask])}
        log(f"  {name}: MAD {m['mad']:.3f} p95 {m['p95']:.2f} "
            f"p99 {m['p99']:.2f} off {m['resid_off']:+.2f} n={m['n']}")

    h2 = res['variants']['H2_med31_clip1']['holdout']
    h3 = res['variants']['H3_med31_clip2_gsig1']['holdout']
    promote = (h3['mad'] < h2['mad']) and (h3['p95'] <= 1.1 * h2['p95'])
    res['decision'] = dict(
        h3_promoted=bool(promote),
        rule='MAD(H3)<MAD(H2) AND p95(H3)<=1.1*p95(H2)',
        mad_delta=round(h3['mad'] - h2['mad'], 3),
        p95_ratio=round(h3['p95'] / h2['p95'], 3) if h2['p95'] else None)
    log(f"РЕШЕНИЕ: H3 {'ПРОМОТИРОВАН в протокол' if promote else 'ОСТАЁТСЯ кандидатом'} "
        f"(dMAD {h3['mad']-h2['mad']:+.3f}, p95 ratio {h3['p95']/h2['p95']:.2f})")
    res['elapsed_min'] = round((datetime.now() - t00).total_seconds() / 60, 1)
    OUTJ.write_text(json.dumps(res, ensure_ascii=False, indent=1))
    log(f"done -> {OUTJ}")


if __name__ == '__main__':
    main()
