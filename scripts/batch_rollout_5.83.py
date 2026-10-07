#!/usr/bin/env python3
"""5.83: пакетный тираж гибридного 2м DEM на 473 пары стрипов.

Протокол (заморожен, 5.80-81 + batch_zones_5.79):
  1) зоны: жадные неперекрывающиеся окна 0.10x0.05 по density grid
     (n>=6 стрипов) + icesat2_clean >=150 тчк, исключая готовые зоны;
  2) STAC-отбор: leaf-off (месяцы 11-04), valid>=0.5, cloud<=0.3,
     dedup pairname, <=4/год, топ-20 по score;
  3) клипы -> кэш зоны (resumable), stats по train, keep MAD<=1.5 & n>=80;
  4) per-strip offsets по ОТКРЫТЫМ train точкам (canopy<1 м) — 5.81;
  5) фьюжия p25/p50/sigma (memmap, чанки) -> blend w=clip((1.5-sigma)/1) — 5.80;
  6) prior v2 (FABDEM + model) 2м;
  7) H2 = prior + med31(clip(residual,+-1));
  8) правило тиража 5.78: стек отбракован -> prior-only;
     prior train MAD >= 0.45 -> продукт hybrid, иначе prior;
  9) продукт: Int16 см БС-77 + quality (n_cov, sigma, canopy30),
     вердикт JSON, строка в zones_summary_5.83.csv, прогресс-журнал.

CLI:
  python3 batch_rollout_5.83.py plan                  -- кандидаты зон
  python3 batch_rollout_5.83.py zone W S E N TAG      -- прогон одной зоны
  python3 batch_rollout_5.83.py summary               -- сводка
"""
import os, sys, json, time
from pathlib import Path
from datetime import datetime
import numpy as np
import pandas as pd
import rasterio
from rasterio.windows import Window, from_bounds
from rasterio.warp import reproject, Resampling

HOME = Path(__file__).resolve().parents[1]
FAB_TIF = HOME / 'scripts/data/consensus_dtm_5.71/bareearth_bs77_cm_5.71.tif'
TMP = HOME / 'scripts/dem_cache/_glo30_tmp'
MODEL = HOME / 'scripts/data/mclf_v2_model_5.74.joblib'
IC_CSV = HOME / 'scripts/data/icesat2_clean.csv'
OUTD = HOME / 'download/products_5.83'
OUTD.mkdir(parents=True, exist_ok=True)
PROG = HOME / 'results/batch_progress_5.83.jsonl'
SUMMARY = HOME / 'results/zones_summary_5.83.csv'

PX = 0.0000178
PAD30 = 40 * 0.0002777777778
MAD_TOL, N_MIN = 1.5, 80
CAN_OPEN = 1.0
W0_SIG, W1_SIG = 0.5, 1.5
RULE_MAD = 0.45
MONTHS = {11, 12, 1, 2, 3, 4}
FEATS = ['canopy', 'slope', 'rough', 'tpi', 'openness', 'dsm', 'lat', 'lon']

# зоны, уже закрытые (5.76-5.79) — тире по bbox (W,S,E,N)
DONE = [
    (30.24, 60.01, 30.34, 60.06),  # пилот дюны
    (30.12, 60.01, 30.24, 60.06),  # Сестрорецк-запад (кросс-зона)
    (30.36, 60.00, 30.46, 60.06),  # A Ржевка/Охта
    (30.07, 59.98, 30.17, 60.04),  # B Озерки/Коломяги
    (30.05, 59.92, 30.15, 59.97),  # Z1 Стрельна/Петергоф-зап
    (30.21, 59.96, 30.31, 60.01),  # Z2 Автово/Дачное
    (30.21, 59.90, 30.31, 59.95),  # Z3 Лигово-юг
    (30.36, 59.9489, 30.46, 59.9989),  # N1 (5.83, партия 1)
    (30.46, 59.9743, 30.56, 60.0243),  # N2 (5.83, партия 1)
]
WIN_W, WIN_H = 0.10, 0.05
CITY_BBOX = (29.60, 59.70, 31.10, 60.30)   # СПб + пригороды (Кронштадт,
# Зеленогорск, Пушкин, Колпино); полное расширение 29.20-31.20 на след. партии
DENS_MIN, ICESAT_MIN = 6, 150

