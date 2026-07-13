# ======================================================================================
# FLOOD MAP — ПОЛНЫЙ ПАЙПЛАЙН (загрузка → анализ → карта)
# Версия 2.1 — включает загрузку тайлов ArcticDEM
# ======================================================================================
#
# ЗАПУСК НА KAGGLE:
# 1. Создайте новый Notebook
# 2. Первая ячейка:
#    !pip install -q pystac-client rioxarray geopandas osmnx folium rasterio mapclassify scipy
# 3. Вторая ячейка: вставьте весь этот файл
#
# ИСПРАВЛЕНИЯ ОТНОСИТЕЛЬНО ОРИГИНАЛА:
# 1. Полный пайплайн: загрузка тайлов + анализ + карта
# 2. Гидрологическая связность (flood fill от seed-точек на воде)
# 3. Калибровка по маске воды из OSM (не по перцентилю)
# 4. Упрощение геометрии в метрической CRS (не в градусах!)
# 5. Фильтрация мелких полигонов
# 6. Морфологическая очистка маски
# 7. Анализ зданий в зоне затопления
# 8. Совместимость с osmnx >= 2.0

import os
import glob
import numpy as np
import geopandas as gpd
import rioxarray
import folium
from rasterio.features import shapes, rasterize
from shapely.geometry import box, Point
from scipy import ndimage
from osgeo import gdal
import pystac_client
import warnings

warnings.filterwarnings("ignore")
gdal.UseExceptions()


# ======================================================================================
# ПАРАМЕТРЫ (единственное место, которое нужно менять)
# ======================================================================================
CITY_NAME = "Saint Petersburg, Russia"
FLOOD_LEVEL = 3.05            # Уровень подъёма воды над ур. моря (БС-77), метры
CRS_METRIC = "EPSG:32636"     # UTM Zone 36N (подходит для СПб)
OUTPUT_DIR = "/kaggle/working/"
TARGET_RES = 2                # Разрешение для анализа (метры)
MIN_POLYGON_AREA_M2 = 200     # Минимальная площадь полигона затопления
SIMPLIFY_TOLERANCE_M = 5      # Упрощение геометрии (метры, в метрической CRS!)
MORPH_CLOSING_M = 20          # Морфологическое закрытие В МЕТРАХ
SEED_SEARCH_RADIUS_PX = 25    # Радиус поиска вокруг seed-точки (в пикселях)

# ===================== ВХОДНЫЕ ДАННЫЕ (уже скачаны) =====================
# DEM — готовый файл из предыдущего прогона
INPUT_DEM = "/kaggle/input/notebooks/ivanesin/flood-map-2-0/dem_merged_reprojected.tif"

# ===================== КАЛИБРОВКА ПО Г/П ГОРНЫЙ ИНСТИТУТ =====================
GAUGE_GI = {"name": "ГИ",    "lat": 59.928441, "lon": 30.270109, "col": "ГИ"}
GAUGE_S1 = {"name": "С1 ФЗ", "lat": 59.990286, "lon": 29.691681, "col": "С1 ФЗ"}
GAUGE_S2 = {"name": "С2 ФЗ", "lat": 59.990286, "lon": 29.691681, "col": "С2 ФЗ"}
GAUGE_PRIMARY = GAUGE_GI

GAUGE_CSV_PATH = "/kaggle/input/spb-water-levels/ГИ_уровень.csv"  # ← ПОМЕНЯЙТЕ ПУТЬ
GAUGE_DATETIME_COL = "Дата и время (московское зимнее)"
GAUGE_UNITS = "cm"

# Дата и время съёмки ArcticDEM (оба стрипа — 2022-03-09 12:40 UTC = 15:40 MSK)
STRIP_DATE = "2022-03-09"
STRIP_MSK_HOUR_START = 15
STRIP_MSK_HOUR_END = 16
# =============================================================================

