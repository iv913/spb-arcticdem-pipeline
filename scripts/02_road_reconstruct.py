#!/usr/bin/env python3
"""
============================================================================
 02_road_reconstruct.py — робастная реконструкция поверхности проезжей части
============================================================================

Вход:  outputs/owner_map.tif (модуль 01), DEM, ось/полигон из OSM-кэша,
       Отметки.txt.
Идея:  по пикселям владельца road строится модель
           h(pk, off) = f(пикетаж) + g(offset_signed),
       где f — сглаженный продольный профиль (асимметрично-робастный:
       выбросы ВВЕРХ — машины/навесы — давятся сильнее, чем вниз),
       g — непараметрический поперечный профиль по знаковому смещению
       (левая/правая проезжие части дуали моделируются одной кривой,
       т.к. offset знаковый относительно оси бульвара).
Выход: outputs/road_surface.tif
         band1: h_bs77 (см. привязку ниже)
         band2: source: 1=measured(inlier), 2=reconstructed(замена outlier/
                gap/mixed), 3=extrapolated(низкая достоверность)
       outputs/road_profile.png, road_residuals.png
       outputs/road_points_report.csv  (кал/holdout, dH после привязки)
       outputs/road_qa.json
       outputs/road_qa_map.html (поверхность на спутнике, повёрнутая сетка
                                 3413 варпится как в модуле 01)

Вертикальная привязка: эмпирический сдвиг = медиана (h_model − h_паспорт)
по calibration-набору точек с owner=road (включает геоид и bias DEM);
holdout не участвует в сдвиге и даёт честный RMSE.
"""
import json
import sys
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd
import geopandas as gpd
import rasterio
from rasterio.windows import from_bounds
from rasterio.transform import array_bounds
from rasterio.warp import transform as warp_transform
import shapely
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

# переиспользуем конфиг и функции модуля 01 (единый источник геометрии);
# имя файла начинается с цифры — обычный import невозможен, грузим importlib
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location(
    'owner_map_mod', Path(__file__).parent / '01_owner_map.py')
m01 = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(m01)

OUT = m01.OUT
UTM = m01.UTM
DEM_PATH = m01.DEM_PATH
OWNER = m01.OWNER_CODES

# --- параметры модели ---
PK_BIN = 5.0            # м, бин продольного профиля
OFF_BIN = 1.0           # м, бин поперечного профиля
MIN_PX_PK_BIN = 8       # мин. пикселей в продольном бине
MIN_PX_OFF_BIN = 30     # мин. пикселей в поперечном бине
UP_REJECT = 0.40        # м: срез вверх от нижней опоры бина (машины ~1.5м)
LOW_ANCHOR_PCT = 20     # нижняя опора бина = этот перцентиль
SMOOTH_HALF_BINS = 2    # полуокно сглаживания профиля (в бинах)
CROSS_CLIP = 0.40       # м, физический предел поперечной поправки
INLIER_TOL = 0.25       # м, |резидуал| для класса measured
MAX_GAP_BINS = 4        # >этого подряд пустых бинов → extrapolated
CAL_DIST_MAX = 5.0      # м, точка годна для привязки, если ближе к дороге
HOLDOUT_PCT = 25
RANDOM_STATE = 42
GEOID_FALLBACK = m01.GEOID_FALLBACK

# --- прореживание бинов профиля (стереотени зданий, мосты) ---
# Тень высокого здания в стерео-DSM накрывает дорогу ПО ВСЕЙ ширине:
# в таком бине нет чистых пикселей, и P20+UP_REJECT сам сидит на тени.
# Лечится на уровне БИНОВ: бин выбрасывается, если он (а) торчит вверх
# над скользящей медианой профиля, либо (б) создаёт физически невозможный
# продольный уклон. Модель интерполирует через разрыв С ДВУХ СТОРОН —
# от уцелевших участков/перекрёстков ("двухстороннее движение").
MAX_SHADOW_JUMP = 1.0   # м вверх над локальной базой профиля → тень
BASELINE_WIN_BINS = 15  # окно скользящей медианы базы (15×5м = 75м)
MAX_LONG_SLOPE = 0.05   # м/м, физический предел продольного уклона улицы


