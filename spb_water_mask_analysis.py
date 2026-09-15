#!/usr/bin/env python3
"""
spb_water_mask_analysis.py — проверить гипотезу: cop<0.5 захватывает низкую сушу.

Для каждого пикселя "воды" (cop<0.5):
- Внутри ли он OSM water polygon?
- Примыкает ли он к большой связной компоненте воды?

Без pipeline tiff — работает только с Copernicus + OSM water.
"""
import os, sys, json, math, re
from pathlib import Path
from datetime import datetime
import numpy as np
import rasterio
from rasterio.warp import transform_bounds
from rasterio.transform import from_origin
from rasterio.features import rasterize, geometry_mask
import shapely.geometry as sg
import shapely.ops as so
import pyproj
from scipy.ndimage import label, distance_transform_edt

HOME = Path('/home/z/my-project')
OUT = HOME / 'download'
OUT.mkdir(parents=True, exist_ok=True)
PERSIST = HOME / 'upload' / 'scripts_persistent'
COP_DIR = HOME / 'scripts' / 'dem_cache' / 'copernicus'
COP_TIF = COP_DIR / 'cop_spb_3413_30m.tif'
WATER_GJSON = OUT / 'spb_osm_water.geojson'

SPB_BBOX = (29.70, 59.75, 30.55, 60.05)


def log(msg):
    print(f'[{datetime.now():%H:%M:%S}] {msg}', flush=True)


def ensure_copernicus():
    """Download + reproject Copernicus 4 tiles if missing."""
    if COP_TIF.exists() and COP_TIF.stat().st_size > 1_000_000:
        log(f'✓ Copernicus cached')
        return
    COP_DIR.mkdir(parents=True, exist_ok=True)
    log('Downloading Copernicus 4 tiles...')
    import subprocess
    for tile in ['N59_00_E029_00', 'N59_00_E030_00', 'N60_00_E029_00', 'N60_00_E030_00']:
        url = f'https://copernicus-dem-30m.s3.eu-central-1.amazonaws.com/Copernicus_DSM_COG_10_{tile}_DEM/Copernicus_DSM_COG_10_{tile}_DEM.tif'
        out = COP_DIR / f'cop_{tile}.tif'
        if not out.exists() or out.stat().st_size < 1_000_000:
            subprocess.run(['wget', '-q', '--timeout=300', url, '-O', str(out)], check=True)
        log(f'  ✓ {tile}')
    
    log('Merging + reprojecting...')
    from rasterio.merge import merge
    srcs = [rasterio.open(str(COP_DIR / f'cop_{t}.tif')) for t in ['N59_00_E029_00', 'N59_00_E030_00', 'N60_00_E029_00', 'N60_00_E030_00']]
    mosaic, tf = merge(srcs)
    spb_3413 = transform_bounds('EPSG:4326', 'EPSG:3413', *SPB_BBOX)
    left = math.floor(spb_3413[0] / 30) * 30
    bottom = math.floor(spb_3413[1] / 30) * 30
    right = math.ceil(spb_3413[2] / 30) * 30
    top = math.ceil(spb_3413[3] / 30) * 30
    w = int((right - left) / 30); h = int((top - bottom) / 30)
    out_tf = from_origin(left, top, 30, 30)
    dst = np.full((h, w), np.nan, dtype='float32')
    from rasterio.warp import reproject, Resampling
    reproject(source=mosaic[0], destination=dst,
              src_transform=tf, src_crs=srcs[0].crs,
              dst_transform=out_tf, dst_crs='EPSG:3413',
              resampling=Resampling.bilinear,
              src_nodata=srcs[0].nodata, dst_nodata=np.nan)
    profile = {'driver': 'GTiff', 'dtype': 'float32', 'count': 1,
               'crs': 'EPSG:3413', 'transform': out_tf,
               'width': w, 'height': h, 'compress': 'deflate', 'nodata': np.nan}
    with rasterio.open(str(COP_TIF), 'w', **profile) as d:
        d.write(dst.astype('float32'), 1)
    for s in srcs: s.close()
    log(f'✓ {COP_TIF}')


