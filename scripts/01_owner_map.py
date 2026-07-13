#!/usr/bin/env python3
"""
============================================================================
 01_owner_map.py — семантическое разбиение DEM по объектам OSM (ТЗ 5.1 v2)
============================================================================

Заменяет "ось ± 8м" на реальную геометрию:
  1. Обе проезжие части дуальных проспектов (все way с точным именем,
     БЕЗ выбрасывания веток MultiLineString).
  2. Ширина каждого way: тег width → lanes×3.25+1.0 → дефолт по классу.
     Полигоны area:highway используются напрямую, если размечены.
  3. Бульвар/зелень/деревья (landuse, leisure, natural, tree_row) —
     ВЫЧИТАЮТСЯ из проезжей части и получают своего владельца.
  4. Здания (way + multipolygon-relation) с буфером +3м.

Выход — owner map: uint8-растр на сетке DEM (2м), каждому пикселю — владелец:
  0 other | 1 road (проезжая часть) | 2 sidewalk | 3 green | 4 building(+3м)
  5 water | + band 2: quality (0 clean, 1 mixed — пиксель на границе владельцев)

QA-выходы (проверить ГЛАЗАМИ до фильтрации):
  outputs/owner_map.tif           — растр владельцев (2 band)
  outputs/carriageway.geojson     — полигоны проезжих частей
  outputs/owner_qa_map.html       — folium: полигоны+растр на спутнике + точки
  outputs/control_points_qa.csv   — каждая опорная точка: владелец, h_DEM,
                                    внутри ли патча (чинит "(nodata)"-багу)
  outputs/owner_qa.json           — числовые метрики

Следующий шаг (отдельный модуль 02): робастная модель h=f(picket)+g(offset)
по пикселям владельца 1 (road) — здесь только геометрия и разнесение.
"""
import json
import re
import sys
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd
import geopandas as gpd
import rasterio
from rasterio.features import rasterize
from rasterio.windows import from_bounds
from rasterio.warp import transform as warp_transform, transform_bounds
from shapely.geometry import LineString, Point, Polygon, MultiPolygon, box, shape
from shapely.ops import unary_union, linemerge
import requests
import folium

# ============================================================
# КОНФИГУРАЦИЯ
# ============================================================
HOME = Path('/home/z/my-project')
UPLOAD = HOME / 'upload'
CACHE = HOME / 'scripts' / 'dem_cache'
OUT = HOME / 'download'
for d in (CACHE / 'osm', OUT):
    d.mkdir(parents=True, exist_ok=True)

DEM_PATH = CACHE / 'arctic' / 'spb_center_arcticdem.tif'
UTM = 'EPSG:32636'

MVP_BBOX = (30.20, 59.92, 30.32, 59.95)          # W, S, E, N
MVP_NAME = "Большой проспект В.О."
PATCH_MARGIN_M = 60          # запас патча вокруг проезжих частей

LANE_W = 3.25                # м на полосу
ROAD_EXTRA = 1.0             # лотки/примыкание к бордюру, суммарно на way
BLD_BUFFER = 3.0             # буфер зданий (стены "наклоняются" в стерео-DSM)
GREEN_BUFFER = 2.0           # буфер зелени (кроны шире контура)
TREE_ROW_HALF_W = 4.0        # полуширина полосы деревьев из линии tree_row
SIDEWALK_W = 2.5             # дефолтная ширина тротуара
GEOID_FALLBACK = 15.9        # только для QA-колонки h_bs77 в CSV

# дефолтные ЧИСЛА ПОЛОС для одного way, если нет тегов width/lanes.
# NB: у односторонних way дуальных проспектов lanes задаётся на направление.
DEFAULT_LANES = {
    'motorway': 3, 'trunk': 3, 'primary': 3, 'secondary': 2,
    'tertiary': 2, 'residential': 2, 'unclassified': 2, 'service': 1,
    'living_street': 1,
}
# сервисные классы, которые не считаем проезжей частью объекта
SKIP_HIGHWAY = {'footway', 'path', 'steps', 'cycleway', 'pedestrian',
                'platform', 'construction', 'proposed'}

