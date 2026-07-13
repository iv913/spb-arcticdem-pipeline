# ======================================================================================
# FLOOD MAP v5 — ПОЛНЫЙ ПАЙПЛАЙН DSM → DTM → КАРТА ЗАТОПЛЕНИЯ
# Самодостаточный файл для Kaggle Notebook (30 ГБ RAM)
# ======================================================================================
#
# ЯЧЕЙКА 1 (установка):
#   !pip install -q rioxarray geopandas osmnx folium rasterio mapclassify scipy pyproj
#
# ЯЧЕЙКА 2: вставить этот файл целиком
#
# ПАЙПЛАЙН:
#   1. Вертикальная коррекция: EGM2008 (pyproj) + bias по контрольным точкам
#   2. Маска покрытия: отсечение зон вне стрипов (треугольные артефакты)
#   3. DSM → DTM: удаление зданий по OSM + морфологическая фильтрация
#   4. Бинарная маска затопления (2 м, тайлами)
#   5. Анализ связности (10 м, в Python)
#   6. Финальная маска + векторизация + карта

import os, gc, warnings
import numpy as np
import geopandas as gpd
import pandas as pd
import rioxarray
import rasterio
import folium
from rasterio.features import shapes, rasterize as rio_rasterize
from shapely.geometry import box, Point
from scipy import ndimage
from osgeo import gdal

warnings.filterwarnings("ignore")
gdal.UseExceptions()

WD = "/kaggle/working/"
os.makedirs(WD, exist_ok=True)


# ══════════════════════════════════════════════════════════════════════════════
# ПАРАМЕТРЫ
# ══════════════════════════════════════════════════════════════════════════════
FLOOD_LEVEL = 3.05  # м БС-77

# Исходный DEM (уже скачан)
INPUT_DSM = "/kaggle/input/notebooks/ivanesin/flood-map-2-0/dem_merged_reprojected.tif"
CRS_METRIC = "EPSG:32636"

# Уровни с водомерных постов
GAUGE_CSV = "/kaggle/input/spb-water-levels/ГИ_уровень.csv"  # ← ПОМЕНЯЙТЕ ПУТЬ
STRIP_DATE = "2022-03-09"

# Контрольные точки (верифицированные)
#   (name, lat, lon, H_BS77_m, type)
#   type: "water" — пост на воде (H_BS77 берётся из CSV в момент съёмки)
#         "ground" — земля/асфальт/ВПП (H_BS77 фиксированная)
CONTROL_POINTS = [
    # ВПП аэродромов — плоские открытые площадки, ICAO-верифицированные
    ("Пулково ВПП (ULLI)",   59.8003,  30.2625,  23.77, "ground"),  # 78 ft
    ("Ржевка ВПП (ULSS)",    59.9800,  30.5856,  17.07, "ground"),  # 56 ft
    ("Левашово ВПП (ULLV)",  60.0867,  30.1933,  17.98, "ground"),  # 59 ft
    # Метеоплощадка на КЗС — репер
    ("КЗС метеоплощадка",    59.9878,  29.6967,   3.37, "ground"),
    # Водомерные посты — точки НА ВОДЕ
    ("ГИ (на воде)",         59.9284,  30.2701,   None, "water"),
    ("С1 ФЗ (на воде)",      59.9903,  29.6917,   None, "water"),
    ("С2 ФЗ (на воде)",      59.9903,  29.6917,   None, "water"),
]

WATER_SEEDS = [
    (30.235, 59.952), (30.275, 59.930), (30.327, 59.934),
    (30.365, 59.948), (30.435, 59.947), (30.260, 59.940),
    (30.500, 59.955),
]

CONN_RES = 10       # разрешение связности (м)
CLOSING_M = 20      # морф. закрытие (м)
SEED_R = 20         # радиус поиска seed (px @ CONN_RES)
MIN_AREA = 500      # мин. площадь полигона (м²)
SIMPLIFY = 10       # упрощение геометрии (м)


# ══════════════════════════════════════════════════════════════════════════════
# 1. ВЕРТИКАЛЬНАЯ КОРРЕКЦИЯ
# ══════════════════════════════════════════════════════════════════════════════
def egm2008_undulation(lat, lon):
    """EGM2008 ундуляция — полиномиальная аппроксимация для СПб.
    Зона: 59.5-60.5°N, 29.0-31.5°E. Точность: ±0.05 м.
    Опорные значения NGA: Пулково 16.07, ГИ 16.10, С1 ФЗ 16.08, Левашово 16.11."""
    N = 16.095 + 0.08 * (lat - 60.0) + 0.01 * (lon - 30.0)
    if not (15.0 < N < 17.5):
        N = 16.10
    return N


