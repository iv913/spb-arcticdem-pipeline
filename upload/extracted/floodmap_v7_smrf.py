# ======================================================================================
# FLOOD MAP v7 — SMRF (Simple Morphological Filter)
# Метод: Liu et al. 2023, NHESS — "Bare-earth DEM generation from ArcticDEM"
# Хельсинки: RMSE 1.02 м, ошибка глубины 0.3 м (сравнимо с LiDAR)
# ======================================================================================
#
# ЯЧЕЙКА 1: !pip install -q rioxarray geopandas osmnx folium rasterio mapclassify scipy
# ЯЧЕЙКА 2: вставить этот файл целиком
#
# ПАЙПЛАЙН:
#   1. Калибровка: эмпирический offset по водомерным постам + EGM2008 полином
#   2. Маска покрытия: nodata → closing → clip по городу
#   3. SMRF: DSM → DTM на 10 м (scipy grey_opening, ~280 МБ RAM)
#   4. Пороговая маска 2 м (тайлами, ~67 МБ RAM)
#   5. Связность: DTM-маска | DSM-маска на 10 м + seeds
#   6. Финальная маска 2 м + карта

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
FLOOD_LEVEL = 3.05
INPUT_DSM = "/kaggle/input/notebooks/ivanesin/flood-map-2-0/dem_merged_reprojected.tif"
CRS_METRIC = "EPSG:32636"
GAUGE_CSV = "/kaggle/input/spb-water-levels/ГИ_уровень.csv"
STRIP_DATE = "2022-03-09"

# SMRF параметры (Liu et al. 2023, адаптированы для СПб)
# slope_threshold: 0.07 (Helsinki) → 0.06 (СПб чуть плотнее)
# window_max: 30 м (Helsinki) → 50 м (СПб: крупные здания, дома-колодцы)
SMRF_SLOPE = 0.06       # порог уклона (доля, не %)
SMRF_WINDOW_MAX = 50    # макс. размер окна (м)
SMRF_CELL = 10          # разрешение DTM (м)

CONN_RES = 10
CLOSING_M = 20
SEED_R = 20
MIN_AREA = 500
SIMPLIFY = 10

CONTROL_POINTS = [
    ("Ржевка ВПП (ULSS)",    59.9800,  30.5856,  17.07, "ground"),
    ("Левашово ВПП (ULLV)",  60.0867,  30.1933,  17.98, "ground"),
    ("КЗС метеоплощадка",    59.9878,  29.6967,   3.37, "ground"),
    ("ГИ (на воде)",         59.9284,  30.2701,   None, "water"),
    ("С1 ФЗ (на воде)",      59.9903,  29.6917,   None, "water"),
    ("С2 ФЗ (на воде)",      59.9903,  29.6917,   None, "water"),
]

WATER_SEEDS = [
    (30.235, 59.952), (30.275, 59.930), (30.327, 59.934),
    (30.365, 59.948), (30.435, 59.947), (30.260, 59.940),
    (30.500, 59.955),
]


# ══════════════════════════════════════════════════════════════════════════════
# 1. КАЛИБРОВКА
# ══════════════════════════════════════════════════════════════════════════════
def egm2008_undulation(lat, lon):
    """Полиномиальная аппроксимация EGM2008 для зоны СПб (±0.05 м)."""
    return 16.095 + 0.08 * (lat - 60.0) + 0.01 * (lon - 30.0)


def read_dem_at_point(dem_path, lat, lon, crs, r=10):
    pt = gpd.GeoDataFrame({'geometry': [Point(lon, lat)]}, crs="EPSG:4326").to_crs(crs)
    px, py = pt.geometry.iloc[0].x, pt.geometry.iloc[0].y
    ds = gdal.Open(dem_path)
    gt = ds.GetGeoTransform()
    c = int((px - gt[0]) / gt[1]); row = int((py - gt[3]) / gt[5])
    x0, y0 = max(0, c-r), max(0, row-r)
    xs, ys = min(2*r, ds.RasterXSize-x0), min(2*r, ds.RasterYSize-y0)
    if xs <= 0 or ys <= 0: ds = None; return np.nan
    arr = ds.GetRasterBand(1).ReadAsArray(x0, y0, xs, ys).astype(float)
    nd = ds.GetRasterBand(1).GetNoDataValue(); ds = None
    if nd is not None: arr[arr == nd] = np.nan
    v = arr[~np.isnan(arr)]; v = v[(v > -200) & (v < 200)]
    return float(np.median(v)) if len(v) > 0 else np.nan


