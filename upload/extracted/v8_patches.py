# ══════════════════════════════════════════════════════════════════════════════
# FLOOD MAP v8 — ПАТЧИ
# Применение: замените соответствующие блоки в ноутбуке
# ══════════════════════════════════════════════════════════════════════════════


# ┌──────────────────────────────────────────────────────────────────────────┐
# │  ПАТЧ 1: Cell 2 (КОНФИГУРАЦИЯ) — EARTHDATA_TOKEN вместо USER/PASS      │
# └──────────────────────────────────────────────────────────────────────────┘
# ЗАМЕНИТЬ строки:
#   EARTHDATA_USER = os.environ.get("EARTHDATA_USER", "")
#   EARTHDATA_PASS = os.environ.get("EARTHDATA_PASS", "")
# НА:

EARTHDATA_TOKEN = os.environ.get("EARTHDATA_TOKEN", "")


# ┌──────────────────────────────────────────────────────────────────────────┐
# │  ПАТЧ 2: Cell 3 — download_atl08: TOKEN-авторизация                    │
# └──────────────────────────────────────────────────────────────────────────┘
# ЗАМЕНИТЬ функцию download_atl08 на:

def download_atl08(lat_min=59.5, lat_max=60.4, lon_min=28.5, lon_max=31.5,
                   date_end=STRIP_DATE, out_dir=None):
    """Загружает ATL08 гранулы через earthaccess (TOKEN)."""
    try:
        import earthaccess
    except ImportError:
        print("   earthaccess не установлен"); return []

    out_dir = out_dir or os.path.join(WD, "atl08")
    os.makedirs(out_dir, exist_ok=True)
    existing = [f for f in os.listdir(out_dir) if f.endswith(".h5")]
    if existing:
        print(f"   ATL08: {len(existing)} файлов из кэша")
        return [os.path.join(out_dir, f) for f in existing]

    if not EARTHDATA_TOKEN:
        print("   ⚠ EARTHDATA_TOKEN не задан — пропускаем ATL08"); return []

    print(f"   Поиск ATL08 для [{lat_min},{lon_min}]–[{lat_max},{lon_max}]...")
    earthaccess.login(strategy="environment")  # читает EARTHDATA_TOKEN
    results = earthaccess.search_data(short_name="ATL08",
        bounding_box=(lon_min, lat_min, lon_max, lat_max),
        temporal=("2018-10-14", date_end), count=50)
    print(f"   Найдено гранул: {len(results)}")
    if not results: return []
    files = earthaccess.download(results, local_path=out_dir)
    print(f"   Скачано: {len(files)} файлов"); return files


# ┌──────────────────────────────────────────────────────────────────────────┐
# │  ПАТЧ 3: Cell 4 — НОВАЯ ФУНКЦИЯ: де-бриджинг                          │
# └──────────────────────────────────────────────────────────────────────────┘
# ДОБАВИТЬ после функции water_flattening (перед threshold_mask_tiled):