OWNER_CODES = {'other': 0, 'road': 1, 'sidewalk': 2, 'green': 3,
               'building': 4, 'water': 5}
OWNER_NAMES = {v: k for k, v in OWNER_CODES.items()}
OWNER_COLORS = {0: (200, 200, 200), 1: (60, 60, 60), 2: (250, 170, 90),
                3: (80, 170, 80), 4: (200, 60, 60), 5: (70, 120, 220)}


def log(msg):
    print(f'[{datetime.now():%H:%M:%S}] {msg}', flush=True)


# ============================================================
# OSM: скачивание и разбор
# ============================================================
def fetch_osm(bbox):
    """Highways + area:highway + buildings(way,relation) + green + water."""
    W, S, E, N = bbox
    cache = CACHE / 'osm' / 'owner_map_osm.json'
    if cache.exists():
        log(f'  OSM из кэша: {cache}')
        return json.loads(cache.read_text())
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
    for url in ('https://maps.mail.ru/osm/tools/overpass/api/interpreter',
                'https://overpass-api.de/api/interpreter'):
        try:
            log(f'  Overpass: {url}')
            r = requests.post(url, data={'data': q}, timeout=240)
            if r.status_code == 200 and r.text.strip():
                data = r.json()
                cache.write_text(json.dumps(data))
                log(f'  OK: {len(data.get("elements", []))} элементов, кэш → {cache}')
                return data
        except Exception as e:
            log(f'  ! {e}')
    log('  ! Overpass недоступен'); return {'elements': []}


def _way_line(el):
    if el.get('type') != 'way' or 'geometry' not in el:
        return None
    c = [(p['lon'], p['lat']) for p in el['geometry']]
    return c if len(c) >= 2 else None


def _iter_polygons(el):
    """Полигоны из way (замкнутый) или multipolygon-relation (outer-члены)."""
    if el.get('type') == 'way':
        c = _way_line(el)
        if c and len(c) >= 4 and c[0] == c[-1]:
            try:
                p = Polygon(c)
                if p.is_valid and p.area > 0:
                    yield p
            except Exception:
                pass
    elif el.get('type') == 'relation':
        for m in el.get('members', []):
            if m.get('role') == 'inner' or 'geometry' not in m:
                continue
            c = [(p['lon'], p['lat']) for p in m['geometry']]
            if len(c) >= 4:
                if c[0] != c[-1]:
                    c.append(c[0])
                try:
                    p = Polygon(c)
                    if p.is_valid and p.area > 0:
                        yield p
                except Exception:
                    pass


def parse_width_m(tags):
    """width → метры; поддержка '12', '12.5', '12,5', '12 m'. None если нет."""
    for key in ('width', 'width:carriageway'):
        v = tags.get(key)
        if not v:
            continue
        m = re.match(r'^\s*(\d+(?:[.,]\d+)?)\s*(?:m|м)?\s*$', str(v))
        if m:
            w = float(m.group(1).replace(',', '.'))
            if 2.0 <= w <= 60.0:
                return w
    return None


def way_carriageway_width(tags):
    """Ширина проезжей части одного way, м."""
    w = parse_width_m(tags)
    if w is not None:
        return w
    lanes = tags.get('lanes')
    try:
        lanes = int(str(lanes).split(';')[0])
    except (TypeError, ValueError):
        lanes = None
    if lanes is None:
        lanes = DEFAULT_LANES.get(tags.get('highway', ''), 2)
    return lanes * LANE_W + ROAD_EXTRA


