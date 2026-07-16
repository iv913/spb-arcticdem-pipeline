#!/usr/bin/env python3
"""
============================================================================
 04_vo_pipeline.py — весь Васильевский остров: профили + поверхность + затопление
============================================================================

Четыре обязательных гейта (по итогам ревью):
  1. Остров выделяется ПОЛИГОНОМ admin-границы OSM, не bbox
  2. По-уличный санитарный гейт: медиана вне [0.5, 8]м БС → failed_street
  3. Water-clip маски дороги: вода(+3м) вычитается; пиксель < +0.3м → suspect_water
  4. Seed связности: вода OSM + контакт с береговой линией, НЕ торцы всех улиц

Стек приоритетов: продукт модуля 02 (Большой) ПОВЕРХ быстрого эстиматора.
"""
import json, sys, re, io, base64, heapq
from pathlib import Path
from datetime import datetime
import numpy as np, pandas as pd, geopandas as gpd, rasterio, shapely
from rasterio.windows import from_bounds
from rasterio.transform import array_bounds
from rasterio.warp import transform as warp_transform, transform_bounds
from rasterio.features import rasterize, geometry_mask
from shapely.geometry import LineString, box
from shapely.ops import unary_union, linemerge
import matplotlib; matplotlib.use('Agg')
import matplotlib.pyplot as plt

import importlib.util as _ilu
_spec = _ilu.spec_from_file_location('m01', Path(__file__).parent / '01_owner_map.py')
m01 = _ilu.module_from_spec(_spec); _spec.loader.exec_module(m01)
_spec2 = _ilu.spec_from_file_location('libp', Path(__file__).parent / 'lib_profile.py')
libp = _ilu.module_from_spec(_spec2); _spec2.loader.exec_module(libp)

HOME = Path('/home/z/my-project'); OUT = HOME / 'download'
CACHE = HOME / 'scripts' / 'dem_cache'; UTM = 'EPSG:32636'
DEM_PATH = CACHE / 'arctic' / 'spb_center_arcticdem.tif'
VO_BBOX = (30.20, 59.92, 30.32, 59.95)
MIN_LENGTH_M = 200; PATCH_MARGIN_M = 30
PK_BIN = 5.0; LOW_ANCHOR_PCT = 20; UP_REJECT = 0.40
SMOOTH_HALF = 2; INLIER_TOL_MAX = 0.50; MAX_GAP_BINS = 4; PIXEL_M = 2.0
FORM_SIGMA = 0.107; CLASS_SIGMA = {1:0.15, 2:0.30, 3:0.80}
KZS_NOTE_LEVEL = 1.6; REPORT_LEVELS = [0.5,1.0,1.5,2.0,2.5,3.0]
# ГЕЙТ 2: санитарные пределы для улицы
STREET_H_MIN = 0.5; STREET_H_MAX = 8.0; STREET_RANGE_MAX = 6.0
# ГЕЙТ 3: нижний гейт пикселя
PIXEL_H_MIN = 0.3; WATER_CLIP_BUFFER = 3.0

def log(msg): print(f'[{datetime.now():%H:%M:%S}] {msg}', flush=True)


