#!/usr/bin/env python3
"""
5.75 шаг 2: пилот стрип-fusion «Северный пояс» (30.24-30.34E, 60.01-60.06N —
Сестрорецк/Лахта/Белоостров, слабая по валидации зона 5.66, но плотнейший
стек 2м-стрип ArcticDEM 2010-2024).

Стадии (кэш в dem_cache/strips_pilot_5.75/):
  1. отбор стрипов пересекающих пилот (valid>=0.5, cloud<=0.3, dedup pairname,
     ранжирование valid desc, топ-20 с разносом по годам)
  2. клипы /vsicurl (COG windowed read) на сетку 2м пилота -> Int16 см
  3. fusion: median (DSM-прокси) и p25 (нижняя огибающая, bare-earth)
  4. валидация на icesat2_clean в bbox: raw p25/p50 vs mclf_v2-сетка vs FABDEM
Выход: dem_cache tif + results/strips_pilot_5.75.json
"""
import os, json, sys
from pathlib import Path
from datetime import datetime
import numpy as np
import pandas as pd
import rasterio
from rasterio.windows import from_bounds
from rasterio.warp import reproject, Resampling

os.environ['AWS_NO_SIGN_REQUEST'] = 'YES'
os.environ['CPL_VSIL_CURL_ALLOWED_EXTENSIONS'] = '.tif'
os.environ['VSI_CACHE'] = 'TRUE'
os.environ['GDAL_HTTP_MAX_RETRY'] = '8'

HOME = Path('/home/z/my-project/repo')
PILOT = (30.24, 60.01, 30.34, 60.06)          # W S E N
PX = 0.0000178                                # ~2 м в градусах
CACHE = HOME / 'scripts/dem_cache/strips_pilot_5.75'
CACHE.mkdir(parents=True, exist_ok=True)
OUTJ = HOME / 'results/strips_pilot_5.75.json'


def log(m):
    print(f"[{datetime.now():%H:%M:%S}] {m}", flush=True)


def stage_select():
    import pystac_client
    from shapely.geometry import shape, box
    cat = pystac_client.Client.open("https://stac.pgc.umn.edu/api/v1/")
    items = list(cat.search(collections=["arcticdem-strips-s2s041-2m"],
                            bbox=list(PILOT)).items())
    pb = box(*PILOT)
    rows = []
    for it in items:
        p = it.properties
        if not shape(it.geometry).intersects(pb):
            continue
        v, c = p.get('pgc:valid_area_percent') or 0, p.get('pgc:cloud_area_percent') or 1
        if v < 0.5 or c > 0.3:
            continue
        rows.append(dict(
            id=it.id[:80], dt=(p.get('datetime') or '')[:10],
            year=(p.get('datetime') or '')[:4],
            pairname=(p.get('pgc:pairname') or '')[:50],
            valid=v, cloud=c, rmse=p.get('pgc:rmse'),
            dem_href=it.assets['dem'].href if 'dem' in it.assets else ''))
    df = pd.DataFrame(rows).drop_duplicates('pairname')
    df['score'] = df.valid * 10 - df.cloud * 5 - df.rmse.fillna(3) * 0.3
    df = df.sort_values('score', ascending=False)
    sel, per_year = [], {}
    for _, r in df.iterrows():
        if per_year.get(r.year, 0) >= 4:
            continue
        per_year[r.year] = per_year.get(r.year, 0) + 1
        sel.append(r)
        if len(sel) >= 20:
            break
    sel = pd.DataFrame(sel)
    sel.to_csv(CACHE / 'selected.csv', index=False)
    log(f"выбрано {len(sel)}/{len(df)} стрипов, годы {sorted(sel.year.unique())}")
    return sel


def stage_clip(sel):
    W = int((PILOT[2] - PILOT[0]) / PX)
    H = int((PILOT[3] - PILOT[1]) / PX)
    tr = rasterio.transform.from_origin(PILOT[0], PILOT[3], PX, PX)
    clips = []
    for i, r in sel.reset_index(drop=True).iterrows():
        dst = CACHE / f"clip_{i:02d}_{r.dt}_{r.id[12:28]}.tif"
        if dst.exists():
            clips.append(dst)
            continue
        try:
            with rasterio.open(r.dem_href) as src:
                win = from_bounds(*PILOT, src.transform)
                arr = src.read(1, window=win, boundless=True,
                               fill_value=src.nodata if src.nodata is not None else -99999)
                nd = src.nodata if src.nodata is not None else -99999
                a = arr.astype('float32')
                a[a == nd] = np.nan
                # в эллипсоид 84 (стрипы) -> вычтем геоид в БС-77 на выходе сцены? нет:
                # калибровка делается на этапе валидации сдвигом по медиане (справедливо
                # для сравнения методов на ОДНОМ датуме)
                out = np.full((H, W), np.nan, dtype='float32')
                reproject(a, out, src_transform=src.window_transform(win),
                          src_crs=src.crs, dst_transform=tr, dst_crs='EPSG:4326',
                          resampling=Resampling.bilinear)
                prof = dict(driver='GTiff', width=W, height=H, dtype='float32',
                            crs='EPSG:4326', transform=tr, compress='deflate',
                            nodata=-32768)
                o = np.where(np.isfinite(out), out, -32768).astype('float32')
                with rasterio.open(dst, 'w', **prof) as d:
                    d.write(o, 1)
            clips.append(dst)
            log(f"  clip {i+1}/{len(sel)} {r.dt} ok ({dst.stat().st_size//1024//1024} MB)")
        except Exception as e:
            log(f"  clip {i+1} FAIL {str(e)[:100]}")
    return clips, tr, W, H


