#!/usr/bin/env python3
"""
11_osm_as_weighted_anchor.py
============================
ПРАВИЛЬНАЯ проверка OSM building heights: добавить как взвешенный якорь в
модель и измерить holdout MAD модели.

КОНТЕКСТ (пользователь):
- Предыдущая проверка (06, 09) сравнивала источник-в-одиночку с моделью
- Правильный вопрос: "добавляет ли источник к модели что-то"
- σ_модель 1.07 + σ_источник 2.03 → σ_комб 0.94 (теоретически, при независимости)
- Это соответствует добавлению в опору с весом 1/σ²

МЕТОД:
1. Использовать существующую модель (v8/v9b pipeline) — без переcчёта всей сшивки
2. Для каждой holdout точки:
   a. Получить h_model (из spb_passport_validation_v11.csv, hs column)
   b. Получить h_osm (из 09_osm_building_heights_with_ndvi.py, h_pred_ground)
   c. Комбинировать: h_combined = (h_model/σ_m² + h_osm/σ_s²) / (1/σ_m² + 1/σ_s²)
   d. dH = hp - h_combined
3. σ_m = 1.07 (v8 holdout MAD), σ_s = 2.03 (OSM NDVI-filtered MAD)
4. Сравнить с baseline (только модель)

Это аппроксимация — реальная интеграция в build_prior требует полного пересчёта.
Но для проверки "добавляет ли что-то" этого достаточно.
"""
import json, sys
from pathlib import Path
from datetime import datetime
import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

DATA = Path('/home/z/my-project/scripts/data')
LOG = lambda m: print(f'[{datetime.now():%H:%M:%S}] {m}', flush=True)


def mad(a):
    a = np.asarray(a, float)
    a = a[np.isfinite(a)]
    if a.size == 0: return float('nan')
    m = np.median(a)
    return float(np.median(np.abs(a - m)) * 1.4826)


def bootstrap_mad_ci(a, n_boot=2000, ci=0.95, seed=42):
    a = np.asarray(a, float)
    a = a[np.isfinite(a)]
    if a.size < 3:
        return float('nan'), float('nan'), float('nan')
    rng = np.random.default_rng(seed)
    boot_mads = np.empty(n_boot)
    n = len(a)
    for i in range(n_boot):
        sample = rng.choice(a, size=n, replace=True)
        m = np.median(sample)
        boot_mads[i] = np.median(np.abs(sample - m)) * 1.4826
    alpha = (1 - ci) / 2
    lo, hi = np.percentile(boot_mads, [alpha * 100, (1 - alpha) * 100])
    return mad(a), float(lo), float(hi)


def combine_two_estimators(h_m, sigma_m, h_s, sigma_s):
    """Inverse-variance weighted combination of two independent estimators.

    h_combined = (h_m/sigma_m² + h_s/sigma_s²) / (1/sigma_m² + 1/sigma_s²)
    sigma_combined = 1/sqrt(1/sigma_m² + 1/sigma_s²)

    This is the maximum likelihood estimate under Gaussian assumption.
    Equivalent to: if we put both as anchors with weights 1/σ² in prior,
    the resulting field is the weighted average in their vicinity.
    """
    w_m = 1.0 / sigma_m ** 2
    w_s = 1.0 / sigma_s ** 2
    h_combined = (h_m * w_m + h_s * w_s) / (w_m + w_s)
    sigma_combined = 1.0 / np.sqrt(w_m + w_s)
    return h_combined, sigma_combined


