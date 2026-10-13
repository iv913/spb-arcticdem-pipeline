#!/usr/bin/env python3
"""5.96b (v2): зоны ПОТЕНЦИАЛЬНОГО затопления СПб по гибридной мозаике 5.90 (BS-77, см).

Модель v2 — чистая гидростатическая «чаша» (bathtub) по валидной суше:
  flood(L) = valid & (h < L) & ~excl & ~water
Причина отказа от связности: footprint мозаики фрагментарен (прямоугольные зоны,
море/Нева в основном nodata, русло Невы в валидных ячейках — мусорный DSM 10+ м),
гидравлическая связность по ЦМР не вычислима. В СПб почти вся низкая суша
гидравлически достижима (плоская дельта), поэтому bathtub — честная аппроксимация.
Сценарий: нагон при прорыве/отказе КЗС, без гидродинамики/дренажа.

Исключение акваторий:
  1) поверхность моря чистая ~0±0.6 м -> water = (h <= 0.7 м);
  2) гидрография OSM (реки/каналы/озёра/пруды) — растеризация с буфером
     (fix мусорных русел Невы/протоков).
Агрегация: блок 8x4 пикселя (~7.9x7.9 м), среднее по валидным, приём >=25%.
Уровни: 1.0–5.0 м шаг 0.5. Отсечение мелких пятен < ~1.9 га.

Выход:
  results/flood_zones_5.96/flood_levels_5.96.geojson
  results/flood_zones_5.96/flood_stats_5.96.json
  кэш сетки: results/flood_zones_5.96/grid_cache_5.96.npz
"""
import json
import math
import os
import time

import numpy as np
import rasterio
from rasterio import features
from rasterio.windows import Window

MOSAIC = '/tmp/my-project/gh-clone/download/mosaic_5.90/spb_hybrid_2m_mosaic_5.90.tif'
MCLF = '/tmp/my-project/gh-clone/download/mclf_v3/mclf_v3_mask_5.90.tif'
OUTDIR = '/tmp/my-project/results/flood_zones_5.96'
CACHE = os.path.join(OUTDIR, 'grid_cache_5.96.npz')
NODATA = -32768

FX, FY = 8, 4                 # агрегация x8, y4 -> ~7.9 м
MIN_FRAC = 0.25               # минимум валидных пикселей в блоке
H_WATER = 0.70                # м, порог исключения поверхности воды
MIN_PART_CELLS = 300          # ~1.9 га, отсечение шума
SIMPLIFY_DEG = 0.00025        # ~25 м
LEVELS = [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0]
BBOX = (29.30, 59.65, 30.75, 60.25)   # OSM-выгрузка
LON0, LAT0 = BBOX[0], BBOX[1]
MX = 111319.49 * math.cos(math.radians(59.95))   # м/град lon
MY = 111132.95                                   # м/град lat
OVERPASS = [
    'https://overpass.osm.ch/api/interpreter',
    'https://overpass-api.de/api/interpreter',
    'https://overpass.kumi.systems/api/interpreter',
]
BUFFER_M = {'river': 120, 'canal': 60, 'stream': 30, 'ditch': 15}


def log(msg):
    print(f'[{time.strftime("%H:%M:%S")}] {msg}', flush=True)


