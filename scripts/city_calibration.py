#!/usr/bin/env python3
"""
============================================================================
 city_calibration.py — калибровка DEM по всем точкам Отметки.txt
============================================================================

Стратегия «кроссворда»:
  • Для КАЖДОЙ точки из Отметки.txt найти ближайшую OSM-дорогу в радиусе 50м
  • На её основе собрать dH = h_dem_ell − h_pas (паспорт)
  • Сгруппировать по улицам — для улиц с ≥3 точками получить робастный shift
  • Единый city_shift = медиана dH по точкам в чистых зонах (road/sidewalk),
    исключая точки на зданиях/воде/далеко от любой дороги
  • Этот shift применяется в модуле 02 для Большого пр-та В.О. (и любой
    другой улицы), потому что bias DEM систематический по всему СПб

Выход: outputs/city_calibration_points.csv  — все 202 точки с привязкой к улице
       outputs/city_calibration.json       — сводка: shift, RMSE, per-street
       outputs/city_qa_map.html            — folium: точки цветом по dH
"""
import json
import sys
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd
import geopandas as gpd
import rasterio
from rasterio.warp import transform as warp_transform, transform_bounds
from shapely.geometry import LineString, Point
from shapely.strtree import STRtree
import requests
import folium

# переиспользуем конфиг и функции модуля 01
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location(
    'owner_map_mod', Path(__file__).parent / '01_owner_map.py')
m01 = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(m01)

OUT = m01.OUT
UTM = m01.UTM
DEM_PATH = m01.DEM_PATH
GEOID_FALLBACK = m01.GEOID_FALLBACK


def log(msg):
    print(f'[{datetime.now():%H:%M:%S}] {msg}', flush=True)


# ============================================================
# 1. Все точки → ближайшая OSM-дорога + DEM-высота + dH
# ============================================================
def fetch_city_highways(bbox):
    W, S, E, N = bbox
    cache = m01.CACHE / 'osm' / f'city_highways_{int(W*1000)}_{int(S*1000)}.json'
    if cache.exists():
        log(f'  OSM из кэша: {cache}')
        return json.loads(cache.read_text())
    q = f"""[out:json][timeout:240];
    (
      way["highway"~"^(motorway|trunk|primary|secondary|tertiary|unclassified|residential|living_street|service)$"]({S},{W},{N},{E});
    );
    out geom;"""
    for url in ('https://maps.mail.ru/osm/tools/overpass/api/interpreter',
                'https://overpass-api.de/api/interpreter'):
        try:
            log(f'  Overpass: {url}')
            r = requests.post(url, data={'data': q}, timeout=240)
            if r.status_code == 200:
                data = r.json()
                cache.write_text(json.dumps(data))
                log(f'  OK: {len(data.get("elements", []))} ways cached')
                return data
        except Exception as e:
            log(f'  ! {e}')
    log('  ! Overpass недоступен'); return {'elements': []}


def sample_dem_at_points(cp, dem_path):
    """Высота DEM (ellipsoidal) в каждой точке."""
    log('Сэмплирование DEM в точках')
    with rasterio.open(str(dem_path)) as src:
        xs, ys = warp_transform('EPSG:4326', src.crs,
                                cp['lon'].tolist(), cp['lat'].tolist())
        h = np.full(len(cp), np.nan)
        for i, (x, y) in enumerate(zip(xs, ys)):
            col = int((x - src.transform.c) / src.transform.a)
            row = int((y - src.transform.f) / src.transform.e)
            if 0 <= row < src.height and 0 <= col < src.width:
                v = src.read(1, window=((row, row+1), (col, col+1)))[0, 0]
                if src.nodata is None or v != src.nodata:
                    h[i] = float(v)
    cp = cp.copy()
    cp['h_dem_ell'] = h
    return cp


def match_to_streets(cp, osm_data):
    """Для каждой точки — ближайший OSM-way в радиусе 50м."""
    log('Матчинг точек к OSM-улицам')
    ways_data = []
    for el in osm_data.get('elements', []):
        if el['type'] != 'way' or 'geometry' not in el:
            continue
        tags = el.get('tags', {})
        coords = [(p['lon'], p['lat']) for p in el['geometry']]
        if len(coords) < 2:
            continue
        ways_data.append({
            'geometry': LineString(coords),
            'name': tags.get('name', ''),
            'highway': tags.get('highway', ''),
            'osm_id': el['id'],
        })
    ways_gdf = gpd.GeoDataFrame(ways_data, crs='EPSG:4326').to_crs(UTM)
    log(f'  {len(ways_gdf)} OSM-ways в кэше')

    pts_gdf = gpd.GeoDataFrame(
        cp, geometry=gpd.points_from_xy(cp.lon, cp.lat),
        crs='EPSG:4326').to_crs(UTM)

    geoms = list(ways_gdf.geometry)
    tree = STRtree(geoms)
    matches = []
    for p in pts_gdf.geometry:
        nearest_idx = tree.nearest(p)
        if nearest_idx is None:
            matches.append({'name': '', 'dist': np.inf, 'highway': '',
                            'osm_id': -1})
            continue
        d = p.distance(geoms[nearest_idx])
        row = ways_gdf.iloc[nearest_idx]
        matches.append({
            'name': row['name'], 'dist': float(d),
            'highway': row['highway'], 'osm_id': int(row['osm_id']),
        })
    md = pd.DataFrame(matches)
    out = pd.concat([cp.reset_index(drop=True), md], axis=1)
    out.drop(columns='geometry', errors='ignore', inplace=True)
    log(f'  Matched within 50m: {(out.dist < 50).sum()}/{len(out)}')
    log(f'  Matched within 15m: {(out.dist < 15).sum()}/{len(out)}')
    log(f'  Matched within  5m: {(out.dist < 5).sum()}/{len(out)}')
    return out