def fetch_vo_polygon():
    """ГЕЙТ 1: получить полигон Васильевского острова из OSM admin-границы."""
    W, S, E, N = VO_BBOX
    cache = CACHE / 'osm' / 'vo_polygon.json'
    if cache.exists():
        data = json.loads(cache.read_text())
    else:
        q = f"""[out:json][timeout:120];
        relation["admin_level"="5"]["name"~"Васильевский"]({S},{W},{N},{E});
        way(r)["admin_level"]({S},{W},{N},{E});
        out geom;"""
        import requests
        data = {'elements': []}
        for url in ('https://maps.mail.ru/osm/tools/overpass/api/interpreter',
                    'https://overpass-api.de/api/interpreter'):
            try:
                r = requests.post(url, data={'data': q}, timeout=120)
                if r.status_code == 200 and r.text.strip():
                    data = r.json(); cache.write_text(json.dumps(data)); break
            except: pass
    # Собрать полигон из ways relation
    ways = {}
    for el in data.get('elements', []):
        if el['type'] == 'way' and 'geometry' in el:
            coords = [(p['lon'], p['lat']) for p in el['geometry']]
            if len(coords) >= 2:
                ways[el['id']] = coords
    # Попытка замкнуть кольцо: найти relation с members
    polygons = []
    for el in data.get('elements', []):
        if el['type'] == 'relation':
            outer = [m['ref'] for m in el.get('members', []) if m.get('role') == 'outer']
            ring = []
            for w_id in outer:
                if w_id in ways:
                    c = ways[w_id]
                    if ring and ring[-1] != c[0]:
                        if ring[-1] == c[-1]: c = c[::-1]
                    ring.extend(c if not ring else c[1:])
            if len(ring) >= 4 and ring[0] == ring[-1]:
                from shapely.geometry import Polygon
                polygons.append(Polygon(ring))
    if polygons:
        poly = unary_union(polygons)
        log(f'  Полигон В.О.: {poly.area:.4f}°², {len(polygons)} колец')
        return poly
    # Fallback: береговая линия из natural=coastline
    log('  ⚠ admin-граница не найдена, fallback на береговую линию')
    osm = m01.fetch_osm(VO_BBOX)
    waters = []
    for el in osm['elements']:
        tags = el.get('tags', {})
        if tags.get('natural') == 'water' or tags.get('waterway') in ('river','canal','riverbank'):
            polys = list(m01._iter_polygons(el))
            waters.extend(polys)
    if not waters:
        log('  ⚠ нет воды — fallback на bbox')
        return box(*VO_BBOX)
    water_u = unary_union(waters)
    # Остров = bbox минус вода
    island = box(*VO_BBOX).difference(water_u)
    log(f'  Полигон В.О. (береговая): {island.area:.4f}°²')
    return island


def load_vo_streets(vo_polygon):
    """Все улицы В.О. внутри полигона (не bbox!)."""
    osm = m01.fetch_osm(VO_BBOX)
    CARRIAGEWAY = {'motorway','trunk','primary','secondary','tertiary',
                   'residential','unclassified','living_street'}
    W, S, E, N = VO_BBOX
    ways = []
    for el in osm['elements']:
        if el['type'] != 'way' or 'geometry' not in el: continue
        tags = el.get('tags', {})
        hw = tags.get('highway', ''); name = tags.get('name', '')
        if not name or hw not in CARRIAGEWAY: continue
        coords = [(p['lon'], p['lat']) for p in el['geometry']]
        if len(coords) < 2: continue
        # ГЕЙТ 1: хотя бы одна точка внутри полигона острова
        from shapely.geometry import Point
        if not any(vo_polygon.contains(Point(lon, lat)) for lon, lat in coords):
            continue
        if 'мост' in name.lower() or 'зсд' in name.lower(): continue
        ways.append({'geometry': LineString(coords), 'name': name, 'highway': hw})
    gdf = gpd.GeoDataFrame(ways, crs='EPSG:4326').to_crs(UTM)
    by_name = gdf.groupby('name').agg(
        geometry=('geometry', lambda g: unary_union(list(g))),
        highway=('highway', 'first'),
        n_ways=('name', 'count'),
        length_m=('geometry', lambda g: g.length.sum()),
    ).reset_index()
    by_name = by_name[by_name['length_m'] >= MIN_LENGTH_M].sort_values('length_m', ascending=False)
    by_name = gpd.GeoDataFrame(by_name, geometry='geometry', crs=UTM)
    log(f'Улиц В.О. (≥{MIN_LENGTH_M}м, внутри полигона): {len(by_name)}, '
        f'суммарно {by_name["length_m"].sum()/1000:.1f}км')
    return by_name