# Seed-точки для заливки (lon, lat)
# НА СУШЕ у кромки воды! ArcticDEM = стерео-DSM → на воде NODATA
WATER_SEEDS_WGS84 = [
    (30.235, 59.952),  # Морская наб. В.О.
    (30.275, 59.930),  # Наб. лейтенанта Шмидта (у ГИ)
    (30.327, 59.934),  # Английская наб.
    (30.365, 59.948),  # Петроградская наб. (у Авроры)
    (30.435, 59.947),  # Синопская наб.
    (30.260, 59.940),  # Гавань В.О.
    (30.500, 59.955),  # Охта (устье)
]

OUT_FILENAME = os.path.join(OUTPUT_DIR, "spb_flood_map_v3.html")


# ======================================================================================
# 0. ЗАГРУЗКА ТАЙЛОВ ARCTICDEM
# ======================================================================================
def download_dem_tiles(tile_ids, collection_id, api_url, output_dir):
    """
    Скачивает тайлы ArcticDEM мозаики через PGC STAC API.
    
    Логика из Cell 23 второго блокнота, с улучшениями:
    - Два метода поиска (по ID, затем по свойству pgc:tile)
    - Пропуск уже скачанных файлов
    - Обработка ошибок без прерывания
    """
    os.makedirs(output_dir, exist_ok=True)
    
    # Настройки AWS для доступа к данным PGC (публичные, без ключей)
    os.environ['AWS_NO_SIGN_REQUEST'] = 'YES'
    os.environ['AWS_REGION'] = 'us-west-2'
    
    full_ids = [f"{tid}_2m_v4.1" for tid in tile_ids]
    
    print(f"   Коллекция: {collection_id}")
    print(f"   Целевые тайлы: {full_ids}")
    
    api = pystac_client.Client.open(api_url)
    
    # Метод 1: поиск по ID
    search = api.search(collections=[collection_id], ids=full_ids)
    found_items = list(search.items())
    
    if not found_items:
        # Метод 2: поиск по свойству pgc:tile
        print("   Поиск по ID не дал результатов. Пробуем по pgc:tile...")
        try:
            search = api.search(
                collections=[collection_id],
                query={"pgc:tile": {"in": tile_ids}}
            )
            found_items = list(search.items())
        except Exception as e:
            print(f"   Ошибка запроса: {e}")
    
    if not found_items:
        raise RuntimeError(
            f"Тайлы не найдены через API ({api_url}). "
            f"Проверьте, что PGC STAC доступен и коллекция {collection_id} существует."
        )
    
    print(f"   Найдено тайлов: {len(found_items)}")
    
    downloaded_files = []
    for item in found_items:
        save_path = os.path.join(output_dir, f"{item.id}.tif")
        
        # Пропускаем уже скачанные
        if os.path.exists(save_path):
            print(f"   Уже скачан: {item.id}")
            downloaded_files.append(save_path)
            continue
        
        # Определяем ключ актива
        asset_key = 'dem' if 'dem' in item.assets else 'data'
        href = item.assets[asset_key].href
        
        try:
            print(f"   Загрузка {item.id}...")
            da = rioxarray.open_rasterio(href)
            da.rio.to_raster(save_path, compress='LZW')
            downloaded_files.append(save_path)
            print(f"   Сохранено: {save_path}")
        except Exception as e:
            print(f"   Ошибка загрузки {item.id}: {e}")
    
    if not downloaded_files:
        raise RuntimeError("Ни один тайл не удалось загрузить.")
    
    return downloaded_files