os.environ['AWS_NO_SIGN_REQUEST'] = 'YES'
os.environ['CPL_VSIL_CURL_ALLOWED_EXTENSIONS'] = '.tif'
os.environ['VSI_CACHE'] = 'TRUE'
os.environ['GDAL_HTTP_MAX_RETRY'] = '8'
os.environ['GDAL_HTTP_TIMEOUT'] = '120'
os.environ['GDAL_HTTP_VERSION'] = '2'


def log(m):
    print(f"[{datetime.now():%H:%M:%S}] {m}", flush=True)


def prog(rec):
    with open(PROG, 'a') as f:
        f.write(json.dumps(dict(ts=datetime.now().isoformat(), **rec),
                           ensure_ascii=False) + '\n')


def glo_tiles_for(bbox):
    n0 = int(np.floor((bbox[1] - PAD30)))
    n1 = int(np.floor((bbox[3] + PAD30)))
    e0 = int(np.floor((bbox[0] - PAD30)))
    e1 = int(np.floor((bbox[2] + PAD30)))
    return [f"N{n:02d}_00_E{e:03d}_00" for n in (n0, n1) for e in (e0, e1)]


# ---------------------------------------------------------------- density grid
def stage_plan():
    import requests
    from shapely.geometry import shape, box
    ic = pd.read_csv(IC_CSV)
    inv = pd.read_csv(HOME / 'results/arcticdem_strips_inventory_5.74.csv')
    inv = inv[inv.scope == 'full']
    strip_pts = []
    # плотность стрипов: точки пересечения пар с окнами считаем по STAC один раз
    r = requests.post("https://stac.pgc.umn.edu/api/v1/search", json={
        "collections": ["arcticdem-strips-s2s041-2m"],
        "bbox": list(CITY_BBOX), "limit": 1000}, timeout=180)
    r.raise_for_status()
    feats = r.json().get('features', [])
    log(f"STAC strips in city bbox: {len(feats)}")
    geoms = []
    for it in feats:
        p = it['properties']
        v = p.get('pgc:valid_area_percent') or 0
        c = p.get('pgc:cloud_area_percent') or 1
        if v >= 0.5 and c <= 0.3:
            geoms.append(shape(it['geometry']))
    # сетка плотности 0.02x0.0127 (как блоки train/holdout), n = число стрипов
    gx = np.arange(CITY_BBOX[0], CITY_BBOX[2], 0.02)
    gy = np.arange(CITY_BBOX[1], CITY_BBOX[3], 0.0127)
    dens = np.zeros((len(gy), len(gx)), dtype='int16')
    for g in geoms:
        b = g.bounds
        i0 = max(int((b[0] - CITY_BBOX[0]) / 0.02), 0)
        i1 = min(int((b[2] - CITY_BBOX[0]) / 0.02) + 1, len(gx))
        j0 = max(int((b[1] - CITY_BBOX[1]) / 0.0127), 0)
        j1 = min(int((b[3] - CITY_BBOX[1]) / 0.0127) + 1, len(gy))
        if i1 > i0 and j1 > j0:
            dens[j0:j1, i0:i1] += 1
    # icesat-покрытие: скользящие окна 0.10x0.05 с шагом 0.02x0.0127,
    # жадный отбор по числу точек + подавление перекрытий (канон 5.79)
    W = int((CITY_BBOX[2] - CITY_BBOX[0]) / WIN_W)
    H = int((CITY_BBOX[3] - CITY_BBOX[1]) / WIN_H)
    cand = []
    step_x, step_y = 0.02, 0.0127
    nx = int((CITY_BBOX[2] - CITY_BBOX[0] - WIN_W) / step_x) + 1
    ny = int((CITY_BBOX[3] - CITY_BBOX[1] - WIN_H) / step_y) + 1
    # 2D-гистограмма точек + интегральное изображение для O(1) сумм по окнам
    hx = np.arange(CITY_BBOX[0], CITY_BBOX[2] + step_x, step_x)
    hy = np.arange(CITY_BBOX[1], CITY_BBOX[3] + step_y, step_y)
    H2, _, _ = np.histogram2d(ic.lat.values, ic.lon.values,
                              bins=[hy, hx])
    I = np.concatenate([np.zeros((1, H2.shape[1]), dtype='int32'),
                        np.cumsum(H2, axis=0)], axis=0)
    I = np.concatenate([np.zeros((I.shape[0], 1), dtype='int32'),
                        np.cumsum(I, axis=1)], axis=1)
    wn_x = int(round(WIN_W / step_x))
    wn_y = int(round(WIN_H / step_y))
    dens_y = int(round(WIN_H / step_y))
    dens_x = int(round(WIN_W / step_x))
    for jy in range(ny):
        for ix in range(nx):
            bb = (round(CITY_BBOX[0] + ix * step_x, 4),
                  round(CITY_BBOX[1] + jy * step_y, 4),
                  round(CITY_BBOX[0] + ix * step_x + WIN_W, 4),
                  round(CITY_BBOX[1] + jy * step_y + WIN_H, 4))
            overl = any(not (bb[2] <= x[0] or bb[0] >= x[2] or
                             bb[3] <= x[1] or bb[1] >= x[3]) for x in DONE)
            if overl:
                continue
            n_ic = int(I[jy + wn_y, ix + wn_x] - I[jy, ix + wn_x] -
                       I[jy + wn_y, ix] + I[jy, ix])
            i0 = int((bb[0] - CITY_BBOX[0]) / 0.02)
            j0 = int((bb[1] - CITY_BBOX[1]) / 0.0127)
            i1 = min(i0 + dens_x, dens.shape[1])
            j1 = min(j0 + dens_y, dens.shape[0])
            dmax = int(dens[j0:j1, i0:i1].max()) if (i1 > i0 and j1 > j0) else 0
            if n_ic >= ICESAT_MIN and dmax >= DENS_MIN:
                cand.append(dict(bbox=list(bb), icesat=n_ic, dens=dmax))
    cand.sort(key=lambda d: -d['icesat'])
    # жадное подавление перекрытий
    picked = []
    for c in cand:
        bb = c['bbox']
        if any(not (bb[2] <= p['bbox'][0] or bb[0] >= p['bbox'][2] or
                    bb[3] <= p['bbox'][1] or bb[1] >= p['bbox'][3])
               for p in picked):
            continue
        picked.append(c)
    plan = dict(date=datetime.now().isoformat(),
                n_candidates=len(cand), zones=picked)
    (HOME / 'results/rollout_plan_5.83.json').write_text(
        json.dumps(plan, ensure_ascii=False, indent=1))
    log(f"кандидатов {len(cand)} -> зон после подавления {len(picked)}")
    for k, p in enumerate(picked):
        log(f"  {k+1:2d}. bbox {p['bbox']} icesat {p['icesat']} dens {p['dens']}")
    return plan


