# ══════════════════════════════════════════════════════════════════════════════
# FLOOD MAP v8.1 — ПОЛНЫЙ НАБОР ПАТЧЕЙ
# ══════════════════════════════════════════════════════════════════════════════
# Применение:
#   Cell 2: замените EARTHDATA_USER/PASS → EARTHDATA_TOKEN (строка 1)
#           замените SMRF_WINDOW_MAX = 50 → 70 (строка 2)
#   Cell 3: замените download_atl08 (строки 10-34)
#   Cell 4: добавьте debridge_osm после water_flattening
#           замените threshold_mask_tiled (оптимизация water mask)
#           замените vectorize_and_map (сглаживание + fix RMSE)
#           замените apply_smrf строку distance_transform_edt
#   Cell 5: замените main() целиком
# ══════════════════════════════════════════════════════════════════════════════


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  CELL 2: КОНФИГУРАЦИЯ — две замены                                     ║
# ╚══════════════════════════════════════════════════════════════════════════╝

# ЗАМЕНА 1: вместо EARTHDATA_USER / EARTHDATA_PASS:
EARTHDATA_TOKEN = os.environ.get("EARTHDATA_TOKEN", "")

# ЗАМЕНА 2: вместо SMRF_WINDOW_MAX = 50:
SMRF_WINDOW_MAX = 70   # 70 м: покрывает блоки В.О. до 140 м (при 10 м)


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  CELL 3: download_atl08 — TOKEN авторизация                            ║
# ╚══════════════════════════════════════════════════════════════════════════╝
# Заменить строки 10–34 (функция download_atl08)

def download_atl08(lat_min=59.5, lat_max=60.4, lon_min=28.5, lon_max=31.5,
                   date_end=STRIP_DATE, out_dir=None):
    """Загружает ATL08 гранулы через earthaccess (EARTHDATA_TOKEN)."""
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
    earthaccess.login(strategy="environment")
    results = earthaccess.search_data(short_name="ATL08",
        bounding_box=(lon_min, lat_min, lon_max, lat_max),
        temporal=("2018-10-14", date_end), count=50)
    print(f"   Найдено гранул: {len(results)}")
    if not results: return []
    files = earthaccess.download(results, local_path=out_dir)
    print(f"   Скачано: {len(files)} файлов"); return files


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  CELL 4: apply_smrf — fix distance_transform_edt (строка ~207)         ║
# ╚══════════════════════════════════════════════════════════════════════════╝
# В apply_smrf, заменить строку:
#   idx = distance_transform_edt(nan_mask, return_indices=True)[1]
# на:
#   idx = distance_transform_edt(nan_mask, return_distances=False, return_indices=True)
# (экономит ~280 МБ RAM — не считает ненужные расстояния)


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  CELL 4: НОВАЯ ФУНКЦИЯ — debridge_osm (после water_flattening)         ║
# ╚══════════════════════════════════════════════════════════════════════════╝

