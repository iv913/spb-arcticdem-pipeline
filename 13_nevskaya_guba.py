#!/usr/bin/env python3
"""
13_nevskaya_guba.py — площадь зеркала и объём накопления Невской губы

V(H) = SUM по пикселям (H - z_eff) * S_px,  где (H - z_eff) > 0

    z_eff = 0        для существующей акватории (detail = 32)
    z_eff = высота   для суши
"""
import json
import sys
from pathlib import Path
from datetime import datetime

import numpy as np
import rasterio
from rasterio.warp import reproject, Resampling
from rasterio.features import geometry_mask
import geopandas as gpd

import importlib.util as _ilu


def _load(name, fname):
    spec = _ilu.spec_from_file_location(name, Path(__file__).parent / fname)
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


geo = _load('geo', 'lib_geo.py')

HOME = Path('/home/z/my-project')
OUT = HOME / 'download'
SURFACE = OUT / 'spb_ground_surface_2m.tif'
BAY_POLY = OUT / 'nevskaya_guba.geojson'
NAMYV_POLY = OUT / 'namyv.geojson'

LEVELS = [0.5, 1.0, 1.6, 2.0, 2.5, 2.93, 3.0, 3.5, 3.80, 4.0, 4.21, 5.0]
NEVA_DISCHARGE_M3S = 2500.0
DETAIL_WATER = 32
BLOCK_ROWS = 2048


def log(msg):
    print(f'[{datetime.now():%H:%M:%S}] {msg}', flush=True)


def hypsometry(z_eff, area_px_m2, levels, sigma_z=None):
    """Гипсометрическая кривая: площадь и объём по уровням."""
    z = np.asarray(z_eff, dtype='float64')
    z = z[np.isfinite(z)]
    out = []
    for H in levels:
        m = z < H
        n = int(m.sum())
        s_m2 = n * area_px_m2
        depth = H - z[m]
        v_m3 = float(depth.sum()) * area_px_m2
        rec = {'level_m': float(H),
               'area_km2': round(s_m2 / 1e6, 3),
               'volume_mln_m3': round(v_m3 / 1e6, 2),
               'mean_depth_m': round(float(depth.mean()), 3) if n else 0.0,
               'n_px': n}
        if sigma_z is not None:
            rec['volume_sigma_mln_m3'] = round(sigma_z * s_m2 / 1e6, 2)
        out.append(rec)
    return out


def fill_time(volumes_mln_m3, discharge_m3s=NEVA_DISCHARGE_M3S):
    """За сколько сток Невы наполнит объём при закрытых створах."""
    out = []
    per_day = discharge_m3s * 86400.0 / 1e6
    for v in volumes_mln_m3:
        out.append(round(v / per_day, 2) if per_day > 0 else None)
    return out, round(per_day, 1)


def read_masked(path, poly_geom, crs_target=None):
    """Поверхность внутри полигона, блоками."""
    with rasterio.open(str(path)) as src:
        transform, crs, shape = src.transform, src.crs, (src.height, src.width)
        nod = src.nodata
        g = gpd.GeoSeries([poly_geom], crs=crs_target or crs).to_crs(crs)
        mask_poly = ~geometry_mask([gg.__geo_interface__ for gg in g],
                                   transform=transform, out_shape=shape,
                                   all_touched=False)
        has_detail = src.count >= 3
        for r0 in range(0, shape[0], BLOCK_ROWS):
            r1 = min(shape[0], r0 + BLOCK_ROWS)
            win = rasterio.windows.Window(0, r0, shape[1], r1 - r0)
            h = src.read(1, window=win).astype('float32')
            if nod is not None:
                h[h == nod] = np.nan
            det = (src.read(2, window=win).astype('float32')
                   if has_detail else np.zeros_like(h))
            yield h, det, mask_poly[r0:r1], transform, crs


