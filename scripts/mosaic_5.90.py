#!/usr/bin/env python3
"""5.90: мозаика «север 5.84 + юг 5.90» — южное расширение тиража 2м.

Рецепт мозаики 5.84 заморожен (mosaic_5.84.py), изменения только:
  - ZONES += 63 южные зоны S* (5.90, prior-only: FABDEM+model, стек
    ArcticDEM на юге отсутствует — 1-2 стрипа против >=6 на севере);
  - выходы download/mosaic_5.90/ (mosaic/quality/trust/density *_5.90);
  - поблочная запись с докаткой (sidecar): перезапуск продолжает с
    последнего завершённого чанка (лимит 10 мин на вызов в песочнице).
Слияние перекрытий: max n_cov, при равенстве min sigma, далее порядок.
Quality prior-only зон синтезируется (n_cov=0, sigma=nodata, canopy30 =
GLO30 DSM - FABDEM DTM, рецептура synth_quality 5.84).
"""
import json
import os
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import rasterio
from rasterio.vrt import WarpedVRT
from rasterio.enums import Resampling
from rasterio.warp import reproject

HOME = Path(__file__).resolve().parents[1]
OUTD = HOME / 'download/mosaic_5.90'
OUTD.mkdir(parents=True, exist_ok=True)
PRODD = HOME / 'download/products_5.83'
PX = 0.0000178
NOD = -32768
QNOD = 65535
CHUNK = 256
CITY_BBOX = (29.20, 59.55, 31.20, 60.30)
DENS_MIN = 6

ZONES = [
    ['PILOT', (30.24, 60.01, 30.34, 60.06), 'regen 5.75/5.76 пилот дюны'],
    ['SESTR', (30.12, 60.01, 30.24, 60.06), 'regen 5.77/5.79 Сестрорецк-запад'],
    ['RZHEV', (30.36, 60.00, 30.46, 60.06), 'regen 5.79 A Ржевка/Охта'],
    ['OZER',  (30.07, 59.98, 30.17, 60.04), 'regen 5.79 B Озерки/Коломяги'],
    ['STREL', (30.05, 59.92, 30.15, 59.97), 'regen 5.79 Z1 Стрельна/Петергоф-зап'],
    ['AVTOV', (30.21, 59.96, 30.31, 60.01), 'regen 5.79 Z2 Автово/Дачное'],
    ['LIGO',  (30.21, 59.90, 30.31, 59.95), 'regen 5.79 Z3 Лигово-юг'],
] + [[f'N{i}', None, '5.83 партия 1/2'] for i in range(1, 15)]

for line in (HOME / 'results/batch_progress_5.83.jsonl').read_text().splitlines():
    try:
        rec = json.loads(line)
    except Exception:
        continue
    import re as _re
    if _re.fullmatch(r'S\d+', str(rec.get('tag', ''))):
        ZONES.append([rec['tag'], [float(x) for x in rec['bbox']],
                      '5.90 юг, prior-only'])


def ftag(tag):
    return tag if not tag.startswith('N') else f"{tag}_5.83"


def zbounds(tag):
    b = next(z[1] for z in ZONES if z[0] == tag)
    if b is not None:
        return b
    return tuple(json.loads(
        (HOME / f'results/zone_{ftag(tag)}_5.83.json').read_text())['zone'])


for z in ZONES:
    if z[1] is None:
        z[1] = zbounds(z[0])


def log(m):
    print(f"[{datetime.now():%H:%M:%S}] {m}", flush=True)


