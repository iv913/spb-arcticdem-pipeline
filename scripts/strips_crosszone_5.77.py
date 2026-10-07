#!/usr/bin/env python3
"""
5.77 шаг A: кросс-зона для гейт-кандидата H3 — СЕВЕР-ЗАПАД
(Сестрорецк-Тарховка-Разлив, 30.12-30.24E, 60.01-60.06N).

Выбор зоны: strips_density_grid_5.75.csv, вне пилота 5.75/5.76
(30.24-30.34, 60.01-60.06); 22 hot-ячейки n>=20, медиана плотности 86 —
максимум вне пилота; 393 точки icesat2_clean; террейн иной, чем в пилоте
(город + озеро Разлив + лес Тарховки против дюнного пояса).

Пайплайн = репликация 5.75/5.76 (requests-POST STAC, клипы 3413,
per-strip ко-регистрация на TRAIN-зебре, фильтр MAD<=1.5 & n>=80,
memmap-стек -> p25/p50 + sigma + n_cov). Уроки 5.74-5.76:
  - dst_nodata=np.nan во всех reproject (нули вне покрытия тайла)
  - rasterio 1.5: count=1 в writer
  - сэмпл точек только после 4326->crs клипа
Выход: dem_cache/strips_zone_5.77/ + results/strips_crosszone_5.77.json
"""
import os, json
from pathlib import Path
from datetime import datetime
import numpy as np
import pandas as pd
import rasterio
from rasterio.windows import from_bounds
from rasterio.warp import reproject, Resampling
import requests

HOME = Path('/home/z/my-project/spb-arcticdem-pipeline')
ZONE = (30.12, 60.01, 30.24, 60.06)           # W S E N
PX = 0.0000178
CACHE = HOME / 'scripts/dem_cache/strips_zone_5.77'
CACHE.mkdir(parents=True, exist_ok=True)
OUTJ = HOME / 'results/strips_crosszone_5.77.json'
MAD_TOL = 1.5

os.environ['AWS_NO_SIGN_REQUEST'] = 'YES'
os.environ['CPL_VSIL_CURL_ALLOWED_EXTENSIONS'] = '.tif'
os.environ['VSI_CACHE'] = 'TRUE'
os.environ['GDAL_HTTP_MAX_RETRY'] = '8'
os.environ['GDAL_HTTP_TIMEOUT'] = '120'
os.environ['GDAL_HTTP_VERSION'] = '2'


def log(m):
    print(f"[{datetime.now():%H:%M:%S}] {m}", flush=True)


def stage_select():
    from shapely.geometry import shape, box
    r = requests.post("https://stac.pgc.umn.edu/api/v1/search", json={
        "collections": ["arcticdem-strips-s2s041-2m"],
        "bbox": list(ZONE), "limit": 500}, timeout=120)
    r.raise_for_status()
    items = r.json().get('features', [])
    log(f"STAC items: {len(items)}")
    pb = box(*ZONE)
    rows = []
    for it in items:
        p = it['properties']
        if not shape(it['geometry']).intersects(pb):
            continue
        v = p.get('pgc:valid_area_percent') or 0
        c = p.get('pgc:cloud_area_percent') or 1
        if v < 0.5 or c > 0.3:
            continue
        rows.append(dict(
            id=it['id'][:80], dt=(p.get('datetime') or '')[:10],
            year=(p.get('datetime') or '')[:4],
            pairname=(p.get('pgc:pairname') or '')[:50],
            valid=v, cloud=c, rmse=p.get('pgc:rmse'),
            dem_href=it['assets']['dem']['href'] if 'dem' in it['assets'] else ''))
    df = pd.DataFrame(rows).drop_duplicates('pairname')
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
    sel = pd.DataFrame(sel).reset_index(drop=True)
    log(f"выбрано {len(sel)} стрипов, годы: {sorted(sel.year.unique())}")
    return sel