def main():
    log('=' * 60)
    log('13_nevskaya_guba: зеркало и объём накопления')
    log('=' * 60)

    if not SURFACE.exists():
        log(f'ERROR: нет {SURFACE}'); sys.exit(1)
    if not BAY_POLY.exists():
        log(f'ERROR: нет {BAY_POLY}'); sys.exit(1)

    bay = gpd.read_file(str(BAY_POLY))
    poly = bay.geometry.union_all() if hasattr(bay.geometry, 'union_all') \
        else bay.unary_union

    with rasterio.open(str(SURFACE)) as src:
        crs = src.crs
        pix = abs(src.transform.a)
    lat_c = 59.95
    px_true = geo.true_pixel_m(pix, lat_c, crs_epsg=int(crs.to_epsg() or 3413))
    area_px = px_true ** 2
    log(f'Пиксель {pix} м в проекции -> {px_true:.3f} м на земле, '
        f'площадь {area_px:.3f} м²')
    log('  (масштаб EPSG:3413 на широте губы; без поправки площади '
        'завышены на 8%)')

    # STREAMING: accumulate per-level sums (memory-efficient, no concat)
    n_water = n_land = 0
    level_sum = {H: 0.0 for H in LEVELS}
    level_count = {H: 0 for H in LEVELS}
    n_total = 0
    for h, det, mp, _, _ in read_masked(SURFACE, poly, crs_target='EPSG:4326'):
        sel = mp & np.isfinite(h)
        if not sel.any():
            continue
        z_arr = h[sel].astype('float32')
        d = det[sel]
        is_w = np.round(d) == DETAIL_WATER
        z_eff_arr = np.where(is_w, 0.0, z_arr)
        n_water += int(is_w.sum()); n_land += int((~is_w).sum())
        n_total += z_eff_arr.size
        for H in LEVELS:
            mask = z_eff_arr < H
            level_count[H] += int(mask.sum())
            level_sum[H] += float((H - z_eff_arr[mask]).sum())
    if n_total == 0:
        log('ERROR: внутри полигона нет данных'); sys.exit(1)
    log(f'Пикселей внутри губы: {n_total:,} '
        f'(акватория {n_water:,}, суша {n_land:,})')
    log(f'  зеркало на ординаре: {n_water * area_px / 1e6:.1f} км²')

    sigma = 1.9
    log(f'  sigma отметок принята {sigma} м (MAD класса detail 23)')

    # Build rows from accumulated sums (no concat needed)
    rows = []
    for H in LEVELS:
        n = level_count[H]
        s_m2 = n * area_px
        v_m3 = level_sum[H] * area_px
        depth_mean = (level_sum[H] / n) if n > 0 else 0.0
        rows.append({
            'level_m': float(H),
            'area_km2': round(s_m2 / 1e6, 3),
            'volume_mln_m3': round(v_m3 / 1e6, 2),
            'mean_depth_m': round(depth_mean, 3) if n else 0.0,
            'n_px': n,
            'volume_sigma_mln_m3': round(sigma * s_m2 / 1e6, 2),
        })
    times, per_day = fill_time([r['volume_mln_m3'] for r in rows])

    log('')
    log(f'{"H, м":>6} {"зеркало, км²":>13} {"объём, млн м³":>14} '
        f'{"±":>8} {"ср.глуб":>8} {"сток Невы, сут":>15}')
    for r, t in zip(rows, times):
        log(f'{r["level_m"]:6.2f} {r["area_km2"]:13.1f} '
            f'{r["volume_mln_m3"]:14.1f} {r.get("volume_sigma_mln_m3", 0):8.1f} '
            f'{r["mean_depth_m"]:8.2f} {t:15.2f}')
    log(f'  сток Невы {NEVA_DISCHARGE_M3S:.0f} м³/с = {per_day} млн м³/сут')
    log('  столбец «сут» — за сколько ТОЛЬКО сток наполнит объём при')
    log('  закрытых створах. Нагон приходит быстрее, но эта величина')
    log('  говорит, сколько створы можно держать закрытыми.')

    scen = None
    if False and NAMYV_POLY.exists():  # disabled - namyv not available
        nam = gpd.read_file(str(NAMYV_POLY))
        npoly = nam.geometry.union_all() if hasattr(nam.geometry, 'union_all') \
            else nam.unary_union
        from rasterio.features import geometry_mask
        with rasterio.open(str(SURFACE)) as src:
            gn = gpd.GeoSeries([npoly], crs=crs).to_crs(src.crs)
            nmask = ~geometry_mask([g.__geo_interface__ for g in gn],
                                   transform=src.transform,
                                   out_shape=(src.height, src.width),
                                   all_touched=False)
        zs2 = []
        i = 0
        for h, det, mp, _, _ in read_masked(SURFACE, poly, crs_target='EPSG:4326'):
            r0, r1 = i, i + h.shape[0]; i = r1
            sel = mp & np.isfinite(h)
            if not sel.any():
                continue
            z = h[sel].astype('float32')
            d = det[sel]
            isw = (np.round(d) == DETAIL_WATER) | nmask[r0:r1][sel]
            zs2.append(np.where(isw, 0.0, z))
        z2 = np.concatenate(zs2)
        rows2 = hypsometry(z2, area_px, LEVELS, sigma_z=sigma)
        scen = rows2
        log('')
        log('--- ВКЛАД НАМЫВНЫХ ТЕРРИТОРИЙ ---')
        log(f'{"H, м":>6} {"с намывом":>12} {"без намыва":>12} '
            f'{"разница, млн м³":>16}')
        for a, b in zip(rows, rows2):
            log(f'{a["level_m"]:6.2f} {a["volume_mln_m3"]:12.1f} '
                f'{b["volume_mln_m3"]:12.1f} '
                f'{b["volume_mln_m3"] - a["volume_mln_m3"]:16.1f}')

    (OUT / 'nevskaya_guba_qa.json').write_text(json.dumps({
        'pixel_m_projected': pix, 'pixel_m_true': round(px_true, 4),
        'area_px_m2': round(area_px, 4),
        'n_px_total': int(n_total), 'n_px_water': n_water,
        'n_px_land': n_land,
        'mirror_at_ordinar_km2': round(n_water * area_px / 1e6, 3),
        'sigma_z_m': sigma,
        'levels': rows, 'without_namyv': scen,
        'neva_discharge_m3s': NEVA_DISCHARGE_M3S,
        'neva_mln_m3_per_day': per_day,
        'fill_days': times,
        'note': ('объём — ПРИРАЩЕНИЕ над ординаром; батиметрия не нужна и '
                 'не использована, абсолютный объём губы этим методом не '
                 'считается'),
    }, ensure_ascii=False, indent=2))
    log(f'✓ {OUT / "nevskaya_guba_qa.json"}')


if __name__ == '__main__':
    main()