# ============================================================
# 2. Per-street робастный shift + единый city_shift
# ============================================================
def compute_city_shift(cp):
    """Единый shift = h_dem_ell − h_pas по чистым точкам.
    Чистая точка: dist ≤ 15м, named street (не unnamed/service),
    h_pas в разумном диапазоне (исключаем мосты/паркинги/крыши),
    dH внутри 3·MAD от медианы."""
    log('Вычисление city_shift')
    # фильтр 1: рядом с дорогой и имя есть
    near = cp[(cp.dist <= 15) & (cp.name != '') & np.isfinite(cp.h_dem_ell)].copy()
    log(f'  Точек в 15м от named road: {len(near)}')

    # фильтр 2: h_pas разумный (исключаем h_pas > 12м — это мосты/паркинги/крыши,
    # обычные улицы СПб в историческом центре 1-8м БС-77)
    near = near[(near.h_pas >= 0) & (near.h_pas <= 12)].copy()
    log(f'  После h_pas∈[0,12]м: {len(near)}')

    # фильтр 3: dH внутри 3·MAD от медианы (робастный)
    near['dH'] = near.h_dem_ell - near.h_pas
    med = near.dH.median()
    mad = np.median(np.abs(near.dH - med)) * 1.4826
    inlier = np.abs(near.dH - med) <= 3 * max(0.1, mad)
    log(f'  dH: median={med:.2f}м, MAD={mad:.2f}м, inliers={inlier.sum()}/{len(near)}')
    clean = near[inlier].copy()

    # финальный shift = медиана по clean
    shift = float(clean.dH.median())
    log(f'  city_shift = {shift:.3f}м (по {len(clean)} чистым точкам)')
    log(f'  Сравнение с GEOID_FALLBACK={GEOID_FALLBACK:.2f}м: '
        f'bias DEM = {shift - GEOID_FALLBACK:+.2f}м')

    # per-street shift для улиц с ≥3 точками
    street_stats = []
    for name, g in clean.groupby('name'):
        if len(g) < 3:
            continue
        m = g.dH.median()
        s_mad = np.median(np.abs(g.dH - m)) * 1.4826
        street_stats.append({
            'street': name, 'n': len(g),
            'median_dH': float(m),
            'mad_dH': float(s_mad),
            'median_h_pas': float(g.h_pas.median()),
            'median_h_dem_ell': float(g.h_dem_ell.median()),
        })
    street_df = pd.DataFrame(street_stats).sort_values('n', ascending=False)
    log(f'  Улиц с ≥3 чистыми точками: {len(street_df)}')
    return shift, clean, near, street_df