# ============================================================
# Геометрия объекта: ВСЕ проезжие части + справочная ось
# ============================================================
def build_object_geometry(osm_data, bbox, object_name):
    W, S, E, N = bbox
    log(f'Геометрия объекта: {object_name}')

    def name_match(tags):
        return tags.get('name', '').strip() == object_name

    def name_match_fuzzy(tags):
        n = tags.get('name', '')
        return 'ольшой' in n and ('В.О' in n or 'Васильевск' in n)

    for matcher, label in ((name_match, 'точное имя'),
                           (name_match_fuzzy, 'fuzzy')):
        ways, area_polys = [], []
        for el in osm_data.get('elements', []):
            tags = el.get('tags', {})
            if not matcher(tags):
                continue
            # площадной контур асфальта, если размечен
            if tags.get('area:highway') or tags.get('area') == 'yes':
                for p in _iter_polygons(el):
                    area_polys.append(p)
                continue
            hw = tags.get('highway')
            if not hw or hw in SKIP_HIGHWAY:
                continue
            c = _way_line(el)
            if not c:
                continue
            if any(W <= x <= E and S <= y <= N for x, y in c):
                ways.append({'line': LineString(c), 'tags': tags})
        if ways or area_polys:
            log(f'  Совпадение по "{label}": {len(ways)} way, '
                f'{len(area_polys)} area:highway')
            break
    else:
        raise RuntimeError(f'В OSM не найдено ни одного way "{object_name}"')

    # --- проезжая часть: union буферов всех way (обе стороны дуали!) ---
    gdf = gpd.GeoDataFrame(
        {'geometry': [w['line'] for w in ways],
         'width': [way_carriageway_width(w['tags']) for w in ways],
         'highway': [w['tags'].get('highway') for w in ways],
         'oneway': [w['tags'].get('oneway', 'no') for w in ways],
         'lanes': [w['tags'].get('lanes') for w in ways],
         'width_tag': [w['tags'].get('width') for w in ways]},
        crs='EPSG:4326').to_crs(UTM)
    log('  Ширины way (м): ' +
        ', '.join(f'{w:.1f}' for w in sorted(set(round(x, 1) for x in gdf["width"]))))

    # cap_style=2 (flat): буфер не вылезает за торцы way
    buffers = [geom.buffer(w / 2.0, cap_style=2)
               for geom, w in zip(gdf.geometry, gdf['width'])]
    carriageway_utm = unary_union(buffers)
    if area_polys:
        ap = gpd.GeoDataFrame({'geometry': area_polys},
                              crs='EPSG:4326').to_crs(UTM)
        carriageway_utm = unary_union([carriageway_utm] + list(ap.geometry))
    log(f'  Проезжая часть: {carriageway_utm.area:.0f} м² '
        f'({carriageway_utm.area / max(1e-9, gdf.length.sum()):.1f} м '
        f'средней ширины на суммарную длину)')

    # --- справочная ось для пикетажа (координатная система, НЕ маска) ---
    merged = linemerge(unary_union(list(gdf.geometry)))
    n_parts = 1
    if merged.geom_type == 'MultiLineString':
        n_parts = len(merged.geoms)
        axis_utm = max(merged.geoms, key=lambda g: g.length)
        log(f'  Ось: MultiLineString из {n_parts} веток; самая длинная '
            f'{axis_utm.length:.0f} м взята как ось пикетажа '
            f'(маска при этом включает ВСЕ ветки)')
    else:
        axis_utm = merged
        log(f'  Ось: {axis_utm.length:.0f} м')

    # тротуары объекта: footway рядом с проезжей частью
    sidewalks = []
    near = carriageway_utm.buffer(20.0)
    for el in osm_data.get('elements', []):
        tags = el.get('tags', {})
        if tags.get('highway') not in ('footway', 'pedestrian', 'path'):
            continue
        c = _way_line(el)
        if not c:
            continue
        ls = gpd.GeoSeries([LineString(c)], crs='EPSG:4326').to_crs(UTM).iloc[0]
        if ls.intersects(near):
            w = parse_width_m(tags) or SIDEWALK_W
            sidewalks.append(ls.buffer(w / 2.0, cap_style=2))
    sidewalk_utm = unary_union(sidewalks) if sidewalks else None
    log(f'  Тротуары рядом: {len(sidewalks)} way')

    return carriageway_utm, axis_utm, gdf, sidewalk_utm