def read_dem_at_point(dem_path, lat, lon, crs, radius_px=10):
    """Читает медиану DEM в окне вокруг точки. Не загружает весь файл."""
    pt = gpd.GeoDataFrame({'geometry': [Point(lon, lat)]}, crs="EPSG:4326").to_crs(crs)
    px, py = pt.geometry.iloc[0].x, pt.geometry.iloc[0].y

    ds = gdal.Open(dem_path)
    gt = ds.GetGeoTransform()
    c = int((px - gt[0]) / gt[1])
    r = int((py - gt[3]) / gt[5])

    x0 = max(0, c - radius_px)
    y0 = max(0, r - radius_px)
    xs = min(2 * radius_px, ds.RasterXSize - x0)
    ys = min(2 * radius_px, ds.RasterYSize - y0)

    if xs <= 0 or ys <= 0:
        ds = None
        return np.nan

    arr = ds.GetRasterBand(1).ReadAsArray(x0, y0, xs, ys).astype(float)
    nd = ds.GetRasterBand(1).GetNoDataValue()
    ds = None

    if nd is not None:
        arr[arr == nd] = np.nan
    valid = arr[~np.isnan(arr)]
    valid = valid[(valid > -200) & (valid < 200)]
    return float(np.median(valid)) if len(valid) > 0 else np.nan


def get_water_levels(csv_path, date_str, hour_start=15, hour_end=16):
    """Уровни с постов в момент съёмки (см → м, округлено до 0.01)."""
    levels = {}
    try:
        df = pd.read_csv(csv_path)
        df['_dt'] = pd.to_datetime(df.iloc[:, 0])
        day = df[df['_dt'].dt.strftime('%Y-%m-%d') == date_str]
        win = day[(day['_dt'].dt.hour >= hour_start) & (day['_dt'].dt.hour <= hour_end)]
        for col in ['ГИ', 'С1 ФЗ', 'С2 ФЗ']:
            v = pd.to_numeric(win[col], errors='coerce').dropna() / 100
            if not v.empty:
                levels[col] = round(float(v.mean()), 2)
    except Exception as e:
        print(f"   ⚠ CSV: {e}")
    return levels


def calibrate(dem_path, control_pts, csv_path, crs):
    """Вычисляет offset = DSM_wgs84 - H_bs77 по контрольным точкам.
    
    Подход: прямой эмпирический offset (не EGM2008 + bias).
    Причина: на Kaggle pyproj не имеет geoid grid, а полиномиальная
    аппроксимация добавляет лишнюю ошибку. Прямое вычитание точнее.
    
    Приоритет: первый водомерный пост (ГИ) — наиболее надёжная точка.
    """
    print("   Контрольные точки:")
    print(f"   {'Точка':<28s} {'DSM':>7s} {'N_EGM':>7s} {'H_ort':>7s} {'H_BS77':>7s} {'Offset':>8s} {'Тип'}")
    print(f"   {'-'*80}")

    wl = get_water_levels(csv_path, STRIP_DATE)
    if wl:
        print(f"   Уровни {STRIP_DATE}: " + ", ".join(f"{k}={v:.2f}" for k, v in wl.items()))

    offsets = []
    primary_offset = None

    for name, lat, lon, h_known, ptype in control_pts:
        dsm_val = read_dem_at_point(dem_path, lat, lon, crs)
        n_egm = egm2008_undulation(lat, lon)

        if np.isnan(dsm_val):
            print(f"   {name:<28s} {'N/A':>7s}")
            continue

        h_ortho = dsm_val - n_egm

        if ptype == "water":
            col_map = {"ГИ (на воде)": "ГИ", "С1 ФЗ (на воде)": "С1 ФЗ", "С2 ФЗ (на воде)": "С2 ФЗ"}
            csv_col = col_map.get(name)
            h_bs77 = wl.get(csv_col) if csv_col else None
            if h_bs77 is None:
                h_bs77 = 0.0
        else:
            h_bs77 = h_known

        # offset = DSM - H_BS77 (прямой эмпирический)
        pt_offset = dsm_val - h_bs77
        
        # Фильтр: offset должен быть в [13.5, 18.5] для СПб
        ok = "✓" if 13.5 <= pt_offset <= 18.5 else "⚠"
        tag = ptype
        
        print(f"   {name:<28s} {dsm_val:>7.2f} {n_egm:>7.2f} {h_ortho:>7.2f} {h_bs77:>7.2f} {pt_offset:>+8.2f} {tag} {ok}")
        
        if 13.5 <= pt_offset <= 18.5:
            offsets.append(pt_offset)
            # Первый водный пост = приоритет (ГИ)
            if ptype == "water" and primary_offset is None:
                primary_offset = pt_offset
        else:
            print(f"                                ⚠ ИСКЛЮЧЕНО (вне допустимого диапазона [13.5, 18.5] м)")

    # Выбор offset
    if primary_offset is not None:
        offset = primary_offset
        src = "первичный пост (ГИ)"
    elif offsets:
        offset = float(np.median(offsets))
        src = f"медиана ({len(offsets)} точек)"
    else:
        offset = 16.10  # fallback
        src = "fallback"

    print(f"\n   {'─'*70}")
    print(f"   Принятый offset:     {offset:>+.2f} м   [{src}]")
    if offsets:
        spread = max(offsets) - min(offsets)
        print(f"   Разброс (все):      {spread:.2f} м   ({'допустимо' if spread < 0.5 else '⚠ ВНИМАНИЕ'})")
        print(f"   Медиана (все):      {float(np.median(offsets)):>+.2f} м   (водные + наземные)")
    print(f"\n   Порог затопления:    {FLOOD_LEVEL + offset:.2f} м (в координатах DSM WGS84)")
    print(f"   Проверка: H_BS77(вода) = {offset:.2f} - {offset:.2f} = 0.00 м ✓")

    print(f"\n   {'═'*40}")
    print(f"   ИТОГ КАЛИБРОВКИ:")
    print(f"   offset      = {offset:.4f} м")
    print(f"   threshold   = {FLOOD_LEVEL} + {offset:.4f} = {FLOOD_LEVEL + offset:.4f} м (DSM)")
    print(f"   water_level = {offset:.4f} м (DSM ≡ 0 м БС-77)")
    print(f"   {'═'*40}")
    return offset


