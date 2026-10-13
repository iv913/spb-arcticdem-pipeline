#!/usr/bin/env python3
"""5.96a: рекогносцировка высот акваторий (Финский залив / Нева / озёра)
для выбора базового уровня воды WLEV0 под гидростатическую модель затопления."""
import numpy as np
import rasterio

MOSAIC = '/tmp/my-project/gh-clone/download/mosaic_5.90/spb_hybrid_2m_mosaic_5.90.tif'
MCLF = '/tmp/my-project/gh-clone/download/mclf_v3/mclf_v3_mask_5.90.tif'

# (имя, lon0, lat0, lon1, lat1)
BOXES = [
    ('gulf_open',  29.60, 59.85, 29.95, 60.00),   # открытый залив
    ('gulf_neva',  30.05, 59.90, 30.28, 60.02),   # Невская губа
    ('neva_river', 30.25, 59.93, 30.40, 59.96),   # русло Невы
    ('lag_lakhta', 30.05, 59.98, 30.15, 60.02),   # Лахтинский разлив
    ('city_land',  30.30, 59.85, 30.45, 59.95),   # суша юг города (контроль)
    ('dudergof',   29.85, 59.77, 29.95, 59.83),   # Дудергофское озеро/высоты
]


def window_stats(name, lon0, lat0, lon1, lat1):
    with rasterio.open(MOSAIC) as s:
        w = rasterio.windows.from_bounds(lon0, lat0, lon1, lat1, s.transform)
        a = s.read(1, window=w).astype(np.float32)
    valid = a != -32768
    v = a[valid]
    if v.size == 0:
        print(f'{name:11s} NO DATA')
        return
    q = np.percentile(v, [1, 5, 25, 50, 75, 95, 99])
    print(f'{name:11s} n={v.size:>8d}  min={v.min():7.2f}  p1..p99=' +
          ' '.join(f'{x:6.2f}' for x in q) + f'  max={v.max():7.2f}  frac<=0={(v<=0).mean():.3f}  frac<=0.5={(v<=0.5).mean():.3f}  frac<=1={(v<=1).mean():.3f}')


def mclf_stats(name, lon0, lat0, lon1, lat1):
    with rasterio.open(MCLF) as s:
        w = rasterio.windows.from_bounds(lon0, lat0, lon1, lat1, s.transform)
        a = s.read(1, window=w)
    u, c = np.unique(a, return_counts=True)
    d = {int(k): int(n) for k, n in zip(u, c)}
    tot = max(1, sum(d.values()))
    print(f'{name:11s} MCLF ' + '  '.join(f'{k}:{v/tot:.3f}' for k, v in sorted(d.items())))


if __name__ == '__main__':
    print('--- heights ---')
    for b in BOXES:
        window_stats(*b)
    print('--- mclf classes ---')
    for b in BOXES:
        mclf_stats(*b)
