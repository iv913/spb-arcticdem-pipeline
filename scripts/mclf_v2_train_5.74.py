#!/usr/bin/env python3
"""
5.74-A шаг 2: M-CLF-v2 — калибровка городского ground-модели.

Идея (из рекомендаций 5.73): база — FABDEM bare-earth (MAD 0.345 на
33908 ICESat-полевых точках), поверх — остаточная коррекция r =
h_ref − fabdem, предсказанная по признакам (канопи GLO−FAB, slope,
roughness, TPI, openness, dsm, lat, lon). Loss = absolute_error
(целевая метрика проекта — MAD/медиана).

Кросс-валидация: пространственные блоки 2x2 км (как в 5.66),
GroupKFold по блокам — исключает утечку внутри треков ICESat.

Выходы:
  scripts/data/mclf_v2_predictions_5.74.csv  (icesat+archi, предсказания v2)
  scripts/data/mclf_v2_cv_5.74.json          (метрики CV + финальной модели)
  scripts/data/mclf_v2_model_5.74.joblib     (модель)
  scripts/data/consensus_dtm_5.71/mclf_v2_ground_bs77_cm_5.74.tif (сетка)
"""
import json, warnings
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.model_selection import GroupKFold
warnings.filterwarnings('ignore')

HOME = Path('/home/z/my-project/repo')
D = HOME / 'scripts/data'
FEAT = D / 'mclf_v2_features_5.74.csv'
CVJ = D / 'mclf_v2_cv_5.74.json'
GRID = D / 'consensus_dtm_5.71'

FEATS = ['canopy', 'slope', 'rough', 'tpi', 'openness', 'dsm', 'lat', 'lon']
BLK_LA, BLK_LO = 0.02, 0.0127          # 2x2 км
NFOLD = 5


def mad(x):
    x = np.asarray(x, float)
    m = np.median(x)
    return float(np.median(np.abs(x - m))), float(m)


def metrics(y_true, y_pred):
    d = np.asarray(y_pred) - np.asarray(y_true)
    md, med = mad(d)
    return dict(mad=round(md, 3), med=round(med, 3),
                p95=round(float(np.percentile(np.abs(d), 95)), 3),
                r=round(float(np.corrcoef(y_true, y_pred)[0, 1]), 4))


def make_model():
    return HistGradientBoostingRegressor(
        loss='absolute_error', max_iter=400, learning_rate=0.06,
        max_depth=4, min_samples_leaf=60, l2_regularization=1.0,
        early_stopping=False, random_state=42)


