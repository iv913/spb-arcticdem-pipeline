#!/usr/bin/env python3
"""download_arcticdem_strips.py — скачивание ArcticDEM strips через PGC STAC API."""
import os, sys, json, subprocess, shutil
from pathlib import Path
from datetime import datetime
os.environ['AWS_NO_SIGN_REQUEST'] = 'YES'
import pystac_client, requests, rasterio, numpy as np
from rasterio.windows import from_bounds
from shapely.geometry import box
import geopandas as gpd

HOME = Path('/home/z/my-project')
CACHE = HOME / 'scripts' / 'dem_cache'
STRIPS_DIR = CACHE / 'arctic' / 'strips'
STRIPS_DIR.mkdir(parents=True, exist_ok=True)

def log(msg): print(f'[{datetime.now():%H:%M:%S}] {msg}', flush=True)

def search_strips(bbox, max_cloud=0.5, min_valid=0.5):
    cat = pystac_client.Client.open("https://stac.pgc.umn.edu/api/v1/")
    search = cat.search(collections=["arcticdem-strips-s2s041-2m"], bbox=bbox)
    items = list(search.items())
    rows = []
    for item in items:
        p = item.properties
        cloud = p.get('pgc:cloud_area_percent', 100)
        valid = p.get('pgc:valid_area_percent', 0)
        if cloud > max_cloud or valid < min_valid: continue
        rows.append({'item': item, 'id': item.id, 'datetime': item.datetime.isoformat(),
                     'pairname': p.get('pgc:pairname',''), 'cloud_pct': cloud,
                     'valid_pct': valid, 'is_xtrack': p.get('pgc:is_xtrack', False),
                     'dem_href': item.assets['dem'].href if 'dem' in item.assets else None})
    rows.sort(key=lambda r: r['datetime'])
    return rows

def clip_via_vsicurl(href, dst_path, bbox_4326, pad_m=200):
    os.environ['CPL_VSIL_CURL_ALLOWED_EXTENSIONS'] = '.tif'
    os.environ['VSI_CACHE'] = 'TRUE'
    os.environ['GDAL_HTTP_MAX_RETRY'] = '10'
    vsiurl = f'/vsicurl/{href}'
    log(f'  /vsicurl clip...')
    try:
        with rasterio.open(vsiurl) as src:
            b = box(*bbox_4326)
            b_utm = gpd.GeoSeries([b], crs='EPSG:4326').to_crs('EPSG:32636').iloc[0]
            b_pad = b_utm.buffer(pad_m)
            b_src = gpd.GeoSeries([b_pad], crs='EPSG:32636').to_crs(src.crs).iloc[0]
            win = from_bounds(*b_src.bounds, src.transform)
            win = win.round_offsets(op='floor').round_lengths(op='ceil')
            arr = src.read(1, window=win).astype('float32')
            if src.nodata is not None: arr[arr == src.nodata] = -9999
            t = rasterio.windows.transform(win, src.transform)
            prof = src.profile.copy()
            prof.update({'height':int(win.height),'width':int(win.width),'transform':t,
                         'dtype':'float32','nodata':-9999,'compress':'deflate'})
            with rasterio.open(str(dst_path), 'w', **prof) as dst: dst.write(arr, 1)
        log(f'  ✓ {dst_path.stat().st_size//(1024*1024)}MB')
        return True
    except Exception as e:
        log(f'  ✗ {e}')
        if dst_path.exists(): dst_path.unlink()
        return False

def clip_local(src_path, dst_path, bbox_4326, pad_m=200):
    with rasterio.open(str(src_path)) as src:
        b = box(*bbox_4326)
        b_utm = gpd.GeoSeries([b], crs='EPSG:4326').to_crs('EPSG:32636').iloc[0]
        b_pad = b_utm.buffer(pad_m)
        b_src = gpd.GeoSeries([b_pad], crs='EPSG:32636').to_crs(src.crs).iloc[0]
        win = from_bounds(*b_src.bounds, src.transform)
        win = win.round_offsets(op='floor').round_lengths(op='ceil')
        arr = src.read(1, window=win).astype('float32')
        if src.nodata is not None: arr[arr == src.nodata] = -9999
        t = rasterio.windows.transform(win, src.transform)
        prof = src.profile.copy()
        prof.update({'height':int(win.height),'width':int(win.width),'transform':t,
                     'dtype':'float32','nodata':-9999,'compress':'deflate'})
        with rasterio.open(str(dst_path), 'w', **prof) as dst: dst.write(arr, 1)
    log(f'  ✓ {dst_path.stat().st_size//(1024*1024)}MB')
    return True

def main():
    log('='*60); log('download_arcticdem_strips'); log('='*60)
    bbox = [30.20, 59.92, 30.32, 59.95]
    strips = search_strips(bbox, max_cloud=0.5, min_valid=0.5)
    log(f'Валидных стрипов: {len(strips)}')

    manifest = []
    for s in strips:
        date_slug = s['datetime'][:10].replace('-', '')
        parts = s['pairname'].split('_') if s['pairname'] else []
        suffix = f'{parts[-2][-4:]}_{parts[-1][-4:]}' if len(parts) >= 4 else 'unk'
        out_clip = STRIPS_DIR / f'strip_{date_slug}_{suffix}_vo_clip.tif'
        if out_clip.exists() and out_clip.stat().st_size > 100*1024:
            log(f'  ✓ {out_clip.name}')
        else:
            if not clip_via_vsicurl(s['dem_href'], out_clip, bbox): continue
        manifest.append({'date': s['datetime'][:10], 'pairname': s['pairname'],
                         'valid_pct': float(s['valid_pct']), 'cloud_pct': float(s['cloud_pct']),
                         'is_xtrack': bool(s['is_xtrack']), 'type': 'strip',
                         'clip_path': str(out_clip.relative_to(HOME))})

    # mosaic clip
    mosaic_path = CACHE / 'arctic' / 'spb_center_arcticdem.tif'
    mosaic_clip = STRIPS_DIR / 'mosaic_vo_clip.tif'
    if mosaic_path.exists() and not mosaic_clip.exists():
        clip_local(mosaic_path, mosaic_clip, bbox)
    if mosaic_clip.exists():
        manifest.insert(0, {'date': 'mosaic_v4.1', 'type': 'mosaic',
                            'clip_path': str(mosaic_clip.relative_to(HOME))})

    (STRIPS_DIR / 'manifest.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    log(f'✓ Манифест: {len(manifest)} источников')

if __name__ == '__main__': main()