def log(msg):
    print(f'[{datetime.now():%H:%M:%S}] {msg}', flush=True)


# ============================================================
# Загрузка растров на ОДНОЙ сетке
# ============================================================
def load_grids():
    owner_path = OUT / 'owner_map.tif'
    if not owner_path.exists():
        log(f'ERROR: нет {owner_path} — сначала модуль 01'); sys.exit(1)
    with rasterio.open(str(owner_path)) as o:
        owner = o.read(1)
        quality = o.read(2)
        transform, crs = o.transform, o.crs
        H, W = owner.shape
    with rasterio.open(str(DEM_PATH)) as src:
        assert src.crs == crs, 'CRS owner_map != CRS DEM'
        win = from_bounds(*array_bounds(H, W, transform), src.transform)
        win = win.round_offsets().round_lengths()
        dem = src.read(1, window=win).astype('float32')
        if src.nodata is not None:
            dem[dem == src.nodata] = np.nan
        t2 = rasterio.windows.transform(win, src.transform)
    # сетки обязаны совпасть попиксельно (owner строился на сетке DEM)
    assert dem.shape == owner.shape, f'{dem.shape} != {owner.shape}'
    assert np.allclose([t2.a, t2.b, t2.c, t2.d, t2.e, t2.f],
                       [transform.a, transform.b, transform.c,
                        transform.d, transform.e, transform.f], atol=1e-6), \
        'сетка DEM-окна не совпала с owner_map'
    log(f'Сетка: {W}×{H}, road px: {(owner == OWNER["road"]).sum():,} '
        f'(clean: {((owner == OWNER["road"]) & (quality == 0)).sum():,})')
    return owner, quality, dem, transform, crs


# ============================================================
# Пикетаж и знаковое смещение (в UTM, векторизовано)
# ============================================================
def picket_offset(xs_dem, ys_dem, dem_crs, axis_utm):
    xu, yu = warp_transform(dem_crs, UTM, list(xs_dem), list(ys_dem))
    pts = shapely.points(np.asarray(xu), np.asarray(yu))
    pk = shapely.line_locate_point(axis_utm, pts)
    foot = shapely.line_interpolate_point(axis_utm, pk)
    L = axis_utm.length
    ahead = shapely.line_interpolate_point(axis_utm, np.minimum(pk + 1.0, L))
    back = shapely.line_interpolate_point(axis_utm, np.maximum(pk - 1.0, 0.0))
    dx = shapely.get_x(ahead) - shapely.get_x(back)
    dy = shapely.get_y(ahead) - shapely.get_y(back)
    px = np.asarray(xu) - shapely.get_x(foot)
    py = np.asarray(yu) - shapely.get_y(foot)
    sign = np.sign(dx * py - dy * px)
    sign[sign == 0] = 1.0
    off = shapely.distance(pts, foot) * sign
    return np.asarray(pk), np.asarray(off)


# ============================================================
# Асимметрично-робастный продольный профиль
# ============================================================
def _prune_bins(pk_arr, h_arr, max_jump=MAX_SHADOW_JUMP,
                max_slope=MAX_LONG_SLOPE, base_win=BASELINE_WIN_BINS):
    """Итеративное прореживание бинов профиля.
    (а) shadow_jump: бин выше скользящей медианы соседей на > max_jump
        (стереотень всегда ВВЕРХ — проверка асимметричная);
    (б) slope: пара соседних бинов с |уклоном| > max_slope — выбрасывается
        ВЕРХНИЙ из пары (нижний, скорее всего, асфальт).
    Возвращает (keep_mask, reason_array)."""
    n = len(h_arr)
    keep = np.ones(n, dtype=bool)
    reason = np.array([''] * n, dtype=object)
    for _ in range(30):
        idx = np.where(keep)[0]
        if len(idx) < 4:
            break
        hv, pkv = h_arr[idx], pk_arr[idx]
        base = pd.Series(hv).rolling(base_win, center=True,
                                     min_periods=3).median().values
        bad_jump = (hv - base) > max_jump
        bad_slope = np.zeros(len(idx), dtype=bool)
        s = np.diff(hv) / np.maximum(np.diff(pkv), 1e-6)
        for i in np.where(np.abs(s) > max_slope)[0]:
            j = i if hv[i] > hv[i + 1] else i + 1
            bad_slope[j] = True
        bad = bad_jump | bad_slope
        if not bad.any():
            break
        keep[idx[bad]] = False
        reason[idx[bad_jump]] = 'shadow_jump'
        reason[idx[bad_slope & ~bad_jump]] = 'slope'
    return keep, reason