# ------------------------------------------------------------------- stage A/B
def stage_select(bbox):
    import requests
    from shapely.geometry import shape, box
    r = requests.post("https://stac.pgc.umn.edu/api/v1/search", json={
        "collections": ["arcticdem-strips-s2s041-2m"],
        "bbox": list(bbox), "limit": 500}, timeout=120)
    r.raise_for_status()
    items = r.json().get('features', [])
    log(f"STAC items: {len(items)}")
    pb = box(*bbox)
    rows = []
    for it in items:
        p = it['properties']
        if not shape(it['geometry']).intersects(pb):
            continue
        v = p.get('pgc:valid_area_percent') or 0
        c = p.get('pgc:cloud_area_percent') or 1
        if v < 0.5 or c > 0.3:
            continue
        dt = (p.get('datetime') or '')[:10]
        if int(dt[5:7]) not in MONTHS:
            continue
        rows.append(dict(id=it['id'][:80], dt=dt, year=dt[:4],
                         pairname=(p.get('pgc:pairname') or '')[:50],
                         valid=v, cloud=c, rmse=p.get('pgc:rmse'),
                         dem_href=it['assets']['dem']['href']
                         if 'dem' in it['assets'] else ''))
    df = pd.DataFrame(rows).drop_duplicates('pairname')
    if not len(df):
        return df
    df['score'] = df.valid * 10 - df.cloud * 5 - df.rmse.fillna(3) * 0.3
    df = df.sort_values('score', ascending=False)
    sel, per_year = [], {}
    for _, row in df.iterrows():
        if per_year.get(row.year, 0) >= 4:
            continue
        per_year[row.year] = per_year.get(row.year, 0) + 1
        sel.append(row)
        if len(sel) >= 20:
            break
    return pd.DataFrame(sel).reset_index(drop=True)