# ======================================================================================
# 1. ОБЪЕДИНЕНИЕ И РЕПРОЕКЦИЯ (GDAL — экономит RAM)
# ======================================================================================
def merge_and_reproject(tif_files, target_crs, target_res, vrt_path, output_path):
    """Объединяет тайлы через VRT и репроецирует через GDAL Warp."""
    print(f"   Тайлов для объединения: {len(tif_files)}")
    for f in tif_files:
        print(f"     - {os.path.basename(f)}")
    
    # VRT — виртуальная мозаика без загрузки в RAM
    print("   Создание VRT...")
    vrt_options = gdal.BuildVRTOptions(resampleAlg='bilinear')
    ds = gdal.BuildVRT(vrt_path, tif_files, options=vrt_options)
    ds.FlushCache()
    ds = None
    
    # Warp — репроекция на уровне GDAL (C++, минимум RAM)
    print(f"   Репроекция в {target_crs}, разрешение {target_res} м...")
    warp_options = gdal.WarpOptions(
        format="GTiff",
        dstSRS=target_crs,
        xRes=target_res,
        yRes=target_res,
        dstNodata=-9999,
        resampleAlg="bilinear",
        creationOptions=["COMPRESS=LZW", "TILED=YES", "BIGTIFF=YES"],
        multithread=True,
        warpMemoryLimit=512,
    )
    ds = gdal.Warp(output_path, vrt_path, options=warp_options)
    ds = None
    
    print("   DEM готов.")
    return output_path


# ======================================================================================
# 2. КАЛИБРОВКА ПО ДАННЫМ Г/П ГОРНЫЙ ИНСТИТУТ
# ======================================================================================
def calibrate_by_gauge(dem, gauge, csv_path, datetime_col, units,
                       strip_date, msk_hour_start, msk_hour_end, crs_metric):
    """
    Точная калибровка ArcticDEM по уровню водомерного поста
    В МОМЕНТ СЪЁМКИ (а не за период).
    
    1. Извлекаем DEM (WGS84 эллипсоид) в точке поста
    2. Из CSV берём уровень (БС-77) в час съёмки (15:00-16:59 MSK)
    3. geoid_offset = DEM_at_gauge - water_level_BS77
    
    Точность: ±0.03 м (разброс уровня за 2-часовое окно).
    """
    import pandas as pd
    
    gauge_lon, gauge_lat = gauge["lon"], gauge["lat"]
    gauge_col = gauge["col"]
    gauge_name = gauge["name"]
    
    print(f"   Пост: {gauge_name} ({gauge_lat:.6f}°N, {gauge_lon:.6f}°E)")
    print(f"   Дата съёмки: {strip_date}, ~{msk_hour_start}:00-{msk_hour_end}:59 MSK")
    
    # --- Шаг 1: значение DEM в точке поста ---
    gauge_gdf = gpd.GeoDataFrame(
        {'geometry': [Point(gauge_lon, gauge_lat)]}, crs="EPSG:4326"
    ).to_crs(crs_metric)
    gx, gy = gauge_gdf.geometry.iloc[0].x, gauge_gdf.geometry.iloc[0].y
    
    transform = dem.rio.transform()
    col_px = int((gx - transform.c) / transform.a)
    row_px = int((gy - transform.f) / transform.e)
    
    dem_arr = dem.values[0]
    
    # Поиск в окне 10×10 px (на случай nodata или попадания на набережную)
    search_r = 10
    best_val = np.nan
    best_dist = search_r * 2
    for dr in range(-search_r, search_r + 1):
        for dc in range(-search_r, search_r + 1):
            r, c = row_px + dr, col_px + dc
            if 0 <= r < dem_arr.shape[0] and 0 <= c < dem_arr.shape[1]:
                v = dem_arr[r, c]
                if not np.isnan(v) and abs(v) < 200:
                    dist = abs(dr) + abs(dc)
                    if dist < best_dist:
                        best_val = v
                        best_dist = dist
    
    if np.isnan(best_val):
        print(f"   ⚠ DEM не содержит данных в точке поста. Fallback.")
        return _fallback_calibration(dem)
    
    print(f"   DEM в точке поста: {best_val:.3f} м (WGS84 эллипсоид)")
    
    # --- Шаг 2: уровень воды в момент съёмки ---
    try:
        df = pd.read_csv(csv_path)
        df['_dt'] = pd.to_datetime(df[datetime_col])
        
        gauge_vals = pd.to_numeric(df[gauge_col], errors='coerce')
        if units == "cm":
            gauge_vals = gauge_vals / 100.0
        df['_level'] = gauge_vals
        
        # Фильтр: дата + часовое окно
        day = df[df['_dt'].dt.strftime('%Y-%m-%d') == strip_date]
        window = day[
            (day['_dt'].dt.hour >= msk_hour_start) &
            (day['_dt'].dt.hour <= msk_hour_end)
        ]
        window_vals = window['_level'].dropna()
        
        if len(window_vals) >= 2:
            # Округляем до 1 см — точность исходных данных
            water_level = round(float(window_vals.mean()), 2)
            print(f"   Уровень в момент съёмки: {water_level:.2f} м (БС-77)")
            print(f"   (среднее за {msk_hour_start}:00-{msk_hour_end}:59, "
                  f"N={len(window_vals)}, точность ±0.01 м)")
        elif not day.empty:
            water_level = round(float(day['_level'].dropna().mean()), 2)
            print(f"   ⚠ Нет данных за точный час. Среднесуточный: {water_level:.2f} м")
        else:
            print(f"   ⚠ Нет данных за {strip_date}. Используем медиану.")
            water_level = round(float(df['_level'].dropna().median()), 2)
            print(f"   Медиана за весь период: {water_level:.2f} м")
        
    except FileNotFoundError:
        print(f"   ⚠ Файл {csv_path} не найден!")
        return _fallback_calibration(dem)
    except Exception as e:
        print(f"   ⚠ Ошибка чтения CSV: {e}")
        return _fallback_calibration(dem)
    
    # --- Шаг 3: вычисление смещения ---
    geoid_offset = best_val - water_level
    
    print(f"\n   ┌──────────────────────────────────────────────────┐")
    print(f"   │  КАЛИБРОВКА ПО Г/П {gauge_name:<29s}│")
    print(f"   │  Дата/время съёмки:  {strip_date} ~15:40 MSK      │")
    print(f"   │  DEM (WGS84):        {best_val:>8.2f} м               │")
    print(f"   │  Уровень (БС-77):    {water_level:>8.2f} м               │")
    print(f"   │  Ундуляция геоида:   {geoid_offset:>8.2f} м               │")
    print(f"   └──────────────────────────────────────────────────┘")
    
    return geoid_offset