def calibrate(dem_path, control_pts, csv_path, crs):
    print(f"   {'Точка':<28s} {'DSM':>7s} {'N_EGM':>7s} {'H_ort':>7s} {'H_BS77':>7s} {'Offset':>8s} {'Тип'}")
    print(f"   {'-'*80}")
    wl = {}
    try:
        df = pd.read_csv(csv_path)
        df['_dt'] = pd.to_datetime(df.iloc[:, 0])
        day = df[df['_dt'].dt.strftime('%Y-%m-%d') == STRIP_DATE]
        win = day[(day['_dt'].dt.hour >= 15) & (day['_dt'].dt.hour <= 16)]
        for col in ['ГИ', 'С1 ФЗ', 'С2 ФЗ']:
            v = pd.to_numeric(win[col], errors='coerce').dropna() / 100
            if not v.empty: wl[col] = round(float(v.mean()), 2)
        if wl: print(f"   Уровни {STRIP_DATE}: " + ", ".join(f"{k}={v:.2f}" for k, v in wl.items()))
    except Exception as e:
        print(f"   ⚠ CSV: {e}")

    offsets = []; primary = None
    for name, lat, lon, h_known, ptype in control_pts:
        dsm_val = read_dem_at_point(dem_path, lat, lon, crs)
        n_egm = egm2008_undulation(lat, lon)
        if np.isnan(dsm_val): print(f"   {name:<28s} {'N/A':>7s}"); continue
        h_ortho = dsm_val - n_egm
        if ptype == "water":
            cm = {"ГИ (на воде)": "ГИ", "С1 ФЗ (на воде)": "С1 ФЗ", "С2 ФЗ (на воде)": "С2 ФЗ"}
            h_bs77 = wl.get(cm.get(name), 0.0)
        else:
            h_bs77 = h_known
        pt_off = dsm_val - h_bs77
        ok = "✓" if 13.5 <= pt_off <= 18.5 else "⚠"
        print(f"   {name:<28s} {dsm_val:>7.2f} {n_egm:>7.2f} {h_ortho:>7.2f} {h_bs77:>7.2f} {pt_off:>+8.2f} {ptype} {ok}")
        if 13.5 <= pt_off <= 18.5:
            offsets.append(pt_off)
            if ptype == "water" and primary is None: primary = pt_off
        else:
            print(f"                                ⚠ ИСКЛЮЧЕНО (вне [13.5, 18.5])")

    offset = primary if primary is not None else (float(np.median(offsets)) if offsets else 16.10)
    src = "первичный пост (ГИ)" if primary else "медиана"
    print(f"\n   Принятый offset:     {offset:>+.2f} м   [{src}]")
    print(f"   Порог затопления:    {FLOOD_LEVEL + offset:.2f} м (DSM)")
    return offset