def collect_layers(osm_data):
    """Здания / зелень / вода со всего bbox (EPSG:4326)."""
    buildings, greens, waters = [], [], []
    for el in osm_data.get('elements', []):
        tags = el.get('tags', {})
        if 'building' in tags:
            buildings.extend(_iter_polygons(el))
            continue
        if (tags.get('landuse') in {'grass', 'forest', 'meadow',
                                    'village_green', 'flowerbed'}
                or tags.get('leisure') in {'park', 'garden', 'playground',
                                           'common'}
                or tags.get('natural') in {'wood', 'scrub', 'wetland'}):
            greens.extend(_iter_polygons(el))
            continue
        if tags.get('natural') == 'tree_row':
            c = _way_line(el)
            if c:
                greens.append(('tree_row', LineString(c)))
            continue
        if tags.get('natural') == 'water' or \
                tags.get('waterway') in {'river', 'canal', 'riverbank'}:
            got = list(_iter_polygons(el))
            if got:
                waters.extend(got)
            else:
                c = _way_line(el)
                if c:
                    waters.append(('line', LineString(c)))
    log(f'Слои OSM: buildings={len(buildings)}, green={len(greens)}, '
        f'water={len(waters)}')
    if len(buildings) == 0:
        log('  ⚠ НОЛЬ зданий — проверьте Overpass-ответ/кэш, это критично!')
    return buildings, greens, waters


def _to_utm_buffered(items, buffer_m, line_half_w):
    """Список Polygon | ('tree_row'|'line', LineString) → union в UTM."""
    geoms = []
    for it in items:
        if isinstance(it, tuple):
            g = gpd.GeoSeries([it[1]], crs='EPSG:4326').to_crs(UTM).iloc[0]
            geoms.append(g.buffer(line_half_w, cap_style=2))
        else:
            g = gpd.GeoSeries([it], crs='EPSG:4326').to_crs(UTM).iloc[0]
            geoms.append(g.buffer(buffer_m) if buffer_m else g)
    return unary_union(geoms) if geoms else None


# ============================================================
# Owner map: растеризация на сетке DEM
# ============================================================
def rasterize_owners(dem_path, carriageway_utm, sidewalk_utm,
                     buildings_utm, green_utm, water_utm):
    log('Растеризация owner map на сетке DEM')
    with rasterio.open(str(dem_path)) as src:
        dem_crs = src.crs
        pad = carriageway_utm.buffer(PATCH_MARGIN_M)
        pad_dem = gpd.GeoSeries([pad], crs=UTM).to_crs(dem_crs).iloc[0]
        win = from_bounds(*pad_dem.bounds, src.transform)
        win = win.round_offsets(op='floor').round_lengths(op='ceil')
        transform = rasterio.windows.transform(win, src.transform)
        H = int(win.height)
        Wd = int(win.width)
        dem = src.read(1, window=win).astype('float32')
        if src.nodata is not None:
            dem[dem == src.nodata] = np.nan
    log(f'  Патч: {Wd}×{H} px (2м)')

    def to_dem(geom):
        if geom is None or geom.is_empty:
            return []
        g = gpd.GeoSeries([geom], crs=UTM).to_crs(dem_crs).iloc[0]
        return [g]

    owner = np.zeros((H, Wd), dtype='uint8')  # 0 = other
    # приоритет снизу вверх: следующая растеризация ПЕРЕЗАПИСЫВАЕТ предыдущую
    layers = [
        ('sidewalk', to_dem(sidewalk_utm)),
        ('road',     to_dem(carriageway_utm)),
        ('green',    to_dem(green_utm)),      # бульвар режет проезжую часть
        ('water',    to_dem(water_utm)),
        ('building', to_dem(buildings_utm)),  # здания — высший приоритет
    ]
    for name, geoms in layers:
        if not geoms:
            continue
        m = rasterize([(g, 1) for g in geoms], out_shape=(H, Wd),
                      transform=transform, fill=0, all_touched=True,
                      dtype='uint8')
        owner[m == 1] = OWNER_CODES[name]

    # mixed: пиксель, у которого 4-сосед другого владельца (бордюр/стена)
    mixed = np.zeros_like(owner, dtype=bool)
    for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        nb = np.roll(owner, (dy, dx), axis=(0, 1))
        if dy == -1: nb[0, :] = owner[0, :]
        if dy == 1:  nb[-1, :] = owner[-1, :]
        if dx == -1: nb[:, 0] = owner[:, 0]
        if dx == 1:  nb[:, -1] = owner[:, -1]
        mixed |= (nb != owner)
    quality = mixed.astype('uint8')  # 0 clean, 1 mixed

    counts = {OWNER_NAMES[c]: int((owner == c).sum())
              for c in np.unique(owner)}
    road_clean = int(((owner == OWNER_CODES['road']) & ~mixed).sum())
    log(f'  Владельцы (px): {counts}')
    log(f'  road clean (без границ): {road_clean:,} px '
        f'= {road_clean * 4 / 1e4:.2f} га')

    out_path = OUT / 'owner_map.tif'
    prof = {'driver': 'GTiff', 'dtype': 'uint8', 'count': 2, 'crs': dem_crs,
            'transform': transform, 'width': Wd, 'height': H,
            'compress': 'deflate', 'nodata': None}
    with rasterio.open(str(out_path), 'w', **prof) as dst:
        dst.write(owner, 1)
        dst.write(quality, 2)
        dst.set_band_description(1, 'owner: 0 other 1 road 2 sidewalk '
                                    '3 green 4 building 5 water')
        dst.set_band_description(2, 'quality: 0 clean, 1 mixed boundary')
    log(f'  ✓ {out_path}')
    return owner, quality, dem, transform, dem_crs, counts, road_clean


