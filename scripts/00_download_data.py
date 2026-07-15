#!/usr/bin/env python3
"""
============================================================================
 00_download_data.py — скачивание всех исходных данных для пайплайна
============================================================================

Скачивает:
  1. ArcticDEM mosaic v4.1 (2m) — через PGC STAC API
  2. ArcticDEM strips (2m) — через PGC STAC API (для мультемпорального fusion)
  3. OSM highways + buildings + water + green — через Overpass API
  4. OSM city-wide highways — для городской калибровки (202 точки)

Исходные файлы пользователя (уже в upload/):
  - Отметки.txt — 202 паспортные точки (lat, lon, h_pas)
  - atl08_ground.gpkg — ICESat-2 ATL08 ground heights (для проверки датума)
  - ArcticDEM.7z — старые ноутбуки с примерами кода

Зависимости: pystac-client, rasterio, geopandas, requests
PGC STAC: https://stac.pgc.umn.edu/api/v1/ (без авторизации, AWS_NO_SIGN_REQUEST)
Overpass:  https://maps.mail.ru/osm/tools/overpass/api/interpreter
"""
import os, sys, json, subprocess, shutil
from pathlib import Path
from datetime import datetime
import requests

os.environ['AWS_NO_SIGN_REQUEST'] = 'YES'
os.environ['CPL_VSIL_CURL_ALLOWED_EXTENSIONS'] = '.tif'
os.environ['VSI_CACHE'] = 'TRUE'
os.environ['GDAL_HTTP_MAX_RETRY'] = '10'
os.environ['GDAL_HTTP_RETRY_DELAY'] = '5'

import numpy as np
import rasterio
from rasterio.warp import transform_bounds, transform as warp_transform
from rasterio.windows import from_bounds
from shapely.geometry import box
import geopandas as gpd