# ---------------------------------------------------------------- grid
def build_grid():
    if os.path.exists(CACHE):
        log('кэш сетки найден')
        z = np.load(CACHE)
        tr = z['tr']
        grid_tr = rasterio.Affine(tr[0], tr[1], tr[2], tr[3], tr[4], tr[5])
        return z['h8'], z['valid8'].astype(bool), grid_tr
    log('агрегация мозаики до ~8 м (без кэша)...')
    with rasterio.open(MOSAIC) as sm, rasterio.open(MCLF) as sc:
        W, H = sm.width, sm.height
        tr = sm.transform
        nx2, ny2 = W // FX, H // FY
        h8 = np.full((ny2, nx2), np.nan, dtype=np.float32)
        cnt8 = np.zeros((ny2, nx2), dtype=np.int32)
        ex8 = np.zeros((ny2, nx2), dtype=np.float32)
        BW, BH = 4096, 4096
        for y0 in range(0, H, BH):
            for x0 in range(0, W, BW):
                w = min(BW, W - x0)
                h = min(BH, H - y0)
                win = Window(x0, y0, w, h)
                a = sm.read(1, window=win)
                m = sc.read(1, window=win)
                valid = a != NODATA
                av = a.astype(np.float32)
                av[~valid] = 0.0
                w2, h2 = w // FX, h // FY
                if w2 == 0 or h2 == 0:
                    continue
                av = av[:h2 * FY, :w2 * FX].reshape(h2, FY, w2, FX)
                vd = valid[:h2 * FY, :w2 * FX].reshape(h2, FY, w2, FX)
                ex = (m[:h2 * FY, :w2 * FX] == 0).astype(np.float32).reshape(h2, FY, w2, FX)
                s_sum = av.sum(axis=(1, 3))
                s_cnt = vd.sum(axis=(1, 3), dtype=np.int32)
                gy, gx = y0 // FY, x0 // FX
                h8[gy:gy + h2, gx:gx + w2] = s_sum / np.maximum(s_cnt, 1)
                cnt8[gy:gy + h2, gx:gx + w2] = s_cnt
                ex8[gy:gy + h2, gx:gx + w2] = ex.sum(axis=(1, 3)) / (FX * FY)
    h8 /= 100.0  # см -> м
    valid8 = cnt8 >= (MIN_FRAC * FX * FY)
    excl8 = ex8 > 0.5
    h8[~valid8] = np.nan
    grid_tr = rasterio.Affine(tr.a * FX, 0, tr.c, 0, tr.e * FY, tr.f)
    os.makedirs(OUTDIR, exist_ok=True)
    np.savez_compressed(CACHE,
                        h8=h8.astype(np.float32), valid8=valid8.astype(np.uint8),
                        excl8=excl8.astype(np.uint8),
                        tr=np.array([grid_tr.a, grid_tr.b, grid_tr.c,
                                     grid_tr.d, grid_tr.e, grid_tr.f]))
    log(f'сетка {nx2}x{ny2}, валидно {valid8.mean()*100:.1f}%, кэш сохранён')
    return h8, valid8, grid_tr