def write_south_quality(tag, b):
    """Материализация quality_5.83_{TAG}.tif для prior-only южной зоны:
    n_cov=0, sigma=nodata, canopy30 = (GLO30 DSM - FABDEM DTM), 2м сетка
    зоны (рецептура synth_quality 5.84, но на диск)."""
    FAB = HOME / 'scripts/data/consensus_dtm_5.71/bareearth_bs77_cm_5.71.tif'
    TMP = HOME / 'scripts/dem_cache/_glo30_tmp'
    dst = PRODD / f'quality_5.83_{tag}.tif'
    W = int(np.ceil((b[2] - b[0]) / PX))
    H = int(np.ceil((b[3] - b[1]) / PX))
    tr = rasterio.transform.from_origin(b[0], b[3], PX, PX)
    q = np.full((3, H, W), QNOD, dtype='uint16')
    with rasterio.open(FAB) as fsrc:
        fb_w = rasterio.windows.from_bounds(b[0] - 0.02, b[1] - 0.02,
                                            b[2] + 0.02, b[3] + 0.02,
                                            fsrc.transform).round_lengths()
        fb = fsrc.read(1, window=fb_w).astype('float32')
        fb[fb == fsrc.nodata] = np.nan
        fb = fb / 100.0          # bareearth хранит СМ (int16) -> метры
        ftr = rasterio.windows.transform(fb_w, fsrc.transform)
        fab = np.full((H, W), np.nan, dtype='float32')
        reproject(fb, fab, src_transform=ftr, src_crs=fsrc.crs,
                  dst_transform=tr, dst_crs='EPSG:4326',
                  resampling=Resampling.bilinear, dst_nodata=np.nan)
    P = 40 * 0.0002777777778
    dsm = np.full((H, W), np.nan, dtype='float32')
    for n in (int(np.floor(b[1] - P)), int(np.floor(b[3] + P))):
        for e in (int(np.floor(b[0] - P)), int(np.floor(b[2] + P))):
            f = TMP / f"N{n:02d}_00_E{e:03d}_00.tif"
            if not f.exists():
                continue
            with rasterio.open(f) as sr:
                a = sr.read(1).astype('float32')
                buf = np.full((H, W), np.nan, dtype='float32')
                reproject(a, buf, src_transform=sr.transform, src_crs=sr.crs,
                          dst_transform=tr, dst_crs='EPSG:4326',
                          resampling=Resampling.bilinear, dst_nodata=np.nan)
                dsm = np.where(np.isfinite(buf) & ~np.isfinite(dsm), buf, dsm)
    can = dsm - fab
    ok = np.isfinite(can)
    q[2] = np.where(ok, np.clip(np.rint(np.where(ok, can, 0) * 100),
                                0, QNOD - 1), QNOD).astype('uint16')
    prof = dict(driver='GTiff', width=W, height=H, count=3, dtype='uint16',
                crs='EPSG:4326', transform=tr, compress='deflate',
                nodata=QNOD)
    with rasterio.open(dst, 'w', **prof) as d:
        d.write(q)
    print(f"  quality: {tag} ({W}x{H})", flush=True)


def synth_quality(W, H, tr, tag):
    """quality prior-only зоны без файла: n_cov=0, sigma=nodata,
    canopy30 = (GLO30 DSM - FABDEM DTM) на 30м -> 2м сетка зоны."""
    FAB = HOME / 'scripts/data/consensus_dtm_5.71/bareearth_bs77_cm_5.71.tif'
    TMP = HOME / 'scripts/dem_cache/_glo30_tmp'
    q = np.full((3, H, W), QNOD, dtype='uint16')
    if not FAB.exists() or not list(TMP.glob('N*.tif')):
        return q
    with rasterio.open(FAB) as fsrc:
        fb = fsrc.read(1).astype('float32')
        fb[fb == fsrc.nodata] = np.nan
        fab = np.full((H, W), np.nan, dtype='float32')
        reproject(fb, fab, src_transform=fsrc.transform, src_crs=fsrc.crs,
                  dst_transform=tr, dst_crs='EPSG:4326',
                  resampling=Resampling.bilinear, dst_nodata=np.nan)
    P = 40 * 0.0002777777778
    b = zbounds(tag)
    dsm = np.full((H, W), np.nan, dtype='float32')
    for n in (int(np.floor(b[1] - P)), int(np.floor(b[3] + P))):
        for e in (int(np.floor(b[0] - P)), int(np.floor(b[2] + P))):
            f = TMP / f"N{n:02d}_00_E{e:03d}_00.tif"
            if not f.exists():
                continue
            with rasterio.open(f) as s:
                a = s.read(1).astype('float32')
                buf = np.full((H, W), np.nan, dtype='float32')
                reproject(a, buf, src_transform=s.transform, src_crs=s.crs,
                          dst_transform=tr, dst_crs='EPSG:4326',
                          resampling=Resampling.bilinear, dst_nodata=np.nan)
                dsm = np.where(np.isfinite(buf) & ~np.isfinite(dsm), buf, dsm)
    can = dsm - fab
    ok = np.isfinite(can)
    q[2] = np.where(ok, np.clip(np.rint(np.where(ok, can, 0) * 100),
                                0, QNOD - 1), QNOD).astype('uint16')
    return q


