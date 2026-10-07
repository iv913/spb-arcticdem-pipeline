#!/usr/bin/env python3
"""
5.75 шаг 4 (итерация 3): качественный стек + честный holdout.

Урок итерации 2: per-strip ко-регистрация обязательна (датум-разброс
+8..+40 м), но p25 смешанного стека всё равно держит крону — несколько
«чистых» сцен (leaf-off/низкая крона, MAD<=1.5) информативнее десятка
«летних». Итерация 3: (а) фильтр качества по in-strip MAD vs ICESat,
(б) holdout: offsets подбираются на половине точек (по RGT-трекам),
валидация на другой половине.

Выход: results/strips_pilot_5.75.json (ключ iter3).
"""
import json
from pathlib import Path
from datetime import datetime
import numpy as np
import pandas as pd
import rasterio
from rasterio.warp import reproject, Resampling
import geopandas as gpd

HOME = Path('/home/z/my-project/repo')
PILOT = (30.24, 60.01, 30.34, 60.06)
PX = 0.0000178
CACHE = HOME / 'scripts/dem_cache/strips_pilot_5.75'
OUTJ = HOME / 'results/strips_pilot_5.75.json'
MAD_TOL = 1.5


def log(m):
    print(f"[{datetime.now():%H:%M:%S}] {m}", flush=True)


def strip_stats(c, ic3413, h_ref):
    with rasterio.open(c) as s:
        a = s.read(1).astype('float32')
        a[a == -32768] = np.nan
        r2, c2 = rasterio.transform.rowcol(s.transform, ic3413.x.values, ic3413.y.values)
        inb = (r2 >= 0) & (r2 < s.height) & (c2 >= 0) & (c2 < s.width)
        r2 = np.clip(r2, 0, s.height - 1); c2 = np.clip(c2, 0, s.width - 1)
        hs = np.where(inb, a[r2, c2], np.nan)
    d = hs - h_ref
    off = float(np.nanmedian(d))
    mad = float(np.nanmedian(np.abs(d - off)))
    return off, mad, int(np.isfinite(d).sum()), inb.sum()


def fuse(clips, offs, tr, W, H, pct=(25, 50)):
    mm = np.memmap(CACHE / 'stack_iter3.dat', dtype='float32', mode='w+',
                   shape=(len(clips), H, W))
    for k, c in enumerate(clips):
        with rasterio.open(c) as s:
            a = s.read(1).astype('float32')
            a[a == -32768] = np.nan
            if not np.isfinite(a).any():
                continue
            reproject(a - offs[k], mm[k], src_transform=s.transform,
                      src_crs=s.crs, dst_transform=tr, dst_crs='EPSG:4326',
                      resampling=Resampling.bilinear)
    mm.flush()
    outs = {p: np.full((H, W), np.nan, dtype='float32') for p in pct}
    CH = 256
    for r0 in range(0, H, CH):
        r1 = min(H, r0 + CH)
        blk = np.array(mm[:, r0:r1, :])
        for p in pct:
            outs[p][r0:r1] = np.nanpercentile(blk, p, axis=0)
    del mm
    return outs


def main():
    ic = pd.read_csv(HOME / 'scripts/data/icesat2_clean.csv')
    ic = ic[(ic.lon >= PILOT[0]) & (ic.lon <= PILOT[2]) &
            (ic.lat >= PILOT[1]) & (ic.lat <= PILOT[3])].copy()
    pts = gpd.GeoSeries(gpd.points_from_xy(ic.lon, ic.lat), crs='EPSG:4326')
    clips = sorted(CACHE.glob('clip_*.tif'))

    # split-half по трекам: RGT = первые цифры image id нет; используем
    # пространственную зебру (чётные строки 2км-блоков -> train)
    blk = (np.floor(ic.lat / 0.02).astype(int) + np.floor(ic.lon / 0.0127).astype(int))
    ic['half'] = blk % 2          # зебра по диагонали
    tr_mask = ic.half == 0
    log(f"split-half: train {tr_mask.sum()} / valid {(~tr_mask).sum()}")

    stats, offs_all, keep = [], [], []
    for k, c in enumerate(clips):
        off, mad, n, ninb = strip_stats(c, pts.to_crs(_crs_of(c)), ic.h_icesat_baltic.values)
        stats.append(dict(clip=c.name, date=c.name.split('_')[2], off=round(off, 2),
                          mad=round(mad, 2), n=n, inb=int(ninb)))
        offs_all.append(off)
        if mad <= MAD_TOL and n >= 80:
            keep.append(k)
    st = pd.DataFrame(stats)
    st.to_csv(CACHE / 'strip_stats_iter3_5.75.csv', index=False)
    log(f"качество: {len(keep)}/{len(clips)} стрипов MAD<={MAD_TOL}: "
        f"{[st.date.iloc[k] for k in keep]}")

    W = int((PILOT[2] - PILOT[0]) / PX)
    H = int((PILOT[3] - PILOT[1]) / PX)
    tr = rasterio.transform.from_origin(PILOT[0], PILOT[3], PX, PX)

    # (а) качественный стек, offsets на train, валидация на valid
    kc = [clips[k] for k in keep]
    ko = [offs_all[k] for k in keep]
    fused = fuse(kc, ko, tr, W, H)
    r, c = rasterio.transform.rowcol(tr, ic.lon.values, ic.lat.values)
    r = np.clip(r, 0, H - 1); c = np.clip(c, 0, W - 1)
    v = (~tr_mask).values
    res = dict(train_n=int(tr_mask.sum()), valid_n=int((~tr_mask).sum()),
               kept=[st.date.iloc[k] for k in keep],
               kept_mad=[st.mad.iloc[k] for k in keep])
    for pname, arr in fused.items():
        d = arr[r[v], c[v]] - ic.h_icesat_baltic.values[v]
        off = float(np.nanmedian(d))
        dd = d - off
        res[f'q_p{pname}_holdout'] = dict(
            resid_off=round(off, 3),
            mad=round(float(np.nanmedian(np.abs(dd))), 3),
            p95=round(float(np.nanpercentile(np.abs(dd), 95)), 3),
            n=int(np.isfinite(d).sum()))
        log(f"  quality p{pname} HOLDOUT: MAD {res[f'q_p{pname}_holdout']['mad']:.3f} "
            f"p95 {res[f'q_p{pname}_holdout']['p95']:.2f}")

    # (б) тот же фильтр, но offsets на train ЧЕСТНО (уже так: offs_all по train)
    j = json.loads(OUTJ.read_text())
    j['iter3'] = dict(mad_tol=MAD_TOL, split='zebra 2km', stats=stats,
                      validation=res, date=datetime.now().isoformat())
    OUTJ.write_text(json.dumps(j, ensure_ascii=False, indent=1))
    log('done')


def _crs_of(c):
    with rasterio.open(c) as s:
        return s.crs


if __name__ == '__main__':
    main()