def stage_clips(sel, bbox, cache):
    import geopandas as gpd
    from shapely.geometry import box
    clips = []
    for i, r in sel.iterrows():
        dst = cache / f"clip_{i:02d}_{r['dt']}_{r['id'][12:28]}.tif"
        if dst.exists():
            clips.append(dst)
            continue
        try:
            with rasterio.open(r.dem_href) as src:
                b_src = (gpd.GeoSeries([box(*bbox)], crs='EPSG:4326')
                         .to_crs(src.crs).iloc[0].buffer(300))
                win = from_bounds(*b_src.bounds, src.transform)
                c0, r0 = int(np.floor(win.col_off)), int(np.floor(win.row_off))
                c1 = int(np.ceil(win.col_off + win.width))
                r1 = int(np.ceil(win.row_off + win.height))
                win2 = Window(c0, r0, c1 - c0, r1 - r0)
                arr = src.read(1, window=win2).astype('float32')
                if src.nodata is not None:
                    arr[arr == src.nodata] = np.nan
                t = rasterio.windows.transform(win2, src.transform)
                prof = dict(driver='GTiff', count=1, height=int(win2.height),
                            width=int(win2.width), dtype='float32',
                            crs=src.crs, transform=t, compress='deflate',
                            nodata=-32768)
                with rasterio.open(dst, 'w', **prof) as d:
                    d.write(np.where(np.isfinite(arr), arr, -32768), 1)
            clips.append(dst)
            log(f"  clip {i+1}/{len(sel)} {r['dt']} ok "
                f"({dst.stat().st_size//1024//1024} MB)")
        except Exception as e:
            log(f"  clip {i+1} FAIL {str(e)[:100]}")
            time.sleep(2)
    return clips


def strip_stats(c, pts_crs, h_ref):
    with rasterio.open(c) as s:
        a = s.read(1).astype('float32')
        a[a == -32768] = np.nan
        px = pts_crs.to_crs(s.crs)
        r2, c2 = rasterio.transform.rowcol(s.transform, px.x.values, px.y.values)
        inb = (r2 >= 0) & (r2 < s.height) & (c2 >= 0) & (c2 < s.width)
        r2 = np.clip(r2, 0, s.height - 1)
        c2 = np.clip(c2, 0, s.width - 1)
        hs = np.where(inb, a[r2, c2], np.nan)
    d = hs - h_ref
    off = float(np.nanmedian(d))
    mad = float(np.nanmedian(np.abs(d - off)))
    return off, mad, int(np.isfinite(d).sum())