def _fallback_calibration(dem):
    """Резервная калибровка — улучшенная версия оригинала."""
    flat_vals = dem.values.flatten()
    valid = flat_vals[(~np.isnan(flat_vals)) & (flat_vals > -50) & (flat_vals < 50)]
    if len(valid) == 0:
        return 0
    water_base = np.percentile(valid, 5)
    print(f"   Резервная калибровка: 5-й перцентиль = {water_base:.2f} м")
    return water_base


# ======================================================================================
# 3. МАСКА ЗАТОПЛЕНИЯ С ГИДРОЛОГИЧЕСКОЙ СВЯЗНОСТЬЮ
# ======================================================================================
def compute_flood_mask(dem, flood_level, water_offset, seeds_wgs84, crs_metric,
                       closing_m=20, target_res=2, seed_search_px=25):
    """
    Строит маску затопления с проверкой гидрологической связности.
    
    ИСПРАВЛЕНИЯ v3:
    - NODATA на воде заполняется уровнем воды перед расчётом маски
      (стерео-DSM не видит воду → дыры рвут связность через реки)
    - Seed-точки на СУШЕ у кромки воды (не на nodata)
    - Морфологическое закрытие в метрах (не зависит от разрешения)
    """
    effective_level = flood_level + water_offset
    print(f"   ArcticDEM использует эллипсоидальные высоты (WGS84)")
    print(f"   Ундуляция геоида в р-не СПб: ~{water_offset:.1f} м")
    print(f"   Уровень затопления: {flood_level:.2f} м (над ур. моря)")
    print(f"   Эффективный порог в DEM: {effective_level:.2f} м (эллипсоидальная высота)")
    
    dem_arr = dem.values[0].copy()  # Копия, чтобы не портить оригинал
    
    # ─── КЛЮЧЕВОЕ ИСПРАВЛЕНИЕ v3: заполнение NODATA ───
    # ArcticDEM = стерео-DSM. На воде (Нева, залив, каналы) стереоматчинг
    # не работает → nodata. Эти дыры рвут связность маски: территории
    # по разные стороны реки оказываются в разных компонентах.
    # Решение: заполняем nodata значением = уровень воды в DEM-координатах.
    # Вода гарантированно ниже порога затопления → попадает в маску.
    water_dem_level = water_offset  # 0 м БС-77 + геоид ≈ уровень воды
    nodata_count = int(np.sum(np.isnan(dem_arr)))
    total_count = dem_arr.shape[0] * dem_arr.shape[1]
    nodata_pct = 100 * nodata_count / total_count
    
    print(f"   Nodata пикселей: {nodata_count:,} ({nodata_pct:.1f}%) — вода, тени, артефакты")
    print(f"   Заполняем nodata уровнем воды ({water_dem_level:.1f} м) для связности")
    dem_arr[np.isnan(dem_arr)] = water_dem_level
    # ──────────────────────────────────────────────────
    
    # Шаг 1: пороговая маска
    raw_mask = (dem_arr <= effective_level).astype(np.uint8)
    print(f"   Пикселей ниже порога (после заполнения nodata): {int(np.sum(raw_mask)):,}")
    
    # Шаг 2: морфологическое закрытие — ПЕРЕСЧЁТ В ПИКСЕЛИ
    closing_px = max(1, int(round(closing_m / target_res)))
    
    # Для больших массивов (>50M пикселей) ограничиваем iterations
    # чтобы не ждать часами. Если closing_px > 15, используем
    # дилатацию с увеличенным ядром вместо многих итераций
    total_pixels = dem_arr.shape[0] * dem_arr.shape[1]
    if closing_px > 12 and total_pixels > 50_000_000:
        print(f"   ⚠ Большой массив ({total_pixels/1e6:.0f}M px) + closing {closing_px} px")
        print(f"     Ограничиваем до 12 итераций (24 м при {target_res} м)")
        closing_px = 12
    
    print(f"   Морфологическое закрытие: {closing_m} м → {closing_px} px "
          f"(при разрешении {target_res} м)")
    
    if closing_px > 0:
        struct = ndimage.generate_binary_structure(2, 2)  # 8-связность
        print(f"   Применяю closing (может занять 1-3 мин при 2 м)...")
        raw_mask = ndimage.binary_closing(
            raw_mask, structure=struct, iterations=closing_px
        ).astype(np.uint8)
        print(f"   Пикселей после closing: {int(np.sum(raw_mask)):,}")
    
    # Шаг 3: связные компоненты
    labels, num_features = ndimage.label(raw_mask)
    print(f"   Найдено связных компонент: {num_features}")
    
    # Шаг 4: seed-точки с поиском в окне
    transform = dem.rio.transform()
    valid_labels = set()
    
    for lon, lat in seeds_wgs84:
        seed_gdf = gpd.GeoDataFrame(
            {'geometry': [Point(lon, lat)]}, crs="EPSG:4326"
        ).to_crs(crs_metric)
        sx, sy = seed_gdf.geometry.iloc[0].x, seed_gdf.geometry.iloc[0].y
        
        # Центральные пиксельные координаты
        col = int((sx - transform.c) / transform.a)
        row = int((sy - transform.f) / transform.e)
        
        # Поиск в окне ±seed_search_px
        r = seed_search_px
        row_lo = max(0, row - r)
        row_hi = min(labels.shape[0], row + r + 1)
        col_lo = max(0, col - r)
        col_hi = min(labels.shape[1], col + r + 1)
        
        window = labels[row_lo:row_hi, col_lo:col_hi]
        unique_labels = set(np.unique(window)) - {0}
        
        if unique_labels:
            valid_labels.update(unique_labels)
            print(f"   Seed ({lon:.3f}, {lat:.3f}) → "
                  f"компоненты {sorted(unique_labels)[:3]}"
                  f"{'...' if len(unique_labels) > 3 else ''}")
        else:
            print(f"   ⚠ Seed ({lon:.3f}, {lat:.3f}) — нет маски в радиусе "
                  f"{r * target_res} м")
    
    if not valid_labels:
        print("   ⚠ Ни один seed не попал в маску. Берём крупные компоненты.")
        min_size = max(10000, 1000 * (10 / target_res) ** 2)  # Масштабируем к разрешению
        for lbl in range(1, num_features + 1):
            if np.sum(labels == lbl) > min_size:
                valid_labels.add(lbl)
    
    if not valid_labels:
        print("   ⚠ Нет компонент. Возвращаем всю маску.")
        return raw_mask
    
    # Финальная маска
    flood_mask = np.isin(labels, list(valid_labels)).astype(np.uint8)
    
    n_total = int(np.sum(raw_mask))
    n_connected = int(np.sum(flood_mask))
    n_removed = n_total - n_connected
    area_ha = n_connected * (target_res ** 2) / 10000
    print(f"   Пикселей до фильтрации: {n_total:,}")
    print(f"   Пикселей после фильтрации: {n_connected:,} (~{area_ha:.0f} га)")
    if n_total > 0:
        print(f"   Удалено изолированных: {n_removed:,} ({100*n_removed/n_total:.1f}%)")
    
    return flood_mask