def density_grid():
    import requests
    from shapely.geometry import shape
    cache = HOME / 'results/density_footprints_5.84.json'
    if cache.exists():
        boxes = json.loads(cache.read_text())
    else:
        r = requests.post("https://stac.pgc.umn.edu/api/v1/search", json={
            "collections": ["arcticdem-strips-s2s041-2m"],
            "bbox": list(CITY_BBOX), "limit": 1000}, timeout=180)
        r.raise_for_status()
        boxes = []
        for it in r.json().get('features', []):
            p = it['properties']
            v = p.get('pgc:valid_area_percent') or 0
            c = p.get('pgc:cloud_area_percent') or 1
            if v >= 0.5 and c <= 0.3:
                boxes.append(list(shape(it['geometry']).bounds))
        cache.write_text(json.dumps(boxes))
    gx = np.arange(CITY_BBOX[0], CITY_BBOX[2], 0.02)
    gy = np.arange(CITY_BBOX[1], CITY_BBOX[3], 0.0127)
    dens = np.zeros((len(gy), len(gx)), dtype='int16')
    for b in boxes:
        i0 = max(int((b[0] - CITY_BBOX[0]) / 0.02), 0)
        i1 = min(int((b[2] - CITY_BBOX[0]) / 0.02) + 1, len(gx))
        j0 = max(int((b[1] - CITY_BBOX[1]) / 0.0127), 0)
        j1 = min(int((b[3] - CITY_BBOX[1]) / 0.0127) + 1, len(gy))
        if i1 > i0 and j1 > j0:
            dens[j0:j1, i0:i1] += 1
    dens = np.ascontiguousarray(np.flipud(dens))
    tr = rasterio.transform.from_origin(CITY_BBOX[0], CITY_BBOX[3], 0.02, 0.0127)
    with rasterio.open(OUTD / 'density_grid_5.90.tif', 'w', driver='GTiff',
                       width=len(gx), height=len(gy), count=1, dtype='int16',
                       crs='EPSG:4326', transform=tr, compress='deflate',
                       nodata=-1) as d:
        d.write(dens, 1)
    return dens, tr, len(boxes)


SIDE = OUTD / 'mosaic_590_chunks.json'