# --------------------------------------------------------------------- prior v2
def build_prior(bbox, tr, W, H):
    """prior v2 (FABDEM + model) на 2м-сетке зоны. Канон v2_prior_zone_5.77."""
    import joblib
    from scipy.ndimage import uniform_filter, minimum_filter
    with rasterio.open(FAB_TIF) as src:
        full = src.read(1).astype('float32')
        nodata = src.nodata
        gtr, gcrs = src.transform, src.crs
        GH, GW = src.shape
    fab_full = full / 100.0
    fab_full[full == nodata] = np.nan
    P = PAD30
    r_lo, c_lo = rasterio.transform.rowcol(gtr, bbox[0] - P, bbox[3] + P)
    r_hi, c_hi = rasterio.transform.rowcol(gtr, bbox[2] + P, bbox[1] - P)
    r0, r1 = max(int(r_lo) - 2, 0), min(int(r_hi) + 2, GH)
    c0, c1 = max(int(c_lo) - 2, 0), min(int(c_hi) + 2, GW)
    win = Window(c0, r0, c1 - c0, r1 - r0)
    wtr = rasterio.windows.transform(win, gtr)
    fab = fab_full[r0:r1, c0:c1]
    h30, w30 = fab.shape
    dsm = np.full((h30, w30), np.nan, dtype='float32')
    for t in glo_tiles_for(bbox):
        f = TMP / f"{t}.tif"
        if not f.exists():
            log(f"  GLO tile {t} отсутствует — пропуск")
            continue
        with rasterio.open(f) as s:
            a = s.read(1).astype('float32')
            buf = np.full((h30, w30), np.nan, dtype='float32')
            reproject(a, buf, src_transform=s.transform, src_crs=s.crs,
                      dst_transform=wtr, dst_crs=gcrs,
                      resampling=Resampling.bilinear, dst_nodata=np.nan)
            dsm = np.where(np.isfinite(buf) & ~np.isfinite(dsm), buf, dsm)
    canopy = dsm - fab
    f = np.where(np.isfinite(fab), fab, 0.0)
    mask = np.isfinite(fab).astype('float32')
    g_dy, g_dx = np.gradient(
        f, 0.00027778 * 111320 * np.cos(np.deg2rad(59.9)),
        0.00027778 * 111320)
    slope = np.degrees(np.arctan(np.hypot(g_dx, g_dy)))
    m1 = uniform_filter(f * mask, 3) / np.maximum(uniform_filter(mask, 3), 1e-6)
    m2 = (uniform_filter(f * f * mask, 3) /
          np.maximum(uniform_filter(mask, 3), 1e-6))
    rough = np.sqrt(np.maximum(m2 - m1 * m1, 0))
    w9 = uniform_filter(mask, 9)
    tpi = f - uniform_filter(f * mask, 9) / np.maximum(w9, 1e-6)
    fmin = minimum_filter(np.where(np.isfinite(fab), fab, 1e9), 5)
    openness = np.where(np.isfinite(fab), f - np.where(fmin < 1e8, fmin, f),
                        np.nan)
    for arr in (slope, rough, tpi, openness):
        arr[np.isnan(fab)] = np.nan
    model = joblib.load(MODEL)
    ys = wtr.f + wtr.e * (np.arange(h30, dtype='float32')[:, None] + 0.5)
    ys = np.broadcast_to(ys, (h30, w30))
    xs = wtr.c + wtr.a * (np.arange(w30, dtype='float32')[None, :] + 0.5)
    xs = np.broadcast_to(xs, (h30, w30))
    cols = dict(canopy=canopy, slope=slope, rough=rough, tpi=tpi,
                openness=openness, dsm=dsm, lat=ys, lon=xs)
    ok = np.isfinite(fab) & np.isfinite(dsm) & np.isfinite(canopy)
    Xg = np.stack([np.where(ok, cols[fn], 0.0) for fn in FEATS], axis=-1)
    v2 = np.full((h30, w30), np.nan, dtype='float32')
    v2[ok] = (fab[ok] + model.predict(Xg[ok]).astype('float32'))
    del Xg, cols
    prior2 = np.full((H, W), np.nan, dtype='float32')
    reproject(v2, prior2, src_transform=wtr, src_crs=gcrs,
              dst_transform=tr, dst_crs='EPSG:4326',
              resampling=Resampling.bilinear, dst_nodata=np.nan)
    return prior2, canopy, wtr, gcrs


# --------------------------------------------------------------- fusion + H2
def fuse_quantiles(clips, offs, tr, W, H, cache):
    mm = np.memmap(cache / 'stack_q.dat', dtype='float32', mode='w+',
                   shape=(len(clips), H, W))
    for k, c in enumerate(clips):
        with rasterio.open(c) as s:
            a = s.read(1).astype('float32')
            a[a == -32768] = np.nan
            if not np.isfinite(a).any():
                continue
            buf = np.full((H, W), np.nan, dtype='float32')
            reproject(a - offs[k], buf, src_transform=s.transform,
                      src_crs=s.crs, dst_transform=tr, dst_crs='EPSG:4326',
                      resampling=Resampling.bilinear, dst_nodata=np.nan)
            mm[k] = buf
            del buf
    mm.flush()
    p25 = np.full((H, W), np.nan, dtype='float32')
    p50 = np.full((H, W), np.nan, dtype='float32')
    n_cov = np.zeros((H, W), dtype='int16')
    sigma = np.full((H, W), np.nan, dtype='float32')
    CH = 128
    NL = len(clips)
    for r0 in range(0, H, CH):
        r1 = min(H, r0 + CH)
        blk = np.array(mm[:, r0:r1, :])
        fin = np.isfinite(blk)
        n_valid = fin.sum(axis=0)
        b = np.where(fin, blk, np.inf).astype('float32')
        b.sort(axis=0)
        rank = np.clip(np.floor((n_valid - 1) * 0.25), 0, NL - 1).astype('int64')
        p25[r0:r1] = np.take_along_axis(b, rank[None, :, :], axis=0)[0]
        rank50 = np.clip(np.floor((n_valid - 1) * 0.5), 0, NL - 1).astype('int64')
        p50[r0:r1] = np.take_along_axis(b, rank50[None, :, :], axis=0)[0]
        p25[r0:r1][n_valid == 0] = np.nan
        p50[r0:r1][n_valid == 0] = np.nan
        n_cov[r0:r1] = n_valid
        cnt = np.maximum(n_valid, 1).astype('float32')
        s1 = np.where(fin, blk, 0.0).sum(axis=0)
        s2 = np.where(fin, blk * blk, 0.0).sum(axis=0)
        var = np.maximum(s2 / cnt - (s1 / cnt) ** 2, 0)
        sig = np.sqrt(var)
        sig[n_valid == 0] = np.nan
        sigma[r0:r1] = sig
        del blk, fin, b, s1, s2, var, sig
    del mm
    os.remove(cache / 'stack_q.dat')
    return p25, p50, n_cov, sigma