def fit_longitudinal(pk, h):
    """Бин 5м: нижняя опора (P20) → срез вверх (+UP_REJECT) → медиана
    оставшихся → прореживание бинов (стереотени/мосты) → сглаживание
    скользящей медианой по УЦЕЛЕВШИМ бинам; разрывы интерполируются
    линейно между уцелевшими участками (заякорено с двух сторон).
    Возвращает (f(pk), is_extrap(pk), таблицу профиля со столбцами
    kept/reject_reason)."""
    b = np.floor(pk / PK_BIN).astype(int)
    df = pd.DataFrame({'b': b, 'pk': pk, 'h': h})
    rows = []
    for bi, g in df.groupby('b'):
        if len(g) < MIN_PX_PK_BIN:
            continue
        anchor = np.percentile(g['h'], LOW_ANCHOR_PCT)
        kept = g['h'][g['h'] <= anchor + UP_REJECT]
        if len(kept) < max(3, MIN_PX_PK_BIN // 2):
            continue
        rows.append({'b': bi, 'pk': (bi + 0.5) * PK_BIN,
                     'h': float(np.median(kept)), 'n': int(len(kept)),
                     'n_raw': int(len(g)),
                     'spread': float(kept.std(ddof=0))})
    prof = pd.DataFrame(rows).sort_values('b').reset_index(drop=True)
    if len(prof) < 4:
        raise RuntimeError('слишком мало валидных бинов профиля')

    # --- прореживание: тени зданий и невозможные уклоны ---
    keep, reason = _prune_bins(prof['pk'].values, prof['h'].values)
    prof['kept'] = keep
    prof['reject_reason'] = reason
    n_rej = int((~keep).sum())
    if n_rej:
        rej = prof[~prof['kept']]
        log(f'  Прорежено бинов: {n_rej}/{len(prof)} '
            f'(shadow_jump={int((rej["reject_reason"] == "shadow_jump").sum())}, '
            f'slope={int((rej["reject_reason"] == "slope").sum())}); '
            f'пикеты: {", ".join(f"{p:.0f}" for p in rej["pk"].head(20))}'
            f'{"..." if n_rej > 20 else ""}')
    kept_prof = prof[prof['kept']].reset_index(drop=True)
    if len(kept_prof) < 4:
        raise RuntimeError('после прореживания осталось <4 бинов')

    # сглаживание: скользящая медиана ±SMOOTH_HALF_BINS по уцелевшим
    hs = kept_prof['h'].values.copy()
    sm = hs.copy()
    for i in range(len(hs)):
        lo, hi = max(0, i - SMOOTH_HALF_BINS), \
            min(len(hs), i + SMOOTH_HALF_BINS + 1)
        sm[i] = np.median(hs[lo:hi])
    kept_prof = kept_prof.copy()
    kept_prof['h_smooth'] = sm
    prof = prof.merge(kept_prof[['b', 'h_smooth']], on='b', how='left')

    # разрывы: пустые И прореженные бины считаются отсутствующими;
    # интерполяция всё равно заполняет их (мост между двумя берегами),
    # но длинные разрывы получают метку extrapolated (низкое доверие)
    full = np.arange(prof['b'].min(), prof['b'].max() + 1)
    have = np.isin(full, kept_prof['b'].values)
    gap_len = {}
    i = 0
    while i < len(full):
        if not have[i]:
            j = i
            while j < len(full) and not have[j]:
                j += 1
            for k in range(i, j):
                gap_len[full[k]] = j - i
            i = j
        else:
            i += 1

    pk_grid = kept_prof['pk'].values
    h_grid = kept_prof['h_smooth'].values

    def f_long(q):
        q = np.asarray(q, dtype=float)
        return np.interp(q, pk_grid, h_grid)  # клип на края автоматически

    def is_extrap(q):
        q = np.asarray(q, dtype=float)
        bq = np.floor(q / PK_BIN).astype(int)
        out = (q < pk_grid.min()) | (q > pk_grid.max())
        big_gap = np.array([gap_len.get(x, 0) > MAX_GAP_BINS for x in bq])
        return out | big_gap

    return f_long, is_extrap, prof


# ============================================================
# Непараметрический поперечный профиль (по остаткам)
# ============================================================
def fit_cross(off, resid):
    b = np.floor(off / OFF_BIN).astype(int)
    df = pd.DataFrame({'b': b, 'r': resid})
    med = df.groupby('b')['r'].agg(['median', 'count']).reset_index()
    med = med[med['count'] >= MIN_PX_OFF_BIN].sort_values('b')
    if len(med) < 3:
        log('  Поперечный профиль: мало данных, g=0')
        return (lambda o: np.zeros_like(np.asarray(o, float))), med
    ob = med['b'].values * OFF_BIN + OFF_BIN / 2
    rv = np.clip(med['median'].values, -CROSS_CLIP, CROSS_CLIP)
    # лёгкое сглаживание тройной медианой
    if len(rv) >= 3:
        rv = np.array([np.median(rv[max(0, i - 1):i + 2])
                       for i in range(len(rv))])

    def g_cross(o):
        o = np.asarray(o, dtype=float)
        out = np.interp(o, ob, rv, left=0.0, right=0.0)
        return np.clip(out, -CROSS_CLIP, CROSS_CLIP)
    return g_cross, med


# ============================================================
# MAIN
# ============================================================
def main():
    log('=' * 60)
    log('02_road_reconstruct: h = f(picket) + g(offset)')
    log('=' * 60)

    owner, quality, dem, transform, dem_crs = load_grids()

    # геометрия из того же источника, что и модуль 01 (кэш OSM)
    osm = m01.fetch_osm(m01.MVP_BBOX)
    carriageway_utm, axis_utm, _, _ = m01.build_object_geometry(
        osm, m01.MVP_BBOX, m01.MVP_NAME)

    # --- обучающие пиксели: road & clean & конечная высота ---
    train = (owner == OWNER['road']) & (quality == 0) & np.isfinite(dem)
    rows, cols = np.where(train)
    xs = transform.c + transform.a * (cols + 0.5) + transform.b * (rows + 0.5)
    ys = transform.f + transform.d * (cols + 0.5) + transform.e * (rows + 0.5)
    log(f'Обучающих пикселей: {len(rows):,}')
    pk, off = picket_offset(xs, ys, dem_crs, axis_utm)
    h = dem[rows, cols].astype(float)

    # --- итерация 1: продольный профиль ---
    f_long, is_extrap, prof = fit_longitudinal(pk, h)
    resid1 = h - f_long(pk)

    # --- поперечный профиль по остаткам инлайеров 1-го прохода;
    #     пиксели прореженных (теневых) бинов исключаются: их остатки
    #     огромны и не описывают поперечный скат ---
    rej_bins = np.array(sorted(prof.loc[~prof['kept'], 'b']), dtype=int)
    px_bin = np.floor(pk / PK_BIN).astype(int)
    not_shadow = ~np.isin(px_bin, rej_bins)
    keep1 = not_shadow & (np.abs(resid1) <=
                          3 * max(0.15, np.median(np.abs(resid1[not_shadow]))
                                  * 1.4826))
    g_cross, cross_tab = fit_cross(off[keep1], resid1[keep1])

    # --- итерация 2: переучиваем продольный на h - g(off) ---
    f_long, is_extrap, prof = fit_longitudinal(pk, h - g_cross(off))
    model_train = f_long(pk) + g_cross(off)
    resid = h - model_train
    mad = np.median(np.abs(resid - np.median(resid))) * 1.4826
    inlier = np.abs(resid) <= max(INLIER_TOL, 3 * mad)
    log(f'Итог обучения: MAD={mad:.3f}м, inliers={inlier.mean()*100:.1f}% '
        f'({inlier.sum():,}/{len(inlier):,})')

    # --- поверхность на всех пикселях владельца road (вкл. mixed) ---
    surf_mask = (owner == OWNER['road'])
    r2, c2 = np.where(surf_mask)
    xs2 = transform.c + transform.a * (c2 + 0.5) + transform.b * (r2 + 0.5)
    ys2 = transform.f + transform.d * (c2 + 0.5) + transform.e * (r2 + 0.5)
    pk2, off2 = picket_offset(xs2, ys2, dem_crs, axis_utm)
    h_model = f_long(pk2) + g_cross(off2)

    surface = np.full(dem.shape, np.nan, dtype='float32')
    source = np.zeros(dem.shape, dtype='uint8')
    surface[r2, c2] = h_model
    source[r2, c2] = 2  # reconstructed по умолчанию

    # measured: clean-inlier пиксели сохраняют ИЗМЕРЕННУЮ высоту
    tr_idx = np.where(train)
    meas_rows = tr_idx[0][inlier]
    meas_cols = tr_idx[1][inlier]
    surface[meas_rows, meas_cols] = dem[meas_rows, meas_cols]
    source[meas_rows, meas_cols] = 1

    ext = is_extrap(pk2)
    source[r2[ext], c2[ext]] = 3
    n_meas = int((source == 1).sum())
    n_rec = int((source == 2).sum())
    n_ext = int((source == 3).sum())
    log(f'Пиксели поверхности: measured={n_meas:,}, reconstructed={n_rec:,}, '
        f'extrapolated={n_ext:,}')

    # --- вертикальная привязка по опорным точкам ---
    cp = m01.load_control_points()
    shift = GEOID_FALLBACK
    val = {'n_cal': 0, 'n_holdout': 0, 'rmse': None, 'mae': None,
           'shift_source': 'GEOID_FALLBACK'}
    report = None
    if not cp.empty:
        pts_utm = gpd.GeoDataFrame(
            cp, geometry=gpd.points_from_xy(cp['lon'], cp['lat']),
            crs='EPSG:4326').to_crs(UTM)
        cp = cp.copy()
        cp['dist'] = pts_utm.geometry.distance(carriageway_utm).values
        near = cp[cp['dist'] <= CAL_DIST_MAX].copy()
        log(f'Точек в {CAL_DIST_MAX}м от проезжей части: {len(near)}')
        if len(near) >= 4:
            xs3, ys3 = warp_transform('EPSG:4326', dem_crs,
                                      near['lon'].tolist(),
                                      near['lat'].tolist())
            pk3, off3 = picket_offset(xs3, ys3, dem_crs, axis_utm)
            near['h_model_ell'] = f_long(pk3) + g_cross(off3)
            near['extrap'] = is_extrap(pk3)
            near = near[~near['extrap']].copy()
            near['dH_ell'] = near['h_model_ell'] - near['h_pas']
            # робастный сдвиг по cal, честный RMSE по holdout
            rng = np.random.RandomState(RANDOM_STATE)
            near['holdout'] = False
            n_ho = max(1, int(len(near) * HOLDOUT_PCT / 100))
            near.loc[rng.choice(near.index, n_ho, replace=False),
                     'holdout'] = True
            cal = near[~near['holdout']]
            med = cal['dH_ell'].median()
            madp = np.median(np.abs(cal['dH_ell'] - med)) * 1.4826
            ok = cal[np.abs(cal['dH_ell'] - med) <= 3 * max(0.1, madp)]
            shift = float(ok['dH_ell'].median())
            ho = near[near['holdout']]
            near['dH_bs77'] = near['dH_ell'] - shift
            val = {'n_cal': int(len(ok)), 'n_holdout': int(len(ho)),
                   'shift_m': shift, 'shift_source': 'control_points',
                   'rmse': float(np.sqrt(np.mean(
                       (ho['dH_ell'] - shift) ** 2))) if len(ho) else None,
                   'mae': float(np.mean(
                       np.abs(ho['dH_ell'] - shift))) if len(ho) else None}
            log(f'Привязка: shift={shift:.2f}м (cal n={len(ok)}); '
                f'holdout n={len(ho)}: RMSE={val["rmse"]}, MAE={val["mae"]}')
            report = near.drop(columns='geometry', errors='ignore')
            report.to_csv(OUT / 'road_points_report.csv', index=False)
        else:
            log('⚠ Точек мало — сдвиг по умолчанию (GEOID_FALLBACK)')
    surface_bs = surface - shift

    # --- запись GeoTIFF ---
    out_path = OUT / 'road_surface.tif'
    prof_r = {'driver': 'GTiff', 'dtype': 'float32', 'count': 2,
              'crs': dem_crs, 'transform': transform,
              'width': dem.shape[1], 'height': dem.shape[0],
              'compress': 'deflate', 'nodata': -9999}
    with rasterio.open(str(out_path), 'w', **prof_r) as dst:
        b1 = surface_bs.copy()
        b1[~np.isfinite(b1)] = -9999
        dst.write(b1, 1)
        dst.write(source.astype('float32'), 2)
        dst.set_band_description(1, 'h BS-77 (shift by control points)')
        dst.set_band_description(2, '1 measured, 2 reconstructed, 3 extrapolated')
    log(f'✓ {out_path}')

    # --- графики ---
    fig, axes = plt.subplots(3, 1, figsize=(14, 11), constrained_layout=True)
    sub = np.random.RandomState(0).choice(len(pk), min(60000, len(pk)),
                                          replace=False)
    axes[0].scatter(pk[sub], h[sub] - shift, s=1, alpha=0.15, c='steelblue',
                    label='road clean px')
    kp = prof[prof['kept']]
    rj = prof[~prof['kept']]
    axes[0].plot(kp['pk'], kp['h_smooth'] - shift, 'r-', lw=2,
                 label='f(picket)')
    if len(rj):
        axes[0].scatter(rj['pk'], rj['h'] - shift, marker='x', s=45,
                        c='black', zorder=5,
                        label=f'прорежено бинов: {len(rj)} '
                              '(стереотени/уклон)')
    axes[0].set_xlabel('пикетаж, м'); axes[0].set_ylabel('м БС-77')
    axes[0].legend(); axes[0].grid(alpha=.3)
    axes[0].set_title(f'{m01.MVP_NAME}: продольный профиль')
    axes[1].scatter(off[sub], resid[sub], s=1, alpha=0.15, c='steelblue')
    oo = np.linspace(off.min(), off.max(), 300)
    axes[1].plot(oo, g_cross(oo), 'r-', lw=2, label='g(offset)')
    axes[1].axvspan(-5, 5, color='green', alpha=0.08, label='зона бульвара')
    axes[1].set_xlabel('знаковое смещение от оси, м')
    axes[1].set_ylabel('остаток, м'); axes[1].legend(); axes[1].grid(alpha=.3)
    axes[1].set_title('поперечный профиль (остатки)')
    axes[2].hist(resid, bins=120, range=(-1.5, 1.5), color='steelblue')
    axes[2].axvline(0, color='r')
    axes[2].set_xlabel('резидуал h − model, м')
    axes[2].set_title(f'резидуалы (MAD={mad:.03f} м, inliers='
                      f'{inlier.mean()*100:.1f}%)')
    plt.savefig(OUT / 'road_profile.png', dpi=140)
    plt.close()
    log(f'✓ {OUT / "road_profile.png"}')

    # --- QA-карта: 3 растровых слоя + спутник + точки ---
    # слои: (1) Поверхность БС-77 (модуль 02), (2) Owner map (модуль 01),
    #        (3) Сырой DEM в полосе 100м от проезжей части (для QA теней)
    import io, base64
    from PIL import Image
    import matplotlib.colors as mcolors
    from matplotlib import colormaps
    import folium
    from rasterio.features import rasterize as _rasterize

    # общий bbox в 4326 для всех слоёв (одна дисплей-сетка 3857 — общая)
    # сначала поверхность как раньше, она задаёт bounds
    v = surface_bs[np.isfinite(surface_bs)]
    lo_s, hi_s = np.percentile(v, 2), np.percentile(v, 98)
    code = np.full(surface_bs.shape, 255, dtype='uint8')
    fin = np.isfinite(surface_bs)
    code[fin] = np.clip((surface_bs[fin] - lo_s) / max(1e-6, hi_s - lo_s)
                        * 254, 0, 254).astype('uint8')
    code_d, src_d, (w4, s4, e4, n4) = m01._reproject_categorical_for_display(
        code, source, transform, dem_crs)

    rgba_surf = np.zeros((*code_d.shape, 4), dtype='uint8')
    cmap_turbo = colormaps['turbo']
    mval = code_d != 255
    rgba_surf[mval] = (cmap_turbo(code_d[mval] / 254.0) * 255).astype('uint8')
    rgba_surf[mval, 3] = 210
    rgba_surf[(src_d == 3) & mval, 3] = 110   # extrapolated — полупрозрачно
    buf = io.BytesIO()
    Image.fromarray(rgba_surf).save(buf, format='PNG', optimize=True)
    b64_surf = base64.b64encode(buf.getvalue()).decode()

    # --- (2) Owner map: owner + quality → RGBA (как в m01.qa_map) ---
    owner_d, qual_d, _ = m01._reproject_categorical_for_display(
        owner, quality, transform, dem_crs)
    rgba_owner = np.zeros((*owner_d.shape, 4), dtype='uint8')
    for code_v, col in m01.OWNER_COLORS.items():
        mm = owner_d == code_v
        rgba_owner[mm, 0], rgba_owner[mm, 1], rgba_owner[mm, 2] = col
        rgba_owner[mm, 3] = 0 if code_v == 0 else 235
    rgba_owner[(qual_d == 1) & (owner_d != 0), 3] = 120
    rgba_owner[owner_d == m01.NODATA_DISPLAY, 3] = 0
    buf = io.BytesIO()
    Image.fromarray(rgba_owner).save(buf, format='PNG', optimize=True)
    b64_owner = base64.b64encode(buf.getvalue()).decode()

    # --- (3) Сырой DEM в полосе 100м от проезжей части ---
    # маска: буфер 100м от carriageway_utm, растеризуем на сетке DEM
    strip_utm = carriageway_utm.buffer(100.0)
    strip_dem = m01.gpd.GeoSeries([strip_utm], crs=UTM).to_crs(dem_crs).iloc[0]
    strip_mask = _rasterize([(strip_dem, 1)], out_shape=dem.shape,
                            transform=transform, fill=0, all_touched=True,
                            dtype='uint8').astype(bool)
    # высоты → uint8 (диапазон по p2/p98 в полосе, шаг ~5см)
    dem_strip = dem.copy()
    dem_strip[~strip_mask] = np.nan
    v_raw = dem_strip[np.isfinite(dem_strip)]
    lo_r, hi_r = float(np.percentile(v_raw, 2)), float(np.percentile(v_raw, 98))
    dem_code = np.full(dem.shape, 255, dtype='uint8')
    fin_r = np.isfinite(dem_strip)
    dem_code[fin_r] = np.clip((dem_strip[fin_r] - lo_r) /
                              max(1e-6, hi_r - lo_r) * 254, 0, 254).astype('uint8')
    # band2 для dem-слоя: 1=valid, 0=nodata (для прозрачности краёв)
    dem_src = np.where(fin_r, 1, 0).astype('uint8')
    dem_d, dem_src_d, _ = m01._reproject_categorical_for_display(
        dem_code, dem_src, transform, dem_crs)
    rgba_dem = np.zeros((*dem_d.shape, 4), dtype='uint8')
    cmap_dem = colormaps['viridis']  # отличается от turbo — визуально разделимо
    mdem = (dem_d != 255) & (dem_src_d == 1)
    rgba_dem[mdem] = (cmap_dem(dem_d[mdem] / 254.0) * 255).astype('uint8')
    rgba_dem[mdem, 3] = 200
    buf = io.BytesIO()
    Image.fromarray(rgba_dem).save(buf, format='PNG', optimize=True)
    b64_dem = base64.b64encode(buf.getvalue()).decode()
    log(f'  DEM raw: полоса 100м, px={int(mdem.sum()):,}, '
        f'диапазон h_ell [{lo_r:.1f}..{hi_r:.1f}]м')

    # --- карта: 3 ImageOverlay со switch'ером; по умолчанию только surface ---
    fm = folium.Map(location=[(s4 + n4) / 2, (w4 + e4) / 2], zoom_start=15,
                    control_scale=True, tiles=None)
    fm.get_root().html.add_child(folium.Element(
        '<style>.leaflet-image-layer{image-rendering:pixelated;'
        'image-rendering:crisp-edges;}</style>'))
    folium.TileLayer('OpenStreetMap', name='OSM', max_zoom=19).add_to(fm)
    folium.TileLayer(tiles='https://server.arcgisonline.com/ArcGIS/rest/'
                           'services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
                     attr='Esri', name='Спутник', max_zoom=19, show=True).add_to(fm)
    folium.raster_layers.ImageOverlay(
        image=f'data:image/png;base64,{b64_dem}',
        bounds=[[s4, w4], [n4, e4]], opacity=0.70,
        name=f'DEM сырой (100м от дороги) [{lo_r:.1f}..{hi_r:.1f}м элл]',
        show=False).add_to(fm)
    folium.raster_layers.ImageOverlay(
        image=f'data:image/png;base64,{b64_owner}',
        bounds=[[s4, w4], [n4, e4]], opacity=0.80,
        name='Owner map (тёмн=road, оранж=sidewalk, зел=green, '
             'красн=building, син=water)',
        show=False).add_to(fm)
    folium.raster_layers.ImageOverlay(
        image=f'data:image/png;base64,{b64_surf}',
        bounds=[[s4, w4], [n4, e4]], opacity=0.85,
        name=f'Поверхность БС-77 [{lo_s:.1f}..{hi_s:.1f}м], '
             'блёкло = extrapolated',
        show=True).add_to(fm)
    if report is not None:
        for _, p in report.iterrows():
            d = p['dH_bs77']
            colr = 'green' if abs(d) < 0.3 else ('orange' if abs(d) < 0.8
                                                 else 'red')
            tag = ' [holdout]' if p['holdout'] else ''
            folium.CircleMarker(
                [p['lat'], p['lon']], radius=5, color='white', weight=1,
                fill=True, fill_color=colr, fill_opacity=0.95,
                popup=folium.Popup(
                    f"h_pas={p['h_pas']:.2f}<br>h_model="
                    f"{p['h_model_ell'] - shift:.2f}<br>dH={d:+.2f}{tag}",
                    max_width=240)).add_to(fm)
    fm.fit_bounds([[s4, w4], [n4, e4]])
    folium.LayerControl(collapsed=False).add_to(fm)
    fm.save(str(OUT / 'road_qa_map.html'))
    log(f'✓ {OUT / "road_qa_map.html"}')

    qa = {
        'object': m01.MVP_NAME,
        'train_px': int(len(rows)), 'inlier_pct': float(inlier.mean() * 100),
        'mad_m': float(mad),
        'surface_px': {'measured': n_meas, 'reconstructed': n_rec,
                       'extrapolated': n_ext},
        'profile_bins': int(len(prof)),
        'bins_rejected': {
            'total': int((~prof['kept']).sum()),
            'shadow_jump': int((prof['reject_reason'] == 'shadow_jump').sum()),
            'slope': int((prof['reject_reason'] == 'slope').sum()),
            'pickets': [float(x) for x in
                        prof.loc[~prof['kept'], 'pk'].round(0)],
        },
        'profile_h_bs77': [
            float(np.nanmin(prof['h_smooth']) - shift),
            float(np.nanmax(prof['h_smooth']) - shift)],
        'cross_bins': int(len(cross_tab)),
        'vertical': val,
        'params': {'PK_BIN': PK_BIN, 'OFF_BIN': OFF_BIN,
                   'UP_REJECT': UP_REJECT, 'LOW_ANCHOR_PCT': LOW_ANCHOR_PCT,
                   'INLIER_TOL': INLIER_TOL, 'CROSS_CLIP': CROSS_CLIP},
    }
    (OUT / 'road_qa.json').write_text(json.dumps(qa, ensure_ascii=False,
                                                 indent=2))
    log(f'✓ {OUT / "road_qa.json"}')
    log('')
    log('ГОТОВО. Смотреть: road_profile.png (профиль должен стать плавным, '
        '2–3.5м БС без скачков к 5–6м), road_qa_map.html (цвет поверхности '
        'должен плавно меняться вдоль, а не пятнами).')


if __name__ == '__main__':
    main()