# ============================================================
# Опорные точки: правильное сэмплирование (чинит "(nodata)")
# ============================================================
def load_control_points():
    pts = []
    path = UPLOAD / 'Отметки.txt'
    if not path.exists():
        log(f'  ! Нет {path}')
        return pd.DataFrame(columns=['lat', 'lon', 'h_pas'])
    for raw in open(path, encoding='utf-8'):
        line = raw.strip()
        if not line or ':' not in line:
            continue
        l, r = line.split(':', 1)
        m = re.match(r'^(\d+[.,]\d+)\s*,\s*(\d+[.,]\d+)$', l.strip())
        if not m:
            continue
        lat = float(m.group(1).replace(',', '.'))
        lon = float(m.group(2).replace(',', '.'))
        m2 = re.match(r'^(-?\d+[.,]?\d*)', r.strip())
        if not m2:
            continue
        pts.append({'lat': lat, 'lon': lon,
                    'h_pas': float(m2.group(1).replace(',', '.'))})
    return pd.DataFrame(pts)


def sample_control_points(cp, dem, owner, quality, transform, dem_crs,
                          carriageway_utm):
    """Каждой точке: row/col, h_DEM, владелец, качество, расстояние до дороги.
    Это диагностический CSV — по нему видно, почему точка (не)валидна."""
    log('Сэмплирование опорных точек на owner map')
    if cp.empty:
        return cp
    xs, ys = warp_transform('EPSG:4326', dem_crs,
                            cp['lon'].tolist(), cp['lat'].tolist())
    inv = ~transform
    rows, cols, hdem, own, qual = [], [], [], [], []
    for x, y in zip(xs, ys):
        fc, fr = inv * (x, y)
        r, c = int(np.floor(fr)), int(np.floor(fc))
        rows.append(r); cols.append(c)
        if 0 <= r < dem.shape[0] and 0 <= c < dem.shape[1]:
            v = dem[r, c]
            hdem.append(float(v) if np.isfinite(v) else np.nan)
            own.append(OWNER_NAMES[int(owner[r, c])])
            qual.append(int(quality[r, c]))
        else:
            hdem.append(np.nan); own.append('out_of_patch'); qual.append(-1)
    cp = cp.copy()
    cp['row'], cp['col'] = rows, cols
    cp['h_dem_ell'] = hdem
    cp['h_dem_bs77'] = cp['h_dem_ell'] - GEOID_FALLBACK
    cp['owner'] = own
    cp['mixed'] = qual
    pts_utm = gpd.GeoDataFrame(
        cp, geometry=gpd.points_from_xy(cp['lon'], cp['lat']),
        crs='EPSG:4326').to_crs(UTM)
    cp['dist_to_road_m'] = pts_utm.geometry.distance(carriageway_utm).values
    cp['dH_raw'] = cp['h_dem_bs77'] - cp['h_pas']

    n_in = int((cp['owner'] != 'out_of_patch').sum())
    n_road = int((cp['owner'] == 'road').sum())
    n_valid = int(np.isfinite(cp['h_dem_ell']).sum())
    log(f'  Всего точек: {len(cp)}; в патче: {n_in}; с валидной h_DEM: '
        f'{n_valid}; владелец=road: {n_road}')
    if n_valid:
        sub = cp[np.isfinite(cp['dH_raw'])]
        log(f'  dH_raw (DEM-BS77 − паспорт): median='
            f'{sub["dH_raw"].median():+.2f}м, '
            f'IQR=[{sub["dH_raw"].quantile(.25):+.2f}, '
            f'{sub["dH_raw"].quantile(.75):+.2f}]')
    csv_path = OUT / 'control_points_qa.csv'
    cp.drop(columns='geometry', errors='ignore').to_csv(csv_path, index=False)
    log(f'  ✓ {csv_path}')
    return cp


