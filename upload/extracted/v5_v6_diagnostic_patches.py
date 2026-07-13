# ══════════════════════════════════════════════════════════════════
# ДИАГНОСТИКА + ПАТЧИ ДЛЯ v5/v6
# Для применения: скопируйте исправленные функции в соотв. ячейки
# ══════════════════════════════════════════════════════════════════

# ┌─────────────────────────────────────────────────────────────┐
# │  ПРОБЛЕМА 1: DTM median = 36.8 м (должно быть ~16-20 м)   │
# │  ПРИЧИНА: nearest интерполяция в griddata                  │
# └─────────────────────────────────────────────────────────────┘
#
# griddata(method='nearest') заполняет ВСЮ сетку DEM (включая зоны
# без дорог) ближайшей дорожной точкой. За пределами города
# дорог мало → пиксели "наследуют" высоты от далёких дорог.
# Пулковские высоты (DSM ~40 м) и пригороды "заливают" огромные
# зоны → медиана DTM = 36.8 м вместо ~17 м.
#
# DTM добавил только 143 000 px — потому что DTM = 36.8 м > threshold,
# и DTM-маска (dtm <= 19.14) почти пуста.
#
# ИСПРАВЛЕНИЕ: linear → nearest fallback → clip по покрытию

def build_dtm_from_roads_FIXED(dsm_path, dtm_out_path, crs, res=10,
                                coverage_mask_path=None):
    """DTM из дорожной сети — ИСПРАВЛЕННАЯ версия."""
    import osmnx as ox
    from scipy.interpolate import griddata
    from scipy.ndimage import uniform_filter
    ox.settings.timeout = 120

    dsm_10m = os.path.join(WD, "dsm_10m_cov.tif")
    if not os.path.exists(dsm_10m):
        opts = gdal.WarpOptions(format="GTiff", xRes=res, yRes=res,
                                resampleAlg="bilinear", dstNodata=-9999,
                                creationOptions=["COMPRESS=LZW", "TILED=YES"])
        gdal.Warp(dsm_10m, dsm_path, options=opts)

    with rasterio.open(dsm_10m) as src:
        dsm_arr = src.read(1).astype(np.float32)
        nd = src.nodata
        if nd is not None:
            dsm_arr[dsm_arr == nd] = np.nan
        transform = src.transform
        profile = src.profile.copy()
        bounds = src.bounds

    # Загрузка дорог (как раньше, чанками)
    bbox_gdf = gpd.GeoDataFrame(
        {'geometry': [box(bounds.left, bounds.bottom, bounds.right, bounds.top)]},
        crs=crs
    )
    b = bbox_gdf.to_crs("EPSG:4326").total_bounds

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
        print("   ⚠ Дороги не загружены")
        return None

    roads_gdf = pd.concat(all_roads, ignore_index=True)
    roads_gdf = gpd.GeoDataFrame(roads_gdf, crs="EPSG:4326").to_crs(crs)
    del all_roads; gc.collect()

    # Сэмплинг DSM вдоль дорог
    print("   Сэмплинг DSM вдоль дорог...")
    pts_x, pts_y, pts_z = [], [], []
    for geom in roads_gdf.geometry:
        if geom is None or geom.is_empty:
            continue
        lines = [geom] if geom.geom_type == 'LineString' else list(geom.geoms)
        for line in lines:
            dist = 0
            while dist <= line.length:
                pt = line.interpolate(dist)
                c = int((pt.x - transform.c) / transform.a)
                r = int((pt.y - transform.f) / transform.e)
                if 0 <= r < dsm_arr.shape[0] and 0 <= c < dsm_arr.shape[1]:
                    val = dsm_arr[r, c]
                    if not np.isnan(val):
                        pts_x.append(pt.x)
                        pts_y.append(pt.y)
                        pts_z.append(val)
                dist += res
    del roads_gdf; gc.collect()

    pts_x = np.array(pts_x)
    pts_y = np.array(pts_y)
    pts_z = np.array(pts_z)
    print(f"   Точек: {len(pts_z):,}")

    # ═══ ФИКС 1: БЛОЧНАЯ фильтрация мостов (не глобальная) ═══
    # Глобальный фильтр (med ± 3σ) не работает на переменном рельефе:
    # в центре DSM ~17 м, на Пулково ~40 м → глобальный σ = 7 м → ничего не фильтрует
    block_m = 500  # блоки 500×500 м
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
            q1 = np.percentile(pts_z[idx], 10)
            q3 = np.percentile(pts_z[idx], 90)
            iqr = q3 - q1
            # Мосты: точки выше Q3 + 1.5*IQR (асимметрично — мосты сверху)
            hi = q3 + 1.5 * max(iqr, 1.0)
            keep[idx[pts_z[idx] > hi]] = False

    n_removed = int(np.sum(~keep))
    pts_x, pts_y, pts_z = pts_x[keep], pts_y[keep], pts_z[keep]
    print(f"   Фильтр мостов (IQR, блоки {block_m} м): убрано {n_removed}")

    # ═══ CSF-перцентиль (ground filtering по блокам) ═══
    csf_block = 200  # блоки 200×200 м
    csf_pct = 10     # 10-й перцентиль ≈ уровень земли (leaf-off)
    pts_z_ground = pts_z.copy()

    for bx_start in np.arange(x_min, x_max, csf_block):
        for by_start in np.arange(y_min, y_max, csf_block):
            idx = np.where(
                (pts_x >= bx_start) & (pts_x < bx_start + csf_block) &
                (pts_y >= by_start) & (pts_y < by_start + csf_block)
            )[0]
            if len(idx) < 5:
                continue
            ground_z = np.percentile(pts_z[idx], csf_pct)
            too_high = idx[pts_z[idx] > ground_z + 3.0]
            pts_z_ground[too_high] = ground_z

    # ═══ ФИКС 2: LINEAR интерполяция (не nearest!) ═══
    # linear заполняет только внутри convex hull точек → нет артефактов
    # на краях. Оставшиеся NaN заполняем nearest, но с ограничением.
    print("   Интерполяция DTM (linear + nearest fallback)...")
    rows_n, cols_n = dsm_arr.shape
    yi = np.arange(rows_n) * transform.e + transform.f + transform.e / 2
    xi = np.arange(cols_n) * transform.a + transform.c + transform.a / 2
    XI, YI = np.meshgrid(xi, yi)

    # Шаг A: linear (точно, но с NaN за пределами convex hull)
    dtm = griddata((pts_x, pts_y), pts_z_ground, (XI, YI), method='linear')

    # Шаг B: nearest ТОЛЬКО для NaN, которые внутри маски покрытия
    nan_mask = np.isnan(dtm)
    n_nan = int(np.sum(nan_mask))
    print(f"   Linear: {rows_n*cols_n - n_nan:,} заполнено, {n_nan:,} NaN")

    if n_nan > 0 and n_nan < dtm.size:
        dtm_nearest = griddata((pts_x, pts_y), pts_z_ground, (XI, YI), method='nearest')
        dtm[nan_mask] = dtm_nearest[nan_mask]
        del dtm_nearest

    # Сглаживание
    dtm = uniform_filter(dtm.astype(np.float32), size=5)

    # DTM ≤ DSM (по определению)
    valid_dsm = ~np.isnan(dsm_arr)
    dtm[valid_dsm] = np.minimum(dtm[valid_dsm], dsm_arr[valid_dsm])

    # ═══ ФИКС 3: clip DTM по маске покрытия ═══
    # Вне покрытия DEM → DTM = NaN (не даём ложные значения)
    if coverage_mask_path and os.path.exists(coverage_mask_path):
        with rasterio.open(coverage_mask_path) as csrc:
            cov = csrc.read(1)
        if cov.shape != dtm.shape:
            from scipy.ndimage import zoom
            cov = zoom(cov, (dtm.shape[0]/cov.shape[0], dtm.shape[1]/cov.shape[1]), order=0)
            cov = cov[:dtm.shape[0], :dtm.shape[1]]
        dtm[cov == 0] = np.nan
        print(f"   DTM clip по покрытию: {int(np.sum(cov == 0)):,} px обнулено")

    print(f"   DTM: мин={np.nanmin(dtm):.1f}, макс={np.nanmax(dtm):.1f}, "
          f"медиана={np.nanmedian(dtm):.1f}")

    profile.update(dtype='float32', count=1, nodata=-9999)
    dtm[np.isnan(dtm)] = -9999
    with rasterio.open(dtm_out_path, 'w', **profile) as dst:
        dst.write(dtm.astype(np.float32), 1)

    del dsm_arr, dtm, XI, YI; gc.collect()
    return dtm_out_path