def build_vo_surface(streets_gdf, dem_path, shift, vo_polygon):
    """Для каждой улицы: P20+0.4 → prune → surface.
    ГЕЙТ 2: санитарный гейт по-улично.
    ГЕЙТ 3: water-clip + нижний гейт пикселя."""
    log('Построение поверхности В.О....')
    src = rasterio.open(str(dem_path))
    dem_crs = src.crs
    streets_dem = streets_gdf.to_crs(dem_crs)
    pad = streets_dem.buffer(PATCH_MARGIN_M).union_all()
    win = from_bounds(*pad.bounds, src.transform)
    win = win.round_offsets(op='floor').round_lengths(op='ceil')
    dem = src.read(1, window=win).astype('float32')
    if src.nodata is not None: dem[dem == src.nodata] = np.nan
    transform = rasterio.windows.transform(win, src.transform)
    H, W = dem.shape
    log(f'  Сетка: {W}×{H} px ({W*2}m × {H*2}m)')

    # Owner mask
    log('  Owner mask...')
    geoms = []
    for _, row in streets_dem.iterrows():
        hw = row['highway']
        w = 12.0 if hw in ('primary','secondary','trunk') else 9.0 if hw == 'tertiary' else 7.0
        g = row['geometry']
        if g.geom_type == 'MultiLineString':
            for line in g.geoms: geoms.append((line.buffer(w/2, cap_style=2), 1))
        elif g.geom_type == 'LineString':
            geoms.append((g.buffer(w/2, cap_style=2), 1))
        elif g.geom_type in ('MultiPolygon', 'Polygon'):
            geoms.append((g, 1))
    road_mask = rasterize(geoms, out_shape=(H, W), transform=transform,
                          fill=0, all_touched=True, dtype='uint8').astype(bool)
    log(f'  Road px (до water-clip): {int(road_mask.sum()):,}')

    # ГЕЙТ 3: water-clip — вычесть полигоны воды (+3м буфер)
    log('  Water-clip маски дороги...')
    osm = m01.fetch_osm(VO_BBOX)
    _, _, waters = m01.collect_layers(osm)
    water_utm = m01.merge_water(waters)
    if water_utm is not None and not water_utm.is_empty:
        water_dem = gpd.GeoSeries([water_utm.buffer(WATER_CLIP_BUFFER)],
                                  crs=UTM).to_crs(dem_crs).iloc[0]
        water_mask = rasterize([(water_dem, 1)], out_shape=(H, W),
                               transform=transform, fill=0, all_touched=True,
                               dtype='uint8').astype(bool)
        n_before = int(road_mask.sum())
        road_mask &= ~water_mask
        log(f'  Water-clip: убрано {n_before - int(road_mask.sum()):,} px')

    surface = np.full((H, W), np.nan, dtype='float32')
    source = np.zeros((H, W), dtype='uint8')
    n_done = 0; n_fail = 0; n_gate_fail = 0; profiles = []

    for idx, row in streets_gdf.iterrows():
        name = row['name']
        axis = row['geometry']
        # MultiLineString → берём самую длинную часть
        if axis.geom_type == 'MultiLineString':
            try:
                merged = linemerge(axis)
                axis = merged if merged.geom_type == 'LineString' else max(merged.geoms, key=lambda g: g.length)
            except:
                axis = max(axis.geoms, key=lambda g: g.length)
        L = axis.length
        if L < 50: continue

        # Быстрый P20+0.4 (DEM уже в памяти)
        n_pk = max(20, int(L / PK_BIN) + 1)
        pks = np.linspace(0, L, n_pk)
        axis_dem_g = gpd.GeoSeries([axis], crs=UTM).to_crs(dem_crs).iloc[0]
        h_vals = []
        for pk in pks:
            pt = axis_dem_g.interpolate(float(pk))
            r_px = 5; col = int((pt.x - transform.c) / transform.a)
            row_px = int((pt.y - transform.f) / transform.e)
            r0, c0 = max(0, row_px-r_px), max(0, col-r_px)
            r1, c1 = min(H, row_px+r_px+1), min(W, col+r_px+1)
            win_vals = dem[r0:r1, c0:c1].ravel()
            win_vals = win_vals[np.isfinite(win_vals)]
            if len(win_vals) < 5: h_vals.append(np.nan); continue
            anchor = np.percentile(win_vals, LOW_ANCHOR_PCT)
            kept_v = win_vals[win_vals <= anchor + UP_REJECT]
            h_vals.append(float(np.median(kept_v)) if len(kept_v) >= 2 else np.nan)

        h_vals = np.array(h_vals)
        ok = np.isfinite(h_vals)
        if ok.sum() < 4: n_fail += 1; continue

        b = np.floor(pks / PK_BIN).astype(int)
        bin_rows = []
        for bi in np.unique(b[ok]):
            mask = (b == bi) & ok
            if mask.sum() < 1: continue
            anchor = np.percentile(h_vals[mask], LOW_ANCHOR_PCT)
            kept_v = h_vals[mask][h_vals[mask] <= anchor + UP_REJECT]
            if len(kept_v) < 1: continue
            bin_rows.append({'b': int(bi), 'pk': float(pks[mask].mean()),
                             'h': float(np.median(kept_v))})
        if len(bin_rows) < 4: n_fail += 1; continue
        prof = pd.DataFrame(bin_rows)

        keep, reason = libp.prune_bins(prof['b'].values.astype(float),
                                       prof['pk'].values, prof['h'].values)
        prof['kept'] = keep
        kept_df = prof[prof['kept']]
        if len(kept_df) < 3: n_fail += 1; continue

        hs = kept_df['h'].values.copy(); sm = hs.copy()
        for i in range(len(hs)):
            lo, hi = max(0, i-SMOOTH_HALF), min(len(hs), i+SMOOTH_HALF+1)
            sm[i] = np.median(hs[lo:hi])
        kept_df = kept_df.copy(); kept_df['h_smooth'] = sm
        pk_grid = kept_df['pk'].values; h_grid = kept_df['h_smooth'].values

        # ГЕЙТ 2: санитарный гейт — медиана улицы вне [0.5, 8]м БС
        h_bs_median = float(np.median(h_grid) - shift)
        h_bs_range = float(h_grid.max() - h_grid.min())
        if h_bs_median < STREET_H_MIN or h_bs_median > STREET_H_MAX or h_bs_range > STREET_RANGE_MAX:
            n_gate_fail += 1
            profiles.append({'name': name, 'length_m': L, 'status': 'failed_street',
                             'h_bs_median': round(h_bs_median, 2),
                             'h_bs_range': round(h_bs_range, 2)})
            continue

        # Растеризация
        street_buf = axis.buffer(6.0, cap_style=2)
        street_dem_g = gpd.GeoSeries([street_buf], crs=UTM).to_crs(dem_crs).iloc[0]
        smask = rasterize([(street_dem_g, 1)], out_shape=(H, W), transform=transform,
                          fill=0, all_touched=True, dtype='uint8').astype(bool)
        smask &= road_mask & ~np.isfinite(surface)
        if not smask.any(): n_done += 1; continue

        rows, cols = np.where(smask)
        xs = transform.c + transform.a * (cols + 0.5) + transform.b * (rows + 0.5)
        ys = transform.f + transform.d * (cols + 0.5) + transform.e * (rows + 0.5)
        pts_dem = shapely.points(xs, ys)
        pk_px = np.asarray(shapely.line_locate_point(axis_dem_g, pts_dem))
        h_model = np.interp(pk_px, pk_grid, h_grid)

        # extrap
        full = np.arange(prof['b'].min(), prof['b'].max() + 1)
        have = np.isin(full, kept_df['b'].values)
        gap_len = {}
        i = 0
        while i < len(full):
            if not have[i]:
                j = i
                while j < len(full) and not have[j]: j += 1
                for k in range(i, j): gap_len[int(full[k])] = j - i
                i = j
            else: i += 1
        bq = np.floor(pk_px / PK_BIN).astype(int)
        ext = (pk_px < pk_grid.min()) | (pk_px > pk_grid.max())
        ext |= np.array([gap_len.get(int(x), 0) > MAX_GAP_BINS for x in bq])

        surface[rows, cols] = h_model - shift
        source[rows, cols] = 3
        dem_vals = dem[rows, cols]
        resid = dem_vals - h_model
        valid = np.isfinite(dem_vals) & ~ext & (np.abs(resid) <= INLIER_TOL_MAX)
        # ГЕЙТ 3: нижний гейт пикселя < +0.3м → suspect_water, не measured
        h_bs = dem_vals[valid] - shift
        valid_suspect = valid.copy()
        valid_suspect[valid] = h_bs >= PIXEL_H_MIN
        surface[rows[valid_suspect], cols[valid_suspect]] = dem_vals[valid_suspect] - shift
        source[rows[valid_suspect], cols[valid_suspect]] = 1
        source[rows[~valid & ~ext], cols[~valid & ~ext]] = 2
        # Пиксели < PIXEL_H_MIN → extrapolated (максимальная неопределённость)
        below = valid & ~valid_suspect
        source[rows[below], cols[below]] = 3

        profiles.append({'name': name, 'length_m': L, 'status': 'ok',
                         'h_bs_median': round(h_bs_median, 2),
                         'h_bs_range': round(h_bs_range, 2),
                         'n_bins': len(prof), 'n_kept': int(keep.sum())})
        n_done += 1
        if n_done % 30 == 0: log(f'  ...{n_done}/{len(streets_gdf)}')

    src.close()
    log(f'  Готово: {n_done} ok, {n_fail} пропущено, {n_gate_fail} failed_street')

    # Стек приоритетов: продукт модуля 02 (Большой) поверх быстрого
    bolshoy_path = OUT / 'road_surface.tif'
    if bolshoy_path.exists():
        log('  Стек приоритетов: накладываем road_surface.tif (Большой пр.)...')
        with rasterio.open(str(bolshoy_path)) as bs:
            # Прочитать в той же сетке (должна совпадать — тот же DEM bbox)
            try:
                b_h = bs.read(1, window=win).astype('float32')
                b_s = bs.read(2, window=win).astype('uint8')
                if bs.nodata is not None: b_h[b_h == bs.nodata] = np.nan
                bolshoy_mask = np.isfinite(b_h)
                surface[bolshoy_mask] = b_h[bolshoy_mask]
                source[bolshoy_mask] = b_s[bolshoy_mask]
                log(f'    Наложено: {int(bolshoy_mask.sum()):,} px из road_surface.tif')
            except Exception as e:
                log(f'    ⚠ не удалось наложить: {e}')

    # Сохранить
    out_path = OUT / 'vo_surface.tif'
    prof_r = {'driver': 'GTiff', 'dtype': 'float32', 'count': 2, 'crs': dem_crs,
              'transform': transform, 'width': W, 'height': H,
              'compress': 'deflate', 'nodata': -9999}
    with rasterio.open(str(out_path), 'w', **prof_r) as dst:
        b1 = surface.copy(); b1[~np.isfinite(b1)] = -9999
        dst.write(b1, 1); dst.write(source.astype('float32'), 2)
        dst.set_band_description(1, 'h BS-77 (stack: 02 over fast estimator)')
        dst.set_band_description(2, '1 measured, 2 reconstructed, 3 extrapolated')
    log(f'✓ {out_path}')
    return surface, source, transform, dem_crs, profiles