def med31_clip(resid_f, size=31, clip=1.0, ch=512):
    import scipy.ndimage as ndi
    H, W = resid_f.shape
    h = size // 2
    out = np.empty((H, W), dtype='float32')
    for r0 in range(0, H, ch):
        r1 = min(H, r0 + ch)
        lo, hi = max(r0 - h, 0), min(r1 + h, H)
        src = np.clip(resid_f[lo:hi], -clip, clip)
        out[r0:r1] = ndi.median_filter(src, size=size, mode='nearest')[
            r0 - lo:r1 - lo]
    return out


def metrics(d):
    d = d[np.isfinite(d)]
    if len(d) == 0:
        return dict(mad=None, p95=None, n=0)
    off = float(np.median(d))
    dd = d - off
    return dict(resid_off=round(off, 3),
                mad=round(float(np.median(np.abs(dd))), 3),
                p95=round(float(np.percentile(np.abs(dd), 95)), 3),
                n=int(d.size))


def write_int16_cm(arr, path, tr, W, H):
    prof = dict(driver='GTiff', width=W, height=H, count=1, dtype='int16',
                crs='EPSG:4326', transform=tr, compress='deflate',
                nodata=-32768)
    a = np.where(np.isfinite(arr), np.rint(arr * 100.0), -32768).astype('int16')
    with rasterio.open(path, 'w', **prof) as d:
        d.write(a, 1)
    del a