def ensure_osm_water():
    if WATER_GJSON.exists() and WATER_GJSON.stat().st_size > 1000:
        log(f'✓ OSM water cached')
        return
    log('Downloading OSM water...')
    import requests
    W, S, E, N = SPB_BBOX
    q = f'''[out:json][timeout:600];(way["natural"="water"]({S},{W},{N},{E});relation["natural"="water"]({S},{W},{N},{E});way["waterway"~"^(river|canal|riverbank)$"]({S},{W},{N},{E});relation["waterway"~"^(river|canal|riverbank)$"]({S},{W},{N},{E}););out geom;'''
    r = requests.post('https://maps.mail.ru/osm/tools/overpass/api/interpreter',
                      data={'data': q}, timeout=600)
    data = r.json()
    log(f'  {len(data.get("elements", []))} elements')
    # Convert with relation stitching
    import importlib.util
    spec = importlib.util.spec_from_file_location('spf', PERSIST / 'spb_full_run_v2.py')
    spf = importlib.util.module_from_spec(spec); spec.loader.exec_module(spf)
    feats = spf.osm_to_geojson(data)
    gj = {'type': 'FeatureCollection', 'features': feats,
          'crs': {'type': 'name', 'properties': {'name': 'urn:ogc:def:crs:OGC:1.3:CRS84'}}}
    WATER_GJSON.write_text(json.dumps(gj, ensure_ascii=False))
    log(f'  ✓ {len(feats)} features')