# ============================================================
# QA-карта
# ============================================================
def qa_map(owner, quality, transform, dem_crs, carriageway_utm, axis_utm,
           cp, green_utm, buildings_utm):
    log('QA-карта')
    import io, base64
    from PIL import Image

    # owner → RGBA. "other" полностью прозрачен, чтобы не заливать спутник.
    # meaningful owners — насыщенные и непрозрачные, mixed пиксели чуть прозрачнее.
    # Цель: на спутнике должно быть чётко видно, где road / green / building.
    rgba = np.zeros((*owner.shape, 4), dtype='uint8')
    for code, col in OWNER_COLORS.items():
        m = owner == code
        rgba[m, 0], rgba[m, 1], rgba[m, 2] = col
        if code == 0:
            rgba[m, 3] = 0           # other — не рисуем, видим спутник
        else:
            rgba[m, 3] = 230         # владельцы — почти непрозрачные
    # mixed (boundary) — полупрозрачно, чтобы видеть "шов"
    rgba[quality == 1, 3] = np.minimum(rgba[quality == 1, 3].astype(int), 110)\
                                .astype('uint8')

    # bounds патча в 4326
    H, Wd = owner.shape
    xs = [transform.c, transform.c + transform.a * Wd]
    ys = [transform.f, transform.f + transform.e * H]
    w4, s4, e4, n4 = transform_bounds(dem_crs, 'EPSG:4326',
                                      min(xs), min(ys), max(xs), max(ys))
    buf = io.BytesIO()
    Image.fromarray(rgba).save(buf, format='PNG', optimize=True)
    b64 = base64.b64encode(buf.getvalue()).decode()

    cw_4326 = gpd.GeoSeries([carriageway_utm], crs=UTM).to_crs('EPSG:4326').iloc[0]
    ax_4326 = gpd.GeoSeries([axis_utm], crs=UTM).to_crs('EPSG:4326').iloc[0]

    # Легенда по точкам пути
    center_lat = (s4 + n4) / 2
    center_lon = (w4 + e4) / 2
    m = folium.Map(location=[center_lat, center_lon], zoom_start=15,
                   control_scale=True, tiles=None)
    folium.TileLayer('OpenStreetMap', name='OSM', max_zoom=19).add_to(m)
    folium.TileLayer(
        tiles='https://server.arcgisonline.com/ArcGIS/rest/services/'
              'World_Imagery/MapServer/tile/{z}/{y}/{x}',
        attr='Esri', name='Спутник', max_zoom=19, show=True).add_to(m)
    folium.raster_layers.ImageOverlay(
        image=f'data:image/png;base64,{b64}',
        bounds=[[s4, w4], [n4, e4]], opacity=0.95,
        name='Owner map (тёмн=road, оранж=sidewalk, зел=green, '
             'красн=building, син=water)').add_to(m)
    folium.GeoJson(cw_4326.__geo_interface__, name='Проезжая часть (полигон)',
                   style_function=lambda x: {'color': 'yellow', 'weight': 3,
                                             'fillOpacity': 0,
                                             'dashArray': '5,3'}).add_to(m)
    folium.GeoJson(ax_4326.__geo_interface__, name='Ось пикетажа',
                   style_function=lambda x: {'color': 'magenta',
                                             'weight': 3}).add_to(m)
    if cp is not None and not cp.empty:
        for _, p in cp.iterrows():
            if p['owner'] == 'out_of_patch':
                continue
            col = {'road': 'lime', 'sidewalk': 'yellow',
                   'other': 'orange', 'green': 'green',
                   'building': 'red', 'water': 'cyan'}.get(p['owner'], 'white')
            folium.CircleMarker(
                [p['lat'], p['lon']], radius=6, color='white', weight=2,
                fill=True, fill_color=col, fill_opacity=0.95,
                popup=folium.Popup(
                    f"h_pas={p['h_pas']:.2f}<br>owner={p['owner']}"
                    f"<br>h_dem_bs77={p['h_dem_bs77']:.2f}"
                    f"<br>dH_raw={p['dH_raw']:+.2f}"
                    f"<br>до дороги {p['dist_to_road_m']:.0f}м",
                    max_width=260)).add_to(m)
    # Авто-fit по bounds патча, чтобы сразу видеть весь кусок
    m.fit_bounds([[s4, w4], [n4, e4]])
    folium.LayerControl(collapsed=False).add_to(m)
    path = OUT / 'owner_qa_map.html'
    m.save(str(path))
    log(f'  ✓ {path}')

    gpd.GeoDataFrame({'geometry': [cw_4326]}, crs='EPSG:4326').to_file(
        OUT / 'carriageway.geojson', driver='GeoJSON')
    log(f'  ✓ {OUT / "carriageway.geojson"}')