HOME = Path('/home/z/my-project')
UPLOAD = HOME / 'upload'
CACHE = HOME / 'scripts' / 'dem_cache'
ARCTIC_DIR = CACHE / 'arctic'
STRIPS_DIR = ARCTIC_DIR / 'strips'
OSM_DIR = CACHE / 'osm'
for d in [ARCTIC_DIR, STRIPS_DIR, OSM_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# Конфигурация
MVP_BBOX = (30.20, 59.92, 30.32, 59.95)  # W, S, E, N — Большой пр-т В.О.
CITY_BBOX_MARGIN = 0.005  # ~500м запас вокруг всех точек
DEM_TILE_IDS = ['32_73_1_1', '32_73_2_1']  # ArcticDEM mosaic tiles для СПб

PGC_STAC = "https://stac.pgc.umn.edu/api/v1/"
OVERPASS_URLS = [
    'https://maps.mail.ru/osm/tools/overpass/api/interpreter',
    'https://overpass-api.de/api/interpreter',
]


def log(msg):
    print(f'[{datetime.now():%H:%M:%S}] {msg}', flush=True)


# ============================================================
# 1. ArcticDEM Mosaic v4.1 (2m) — через PGC STAC
# ============================================================
def download_arcticdem_mosaic():
    """Скачать mosaic tiles через PGC STAC, объединить в один TIF."""
    mosaic_path = ARCTIC_DIR / 'spb_center_arcticdem.tif'
    if mosaic_path.exists() and mosaic_path.stat().st_size > 10 * 1024 * 1024:
        log(f'  ✓ mosaic уже есть: {mosaic_path.name} ({mosaic_path.stat().st_size//(1024*1024)}MB)')
        return mosaic_path

    log('1. ArcticDEM mosaic v4.1 (2m) через PGC STAC')
    import pystac_client

    cat = pystac_client.Client.open(PGC_STAC)
    collection = "arcticdem-mosaics-v4.1-2m"

    # Поиск по tile ID
    full_ids = [f'{tid}_2m_v4.1' for tid in DEM_TILE_IDS]
    search = cat.search(collections=[collection], ids=full_ids)
    items = list(search.items())

    if not items:
        log('  Поиск по ID не удался — пробуем по bbox')
        search = cat.search(collections=[collection], bbox=list(MVP_BBOX))
        items = list(search.items())

    if not items:
        log('  ⚠ Тайлы mosaic не найдены — возможно нужен ручной download с PGC')
        return None

    log(f'  Найдено тайлов: {len(items)}')

    # Слить через /vsicurl/ + rasterio.merge
    from rasterio.merge import merge
    srcs = []
    for item in items:
        href = item.assets['dem'].href
        log(f'  Открываю /vsicurl/: {item.id}')
        srcs.append(rasterio.open(f'/vsicurl/{href}'))

    if len(srcs) == 1:
        # Один тайл — просто копируем
        arr = srcs[0].read(1)
        prof = srcs[0].profile.copy()
        prof.update(compress='deflate')
        with rasterio.open(str(mosaic_path), 'w', **prof) as dst:
            dst.write(arr, 1)
    else:
        mosaic, transform = merge(srcs)
        prof = srcs[0].profile.copy()
        prof.update({
            'height': mosaic.shape[1], 'width': mosaic.shape[2],
            'transform': transform, 'count': 1, 'compress': 'deflate',
        })
        with rasterio.open(str(mosaic_path), 'w', **prof) as dst:
            dst.write(mosaic[0], 1)

    for s in srcs:
        s.close()

    log(f'  ✓ {mosaic_path.name} ({mosaic_path.stat().st_size//(1024*1024)}MB)')
    return mosaic_path


# ============================================================
# 2. ArcticDEM Strips (2m) — через PGC STAC, /vsicurl/ клип
# ============================================================
def download_arcticdem_strips():
    """Скачать стрипы ArcticDEM для V.O. bbox (мультемпоральный fusion)."""
    manifest_path = STRIPS_DIR / 'manifest.json'
    if manifest_path.exists():
        m = json.loads(manifest_path.read_text())
        if len(m) >= 5:
            log(f'  ✓ strips manifest уже есть: {len(m)} источников')
            return manifest_path

    log('2. ArcticDEM strips (2m) через PGC STAC')
    import pystac_client

    cat = pystac_client.Client.open(PGC_STAC)
    search = cat.search(
        collections=["arcticdem-strips-s2s041-2m"],
        bbox=list(MVP_BBOX),
    )
    items = list(search.items())
    log(f'  Найдено стрипов: {len(items)}')

    # Фильтр: cloud ≤ 0.5, valid ≥ 0.5
    strips = []
    for item in items:
        p = item.properties
        cloud = p.get('pgc:cloud_area_percent', 100)
        valid = p.get('pgc:valid_area_percent', 0)
        if cloud > 0.5 or valid < 0.5:
            continue
        strips.append({
            'item': item,
            'datetime': item.datetime.isoformat() if item.datetime else '',
            'pairname': p.get('pgc:pairname', ''),
            'valid_pct': valid,
            'cloud_pct': cloud,
            'dem_href': item.assets['dem'].href if 'dem' in item.assets else None,
        })
    strips.sort(key=lambda s: s['datetime'])
    log(f'  После фильтра: {len(strips)} стрипов')

    manifest = []
    for s in strips:
        date_slug = s['datetime'][:10].replace('-', '')
        parts = s['pairname'].split('_') if s['pairname'] else []
        suffix = f'{parts[-2][-4:]}_{parts[-1][-4:]}' if len(parts) >= 4 else 'unk'
        out_clip = STRIPS_DIR / f'strip_{date_slug}_{suffix}_vo_clip.tif'

        if out_clip.exists() and out_clip.stat().st_size > 100 * 1024:
            log(f'  ✓ {out_clip.name}')
        else:
            log(f'  Клип {date_slug} через /vsicurl/...')
            try:
                with rasterio.open(f'/vsicurl/{s["dem_href"]}') as src:
                    b = box(*MVP_BBOX)
                    b_utm = gpd.GeoSeries([b], crs='EPSG:4326').to_crs('EPSG:32636').iloc[0]
                    b_pad = b_utm.buffer(200)
                    b_src = gpd.GeoSeries([b_pad], crs='EPSG:32636').to_crs(src.crs).iloc[0]
                    win = from_bounds(*b_src.bounds, src.transform)
                    win = win.round_offsets(op='floor').round_lengths(op='ceil')
                    arr = src.read(1, window=win).astype('float32')
                    if src.nodata is not None:
                        arr[arr == src.nodata] = -9999
                    t = rasterio.windows.transform(win, src.transform)
                    prof = src.profile.copy()
                    prof.update({
                        'height': int(win.height), 'width': int(win.width),
                        'transform': t, 'dtype': 'float32',
                        'nodata': -9999, 'compress': 'deflate',
                    })
                    with rasterio.open(str(out_clip), 'w', **prof) as dst:
                        dst.write(arr, 1)
                log(f'  ✓ {out_clip.stat().st_size//(1024*1024)}MB')
            except Exception as e:
                log(f'  ✗ {e}')
                continue

        manifest.append({
            'date': s['datetime'][:10],
            'pairname': s['pairname'],
            'valid_pct': float(s['valid_pct']),
            'cloud_pct': float(s['cloud_pct']),
            'type': 'strip',
            'clip_path': str(out_clip.relative_to(HOME)),
        })

    # Добавить mosaic clip в манифест
    mosaic_path = ARCTIC_DIR / 'spb_center_arcticdem.tif'
    mosaic_clip = STRIPS_DIR / 'mosaic_vo_clip.tif'
    if mosaic_path.exists() and not mosaic_clip.exists():
        log('  Клип mosaic в V.O. bbox...')
        with rasterio.open(str(mosaic_path)) as src:
            b = box(*MVP_BBOX)
            b_utm = gpd.GeoSeries([b], crs='EPSG:4326').to_crs('EPSG:32636').iloc[0]
            b_pad = b_utm.buffer(200)
            b_src = gpd.GeoSeries([b_pad], crs='EPSG:32636').to_crs(src.crs).iloc[0]
            win = from_bounds(*b_src.bounds, src.transform)
            win = win.round_offsets(op='floor').round_lengths(op='ceil')
            arr = src.read(1, window=win).astype('float32')
            if src.nodata is not None:
                arr[arr == src.nodata] = -9999
            t = rasterio.windows.transform(win, src.transform)
            prof = src.profile.copy()
            prof.update({
                'height': int(win.height), 'width': int(win.width),
                'transform': t, 'dtype': 'float32',
                'nodata': -9999, 'compress': 'deflate',
            })
            with rasterio.open(str(mosaic_clip), 'w', **prof) as dst:
                dst.write(arr, 1)
        log(f'  ✓ mosaic_vo_clip.tif')

    if mosaic_clip.exists():
        manifest.insert(0, {
            'date': 'mosaic_v4.1', 'type': 'mosaic',
            'clip_path': str(mosaic_clip.relative_to(HOME)),
        })

    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    log(f'  ✓ Манифест: {len(manifest)} источников')
    return manifest_path


# ============================================================
# 3. OSM для объекта (V.O. bbox) — Overpass
# ============================================================
def download_osm_object():
    """OSM для модуля 01: highways + buildings + water + green."""
    cache = OSM_DIR / 'owner_map_osm.json'
    if cache.exists():
        log(f'  ✓ OSM объект уже в кэше: {cache.name}')
        return cache

    log('3. OSM для объекта (V.O. bbox) через Overpass')
    W, S, E, N = MVP_BBOX
    q = f"""[out:json][timeout:240];
(
  way["highway"]({S},{W},{N},{E});
  way["area:highway"]({S},{W},{N},{E});
  way["building"]({S},{W},{N},{E});
  relation["building"]({S},{W},{N},{E});
  way["landuse"~"^(grass|forest|meadow|village_green|flowerbed)$"]({S},{W},{N},{E});
  way["leisure"~"^(park|garden|playground|common)$"]({S},{W},{N},{E});
  way["natural"~"^(water|wood|scrub|tree_row|wetland)$"]({S},{W},{N},{E});
  relation["natural"="water"]({S},{W},{N},{E});
  way["waterway"~"^(river|canal|riverbank)$"]({S},{W},{N},{E});
);
out geom;"""

    for url in OVERPASS_URLS:
        try:
            log(f'  Overpass: {url}')
            r = requests.post(url, data={'data': q}, timeout=240)
            if r.status_code == 200 and r.text.strip():
                data = r.json()
                cache.write_text(json.dumps(data))
                log(f'  ✓ {len(data.get("elements", []))} элементов')
                return cache
        except Exception as e:
            log(f'  ! {e}')
    log('  ⚠ Overpass недоступен')
    return None


# ============================================================
# 4. OSM city-wide highways — для городской калибровки
# ============================================================
def download_osm_city():
    """OSM highways для всего bbox точек (калибровка по 202 точкам)."""
    # Загружаем точки, считаем bbox
    import re
    pts_path = UPLOAD / 'Отметки.txt'
    if not pts_path.exists():
        log('  ⚠ Нет Отметки.txt — пропуск city OSM')
        return None

    pts = []
    for line in open(pts_path, encoding='utf-8'):
        line = line.strip()
        if not line or ':' not in line:
            continue
        l, r = line.split(':', 1)
        m = re.match(r'^(\d+[.,]\d+)\s*,\s*(\d+[.,]\d+)$', l.strip())
        if not m:
            continue
        lat = float(m.group(1).replace(',', '.'))
        lon = float(m.group(2).replace(',', '.'))
        pts.append((lat, lon))

    if not pts:
        log('  ⚠ Точки не распознаны')
        return None

    lats = [p[0] for p in pts]
    lons = [p[1] for p in pts]
    margin = CITY_BBOX_MARGIN
    W = min(lons) - margin
    S = min(lats) - margin
    E = max(lons) + margin
    N = max(lats) + margin

    cache = OSM_DIR / f'city_highways_{int(W*1000)}_{int(S*1000)}.json'
    if cache.exists():
        log(f'  ✓ City OSM уже в кэше: {cache.name}')
        return cache

    log(f'4. OSM city-wide highways (bbox {W:.4f},{S:.4f},{E:.4f},{N:.4f})')
    log(f'  Покрытие: {(E-W)*55:.0f}км × {(N-S)*111:.0f}км, {len(pts)} точек')

    q = f"""[out:json][timeout:240];
(
  way["highway"~"^(motorway|trunk|primary|secondary|tertiary|unclassified|residential|living_street|service)$"]({S},{W},{N},{E});
);
out geom;"""

    for url in OVERPASS_URLS:
        try:
            log(f'  Overpass: {url}')
            r = requests.post(url, data={'data': q}, timeout=240)
            if r.status_code == 200 and r.text.strip():
                data = r.json()
                cache.write_text(json.dumps(data))
                log(f'  ✓ {len(data.get("elements", []))} ways')
                return cache
        except Exception as e:
            log(f'  ! {e}')
    log('  ⚠ Overpass недоступен')
    return None


# ============================================================
# 5. Проверка исходных файлов пользователя
# ============================================================
def check_user_files():
    """Проверить, что файлы из upload/ на месте."""
    log('5. Проверка исходных файлов')
    files = {
        'Отметки.txt': UPLOAD / 'Отметки.txt',
        'atl08_ground.gpkg': UPLOAD / 'atl08_ground.gpkg',
        'ArcticDEM.7z': UPLOAD / 'ArcticDEM.7z',
    }
    for name, path in files.items():
        if path.exists():
            log(f'  ✓ {name} ({path.stat().st_size//(1024*1024)}MB)')
        else:
            log(f'  ⚠ {name} — отсутствует (не критично для основных модулей)')


# ============================================================
# MAIN
# ============================================================
def main():
    log('=' * 60)
    log('00_download_data: скачивание всех исходных данных')
    log('=' * 60)

    # 0. Проверка файлов пользователя
    check_user_files()

    # 1. ArcticDEM mosaic
    mosaic = download_arcticdem_mosaic()
    if mosaic is None:
        log('⚠ Mosaic не скачан — модули 01-03 не смогут работать')
        log('  Возможные причины: PGC STAC недоступен, нужен ручный download')
        log('  Альтернатива: скопировать spb_center_arcticdem.tif вручную')

    # 2. ArcticDEM strips
    download_arcticdem_strips()

    # 3. OSM для объекта
    download_osm_object()

    # 4. OSM city-wide
    download_osm_city()

    # Итог
    log('')
    log('=' * 60)
    log('Скачивание завершено. Структура кэша:')
    log('=' * 60)

    # ArcticDEM
    if mosaic and mosaic.exists():
        log(f'  DEM mosaic: {mosaic} ({mosaic.stat().st_size//(1024*1024)}MB)')

    # Strips
    strips = list(STRIPS_DIR.glob('strip_*_vo_clip.tif'))
    if strips:
        log(f'  DEM strips: {len(strips)} файлов в {STRIPS_DIR}')
        manifest = STRIPS_DIR / 'manifest.json'
        if manifest.exists():
            m = json.loads(manifest.read_text())
            log(f'  Manifest: {len(m)} источников')

    # OSM
    osm_files = list(OSM_DIR.glob('*.json'))
    for f in osm_files:
        log(f'  OSM: {f.name} ({f.stat().st_size//(1024*1024)}MB)')

    # User files
    log(f'  User files: {UPLOAD}')

    log('')
    log('Теперь можно запускать:')
    log('  python3 scripts/01_owner_map.py')
    log('  python3 scripts/city_calibration.py')
    log('  python3 scripts/multi_dem_profile.py')
    log('  python3 scripts/02b_profile_fusion.py')
    log('  python3 scripts/02_road_reconstruct.py')
    log('  python3 scripts/02c_full_profile_analysis.py')
    log('  python3 scripts/03_flood_map.py')


if __name__ == '__main__':
    main()
