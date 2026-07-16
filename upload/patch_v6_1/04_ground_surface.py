#!/usr/bin/env python3
"""
============================================================================
 04_ground_surface.py (v5) — СБОРКА поверхности поверх опорного поля
============================================================================

Иерархия перевёрнута относительно v3/v4. Раньше опорой были улицы, от
них диффузией достраивались дворы и газоны. Замер по 49 паспортным
точкам показал, что на Васильевском это ровно наоборот:

    открытая земля, сырой DSM   n=11   dH +0.51 м   MAD 0.50
    скелет (улица)              n= 6   dH +1.62 м   MAD 2.15
    крона/машина -> prior       n= 5   dH +2.25 м   MAD 2.25
    дальний prior               n=22   dH +1.94 м   MAD 1.28

Датум верен (сырой DSM на открытой земле даёт полметра), ломался
оценщик уровня улицы — в каньоне H/W~1 стереопара не видит дна.
Диффузия честно разносила эту ошибку по всему острову.

Теперь:
  * опорное поле строит модуль 04a по ОТКРЫТОЙ земле (anchor_ground.tif);
  * улицы приходят из 04_vo_pipeline уже сверенными с этим полем;
  * здесь происходит только сборка и вторая, пиксельная сверка улиц.

Выход: OUT/ground_surface.tif — контракт модуля 03 сохранён.
  band1 h_bs77
  band2 source 1/2/3
  band3 detail: 11-13 улица измерена / 14 улица замощена опорой /
                21 открытая земля из DSM / 22-23 замещена опорой /
                31 кромка / 32 водоём / 41 компонента без опоры
  band4 stat: 1 — учитывать в процентах затопления
"""
import json
import sys
from pathlib import Path
from datetime import datetime

import numpy as np
import rasterio
from rasterio.warp import reproject, Resampling
from scipy.ndimage import distance_transform_edt

import importlib.util as _ilu


def _load(name, fname):
    spec = _ilu.spec_from_file_location(name, Path(__file__).parent / fname)
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


m01 = _load('m01', '01_owner_map.py')
libv = _load('libv', 'lib_vertical.py')
libg = _load('libg', 'lib_ground.py')

OUT = m01.OUT
DEM_PATH = m01.DEM_PATH
OWNER_TIF = OUT / 'vo_owner_map.tif'
ANCHOR_TIF = OUT / 'anchor_ground.tif'
SKELETON_STACK = [OUT / 'road_surface.tif',    # Большой: модуль 02 + fusion
                  OUT / 'vo_surface.tif']      # остров: сверенные улицы

# v6: приёмка асимметрична — ошибка DSM односторонняя (54 из 62
# паспортных точек дали dH > 0, ниже -0.8 м ушла одна).
TOL_ACCEPT_UP = 0.50     # м: насколько DSM вправе быть ВЫШЕ опоры
TOL_ACCEPT_DOWN = 1.50   # м: и насколько НИЖЕ (там чаще ошибается опора)
TOL_STREET = 0.75        # м: пиксельная сверка улицы с опорой
WATER_GATE_BS = 0.3
WATER_H_BS = 0.0
RECON_DIST_M = 40.0
PIXEL_M = 2.0
SKEL_ACCEPT_SOURCES = (1, 2)
SKEL_SANE_BS = (0.0, 9.0)


def log(msg):
    print(f'[{datetime.now():%H:%M:%S}] {msg}', flush=True)


def read_on_grid(path, ref_transform, ref_crs, shape, band=1,
                 resamp=Resampling.bilinear):
    with rasterio.open(str(path)) as src:
        out = np.full(shape, np.nan, dtype='float32')
        reproject(source=rasterio.band(src, band), destination=out,
                  src_nodata=src.nodata, dst_nodata=np.nan,
                  dst_transform=ref_transform, dst_crs=ref_crs,
                  resampling=resamp)
    return out