def main():
    df = pd.read_csv(FEAT)
    ic = df[df.src == 'icesat'].copy()
    ar = df[df.src == 'archi'].copy()
    print(f"icesat {len(ic)}, archi {len(ar)}")

    # блоки 2х2 км и фолды
    ic['blk'] = (np.floor(ic.lat / BLK_LA).astype(int) * 10000 +
                 np.floor(ic.lon / BLK_LO).astype(int) % 10000)
    ic['fold'] = ic.blk % NFOLD
    X = ic[FEATS].values.astype('float64')
    fab = ic.fabdem.values
    y = ic.h_ref.values
    resid = y - fab

    # --- CV: предсказываем остаток, метрики на земле ---
    pred_v2 = np.zeros(len(ic))
    pred_fab = fab.copy()
    pred_model = ic.h_model.values
    folds_stat = []
    for f in range(NFOLD):
        tr, te = ic.fold != f, ic.fold == f
        m = make_model().fit(X[tr.values], resid[tr.values])
        r_hat = m.predict(X[te.values])
        pred_v2[te.values] = fab[te.values] + r_hat
        folds_stat.append(dict(
            fold=f, n=int(te.sum()),
            fab=metrics(y[te.values], fab[te.values]),
            model=metrics(y[te.values], pred_model[te.values]),
            v2=metrics(y[te.values], pred_v2[te.values])))
        print(f"  fold {f}: n={te.sum():6d} "
              f"fab {folds_stat[-1]['fab']['mad']:.3f} "
              f"model {folds_stat[-1]['model']['mad']:.3f} "
              f"v2 {folds_stat[-1]['v2']['mad']:.3f}")

    cv_summary = dict(
        fab=metrics(y, pred_fab), model=metrics(y, pred_model),
        v2=metrics(y, pred_v2),
        agg_fab_mad=round(np.mean([s['fab']['mad'] for s in folds_stat]), 3),
        agg_model_mad=round(np.mean([s['model']['mad'] for s in folds_stat]), 3),
        agg_v2_mad=round(np.mean([s['v2']['mad'] for s in folds_stat]), 3))

    # --- финальная модель на всём наборе ---
    final = make_model().fit(X, resid)
    imp_note = dict(n_train=len(ic), features=FEATS,
                    loss='absolute_error',
                    params=dict(max_iter=400, lr=0.06, depth=4,
                                min_samples_leaf=60, l2=1.0))

    # --- зонная валидация (12 зон 5.66/5.68) на CV-предсказаниях ---
    zones = {
        "Кронштадт": (59.95, 60.02, 29.66, 29.83),
        "Курортный": (60.09, 60.31, 29.55, 30.20),
        "Пушкинский": (59.60, 59.80, 30.05, 30.47),
        "Колпинский": (59.66, 59.79, 30.45, 30.72),
        "Петродворцовый": (59.80, 59.98, 29.85, 30.15),
        "Кировский (юг)": (59.75, 59.87, 30.10, 30.33),
        "Красносельский": (59.74, 59.89, 29.95, 30.29),
        "Фрунзенский (Купчино)": (59.77, 59.90, 30.30, 30.60),
        "Невский (восток)": (59.80, 59.94, 30.38, 30.75),
        "Приморский": (59.95, 60.11, 30.13, 30.46),
        "Василеостровский": (59.90, 60.01, 29.95, 30.32),
        "Центральный": (59.88, 60.02, 30.26, 30.45),
    }
    zone_stats = {}
    for z, (la0, la1, lo0, lo1) in zones.items():
        mz = ((ic.lat >= la0) & (ic.lat <= la1) & (ic.lon >= lo0) & (ic.lon <= lo1))
        if mz.sum() < 20:
            continue
        i = mz.values
        zone_stats[z] = dict(
            n=int(mz.sum()),
            fab=metrics(y[i], fab[i]), model=metrics(y[i], pred_model[i]),
            v2=metrics(y[i], pred_v2[i]))
        print(f"  {z:22s} n={mz.sum():6d} "
              f"fab {zone_stats[z]['fab']['mad']:.2f} "
              f"model {zone_stats[z]['model']['mad']:.2f} "
              f"v2 {zone_stats[z]['v2']['mad']:.2f}")

    # --- предсказания для archi (per-mark) ---
    Xa = ar[FEATS].values.astype('float64')
    ar['h_v2'] = ar.fabdem.values + final.predict(Xa)
    ar['dH_fab'] = ar.fabdem - ar.h_ref
    ar['dH_v2'] = ar.h_v2 - ar.h_ref
    ar['dH_model'] = ar.h_model - ar.h_ref

    # --- предсказания для icesat (in-sample, для CSV-канона) ---
    ic['h_v2'] = pred_v2          # честные CV-предсказания
    ic['dH_v2'] = ic.h_v2 - ic.h_ref

    out_cols = ['src', 'lat', 'lon', 'h_ref', 'h_model', 'fabdem', 'h_v2',
                'dH_model', 'dH_v2', 'canopy', 'slope', 'rough', 'tpi',
                'openness', 'dsm']
    # фикс portability: features-скрипт переименовывает value -> h_ref
    if 'value' not in ar.columns and 'h_ref' in ar.columns:
        ar['value'] = ar['h_ref']
    pd.concat([ic[out_cols], ar[out_cols + ['project_name', 'value']]],
              ignore_index=True).to_csv(D / 'mclf_v2_predictions_5.74.csv',
                                        index=False)
    from joblib import dump
    dump(final, D / 'mclf_v2_model_5.74.joblib')

    # --- якоря archi: агрегат по проектам ---
    anchors = (ar.groupby('project_name')
               .agg(n=('value', 'size'), val_med=('value', 'median'),
                    fab_med=('fabdem', 'median'), v2_med=('h_v2', 'median'),
                    model_med=('h_model', 'median'))
               .reset_index())
    anchors['dH_fab'] = anchors.fab_med - anchors.val_med
    anchors['dH_v2'] = anchors.v2_med - anchors.val_med
    anchors['dH_model'] = anchors.model_med - anchors.val_med
    anchors.round(2).to_csv(D / 'mclf_v2_archi_anchors_5.74.csv', index=False)

    # --- сетка: калиброванная земля на весьbbox 5.71 ---
    print("сетка v2...")
    import rasterio
    from scipy.ndimage import uniform_filter
    with rasterio.open(GRID / 'bareearth_bs77_cm_5.71.tif') as src:
        fab_raw = src.read(1).astype('float32'); tr = src.transform
        nodata = src.nodata; H, W = src.shape; crs = src.crs
    fab_g = fab_raw / 100.0; fab_g[fab_raw == nodata] = np.nan
    with rasterio.open(GRID / 'glo30_dsm_bs77_cm_5.74.tif') as src:
        dsm_raw = src.read(1).astype('float32')
        dsm = dsm_raw / 100.0; dsm[dsm_raw == -32768] = np.nan
    with rasterio.open(GRID / 'canopy_bias_cm_5.74.tif') as src:
        can_raw = src.read(1).astype('float32')
        canopy = can_raw / 100.0; canopy[can_raw == -32768] = np.nan
    t = np.load(GRID / 'fabdem_terrain_5.74.npz')
    rr, cc = np.mgrid[0:H, 0:W]
    xs = tr.c + tr.a * (cc + 0.5)
    ys = tr.f + tr.e * (rr + 0.5)
    G = dict(canopy=canopy, slope=t['slope'], rough=t['rough'],
             tpi=t['tpi'], openness=t['openness'], dsm=dsm,
             lat=ys, lon=xs)
    # фикс OOM (новая среда, 4 ГБ): чанковый predict по строкам
    r_g = np.full((H, W), np.nan, dtype='float32')
    CH = 256
    for r0 in range(0, H, CH):
        r1 = min(r0 + CH, H)
        cols = {f: G[f][r0:r1].ravel() for f in FEATS}
        ok = np.isfinite(cols['canopy']) & np.isfinite(cols['dsm']) & \
             np.isfinite(fab_g[r0:r1].ravel())
        for f in FEATS:
            cols[f] = np.where(ok, cols[f], 0.0)
        if ok.sum() == 0:
            continue
        Xg = np.stack([cols[f] for f in FEATS], 1)
        pred = final.predict(Xg).astype('float32')
        block = np.full(r1 - r0, np.nan, dtype='float32')[:, None]
        block = np.broadcast_to(block, (r1 - r0, W)).copy()
        flat = block.reshape(-1)
        flat[ok] = pred[ok]
        r_g[r0:r1] = block
    v2 = fab_g + r_g
    prof = dict(driver='GTiff', width=W, height=H, count=1, dtype='int16',
                crs=crs, transform=tr, compress='deflate', predictor=2,
                nodata=-32768)
    out = np.full((H, W), -32768, dtype='int16')
    fin = np.isfinite(v2)
    out[fin] = np.clip(np.rint(v2[fin] * 100), -32000, 32000)
    with rasterio.open(GRID / 'mclf_v2_ground_bs77_cm_5.74.tif', 'w', **prof) as d:
        d.write(out, 1)
    # roundtrip-проверка
    with rasterio.open(GRID / 'mclf_v2_ground_bs77_cm_5.74.tif') as src:
        chk = src.read(1)
    rtd = np.nanmax(np.abs(chk[fin].astype('float64') / 100 - v2[fin]))
    print(f"сетка v2: {fin.mean():.1%} валидно, roundtrip {rtd:.4f} м")

    meta = dict(cv=cv_summary, folds=folds_stat, zones=zone_stats,
                model=imp_note, grid_roundtrip_m=round(float(rtd), 4),
                grid_valid_frac=round(float(fin.mean()), 4))
    CVJ.write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    print(json.dumps(cv_summary, indent=1))


if __name__ == '__main__':
    main()