def main():
    import sys as _sys
    if len(_sys.argv) > 1 and _sys.argv[1] == 'init':
        # предсоздание выходных растров (отдельный вызов, чтобы все
        # последующие merge-вызовы работали в r+ и успевали close())
        src0 = PRODD / 'hybrid_2m_5.83_N1_5.83.tif'
        with rasterio.open(src0) as s0:
            pass
        return
    if len(_sys.argv) > 1 and _sys.argv[1] == 'quality':
        n = 0
        for tag, b, _ in ZONES[21:]:
            if not (PRODD / f'quality_5.83_{tag}.tif').exists():
                write_south_quality(tag, b)
                n += 1
        print(f"quality материализовано: {n} зон")
        return

    t0 = time.time()
    log(f"зон: {len(ZONES)} (север 21 + юг {len(ZONES) - 21})")
    dsrc, qsrc = {}, {}
    for tag, _, _ in ZONES:
        p = PRODD / f"hybrid_2m_5.83_{ftag(tag)}.tif"
        if not p.exists():
            raise SystemExit(f"нет продукта: {p}")
        dsrc[tag] = rasterio.open(p)
        qp = PRODD / f"quality_5.83_{ftag(tag)}.tif"
        if qp.exists():
            qsrc[tag] = rasterio.open(qp)
    srcs = list(dsrc.items())

    W0 = min(d.bounds[0] for d in dsrc.values())
    E0 = max(d.bounds[2] for d in dsrc.values())
    S0 = min(d.bounds[1] for d in dsrc.values())
    N0 = max(d.bounds[3] for d in dsrc.values())
    GW = int(np.ceil((E0 - W0) / PX))
    GH = int(np.ceil((N0 - S0) / PX))
    gtr = rasterio.transform.from_origin(W0, N0, PX, PX)
    log(f"сетка {GH}x{GW} ({GW * GH / 1e6:.0f}M px), "
        f"bbox {W0:.4f},{S0:.4f},{E0:.4f},{N0:.4f}")

    # прямое размещение: nearest-трансляция = целочисленный сдвиг
    # (глобальный пиксель (iz+G, jx+J) <- пиксель зоны (iz, jx))
    place = []
    for tag, sd in dsrc.items():
        b = zbounds(tag)
        G = int(round((N0 - b[3]) / PX))     # сдвиг строк зоны -> сетка
        J = int(round((b[0] - W0) / PX))     # сдвиг столбцов
        H_z, W_z = sd.shape
        place.append((tag, b, G, J, H_z, W_z, tag in qsrc))

    synth = {}
    noq = [tag for tag in dsrc if tag not in qsrc]
    if noq:
        log(f"внимание: без quality-файла {len(noq)} зон (синтез RAM): "
            f"{noq[:5]}...")
        for tag in noq:
            b = zbounds(tag)
            W = int(np.ceil((b[2] - b[0]) / PX))
            H = int(np.ceil((b[3] - b[1]) / PX))
            tr = rasterio.transform.from_origin(b[0], b[3], PX, PX)
            synth[tag] = (b, synth_quality(W, H, tr, tag))
    else:
        log("quality-файлы есть у всех зон")

    dens, dtr, n_strips = density_grid()
    log(f"density grid: {n_strips} стрипов")

    # --- докатка: sidecar с последним завершённым чанком ---
    start_chunk = 0
    mode_fd = mode_fq = mode_ftm = 'w'
    if SIDE.exists() and json.loads(SIDE.read_text()).get('grid') == [GH, GW]:
        start_chunk = json.loads(SIDE.read_text())['last_chunk'] + 1
        mode_fd = mode_fq = mode_ftm = 'r+'
        log(f"докатка с чанка {start_chunk}")
    elif SIDE.exists():
        SIDE.unlink()

    fm = OUTD / 'spb_hybrid_2m_mosaic_5.90.tif'
    fq_f = OUTD / 'spb_quality_5.90.tif'
    ft = OUTD / 'trust_mask_2m_5.90.tif'
    fd = rasterio.open(fm, mode_fd, driver='GTiff', width=GW, height=GH,
                       count=1, dtype='int16', crs='EPSG:4326', transform=gtr,
                       compress='deflate', predictor=2, nodata=NOD)
    fq = rasterio.open(fq_f, mode_fq, driver='GTiff', width=GW, height=GH,
                       count=3, dtype='uint16', crs='EPSG:4326', transform=gtr,
                       compress='deflate', predictor=2, nodata=QNOD)
    ftm = rasterio.open(ft, mode_ftm, driver='GTiff', width=GW, height=GH,
                        count=1, dtype='uint8', crs='EPSG:4326', transform=gtr,
                        compress='deflate', predictor=2)

    stats = dict(valid_px=0, nodata_px=0, seam_px=0, trust_valid_px=0,
                 ncov_hist={})
    nch = (GH + CHUNK - 1) // CHUNK
    # CLI-диапазон: merge [from] [to] — вызов обязан уложиться в таймаут
    # и завершиться close() (GDAL сбрасывает блоки только при close)
    if len(_sys.argv) > 1 and _sys.argv[1] == 'merge':
        a, b = int(_sys.argv[2]), min(int(_sys.argv[3]), nch - 1)
        rng = range(a, b + 1)
        log(f"merge чанков {a}..{b} (из {nch})")
    else:
        rng = range(start_chunk, nch)
    for ci in rng:
        r0, r1 = ci * CHUNK, min((ci + 1) * CHUNK, GH)
        h = r1 - r0
        win = rasterio.windows.Window(0, r0, GW, h)
        top = N0 - r0 * PX
        bot = N0 - r1 * PX
        ctr = rasterio.transform.from_origin(W0, top, PX, PX)
        dens_ch = np.zeros((h, GW), dtype='uint8')
        reproject(dens, dens_ch, src_transform=dtr, src_crs='EPSG:4326',
                  dst_transform=ctr, dst_crs='EPSG:4326',
                  resampling=Resampling.nearest, dst_nodata=0)
        trusted_ch = (dens_ch >= DENS_MIN).astype('uint8')
        best_sc = np.full((h, GW), -2**31 + 1, dtype='int32')
        best_dem = np.full((h, GW), NOD, dtype='int16')
        best_nc = np.zeros((h, GW), dtype='int32')
        best_sg = np.full((h, GW), 10**6, dtype='int32')
        best_can = np.full((h, GW), QNOD, dtype='uint16')
        valid_cnt = np.zeros((h, GW), dtype='uint8')
        for tag, b, G, J, H_z, W_z, has_q in place:
            # строки зоны, видимые в полосе чанка: global = iz + G
            iz0, iz1 = max(r0 - G, 0), min(r1 - G, H_z)
            jx0, jx1 = max(-J, 0), min(GW - J, W_z)
            if iz1 <= iz0 or jx1 <= jx0:
                continue
            sd = dsrc[tag]
            dem = sd.read(1, window=rasterio.windows.Window(
                jx0, iz0, jx1 - jx0, iz1 - iz0))
            valid = dem != NOD
            if not valid.any():
                continue
            gr, gc = iz0 + G - r0, jx0 + J
            valid_cnt[gr:gr + dem.shape[0], gc:gc + dem.shape[1]] += valid
            if has_q:
                qq = qsrc[tag].read((1, 2, 3), window=rasterio.windows.Window(
                    jx0, iz0, jx1 - jx0, iz1 - iz0))
                nc = qq[0].astype('int32')
                sg = qq[1].astype('int32')
                can = qq[2]
                nc = np.where(nc == QNOD, 0, nc)
                sg = np.where(sg >= QNOD, 10**6, sg)
            elif tag in synth:
                # synth-массив уже на 2м-сетке зоны: прямая нарезка
                can = synth[tag][1][2][iz0:iz1, jx0:jx1]
                nc = np.zeros(dem.shape, dtype='int32')
                sg = np.full(dem.shape, 10**6, dtype='int32')
            else:
                nc = np.zeros(dem.shape, dtype='int32')
                sg = np.full(dem.shape, 10**6, dtype='int32')
                can = np.full(dem.shape, QNOD, dtype='uint16')
            # рецептура 5.84: score = n_cov*20000 - sigma; upd = sc > best_sc
            sc = nc * 20000 - np.minimum(sg, 10**6)
            sc = np.where(valid, sc, -2**31 + 1)
            sub = (slice(gr, gr + dem.shape[0]), slice(gc, gc + dem.shape[1]))
            bsc = best_sc[sub]
            upd = sc > bsc
            np.copyto(bsc, sc, where=upd)
            np.copyto(best_dem[sub], dem, where=upd)
            np.copyto(best_nc[sub], nc, where=upd)
            np.copyto(best_sg[sub], np.minimum(sg, 10**6), where=upd)
            np.copyto(best_can[sub], can, where=upd)
        anyvalid = best_sc > -2**31 + 1
        q0 = np.where(best_nc > 0, np.minimum(best_nc, QNOD - 1),
                      QNOD).astype('uint16')
        q1 = np.where(best_sg >= 10**6, QNOD,
                      np.minimum(best_sg, QNOD - 1)).astype('uint16')
        dem_out = np.where(anyvalid, best_dem, NOD).astype('int16')
        stats['seam_px'] += int(((valid_cnt > 1) & anyvalid).sum())
        stats['valid_px'] += int(anyvalid.sum())
        stats['nodata_px'] += int((~anyvalid).sum())
        stats['trust_valid_px'] += int((trusted_ch & anyvalid).sum())
        vals, cnts = np.unique(best_nc[anyvalid], return_counts=True)
        for vv, cc in zip(vals, cnts):
            stats['ncov_hist'][int(vv)] = \
                stats['ncov_hist'].get(int(vv), 0) + int(cc)
        fd.write(dem_out, 1, window=win)
        fq.write(np.stack([q0, q1, best_can]), window=win)
        ftm.write(trusted_ch, 1, window=win)
        del dem_out, q0, q1, best_sc, best_dem, best_nc, best_sg, best_can, \
            valid_cnt, dens_ch, trusted_ch, anyvalid
        SIDE.write_text(json.dumps(dict(grid=[GH, GW], last_chunk=ci)))
        if (ci + 1) % 10 == 0:
            log(f"чанк {ci + 1}/{nch} ({time.time() - t0:.0f}s)")
    fd.close(); fq.close(); ftm.close()
    SIDE.unlink()
    for d in dsrc.values():
        d.close()
    for q in qsrc.values():
        q.close()

    from shapely.geometry import box
    ov = []
    for i in range(len(ZONES)):
        for j in range(i + 1, len(ZONES)):
            a, b = box(*ZONES[i][1]), box(*ZONES[j][1])
            inter = a.intersection(b).area
            if inter > 1e-12:
                ov.append([ZONES[i][0], ZONES[j][0], round(inter * 1e4, 4)])

    out = dict(
        date=datetime.now().isoformat(), n_zones=len(ZONES),
        n_zones_south=len(ZONES) - 21,
        grid=dict(gw=GW, gh=GH, px=PX, bbox=[round(W0, 6), round(S0, 6),
                                             round(E0, 6), round(N0, 6)]),
        valid_px=stats['valid_px'], nodata_px=stats['nodata_px'],
        valid_pct=round(100 * stats['valid_px'] / (GW * GH), 2),
        seam_px=stats['seam_px'], bbox_overlaps=ov,
        density=dict(n_strips_kept=n_strips, dens_min=DENS_MIN,
                     trust_valid_pct=round(100 * stats['trust_valid_px'] /
                                           max(stats['valid_px'], 1), 2)),
        ncov_hist_top=dict(sorted(stats['ncov_hist'].items(),
                                  key=lambda x: -x[1])[:12]),
        minutes=round((time.time() - t0) / 60, 1),
    )
    (HOME / 'results/mosaic_5.90.json').write_text(
        json.dumps(out, ensure_ascii=False, indent=1))
    log(json.dumps({k: v for k, v in out.items()
                    if k not in ('ncov_hist_top', 'bbox_overlaps')},
                   ensure_ascii=False))
    log('ГОТОВО: download/mosaic_5.90/')


if __name__ == '__main__':
    main()
