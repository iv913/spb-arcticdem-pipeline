#!/usr/bin/env python3
"""5.96b-диагностика: почему зоны затопления малы?
Проверка: (1) русло Невы как барьер связности; (2) высоты известных низких
районов; (3) геометрия частей L=5; (4) карта-диагностика."""
import json
import os
import sys

os.environ.setdefault('MPLCONFIGDIR', '/tmp/mplcache')

import numpy as np

import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location('fz', '/tmp/my-project/scripts/flood_zones_5.96.py')
fz = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fz)
build_grid = fz.build_grid
MOSAIC = fz.MOSAIC
MCLF = fz.MCLF

import rasterio  # noqa: E402
from rasterio.windows import from_bounds  # noqa: E402


def pct(a, m, name):
    v = a[(a != -32768)]
    if v.size == 0:
        print(f'{name:24s} NO DATA')
        return None
    q = np.percentile(v, [5, 25, 50, 75, 95]) / 100.0
    print(f'{name:24s} n={v.size:>9d}  p5..p95 (м): ' + ' '.join(f'{x:5.2f}' for x in q))
    return v


def box_stats(name, lon0, lat0, lon1, lat1, exclude_excl=True):
    with rasterio.open(MOSAIC) as s:
        a = s.read(1, window=from_bounds(lon0, lat0, lon1, lat1, s.transform)).astype(np.float32)
    with rasterio.open(MCLF) as s:
        m = s.read(1, window=from_bounds(lon0, lat0, lon1, lat1, s.transform))
    a /= 100.0
    land = (m != 0) & (a != -327.68)
    wat = (m == 0) & (a != -327.68)
    print(f'== {name} ==  доля excl={wat.sum()/max(1,(a!=-327.68).sum()):.2f}')
    if land.sum():
        q = np.percentile(a[land], [5, 25, 50, 75, 95])
        print(f'   суша (mclf!=0) p5..p95: ' + ' '.join(f'{x:5.2f}' for x in q))
    if wat.sum():
        q = np.percentile(a[wat], [5, 25, 50, 75, 95])
        print(f'   excl/вода      p5..p95: ' + ' '.join(f'{x:5.2f}' for x in q))


if __name__ == '__main__':
    # --- 1. известные районы ---
    box_stats('Нева (русло, Литейный-АлексНевского)', 30.30, 59.968, 30.40, 59.985)
    box_stats('Нева (устье, Гутуевский-Вас.о.)',      30.24, 59.940, 30.30, 59.965)
    box_stats('Васильевский остров (запад)',          30.15, 59.935, 30.24, 59.975)
    box_stats('Коломна/Сенная (Фонтанка-Мойка)',      30.26, 59.920, 30.33, 59.940)
    box_stats('Кировский р-н (Дачное-Автово)',        30.15, 59.845, 30.28, 59.890)
    box_stats('Петроградский о-в',                    30.28, 59.960, 30.37, 59.990)
    box_stats('Охта/Малоохтинский',                   30.40, 59.930, 30.50, 59.960)
    box_stats('Лахта-Ольгино (низкий север)',         30.05, 59.990, 30.20, 60.030)
    box_stats('Стрельна (низкий берег)',              29.90, 59.820, 30.00, 59.850)

    # --- 2. сетка: где проходит граница компонент ---
    h8, valid8, excl8, grid_tr = build_grid()
    from scipy import ndimage
    permanent = valid8 & (h8 <= 0.30)
    struct8 = np.ones((3, 3), dtype=np.int32)
    for L in (1.0, 3.0, 5.0):
        flood = valid8 & (h8 < L)
        lab, n = ndimage.label(flood, structure=struct8)
        # компонента залива
        gulf_id = lab[1510, 3549]
        comp = lab == gulf_id
        sizes = np.bincount(lab.ravel())
        top = np.argsort(sizes[1:])[-6:][::-1] + 1
        print(f'--- L={L}: всего компонент {n}, топ-6 по размеру:')
        for tid in top:
            sl = ndimage.find_objects(lab)[tid - 1]
            cy = (sl[0].start + sl[0].stop) / 2
            cx = (sl[1].start + sl[1].stop) / 2
            lon = grid_tr.c + cx * grid_tr.a
            lat = grid_tr.f + cy * grid_tr.e
            mark = ' <-ЗАЛИВ' if tid == gulf_id else ''
            print(f'   comp {tid}: {sizes[tid]*24.9/1e6:9.2f} км2, центр ({lat:.3f},{lon:.3f}){mark}')
        # доли крупных низких островов в компоненте залива
        for nm, (lo0, la0, lo1, la1) in {
            'Васильевский': (30.15, 59.935, 30.24, 59.975),
            'Петроградский': (30.28, 59.960, 30.37, 59.990),
            'Коломна': (30.26, 59.920, 30.33, 59.940),
            'Кировский': (30.15, 59.845, 30.28, 59.890),
        }.items():
            c0 = int((lo0 - grid_tr.c) / grid_tr.a); c1 = int((lo1 - grid_tr.c) / grid_tr.a)
            r0 = int((grid_tr.f - la1) / -grid_tr.e); r1 = int((grid_tr.f - la0) / -grid_tr.e)
            sub = comp[max(0, r0):r1, max(0, c0):c1]
            hv = h8[max(0, r0):r1, max(0, c0):c1]
            vv = valid8[max(0, r0):r1, max(0, c0):c1] & (hv < L)
            print(f'   {nm:14s}: ниже {L} м ячеек {vv.sum():>7d}, из них в компоненте залива {sub.sum():>7d}')