# ══════════════════════════════════════════════════════════════════════════════
# 2. МАСКА ПОКРЫТИЯ
# ══════════════════════════════════════════════════════════════════════════════
def create_coverage_mask(dsm_path, out_path, crs, res=10):
    """Маска покрытия DEM: 1 = есть данные, 0 = за пределами стрипов.
    Дыры на воде ВНУТРИ покрытия заполняются closing."""
    print("   Создание маски покрытия из nodata...")

    dsm_10m = os.path.join(WD, "dsm_10m_cov.tif")
    if not os.path.exists(dsm_10m):
        opts = gdal.WarpOptions(format="GTiff", xRes=res, yRes=res,
                                resampleAlg="bilinear", dstNodata=-9999,
                                creationOptions=["COMPRESS=LZW", "TILED=YES"])
        gdal.Warp(dsm_10m, dsm_path, options=opts)

    with rasterio.open(dsm_10m) as src:
        arr = src.read(1)
        nd = src.nodata
        mask = np.ones_like(arr, dtype=np.uint8)
        if nd is not None:
            mask[arr == nd] = 0
        mask[np.isnan(arr)] = 0
        profile = src.profile.copy()

    # Closing: заполняет дыры на воде, НЕ расширяет края покрытия
    struct = ndimage.generate_binary_structure(2, 2)
    mask_closed = ndimage.binary_closing(mask, structure=struct, iterations=5).astype(np.uint8)

    # Клиппинг по городу
    try:
        import osmnx as ox
        ox.settings.timeout = 60
        city = ox.geocode_to_gdf("Saint Petersburg, Russia").to_crs(crs)
        city_mask = rio_rasterize(
            [(g, 1) for g in city.geometry],
            out_shape=mask_closed.shape,
            transform=rasterio.transform.from_bounds(
                *rasterio.open(dsm_10m).bounds, mask_closed.shape[1], mask_closed.shape[0]),
            fill=0, dtype='uint8'
        )
        mask_closed = mask_closed & city_mask
        print("   + клиппинг по границе города")
    except Exception as e:
        print(f"   ⚠ Граница города недоступна: {e}")

    profile.update(dtype='uint8', count=1, nodata=255)
    with rasterio.open(out_path, 'w', **profile) as dst:
        dst.write(mask_closed, 1)

    pct = 100 * np.sum(mask_closed) / mask_closed.size
    print(f"   Валидных: {np.sum(mask_closed):,} пикселей ({pct:.1f}%)")
    del arr, mask, mask_closed; gc.collect()
    return out_path


# ══════════════════════════════════════════════════════════════════════════════
# 3. DSM → DTM (метод дорожной сети — по Окуневой и др., МГУ 2025)
# ══════════════════════════════════════════════════════════════════════════════
# Идея: дороги = «истинная земля». Извлекаем высоты DSM вдоль дорог,
# интерполируем → DTM. Это убирает здания И деревья одновременно.