def stage_fuse(clips, tr, W, H):
    stack = []
    for c in clips:
        with rasterio.open(c) as s:
            a = s.read(1).astype('float32')
            a[a == -32768] = np.nan
            stack.append(a)
    st = np.stack(stack)
    med = np.nanmedian(st, axis=0)
    p25 = np.nanpercentile(st, 25, axis=0)
    n_cov = np.sum(np.isfinite(st), axis=0)
    log(f"fusion: медианное покрытие {np.nanmedian(n_cov):.0f} стрипов/px, "
        f"p25-покрытие {np.isfinite(p25).mean():.0%}")
    return med, p25, n_cov


def stage_validate(med, p25, tr, W, H):
    ic = pd.read_csv(HOME / 'scripts/data/icesat2_clean.csv')
    m = ((ic.lon >= PILOT[0]) & (ic.lon <= PILOT[2]) &
         (ic.lat >= PILOT[1]) & (ic.lat <= PILOT[3]))
    ic = ic[m].copy()
    log(f"icesat2_clean в пилоте: {len(ic)} точек")
    if len(ic) < 10:
        return None
    rows, cols = rasterio.transform.rowcol(tr, ic.lon.values, ic.lat.values)
    rows = np.clip(rows, 0, H - 1); cols = np.clip(cols, 0, W - 1)
    idx = (rows, cols)
    ic['h_strip_p25'] = p25[idx]
    ic['h_strip_med'] = med[idx]
    # v2-сетка и FABDEM (датум БС-77) — сырые стрипы эллипсоидальные, поэтому
    # для сравнения методов каждый приводим к полевому эталону своим сдвигом
    # (med offset) — метрика MAD после сдвига
    with rasterio.open(HOME / 'scripts/data/consensus_dtm_5.71/mclf_v2_ground_bs77_cm_5.74.tif') as s:
        v2 = s.read(1).astype('float32') / 100.0
        v2[s.read(1) == -32768] = np.nan
    with rasterio.open(HOME / 'scripts/data/consensus_dtm_5.71/bareearth_bs77_cm_5.71.tif') as s:
        fab = s.read(1).astype('float32') / 100.0
        fab[s.read(1) == -32768] = np.nan
    ic['h_v2'] = v2[idx]
    ic['h_fab'] = fab[idx]

    res = dict(n=int(len(ic)))
    for name, col in (('strip_raw_med', 'h_strip_med'),
                      ('strip_p25', 'h_strip_p25'),
                      ('strip_p25+medoffset', 'h_strip_p25'),
                      ('mclf_v2_30m', 'h_v2'),
                      ('fabdem_30m', 'h_fab')):
        d = ic[col] - ic.h_icesat_baltic
        off = float(np.nanmedian(d))
        dd = d - off
        res[name] = dict(offset=round(off, 3),
                         mad=round(float(np.nanmedian(np.abs(dd))), 3),
                         med=round(float(np.nanmedian(dd)), 3),
                         p95=round(float(np.nanpercentile(np.abs(dd), 95)), 3))
        log(f"  {name:22s} off {off:+.2f} MAD {res[name]['mad']:.3f} p95 {res[name]['p95']:.2f}")
    # исторический угол: p25 до 2017 vs после (квоты есть в id/дате)
    return res


def main():
    sel_path = CACHE / 'selected.csv'
    sel = stage_select() if not sel_path.exists() else pd.read_csv(sel_path)
    clips, tr, W, H = stage_clip(sel)
    if len(clips) < 3:
        log("FAIL: <3 клипов")
        sys.exit(1)
    med, p25, n_cov = stage_fuse(clips, tr, W, H)
    res = stage_validate(med, p25, tr, W, H)
    # сохраняем fusion-продукты
    for name, a in (('strip_med', med), ('strip_p25', p25)):
        prof = dict(driver='GTiff', width=W, height=H, dtype='int16',
                    crs='EPSG:4326', transform=tr, compress='deflate',
                    nodata=-32768)
        o = np.where(np.isfinite(a), np.rint(a * 100), -32768).astype('int16')
        with rasterio.open(CACHE / f'fusion_{name}_5.75.tif', 'w', **prof) as d:
            d.write(o, 1)
    meta = dict(date=datetime.now().isoformat(), pilot=list(PILOT),
                n_strips=int(len(clips)),
                years=sorted(sel.year.unique().tolist()),
                median_cov_per_px=float(np.nanmedian(n_cov)),
                validation=res)
    OUTJ.write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    log(f"done -> {OUTJ}")


if __name__ == '__main__':
    main()
