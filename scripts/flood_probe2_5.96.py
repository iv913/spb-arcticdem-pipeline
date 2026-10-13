#!/usr/bin/env python3
"""5.96a-v2: структура шума над акваториями; подтверждение единиц (см);
распределение высот по классам MCLF в акваториях."""
import numpy as np
import rasterio
from rasterio.windows import from_bounds

MOSAIC = '/tmp/my-project/gh-clone/download/mosaic_5.90/spb_hybrid_2m_mosaic_5.90.tif'
MCLF = '/tmp/my-project/gh-clone/download/mclf_v3/mclf_v3_mask_5.90.tif'


def boxes_read(lon0, lat0, lon1, lat1):
    with rasterio.open(MOSAIC) as s:
        a = s.read(1, window=from_bounds(lon0, lat0, lon1, lat1, s.transform)).astype(np.float32)
    with rasterio.open(MCLF) as s:
        m = s.read(1, window=from_bounds(lon0, lat0, lon1, lat1, s.transform))
    return a, m


def crosstab(name, lon0, lat0, lon1, lat1):
    a, m = boxes_read(lon0, lat0, lon1, lat1)
    v = a != -32768
    bands = [(None, 0.3), (0.3, 1.0), (1.0, 2.0), (2.0, 5.0), (5.0, None)]
    print(f'== {name} == n={v.sum()}')
    for k in sorted(set(m[v].tolist())):
        sel = v & (m == k)
        n = sel.sum()
        if n == 0:
            continue
        h = a[sel]
        parts = []
        for lo, hi in bands:
            s = ((h >= lo) if lo is not None else True) & ((h < hi) if hi is not None else True)
            parts.append(f'{s.mean():.2f}')
        print(f'  mclf={k} n={n:>9d} ({n/max(1,v.sum()):.2f})  <=0.3 /0.3-1 /1-2 /2-5 /5+ : ' + ' '.join(parts) + f'   p50={np.percentile(h,50):.2f} p95={np.percentile(h,95):.2f}')


def patch_stats(name, lon0, lat0, lon1, lat1, thr=1.0):
    """средний размер связного пятна (h>thr) в боксе — тонкие стрелы или широкие поля?"""
    from scipy import ndimage
    a, _ = boxes_read(lon0, lat0, lon1, lat1)
    mask = (a > thr) & (a != -32768)
    lab, n = ndimage.label(mask, structure=np.ones((3, 3)))
    sizes = np.bincount(lab.ravel())[1:] if n else np.array([0])
    big = sizes[sizes >= 50]
    print(f'{name}: cells(h>{thr})={mask.sum()} patches={n} big(>=50px)={len(big)} '
          f'big_cover={big.sum() if len(big) else 0}/{mask.sum()} max_patch={sizes.max() if n else 0}')


if __name__ == '__main__':
    # 1) единицы: Пулково (земля ~75 м)
    a, _ = boxes_read(30.30, 59.76, 30.36, 59.80)
    gnd = a[(a != -32768) & (a > 0)]
    print(f'pulkovo ground p50={np.percentile(gnd,50):.0f} p95={np.percentile(gnd,95):.0f} max={gnd.max():.0f}  (ожидание ~7500 если см)')

    # 2) кросс-таблицы по акваториям
    crosstab('gulf_open_pure', 29.45, 59.80, 29.90, 59.93)   # открытая вода без Котлина
    crosstab('gulf_neva',      30.05, 59.90, 30.28, 60.02)
    crosstab('neva_river',     30.25, 59.93, 30.40, 59.96)
    crosstab('shore_south',    29.95, 59.83, 30.10, 59.90)   # южный берег залива (Петергоф шоссе)

    # 3) пятнистость шума
    patch_stats('gulf_open_pure', 29.45, 59.80, 29.90, 59.93, 1.0)
    patch_stats('gulf_neva',      30.05, 59.90, 30.28, 60.02, 1.0)
