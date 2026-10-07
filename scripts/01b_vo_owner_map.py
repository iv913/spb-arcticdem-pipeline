#!/usr/bin/env python3
"""01b_vo_owner_map.py — построить owner map для всего В.О."""
import json, sys
from pathlib import Path
from datetime import datetime
import numpy as np, geopandas as gpd, rasterio
from rasterio.features import rasterize
from rasterio.windows import from_bounds
from shapely.geometry import LineString
from shapely.ops import unary_union

import importlib.util as _ilu
_spec = _ilu.spec_from_file_location('m01', Path(__file__).parent / '01_owner_map.py')
m01 = _ilu.module_from_spec(_spec); _spec.loader.exec_module(m01)

OUT = m01.OUT; CACHE = m01.CACHE; DEM_PATH = m01.DEM_PATH; UTM = m01.UTM
OWNER = m01.OWNER_CODES
VO_BBOX = (30.20, 59.92, 30.32, 59.95)
PATCH_MARGIN_M = 60


def log(msg):
    print(f'[{datetime.now():%H:%M:%S}] {msg}', flush=True)


def fetch_vo_polygon():
    cache = CACHE / 'osm' / 'vo_polygon.json'
    if cache.exists():
        data = json.loads(cache.read_text())
    else:
        log('ERROR: нет vo_polygon.json'); sys.exit(1)
    ways = {}
    for el in data.get('elements', []):
        if el['type'] == 'way' and 'geometry' in el:
            coords = [(p['lon'], p['lat']) for p in el['geometry']]
            if len(coords) >= 2:
                ways[el['id']] = coords
    outer_ls, inner_ls = [], []
    for el in data.get('elements', []):
        if el['type'] != 'relation': continue
        for m in el.get('members', []):
            if m['ref'] not in ways: continue
            ls = LineString(ways[m['ref']])
            (inner_ls if m.get('role') == 'inner' else outer_ls).append(ls)
    outer_polys = m01._stitch_rings(outer_ls)
    if not outer_polys:
        log('ERROR: stitch_rings failed'); sys.exit(1)
    poly = unary_union(outer_polys)
    if inner_ls:
        poly = poly.difference(unary_union(m01._stitch_rings(inner_ls)))
    if not poly.is_valid:
        poly = poly.buffer(0)
    return poly


def main():
    log('='*60); log('01b_vo_owner_map'); log('='*60)
    vo_poly = fetch_vo_polygon()
    area_km2 = gpd.GeoSeries([vo_poly], crs='EPSG:4326').to_crs(UTM).iloc[0].area / 1e6
    log(f'  Полигон В.О.: {area_km2:.2f} км²')

    osm = m01.fetch_osm(VO_BBOX, water_margin_deg=0.025)
    buildings, greens, waters = m01.collect_layers(osm)
    log(f'Слои: buildings={len(buildings)}, green={len(greens)}, water={len(waters)}')

    buildings_utm = m01._to_utm_buffered(buildings, m01.BLD_BUFFER, 0)
    green_utm = m01._to_utm_buffered(greens, m01.GREEN_BUFFER, m01.TREE_ROW_HALF_W)
    water_utm = m01.merge_water(waters)

    with rasterio.open(str(DEM_PATH)) as src:
        dem_crs = src.crs
        vo_dem = gpd.GeoSeries([vo_poly], crs='EPSG:4326').to_crs(dem_crs).iloc[0]
        pad = vo_dem.buffer(PATCH_MARGIN_M)
        win = from_bounds(*pad.bounds, src.transform)
        win = win.round_offsets(op='floor').round_lengths(op='ceil')
        transform = rasterio.windows.transform(win, src.transform)
        H = int(win.height); Wd = int(win.width)
        log(f'  Сетка: {Wd}×{H} px')

    def to_dem(geom):
        if geom is None or geom.is_empty: return []
        g = gpd.GeoSeries([geom], crs=UTM).to_crs(dem_crs).iloc[0]
        return [g]

    owner = np.zeros((H, Wd), dtype='uint8')
    for name, geoms in [('water', to_dem(water_utm)), ('green', to_dem(green_utm)),
                        ('building', to_dem(buildings_utm))]:
        if not geoms: continue
        m = rasterize([(g, 1) for g in geoms], out_shape=(H, Wd),
                      transform=transform, fill=0, all_touched=True, dtype='uint8')
        owner[m == 1] = OWNER[name]

    mixed = np.zeros_like(owner, dtype=bool)
    for dy, dx in ((-1,0),(1,0),(0,-1),(0,1)):
        nb = np.roll(owner, (dy, dx), axis=(0,1))
        if dy == -1: nb[0, :] = owner[0, :]
        if dy == 1:  nb[-1, :] = owner[-1, :]
        if dx == -1: nb[:, 0] = owner[:, 0]
        if dx == 1:  nb[:, -1] = owner[:, -1]
        mixed |= (nb != owner)
    quality = mixed.astype('uint8')

    counts = {m01.OWNER_NAMES[c]: int((owner == c).sum()) for c in np.unique(owner)}
    log(f'  Владельцы (px): {counts}')

    out = OUT / 'vo_owner_map.tif'
    prof = {'driver': 'GTiff', 'dtype': 'uint8', 'count': 2, 'crs': dem_crs,
            'transform': transform, 'width': Wd, 'height': H,
            'compress': 'deflate', 'nodata': None}
    with rasterio.open(str(out), 'w', **prof) as dst:
        dst.write(owner, 1); dst.write(quality, 2)
    log(f'✓ {out}')


if __name__ == '__main__':
    main()