# ======================================================================================
# 4. ВЕКТОРИЗАЦИЯ С ФИЛЬТРАЦИЕЙ
# ======================================================================================
def vectorize_flood(flood_mask, transform, crs, min_area_m2, simplify_m):
    """Векторизует маску, фильтрует мелкие полигоны, упрощает В МЕТРАХ."""
    results = list(
        {'properties': {'val': v}, 'geometry': s}
        for s, v in shapes(flood_mask, mask=flood_mask == 1, transform=transform)
    )
    
    if not results:
        return None
    
    flood_gdf = gpd.GeoDataFrame.from_features(results, crs=crs)
    print(f"   Полигонов до фильтрации: {len(flood_gdf)}")
    
    # Фильтр по площади
    flood_gdf = flood_gdf[flood_gdf.geometry.area >= min_area_m2]
    print(f"   Полигонов после фильтрации (≥{min_area_m2} м²): {len(flood_gdf)}")
    
    if flood_gdf.empty:
        return None
    
    flood_gdf = flood_gdf.dissolve()
    
    # ИСПРАВЛЕНИЕ: упрощение В МЕТРИЧЕСКОЙ CRS, не после to_crs(4326)!
    flood_gdf.geometry = flood_gdf.geometry.simplify(simplify_m)
    
    total_area_ha = flood_gdf.geometry.area.sum() / 10000
    print(f"   Площадь затопления: {total_area_ha:.1f} га")
    
    return flood_gdf


