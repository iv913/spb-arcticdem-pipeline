#!/usr/bin/env python3
"""5.84: мозаика 21 зонного продукта гибридного 2м DEM + quality-каналы +
density grid (маска доверия).

Входы: download/products_5.83/{hybrid_2m,quality}_5.83_<TAG>_5.83.tif
  - 14 зон 5.83 (N1..N14; N13 out-of-cover);
  - 7 регенерированных якорных зон 5.76-5.79 (PILOT/SESTR/RZHEV/OZER/
    STREL/AVTOV/LIGO, замороженный рецепт 5.80-81, regen_zones_5.84.sh).
Конвенция: DEM Int16 СМ БС-77, nodata -32768; quality 3xuint16
(n_cov, sigma_cm, canopy30_cm), nodata 65535 (=n_cov 0).
Слияние перекрытий: max n_cov, при равенстве min sigma, при равенстве —
порядок в списке. Перекрытий по плану почти нет (жадные окна) — аудит в отчёте.

Выходы: download/mosaic_5.84/ + results/mosaic_5.84.{md,json}
"""
import json, time
from datetime import datetime
from pathlib import Path

import numpy as np
import rasterio
from rasterio.vrt import WarpedVRT
from rasterio.enums import Resampling
from rasterio.warp import reproject

HOME = Path(__file__).resolve().parents[1]
OUTD = HOME / 'download/mosaic_5.84'
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


def ftag(tag):
    """суффикс файлов: зоны 5.83 шли с тагом N<i>_5.83"""
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


def audit_overlaps():
    from shapely.geometry import box
    rows = []
    for i in range(len(ZONES)):
        for j in range(i + 1, len(ZONES)):
            a, b = box(*ZONES[i][1]), box(*ZONES[j][1])
            inter = a.intersection(b).area
            if inter > 1e-12:
                rows.append([ZONES[i][0], ZONES[j][0], round(inter * 1e4, 4)])
    return rows


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
    """Канон 5.83 stage_plan: STAC footprints (valid>=0.5, cloud<=0.3),
    сетка 0.02x0.0127, n = число стрипов."""
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
    # canon stage_plan копит dens индексом ОТ ЮГА (j = (lat-59.55)/0.0127);
    # для растра с северной transform нужен flipud
    dens = np.flipud(dens)
    tr = rasterio.transform.from_origin(CITY_BBOX[0], CITY_BBOX[3], 0.02, 0.0127)
    with rasterio.open(OUTD / 'density_grid_5.84.tif', 'w', driver='GTiff',
                       width=len(gx), height=len(gy), count=1, dtype='int16',
                       crs='EPSG:4326', transform=tr, compress='deflate',
                       nodata=-1) as d:
        d.write(dens, 1)
    return dens, tr, len(boxes)