def main():
    LOG('=' * 70)
    LOG('OSM building heights as WEIGHTED ANCHOR in model')
    LOG('=' * 70)
    LOG('Test: does adding OSM source as 1/σ² anchor improve holdout MAD of MODEL?')
    LOG('Method: inverse-variance weighting (approximation of build_prior with both)')

    # Load v11 holdout — has h_model (hs column) and h_passport (hp)
    v11 = pd.read_csv(DATA / 'spb_passport_validation_v11.csv')
    holdout = v11[v11.role == 'holdout'].copy()
    LOG(f'\nHoldout: {len(holdout)} points')

    # Load OSM predictions (best config: r100 NDVI t0.3)
    osm = pd.read_csv(DATA / 'osm_ndvi_r100_t0.3.csv')
    osm = osm[osm.matched].copy()  # only matched points
    LOG(f'OSM predictions: {len(osm)} matched (radius=100m, NDVI t=0.3)')

    # Merge holdout with OSM on lat/lon (or close match)
    # Both should have same lat/lon precision
    merged = holdout.merge(osm[['lat', 'lon', 'h_pred_ground', 'median_levels']],
                            on=['lat', 'lon'], how='inner')
    LOG(f'Merged (holdout ∩ OSM matched): {len(merged)} points')

    if len(merged) < 5:
        LOG('ERROR: too few merged points')
        return

    # Baseline: model alone
    sigma_m = mad(merged.dH.values)  # MAD of model vs passport
    # Use theoretical v8 baseline = 1.07, or actual from full holdout
    actual_sigma_m = mad(holdout.dH.values)  # all holdout
    # Use actual for combined test (we're on the subset with OSM matched)
    # But to be fair, use the theoretical σ_model = 1.07 (v8 city-wide)
    sigma_model = 1.07  # city-wide v8 holdout MAD
    sigma_source = 2.03  # OSM r100 NDVI t0.3 MAD (from 09)

    LOG(f'\nSigma values:')
    LOG(f'  σ_model (v8 city-wide): {sigma_model:.3f} m')
    LOG(f'  σ_source (OSM r100 NDVI t0.3): {sigma_source:.3f} m')
    LOG(f'  σ_model_subset (this merged subset, model alone): {sigma_m:.3f} m')

    # Combined prediction: h_combined = (h_m/σ_m² + h_s/σ_s²) / (1/σ_m² + 1/σ_s²)
    h_m = merged.hs.values  # model height
    h_s = merged.h_pred_ground.values  # OSM prediction
    h_combined, sigma_combined_theoretical = combine_two_estimators(
        h_m, sigma_model, h_s, sigma_source)

    # dH_combined = hp - h_combined
    dH_combined = merged.hp.values - h_combined
    dH_model_alone = merged.hp.values - h_m
    dH_source_alone = merged.hp.values - h_s

    # Bootstrap CIs
    mad_model_alone, ci_lo_m, ci_hi_m = bootstrap_mad_ci(dH_model_alone, n_boot=2000)
    mad_source_alone, ci_lo_s, ci_hi_s = bootstrap_mad_ci(dH_source_alone, n_boot=2000)
    mad_combined, ci_lo_c, ci_hi_c = bootstrap_mad_ci(dH_combined, n_boot=2000)

    LOG(f'\nResults (n={len(merged)} points):')
    LOG(f'{"":30s} {"MAD":>7s} {"95% CI":>22s} {"CI width":>10s}')
    LOG(f'{"-"*30} {"-"*7} {"-"*22} {"-"*10}')
    LOG(f'{"Model alone (v8)":30s} {mad_model_alone:>7.3f} [{ci_lo_m:.3f}, {ci_hi_m:.3f}]      {ci_hi_m-ci_lo_m:.3f}')
    LOG(f'{"Source alone (OSM NDVI)":30s} {mad_source_alone:>7.3f} [{ci_lo_s:.3f}, {ci_hi_s:.3f}]      {ci_hi_s-ci_lo_s:.3f}')
    LOG(f'{"Combined (1/σ² weighted)":30s} {mad_combined:>7.3f} [{ci_lo_c:.3f}, {ci_hi_c:.3f}]      {ci_hi_c-ci_lo_c:.3f}')

    LOG(f'\nTheoretical σ_combined (if independent): {sigma_combined_theoretical:.3f} m')
    LOG(f'Theoretical improvement: {100*(sigma_model-sigma_combined_theoretical)/sigma_model:+.1f}%')
    LOG(f'Actual improvement (on subset): {100*(mad_model_alone-mad_combined)/mad_model_alone:+.1f}%')

    # Verdict
    LOG(f'\n=== VERDICT ===')
    if mad_combined < mad_model_alone - 0.05:
        LOG(f'  ✅ Combined MAD ({mad_combined:.3f}) < Model alone ({mad_model_alone:.3f})')
        LOG(f'  → OSM source ADDS INFORMATION to model')
        LOG(f'  → Should be integrated as weighted anchor in build_prior')
    elif mad_combined > mad_model_alone + 0.05:
        LOG(f'  ⚠ Combined MAD ({mad_combined:.3f}) > Model alone ({mad_model_alone:.3f})')
        LOG(f'  → OSM source HURTS model (errors not independent — likely correlated with model)')
    else:
        LOG(f'  = Combined MAD ({mad_combined:.3f}) ≈ Model alone ({mad_model_alone:.3f})')
        LOG(f'  → No detectable improvement (within noise on n={len(merged)})')

    # Also test with different σ_source values (sensitivity)
    LOG(f'\n=== Sensitivity: σ_source ∈ [1.5, 5.0] ===')
    LOG(f'{"σ_source":>10s} {"σ_combined_theor":>18s} {"actual MAD":>12s} {"Δ vs model":>12s}')
    LOG(f'{"-"*10} {"-"*18} {"-"*12} {"-"*12}')
    for sigma_s_test in [1.5, 2.0, 2.5, 3.0, 4.0, 5.0]:
        h_combined_test, sigma_combined_test = combine_two_estimators(
            h_m, sigma_model, h_s, sigma_s_test)
        dH_c_test = merged.hp.values - h_combined_test
        mad_c_test = mad(dH_c_test)
        delta = mad_c_test - mad_model_alone
        LOG(f'{sigma_s_test:>10.2f} {sigma_combined_test:>18.3f} {mad_c_test:>12.3f} {delta:>+12.3f}')

    # Save
    summary = {
        'experiment': 'OSM as weighted anchor (proper test)',
        'method': 'Inverse-variance weighting: h_combined = (h_m/σ_m² + h_s/σ_s²) / (1/σ_m² + 1/σ_s²)',
        'sigma_model': sigma_model,
        'sigma_source': sigma_source,
        'sigma_combined_theoretical': sigma_combined_theoretical,
        'n_merged_points': len(merged),
        'results': {
            'model_alone': {
                'mad': float(mad_model_alone),
                'ci_95': [float(ci_lo_m), float(ci_hi_c)],
                'dH': dH_model_alone.tolist(),
            },
            'source_alone': {
                'mad': float(mad_source_alone),
                'ci_95': [float(ci_lo_s), float(ci_hi_s)],
                'dH': dH_source_alone.tolist(),
            },
            'combined_1_sigma_2': {
                'mad': float(mad_combined),
                'ci_95': [float(ci_lo_c), float(ci_hi_c)],
                'dH': dH_combined.tolist(),
                'theoretical_sigma_combined': float(sigma_combined_theoretical),
                'improvement_pct': float(100*(mad_model_alone-mad_combined)/mad_model_alone),
            },
        },
        'sensitivity_to_sigma_source': [
            {
                'sigma_source': float(sigma_s_test),
                'sigma_combined_theoretical': float(sigma_combined_test),
                'mad_combined': float(mad_c_test),
                'delta_vs_model': float(mad_c_test - mad_model_alone),
            }
            for sigma_s_test in [1.5, 2.0, 2.5, 3.0, 4.0, 5.0]
            for h_combined_test, sigma_combined_test in [combine_two_estimators(h_m, sigma_model, h_s, sigma_s_test)]
            for dH_c_test in [merged.hp.values - h_combined_test]
            for mad_c_test in [mad(dH_c_test)]
        ],
    }
    out = DATA / 'osm_as_weighted_anchor.json'
    out.write_text(json.dumps(summary, indent=2, default=str))
    LOG(f'\nSaved → {out}')


if __name__ == '__main__':
    main()