# ---------------------------------------------------------------- OSM water
def fetch_osm_water(grid_tr, shape_out):
    """Оверпасс: natural=water (полигоны) + водные линии; растеризация в сетку."""
    import requests
    from shapely.geometry import LineString, Polygon, shape as sh_shape
    from shapely.ops import unary_union

    q = ('[out:json][timeout:180];('
         'way["natural"="water"](29.3,59.6,30.75,60.25);'
         'relation["natural"="water"](29.3,59.6,30.75,60.25);'
         'way["waterway"~"^(river|canal|stream|ditch|riverbank)$"](29.3,59.6,30.75,60.25);'
         ');out geom;')
    data = None
    for url in OVERPASS:
        try:
            log(f'overpass: {url}')
            r = requests.post(url, data={'data': q}, timeout=180,
                              headers={'User-Agent': 'spb-arcticdem-pipeline/5.96'})
            if r.status_code == 200 and r.content:
                data = r.json()
                break
        except Exception as e:  # noqa: BLE001
            log(f'  fail: {e}')
    if data is None:
        log('OSM недоступен — фолбэк: вода только по высоте (h<=0.7)')
        return None, False

    def to_met(coords):
        return [((p['lon'] - LON0) * MX, (p['lat'] - LAT0) * MY) for p in coords]

    def to_deg(coords):
        return [(LON0 + x / MX, LAT0 + y / MY) for x, y in coords]

    polys, lines = [], []
    n_ways = n_rels = 0
    for el in data.get('elements', []):
        typ = el.get('type')
        g = el.get('geometry')
        if not g:
            continue
        pts = to_met(g)
        if len(pts) < 2:
            continue
        tags = el.get('tags', {}) or {}
        if typ == 'relation':
            n_rels += 1
            # члены мультиполигона — внешние как полигоны, внутренние дырками (грубо: внешние)
            for m in el.get('members', []):
                if m.get('type') == 'way' and m.get('geometry') and m.get('role') != 'inner':
                    mp = to_met(m['geometry'])
                    if len(mp) >= 4:
                        try:
                            polys.append(Polygon(mp))
                        except Exception:  # noqa: BLE001
                            pass
            continue
        n_ways += 1
        ww = tags.get('waterway')
        if ww in BUFFER_M and tags.get('natural') != 'water':
            try:
                lines.append(LineString(pts).buffer(BUFFER_M[ww]))
            except Exception:  # noqa: BLE001
                pass
        elif ww == 'riverbank':
            try:
                p = Polygon(pts)
                if p.is_valid and p.area > 0:
                    polys.append(p)
            except Exception:  # noqa: BLE001
                pass
        elif typ == 'way' and tags.get('natural') == 'water':
            try:
                p = Polygon(pts)
                if p.is_valid and p.area > 100:
                    polys.append(p)
            except Exception:  # noqa: BLE001
                pass
    log(f'OSM: ways={n_ways} relations={n_rels} полигонов={len(polys)} буферов={len(lines)}')
    shapes = []
    for p in polys:  # полигоны воды (метрика -> градусы)
        if p.geom_type != 'Polygon' or p.is_empty or p.area <= 100:
            continue
        try:
            shapes.append(Polygon(to_deg(list(p.exterior.coords))))
        except Exception:  # noqa: BLE001
            continue
    for lbuf in lines:  # буферизованные линии рек/каналов
        if lbuf.is_empty:
            continue
        parts = [lbuf] if lbuf.geom_type == 'Polygon' else list(lbuf.geoms)
        for part in parts:
            if part.geom_type != 'Polygon' or part.is_empty:
                continue
            try:
                shapes.append(Polygon(to_deg(list(part.exterior.coords))))
            except Exception:  # noqa: BLE001
                continue
    if not shapes:
        return None, True
    water_r = features.rasterize([(s, 1) for s in shapes], out_shape=shape_out,
                                 transform=grid_tr, fill=0, dtype='uint8')
    return water_r.astype(bool), True


