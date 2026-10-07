#!/usr/bin/env python3
"""
5.77 шаг B: v2-приор (M-CLF-v2, 30 м, БС-77) на кросс-зоне
Сестрорецк-запад. Репликация v2_prior_pilot_5.76.py с заменой окна:
  GLO-30 тайл N60_00_E030_00 (зона 60.01-60.06N, 30.12-30.24E целиком
  внутри одного тайла; N59 не нужен) -> bilinear на сетку 5.71 (м, без
  датум-сдвига — конвенция обучения 5.74); canopy = dsm - fabdem;
  морфометрия 5.74 на окне с падом 40 px; v2 = fabdem + model.predict.
УРОК 5.74/5.76: reproject только с dst_nodata=np.nan + лог покрытия.
Выход: dem_cache/strips_zone_5.77/prior_v2_2m_5.77.tif
"""
import os, json
from pathlib import Path
from datetime import datetime
import numpy as np
import rasterio
from rasterio.warp import reproject, Resampling
from rasterio.windows import from_bounds, Window
import joblib
import scipy.ndimage as ndi

HOME = Path('/home/z/my-project/spb-arcticdem-pipeline')
ZONE = (30.12, 60.01, 30.24, 60.06)
CACHE = HOME / 'scripts/dem_cache/strips_zone_5.77'
FAB_TIF = HOME / 'scripts/data/consensus_dtm_5.71/bareearth_bs77_cm_5.71.tif'
MODEL = HOME / 'scripts/data/mclf_v2_model_5.74.joblib'
TMP = HOME / 'scripts/dem_cache/_glo30_tmp'
TMP.mkdir(parents=True, exist_ok=True)
FEATS = ['canopy', 'slope', 'rough', 'tpi', 'openness', 'dsm', 'lat', 'lon']
GLO_TILES = ['N60_00_E030_00']
PAD = 40


def log(m):
    print(f"[{datetime.now():%H:%M:%S}] {m}", flush=True)


