#!/usr/bin/env python3
"""
5.74-A шаг 1: признаки M-CLF-v2.

Догрузка GLO-30 (6 тайлов AWS, публичный S3) -> warp на сетку 5.71
(bareearth_bs77_cm_5.71.tif: EPSG:4326, 30м, 59.55-60.30N, 28.35-31.05E)
-> DSM + канопи (GLO-30 - FABDEM) + морфометрия FABDEM
   (slope, roughness, TPI 9x9, openness-прокси) -> сэмпл на
   icesat2_clean (33908) и 24 якоря archi.

Выходы:
  scripts/data/consensus_dtm_5.71/glo30_dsm_bs77_cm_5.74.tif   (Int16 см)
  scripts/data/consensus_dtm_5.71/canopy_bias_cm_5.74.tif      (Int16 см)
  scripts/data/consensus_dtm_5.71/fabdem_terrain_5.74.npz      (slope и пр.)
  scripts/data/mclf_v2_features_5.74.csv                       (сэмпл признаков)
"""
import os, json, subprocess, sys
import urllib.request
from pathlib import Path
import numpy as np
import pandas as pd
import rasterio
from rasterio.warp import reproject, Resampling
from rasterio.windows import from_bounds

os.environ.setdefault('GDAL_HTTP_MAX_RETRY', '8')
HOME = Path('/home/z/my-project/repo')
D = HOME / 'scripts/data/consensus_dtm_5.71'
FAB = D / 'bareearth_bs77_cm_5.71.tif'
DSM = D / 'glo30_dsm_bs77_cm_5.74.tif'
CANOPY = D / 'canopy_bias_cm_5.74.tif'
TERR = D / 'fabdem_terrain_5.74.npz'
SAMPLE = HOME / 'scripts/data/mclf_v2_features_5.74.csv'
GLO_TILES = [f"N{n}_00_E{e:03d}_00" for n in (59, 60) for e in (28, 29, 30)]


def glo30_url(t):
    return (f"https://copernicus-dem-30m.s3.amazonaws.com/"
            f"Copernicus_DSM_COG_10_{t}_DEM/Copernicus_DSM_COG_10_{t}_DEM.tif")


def log(m):
    print(m, flush=True)


def download(url, dst):
    if dst.exists() and dst.stat().st_size > 100000:
        log(f"  skip {dst.name}")
        return
    log(f"  GET {Path(url).name} ({dst.stat().st_size if dst.exists() else 0} B)")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=600) as r, open(dst, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)