# ============================================================
# 3. QA-карта: все точки цветом по dH после shift
# ============================================================
def qa_map(cp_all, clean, shift, street_df):
    log('QA-карта городской калибровки')
    import folium

    # вычисляем dH после shift для всех точек
    df = cp_all.copy()
    df['dH_residual'] = (df.h_dem_ell - shift) - df.h_pas

    center = [df.lat.mean(), df.lon.mean()]
    m = folium.Map(location=center, zoom_start=11,
                   control_scale=True, tiles=None)
    folium.TileLayer('OpenStreetMap', name='OSM', max_zoom=19).add_to(m)
    folium.TileLayer(
        tiles='https://server.arcgisonline.com/ArcGIS/rest/services/'
              'World_Imagery/MapServer/tile/{z}/{y}/{x}',
        attr='Esri', name='Спутник', max_zoom=19, show=False).add_to(m)

    # все точки: цвет по dH_residual (зелёный=|dH|<0.5, оранжевый<1.5, красный>1.5)
    for _, p in df.iterrows():
        if not np.isfinite(p.get('h_dem_ell', np.nan)):
            color = 'gray'
            dH_str = 'нет DEM'
        else:
            d = p['dH_residual']
            color = 'green' if abs(d) < 0.5 else (
                'orange' if abs(d) < 1.5 else 'red')
            dH_str = f'{d:+.2f}м'
        street = p.get('name', '') or '(unnamed)'
        folium.CircleMarker(
            [p.lat, p.lon], radius=4, color='black', weight=0.5,
            fill=True, fill_color=color, fill_opacity=0.85,
            popup=folium.Popup(
                f"h_pas={p.h_pas:.2f}<br>"
                f"h_dem_ell={p.get('h_dem_ell', float('nan')):.2f}<br>"
                f"shift={shift:.2f}<br>"
                f"dH_residual={dH_str}<br>"
                f"street={street}<br>"
                f"dist={p.get('dist', -1):.1f}м",
                max_width=240)).add_to(m)

    # отметим shift-source в легенде
    m.get_root().html.add_child(folium.Element(
        f'<div style="position:absolute;top:10px;right:10px;z-index:9999;'
        f'background:white;padding:10px;border:1px solid #888;border-radius:5px;'
        f'font-family:Arial;font-size:12px">'
        f'<b>Городская калибровка</b><br>'
        f'shift = {shift:.2f}м (med dH по {len(clean)} точкам)<br>'
        f'GEOID_FALLBACK = {GEOID_FALLBACK:.2f}м<br>'
        f'bias DEM = {shift - GEOID_FALLBACK:+.2f}м<br>'
        f'зелёный: |dH|&lt;0.5м, оранжевый: &lt;1.5м, красный: &gt;1.5м<br>'
        f'серый: нет DEM в точке'
        f'</div>'))

    m.fit_bounds([[df.lat.min(), df.lon.min()],
                  [df.lat.max(), df.lon.max()]])
    folium.LayerControl(collapsed=False).add_to(m)
    path = OUT / 'city_qa_map.html'
    m.save(str(path))
    log(f'  ✓ {path}')


# ============================================================
# MAIN
# ============================================================
def main():
    log('=' * 60)
    log('city_calibration: калибровка DEM по всем точкам Отметки.txt')
    log('=' * 60)

    cp = m01.load_control_points()
    log(f'Точек: {len(cp)}')
    log(f'Lat: {cp.lat.min():.4f}..{cp.lat.max():.4f}, '
        f'Lon: {cp.lon.min():.4f}..{cp.lon.max():.4f}')

    # bbox по точкам + margin
    margin = 0.005
    bbox = (cp.lon.min() - margin, cp.lat.min() - margin,
            cp.lon.max() + margin, cp.lat.max() + margin)

    # 1. OSM highways для всего bbox
    osm = fetch_city_highways(bbox)

    # 2. DEM-высота в каждой точке
    cp = sample_dem_at_points(cp, DEM_PATH)
    n_valid = int(np.isfinite(cp.h_dem_ell).sum())
    log(f'Точек с валидной h_dem_ell: {n_valid}/{len(cp)}')

    # 3. Match к ближайшей улице
    cp = match_to_streets(cp, osm)

    # 4. dH = h_dem_ell − h_pas
    cp['dH'] = cp.h_dem_ell - cp.h_pas

    # 5. city_shift
    shift, clean, near, street_df = compute_city_shift(cp)

    # 6. Сохранить CSV + JSON
    cp.to_csv(OUT / 'city_calibration_points.csv', index=False)
    log(f'  ✓ {OUT / "city_calibration_points.csv"}')

    # Holdout RMSE по чистым точкам (для честной оценки)
    rng = np.random.RandomState(42)
    clean = clean.copy()
    clean['holdout'] = False
    n_ho = max(1, int(len(clean) * 0.25))
    clean.loc[rng.choice(clean.index, n_ho, replace=False), 'holdout'] = True
    ho = clean[clean.holdout]
    rmse = float(np.sqrt(np.mean((ho.dH - shift) ** 2))) if len(ho) else None
    mae = float(np.mean(np.abs(ho.dH - shift))) if len(ho) else None
    log(f'Holdout: n={len(ho)}, RMSE={rmse:.3f}м, MAE={mae:.3f}м')

    qa = {
        'n_total': int(len(cp)),
        'n_with_dem': n_valid,
        'n_near_road_50m': int((cp.dist < 50).sum()),
        'n_near_road_15m': int((cp.dist < 15).sum()),
        'n_clean_calibration': int(len(clean)),
        'shift_m': shift,
        'shift_source': 'city_calibration',
        'geoid_fallback_m': GEOID_FALLBACK,
        'bias_dem_m': shift - GEOID_FALLBACK,
        'rmse_holdout_m': rmse,
        'mae_holdout_m': mae,
        'n_holdout': int(len(ho)),
        'streets_with_3plus_points': int(len(street_df)),
        'streets_top': street_df.head(20).to_dict(orient='records'),
    }
    (OUT / 'city_calibration.json').write_text(
        json.dumps(qa, ensure_ascii=False, indent=2))
    log(f'  ✓ {OUT / "city_calibration.json"}')

    # 7. QA-карта
    qa_map(cp, clean, shift, street_df)

    log('')
    log('ГОТОВО. shift можно использовать в модуле 02 как '
        f'CITY_SHIFT={shift:.3f} (заменить GEOID_FALLBACK)')


if __name__ == '__main__':
    main()