def debridge_osm(crs, tile_sz=2048):
    """
    Де-бриджинг: прожигание мостов в DSM до уровня воды.
    
    Проблема: мосты в DSM — это «стены», блокирующие поток.
    ArcticDEM показывает полотно моста (~5-8 м БС-77), которое
    выше порога затопления → белая полоса через зону затопления.
    
    Решение: пересечение highway + waterway из OSM → буфер →
    в DSM (10 м) заменяем на уровень воды под мостом.
    """
    import osmnx as ox
    ox.settings.timeout = 120

    dsm10 = os.path.join(WD, "dsm_10m.tif")
    if not os.path.exists(dsm10):
        print("   ⚠ dsm_10m.tif не найден"); return

    with rasterio.open(dsm10) as src:
        rows, cols = src.shape
        tf = src.transform
        bounds = src.bounds

    b = gpd.GeoDataFrame(
        {'geometry': [box(bounds.left, bounds.bottom, bounds.right, bounds.top)]},
        crs=crs).to_crs("EPSG:4326").total_bounds
    bbox = (b[0], b[1], b[2], b[3])

    # Загружаем мосты из OSM (пересечение дорог и воды)
    try:
        bridges = ox.features_from_bbox(bbox=bbox,
            tags={'bridge': True})
        if bridges.empty:
            # Fallback: man_made=bridge
            bridges = ox.features_from_bbox(bbox=bbox,
                tags={'man_made': 'bridge'})
    except Exception as e:
        print(f"   Мосты OSM: {e}"); return

    if bridges.empty:
        print("   Мосты: не найдены в OSM"); return

    # Фильтруем линии и полигоны
    bridges = bridges[bridges.geometry.type.isin(
        ['LineString', 'MultiLineString', 'Polygon', 'MultiPolygon']
    )].to_crs(crs)

    # Буфер: ширина моста ~15-30 м, берём 20 м по обе стороны от оси
    bridge_polys = bridges.geometry.buffer(20)
    print(f"   Мосты: {len(bridges)} объектов из OSM")

    # Растеризуем маску мостов
    bridge_mask = rio_rasterize(
        [(g, 1) for g in bridge_polys],
        out_shape=(rows, cols),
        transform=tf,
        fill=0, dtype='uint8'
    )

    # Прожигаем: заменяем высоту моста на минимум из окрестности
    # (уровень воды под мостом ≈ минимум соседних пикселей)
    src_ds = gdal.Open(dsm10, gdal.GA_Update)
    band = src_ds.GetRasterBand(1)
    nd_val = band.GetNoDataValue()

    n_burned = 0
    nty = (rows + tile_sz - 1) // tile_sz
    for ty in range(nty):
        yo = ty * tile_sz; ys = min(tile_sz, rows - yo)
        for tx in range((cols + tile_sz - 1) // tile_sz):
            xo = tx * tile_sz; xs = min(tile_sz, cols - xo)
            bm = bridge_mask[yo:yo+ys, xo:xo+xs]
            if np.sum(bm) == 0:
                continue
            tile = band.ReadAsArray(xo, yo, xs, ys).astype(np.float32)
            if nd_val is not None:
                tile[tile == nd_val] = np.nan

            # Для каждого пикселя моста: берём минимум из окна 5×5
            # (уровень воды/набережной рядом с мостом)
            local_min = ndimage.minimum_filter(tile, size=5)
            tile[bm == 1] = local_min[bm == 1]

            if nd_val is not None:
                tile[np.isnan(tile)] = nd_val
            band.WriteArray(tile, xo, yo)
            n_burned += int(np.sum(bm))

    src_ds.FlushCache(); src_ds = None
    del bridge_mask; gc.collect()
    print(f"   Де-бриджинг: {n_burned:,} пикселей мостов прожжено")


# ┌──────────────────────────────────────────────────────────────────────────┐
# │  ПАТЧ 4: Cell 4 — vectorize_and_map: сглаживание полигонов             │
# └──────────────────────────────────────────────────────────────────────────┘
# В функции vectorize_and_map, ЗАМЕНИТЬ строки:
#   gdf = gdf[gdf.geometry.area>=MIN_AREA].dissolve()
#   gdf.geometry = gdf.geometry.simplify(SIMPLIFY)
# НА:

    gdf = gdf[gdf.geometry.area >= MIN_AREA].dissolve()
    # Сглаживание: buffer(+r).buffer(-r) убирает угловатость от 10м сетки
    # Потом simplify для уменьшения числа вершин
    smooth_r = 15  # радиус сглаживания (м)
    gdf.geometry = gdf.geometry.buffer(smooth_r).buffer(-smooth_r)
    gdf.geometry = gdf.geometry.simplify(SIMPLIFY)
    # Удаляем артефакты после buffer (крошечные полигоны)
    gdf = gdf[gdf.geometry.area >= MIN_AREA]


# ┌──────────────────────────────────────────────────────────────────────────┐
# │  ПАТЧ 5: Cell 4 — vectorize_and_map: RMSE подписи                     │
# └──────────────────────────────────────────────────────────────────────────┘
# ЗАМЕНИТЬ строку:
#   Coreg dz: {shift['dz']:+.3f} м (RMSE {shift['rmse_after']:.3f}→{shift['rmse_before']:.3f})<br>
# НА:

    # Coreg dz: {shift['dz']:+.3f} м (RMSE {shift['rmse_before']:.3f}→{shift['rmse_after']:.3f})<br>


# ┌──────────────────────────────────────────────────────────────────────────┐
# │  ПАТЧ 6: Cell 4 — SMRF: fix distance_transform_edt                    │
# └──────────────────────────────────────────────────────────────────────────┘
# В функции apply_smrf, ЗАМЕНИТЬ:
#   idx = distance_transform_edt(nan_mask, return_indices=True)[1]
# НА:

        idx = distance_transform_edt(nan_mask, return_distances=False, return_indices=True)


# ┌──────────────────────────────────────────────────────────────────────────┐
# │  ПАТЧ 7: Cell 5 (MAIN) — убрать дублирование, добавить де-бриджинг     │
# └──────────────────────────────────────────────────────────────────────────┘
# ЗАМЕНИТЬ ВЕСЬ main() на:

def main():
    print("=" * 70)
    print(" FLOOD MAP v8.1 — SMRF + ICESat-2 + де-бриджинг")
    print(f" Уровень: {FLOOD_LEVEL} м БС-77  |  Дата: {STRIP_DATE}")
    print(f" EGM2008(60N,30E)={EGM_A0:.3f} м | δ_BS77={DELTA_BS77:+.2f} м")
    print(f" SMRF: slope={SMRF_SLOPE}, window={SMRF_WINDOW_MAX} м")
    print("=" * 70)

    # Пути
    dsm_coreg   = os.path.join(WD, "dsm_coregistered.tif")
    stable_mask = os.path.join(WD, "stable_surface_mask.tif")
    cov_mask    = os.path.join(WD, "coverage_mask.tif")
    water_mask  = os.path.join(WD, "osm_water_mask.tif")
    dtm_10m     = os.path.join(WD, "dtm_smrf_10m.tif")
    mask_2m     = os.path.join(WD, "mask_2m.tif")
    conn_10m    = os.path.join(WD, "conn_10m.tif")
    final_2m    = os.path.join(WD, "flood_final_2m.tif")
    prob_map    = os.path.join(WD, "flood_probability.tif")
    out_html    = os.path.join(WD, "spb_flood_v8.html")
    meta_json   = os.path.join(WD, "calibration_v8.json")

    # ── 0. ICESat-2 ────────────────────────────────────────────────────────
    print(f"\n[0/7] ICESat-2 ATL08 → Nuth & Kääb...")
    shift = {'dx': 0., 'dy': 0., 'dz': 0.,
             'rmse_before': np.nan, 'rmse_after': np.nan, 'n': 0}
    atl_files = download_atl08(date_end=STRIP_DATE)
    if atl_files:
        build_stable_mask(INPUT_DSM, CRS_METRIC, stable_mask)
        parts = []
        for f in atl_files:
            g = parse_atl08(f, CRS_METRIC)
            if not g.empty:
                parts.append(g)
                print(f"   {os.path.basename(f)}: {len(g)} точек")
        if parts:
            atl_gdf = gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), crs=CRS_METRIC)
            print(f"   ATL08 итого: {len(atl_gdf):,} точек")
            shift = nuth_kaab(INPUT_DSM, atl_gdf, stable_mask, CRS_METRIC)

    dsm_work = apply_shift(INPUT_DSM, shift, dsm_coreg)
    print(f"   Сдвиг: dx={shift['dx']:+.3f} dy={shift['dy']:+.3f} dz={shift['dz']:+.3f} м")
    gc.collect()

    # ── 1. Калибровка ──────────────────────────────────────────────────────
    print(f"\n[1/7] Вертикальная калибровка...")
    calib     = calibrate(dsm_work, CONTROL_POINTS, GAUGE_CSV, CRS_METRIC)
    offset    = calib['offset']
    threshold = FLOOD_LEVEL + offset
    wl_level  = offset
    gauge_wl  = get_gauge_levels(GAUGE_CSV, STRIP_DATE)
    print(f"\n   offset={offset:.4f} м | threshold={threshold:.4f} м")
    gc.collect()

    # ── 2. Маска покрытия ──────────────────────────────────────────────────
    print(f"\n[2/7] Маска покрытия...")
    create_coverage_mask(dsm_work, cov_mask, CRS_METRIC)
    gc.collect()

    # ── 3. Water + Bridges + SMRF ──────────────────────────────────────────
    print(f"\n[3/7] Гидрокоррекция: вода + мосты + SMRF...")

    # 3a. OSM water mask
    print("   [3a] OSM water mask...")
    build_osm_water_mask(dsm_work, water_mask, CRS_METRIC)
    gc.collect()

    # 3b. Water flattening (in-place на dsm_10m.tif)
    print("   [3b] Water flattening...")
    water_flattening(CRS_METRIC, offset, gauge_wl)
    gc.collect()

    # 3c. Де-бриджинг (in-place на dsm_10m.tif)
    print("   [3c] Де-бриджинг...")
    debridge_osm(CRS_METRIC)
    gc.collect()

    # 3d. SMRF: DSM → DTM
    print("   [3d] SMRF: DSM → DTM...")
    apply_smrf(dsm_work, dtm_10m, CRS_METRIC,
               slope_thr=SMRF_SLOPE, window_max_m=SMRF_WINDOW_MAX,
               cell_m=SMRF_CELL, cov_mask_path=cov_mask,
               csf_mult=calib.get('csf_mult', 1.0))
    gc.collect()

    # ── 4. Пороговая маска 2 м ─────────────────────────────────────────────
    print(f"\n[4/7] Пороговая маска 2 м (threshold={threshold:.3f} м)...")
    threshold_mask_tiled(dsm_work, mask_2m, threshold, wl_level,
                         water_mask_path=water_mask)
    gc.collect()

    # ── 5. Связность ───────────────────────────────────────────────────────
    print(f"\n[5/7] Связность + DTM-коррекция...")
    connectivity_analysis(mask_2m, conn_10m, WATER_SEEDS, CRS_METRIC,
                          CLOSING_M, CONN_RES, SEED_R,
                          cov_path=cov_mask, dtm_path=dtm_10m,
                          threshold=threshold)
    gc.collect()

    # ── 6. Финальная маска + вероятность ────────────────────────────────────
    print(f"\n[6/7] Финальная маска 2 м + карта вероятности...")
    apply_filter_2m(mask_2m, conn_10m, final_2m)

    # Вероятность (Монте-Карло по CI offset)
    build_probability_map(dsm_work, prob_map, calib, FLOOD_LEVEL, n_samples=50)

    # Очистка промежуточных
    for f in [mask_2m, conn_10m]:
        if os.path.exists(f): os.remove(f)
    gc.collect()

    # ── 7. Карта ───────────────────────────────────────────────────────────
    print(f"\n[7/7] Карта Folium...")
    vectorize_and_map(final_2m, calib, shift, CRS_METRIC, out_html)

    # Метаданные
    meta = {
        "version": "v8.1",
        "strip_date": STRIP_DATE,
        "flood_level_bs77": FLOOD_LEVEL,
        "calibration": {
            "offset": offset,
            "ci_lo": calib['ci_lo'], "ci_hi": calib['ci_hi'],
            "source": calib['note']
        },
        "coregistration": {
            "dx": shift['dx'], "dy": shift['dy'], "dz": shift['dz'],
            "rmse_before": shift['rmse_before'],
            "rmse_after": shift['rmse_after']
        },
        "dtm_method": "SMRF + de-bridging",
        "smrf": {"slope": SMRF_SLOPE, "window_max_m": SMRF_WINDOW_MAX},
        "threshold_dsm": threshold,
    }
    with open(meta_json, 'w', encoding='utf-8') as jf:
        json.dump(meta, jf, ensure_ascii=False, indent=2)

    print(f"\n{'='*70}")
    print(f" ГОТОВО! Flood Map v8.1")
    print(f" Карта:       {out_html}")
    print(f" Маска 2 м:   {final_2m}")
    print(f" Вероятность: {prob_map}")
    print(f" Метаданные:  {meta_json}")
    print(f"{'='*70}")

main()