def main():
    # --- 1) сетка 5.71 ---
    with rasterio.open(FAB) as src:
        fab_raw = src.read(1)
        nodata = src.nodata
        prof = src.profile.copy()
        transform, crs, W, H = src.transform, src.crs, src.width, src.height
    fab = fab_raw.astype('float32') / 100.0
    fab[fab_raw == nodata] = np.nan
    log(f"сетка {W}x{H}, fabs: {np.nanpercentile(fab, [1, 50, 99]).round(2)}")

    # --- 2) GLO-30 -> тайлы -> warp на сетку 5.71 ---
    # Фикс урока 5.74: reproject без dst_nodata заливает нулями области вне
    # покрытия тайла -> каждый следующий тайл затирал предыдущие.
    # Правильный паттерн: буфер с dst_nodata=np.nan + isfinite-комбинация.
    tmp = D / '_glo30_tmp'; tmp.mkdir(exist_ok=True)
    mosaicked = np.full((H, W), np.nan, dtype='float32')
    for t in GLO_TILES:
        tif = tmp / f"{t}.tif"
        download(glo30_url(t), tif)
        with rasterio.open(tif) as s:
            band = s.read(1).astype('float32')
            nd = s.nodata
            if nd is not None:
                band[band == nd] = np.nan
            # в 5.71 GLO-30 применялся со сдвигом -0.131 (offset_to_bs77);
            # здесь храним сырой DSM без сдвига — калибровка отдельно в модели
            buf = np.full((H, W), np.nan, dtype='float32')
            reproject(band, buf, src_transform=s.transform, src_crs=s.crs,
                      dst_transform=transform, dst_crs=crs,
                      dst_area_or_point='Area', resampling=Resampling.bilinear,
                      dst_nodata=np.nan)
            log(f"  {t}: покрытие сетки {np.isfinite(buf).mean():.1%}")
            mosaicked = np.where(np.isfinite(buf) & ~np.isfinite(mosaicked),
                                 buf, mosaicked)
    log(f"GLO-30 mosaic: {np.isfinite(mosaicked).sum() / mosaicked.size:.1%} валидно")

    # канопи = DSM - FABDEM (обе в м, без датум-сдвигов: сдвиги ~0.1 м тут шум)
    canopy = mosaicked - fab
    log(f"канопи: med {np.nanmedian(canopy):.2f} м, p90 {np.nanpercentile(canopy, 90):.2f}")

    prof.update(dtype='int16', nodata=-32768, compress='deflate', predictor=2)
    for name, arr in (('glo30_dsm_bs77_cm_5.74.tif', mosaicked),
                      ('canopy_bias_cm_5.74.tif', canopy)):
        out = np.empty_like(arr)
        out[:] = -32768
        fin = np.isfinite(arr)
        out[fin] = np.clip(np.rint(arr[fin] * 100), -32000, 32000)
        with rasterio.open(D / name, 'w', **prof) as dst:
            dst.write(out.astype('int16'), 1)
        log(f"  wrote {name}")

    # --- 3) морфометрия FABDEM (м) ---
    log("морфометрия FABDEM...")
    f = np.where(np.isfinite(fab), fab, 0.0)
    mask = np.isfinite(fab).astype('float32')
    import scipy.ndimage as ndi
    # slope: градиент по сетке (dz/dx, dz/dy) в градусах
    g_dy, g_dx = np.gradient(f, 0.00027778 * 111320 * np.cos(np.deg2rad(59.9)),
                             0.00027778 * 111320)
    slope = np.degrees(np.arctan(np.hypot(g_dx, g_dy)))
    # roughness: std в 3x3 (uniform_filter среднее -> E[x^2]-E[x]^2)
    m1 = ndi.uniform_filter(f * mask, 3) / np.maximum(ndi.uniform_filter(mask, 3), 1e-6)
    m2 = ndi.uniform_filter(f * f * mask, 3) / np.maximum(ndi.uniform_filter(mask, 3), 1e-6)
    rough = np.sqrt(np.maximum(m2 - m1 * m1, 0))
    # TPI 9x9: fab - mean(fab, 9x9)
    w9 = ndi.uniform_filter(mask, 9)
    tpi = f - ndi.uniform_filter(f * mask, 9) / np.maximum(w9, 1e-6)
    # открытость локальная: fab - min в 5x5 (эмбанкмент/ямы)
    fmin = ndi.minimum_filter(np.where(np.isfinite(fab), fab, 1e9), 5)
    openness = np.where(np.isfinite(fab), f - np.where(fmin < 1e8, fmin, f), np.nan)
    slope[np.isnan(fab)] = np.nan; rough[np.isnan(fab)] = np.nan
    tpi[np.isnan(fab)] = np.nan
    np.savez_compressed(TERR, slope=slope.astype('float32'),
                        rough=rough.astype('float32'),
                        tpi=tpi.astype('float32'),
                        openness=openness.astype('float32'))
    log(f"  slope med {np.nanmedian(slope):.3f}deg, rough med {np.nanmedian(rough):.2f}м")

    # --- 4) сэмпл на icesat2_clean + archi ---
    def sample(pts_lat, pts_lon):
        rows, cols = rasterio.transform.rowcol(transform, pts_lon, pts_lat)
        rows = np.clip(rows, 0, H - 1); cols = np.clip(cols, 0, W - 1)
        idx = (rows, cols)
        return dict(fabdem=fab[idx], dsm=mosaicked[idx], canopy=canopy[idx],
                    slope=slope[idx], rough=rough[idx], tpi=tpi[idx],
                    openness=openness[idx])

    ic = pd.read_csv(HOME / 'scripts/data/icesat2_clean.csv')
    s_ic = sample(ic.lat.values, ic.lon.values)
    out = ic[['lat', 'lon', 'h_icesat_baltic', 'h_model', 'dH_corrected']].copy()
    out.rename(columns={'h_icesat_baltic': 'h_ref', 'dH_corrected': 'dH_model'}, inplace=True)
    for k, v in s_ic.items(): out[k] = v
    out['src'] = 'icesat'

    ar = pd.read_csv(HOME / 'scripts/data/archi_final_clean_v2.csv')
    # per-mark сэмпл (у archi есть lat/lon per-mark)
    s_ar = sample(ar.lat.values, ar.lon.values)
    ar_out = ar[['lat', 'lon', 'value', 'h_model', 'dH', 'project_name']].copy()
    ar_out.rename(columns={'value': 'h_ref', 'dH': 'dH_model'}, inplace=True)
    for k, v in s_ar.items(): ar_out[k] = v
    ar_out['src'] = 'archi'
    full = pd.concat([out, ar_out], ignore_index=True)
    full.to_csv(SAMPLE, index=False)
    log(f"сэмпл: {SAMPLE} ({len(out)} icesat + {len(ar_out)} archi)")
    log(f"  fabdem NaN в сэмпле: {full.fabdem.isna().mean():.2%}")

    meta = dict(n_icesat=int(len(out)), n_archi=int(len(ar_out)),
                tiles=[str(t) for t in GLO_TILES],
                canopy_med_m=float(np.nanmedian(canopy)),
                canopy_p90_m=float(np.nanpercentile(canopy, 90)))
    (D / 'features_meta_5.74.json').write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    log(json.dumps(meta, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