# ══════════════════════════════════════════════════════════════════════════════
# 2. МАСКА ПОКРЫТИЯ
# ══════════════════════════════════════════════════════════════════════════════
def create_coverage_mask(dsm_path, out_path, crs, res=10):
    dsm_10m = os.path.join(WD, "dsm_10m.tif")
    if not os.path.exists(dsm_10m):
        gdal.Warp(dsm_10m, dsm_path, options=gdal.WarpOptions(
            format="GTiff", xRes=res, yRes=res, resampleAlg="bilinear",
            dstNodata=-9999, creationOptions=["COMPRESS=LZW", "TILED=YES"]))
    with rasterio.open(dsm_10m) as src:
        arr = src.read(1); nd = src.nodata; profile = src.profile.copy()
        mask = np.ones_like(arr, dtype=np.uint8)
        if nd is not None: mask[arr == nd] = 0
        mask[np.isnan(arr)] = 0
    struct = ndimage.generate_binary_structure(2, 2)
    mask = ndimage.binary_closing(mask, structure=struct, iterations=5).astype(np.uint8)
    try:
        import osmnx as ox; ox.settings.timeout = 60
        city = ox.geocode_to_gdf("Saint Petersburg, Russia").to_crs(crs)
        with rasterio.open(dsm_10m) as s2:
            city_mask = rio_rasterize([(g, 1) for g in city.geometry],
                out_shape=(s2.height, s2.width), transform=s2.transform, fill=0, dtype='uint8')
        mask = mask & city_mask; print("   + клиппинг по городу")
    except Exception as e:
        print(f"   ⚠ Граница города: {e}")
    profile.update(dtype='uint8', count=1, nodata=255)
    with rasterio.open(out_path, 'w', **profile) as dst: dst.write(mask, 1)
    print(f"   Валидных: {np.sum(mask):,} px ({100*np.sum(mask)/mask.size:.1f}%)")
    del arr, mask; gc.collect()