# ============================================================
# MAIN
# ============================================================
def main():
    log('=' * 60)
    log('01_owner_map: семантическое разбиение DEM по OSM')
    log('=' * 60)
    if not DEM_PATH.exists():
        log(f'ERROR: нет DEM {DEM_PATH}'); sys.exit(1)

    osm = fetch_osm(MVP_BBOX)
    carriageway, axis, ways_gdf, sidewalk = build_object_geometry(
        osm, MVP_BBOX, MVP_NAME)
    buildings, greens, waters = collect_layers(osm)
    buildings_utm = _to_utm_buffered(buildings, BLD_BUFFER, 0)
    green_utm = _to_utm_buffered(greens, GREEN_BUFFER, TREE_ROW_HALF_W)
    water_utm = _to_utm_buffered(waters, 0.0, 10.0)

    (owner, quality, dem, transform, dem_crs,
     counts, road_clean) = rasterize_owners(
        DEM_PATH, carriageway, sidewalk, buildings_utm, green_utm, water_utm)

    cp = load_control_points()
    cp = sample_control_points(cp, dem, owner, quality, transform, dem_crs,
                               carriageway)

    qa_map(owner, quality, transform, dem_crs, carriageway, axis,
           cp, green_utm, buildings_utm)

    qa = {
        'object': MVP_NAME,
        'n_ways': int(len(ways_gdf)),
        'axis_len_m': float(axis.length),
        'carriageway_area_m2': float(carriageway.area),
        'owner_px': counts,
        'road_clean_px': road_clean,
        'green_cuts_road': bool(green_utm is not None and
                                green_utm.intersects(carriageway)),
        'checks': {
            'маска на асфальте, не на бульваре':
                'открыть owner_qa_map.html на спутнике: тёмное = road '
                'должно лежать на проезжих частях ПО ОБЕ стороны бульвара',
            'обе стороны дуали': 'жёлтый полигон должен покрывать обе '
                'проезжие части по всей длине',
            'точки': 'в control_points_qa.csv точки с owner=road и '
                'dist_to_road_m<5 — рабочий набор для калибровки',
        },
    }
    (OUT / 'owner_qa.json').write_text(
        json.dumps(qa, ensure_ascii=False, indent=2))
    log(f'  ✓ {OUT / "owner_qa.json"}')
    log('')
    log('ГОТОВО. Проверить глазами owner_qa_map.html, затем модуль 02 '
        '(робастная реконструкция по владельцу road).')


if __name__ == '__main__':
    main()