def debridge_osm(crs, tile_sz=2048):
    """
    Де-бриджинг: прожигание мостов и эстакад в DSM (10 м) до уровня воды.
    
    Обрабатывает:
      - Мосты (bridge=yes): Благовещенский, Дворцовый, Троицкий и др.
      - Эстакады (man_made=bridge): ЗСД, развязки
      - Путепроводы (bridge=viaduct)
    
    Метод: для каждого пикселя моста → minimum_filter(size=7)
    берёт минимум из окрестности 70×70 м (уровень воды/набережной).
    """
    import osmnx as ox
    ox.settings.timeout = 120

    dsm10 = os.path.join(WD, "dsm_10m.tif")
    if not os.path.exists(dsm10):
        print("   ⚠ dsm_10m.tif не найден"); return

    with rasterio.open(dsm10) as src:
        rows, cols = src.shape; tf = src.transform; bounds = src.bounds

    b = gpd.GeoDataFrame(
        {'geometry': [box(bounds.left, bounds.bottom, bounds.right, bounds.top)]},
        crs=crs).to_crs("EPSG:4326").total_bounds
    bbox = (b[0], b[1], b[2], b[3])

    # Загружаем мосты + эстакады + путепроводы
    all_bridges = []
    for tags in [{'bridge': True}, {'man_made': 'bridge'}, {'bridge': 'viaduct'}]:
        try:
            feats = ox.features_from_bbox(bbox=bbox, tags=tags)
            if not feats.empty:
                feats = feats[feats.geometry.type.isin(
                    ['LineString', 'MultiLineString', 'Polygon', 'MultiPolygon']
                )].to_crs(crs)
                all_bridges.append(feats[['geometry']])
                print(f"     {tags}: {len(feats)}")
        except Exception as e:
            print(f"     {tags}: ⚠ {e}")

    if not all_bridges:
        print("   Де-бриджинг: нет данных OSM"); return

    bridges = gpd.GeoDataFrame(pd.concat(all_bridges, ignore_index=True), crs=crs)
    bridge_polys = bridges.geometry.buffer(25)  # 25 м = полоса моста + запас

    bridge_mask = rio_rasterize(
        [(g, 1) for g in bridge_polys],
        out_shape=(rows, cols), transform=tf, fill=0, dtype='uint8'
    )
    n_bridge_px = int(np.sum(bridge_mask))
    print(f"   Маска мостов/эстакад: {n_bridge_px:,} px ({len(bridges)} объектов)")

    # Прожигаем тайлами
    src_ds = gdal.Open(dsm10, gdal.GA_Update)
    band = src_ds.GetRasterBand(1); nd_val = band.GetNoDataValue()
    n_burned = 0

    nty = (rows + tile_sz - 1) // tile_sz
    for ty in range(nty):
        yo = ty * tile_sz; ys = min(tile_sz, rows - yo)
        for tx in range((cols + tile_sz - 1) // tile_sz):
            xo = tx * tile_sz; xs = min(tile_sz, cols - xo)
            bm = bridge_mask[yo:yo+ys, xo:xo+xs]
            if np.sum(bm) == 0: continue

            tile = band.ReadAsArray(xo, yo, xs, ys).astype(np.float32)
            if nd_val is not None: tile[tile == nd_val] = np.nan

            # Минимум в окне 7×7 (70 м) — уровень воды/набережной под мостом
            local_min = ndimage.minimum_filter(tile, size=7)
            tile[bm == 1] = local_min[bm == 1]

            if nd_val is not None: tile[np.isnan(tile)] = nd_val
            band.WriteArray(tile, xo, yo)
            n_burned += int(np.sum(bm))

    src_ds.FlushCache(); src_ds = None
    del bridge_mask; gc.collect()
    print(f"   Де-бриджинг: {n_burned:,} px прожжено (мосты + ЗСД + эстакады)")

    # Также создаём маску мостов для 2 м (для threshold_mask_tiled)
    bridge_mask_path = os.path.join(WD, "bridge_mask_10m.tif")
    with rasterio.open(dsm10) as src:
        prof = src.profile.copy()
    # Пересоздаём маску (bridge_mask уже удалена)
    bridge_mask2 = rio_rasterize(
        [(g, 1) for g in bridge_polys],
        out_shape=(rows, cols), transform=tf, fill=0, dtype='uint8'
    )
    prof.update(dtype='uint8', count=1, nodata=255)
    with rasterio.open(bridge_mask_path, 'w', **prof) as dst:
        dst.write(bridge_mask2, 1)
    del bridge_mask2; gc.collect()
    return bridge_mask_path


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  CELL 4: threshold_mask_tiled — добавлена маска мостов                  ║
# ╚══════════════════════════════════════════════════════════════════════════╝
# Заменить полностью

def threshold_mask_tiled(dsm_path, out_path, threshold, water_level,
                          water_mask_path=None, bridge_mask_path=None,
                          tile_sz=4096):
    """
    Тайловая пороговая маска с форсированием воды и мостов.
    Маски 10 м → апсэмплируются потайлово (не целиком → экономит RAM).
    """
    src = gdal.Open(dsm_path)
    cols, rows = src.RasterXSize, src.RasterYSize
    band = src.GetRasterBand(1); src_nd = band.GetNoDataValue()
    gt = src.GetGeoTransform()
    dst = gdal.GetDriverByName('GTiff').Create(
        out_path, cols, rows, 1, gdal.GDT_Byte,
        ['COMPRESS=LZW', 'TILED=YES', 'BLOCKXSIZE=256', 'BLOCKYSIZE=256', 'BIGTIFF=YES'])
    dst.SetGeoTransform(gt); dst.SetProjection(src.GetProjection())
    dst.GetRasterBand(1).SetNoDataValue(255)

    # Открываем 10 м маски (если есть)
    wm_ds = gdal.Open(water_mask_path) if water_mask_path and os.path.exists(water_mask_path) else None
    bm_ds = gdal.Open(bridge_mask_path) if bridge_mask_path and os.path.exists(bridge_mask_path) else None

    def upsample_mask_tile(mask_ds, xo, yo, xs, ys):
        """Читает фрагмент 10 м маски и апсэмплирует до 2 м через nearest."""
        if mask_ds is None: return None
        mgt = mask_ds.GetGeoTransform()
        # Координаты тайла в 10 м сетке
        x_world = gt[0] + xo * gt[1]
        y_world = gt[3] + yo * gt[5]
        mc = int((x_world - mgt[0]) / mgt[1])
        mr = int((y_world - mgt[3]) / mgt[5])
        mx = int(np.ceil(xs * abs(gt[1]) / abs(mgt[1]))) + 2
        my = int(np.ceil(ys * abs(gt[5]) / abs(mgt[5]))) + 2
        mc = max(0, mc); mr = max(0, mr)
        mx = min(mx, mask_ds.RasterXSize - mc)
        my = min(my, mask_ds.RasterYSize - mr)
        if mx <= 0 or my <= 0: return np.zeros((ys, xs), dtype=np.uint8)
        chunk = mask_ds.GetRasterBand(1).ReadAsArray(mc, mr, mx, my)
        # Nearest upsample
        from scipy.ndimage import zoom as ndz
        zy = ys / max(my, 1); zx = xs / max(mx, 1)
        up = ndz(chunk, (zy, zx), order=0).astype(np.uint8)
        return up[:ys, :xs]

    n_water = 0; n_bridge = 0
    nty = (rows + tile_sz - 1) // tile_sz
    for ty in range(nty):
        yo = ty * tile_sz; ys = min(tile_sz, rows - yo)
        for tx in range((cols + tile_sz - 1) // tile_sz):
            xo = tx * tile_sz; xs = min(tile_sz, cols - xo)
            tile = band.ReadAsArray(xo, yo, xs, ys).astype(np.float32)
            nd_m = np.isnan(tile)
            if src_nd is not None: nd_m |= (tile == src_nd)
            tile[nd_m] = water_level
            mask = (tile <= threshold).astype(np.uint8)

            # Вода
            wt = upsample_mask_tile(wm_ds, xo, yo, xs, ys)
            if wt is not None and np.any(wt):
                mask = np.maximum(mask, wt)
                n_water += int(np.sum(wt & ~mask.astype(bool)))

            # Мосты
            bt = upsample_mask_tile(bm_ds, xo, yo, xs, ys)
            if bt is not None and np.any(bt):
                mask = np.maximum(mask, bt)
                n_bridge += int(np.sum(bt))

            dst.GetRasterBand(1).WriteArray(mask, xo, yo)
        if (ty+1) % max(1, nty//5) == 0 or ty == nty-1:
            print(f"     {100*(ty+1)/nty:.0f}%")
    dst.FlushCache(); dst = None; src = None
    wm_ds = None; bm_ds = None
    if n_water: print(f"   OSM water: форсировано на 2 м")
    if n_bridge: print(f"   Мосты/ЗСД: форсировано на 2 м")


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  CELL 4: vectorize_and_map — сглаживание + fix RMSE                    ║
# ╚══════════════════════════════════════════════════════════════════════════╝
# Заменить полностью

def vectorize_and_map(final_mask, calib, shift, crs, out_html):
    """Векторизация + сглаживание + Folium карта."""
    with rasterio.open(final_mask) as src:
        arr = src.read(1); tf = src.transform; bounds = src.bounds
    print(f"   Затопленных: {int(np.sum(arr)):,} px")
    if np.sum(arr) == 0: print("   Нет затопления."); return

    geoms = [{'properties': {'v': int(v)}, 'geometry': s}
             for s, v in shapes(arr, mask=arr == 1, transform=tf)]
    del arr; gc.collect()
    if not geoms: return

    gdf = gpd.GeoDataFrame.from_features(geoms, crs=crs); del geoms; gc.collect()
    gdf = gdf[gdf.geometry.area >= MIN_AREA].dissolve()

    # ═══ Сглаживание: buffer(+r).buffer(-r) убирает угловатость 10 м сетки
    smooth_r = 15  # м
    gdf.geometry = gdf.geometry.buffer(smooth_r).buffer(-smooth_r)
    gdf.geometry = gdf.geometry.simplify(SIMPLIFY)
    gdf = gdf[gdf.geometry.area >= MIN_AREA]

    ha = gdf.geometry.area.sum() / 10000
    print(f"   Площадь: {ha:.0f} га (сглаживание: buffer ±{smooth_r} м)")

    bw = gpd.GeoDataFrame({'geometry': [box(bounds.left, bounds.bottom, bounds.right, bounds.top)]},
                          crs=crs).to_crs("EPSG:4326").total_bounds
    cy, cx = (bw[1]+bw[3])/2, (bw[0]+bw[2])/2
    m = folium.Map(location=[cy, cx], zoom_start=11, tiles='CartoDB positron')
    fw = gdf.to_crs("EPSG:4326")
    folium.GeoJson(fw, name='Зона затопления',
        style_function=lambda x: {'fillColor': '#1E90FF', 'color': '#0000CD',
                                   'weight': 0.5, 'fillOpacity': 0.55}).add_to(m)

    # ═══ Info-панель (RMSE fix: before→after)
    rmse_b = shift.get('rmse_before', float('nan'))
    rmse_a = shift.get('rmse_after', float('nan'))
    info = f"""<div style="position:fixed;bottom:30px;left:30px;z-index:1000;
    background:white;padding:12px;border-radius:8px;box-shadow:2px 2px 8px rgba(0,0,0,.3);
    font-size:12px;line-height:1.6em">
    <b>Flood Map v8.1</b><br>
    Уровень: {FLOOD_LEVEL} м БС-77<br>
    Дата стрипов: {STRIP_DATE}<br>
    Offset: {calib['offset']:.3f} м [{calib['note']}]<br>
    CI 95%: [{calib['ci_lo']:.3f}, {calib['ci_hi']:.3f}] м<br>
    Coreg dz: {shift['dz']:+.3f} м (RMSE {rmse_b:.3f}→{rmse_a:.3f})<br>
    DTM: SMRF (slope={SMRF_SLOPE}, w={SMRF_WINDOW_MAX} м) + de-bridge<br>
    Площадь: <b>{ha:.0f} га</b>
    </div>"""
    m.get_root().html.add_child(folium.Element(info))

    try:
        import osmnx as ox; ox.settings.timeout = 60
        fb = fw.total_bounds
        lu = ox.features_from_bbox(bbox=(fb[0], fb[1], fb[2], fb[3]), tags={'landuse': True})
        if not lu.empty:
            lu = lu[lu.geometry.type.isin(['Polygon', 'MultiPolygon'])].to_crs(crs)
            imp = gpd.overlay(lu, gdf, how='intersection')
            imp['ha'] = imp.geometry.area / 10000
            print("\n   Landuse в зоне затопления:")
            print(imp.groupby('landuse')['ha'].sum().sort_values(ascending=False).head(10).to_string())
    except Exception as e:
        print(f"   ⚠ landuse: {e}")
    folium.LayerControl().add_to(m)
    m.save(out_html); print(f"   Карта: {out_html}")
    return m


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  CELL 5: MAIN — полная замена (без дублирования, с де-бриджингом)       ║
# ╚══════════════════════════════════════════════════════════════════════════╝

def main():
    print("=" * 70)
    print(" FLOOD MAP v8.1 — SMRF + ICESat-2 + де-бриджинг + сглаживание")
    print(f" Уровень: {FLOOD_LEVEL} м БС-77  |  Дата: {STRIP_DATE}")
    print(f" EGM2008(60N,30E)={EGM_A0:.3f} м | δ_BS77={DELTA_BS77:+.2f} м")
    print(f" SMRF: slope={SMRF_SLOPE}, window={SMRF_WINDOW_MAX} м")
    print("=" * 70)

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

    # ── 0. ICESat-2 ────────────────────────────────────────────────
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
            atl_gdf = gpd.GeoDataFrame(
                pd.concat(parts, ignore_index=True), crs=CRS_METRIC)
            print(f"   ATL08 итого: {len(atl_gdf):,} точек")
            shift = nuth_kaab(INPUT_DSM, atl_gdf, stable_mask, CRS_METRIC)
    dsm_work = apply_shift(INPUT_DSM, shift, dsm_coreg)
    print(f"   Сдвиг: dx={shift['dx']:+.3f} dy={shift['dy']:+.3f} dz={shift['dz']:+.3f} м")
    gc.collect()

    # ── 1. Калибровка ──────────────────────────────────────────────
    print(f"\n[1/7] Вертикальная калибровка...")
    calib = calibrate(dsm_work, CONTROL_POINTS, GAUGE_CSV, CRS_METRIC)
    offset = calib['offset']
    threshold = FLOOD_LEVEL + offset
    wl_level = offset
    gauge_wl = get_gauge_levels(GAUGE_CSV, STRIP_DATE)
    print(f"\n   offset={offset:.4f} м | threshold={threshold:.4f} м")
    gc.collect()

    # ── 2. Маска покрытия ──────────────────────────────────────────
    print(f"\n[2/7] Маска покрытия...")
    create_coverage_mask(dsm_work, cov_mask, CRS_METRIC)
    gc.collect()

    # ── 3. Гидрокоррекция ──────────────────────────────────────────
    print(f"\n[3/7] Гидрокоррекция: вода + мосты + SMRF...")

    print("   [3a] OSM water mask...")
    build_osm_water_mask(dsm_work, water_mask, CRS_METRIC)
    gc.collect()

    print("   [3b] Water flattening...")
    water_flattening(CRS_METRIC, offset, gauge_wl)
    gc.collect()

    print("   [3c] Де-бриджинг (мосты + ЗСД + эстакады)...")
    bridge_mask_file = debridge_osm(CRS_METRIC)  # returns path or None
    gc.collect()

    print("   [3d] SMRF: DSM → DTM...")
    apply_smrf(dsm_work, dtm_10m, CRS_METRIC,
               slope_thr=SMRF_SLOPE, window_max_m=SMRF_WINDOW_MAX,
               cell_m=SMRF_CELL, cov_mask_path=cov_mask,
               csf_mult=calib.get('csf_mult', 1.0))
    gc.collect()

    # ── 4. Пороговая маска 2 м ─────────────────────────────────────
    print(f"\n[4/7] Пороговая маска 2 м (threshold={threshold:.3f} м)...")
    threshold_mask_tiled(dsm_work, mask_2m, threshold, wl_level,
                         water_mask_path=water_mask,
                         bridge_mask_path=bridge_mask_file)
    gc.collect()

    # ── 5. Связность ───────────────────────────────────────────────
    print(f"\n[5/7] Связность + DTM-коррекция...")
    connectivity_analysis(mask_2m, conn_10m, WATER_SEEDS, CRS_METRIC,
                          CLOSING_M, CONN_RES, SEED_R,
                          cov_path=cov_mask, dtm_path=dtm_10m,
                          threshold=threshold)
    gc.collect()

    # ── 6. Финальная маска + вероятность ────────────────────────────
    print(f"\n[6/7] Финальная маска + вероятность...")
    apply_filter_2m(mask_2m, conn_10m, final_2m)
    build_probability_map(dsm_work, prob_map, calib, FLOOD_LEVEL, n_samples=50)
    for f in [mask_2m, conn_10m]:
        if os.path.exists(f): os.remove(f)
    gc.collect()

    # ── 7. Карта ───────────────────────────────────────────────────
    print(f"\n[7/7] Карта...")
    vectorize_and_map(final_2m, calib, shift, CRS_METRIC, out_html)

    # Метаданные
    meta = {
        "version": "v8.1",
        "strip_date": STRIP_DATE,
        "flood_level_bs77": FLOOD_LEVEL,
        "calibration": {"offset": offset, "ci_lo": calib['ci_lo'],
                        "ci_hi": calib['ci_hi'], "source": calib['note']},
        "coregistration": {"dx": shift['dx'], "dy": shift['dy'], "dz": shift['dz'],
                           "rmse_before": shift['rmse_before'],
                           "rmse_after": shift['rmse_after']},
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