# ══════════════════════════════════════════════════════════════════════════════
# 3. SMRF — Simple Morphological Filter (Pingel et al. 2013)
# ══════════════════════════════════════════════════════════════════════════════
def apply_smrf(dsm_path, dtm_out_path, crs, slope_threshold=0.06,
               window_max_m=50, cell_size_m=10, coverage_mask_path=None):
    """
    SMRF фильтр для конвертации DSM → DTM.

    Реализация по Pingel et al. 2013 + Liu et al. 2023 (Helsinki).

    Алгоритм:
    1. Морфологическое открытие (grey_opening) с линейно растущим окном
    2. На каждом шаге: если разница > slope * window → пиксель = объект
    3. Объектные пиксели заменяются интерполяцией от ground-пикселей

    Параметры для СПб (по Liu et al. 2023):
      slope_threshold: 0.06 (6% уклона)
      window_max_m: 50 м (крупные здания)
      cell_size_m: 10 м (разрешение обработки)
    """
    print(f"   Параметры: slope={slope_threshold}, window={window_max_m} м, cell={cell_size_m} м")

    # Понижаем DSM до рабочего разрешения
    dsm_work = os.path.join(WD, "dsm_10m.tif")
    if not os.path.exists(dsm_work):
        gdal.Warp(dsm_work, dsm_path, options=gdal.WarpOptions(
            format="GTiff", xRes=cell_size_m, yRes=cell_size_m,
            resampleAlg="bilinear", dstNodata=-9999,
            creationOptions=["COMPRESS=LZW", "TILED=YES"]))

    with rasterio.open(dsm_work) as src:
        dsm = src.read(1).astype(np.float32)
        nd = src.nodata
        transform = src.transform
        profile = src.profile.copy()

    if nd is not None:
        dsm[dsm == nd] = np.nan

    print(f"   DSM: {dsm.shape}, {dsm.shape[0]*dsm.shape[1]:,} px, "
          f"~{dsm.nbytes/1e6:.0f} МБ")

    # Заполняем NaN для morphological operations (nearest valid)
    nan_mask = np.isnan(dsm)
    if np.any(nan_mask):
        from scipy.ndimage import distance_transform_edt
        # Заполняем NaN ближайшим валидным значением (для opening)
        idx = distance_transform_edt(nan_mask, return_distances=False, return_indices=True)
        dsm_filled = dsm[tuple(idx)]
    else:
        dsm_filled = dsm.copy()

    # ═══ SMRF: прогрессивное морфологическое открытие ═══
    window_max_px = max(3, int(round(window_max_m / cell_size_m)))
    object_mask = np.zeros(dsm.shape, dtype=bool)

    # Начальная поверхность = оригинал
    surface = dsm_filled.copy()

    print(f"   SMRF: окно 1 → {window_max_px} px ({cell_size_m} → {window_max_m} м)")

    for w in range(1, window_max_px + 1):
        # Морфологическое открытие (erosion + dilation)
        # Эквивалентно "cloth" которая ложится на поверхность снизу
        footprint = np.ones((2*w+1, 2*w+1))
        opened = ndimage.grey_opening(surface, footprint=footprint)

        # Разница: где поверхность значительно выше opened → объект
        diff = surface - opened

        # Порог: slope * window_size_m
        threshold = slope_threshold * w * cell_size_m
        new_objects = diff > threshold

        object_mask |= new_objects

        # Обновляем surface: объектные пиксели заменяем opened значениями
        surface[new_objects] = opened[new_objects]

        if w % max(1, window_max_px // 5) == 0 or w == window_max_px:
            pct_obj = 100 * np.sum(object_mask & ~nan_mask) / max(1, np.sum(~nan_mask))
            print(f"     w={w:2d} ({w*cell_size_m:3d} м): объектов {pct_obj:.1f}%")

    # Маска покрытия (не фильтруем за пределами данных)
    if coverage_mask_path and os.path.exists(coverage_mask_path):
        with rasterio.open(coverage_mask_path) as csrc:
            cov = csrc.read(1)
        if cov.shape != dsm.shape:
            from scipy.ndimage import zoom as ndzoom
            cov = ndzoom(cov, (dsm.shape[0]/cov.shape[0], dsm.shape[1]/cov.shape[1]), order=0)
            cov = cov[:dsm.shape[0], :dsm.shape[1]]
        object_mask[cov == 0] = False

    n_objects = int(np.sum(object_mask & ~nan_mask))
    n_ground = int(np.sum(~object_mask & ~nan_mask))
    print(f"   Объектов: {n_objects:,} ({100*n_objects/max(1,n_ground+n_objects):.1f}%)")
    print(f"   Земля: {n_ground:,}")

    # ═══ Интерполяция: заполняем объектные пиксели ═══
    # Метод Liu et al.: image inpainting. Мы используем surface (которая уже
    # содержит opened-значения для объектов) + сглаживание
    dtm = surface.copy()

    # Сглаживание для устранения ступенчатых артефактов opening
    dtm = ndimage.uniform_filter(dtm, size=3)

    # DTM ≤ DSM (по определению)
    valid = ~nan_mask
    dtm[valid] = np.minimum(dtm[valid], dsm[valid])

    # Восстанавливаем NaN
    dtm[nan_mask] = -9999

    print(f"   DTM: мин={np.nanmin(dtm[valid]):.1f}, макс={np.nanmax(dtm[valid]):.1f}, "
          f"медиана={np.nanmedian(dtm[valid]):.1f}")

    ndsm = dsm - dtm
    ndsm[nan_mask] = 0
    print(f"   nDSM (высота объектов): медиана={np.median(ndsm[valid & object_mask]):.1f} м, "
          f"P95={np.percentile(ndsm[valid & object_mask], 95):.1f} м")

    # Сохраняем
    profile.update(dtype='float32', count=1, nodata=-9999)
    with rasterio.open(dtm_out_path, 'w', **profile) as dst:
        dst.write(dtm.astype(np.float32), 1)

    del dsm, dsm_filled, surface, opened, dtm, object_mask; gc.collect()
    print(f"   DTM сохранён: {dtm_out_path}")
    return dtm_out_path


# ══════════════════════════════════════════════════════════════════════════════
# 4. ПОРОГОВАЯ МАСКА 2 М (тайлами)
# ══════════════════════════════════════════════════════════════════════════════
def threshold_mask_tiled(dsm_path, out_path, threshold, water_level, tile_sz=4096):
    src = gdal.Open(dsm_path)
    cols, rows = src.RasterXSize, src.RasterYSize
    band = src.GetRasterBand(1); src_nd = band.GetNoDataValue()
    drv = gdal.GetDriverByName('GTiff')
    dst = drv.Create(out_path, cols, rows, 1, gdal.GDT_Byte,
                     ['COMPRESS=LZW', 'TILED=YES', 'BLOCKXSIZE=256', 'BLOCKYSIZE=256'])
    dst.SetGeoTransform(src.GetGeoTransform())
    dst.SetProjection(src.GetProjection())
    dst.GetRasterBand(1).SetNoDataValue(255)
    n_ty = (rows + tile_sz - 1) // tile_sz
    for ty in range(n_ty):
        y_off = ty * tile_sz; y_sz = min(tile_sz, rows - y_off)
        for tx in range((cols + tile_sz - 1) // tile_sz):
            x_off = tx * tile_sz; x_sz = min(tile_sz, cols - x_off)
            tile = band.ReadAsArray(x_off, y_off, x_sz, y_sz).astype(np.float32)
            nd_mask = np.isnan(tile)
            if src_nd is not None: nd_mask |= (tile == src_nd)
            tile[nd_mask] = water_level
            dst.GetRasterBand(1).WriteArray((tile <= threshold).astype(np.uint8), x_off, y_off)
        if (ty+1) % max(1, n_ty//5) == 0 or ty == n_ty-1:
            print(f"     {100*(ty+1)/n_ty:.0f}%")
    dst.FlushCache(); dst = None; src = None


# ══════════════════════════════════════════════════════════════════════════════
# 5. СВЯЗНОСТЬ + DTM-КОРРЕКЦИЯ
# ══════════════════════════════════════════════════════════════════════════════
def connectivity(mask_2m, out_conn, seeds, crs, closing_m, conn_res, seed_r,
                 cov_path=None, dtm_path=None, threshold=None):
    mask_10m = os.path.join(WD, "mask_10m.tif")
    gdal.Warp(mask_10m, mask_2m, options=gdal.WarpOptions(
        format="GTiff", xRes=conn_res, yRes=conn_res, resampleAlg="max",
        creationOptions=["COMPRESS=LZW", "TILED=YES"]))
    with rasterio.open(mask_10m) as src:
        arr = src.read(1); transform = src.transform; profile = src.profile.copy()
    print(f"   Маска 10 м: {arr.shape} ({arr.shape[0]*arr.shape[1]:,} px)")

    # DTM-коррекция: добавляем пиксели, где DTM ≤ порог (здания/деревья)
    if dtm_path and os.path.exists(dtm_path) and threshold:
        with rasterio.open(dtm_path) as dsrc:
            dtm = dsrc.read(1)
        dtm[dtm == -9999] = np.nan
        dtm_mask = (~np.isnan(dtm) & (dtm <= threshold)).astype(np.uint8)
        n_add = int(np.sum(~arr.astype(bool) & dtm_mask.astype(bool)))
        arr = arr | dtm_mask
        print(f"   + DTM добавил {n_add:,} px (SMRF-фильтрованные здания/деревья)")
        del dtm, dtm_mask

    # Closing
    closing_px = max(1, int(round(closing_m / conn_res)))
    struct = ndimage.generate_binary_structure(2, 2)
    arr = ndimage.binary_closing(arr, structure=struct, iterations=closing_px).astype(np.uint8)

    # Connected components + seeds
    labels, n_feat = ndimage.label(arr)
    print(f"   Компонент: {n_feat}")
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
        for lbl in range(1, n_feat+1):
            if np.sum(labels == lbl) > 5000: valid_labels.add(lbl)

    conn = np.isin(labels, list(valid_labels)).astype(np.uint8)

    # Покрытие
    if cov_path and os.path.exists(cov_path):
        with rasterio.open(cov_path) as csrc: cov = csrc.read(1)
        if cov.shape != conn.shape:
            from scipy.ndimage import zoom as ndzoom
            cov = ndzoom(cov, (conn.shape[0]/cov.shape[0], conn.shape[1]/cov.shape[1]), order=0)
            cov = cov[:conn.shape[0], :conn.shape[1]]
        n_before = int(np.sum(conn))
        conn = conn & cov
        print(f"   Покрытие: удалено {n_before - int(np.sum(conn)):,} px")

    n_final = int(np.sum(conn))
    print(f"   Итого: {n_final:,} px (~{n_final * conn_res**2 / 10000:.0f} га)")
    profile.update(dtype='uint8', nodata=255)
    with rasterio.open(out_conn, 'w', **profile) as dst: dst.write(conn, 1)
    del labels, arr, conn; gc.collect()


# ══════════════════════════════════════════════════════════════════════════════
# 6. ФИНАЛЬНАЯ МАСКА + КАРТА
# ══════════════════════════════════════════════════════════════════════════════
def apply_filter_2m(mask_2m, conn_10m, out_final, tile_sz=4096):
    conn_2m = os.path.join(WD, "conn_2m.tif")
    src_mask = gdal.Open(mask_2m); gt = src_mask.GetGeoTransform()
    gdal.Warp(conn_2m, conn_10m, options=gdal.WarpOptions(
        format="GTiff", xRes=abs(gt[1]), yRes=abs(gt[5]), resampleAlg="near",
        creationOptions=["COMPRESS=LZW", "TILED=YES"]))
    src_filt = gdal.Open(conn_2m)
    cols, rows = src_mask.RasterXSize, src_mask.RasterYSize
    drv = gdal.GetDriverByName('GTiff')
    dst = drv.Create(out_final, cols, rows, 1, gdal.GDT_Byte,
                     ['COMPRESS=LZW', 'TILED=YES'])
    dst.SetGeoTransform(gt); dst.SetProjection(src_mask.GetProjection())
    for ty in range((rows + tile_sz - 1) // tile_sz):
        y_off = ty * tile_sz; y_sz = min(tile_sz, rows - y_off)
        for tx in range((cols + tile_sz - 1) // tile_sz):
            x_off = tx * tile_sz; x_sz = min(tile_sz, cols - x_off)
            m = src_mask.GetRasterBand(1).ReadAsArray(x_off, y_off, x_sz, y_sz)
            fc, fr = src_filt.RasterXSize, src_filt.RasterYSize
            result = np.zeros_like(m)
            if x_off < fc and y_off < fr:
                fxs = min(x_sz, fc - x_off); fys = min(y_sz, fr - y_off)
                f = src_filt.GetRasterBand(1).ReadAsArray(x_off, y_off, fxs, fys)
                result[:fys, :fxs] = m[:fys, :fxs] & f
            dst.GetRasterBand(1).WriteArray(result, x_off, y_off)
    dst.FlushCache(); dst = None; src_mask = None; src_filt = None
    if os.path.exists(conn_2m): os.remove(conn_2m)


def vectorize_and_map(final_mask, crs, out_html):
    with rasterio.open(final_mask) as src:
        arr = src.read(1); transform = src.transform; bounds = src.bounds
    n_flood = int(np.sum(arr))
    print(f"   Затопленных: {n_flood:,} px")
    if n_flood == 0: print("   Нет затопления."); return
    geoms = list({'properties': {'v': int(v)}, 'geometry': s}
                 for s, v in shapes(arr, mask=arr == 1, transform=transform))
    del arr; gc.collect()
    if not geoms: return
    gdf = gpd.GeoDataFrame.from_features(geoms, crs=crs); del geoms; gc.collect()
    gdf = gdf[gdf.geometry.area >= MIN_AREA]
    print(f"   Полигонов (≥{MIN_AREA} м²): {len(gdf)}")
    if gdf.empty: return
    gdf = gdf.dissolve(); gdf.geometry = gdf.geometry.simplify(SIMPLIFY)
    area_ha = gdf.geometry.area.sum() / 10000
    print(f"   Площадь: {area_ha:.0f} га")

    bbox_wgs = gpd.GeoDataFrame(
        {'geometry': [box(bounds.left, bounds.bottom, bounds.right, bounds.top)]},
        crs=crs).to_crs("EPSG:4326").total_bounds
    cy, cx = (bbox_wgs[1]+bbox_wgs[3])/2, (bbox_wgs[0]+bbox_wgs[2])/2
    m = folium.Map(location=[cy, cx], zoom_start=11, tiles='CartoDB positron')
    flood_wgs = gdf.to_crs("EPSG:4326")
    folium.GeoJson(flood_wgs, name='Зона затопления',
                   style_function=lambda x: {'fillColor':'#1E90FF','color':'#0000CD',
                                             'weight':0.5,'fillOpacity':0.5}).add_to(m)
    try:
        import osmnx as ox; ox.settings.timeout = 60
        fb = flood_wgs.total_bounds
        lu = ox.features_from_bbox(bbox=(fb[0],fb[1],fb[2],fb[3]), tags={'landuse': True})
        if not lu.empty:
            lu = lu.to_crs(crs)
            lu = lu[lu.geometry.type.isin(['Polygon','MultiPolygon'])]
            impact = gpd.overlay(lu, gdf, how='intersection')
            impact['ha'] = impact.geometry.area / 10000
            print("\n   Landuse:")
            print(impact.groupby('landuse')['ha'].sum().sort_values(ascending=False).head(10).to_string())
    except Exception as e:
        print(f"   ⚠ OSM landuse: {e}")
    folium.LayerControl().add_to(m)
    m.save(out_html); print(f"   Карта: {out_html}")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════
def main():
    print("=" * 70)
    print(" FLOOD MAP v7 — SMRF (Liu et al. 2023)")
    print(f" Уровень: {FLOOD_LEVEL} м БС-77")
    print(f" SMRF: slope={SMRF_SLOPE}, window={SMRF_WINDOW_MAX} м")
    print("=" * 70)

    mask_2m  = os.path.join(WD, "mask_2m.tif")
    conn_10m = os.path.join(WD, "conn_10m.tif")
    final_2m = os.path.join(WD, "flood_final_2m.tif")
    cov_mask = os.path.join(WD, "coverage_mask.tif")
    dtm_10m  = os.path.join(WD, "dtm_smrf_10m.tif")
    out_html = os.path.join(WD, "spb_flood_v7.html")

    # 1
    print(f"\n[1/6] Калибровка...")
    offset = calibrate(INPUT_DSM, CONTROL_POINTS, GAUGE_CSV, CRS_METRIC)
    threshold = FLOOD_LEVEL + offset
    water_level = offset
    gc.collect()

    # 2
    print(f"\n[2/6] Маска покрытия...")
    create_coverage_mask(INPUT_DSM, cov_mask, CRS_METRIC)
    gc.collect()

    # 3
    print(f"\n[3/6] SMRF: DSM → DTM...")
    apply_smrf(INPUT_DSM, dtm_10m, CRS_METRIC,
               slope_threshold=SMRF_SLOPE,
               window_max_m=SMRF_WINDOW_MAX,
               cell_size_m=SMRF_CELL,
               coverage_mask_path=cov_mask)
    gc.collect()

    # 4
    print(f"\n[4/6] Пороговая маска 2 м (порог {threshold:.2f})...")
    threshold_mask_tiled(INPUT_DSM, mask_2m, threshold, water_level)
    gc.collect()

    # 5
    print(f"\n[5/6] Связность + DTM-коррекция...")
    connectivity(mask_2m, conn_10m, WATER_SEEDS, CRS_METRIC,
                 CLOSING_M, CONN_RES, SEED_R,
                 cov_path=cov_mask, dtm_path=dtm_10m, threshold=threshold)
    gc.collect()

    # 6
    print(f"\n[6/6] Финальная маска + карта...")
    apply_filter_2m(mask_2m, conn_10m, final_2m)
    for f in [mask_2m, conn_10m]: 
        if os.path.exists(f): os.remove(f)
    vectorize_and_map(final_2m, CRS_METRIC, out_html)
    print(f"\n{'='*70}\n ГОТОВО!\n{'='*70}")

main()