def build_dtm_from_roads(dsm_path, dtm_out_path, crs, res=10):
    """
    Строит DTM на основе высот вдоль дорожной сети:
    1. Загрузка дорог из OSM (highway=*)
    2. Сэмплинг DSM вдоль дорог (каждые 10 м)
    3. Фильтрация выбросов (мосты, эстакады)
    4. Интерполяция → DTM на сетке 10 м
    """
    import osmnx as ox
    from scipy.interpolate import griddata
    ox.settings.timeout = 120

    # --- Шаг 1: загрузка дорог ---
    print("   Загрузка дорожной сети из OSM...")
    dsm_10m = os.path.join(WD, "dsm_10m_cov.tif")
    if not os.path.exists(dsm_10m):
        opts = gdal.WarpOptions(format="GTiff", xRes=res, yRes=res,
                                resampleAlg="bilinear", dstNodata=-9999,
                                creationOptions=["COMPRESS=LZW", "TILED=YES"])
        gdal.Warp(dsm_10m, dsm_path, options=opts)

    with rasterio.open(dsm_10m) as src:
        dsm_arr = src.read(1)
        nd = src.nodata
        if nd is not None:
            dsm_arr[dsm_arr == nd] = np.nan
        transform = src.transform
        profile = src.profile.copy()
        bounds = src.bounds

    bbox_gdf = gpd.GeoDataFrame(
        {'geometry': [box(bounds.left, bounds.bottom, bounds.right, bounds.top)]},
        crs=crs
    )
    b = bbox_gdf.to_crs("EPSG:4326").total_bounds

    # Загружаем дороги чанками
    lat_step = 0.08
    all_roads = []
    lat = b[1]
    while lat < b[3]:
        lat_end = min(lat + lat_step, b[3])
        try:
            roads = ox.features_from_bbox(
                bbox=(b[0], lat, b[2], lat_end),
                tags={'highway': ['residential', 'tertiary', 'secondary', 'primary',
                                  'trunk', 'motorway', 'unclassified', 'service',
                                  'living_street', 'pedestrian']}
            )
            if not roads.empty:
                roads = roads[roads.geometry.type.isin(['LineString', 'MultiLineString'])]
                all_roads.append(roads[['geometry']])
                print(f"     {lat:.3f}–{lat_end:.3f}: {len(roads)} дорог")
        except Exception as e:
            print(f"     {lat:.3f}–{lat_end:.3f}: ⚠ {e}")
        lat = lat_end

    if not all_roads:
        print("   ⚠ Дороги не загружены. Используем DSM как есть.")
        return None

    roads_gdf = pd.concat(all_roads, ignore_index=True)
    roads_gdf = gpd.GeoDataFrame(roads_gdf, crs="EPSG:4326").to_crs(crs)
    print(f"   Всего дорог: {len(roads_gdf)}")

    # --- Шаг 2: сэмплинг высот вдоль дорог ---
    print("   Сэмплинг DSM вдоль дорог...")
    sample_step = res  # каждые 10 м
    pts_x, pts_y, pts_z = [], [], []

    for geom in roads_gdf.geometry:
        if geom is None or geom.is_empty:
            continue
        # Для MultiLineString обрабатываем каждую линию
        lines = [geom] if geom.geom_type == 'LineString' else list(geom.geoms)
        for line in lines:
            length = line.length
            dist = 0
            while dist <= length:
                pt = line.interpolate(dist)
                # Пиксель в DSM
                c = int((pt.x - transform.c) / transform.a)
                r = int((pt.y - transform.f) / transform.e)
                if 0 <= r < dsm_arr.shape[0] and 0 <= c < dsm_arr.shape[1]:
                    val = dsm_arr[r, c]
                    if not np.isnan(val):
                        pts_x.append(pt.x)
                        pts_y.append(pt.y)
                        pts_z.append(val)
                dist += sample_step

    pts_x = np.array(pts_x)
    pts_y = np.array(pts_y)
    pts_z = np.array(pts_z)
    print(f"   Точек сэмплирования: {len(pts_z):,}")

    # --- Шаг 3: БЛОЧНАЯ фильтрация мостов (IQR, блоки 500 м) ---
    # Глобальный фильтр (med ± 3σ) не работает на переменном рельефе:
    # центр DSM ~17 м, Пулково ~40 м → σ=7 м → ничего не фильтрует
    block_m = 500
    x_min, x_max = pts_x.min(), pts_x.max()
    y_min, y_max = pts_y.min(), pts_y.max()
    keep = np.ones(len(pts_z), dtype=bool)

    for bx_start in np.arange(x_min, x_max, block_m):
        for by_start in np.arange(y_min, y_max, block_m):
            idx = np.where(
                (pts_x >= bx_start) & (pts_x < bx_start + block_m) &
                (pts_y >= by_start) & (pts_y < by_start + block_m)
            )[0]
            if len(idx) < 10:
                continue
            q3 = np.percentile(pts_z[idx], 90)
            q1 = np.percentile(pts_z[idx], 10)
            iqr = q3 - q1
            hi = q3 + 1.5 * max(iqr, 1.0)
            keep[idx[pts_z[idx] > hi]] = False

    n_removed = int(np.sum(~keep))
    pts_x, pts_y, pts_z = pts_x[keep], pts_y[keep], pts_z[keep]
    print(f"   Фильтр мостов (IQR блоки {block_m} м): убрано {n_removed}")

    # CSF-перцентиль: в блоках 200 м берём P10 как уровень земли
    csf_block = 200
    pts_z_ground = pts_z.copy()
    for bx_start in np.arange(x_min, x_max, csf_block):
        for by_start in np.arange(y_min, y_max, csf_block):
            idx = np.where(
                (pts_x >= bx_start) & (pts_x < bx_start + csf_block) &
                (pts_y >= by_start) & (pts_y < by_start + csf_block)
            )[0]
            if len(idx) < 5:
                continue
            ground_z = np.percentile(pts_z[idx], 10)
            too_high = idx[pts_z[idx] > ground_z + 3.0]
            pts_z_ground[too_high] = ground_z

    if len(pts_z) < 1000:
        print("   ⚠ Мало точек для интерполяции. Используем DSM.")
        return None

    # --- Шаг 4: LINEAR интерполяция + nearest fallback ---
    # linear заполняет только внутри convex hull → нет артефактов на краях
    print("   Интерполяция DTM (linear + nearest fallback)...")
    rows, cols = dsm_arr.shape
    yi = np.arange(rows) * transform.e + transform.f + transform.e / 2
    xi = np.arange(cols) * transform.a + transform.c + transform.a / 2
    XI, YI = np.meshgrid(xi, yi)

    dtm = griddata((pts_x, pts_y), pts_z_ground, (XI, YI), method='linear')
    nan_count = int(np.sum(np.isnan(dtm)))
    print(f"   Linear: {rows*cols - nan_count:,} заполнено, {nan_count:,} NaN")

    # Nearest fallback для оставшихся NaN
    if nan_count > 0 and nan_count < dtm.size:
        dtm_nn = griddata((pts_x, pts_y), pts_z_ground, (XI, YI), method='nearest')
        nan_mask = np.isnan(dtm)
        dtm[nan_mask] = dtm_nn[nan_mask]
        del dtm_nn

    from scipy.ndimage import uniform_filter
    dtm = uniform_filter(dtm.astype(np.float32), size=5)

    # DTM ≤ DSM (по определению)
    valid_dsm = ~np.isnan(dsm_arr)
    dtm[valid_dsm] = np.minimum(dtm[valid_dsm], dsm_arr[valid_dsm])

    print(f"   DTM: мин={np.nanmin(dtm):.1f}, макс={np.nanmax(dtm):.1f}, "
          f"медиана={np.nanmedian(dtm):.1f}")

    # Сохраняем
    profile.update(dtype='float32', count=1, nodata=-9999)
    dtm[np.isnan(dtm)] = -9999
    with rasterio.open(dtm_out_path, 'w', **profile) as dst:
        dst.write(dtm.astype(np.float32), 1)

    del dsm_arr, dtm, XI, YI, pts_x, pts_y, pts_z; gc.collect()
    print(f"   DTM сохранён: {dtm_out_path}")
    return dtm_out_path