def stage_clips(sel):
    import geopandas as gpd
    from shapely.geometry import box
    clips = []
    for i, r in sel.iterrows():
        dst = CACHE / f"clip_{i:02d}_{r['dt']}_{r['id'][12:28]}.tif"
        if dst.exists():
            clips.append(dst)
            continue
        try:
            with rasterio.open(r.dem_href) as src:
                b_src = (gpd.GeoSeries([box(*ZONE)], crs='EPSG:4326')
                         .to_crs(src.crs).iloc[0].buffer(300))
                win = from_bounds(*b_src.bounds, src.transform)
                win = rasterio.windows.Window(
                    int(np.floor(win.col_off)), int(np.floor(win.row_off)),
                    int(np.ceil(win.col_off + win.width)) - int(np.floor(win.col_off)),
                    int(np.ceil(win.row_off + win.height)) - int(np.floor(win.row_off)))
                arr = src.read(1, window=win).astype('float32')
                if src.nodata is not None:
                    arr[arr == src.nodata] = np.nan
                t = rasterio.windows.transform(win, src.transform)
                prof = dict(driver='GTiff', count=1, height=int(win.height),
                            width=int(win.width), dtype='float32',
                            crs=src.crs, transform=t, compress='deflate',
                            nodata=-32768)
                with rasterio.open(dst, 'w', **prof) as d:
                    d.write(np.where(np.isfinite(arr), arr, -32768), 1)
            clips.append(dst)
            log(f"  clip {i+1}/{len(sel)} {r['dt']} ok ({dst.stat().st_size//1024//1024} MB)")
        except Exception as e:
            log(f"  clip {i+1} FAIL {str(e)[:120]}")
    return clips


def strip_stats(c, ic3413, h_ref):
    with rasterio.open(c) as s:
        a = s.read(1).astype('float32')
        a[a == -32768] = np.nan
        px = ic3413.to_crs(s.crs)
        r2, c2 = rasterio.transform.rowcol(s.transform, px.x.values, px.y.values)
        inb = (r2 >= 0) & (r2 < s.height) & (c2 >= 0) & (c2 < s.width)
        r2 = np.clip(r2, 0, s.height - 1); c2 = np.clip(c2, 0, s.width - 1)
        hs = np.where(inb, a[r2, c2], np.nan)
    d = hs - h_ref
    off = float(np.nanmedian(d))
    mad = float(np.nanmedian(np.abs(d - off)))
    return off, mad, int(np.isfinite(d).sum())


def fuse(clips, offs, tr, W, H):
    """качественный стек (memmap) -> p25/p50 + n_cov + sigma"""
    mm = np.memmap(CACHE / 'stack_q.dat', dtype='float32', mode='w+',
                   shape=(len(clips), H, W))
    for k, c in enumerate(clips):
        with rasterio.open(c) as s:
            a = s.read(1).astype('float32')
            a[a == -32768] = np.nan
            if not np.isfinite(a).any():
                log(f"  fuse {k}: {c.name} SKIP (нет данных)")
                continue
            reproject(a - offs[k], mm[k], src_transform=s.transform,
                      src_crs=s.crs, dst_transform=tr, dst_crs='EPSG:4326',
                      resampling=Resampling.bilinear, dst_nodata=np.nan)
        log(f"  fuse {k}: {c.name[6:22]} ok")
    mm.flush()
    outs = {p: np.full((H, W), np.nan, dtype='float32') for p in (25, 50)}
    n_cov = np.zeros((H, W), dtype='int16')
    sigma = np.zeros((H, W), dtype='float32')
    CH = 256
    for r0 in range(0, H, CH):
        r1 = min(H, r0 + CH)
        blk = np.array(mm[:, r0:r1, :])
        b = np.where(np.isfinite(blk), blk, np.inf).astype('float32')
        b.sort(axis=0)
        fin = np.isfinite(blk)
        n_valid = np.sum(fin, axis=0)
        for p in outs:
            rank = np.clip(np.floor((n_valid - 1) * p / 100.0), 0,
                           b.shape[0] - 1).astype('int64')
            outs[p][r0:r1] = np.take_along_axis(
                b, rank[None, :, :], axis=0)[0]
        n_cov[r0:r1] = n_valid
        cnt = np.maximum(n_valid, 1).astype('float32')
        s1 = np.where(fin, blk, 0.0).sum(axis=0)
        s2 = np.where(fin, blk * blk, 0.0).sum(axis=0)
        var = np.maximum(s2 / cnt - (s1 / cnt) ** 2, 0.0)
        sigma[r0:r1] = np.sqrt(var)
        sigma[r0:r1][n_valid == 0] = np.nan
        if r0 % 1024 == 0:
            log(f"  percentile block {r0}/{H}")
    del mm
    return outs, n_cov, sigma