def vo_flood_map(surface, source, transform, crs, shift_sigma):
    """Priority-flood для В.О. с ГЕЙТ 4: seed только от воды/береговой линии."""
    log('Priority-flood для В.О....')
    road = np.isfinite(surface)
    h = surface
    u = np.full(h.shape, np.nan, dtype='float32')
    for cls, s in CLASS_SIGMA.items():
        m = road & (source == cls)
        u[m] = np.sqrt(FORM_SIGMA**2 + shift_sigma**2 + s**2)

    # ГЕЙТ 4: seed = вода OSM + пиксели дороги в контакте с береговой линией
    osm = m01.fetch_osm(VO_BBOX)
    _, _, waters = m01.collect_layers(osm)
    water_utm = m01.merge_water(waters)
    seeds = np.zeros(h.shape, dtype=bool)
    if water_utm is not None and not water_utm.is_empty:
        g = gpd.GeoSeries([water_utm], crs=UTM).to_crs(crs).iloc[0]
        seeds = ~geometry_mask([g.__geo_interface__], transform=transform,
                               out_shape=h.shape, all_touched=True)
    # Также сеем пиксели дороги, контактирующие с водой (buffer 5м от воды)
    if water_utm is not None and not water_utm.is_empty:
        water_buf = gpd.GeoSeries([water_utm.buffer(5.0)], crs=UTM).to_crs(crs).iloc[0]
        water_contact = ~geometry_mask([water_buf.__geo_interface__], transform=transform,
                                       out_shape=h.shape, all_touched=True)
        seeds |= (water_contact & road)
    log(f'  Seed (вода OSM + береговой контакт): {int(seeds.sum()):,} px')

    # Priority-flood
    H, W = h.shape
    Z = np.full((H, W), np.inf, dtype='float64')
    valid = np.isfinite(h); pq = []
    for r, c in zip(*np.where(seeds & valid)):
        z = float(h[r, c]); Z[r, c] = z; heapq.heappush(pq, (z, r, c))
    nbrs = ((-1,0),(1,0),(0,-1),(0,1),(-1,-1),(-1,1),(1,-1),(1,1))
    while pq:
        z, r, c = heapq.heappop(pq)
        if z > Z[r, c]: continue
        for dr, dc in nbrs:
            rr, cc = r+dr, c+dc
            if 0 <= rr < H and 0 <= cc < W and valid[rr, cc]:
                nz = max(z, float(h[rr, cc]))
                if nz < Z[rr, cc]: Z[rr, cc] = nz; heapq.heappush(pq, (nz, rr, cc))
    Z[~valid | np.isinf(Z)] = np.nan
    log(f'  Z_spill: [{np.nanmin(Z):.2f}, {np.nanmax(Z):.2f}] м БС')

    # GIS-продукт
    tif = OUT / 'vo_flood_surface.tif'
    prof = {'driver':'GTiff','dtype':'float32','count':4,'crs':crs,'transform':transform,
            'width':W,'height':H,'compress':'deflate','nodata':-9999}
    with rasterio.open(str(tif), 'w', **prof) as dst:
        for bi, (arr, name) in enumerate(
            [(h,'h_bs77'),(u,'uncertainty_m'),(Z.astype('float32'),'z_spill_bs77'),
             (source.astype('float32'),'source 1/2/3')], start=1):
            a = arr.copy(); a[~np.isfinite(a)] = -9999
            dst.write(a.astype('float32'), bi); dst.set_band_description(bi, name)
    log(f'✓ {tif}')

    # Контрольная таблица
    total = int(road.sum()); qa_levels = {}
    for Hl in REPORT_LEVELS:
        wet = road & (Z < Hl); depth = np.where(wet, Hl - h, np.nan)
        core = wet & ((Hl - h) > u)
        opt = road & ((Z - u) < Hl) & ((Hl - h) > -u)
        qa_levels[f'{Hl:.1f}'] = {
            'pct_pess': round(100*core.sum()/total, 1),
            'pct_best': round(100*wet.sum()/total, 1),
            'pct_opt': round(100*opt.sum()/total, 1),
            'depth_max': round(float(np.nanmax(depth)), 2) if wet.any() else None,
            'kzs': Hl > KZS_NOTE_LEVEL}
        log(f'  H={Hl:.1f}м: {qa_levels[f"{Hl:.1f}"]["pct_best"]}% '
            f'[{qa_levels[f"{Hl:.1f}"]["pct_pess"]}..{qa_levels[f"{Hl:.1f}"]["pct_opt"]}]'
            + (' [КЗС]' if Hl > KZS_NOTE_LEVEL else ''))

    # HTML карта (тайловая, из 03_flood_map HTML_TEMPLATE)
    log('  HTML карта (тайловая)...')
    enc_lo = float(np.floor(min(np.nanmin(h), np.nanmin(Z[np.isfinite(Z)])) * 2) / 2 - 0.1) if np.isfinite(Z).any() else float(np.floor(np.nanmin(h)*2)/2-0.1)
    enc_hi = float(np.ceil(max(np.nanmax(h), np.nanmax(Z[np.isfinite(Z)])) * 2) / 2 + 0.1) if np.isfinite(Z).any() else float(np.ceil(np.nanmax(h)*2)/2+0.1)

    def enc(a):
        out = np.zeros(a.shape, dtype='uint8'); m = np.isfinite(a)
        out[m] = 1 + np.clip((a[m]-enc_lo)/(enc_hi-enc_lo)*253, 0, 253).astype('uint8')
        return out
    h8 = enc(h); z8 = enc(Z)
    h8d, z8d, (w4,s4,e4,n4) = m01._reproject_categorical_for_display(h8, z8, transform, crs)
    s8 = source.copy(); s8[~road] = 255
    s8d, _, _ = m01._reproject_categorical_for_display(s8, s8.copy(), transform, crs)
    from PIL import Image
    rgba = np.zeros((*h8d.shape, 4), dtype='uint8')
    vd = (h8d > 0) & (h8d != 255)
    rgba[..., 0] = np.where(vd, h8d, 0); rgba[..., 1] = np.where(vd, z8d, 0)
    rgba[..., 2] = np.where(vd & (s8d <= 3), s8d, 0); rgba[..., 3] = np.where(vd, 255, 0)
    buf = io.BytesIO(); Image.fromarray(rgba).save(buf, format='PNG', optimize=True)
    b64 = base64.b64encode(buf.getvalue()).decode()

    us = {1: float(np.sqrt(FORM_SIGMA**2+shift_sigma**2+0.15**2)),
          2: float(np.sqrt(FORM_SIGMA**2+shift_sigma**2+0.30**2)),
          3: float(np.sqrt(FORM_SIGMA**2+shift_sigma**2+0.80**2))}

    # Импортируем HTML_TEMPLATE из 03_flood_map
    import importlib.util as _ilu3
    _spec3 = _ilu3.spec_from_file_location('flood03', Path(__file__).parent / '03_flood_map.py')
    flood03 = _ilu3.module_from_spec(_spec3); _spec3.loader.exec_module(flood03)
    html = flood03.HTML_TEMPLATE
    for k, v in {'__B64__':b64,'__W4__':repr(w4),'__S4__':repr(s4),'__E4__':repr(e4),'__N4__':repr(n4),
        '__LO__':repr(enc_lo),'__HI__':repr(enc_hi),'__U1__':f'{us[1]:.3f}','__U2__':f'{us[2]:.3f}','__U3__':f'{us[3]:.3f}',
        '__KZS__':f'{KZS_NOTE_LEVEL:.1f}','__HMIN__':'0.0','__HMAX__':'4.0','__HSTEP__':'0.10',
        '__TITLE__':'Васильевский остров','__PTS__':'[]'}.items():
        html = html.replace(k, v)
    (OUT / 'vo_flood_map.html').write_text(html, encoding='utf-8')
    log(f'✓ vo_flood_map.html (тайловый ползунок)')

    qa = {'object': 'Васильевский остров',
          'model': 'priority-flood spill levels; tile-render; water-seed only (gate 4)',
          'error_budget': {'form': FORM_SIGMA, 'shift': shift_sigma, 'class': CLASS_SIGMA},
          'report_levels': qa_levels,
          'road_px': total,
          'products': {'gis': 'vo_flood_surface.tif', 'web': 'vo_flood_map.html'}}
    (OUT / 'vo_flood_qa.json').write_text(json.dumps(qa, ensure_ascii=False, indent=2))
    log(f'✓ vo_flood_qa.json')