def main():
    with rasterio.open(FAB_TIF) as src:
        full = src.read(1).astype('float32')
        nodata = src.nodata
        gtr, gcrs = src.transform, src.crs
        GH, GW = src.shape
    fab_full = full / 100.0
    fab_full[full == nodata] = np.nan
    W0, S0, E0, N0 = ZONE
    P = PAD * 0.0002777777778
    r_lo, c_lo = rasterio.transform.rowcol(gtr, W0 - P, N0 + P)
    r_hi, c_hi = rasterio.transform.rowcol(gtr, E0 + P, S0 - P)
    r0, r1 = max(int(r_lo) - 2, 0), min(int(r_hi) + 2, GH)
    c0, c1 = max(int(c_lo) - 2, 0), min(int(c_hi) + 2, GW)
    win = Window(c0, r0, c1 - c0, r1 - r0)
    wtr = rasterio.windows.transform(win, gtr)
    fab = fab_full[r0:r1, c0:c1]
    H, W = fab.shape
    log(f"окно 30м: {W}x{H}, {wtr}")

    os.environ['AWS_NO_SIGN_REQUEST'] = 'YES'
    dsm = np.full((H, W), np.nan, dtype='float32')
    for t in GLO_TILES:
        f = TMP / f"{t}.tif"
        if not f.exists():
            raise FileNotFoundError(
                f"{f} отсутствует — тайл в git (_glo30_tmp), проверь clone")
        with rasterio.open(f) as s:
            a = s.read(1).astype('float32')
            buf = np.full((H, W), np.nan, dtype='float32')
            reproject(a, buf, src_transform=s.transform, src_crs=s.crs,
                      dst_transform=wtr, dst_crs=gcrs,
                      resampling=Resampling.bilinear, dst_nodata=np.nan)
            log(f"  {t}: покрытие окна {np.isfinite(buf).mean():.1%}, "
                f"med {np.nanmedian(buf):.2f} м")
            dsm = np.where(np.isfinite(buf) & ~np.isfinite(dsm), buf, dsm)
    log(f"GLO-30 окно: {np.isfinite(dsm).mean():.1%} валидно, "
        f"med {np.nanmedian(dsm):.2f} м")

    canopy = dsm - fab
    log(f"канопи: med {np.nanmedian(canopy):.2f} м")

    f = np.where(np.isfinite(fab), fab, 0.0)
    mask = np.isfinite(fab).astype('float32')
    g_dy, g_dx = np.gradient(f, 0.00027778 * 111320 * np.cos(np.deg2rad(60.0)),
                             0.00027778 * 111320)
    slope = np.degrees(np.arctan(np.hypot(g_dx, g_dy)))
    m1 = ndi.uniform_filter(f * mask, 3) / np.maximum(ndi.uniform_filter(mask, 3), 1e-6)
    m2 = ndi.uniform_filter(f * f * mask, 3) / np.maximum(ndi.uniform_filter(mask, 3), 1e-6)
    rough = np.sqrt(np.maximum(m2 - m1 * m1, 0))
    w9 = ndi.uniform_filter(mask, 9)
    tpi = f - ndi.uniform_filter(f * mask, 9) / np.maximum(w9, 1e-6)
    fmin = ndi.minimum_filter(np.where(np.isfinite(fab), fab, 1e9), 5)
    openness = np.where(np.isfinite(fab), f - np.where(fmin < 1e8, fmin, f), np.nan)
    slope[np.isnan(fab)] = np.nan; rough[np.isnan(fab)] = np.nan
    tpi[np.isnan(fab)] = np.nan

    model = joblib.load(MODEL)
    ys = wtr.f + wtr.e * (np.arange(H, dtype='float32')[:, None] + 0.5)
    ys = np.broadcast_to(ys, (H, W))
    xs = wtr.c + wtr.a * (np.arange(W, dtype='float32')[None, :] + 0.5)
    xs = np.broadcast_to(xs, (H, W))
    cols = dict(canopy=canopy, slope=slope, rough=rough, tpi=tpi,
                openness=openness, dsm=dsm, lat=ys, lon=xs)
    ok = np.isfinite(fab)
    Xg = np.stack([cols[fn][ok] for fn in FEATS], 1)
    v2 = np.full((H, W), np.nan, dtype='float32')
    v2[ok] = (fab[ok] + model.predict(Xg).astype('float32')).astype('float32')
    log(f"v2_30m: med {np.nanmedian(v2):.2f} м БС-77, "
        f"{ok.mean():.1%} валидно")

    PW = int((ZONE[2] - ZONE[0]) / 0.0000178)
    PH = int((ZONE[3] - ZONE[1]) / 0.0000178)
    ptr = rasterio.transform.from_origin(ZONE[0], ZONE[3], 0.0000178, 0.0000178)
    prior2 = np.full((PH, PW), np.nan, dtype='float32')
    reproject(v2, prior2, src_transform=wtr, src_crs=gcrs,
              dst_transform=ptr, dst_crs='EPSG:4326',
              resampling=Resampling.bilinear, dst_nodata=np.nan)
    prof2 = dict(driver='GTiff', width=PW, height=PH, count=1, dtype='float32',
                 crs='EPSG:4326', transform=ptr, compress='deflate', nodata=-32768)
    with rasterio.open(CACHE / 'prior_v2_2m_5.77.tif', 'w', **prof2) as d:
        d.write(np.where(np.isfinite(prior2), prior2, -32768), 1)
    log(f"prior 2м: {np.isfinite(prior2).mean():.1%} валидно, "
        f"med {np.nanmedian(prior2):.2f} м")
    (CACHE / 'prior_v2_5.77.json').write_text(json.dumps(
        dict(window=dict(r0=r0, r1=r1, c0=c0, c1=c1), tiles=GLO_TILES,
             dsm_valid=round(float(np.isfinite(dsm).mean()), 4),
             canopy_med=round(float(np.nanmedian(canopy)), 3),
             v2_med=round(float(np.nanmedian(v2)), 3)), indent=1))
    log('done')


if __name__ == '__main__':
    main()