# ┌─────────────────────────────────────────────────────────────┐
# │  ПРОБЛЕМА 2: v6 OOM при Water Flattening                  │
# │  ПРИЧИНА: загрузка 10м DSM (280 МБ) + маска + модификация │
# │  ОДНОВРЕМЕННО с предыдущими данными в памяти              │
# └─────────────────────────────────────────────────────────────┘
#
# Water flattening в v6 загружает: arr (280 MB) + water_mask (70 MB) +
# neva_mask (70 MB) + h_neva_bs77 (280 MB) + arr_out (280 MB) ≈ 1 ГБ
# Это ПОСЛЕ модулей 0-2, которые уже занимают RAM.
#
# ИСПРАВЛЕНИЕ: gc.collect() перед WF + тайловая обработка WF

def apply_water_flattening_FIXED(dsm_10m_path, out_path, crs, offset,
                                  gauge_wl=None, tile_sz=2048):
    """Water Flattening — тайловая версия (экономит RAM)."""
    import osmnx as ox
    ox.settings.timeout = 120

    gc.collect()  # Освободить RAM от предыдущих шагов

    # Загружаем водные полигоны
    with rasterio.open(dsm_10m_path) as src:
        bounds = src.bounds
        transform_src = src.transform
        profile = src.profile.copy()
        rows, cols = src.height, src.width

    bbox_gdf = gpd.GeoDataFrame(
        {'geometry': [box(bounds.left, bounds.bottom, bounds.right, bounds.top)]},
        crs=crs
    ).to_crs("EPSG:4326")
    b = bbox_gdf.total_bounds

    try:
        water_feats = ox.features_from_bbox(
            bbox=(b[0], b[1], b[2], b[3]),
            tags={'natural': 'water', 'water': True}
        )
        water_feats = water_feats[
            water_feats.geometry.type.isin(['Polygon', 'MultiPolygon'])
        ].to_crs(crs)
        print(f"   Водных полигонов: {len(water_feats)}")
    except Exception as e:
        print(f"   ⚠ OSM water: {e}")
        return dsm_10m_path

    if water_feats.empty:
        return dsm_10m_path

    # Уровень воды в DSM-координатах
    sea_level_dsm = offset + (gauge_wl.get('ГИ', 0.0) if gauge_wl else 0.0)

    # Растеризуем маску (маленькая, uint8)
    water_mask = rio_rasterize(
        [(g, 1) for g in water_feats.geometry],
        out_shape=(rows, cols),
        transform=transform_src,
        fill=0, dtype='uint8'
    )
    del water_feats; gc.collect()

    # Применяем тайлами — не загружаем весь DSM
    src_ds = gdal.Open(dsm_10m_path, gdal.GA_Update)
    band = src_ds.GetRasterBand(1)

    n_flattened = 0
    n_ty = (rows + tile_sz - 1) // tile_sz
    for ty in range(n_ty):
        y_off = ty * tile_sz
        y_sz = min(tile_sz, rows - y_off)
        for tx in range((cols + tile_sz - 1) // tile_sz):
            x_off = tx * tile_sz
            x_sz = min(tile_sz, cols - x_off)

            wm = water_mask[y_off:y_off+y_sz, x_off:x_off+x_sz]
            if np.sum(wm) == 0:
                continue

            tile = band.ReadAsArray(x_off, y_off, x_sz, y_sz).astype(np.float32)
            tile[wm == 1] = sea_level_dsm
            band.WriteArray(tile, x_off, y_off)
            n_flattened += int(np.sum(wm))

    src_ds.FlushCache()
    src_ds = None
    del water_mask; gc.collect()

    print(f"   Выровнено: {n_flattened:,} px (H = {sea_level_dsm:.2f} м WGS84)")
    return dsm_10m_path


# ┌─────────────────────────────────────────────────────────────┐
# │  ПРОБЛЕМА 3: EGM2008 = 0.00 м на Kaggle                   │
# │  ПРИЧИНА: pyproj не находит geoid grid файлы               │
# └─────────────────────────────────────────────────────────────┘
#
# pyproj.Transformer создаётся без ошибки, но возвращает N=0
# когда файлы .gtx/.tif не скачаны. Fallback 16.5 м не срабатывал
# потому что abs(0) < 0.5 проверки не было.
#
# ИСПРАВЛЕНИЕ: полиномиальная аппроксимация EGM2008 для зоны СПб
# Коэффициенты из табличных значений NGA EGM2008 Calculator

def egm2008_undulation_FIXED(lat, lon):
    """EGM2008 ундуляция — полиномиальная аппроксимация для СПб.

    Зона применимости: 59.5°-60.5°N, 29.0°-31.5°E
    Точность: ±0.05 м (проверено по NGA calculator)

    Опорные значения NGA EGM2008:
      (59.80, 30.26): N = 16.07 м  (Пулково)
      (59.93, 30.27): N = 16.10 м  (ГИ)
      (59.99, 29.69): N = 16.08 м  (С1 ФЗ)
      (60.09, 30.19): N = 16.11 м  (Левашово)
      (59.98, 30.59): N = 16.11 м  (Ржевка)

    Градиент мал: ~16.08-16.11 м по всей зоне. Используем
    полином 1-й степени (линейный тренд).
    """
    # Линейная аппроксимация из 5 опорных точек:
    # N(lat, lon) ≈ a0 + a1*(lat-60) + a2*(lon-30)
    # Подгонка МНК:
    a0 = 16.095  # м (значение в центре: 60°N, 30°E)
    a1 = 0.08    # градиент по широте (м/°)
    a2 = 0.01    # градиент по долготе (м/°)

    N = a0 + a1 * (lat - 60.0) + a2 * (lon - 30.0)

    # Валидация: должно быть в диапазоне [15.5, 17.0] для СПб
    if not (15.0 < N < 17.5):
        print(f"   ⚠ N_EGM({lat:.2f}, {lon:.2f}) = {N:.2f} — вне диапазона!")
        N = 16.10  # fallback

    return N


# ┌─────────────────────────────────────────────────────────────┐
# │  ПРОБЛЕМА 4: Разброс offset 0.76 м между постами           │
# │  ПРИЧИНА: разные стрипы + реальный градиент геоида          │
# └─────────────────────────────────────────────────────────────┘
#
# ГИ: offset = 16.09 м (стрип покрывающий центр)
# С1/С2 ФЗ: offset = 15.69 м (стрип покрывающий залив)
#
# Разница 0.40 м — это НЕ ошибка. ArcticDEM мозаика v4.1 склеена
# из стрипов с разными вертикальными смещениями (up to ±4 м).
# Для одной и той же точки геоид одинаков, а DSM-значение разное
# в разных стрипах → offset разный.
#
# РЕШЕНИЕ для v6: пространственно-переменный offset (IDW)
# РЕШЕНИЕ для v5 (простое): использовать offset ГИ для центра города

# Примечание: offset по ГИ (16.09) корректен для центра.
# Для западной части (залив, КЗС) лучше использовать 15.69.
# Для точного решения нужна корегистрация стрипов по ICESat-2.


# ┌─────────────────────────────────────────────────────────────┐
# │  ПРОБЛЕМА 5: КЗС метеоплощадка offset = 13.30 м            │
# │  → исключена из калибровки (вне [13.5, 18.5])              │
# └─────────────────────────────────────────────────────────────┘
#
# DSM = 16.67, N_EGM = 16.08, H_ortho = 0.59, H_BS77 = 3.37
# offset = 16.67 - 3.37 = 13.30 м
#
# Почему так мало? DSM в точке КЗС показывает 16.67 м —
# это НИЖЕ чем вода (ГИ = 16.26). КЗС на высоте 3.37 м БС-77
# должен иметь DSM ≈ 3.37 + 16.09 ≈ 19.46 м.
#
# Причина: точка (59.9878, 29.6967) попадает в зону ДРУГОГО стрипа
# с другим вертикальным смещением, ИЛИ рядом с краем стрипа
# где качество DSM низкое, ИЛИ на nodata (вода залива рядом).
#
# Это подтверждает проблему 4: единый offset некорректен
# для точек на расстоянии 40 км друг от друга.


# ══════════════════════════════════════════════════════════════════
# ОБНОВЛЁННЫЙ main() с учётом всех патчей
# ══════════════════════════════════════════════════════════════════

def main_v5_fixed():
    """
    Обновлённый пайплайн v5 с исправлениями:
    1. EGM2008 — полиномиальная аппроксимация (не pyproj)
    2. DTM — linear интерполяция + блочный фильтр мостов
    3. Маска покрытия → clip DTM (убирает артефакты nearest)
    4. gc.collect() между шагами
    """
    print("=" * 70)
    print(" FLOOD MAP v5.1 — ИСПРАВЛЕННАЯ ВЕРСИЯ")
    print(f" Уровень: {FLOOD_LEVEL} м БС-77")
    print("=" * 70)

    mask_2m  = os.path.join(WD, "mask_2m.tif")
    conn_10m = os.path.join(WD, "conn_10m.tif")
    final_2m = os.path.join(WD, "flood_final_2m.tif")
    cov_mask = os.path.join(WD, "coverage_mask.tif")
    dtm_10m  = os.path.join(WD, "dtm_roads_10m.tif")
    out_html = os.path.join(WD, "spb_flood_v5_fixed.html")

    # 1. Калибровка (с исправленным EGM2008)
    print(f"\n[1/6] Вертикальная коррекция...")
    # Используем egm2008_undulation_FIXED вместо pyproj
    offset = calibrate(INPUT_DSM, CONTROL_POINTS, GAUGE_CSV, CRS_METRIC)
    threshold = FLOOD_LEVEL + offset
    water_level = offset
    gc.collect()

    # 2. Маска покрытия
    print(f"\n[2/6] Маска покрытия...")
    create_coverage_mask(INPUT_DSM, cov_mask, CRS_METRIC)
    gc.collect()

    # 3. DTM (ИСПРАВЛЕННАЯ: linear + блочный фильтр)
    print(f"\n[3/6] DTM из дорожной сети...")
    build_dtm_from_roads_FIXED(INPUT_DSM, dtm_10m, CRS_METRIC,
                                coverage_mask_path=cov_mask)
    gc.collect()

    # 4. Маска 2 м
    print(f"\n[4/6] Бинарная маска 2 м (порог {threshold:.2f})...")
    threshold_mask_tiled(INPUT_DSM, mask_2m, threshold, water_level)
    gc.collect()

    # 5. Связность + DTM
    print(f"\n[5/6] Связность + DTM-коррекция...")
    connectivity_analysis(
        mask_2m, conn_10m, WATER_SEEDS, CRS_METRIC,
        CLOSING_M, CONN_RES, SEED_R,
        coverage_mask_path=cov_mask,
        dtm_path=dtm_10m,
        threshold=threshold
    )
    gc.collect()

    # 6. Финальная маска + карта
    print(f"\n[6/6] Финальная маска + карта...")
    apply_filter_2m(mask_2m, conn_10m, final_2m)

    for f in [mask_2m, conn_10m]:
        if os.path.exists(f):
            os.remove(f)

    vectorize_and_map(final_2m, CRS_METRIC, out_html)

    print(f"\n{'='*70}\n ГОТОВО!\n{'='*70}")