def main():
    t0 = time.time()
    log(f"зон: {len(ZONES)}")
    srcs = []
    for tag, _, _ in ZONES:
        p = PRODD / f"hybrid_2m_5.83_{ftag(tag)}.tif"
        if not p.exists():
            raise SystemExit(f"нет продукта: {p}")
        qp = PRODD / f"quality_5.83_{ftag(tag)}.tif"
        srcs.append((tag, rasterio.open(p),
                     rasterio.open(qp) if qp.exists() else None))

    W0 = min(s.bounds[0] for _, s, _ in srcs)
    E0 = max(s.bounds[2] for _, s, _ in srcs)
    S0 = min(s.bounds[1] for _, s, _ in srcs)
    N0 = max(s.bounds[3] for _, s, _ in srcs)
    GW = int(np.ceil((E0 - W0) / PX))
    GH = int(np.ceil((N0 - S0) / PX))
    gtr = rasterio.transform.from_origin(W0, N0, PX, PX)
    log(f"сетка {GH}x{GW} ({GW*GH/1e6:.0f}M px), "
        f"bbox {W0:.4f},{S0:.4f},{E0:.4f},{N0:.4f}")

    vrts = []
    for tag, s, qs in srcs:
        v = WarpedVRT(s, crs='EPSG:4326', transform=gtr, width=GW, height=GH,
                      resampling=Resampling.nearest, nodata=NOD)
        qv = None if qs is None else WarpedVRT(
            qs, crs='EPSG:4326', transform=gtr, width=GW, height=GH,
            resampling=Resampling.nearest, nodata=QNOD)
        vrts.append((tag, v, qv))

    synth = {}
    for tag, _, qs in srcs:
        if qs is None:
            b = zbounds(tag)
            W = int(np.ceil((b[2] - b[0]) / PX))
            H = int(np.ceil((b[3] - b[1]) / PX))
            tr = rasterio.transform.from_origin(b[0], b[3], PX, PX)
            synth[tag] = (b, synth_quality(W, H, tr, tag))
            log(f"quality синтез: {tag} (prior-only)")

    dens, dtr, n_strips = density_grid()
    # trust-маска dens>=6: репроецируется ПОЧАНКОВО (экономия ~1.7 ГБ RAM)

    fd = rasterio.open(OUTD / 'spb_hybrid_2m_mosaic_5.84.tif', 'w',
                       driver='GTiff', width=GW, height=GH, count=1,
                       dtype='int16', crs='EPSG:4326', transform=gtr,
                       compress='deflate', predictor=2, nodata=NOD)
    fq = rasterio.open(OUTD / 'spb_quality_5.84.tif', 'w',
                       driver='GTiff', width=GW, height=GH, count=3,
                       dtype='uint16', crs='EPSG:4326', transform=gtr,
                       compress='deflate', predictor=2, nodata=QNOD)
    ftm = rasterio.open(OUTD / 'trust_mask_2m_5.84.tif', 'w',
                        driver='GTiff', width=GW, height=GH, count=1,
                        dtype='uint8', crs='EPSG:4326', transform=gtr,
                        compress='deflate', predictor=2)

    stats = dict(valid_px=0, nodata_px=0, seam_px=0, trust_valid_px=0,
                 ncov_hist={})
    nch = (GH + CHUNK - 1) // CHUNK
    for ci in range(nch):
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
        for tag, v, qv in vrts:
            b = zbounds(tag)
            if b[1] >= top or b[3] <= bot:
                continue
            dem = v.read(1, window=win)
            valid = dem != NOD
            if not valid.any():
                continue
            valid_cnt += valid
            if qv is not None:
                qq = qv.read((1, 2, 3), window=win)
                nc, sg, can = qq[0].astype('int32'), qq[1].astype('int32'), qq[2]
            elif tag in synth:
                sb, sq = synth[tag]
                cw = int(np.ceil((sb[2] - sb[0]) / PX))
                chh = int(np.ceil((sb[3] - sb[1]) / PX))
                str_ = rasterio.transform.from_origin(sb[0], sb[3], PX, PX)
                can = np.full((h, GW), QNOD, dtype='uint16')
                reproject(sq[2], can, src_transform=str_, src_crs='EPSG:4326',
                          dst_transform=ctr, dst_crs='EPSG:4326',
                          resampling=Resampling.nearest, dst_nodata=QNOD)
                nc = np.zeros((h, GW), dtype='int32')
                sg = np.full((h, GW), 10**6, dtype='int32')
            else:
                nc = np.zeros((h, GW), dtype='int32')
                sg = np.full((h, GW), 10**6, dtype='int32')
                can = np.full((h, GW), QNOD, dtype='uint16')
            nc_e = np.where(nc == QNOD, 0, nc)
            sc = nc_e * 20000 - np.minimum(sg, 10**6)
            sc = np.where(valid, sc, -2**31 + 1)
            upd = sc > best_sc
            np.copyto(best_sc, sc, where=upd)
            np.copyto(best_dem, dem, where=upd)
            np.copyto(best_nc, nc_e, where=upd)
            np.copyto(best_sg, sg, where=upd)
            np.copyto(best_can, can, where=upd)
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
        log(f"чанк {ci+1}/{nch} ok")
    fd.close(); fq.close(); ftm.close()
    for tag, s, qs in srcs:
        s.close()
        if qs is not None:
            qs.close()
    for _, v, qv in vrts:
        v.close()
        if qv is not None:
            qv.close()

    ov = audit_overlaps()
    out = dict(
        date=datetime.now().isoformat(), n_zones=len(ZONES),
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
    (HOME / 'results/mosaic_5.84.json').write_text(
        json.dumps(out, ensure_ascii=False, indent=1))
    log(json.dumps({k: v for k, v in out.items() if k != 'ncov_hist_top'},
                   ensure_ascii=False))
    log('ГОТОВО: download/mosaic_5.84/')


if __name__ == '__main__':
    main()