def main():
    log('=' * 60)
    log('04_ground_surface v5: сборка поверх опорного поля')
    log('=' * 60)

    if not OWNER_TIF.exists():
        log(f'ERROR: нет {OWNER_TIF}'); sys.exit(1)
    with rasterio.open(str(OWNER_TIF)) as o:
        owner = o.read(1)
        transform, crs, shape = o.transform, o.crs, (o.height, o.width)
    OWNER = m01.OWNER_CODES
    log(f'Сетка: {shape[1]}x{shape[0]}')

    vert = libv.load_vertical(OUT, region='vo', logger=log)
    shift = vert['shift_m']; shift_sigma = vert['sigma_m']
    dsm = read_on_grid(DEM_PATH, transform, crs, shape) - shift

    # ------------------------------------------------------------------
    # Опорное поле (модуль 04a) — теперь ЕДИНСТВЕННЫЙ источник prior
    # ------------------------------------------------------------------
    if not ANCHOR_TIF.exists():
        log(f'ERROR: нет {ANCHOR_TIF}. Сначала 04a_anchor_ground.py — '
            'в v5 опора строится по открытой земле, а не по улицам.')
        sys.exit(1)
    prior = read_on_grid(ANCHOR_TIF, transform, crs, shape, band=1)
    a_qual = read_on_grid(ANCHOR_TIF, transform, crs, shape, band=2,
                          resamp=Resampling.nearest)
    a_flags = read_on_grid(ANCHOR_TIF, transform, crs, shape, band=4,
                           resamp=Resampling.nearest)
    a_qual = np.nan_to_num(a_qual).astype('uint8')
    a_flags = np.nan_to_num(a_flags).astype('uint8')
    keep_land = a_flags == 1
    water = a_flags == 2
    dropped = a_flags == 3
    log(f'Опора: суша {int(keep_land.sum()):,} px, вода {int(water.sum()):,} px, '
        f'без опоры {int(dropped.sum()):,} px; '
        f'измеренной опоры {int((a_qual == 1).sum()):,} px')

    building = owner == OWNER['building']

    # ------------------------------------------------------------------
    # Улицы: приходят готовыми, но проверяются ещё раз попиксельно
    # ------------------------------------------------------------------
    skel = np.full(shape, np.nan, dtype='float32')
    skel_src = np.zeros(shape, dtype='uint8')
    rej = {'source': 0, 'sane': 0}
    for p in reversed([q for q in SKELETON_STACK if q.exists()]):
        hh = read_on_grid(p, transform, crs, shape, band=1,
                          resamp=Resampling.nearest)
        ss = read_on_grid(p, transform, crs, shape, band=2,
                          resamp=Resampling.nearest)
        ss = np.nan_to_num(ss).astype('uint8')
        fin = np.isfinite(hh)
        ok_src = np.isin(ss, SKEL_ACCEPT_SOURCES)
        ok_sane = (hh >= SKEL_SANE_BS[0]) & (hh <= SKEL_SANE_BS[1])
        m = fin & ok_src & ok_sane
        skel[m] = hh[m]; skel_src[m] = ss[m]
        rej['source'] += int((fin & ~ok_src).sum())
        rej['sane'] += int((fin & ok_src & ~ok_sane).sum())
        log(f'  улицы += {p.name}: принято {int(m.sum()):,} из {int(fin.sum()):,} px')
    log(f'  отвергнуто: source={rej["source"]:,}, вне диапазона={rej["sane"]:,}')

    on_street = np.isfinite(skel) & keep_land
    # Пиксельная сверка: даже у улицы со статусом measured отдельные
    # участки могут сидеть в тени. Опора здесь — арбитр.
    cmp_ok = on_street & np.isfinite(prior)
    # односторонне: мостим только улицы ВЫШЕ опоры. Улица ниже опоры —
    # свидетельство против опоры, а не против улицы.
    dev = np.where(cmp_ok, skel - prior, np.nan)
    street_bad = cmp_ok & (dev > TOL_STREET)
    n_below = int((cmp_ok & (dev < -TOL_STREET)).sum())
    if n_below:
        log(f'  ⚠ {n_below:,} px улиц НИЖЕ опоры более чем на {TOL_STREET} м — '
            'опора там подозрительна, улицы оставлены как есть')
    street_keep = on_street & ~street_bad
    if cmp_ok.any():
        log(f'Сверка улиц с опорой: медиана |Δ| = '
            f'{np.nanmedian(np.abs(dev[cmp_ok])):.2f} м; замощено опорой '
            f'{int(street_bad.sum()):,} из {int(cmp_ok.sum()):,} px '
            f'({100 * street_bad.sum() / max(1, cmp_ok.sum()):.0f}%)')

    # ------------------------------------------------------------------
    # Открытая земля: guided acceptance ОТ ОПОРЫ (а не от улиц)
    # ------------------------------------------------------------------
    low = keep_land & ~building & ~on_street & np.isfinite(dsm) & \
        (dsm <= WATER_GATE_BS)
    open_mask = (keep_land & ~building & ~on_street & np.isfinite(dsm)
                 & np.isfinite(prior))
    accept = open_mask & libg.accept_dsm(
        dsm, prior, tol_up=TOL_ACCEPT_UP, tol_down=TOL_ACCEPT_DOWN) & \
        (dsm > WATER_GATE_BS)
    suspect = open_mask & low
    replace = open_mask & ~accept & ~suspect

    dist_a = distance_transform_edt(~(a_qual == 1)) * PIXEL_M
    near = dist_a <= RECON_DIST_M

    # ------------------------------------------------------------------
    # Сборка
    # ------------------------------------------------------------------
    surface = np.full(shape, np.nan, dtype='float32')
    source = np.zeros(shape, dtype='uint8')
    detail = np.zeros(shape, dtype='uint8')
    stat = np.zeros(shape, dtype='uint8')

    surface[street_keep] = skel[street_keep]
    source[street_keep] = np.where(skel_src[street_keep] > 0,
                                   skel_src[street_keep], 2)
    detail[street_keep] = 10 + source[street_keep]
    stat[street_keep] = 1

    surface[street_bad] = prior[street_bad]
    source[street_bad] = 2
    detail[street_bad] = 14
    stat[street_bad] = 1

    surface[accept] = dsm[accept]; source[accept] = 1
    detail[accept] = 21; stat[accept] = 1

    surface[replace] = prior[replace]
    source[replace] = np.where(near[replace], 2, 3)
    detail[replace] = np.where(near[replace], 22, 23)
    stat[replace] = 1

    surface[suspect] = prior[suspect]; source[suspect] = 3
    detail[suspect] = 31; stat[suspect] = 1

    surface[water] = WATER_H_BS; source[water] = 3
    detail[water] = 32; stat[water] = 0
    detail[dropped & (detail == 0)] = 41
    # здания остаются NaN -> барьеры для priority-flood

    n = {k: int((detail == v).sum()) for k, v in
         (('street_meas', 11), ('street_rec', 12), ('street_ext', 13),
          ('street_bridged', 14), ('open_accepted', 21),
          ('open_near_anchor', 22), ('open_far_anchor', 23),
          ('edge', 31), ('water_body', 32), ('no_anchor', 41))}
    tot = int(np.isfinite(surface).sum())
    n_stat = int((stat == 1).sum())
    log(f'Поверхность: {tot:,} px; в статистике {n_stat:,} px '
        f'({n_stat * PIXEL_M ** 2 / 1e6:.2f} км²)')
    log(f'  детально: {n}')
    open_tot = n['open_accepted'] + n['open_near_anchor'] + n['open_far_anchor']
    log(f'  открытая земля принята измеренной: '
        f'{100 * n["open_accepted"] / max(1, open_tot):.0f}%')
    if n_stat * PIXEL_M ** 2 / 1e6 > 20:
        log('  ⚠ площадь суши >20 км² — ожидается ~15–19 км². Проверьте гейт 1.')

    out = OUT / 'ground_surface.tif'
    prof = {'driver': 'GTiff', 'dtype': 'float32', 'count': 4, 'crs': crs,
            'transform': transform, 'width': shape[1], 'height': shape[0],
            'compress': 'deflate', 'nodata': -9999}
    with rasterio.open(str(out), 'w', **prof) as dst:
        a = surface.copy(); a[~np.isfinite(a)] = -9999
        dst.write(a, 1)
        dst.write(source.astype('float32'), 2)
        dst.write(detail.astype('float32'), 3)
        dst.write(stat.astype('float32'), 4)
        dst.set_band_description(1, 'h_bs77 ground (v5: anchor-first)')
        dst.set_band_description(2, 'source 1/2/3 (контракт модуля 03)')
        dst.set_band_description(3, 'detail 11-14 street / 21-23 open / '
                                    '31 edge / 32 water / 41 no anchor')
        dst.set_band_description(4, 'stat 1=учитывать в % затопления')
    log(f'✓ {out}')

    (OUT / 'ground_surface_qa.json').write_text(json.dumps({
        'version': 'v5-anchor-first',
        'px': n, 'total_px': tot, 'stat_px': n_stat,
        'stat_area_km2': round(n_stat * PIXEL_M ** 2 / 1e6, 3),
        'shift_m': shift, 'shift_sigma_m': shift_sigma,
        'datum_source': vert['source'],
        'tol_accept_up_m': TOL_ACCEPT_UP, 'tol_accept_down_m': TOL_ACCEPT_DOWN,
        'tol_street_m': TOL_STREET,
        'street_check': {
            'compared_px': int(cmp_ok.sum()),
            'bridged_px': int(street_bad.sum()),
            'median_abs_dev_m': (round(float(np.nanmedian(dev[cmp_ok])), 3)
                                 if cmp_ok.any() else None)},
        'skeleton_rejected': rej,
        'skeleton_stack': [p.name for p in SKELETON_STACK if p.exists()],
    }, ensure_ascii=False, indent=2))
    log(f'✓ {OUT / "ground_surface_qa.json"}')


if __name__ == '__main__':
    main()