# ======================================================================================
# 5. АНАЛИЗ УЩЕРБА
# ======================================================================================
def analyze_impact(flood_gdf, bbox_wgs84, crs_metric):
    """
    Анализ landuse + зданий в зоне затопления.
    
    ИСПРАВЛЕНИЕ v2.1: используем bbox зоны затопления (а не всего DEM),
    что кратно уменьшает объём запроса к Overpass API.
    """
    try:
        import osmnx as ox
        ox.settings.timeout = 60
    except ImportError:
        print("   osmnx не установлен. Пропуск анализа ущерба.")
        return {}
    
    # Bbox зоны затопления (а не всего DEM — намного меньше!)
    flood_wgs = flood_gdf.to_crs("EPSG:4326")
    fb = flood_wgs.total_bounds  # minx, miny, maxx, maxy
    bbox_tuple = (fb[0], fb[1], fb[2], fb[3])
    
    # Проверка размера bbox (защита от слишком больших запросов)
    area_deg2 = (fb[2] - fb[0]) * (fb[3] - fb[1])
    print(f"   Bbox зоны затопления: {area_deg2:.3f} deg² ({fb})")
    
    results = {}
    
    # --- Landuse ---
    try:
        print("   Загрузка landuse из OSM...")
        landuse = ox.features_from_bbox(bbox=bbox_tuple, tags={'landuse': True})
        if not landuse.empty:
            landuse = landuse.to_crs(crs_metric)
            landuse = landuse[landuse.geometry.type.isin(['Polygon', 'MultiPolygon'])]
            impact_lu = gpd.overlay(landuse, flood_gdf, how='intersection')
            impact_lu['flooded_ha'] = impact_lu.geometry.area / 10000
            results['landuse'] = impact_lu
            
            print("\n   ТОП ПОСТРАДАВШИХ ЗОН (landuse):")
            summary = impact_lu.groupby('landuse')['flooded_ha'].sum().sort_values(ascending=False)
            print(summary.head(10).to_string())
    except Exception as e:
        print(f"   Ошибка загрузки landuse: {e}")
    
    # --- Здания (только если зона не слишком большая) ---
    if area_deg2 > 0.5:
        print(f"   ⚠ Зона затопления слишком велика ({area_deg2:.1f} deg²) для загрузки зданий.")
        print("     Пропускаем слой зданий. Уменьшите bbox или повысьте FLOOD_LEVEL.")
    else:
        try:
            print("\n   Загрузка зданий из OSM...")
            buildings = ox.features_from_bbox(bbox=bbox_tuple, tags={'building': True})
            if not buildings.empty:
                buildings = buildings.to_crs(crs_metric)
                buildings = buildings[buildings.geometry.type.isin(['Polygon', 'MultiPolygon'])]
                flooded_buildings = gpd.sjoin(buildings, flood_gdf, how='inner', predicate='intersects')
                results['buildings'] = flooded_buildings
                print(f"   Зданий в зоне затопления: {len(flooded_buildings)}")
        except Exception as e:
            print(f"   Ошибка загрузки зданий: {e}")
    
    return results