def main():
    log('=' * 60)
    log('Water mask analysis: cop<0.5 vs OSM water + connectivity')
    log('=' * 60)
    
    ensure_copernicus()
    ensure_osm_water()
    
    # Load Copernicus
    log('\nLoading Copernicus...')
    with rasterio.open(str(COP_TIF)) as src:
        cop = src.read(1).astype('float32')
        cop[cop == src.nodata] = np.nan
        cop_tf = src.transform
        cop_shape = (src.height, src.width)
    log(f'  shape: {cop_shape}, valid: {np.isfinite(cop).sum():,}')
    log(f'  range: [{np.nanmin(cop):.1f}, {np.nanmax(cop):.1f}] m')
    
    # cop<0.5 mask
    cop_water = np.isfinite(cop) & (cop < 0.5)
    n_cop_water = int(cop_water.sum())
    px_area_m2 = 30 * 30 * 0.924  # поправка на проекцию (8% завышения)
    log(f'\ncop<0.5 mask: {n_cop_water:,} px = {n_cop_water * px_area_m2 / 1e6:.1f} км²')
    
    # OSM water mask
    log('\nLoading OSM water polys...')
    with open(WATER_GJSON) as f:
        gj = json.load(f)
    bbox_poly = sg.box(*SPB_BBOX)
    polys_4326 = []
    for ft in gj['features']:
        if ft['geometry']['type'] != 'Polygon': continue
        g = sg.shape(ft['geometry'])
        if not g.is_valid: g = g.buffer(0)
        if g.is_empty: continue
        try:
            inter = g.intersection(bbox_poly)
            if not inter.is_empty:
                if inter.geom_type == 'Polygon': polys_4326.append(inter)
                elif inter.geom_type == 'MultiPolygon':
                    for p in inter.geoms:
                        if not p.is_empty: polys_4326.append(p)
        except: pass
    log(f'  {len(polys_4326)} water polys')
    
    # Project to EPSG:3413
    proj = pyproj.Transformer.from_crs('EPSG:4326', 'EPSG:3413', always_xy=True).transform
    polys_3413 = [so.transform(proj, p) for p in polys_4326 if p is not None and not p.is_empty]
    
    # Rasterize OSM water at Copernicus grid (30m)
    geoms = [(p, 1) for p in polys_3413 if p is not None and not p.is_empty]
    osm_water_mask = rasterize(geoms, out_shape=cop_shape, transform=cop_tf,
                                fill=0, dtype='uint8', all_touched=False).astype(bool)
    n_osm_water = int(osm_water_mask.sum())
    log(f'  OSM water: {n_osm_water:,} px = {n_osm_water * px_area_m2 / 1e6:.1f} км²')
    
    # Comparison
    both = cop_water & osm_water_mask
    cop_only = cop_water & ~osm_water_mask
    osm_only = ~cop_water & osm_water_mask
    
    log(f'\n=== COMPARISON ===')
    log(f'  cop<0.5 AND OSM water:    {both.sum():,} px = {both.sum() * px_area_m2 / 1e6:.1f} км²')
    log(f'  cop<0.5 only (LOW LAND):  {cop_only.sum():,} px = {cop_only.sum() * px_area_m2 / 1e6:.1f} км²')
    log(f'  OSM water only:           {osm_only.sum():,} px = {osm_only.sum() * px_area_m2 / 1e6:.1f} км²')
    
    log(f'\n=== % cop<0.5 covered by OSM ===')
    pct = 100 * both.sum() / max(1, cop_water.sum())
    log(f'  {pct:.1f}% of cop<0.5 is real OSM water')
    log(f'  {100-pct:.1f}% of cop<0.5 is LOW LAND captured by mistake')
    
    # Connectivity: find largest connected component of cop<0.5
    log(f'\n=== CONNECTIVITY ANALYSIS ===')
    log(f'  Labelling connected components (8-connectivity)...')
    labeled, n_comp = label(cop_water, structure=np.ones((3, 3)))
    log(f'  Found {n_comp} components')
    
    # Component sizes
    sizes = np.bincount(labeled.ravel())
    sizes[0] = 0  # background
    sorted_sizes = sorted(sizes, reverse=True)
    
    log(f'  Top 10 component sizes:')
    for i, s in enumerate(sorted_sizes[:10]):
        log(f'    #{i+1}: {s:,} px = {s * px_area_m2 / 1e6:.2f} км²')
    
    # Largest component (presumably Финский залив + Нева)
    largest_id = np.argmax(sizes)
    largest_mask = labeled == largest_id
    n_largest = int(largest_mask.sum())
    log(f'\n  Largest component: {n_largest:,} px = {n_largest * px_area_m2 / 1e6:.1f} км²')
    
    # How much of cop<0.5 is in largest component (connected to gulf)?
    in_largest = cop_water & largest_mask
    isolated = cop_water & ~largest_mask
    log(f'  Connected to gulf (largest):    {in_largest.sum():,} px = {in_largest.sum() * px_area_m2 / 1e6:.1f} км²')
    log(f'  ISOLATED (low land, not bay):   {isolated.sum():,} px = {isolated.sum() * px_area_m2 / 1e6:.1f} км²')
    
    # Check small components — likely low-lying land
    log(f'\n  Small components (likely low land, NOT real water):')
    small_total = 0
    for cid in range(1, n_comp + 1):
        if cid == largest_id: continue
        s = int(sizes[cid])
        if s > 10:  # ignore tiny noise
            small_total += s
            if s > 50:
                # Check if component is inside OSM water
                comp_mask = labeled == cid
                in_osm = (comp_mask & osm_water_mask).sum()
                pct_in_osm = 100 * in_osm / s if s > 0 else 0
                log(f'    comp {cid}: {s:,} px = {s * px_area_m2 / 1e6:.3f} км², {pct_in_osm:.0f}% in OSM water')
    log(f'  Total small components: {small_total:,} px = {small_total * px_area_m2 / 1e6:.1f} км²')
    
    # Verdict
    log(f'\n=== VERDICT ===')
    if isolated.sum() > cop_water.sum() * 0.1:
        log(f'  ⚠ cop<0.5 captures {isolated.sum() * px_area_m2 / 1e6:.1f} км² of ISOLATED low land!')
        log(f'  Это {100 * isolated.sum() / cop_water.sum():.1f}% от всей cop<0.5 маски.')
        log(f'  Нужно добавить требование связности с заливом (split_low_pixels).')
    else:
        log(f'  ✓ cop<0.5 в основном связан с заливом ({100 * in_largest.sum() / cop_water.sum():.1f}%)')
    
    # Save result
    result = {
        'cop_water_total_km2': round(cop_water.sum() * px_area_m2 / 1e6, 1),
        'osm_water_total_km2': round(osm_water_mask.sum() * px_area_m2 / 1e6, 1),
        'overlap_km2': round(both.sum() * px_area_m2 / 1e6, 1),
        'cop_only_low_land_km2': round(cop_only.sum() * px_area_m2 / 1e6, 1),
        'pct_cop_covered_by_osm': round(float(pct), 1),
        'pct_cop_isolated': round(100 * isolated.sum() / max(1, cop_water.sum()), 1),
        'largest_component_km2': round(n_largest * px_area_m2 / 1e6, 1),
        'isolated_low_land_km2': round(isolated.sum() * px_area_m2 / 1e6, 1),
        'n_components': int(n_comp),
        'pixel_area_m2_corrected': round(px_area_m2, 3),
    }
    result_path = PERSIST / 'spb_water_mask_analysis.json'
    result_path.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    log(f'\n✓ Saved: {result_path}')


if __name__ == '__main__':
    main()