def main():
    log('='*60)
    log('04_vo_pipeline: весь Васильевский остров (4 гейта)')
    log('='*60)

    # Shift из city_calibration v3
    cc_path = OUT / 'city_calibration.json'
    if cc_path.exists():
        cc = json.loads(cc_path.read_text())
        shift = float(cc.get('shift_m', 15.9))
        shift_sigma = float(cc.get('between_street_mad_m', 0.80))
        log(f'Shift: {shift:.3f}м, SIGMA: {shift_sigma:.3f}м (v{cc.get("version","?")})')
    else:
        shift = 15.9; shift_sigma = 0.80
        log(f'⚠ city_calibration.json не найден — defaults')

    # ГЕЙТ 1: полигон острова
    log('ГЕЙТ 1: полигон В.О....')
    vo_poly = fetch_vo_polygon()

    # Улицы внутри полигона
    streets = load_vo_streets(vo_poly)

    # Поверхность (с ГЕЙТ 2, 3, стек приоритетов)
    surface, source, transform, crs, profiles = build_vo_surface(
        streets, DEM_PATH, shift, vo_poly)

    # График профилей
    log('График профилей...')
    ok_profiles = [p for p in profiles if p.get('status') == 'ok']
    fail_profiles = [p for p in profiles if p.get('status') == 'failed_street']
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(16, 10), constrained_layout=True)
    # OK streets
    colors = plt.cm.tab20(np.linspace(0, 1, min(20, len(ok_profiles))))
    for i, p in enumerate(ok_profiles[:30]):
        ax1.barh(i, p['h_bs_range'], left=p['h_bs_median'] - p['h_bs_range']/2,
                height=0.7, color=colors[i % 20], alpha=0.7)
    ax1.set_yticks(range(min(30, len(ok_profiles))))
    ax1.set_yticklabels([p['name'][:30] for p in ok_profiles[:30]], fontsize=7)
    ax1.set_xlabel('h_bs77, м'); ax1.set_title(f'В.О.: OK улицы ({len(ok_profiles)})')
    ax1.axvline(0, color='red', ls='--', alpha=0.5); ax1.axvline(8, color='red', ls='--', alpha=0.5)
    ax1.grid(alpha=0.3)
    # Failed streets
    if fail_profiles:
        for i, p in enumerate(fail_profiles[:20]):
            ax2.barh(i, p['h_bs_range'], left=p['h_bs_median'] - p['h_bs_range']/2,
                    height=0.7, color='red', alpha=0.5)
        ax2.set_yticks(range(min(20, len(fail_profiles))))
        ax2.set_yticklabels([p['name'][:30] for p in fail_profiles[:20]], fontsize=7)
        ax2.set_xlabel('h_bs77, м'); ax2.set_title(f'Failed streets ({len(fail_profiles)}) — очередь на полный конвейер')
        ax2.axvline(0, color='red', ls='--', alpha=0.5); ax2.axvline(8, color='red', ls='--', alpha=0.5)
        ax2.grid(alpha=0.3)
    plt.savefig(OUT / 'vo_profiles.png', dpi=140); plt.close()
    log(f'✓ vo_profiles.png (ok={len(ok_profiles)}, failed={len(fail_profiles)})')

    # Затопление (с ГЕЙТ 4)
    vo_flood_map(surface, source, transform, crs, shift_sigma)

    # Итог
    n_meas = int((source == 1).sum()); n_rec = int((source == 2).sum())
    n_ext = int((source == 3).sum()); total = n_meas + n_rec + n_ext
    log('')
    log('='*60)
    log('ИТОГ В.О. (4 гейта):')
    log(f'  Улиц: ok={len(ok_profiles)}, failed_street={len(fail_profiles)}, пропущено={len(profiles)-len(ok_profiles)-len(fail_profiles)}')
    log(f'  Road px: {total:,} ({total*4/1e6:.1f} га)')
    if total:
        log(f'  measured: {n_meas:,} ({100*n_meas/total:.0f}%)')
        log(f'  reconstructed: {n_rec:,} ({100*n_rec/total:.0f}%)')
        log(f'  extrapolated: {n_ext:,} ({100*n_ext/total:.0f}%)')
    log(f'  shift: {shift:.3f}м, SIGMA: {shift_sigma:.3f}м')
    log(f'  Файлы: vo_surface.tif, vo_flood_surface.tif, vo_flood_map.html')
    if fail_profiles:
        log(f'  Очередь на полный конвейер ({len(fail_profiles)} улиц):')
        for p in sorted(fail_profiles, key=lambda x: -x['length_m'])[:10]:
            log(f'    {p["name"][:40]:40s} L={p["length_m"]:.0f}м h_med={p["h_bs_median"]}м range={p["h_bs_range"]}м')
    log('='*60)


if __name__ == '__main__':
    main()