# ---------------------------------------------------------------- main
def main():
    os.makedirs(OUTDIR, exist_ok=True)
    t0 = time.time()
    h8, valid8, grid_tr = build_grid()
    ny, nx = h8.shape
    lat = grid_tr.f + (np.arange(ny) + 0.5) * grid_tr.e
    dx_m = abs(grid_tr.a) * MX * np.ones(ny)
    dx_m = abs(grid_tr.a) * 111319.49 * np.cos(np.radians(lat))
    dy_m = abs(grid_tr.e) * MY
    area_row = (dx_m * dy_m).astype(np.float64)
    cell_area = float(area_row.mean())
    log(f'ячейка {dx_m.mean():.2f}x{dy_m:.2f} м = {cell_area:.1f} м2')

    water_r, osm_ok = fetch_osm_water(grid_tr, (ny, nx))
    if water_r is None:
        water_r = np.zeros((ny, nx), dtype=bool)
    water = (valid8 & (h8 <= H_WATER)) | water_r
    log(f'вода: h<={H_WATER} м = {(valid8 & (h8 <= H_WATER)).sum()*cell_area/1e6:.1f} км2, '
        f'OSM={water_r.sum()*cell_area/1e6:.1f} км2, итог {water.sum()*cell_area/1e6:.1f} км2')

    land_ok = valid8 & ~water
    stats = {
        'stage': '5.96', 'model': 'bathtub (потенциальные зоны, без гидродинамики)',
        'datum': 'BS-77', 'scenario': 'нагон при отказе/прорыве КЗС',
        'water_exclude': f'h<={H_WATER} м + OSM hydrography',
        'osm_ok': osm_ok, 'cell_m': [round(float(dx_m.mean()), 2), round(float(dy_m), 2)],
        'min_part_ha': round(MIN_PART_CELLS * cell_area / 1e4, 2),
        'simplify_m': round(SIMPLIFY_DEG * 111319.49 * math.cos(math.radians(59.95)), 1),
        'levels': [], 'valid_km2': round(valid8.sum() * cell_area / 1e6, 1),
        'land_km2': round(land_ok.sum() * cell_area / 1e6, 1),
        'water_km2': round(water.sum() * cell_area / 1e6, 1),
        'mosaic': 'spb_hybrid_2m_mosaic_5.90.tif',
    }
    log(f'валидная суша: {stats["land_km2"]} км2, вода: {stats["water_km2"]} км2')

    feats = []
    prev = 0.0
    for L in LEVELS:
        tL = time.time()
        land = land_ok & (h8 < L)
        from scipy import ndimage
        lab, n = ndimage.label(land, structure=np.ones((3, 3), dtype=np.int32))
        sizes = np.bincount(lab.ravel())
        keep = np.where(sizes >= MIN_PART_CELLS)[0]
        keep = keep[keep != 0]
        landk = np.isin(lab, keep)
        area_km2 = float((area_row[:, None] * landk).sum() / 1e6)
        mask_u8 = landk.astype(np.uint8)
        geoms = [g for g, v in features.shapes(mask_u8, mask=landk,
                                               transform=grid_tr, connectivity=4) if v == 1]
        from shapely.geometry import shape as sh_shape
        from shapely.ops import unary_union
        geoms_s = [sh_shape(g).simplify(SIMPLIFY_DEG, preserve_topology=True) for g in geoms]
        mp = unary_union(geoms_s) if geoms_s else None
        parts_out = []
        if mp is not None and not mp.is_empty:
            plist = [mp] if mp.geom_type == 'Polygon' else (
                list(mp.geoms) if mp.geom_type == 'MultiPolygon' else [])
            for g in plist:
                if g.geom_type != 'Polygon' or g.is_empty:
                    continue
                rings = [[[round(x, 5), round(y, 5)] for x, y in g.exterior.coords]]
                rings += [[[round(x, 5), round(y, 5)] for x, y in r.coords]
                          for r in g.interiors]
                parts_out.append(rings)
        delta = area_km2 - prev
        stats['levels'].append({'level_m': L, 'area_km2': round(area_km2, 2),
                                'delta_km2': round(delta, 2), 'parts': len(parts_out)})
        if parts_out:
            feats.append({'type': 'Feature',
                          'properties': {'level_m': L, 'area_km2': round(area_km2, 2),
                                         'parts': len(parts_out)},
                          'geometry': {'type': 'MultiPolygon', 'coordinates': parts_out}})
        prev = area_km2
        log(f'L={L:>3.1f} м: {area_km2:8.2f} км2 (+{delta:.2f}), частей {len(parts_out)}, '
            f'{time.time()-tL:.1f} с')

    fc = {'type': 'FeatureCollection', 'name': 'spb_flood_zones_5.96',
          'crs': {'type': 'name', 'properties': {'name': 'urn:ogc:def:crs:OGC:1.3:CRS84'}},
          'features': feats}
    with open(os.path.join(OUTDIR, 'flood_levels_5.96.geojson'), 'w') as f:
        json.dump(fc, f, ensure_ascii=False)
    stats['elapsed_s'] = round(time.time() - t0, 1)
    with open(os.path.join(OUTDIR, 'flood_stats_5.96.json'), 'w') as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)
    log(f'ГОТОВО за {stats["elapsed_s"]} c')
    for r in stats['levels']:
        print(f"  +{r['level_m']:.1f} м: {r['area_km2']:8.2f} км2 (прирост {r['delta_km2']:7.2f})")


if __name__ == '__main__':
    main()