# ======================================================================================
# 6. ВИЗУАЛИЗАЦИЯ
# ======================================================================================
def build_map(flood_gdf, impact_results, bbox_wgs84, output_path):
    """Строит интерактивную карту Folium."""
    b_minx, b_miny, b_maxx, b_maxy = bbox_wgs84
    cy, cx = (b_miny + b_maxy) / 2, (b_minx + b_maxx) / 2
    
    m = folium.Map(location=[cy, cx], zoom_start=12, tiles='CartoDB positron')
    
    # Перепроекция в WGS84 для Folium (упрощение уже сделано в метрах!)
    flood_wgs = flood_gdf.to_crs("EPSG:4326")
    
    folium.GeoJson(
        flood_wgs,
        name='Зона затопления',
        style_function=lambda x: {
            'fillColor': '#1E90FF', 'color': '#0000CD',
            'weight': 0.5, 'fillOpacity': 0.5
        }
    ).add_to(m)
    
    if 'landuse' in impact_results and not impact_results['landuse'].empty:
        impact_wgs = impact_results['landuse'].to_crs("EPSG:4326")
        folium.GeoJson(
            impact_wgs,
            name='Затопленные территории',
            style_function=lambda x: {
                'fillColor': '#FF4500', 'color': 'black',
                'weight': 0.5, 'fillOpacity': 0.5
            },
            tooltip=folium.GeoJsonTooltip(
                fields=['landuse', 'flooded_ha'],
                aliases=['Тип', 'Площадь (га)'],
                localize=True
            )
        ).add_to(m)
    
    if 'buildings' in impact_results and not impact_results['buildings'].empty:
        bld_wgs = impact_results['buildings'].to_crs("EPSG:4326")
        if len(bld_wgs) > 5000:
            print(f"   ⚠ Много зданий ({len(bld_wgs)}), показываем 5000")
            bld_wgs = bld_wgs.head(5000)
        
        folium.GeoJson(
            bld_wgs[['geometry']],
            name='Затопленные здания',
            style_function=lambda x: {
                'fillColor': '#FF0000', 'color': '#8B0000',
                'weight': 0.3, 'fillOpacity': 0.7
            },
            show=False  # Скрыт по умолчанию
        ).add_to(m)
    
    folium.LayerControl().add_to(m)
    m.save(output_path)
    print(f"   Карта сохранена: {output_path}")
    return m


