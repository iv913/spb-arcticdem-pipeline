#!/usr/bin/env python3
"""
5.75 шаг 3 (итерация 2): per-strip ко-регистрация перед fusion.

Урок итерации 1: наивный p25 стека 20 стрипов дал MAD 1.12 (против
v2-сетки 0.48) — у каждого стрипа свой датум/геолокационный сдвиг
(стереопары, разные витки, геоид), они не усредняются процентилью.

Метод: offset_k = median(h_strip_k(ICESat ground pts) − h_icesat)
по icesat2_clean в пилоте (378 точек); corrected_k = strip_k − offset_k;
затем стек -> med / p25. Валидация — та же выборка (offsets ~ 0).
Выход: результаты в results/strips_pilot_5.75.json (ключ coreg).
"""
import json
from pathlib import Path
from datetime import datetime
import numpy as np
import pandas as pd
import rasterio
from rasterio.warp import reproject, Resampling

HOME = Path('/home/z/my-project/repo')
PILOT = (30.24, 60.01, 30.34, 60.06)
PX = 0.0000178
CACHE = HOME / 'scripts/dem_cache/strips_pilot_5.75'
OUTJ = HOME / 'results/strips_pilot_5.75.json'


def log(m):
    print(f"[{datetime.now():%H:%M:%S}] {m}", flush=True)


def main():
    ic = pd.read_csv(HOME / 'scripts/data/icesat2_clean.csv')
    ic = ic[(ic.lon >= PILOT[0]) & (ic.lon <= PILOT[2]) &
            (ic.lat >= PILOT[1]) & (ic.lat <= PILOT[3])].copy()
    log(f"ICESat ground в пилоте: {len(ic)}")

    clips = sorted(CACHE.glob('clip_*.tif'))
    W = int((PILOT[2] - PILOT[0]) / PX)
    H = int((PILOT[3] - PILOT[1]) / PX)
    tr = rasterio.transform.from_origin(PILOT[0], PILOT[3], PX, PX)

    # per-strip offset по полевым точкам (выборка стрипа на точках ICESat)
    offs = []
    for k, c in enumerate(clips):
        with rasterio.open(c) as s:
            a = s.read(1).astype('float32')
            a[a == -32768] = np.nan
            r2, c2 = rasterio.transform.rowcol(s.transform, ic.lon.values, ic.lat.values)
            r2 = np.clip(r2, 0, s.height - 1)
            c2 = np.clip(c2, 0, s.width - 1)
            hs = a[r2, c2]
        d = hs - ic.h_icesat_baltic.values
        off = float(np.nanmedian(d))
        offs.append(dict(clip=c.name, date=c.name.split('_')[2],
                         n=int(np.isfinite(d).sum()), offset_m=round(off, 3),
                         spread=round(float(np.nanmedian(np.abs(d - off))), 3)))
        log(f"  {c.name[6:20]}: off {off:+.2f} (n={offs[-1]['n']}, MAD {offs[-1]['spread']:.2f})")
    off_df = pd.DataFrame(offs)
    off_df.to_csv(CACHE / 'strip_offsets_5.75.csv', index=False)

    # fusion ко-регистрированных слоёв (memmap + блоки, как в 5.75 шаг 2)
    mm = np.memmap(CACHE / 'stack_coreg.dat', dtype='float32', mode='w+',
                   shape=(len(clips), H, W))
    for k, c in enumerate(clips):
        with rasterio.open(c) as s:
            a = s.read(1).astype('float32')
            a[a == -32768] = np.nan
            if not np.isfinite(a).any():
                continue
            out = mm[k]
            reproject(a - offs[k]['offset_m'], out,
                      src_transform=s.transform, src_crs=s.crs,
                      dst_transform=tr, dst_crs='EPSG:4326',
                      resampling=Resampling.bilinear)
    mm.flush()
    med = np.full((H, W), np.nan, dtype='float32')
    p25 = np.full((H, W), np.nan, dtype='float32')
    CH = 256
    for r0 in range(0, H, CH):
        r1 = min(H, r0 + CH)
        blk = np.array(mm[:, r0:r1, :])
        med[r0:r1] = np.nanmedian(blk, axis=0)
        p25[r0:r1] = np.nanpercentile(blk, 25, axis=0)
    del mm

    # валидация: те же точки (in-sample по offset, честно указываем)
    r, c = rasterio.transform.rowcol(tr, ic.lon.values, ic.lat.values)
    r = np.clip(r, 0, H - 1); c = np.clip(c, 0, W - 1)
    idx = (r, c)
    res = dict(n=int(len(ic)), note='offsets подобраны на этих же точках (in-sample); MAD завышен для внешней оценки не будет, p95 честен')
    for name, arr in (('coreg_med', med), ('coreg_p25', p25)):
        d = arr[idx] - ic.h_icesat_baltic.values
        off = float(np.nanmedian(d))
        dd = d - off
        res[name] = dict(resid_offset=round(off, 3),
                         mad=round(float(np.nanmedian(np.abs(dd))), 3),
                         p95=round(float(np.nanpercentile(np.abs(dd), 95)), 3))
        log(f"  {name}: MAD {res[name]['mad']:.3f} p95 {res[name]['p95']:.2f}")

    j = json.loads(OUTJ.read_text())
    j['coreg'] = dict(strip_offsets=offs, validation=res,
                      date=datetime.now().isoformat())
    OUTJ.write_text(json.dumps(j, ensure_ascii=False, indent=1))
    # сохранить продукты
    for name, a in (('coreg_med', med), ('coreg_p25', p25)):
        prof = dict(driver='GTiff', count=1, width=W, height=H, dtype='int16',
                    crs='EPSG:4326', transform=tr, compress='deflate',
                    nodata=-32768)
        o = np.where(np.isfinite(a), np.rint(a * 100), -32768).astype('int16')
        with rasterio.open(CACHE / f'fusion_{name}_5.75.tif', 'w', **prof) as d:
            d.write(o, 1)
    log('done')


if __name__ == '__main__':
    main()