def main():
    t00 = datetime.now()
    ic = pd.read_csv(HOME / 'scripts/data/icesat2_clean.csv')
    ic = ic[(ic.lon >= ZONE[0]) & (ic.lon <= ZONE[2]) &
            (ic.lat >= ZONE[1]) & (ic.lat <= ZONE[3])].copy()
    import geopandas as gpd
    pts = gpd.GeoSeries(gpd.points_from_xy(ic.lon, ic.lat), crs='EPSG:4326')
    blk = (np.floor(ic.lat / 0.02).astype(int) +
           np.floor(ic.lon / 0.0127).astype(int))
    ic['half'] = blk % 2
    tr_mask = (ic.half == 0).values
    log(f"ICESat в зоне: {len(ic)}; train {tr_mask.sum()} / "
        f"valid {(~tr_mask).sum()} (зебра 2км)")

    sel = stage_select()
    sel.to_csv(CACHE / 'sel_5.77.csv', index=False)
    clips = stage_clips(sel)
    log(f"клипов готово: {len(clips)}")

    stats, offs_all = [], []
    for k, c in enumerate(clips):
        off, mad, n = strip_stats(c, pts.to_crs(_crs_of(c)),
                                  ic.h_icesat_baltic.values)
        stats.append(dict(clip=c.name, date=c.name.split('_')[2],
                          off=round(off, 2), mad=round(mad, 2), n=n))
        offs_all.append(off)
    st = pd.DataFrame(stats)
    st.to_csv(CACHE / 'strip_stats_5.77.csv', index=False)
    keep = [k for k, s in enumerate(stats) if s['mad'] <= MAD_TOL and s['n'] >= 80]
    log(f"качество: {len(keep)}/{len(clips)} стрипов MAD<={MAD_TOL}: "
        f"{[st.date.iloc[k] for k in keep]}")

    W = int((ZONE[2] - ZONE[0]) / PX)
    H = int((ZONE[3] - ZONE[1]) / PX)
    tr = rasterio.transform.from_origin(ZONE[0], ZONE[3], PX, PX)
    kc = [clips[k] for k in keep]
    ko = [offs_all[k] for k in keep]
    fused, n_cov, sigma = fuse(kc, ko, tr, W, H)
    np.save(CACHE / 'n_cov_5.77.npy', n_cov)
    np.save(CACHE / 'sigma_5.77.npy', sigma)
    for p, arr in fused.items():
        prof = dict(driver='GTiff', width=W, height=H, count=1,
                    dtype='float32', crs='EPSG:4326', transform=tr,
                    compress='deflate', nodata=-32768)
        with rasterio.open(CACHE / f'fused_q_p{p}_5.77.tif', 'w', **prof) as d:
            d.write(np.where(np.isfinite(arr), arr, -32768), 1)

    r, c = rasterio.transform.rowcol(tr, ic.lon.values, ic.lat.values)
    r = np.clip(r, 0, H - 1); c = np.clip(c, 0, W - 1)
    v = ~tr_mask
    res = dict(zone=list(ZONE), train_n=int(tr_mask.sum()), valid_n=int(v.sum()),
               kept=[stats[k]['date'] for k in keep],
               kept_mad=[stats[k]['mad'] for k in keep],
               kept_off=[stats[k]['off'] for k in keep],
               strips_total=len(clips))
    for p, arr in fused.items():
        d = arr[r[v], c[v]] - ic.h_icesat_baltic.values[v]
        off = float(np.nanmedian(d))
        dd = d - off
        res[f'q_p{p}_holdout'] = dict(
            resid_off=round(off, 3),
            mad=round(float(np.nanmedian(np.abs(dd))), 3),
            p95=round(float(np.nanpercentile(np.abs(dd), 95)), 3),
            n=int(np.isfinite(d).sum()))
        log(f"  quality p{p} HOLDOUT: MAD {res[f'q_p{p}_holdout']['mad']:.3f} "
            f"p95 {res[f'q_p{p}_holdout']['p95']:.2f}")
    res['cover'] = round(float(np.mean(n_cov > 0)), 4)
    res['sigma_med'] = round(float(np.nanmedian(sigma)), 3)
    res['elapsed_min'] = round((datetime.now() - t00).total_seconds() / 60, 1)
    OUTJ.write_text(json.dumps(res, ensure_ascii=False, indent=1))
    log(f"done {res['elapsed_min']} min, покрытие стека {res['cover']:.1%}")


def _crs_of(c):
    with rasterio.open(c) as s:
        return s.crs


if __name__ == '__main__':
    main()