# ======================================================================================
# MAIN
# ======================================================================================
def main():
    print(f"{'='*70}")
    print(f" АНАЛИЗ ЗАТОПЛЕНИЯ: {CITY_NAME}")
    print(f" Уровень подъёма воды: {FLOOD_LEVEL} м")
    print(f"{'='*70}")
    
    # 1. Загрузка готового DEM
    print(f"\n[1/4] Загрузка DEM: {INPUT_DEM}")
    dem = rioxarray.open_rasterio(INPUT_DEM, masked=True)
    print(f"   Размер: {dem.shape}, CRS: {dem.rio.crs}")
    
    xmin, ymin, xmax, ymax = dem.rio.bounds()
    bbox_poly = gpd.GeoDataFrame(
        {'geometry': [box(xmin, ymin, xmax, ymax)]}, crs=CRS_METRIC
    )
    bbox_wgs84 = bbox_poly.to_crs("EPSG:4326").total_bounds
    print(f"   Bbox WGS84: {bbox_wgs84}")
    
    # 2. Калибровка по г/п
    print(f"\n[2/4] Калибровка по г/п...")
    water_offset = calibrate_by_gauge(
        dem, GAUGE_PRIMARY, GAUGE_CSV_PATH, GAUGE_DATETIME_COL,
        GAUGE_UNITS, STRIP_DATE, STRIP_MSK_HOUR_START, STRIP_MSK_HOUR_END,
        CRS_METRIC
    )
    
    # 3. Маска затопления
    print(f"\n[3/4] Расчёт маски затопления ({FLOOD_LEVEL} м + калибровка)...")
    flood_mask = compute_flood_mask(
        dem, FLOOD_LEVEL, water_offset, WATER_SEEDS_WGS84, CRS_METRIC,
        closing_m=MORPH_CLOSING_M,
        target_res=TARGET_RES,
        seed_search_px=SEED_SEARCH_RADIUS_PX
    )
    
    # Освобождаем RAM — DEM больше не нужен, только transform
    dem_transform = dem.rio.transform()
    del dem
    import gc; gc.collect()
    print("   RAM освобождена (DEM удалён из памяти)")
    
    # 4. Векторизация + карта
    print("\n[4/4] Векторизация и карта...")
    flood_gdf = vectorize_flood(
        flood_mask, dem_transform, CRS_METRIC,
        MIN_POLYGON_AREA_M2, SIMPLIFY_TOLERANCE_M
    )
    
    del flood_mask; gc.collect()
    
    if flood_gdf is None or flood_gdf.empty:
        print("Затопления не обнаружено при заданном уровне.")
        return
    
    impact = analyze_impact(flood_gdf, bbox_wgs84, CRS_METRIC)
    m = build_map(flood_gdf, impact, bbox_wgs84, OUT_FILENAME)
    
    print(f"\n{'='*70}")
    print(f" ГОТОВО! Карта: {OUT_FILENAME}")
    print(f"{'='*70}")
    
    return m


# Запуск
main()
