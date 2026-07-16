#!/usr/bin/env python3
"""
============================================================================
 04a_anchor_ground.py — ОПОРНОЕ ПОЛЕ по открытой земле
============================================================================

Новый верх иерархии (v5). Раньше опорой были улицы, а дворы и газоны
достраивались от них. Замер по паспортным точкам показал, что на
Васильевском это ровно наоборот: улицы — каньоны без отражений от земли
(медиана dH +1.6 м при MAD 2.15), а открытая земля даёт +0.51 м при
MAD 0.50. Поэтому опора теперь — открытая земля, а улицы выводятся из неё
(модуль 04_vo_pipeline) и проверяются против неё.

Что делает модуль:
  1. Кандидаты: не здание, не вода, DSM конечен.
  2. Морфологический фильтр земли (lib_ground.pmf_ground) убирает кроны,
     машины, киоски, навесы.
  3. Гейт открытости: пиксель дальше OPEN_MIN_M от фасада — иначе наклон
     здания в стереопаре заливает его.
  4. Вода: owner-маска + крупные компоненты ниже гейта, ПРИМЫКАЮЩИЕ к
     известной воде. Вода — барьер для диффузии.
  5. Компоненты суши без опоры в продукт не идут (страховка гейта 1).
  6. Диффузия опоры -> опорное поле h_anchor(x,y) на всей суше.

Выход: OUT/anchor_ground.tif
  band1 h_anchor  — м БС-77, опорный уровень земли всюду на суше
  band2 quality   — 1 измеренная опора, 2 ближняя диффузия (<RECON_DIST_M),
                    3 дальняя диффузия, 0 вне домена
  band3 dist_m    — расстояние до ближайшей измеренной опоры, м
  band4 flags     — 1 суша в статистике, 2 водоём, 3 компонента без опоры
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

PIXEL_M = 2.0
WATER_GATE_BS = 0.3
RECON_DIST_M = 40.0
OPEN_MIN_M = 6.0
MIN_ANCHOR_FRAC = 0.02      # ниже этого доля опоры считается провалом


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
    log('04a_anchor_ground: опорное поле по открытой земле')
    log('=' * 60)

    if not OWNER_TIF.exists():
        log(f'ERROR: нет {OWNER_TIF} — сначала островной owner map')
        sys.exit(1)
    with rasterio.open(str(OWNER_TIF)) as o:
        owner = o.read(1)
        transform, crs, shape = o.transform, o.crs, (o.height, o.width)
    OWNER = m01.OWNER_CODES
    log(f'Сетка: {shape[1]}x{shape[0]} px')

    vert = libv.load_vertical(OUT, region='vo', logger=log)
    shift = vert['shift_m']

    dsm = read_on_grid(DEM_PATH, transform, crs, shape) - shift
    building = owner == OWNER['building']
    water_osm = owner == OWNER['water']
    log(f'Здания: {int(building.sum()):,} px, вода OSM: {int(water_osm.sum()):,} px')

    # --- вода: owner + крупные компоненты ниже гейта, ПРИМЫКАЮЩИЕ к воде ---
    low = np.isfinite(dsm) & ~building & (dsm <= WATER_GATE_BS)
    bodies, edges = libg.split_low_pixels(low, adjacent_to=water_osm,
                                          pixel_m=PIXEL_M)
    water = water_osm | bodies
    log(f'Вода: +{int(bodies.sum()):,} px компонентами '
        f'({bodies.sum() * PIXEL_M ** 2 / 1e6:.2f} км²), '
        f'кромки (остаются сушей): {int(edges.sum()):,} px')

    # --- опора: морфологический фильтр земли + гейт открытости ---
    log('Морфологический фильтр земли (кроны, машины, киоски)...')
    anchors, ground, dist_b = libg.anchor_field(
        dsm, building, water, pixel_m=PIXEL_M, open_min_m=OPEN_MIN_M)
    cand = int((~building & ~water & np.isfinite(dsm)).sum())
    log(f'  кандидатов (не дом, не вода): {cand:,} px')
    log(f'  прошли фильтр земли: {int(ground.sum()):,} px '
        f'({100 * ground.sum() / max(1, cand):.0f}%)')
    log(f'  из них открытых (>={OPEN_MIN_M} м от фасада): '
        f'{int(anchors.sum()):,} px ({100 * anchors.sum() / max(1, cand):.0f}%)')
    if anchors.sum() < MIN_ANCHOR_FRAC * cand:
        log('  ⚠ опоры критически мало — проверьте маску зданий и порог '
            'открытости; поле будет почти целиком экстраполяцией')
    if not anchors.any():
        log('ERROR: опора пуста'); sys.exit(1)

    h_a = np.where(anchors, dsm, np.nan)
    log(f'  опора: h ∈ [{np.nanmin(h_a):.2f}, {np.nanmax(h_a):.2f}] м БС, '
        f'медиана {np.nanmedian(h_a):.2f} м')

    # --- домен: суша с опорой ---
    land = ~water
    keep, comp_stats = libg.components_with_skeleton(land, anchors,
                                                     pixel_m=PIXEL_M)
    dropped = land & ~keep
    log(f'Компоненты суши (топ-10): {comp_stats}')
    if int(dropped.sum()):
        log(f'  отброшено без опоры: {int(dropped.sum()):,} px '
            f'({dropped.sum() * PIXEL_M ** 2 / 1e6:.2f} км²)')

    log('Диффузия опоры (вода = барьер)...')
    prior = libg.build_prior(np.where(anchors & keep, dsm, np.nan), domain=keep)

    dist_a = distance_transform_edt(~(anchors & keep)) * PIXEL_M
    quality = np.zeros(shape, dtype='uint8')
    quality[keep] = np.where(dist_a[keep] <= RECON_DIST_M, 2, 3)
    quality[anchors & keep] = 1

    flags = np.zeros(shape, dtype='uint8')
    flags[keep] = 1
    flags[water] = 2
    flags[dropped] = 3

    n1 = int((quality == 1).sum()); n2 = int((quality == 2).sum())
    n3 = int((quality == 3).sum())
    log(f'Опорное поле: измеренных {n1:,}, ближних {n2:,}, дальних {n3:,} px; '
        f'суша {int(keep.sum()) * PIXEL_M ** 2 / 1e6:.2f} км²')
    log(f'  медиана расстояния до опоры: {np.median(dist_a[keep]):.1f} м, '
        f'p95 {np.percentile(dist_a[keep], 95):.1f} м')

    out = OUT / 'anchor_ground.tif'
    prof = {'driver': 'GTiff', 'dtype': 'float32', 'count': 4, 'crs': crs,
            'transform': transform, 'width': shape[1], 'height': shape[0],
            'compress': 'deflate', 'nodata': -9999}
    with rasterio.open(str(out), 'w', **prof) as dst:
        a = prior.copy(); a[~np.isfinite(a)] = -9999
        dst.write(a, 1)
        dst.write(quality.astype('float32'), 2)
        dst.write(dist_a.astype('float32'), 3)
        dst.write(flags.astype('float32'), 4)
        dst.set_band_description(1, 'h_anchor BS-77 (опорное поле)')
        dst.set_band_description(2, 'quality 1 измерено / 2 ближняя / 3 дальняя')
        dst.set_band_description(3, 'dist_to_anchor_m')
        dst.set_band_description(4, 'flags 1 суша / 2 водоём / 3 без опоры')
    log(f'✓ {out}')

    (OUT / 'anchor_ground_qa.json').write_text(json.dumps({
        'shift_m': shift, 'shift_sigma_m': vert['sigma_m'],
        'datum_source': vert['source'],
        'px': {'candidates': cand, 'ground_filtered': int(ground.sum()),
               'anchors': int(anchors.sum()), 'measured': n1,
               'near': n2, 'far': n3},
        'anchor_frac_of_candidates': round(float(anchors.sum()) / max(1, cand), 4),
        'dist_to_anchor_m': {'median': round(float(np.median(dist_a[keep])), 1),
                             'p95': round(float(np.percentile(dist_a[keep], 95)), 1)},
        'land_km2': round(int(keep.sum()) * PIXEL_M ** 2 / 1e6, 3),
        'water': {'osm_px': int(water_osm.sum()), 'bodies_px': int(bodies.sum()),
                  'edges_kept_as_land_px': int(edges.sum())},
        'open_min_m': OPEN_MIN_M, 'pmf': {'windows': list(libg.PMF_WINDOWS),
                                          'slope': libg.PMF_SLOPE,
                                          'dh0': libg.PMF_DH0,
                                          'dh_max': libg.PMF_DH_MAX},
        'land_components_top10': comp_stats,
    }, ensure_ascii=False, indent=2))
    log(f'✓ {OUT / "anchor_ground_qa.json"}')


if __name__ == '__main__':
    main()