# ------------------------------------------------------------------- zone run
def run_zone(W_, S_, E_, N_, TAG):
    bbox = (float(W_), float(S_), float(E_), float(N_))
    cache = HOME / f'scripts/dem_cache/strips_{TAG}'
    cache.mkdir(parents=True, exist_ok=True)
    t0 = datetime.now()
    ic = pd.read_csv(IC_CSV)
    ic = ic[(ic.lon >= bbox[0]) & (ic.lon <= bbox[2]) &
            (ic.lat >= bbox[1]) & (ic.lat <= bbox[3])].copy()
    blk = (np.floor(ic.lat / 0.02).astype(int) +
           np.floor(ic.lon / 0.0127).astype(int))
    ic['half'] = blk % 2
    tr_mask = (ic.half == 0).values
    log(f"ICESat в зоне: {len(ic)}; train {tr_mask.sum()} / "
        f"valid {(~tr_mask).sum()}")

    W = int((bbox[2] - bbox[0]) / PX)
    H = int((bbox[3] - bbox[1]) / PX)
    tr = rasterio.transform.from_origin(bbox[0], bbox[3], PX, PX)

    # --- prior v2 (нужен и для prior-only, и для гибрида) ---
    log("prior v2 (FABDEM+model)...")
    prior, canopy30_win, wtr30, gcrs30 = build_prior(bbox, tr, W, H)
    with rasterio.open(cache / f'prior_v2_2m_{TAG}.tif', 'w',
                       driver='GTiff', width=W, height=H, count=1,
                       dtype='float32', crs='EPSG:4326', transform=tr,
                       compress='deflate', nodata=-32768) as d:
        d.write(np.where(np.isfinite(prior), prior, -32768), 1)
    log(f"prior 2м: {np.isfinite(prior).mean():.1%} валидно, "
        f"med {np.nanmedian(prior):.2f} м")

    # канопи на точках (30м окно)
    r30, c30 = rasterio.transform.rowcol(wtr30, ic.lon.values, ic.lat.values)
    r30 = np.clip(r30, 0, canopy30_win.shape[0] - 1)
    c30 = np.clip(c30, 0, canopy30_win.shape[1] - 1)
    ic['can'] = canopy30_win[r30, c30].astype('float32')
    open_tr = tr_mask & (ic.can.values < CAN_OPEN)

    # --- стек (leaf-off) ---
    sel = stage_select(bbox)
    if len(sel) == 0:
        log("стрип-кандидатов нет — prior-only")
        sel = pd.DataFrame()
    sel.to_csv(cache / 'sel.csv', index=False)
    res = dict(zone=list(bbox), tag=TAG,
               train_n=int(tr_mask.sum()), valid_n=int((~tr_mask).sum()))
    r, c = rasterio.transform.rowcol(tr, ic.lon.values, ic.lat.values)
    r = np.clip(r, 0, H - 1)
    c = np.clip(c, 0, W - 1)
    hv = ~tr_mask

    pr_d = prior[r, c] - ic.h_icesat_baltic.values
    res['prior'] = dict(train=metrics(pr_d[tr_mask]),
                        holdout=metrics(pr_d[hv]))
    log(f"prior HOLDOUT: MAD {res['prior']['holdout']['mad']} "
        f"p95 {res['prior']['holdout']['p95']}")

    kept = []
    if len(sel):
        clips = stage_clips(sel, bbox, cache)
        log(f"клипов готово: {len(clips)}")
        pts = (__import__('geopandas').GeoSeries(
            __import__('geopandas').points_from_xy(ic.lon, ic.lat),
            crs='EPSG:4326'))
        stats = []
        for k, cl in enumerate(clips):
            off, mad, n = strip_stats(cl, pts, ic.h_icesat_baltic.values)
            stats.append(dict(clip=cl.name, date=cl.name.split('_')[2],
                              off=round(off, 2), mad=round(mad, 2), n=n))
        st = pd.DataFrame(stats)
        st.to_csv(cache / 'strip_stats.csv', index=False)
        kept_idx = [k for k, s in enumerate(stats)
                    if s['mad'] <= MAD_TOL and s['n'] >= N_MIN]
        log(f"качество: {len(kept_idx)}/{len(clips)} MAD<={MAD_TOL}")
        # offsets по открытым train (5.81)
        offs = []
        for k in kept_idx:
            cl = clips[k]
            with rasterio.open(cl) as s:
                a = s.read(1).astype('float32')
                a[a == -32768] = np.nan
                px = pts.to_crs(s.crs)
                r2, c2 = rasterio.transform.rowcol(s.transform, px.x.values,
                                                   px.y.values)
                inb = (r2 >= 0) & (r2 < s.height) & (c2 >= 0) & (c2 < s.width)
                r2 = np.clip(r2, 0, s.height - 1)
                c2 = np.clip(c2, 0, s.width - 1)
                hs = np.where(inb, a[r2, c2], np.nan)
            d = hs[open_tr] - ic.h_icesat_baltic.values[open_tr]
            d = d[np.isfinite(d)]
            offs.append(float(np.median(d)) if len(d) else stats[k]['off'])
        if kept_idx:
            p25, p50, n_cov, sigma = fuse_quantiles(
                [clips[k] for k in kept_idx], offs, tr, W, H, cache)
            w = np.clip((W1_SIG - sigma) / (W1_SIG - W0_SIG), 0, 1).astype('float32')
            blend = np.where(
                np.isfinite(p25) | np.isfinite(p50),
                w * np.nan_to_num(p50) + (1 - w) * np.nan_to_num(p25), np.nan)
            del w
            # quality-каналы (конвенция 5.82): n_cov, sigma_cm, canopy30_cm
            can2m = np.full((H, W), np.nan, dtype='float32')
            reproject(canopy30_win, can2m, src_transform=wtr30,
                      src_crs=gcrs30, dst_transform=tr, dst_crs='EPSG:4326',
                      resampling=Resampling.bilinear, dst_nodata=np.nan)
            nd16 = 65535
            q = np.full((3, H, W), nd16, dtype='uint16')
            q[0] = np.where(n_cov > 0,
                            np.minimum(n_cov.astype('int32'), nd16 - 1),
                            nd16).astype('uint16')
            q[1] = np.where(np.isfinite(sigma),
                            np.minimum(np.rint(sigma * 100), nd16 - 1), nd16)
            q[2] = np.where(np.isfinite(can2m),
                            np.clip(np.rint(can2m * 100), 0, nd16 - 1), nd16)
            with rasterio.open(OUTD / f'quality_5.83_{TAG}.tif', 'w',
                               driver='GTiff', width=W, height=H, count=3,
                               dtype='uint16', crs='EPSG:4326', transform=tr,
                               compress='deflate', nodata=nd16) as d:
                d.write(q)
            del q, can2m, sigma, n_cov, p25, p50
            resid = blend - prior
            resid_f = np.where(np.isfinite(resid), resid, 0.0).astype('float32')
            del resid, blend
            m1 = med31_clip(resid_f, 31, 1.0)
            del resid_f
            hybrid = prior + m1
            del m1
            hy_d = hybrid[r, c] - ic.h_icesat_baltic.values
            res['H2'] = dict(train=metrics(hy_d[tr_mask]),
                             holdout=metrics(hy_d[hv]))
            log(f"H2 HOLDOUT: MAD {res['H2']['holdout']['mad']} "
                f"p95 {res['H2']['holdout']['p95']}")
            kept = [stats[k]['date'] for k in kept_idx]
            # продукт по правилу тиража
            use_hybrid = res['prior']['train']['mad'] >= RULE_MAD
            product = hybrid if use_hybrid else prior
            res['rule'] = dict(prior_train_mad=res['prior']['train']['mad'],
                               use_hybrid=bool(use_hybrid))
            write_int16_cm(product, OUTD / f'hybrid_2m_5.83_{TAG}.tif',
                           tr, W, H)
            res['product'] = ('H2_hybrid' if use_hybrid else 'prior_v2_30m')
            del hybrid, product
        else:
            log("стек отбракован целиком — prior-only (протокол 5.79)")

    if 'H2' not in res:
        res['rule'] = dict(prior_train_mad=res['prior']['train']['mad'],
                           use_hybrid=False, reason='stack_rejected_or_empty')
        res['product'] = 'prior_v2_30m'
        write_int16_cm(prior, OUTD / f'hybrid_2m_5.83_{TAG}.tif', tr, W, H)

    # quality поверх (для prior-only n_cov=0)
    if 'H2' in res:
        pass
    res['minutes'] = round((datetime.now() - t0).total_seconds() / 60, 1)
    outj = HOME / 'results' / f'zone_{TAG}_5.83.json'
    outj.write_text(json.dumps(res, ensure_ascii=False, indent=1))
    prog(dict(tag=TAG, bbox=list(bbox), product=res['product'],
              prior_holdout=res['prior']['holdout'],
              h2_holdout=res.get('H2', {}).get('holdout'),
              rule=res['rule'], minutes=res['minutes']))
    # сводка
    row = dict(tag=TAG, bbox=' '.join(str(x) for x in bbox),
               product=res['product'],
               prior_mad=res['prior']['holdout']['mad'],
               prior_p95=res['prior']['holdout']['p95'],
               h2_mad=(res.get('H2', {}).get('holdout') or {}).get('mad'),
               h2_p95=(res.get('H2', {}).get('holdout') or {}).get('p95'),
               product_mad=(res['H2']['holdout']['mad'] if res.get('H2')
                            and res['rule'].get('use_hybrid')
                            else res['prior']['holdout']['mad']),
               n_strips=len(kept))
    df = pd.DataFrame([row])
    hdr = not SUMMARY.exists()
    df.to_csv(SUMMARY, mode='a', header=hdr, index=False)
    log(f"done {TAG}: продукт {res['product']}, {res['minutes']} min")
    return res


# quality-каналы пишутся в run_zone сразу (конвенция 5.82)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'plan'
    if cmd == 'plan':
        stage_plan()
    elif cmd == 'zone':
        run_zone(*sys.argv[2:7])
    elif cmd == 'summary':
        if SUMMARY.exists():
            print(SUMMARY.read_text())
        else:
            print('сводки пока нет')
    else:
        print(__doc__)


if __name__ == '__main__':
    main()