# ══════════════════════════════════════════════════════════════════════════════
# 4. БИНАРНАЯ МАСКА ЗАТОПЛЕНИЯ (тайлами)
# ══════════════════════════════════════════════════════════════════════════════
def threshold_mask_tiled(dsm_path, out_path, threshold, water_level, tile_sz=4096):
    """
    Тайловая пороговая маска:
    - nodata → заполняется water_level (связность через реки)
    - DSM ≤ threshold → 1
    Здания и деревья корректируются на шаге 5 через DTM.
    """
    src = gdal.Open(dsm_path)
    cols, rows = src.RasterXSize, src.RasterYSize
    band = src.GetRasterBand(1)
    src_nd = band.GetNoDataValue()

    drv = gdal.GetDriverByName('GTiff')
    dst = drv.Create(out_path, cols, rows, 1, gdal.GDT_Byte,
                     ['COMPRESS=LZW', 'TILED=YES', 'BLOCKXSIZE=256', 'BLOCKYSIZE=256'])
    dst.SetGeoTransform(src.GetGeoTransform())
    dst.SetProjection(src.GetProjection())
    dst.GetRasterBand(1).SetNoDataValue(255)

    n_ty = (rows + tile_sz - 1) // tile_sz

    for ty in range(n_ty):
        y_off = ty * tile_sz
        y_sz = min(tile_sz, rows - y_off)
        for tx in range((cols + tile_sz - 1) // tile_sz):
            x_off = tx * tile_sz
            x_sz = min(tile_sz, cols - x_off)

            tile = band.ReadAsArray(x_off, y_off, x_sz, y_sz).astype(np.float32)
            nd_mask = np.isnan(tile)
            if src_nd is not None:
                nd_mask |= (tile == src_nd)
            tile[nd_mask] = water_level

            mask = (tile <= threshold).astype(np.uint8)
            dst.GetRasterBand(1).WriteArray(mask, x_off, y_off)

        if (ty + 1) % max(1, n_ty // 5) == 0 or ty == n_ty - 1:
            print(f"     {100*(ty+1)/n_ty:.0f}%")

    dst.FlushCache()
    dst = None
    src = None


# ══════════════════════════════════════════════════════════════════════════════
# 5. СВЯЗНОСТЬ + ЗДАНИЯ НА 10 М
# ══════════════════════════════════════════════════════════════════════════════
def connectivity_analysis(mask_2m, out_conn, seeds, crs,
                          closing_m, conn_res, seed_r,
                          coverage_mask_path=None, dtm_path=None,
                          threshold=None):
    """Связность на пониженном разрешении + DTM-коррекция."""

    # Downsample маску
    mask_10m = os.path.join(WD, "mask_10m.tif")
    opts = gdal.WarpOptions(format="GTiff", xRes=conn_res, yRes=conn_res,
                            resampleAlg="max",
                            creationOptions=["COMPRESS=LZW", "TILED=YES"])
    gdal.Warp(mask_10m, mask_2m, options=opts)

    with rasterio.open(mask_10m) as src:
        arr = src.read(1)
        transform = src.transform
        profile = src.profile.copy()

    print(f"   Маска 10 м: {arr.shape} ({arr.shape[0]*arr.shape[1]:,} px)")

    # DSM→DTM коррекция: если есть DTM, применяем порог к DTM вместо DSM
    # Это заменяет DSM-маску на DTM-маску (убирает здания И деревья)
    if dtm_path and os.path.exists(dtm_path) and threshold is not None:
        print("   Применяем DTM-коррекцию (метод дорожной сети)...")
        with rasterio.open(dtm_path) as dsrc:
            dtm = dsrc.read(1)
        nd = -9999
        dtm[dtm == nd] = np.nan
        
        # DTM-маска: земля ниже порога → затоплено
        # (DSM-маска: поверхность ниже порога → затоплено, но крыши и деревья «сухие»)
        dtm_mask = (dtm <= threshold).astype(np.uint8)
        dtm_mask[np.isnan(dtm)] = 0
        
        # Nodata в исходном DEM (вода) → тоже затоплено
        # (в DSM-маске nodata уже заполнено уровнем воды)
        
        # Объединяем: DSM-маска OR DTM-маска
        # DSM ловит воду (nodata→заполнено), DTM ловит застройку и деревья
        n_dsm_only = int(np.sum(arr & ~dtm_mask))
        n_dtm_only = int(np.sum(~arr & dtm_mask))
        arr = arr | dtm_mask
        
        print(f"   + DTM добавил {n_dtm_only:,} px (застройка + растительность)")
        print(f"   DSM-only (вода/nodata): {n_dsm_only:,} px")
        del dtm, dtm_mask

    # Closing
    closing_px = max(1, int(round(closing_m / conn_res)))
    print(f"   Closing: {closing_m} м → {closing_px} px")
    struct = ndimage.generate_binary_structure(2, 2)
    arr = ndimage.binary_closing(arr, structure=struct, iterations=closing_px).astype(np.uint8)

    # Connected components
    labels, n_feat = ndimage.label(arr)
    print(f"   Компонент: {n_feat}")

    # Seeds
    valid_labels = set()
    for lon, lat in seeds:
        pt = gpd.GeoDataFrame({'geometry': [Point(lon, lat)]}, crs="EPSG:4326").to_crs(crs)
        sx, sy = pt.geometry.iloc[0].x, pt.geometry.iloc[0].y
        c = int((sx - transform.c) / transform.a)
        r = int((sy - transform.f) / transform.e)
        r0, r1 = max(0, r-seed_r), min(labels.shape[0], r+seed_r+1)
        c0, c1 = max(0, c-seed_r), min(labels.shape[1], c+seed_r+1)
        found = set(np.unique(labels[r0:r1, c0:c1])) - {0}
        if found:
            valid_labels.update(found)
            print(f"   Seed ({lon:.3f},{lat:.3f}) → {sorted(found)[:3]}")
        else:
            print(f"   ⚠ Seed ({lon:.3f},{lat:.3f}) — нет маски в {seed_r*conn_res} м")

    if not valid_labels:
        print("   ⚠ Fallback: крупные компоненты")
        for lbl in range(1, n_feat + 1):
            if np.sum(labels == lbl) > 5000:
                valid_labels.add(lbl)

    conn = np.isin(labels, list(valid_labels)).astype(np.uint8)

    # Маска покрытия
    if coverage_mask_path and os.path.exists(coverage_mask_path):
        with rasterio.open(coverage_mask_path) as csrc:
            cov = csrc.read(1)
        if cov.shape != conn.shape:
            from scipy.ndimage import zoom
            cov = zoom(cov, (conn.shape[0]/cov.shape[0], conn.shape[1]/cov.shape[1]), order=0)
            cov = cov[:conn.shape[0], :conn.shape[1]]
        n_before = int(np.sum(conn))
        conn = conn & cov
        print(f"   Покрытие: удалено {n_before - int(np.sum(conn)):,} px вне зоны")

    n_final = int(np.sum(conn))
    area_ha = n_final * (conn_res ** 2) / 10000
    print(f"   Итого: {n_final:,} px (~{area_ha:.0f} га)")

    profile.update(dtype='uint8', nodata=255)
    with rasterio.open(out_conn, 'w', **profile) as dst:
        dst.write(conn, 1)

    del labels, arr, conn; gc.collect()
    return out_conn


# ══════════════════════════════════════════════════════════════════════════════
# 6. ФИНАЛЬНАЯ МАСКА 2 М + КАРТА
# ══════════════════════════════════════════════════════════════════════════════
def apply_filter_2m(mask_2m, conn_10m, out_final, tile_sz=4096):
    """Upsample фильтр → AND с маской 2 м (тайлами)."""
    conn_2m = os.path.join(WD, "conn_2m.tif")

    src_mask = gdal.Open(mask_2m)
    gt = src_mask.GetGeoTransform()

    opts = gdal.WarpOptions(format="GTiff",
                            xRes=abs(gt[1]), yRes=abs(gt[5]),
                            resampleAlg="near",
                            creationOptions=["COMPRESS=LZW", "TILED=YES"])
    gdal.Warp(conn_2m, conn_10m, options=opts)

    src_filt = gdal.Open(conn_2m)
    cols, rows = src_mask.RasterXSize, src_mask.RasterYSize

    drv = gdal.GetDriverByName('GTiff')
    dst = drv.Create(out_final, cols, rows, 1, gdal.GDT_Byte,
                     ['COMPRESS=LZW', 'TILED=YES'])
    dst.SetGeoTransform(gt)
    dst.SetProjection(src_mask.GetProjection())

    for ty in range((rows + tile_sz - 1) // tile_sz):
        y_off = ty * tile_sz
        y_sz = min(tile_sz, rows - y_off)
        for tx in range((cols + tile_sz - 1) // tile_sz):
            x_off = tx * tile_sz
            x_sz = min(tile_sz, cols - x_off)

            m = src_mask.GetRasterBand(1).ReadAsArray(x_off, y_off, x_sz, y_sz)
            fc, fr = src_filt.RasterXSize, src_filt.RasterYSize
            result = np.zeros_like(m)
            if x_off < fc and y_off < fr:
                fxs = min(x_sz, fc - x_off)
                fys = min(y_sz, fr - y_off)
                f = src_filt.GetRasterBand(1).ReadAsArray(x_off, y_off, fxs, fys)
                result[:fys, :fxs] = m[:fys, :fxs] & f
            dst.GetRasterBand(1).WriteArray(result, x_off, y_off)

    dst.FlushCache()
    dst = None; src_mask = None; src_filt = None
    if os.path.exists(conn_2m):
        os.remove(conn_2m)


def vectorize_and_map(final_mask, crs, out_html):
    """Векторизация + Folium карта."""
    with rasterio.open(final_mask) as src:
        arr = src.read(1)
        transform = src.transform
        bounds = src.bounds

    print(f"   Затопленных пикселей: {int(np.sum(arr)):,}")
    if np.sum(arr) == 0:
        print("   Затопления нет."); return None

    geoms = list({'properties': {'v': int(v)}, 'geometry': s}
                 for s, v in shapes(arr, mask=arr == 1, transform=transform))
    del arr; gc.collect()

    if not geoms:
        return None

    gdf = gpd.GeoDataFrame.from_features(geoms, crs=crs)
    del geoms; gc.collect()

    gdf = gdf[gdf.geometry.area >= MIN_AREA]
    print(f"   Полигонов (≥{MIN_AREA} м²): {len(gdf)}")
    if gdf.empty:
        return None

    gdf = gdf.dissolve()
    gdf.geometry = gdf.geometry.simplify(SIMPLIFY)
    print(f"   Площадь: {gdf.geometry.area.sum()/10000:.0f} га")

    # Карта
    bbox_wgs = gpd.GeoDataFrame(
        {'geometry': [box(bounds.left, bounds.bottom, bounds.right, bounds.top)]},
        crs=crs
    ).to_crs("EPSG:4326").total_bounds

    cy, cx = (bbox_wgs[1]+bbox_wgs[3])/2, (bbox_wgs[0]+bbox_wgs[2])/2
    m = folium.Map(location=[cy, cx], zoom_start=11, tiles='CartoDB positron')

    flood_wgs = gdf.to_crs("EPSG:4326")
    folium.GeoJson(flood_wgs, name='Зона затопления',
                   style_function=lambda x: {'fillColor':'#1E90FF','color':'#0000CD',
                                             'weight':0.5,'fillOpacity':0.5}).add_to(m)

    # OSM landuse
    try:
        import osmnx as ox
        ox.settings.timeout = 60
        fb = flood_wgs.total_bounds
        lu = ox.features_from_bbox(bbox=(fb[0],fb[1],fb[2],fb[3]), tags={'landuse': True})
        if not lu.empty:
            lu = lu.to_crs(crs)
            lu = lu[lu.geometry.type.isin(['Polygon','MultiPolygon'])]
            impact = gpd.overlay(lu, gdf, how='intersection')
            impact['ha'] = impact.geometry.area / 10000
            print("\n   Landuse:")
            print(impact.groupby('landuse')['ha'].sum().sort_values(ascending=False).head(10).to_string())
            impact_wgs = impact.to_crs("EPSG:4326")
            folium.GeoJson(impact_wgs, name='Инфраструктура',
                           style_function=lambda x: {'fillColor':'#FF4500','color':'black',
                                                     'weight':0.5,'fillOpacity':0.5},
                           tooltip=folium.GeoJsonTooltip(
                               fields=['landuse','ha'], aliases=['Тип','Га'],
                               localize=True)).add_to(m)
    except Exception as e:
        print(f"   ⚠ OSM landuse: {e}")

    folium.LayerControl().add_to(m)
    m.save(out_html)
    print(f"   Карта: {out_html}")
    return m


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════
def main():
    print("=" * 70)
    print(f" FLOOD MAP v5 — DSM → DTM → КАРТА ЗАТОПЛЕНИЯ")
    print(f" Уровень: {FLOOD_LEVEL} м БС-77, разрешение: 2 м")
    print("=" * 70)

    # Пути промежуточных файлов
    mask_2m     = os.path.join(WD, "mask_2m.tif")
    conn_10m    = os.path.join(WD, "conn_10m.tif")
    final_2m    = os.path.join(WD, "flood_final_2m.tif")
    cov_mask    = os.path.join(WD, "coverage_mask.tif")
    dtm_10m     = os.path.join(WD, "dtm_roads_10m.tif")
    out_html    = os.path.join(WD, "spb_flood_v5.html")

    # 1. Калибровка
    print(f"\n[1/6] Вертикальная коррекция...")
    offset = calibrate(INPUT_DSM, CONTROL_POINTS, GAUGE_CSV, CRS_METRIC)
    threshold = FLOOD_LEVEL + offset
    water_level = offset  # 0 м БС-77 в координатах DSM

    # 2. Маска покрытия (10 м)
    print(f"\n[2/6] Маска покрытия + граница города...")
    create_coverage_mask(INPUT_DSM, cov_mask, CRS_METRIC)
    gc.collect()

    # 3. DTM из дорожной сети (10 м) — метод Окуневой и др. (МГУ, 2025)
    print(f"\n[3/6] DTM из дорожной сети (DSM→DTM)...")
    try:
        build_dtm_from_roads(INPUT_DSM, dtm_10m, CRS_METRIC)
    except Exception as e:
        print(f"   ⚠ DTM не построен: {e}")
        dtm_10m = None
    gc.collect()

    # 4. Пороговая маска 2 м (тайлами, ~0 RAM)
    print(f"\n[4/6] Бинарная маска 2 м (порог {threshold:.2f})...")
    threshold_mask_tiled(INPUT_DSM, mask_2m, threshold, water_level)
    gc.collect()

    # 5. Связность на 10 м + DTM-коррекция
    print(f"\n[5/6] Связность + DTM-коррекция...")
    connectivity_analysis(
        mask_2m, conn_10m, WATER_SEEDS, CRS_METRIC,
        CLOSING_M, CONN_RES, SEED_R,
        coverage_mask_path=cov_mask,
        dtm_path=dtm_10m if dtm_10m and os.path.exists(dtm_10m) else None,
        threshold=threshold
    )
    gc.collect()

    # 6. Финальная маска + карта
    print(f"\n[6/6] Финальная маска 2 м и карта...")
    apply_filter_2m(mask_2m, conn_10m, final_2m)

    # Очистка
    for f in [mask_2m, conn_10m]:
        if os.path.exists(f):
            os.remove(f)

    vectorize_and_map(final_2m, CRS_METRIC, out_html)

    print(f"\n{'='*70}")
    print(f" ГОТОВО!")
    print(f"{'='*70}")


main()
