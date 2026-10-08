#!/usr/bin/env python3
"""
14_sign_test_wilcoxon_recheck.py
================================
Пересчёт 4 сравнений мощными поточечными тестами.

КОНТЕКСТ (пользователь):
- Прежний парный бутстрап MAD-разности — слабый тест
- MAD агрегирует всю выборку, поэтому парность почти не помогает
- На малых n нужно использовать поточечные тесты:
  1. Знаковый тест — сколько точек из 40 стало лучше?
  2. Уилкоксон — учитывает знак и величину, устойчив к выбросам

ДАННЫЕ УЖЕ ЕСТЬ:
- per-point dH для v7, v8, v9b, v11 (извлечены из git history)
- 40 общих holdout точек (по lat/lon merge)

ПРОВЕРКА:
1. v7 vs v8 (passport train, BEST +0.284) — значимо ли по знаковому тесту?
2. v8 vs v9b (choose_block_px, kept) — не ухудшило ли?
3. v8 vs v11 (accept_dsm_with_relief, REVERTED) — действительно ли HURT?
4. v8 vs v9 (cap_far_field) — нет per-point dH, нельзя проверить

СТАТИСТИКИ:
- Знаковый тест: n_better / n_total, binomial test (H0: p=0.5)
- Уилкоксон: signed-rank test (H0: symmetric around 0)
- Дополнительно: медиана поточечной разности |dH_v8| - |dH_v7| с CI
"""
import json, sys
from pathlib import Path
from datetime import datetime
import numpy as np
import pandas as pd
from scipy import stats

DATA = Path('/home/z/my-project/scripts/data')
LOG = lambda m: print(f'[{datetime.now():%H:%M:%S}] {m}', flush=True)


def sign_test(dH_a, dH_b, alternative='two-sided'):
    """Sign test: how many points have smaller |dH| in B than in A?

    H0: P(|dH_B| < |dH_A|) = 0.5
    Returns (n_better, n_worse, n_tied, p_value, point_estimate_better_pct)
    """
    abs_a = np.abs(dH_a)
    abs_b = np.abs(dH_b)
    better = (abs_b < abs_a).sum()
    worse = (abs_b > abs_a).sum()
    tied = (abs_b == abs_a).sum()
    n = better + worse  # exclude ties

    if n == 0:
        return 0, 0, int(tied), 1.0, 0.0

    # Binomial test
    p_val = stats.binomtest(better, n, p=0.5, alternative=alternative).pvalue
    pct_better = 100.0 * better / n if n > 0 else 0.0
    return int(better), int(worse), int(tied), float(p_val), float(pct_better)


def wilcoxon_test(dH_a, dH_b, alternative='two-sided'):
    """Wilcoxon signed-rank test on |dH_A| - |dH_B|.

    Tests whether the differences in absolute residuals are symmetric around 0.
    More powerful than sign test (uses magnitude).
    """
    abs_a = np.abs(dH_a)
    abs_b = np.abs(dH_b)
    diff = abs_a - abs_b  # positive: B is better
    diff = diff[diff != 0]
    if len(diff) < 5:
        return None
    try:
        stat, p = stats.wilcoxon(diff, alternative=alternative)
        median_diff = float(np.median(diff))
        return float(stat), float(p), median_diff
    except Exception as e:
        return None


def per_point_diff_bootstrap(dH_a, dH_b, n_boot=2000, ci=0.95, seed=42):
    """Bootstrap CI for median of per-point |dH_A| - |dH_B|.

    This is the proper per-point statistic (vs MAD-difference).
    Each point contributes its own delta, and we bootstrap the median.
    """
    abs_a = np.abs(dH_a)
    abs_b = np.abs(dH_b)
    diff = abs_a - abs_b  # positive: B is better

    rng = np.random.default_rng(seed)
    boot_medians = np.empty(n_boot)
    n = len(diff)
    for i in range(n_boot):
        sample = rng.choice(diff, size=n, replace=True)
        boot_medians[i] = np.median(sample)

    point = float(np.median(diff))
    alpha = (1 - ci) / 2
    lo, hi = np.percentile(boot_medians, [alpha * 100, (1 - alpha) * 100])
    return point, float(lo), float(hi)


def paired_bootstrap_mad(dH_a, dH_b, n_boot=5000, seed=42):
    """For comparison: the weak MAD-difference test from 13_recheck."""
    rng = np.random.default_rng(seed)
    n = len(dH_a)
    boot_diffs = np.empty(n_boot)
    for i in range(n_boot):
        idx = rng.choice(n, size=n, replace=True)
        m_a = np.median(dH_a[idx])
        m_b = np.median(dH_b[idx])
        mad_a = np.median(np.abs(dH_a[idx] - m_a)) * 1.4826
        mad_b = np.median(np.abs(dH_b[idx] - m_b)) * 1.4826
        boot_diffs[i] = mad_a - mad_b
    point_diff = float(np.median(np.abs(dH_a - np.median(dH_a))) * 1.4826 - np.median(np.abs(dH_b - np.median(dH_b))) * 1.4826)
    lo, hi = np.percentile(boot_diffs, [2.5, 97.5])
    return point_diff, float(lo), float(hi)


def analyze_pair(name_a, name_b, dH_a, dH_b, description, original_decision):
    """Run all four tests on one pair."""
    n = len(dH_a)
    abs_a = np.abs(dH_a)
    abs_b = np.abs(dH_b)

    # MAD values
    mad_a = float(np.median(np.abs(dH_a - np.median(dH_a))) * 1.4826)
    mad_b = float(np.median(np.abs(dH_b - np.median(dH_b))) * 1.4826)

    # 1. Sign test
    n_better, n_worse, n_tied, p_sign, pct_better = sign_test(dH_a, dH_b)

    # 2. Wilcoxon
    wilc = wilcoxon_test(dH_a, dH_b)
    if wilc:
        w_stat, p_wilc, median_diff = wilc
    else:
        w_stat, p_wilc, median_diff = None, None, None

    # 3. Per-point bootstrap of median difference
    pt_diff, pt_lo, pt_hi = per_point_diff_bootstrap(dH_a, dH_b)

    # 4. Paired MAD bootstrap (the weak test)
    mad_diff, mad_lo, mad_hi = paired_bootstrap_mad(dH_a, dH_b)

    # Significance thresholds (n=40, sign test)
    # 27/40 → p=0.038, 28/40 → p=0.017
    sign_threshold_5pct = 27  # for two-sided, 27+/40 OR 13-/40

    LOG(f'\n  {description}')
    LOG(f'  {name_a} → {name_b} (n={n}, ties={n_tied})')
    LOG(f'  MAD: {mad_a:.4f} → {mad_b:.4f} (Δ {mad_b-mad_a:+.4f})')
    LOG(f'')
    LOG(f'  1. SIGN TEST (|dH_B| < |dH_A| per point):')
    LOG(f'     better={n_better}/{n_better+n_worse} ({pct_better:.1f}%), worse={n_worse}, ties={n_tied}')
    LOG(f'     Binomial p-value: {p_sign:.4f}')
    sign_sig = n_better >= sign_threshold_5pct or n_worse >= sign_threshold_5pct
    if sign_sig:
        direction = 'BETTER' if n_better > n_worse else 'WORSE'
        LOG(f'     ✅ SIGNIFICANT at 5% ({direction}, threshold {sign_threshold_5pct}/40)')
    else:
        LOG(f'     ⚠ not significant (need ≥{sign_threshold_5pct} better or worse for p<0.05)')

    LOG(f'')
    LOG(f'  2. WILCOXON signed-rank test:')
    if p_wilc is not None:
        LOG(f'     stat={w_stat:.1f}, p-value={p_wilc:.4f}, median |dH_A|-|dH_B| = {median_diff:+.4f} m')
        wilc_sig = p_wilc < 0.05
        if wilc_sig:
            direction = 'BETTER' if median_diff > 0 else 'WORSE'
            LOG(f'     ✅ SIGNIFICANT at 5% ({direction})')
        else:
            LOG(f'     ⚠ not significant at 5%')
    else:
        LOG(f'     insufficient non-zero differences')

    LOG(f'')
    LOG(f'  3. PER-POINT MEDIAN |dH_A|-|dH_B| bootstrap:')
    LOG(f'     point estimate: {pt_diff:+.4f} m')
    LOG(f'     95% CI: [{pt_lo:+.4f}, {pt_hi:+.4f}], width={pt_hi-pt_lo:.4f}')
    pt_sig = (pt_lo > 0 and pt_hi > 0) or (pt_lo < 0 and pt_hi < 0)
    if pt_sig:
        direction = 'BETTER' if pt_diff > 0 else 'WORSE'
        LOG(f'     ✅ CI excludes 0 ({direction})')
    else:
        LOG(f'     ⚠ CI includes 0 — not significant')

    LOG(f'')
    LOG(f'  4. PAIRED MAD DIFFERENCE bootstrap (weak test, for comparison):')
    LOG(f'     point estimate: {mad_diff:+.4f} m')
    LOG(f'     95% CI: [{mad_lo:+.4f}, {mad_hi:+.4f}], width={mad_hi-mad_lo:.4f}')
    mad_sig = (mad_lo > 0 and mad_hi > 0) or (mad_lo < 0 and mad_hi < 0)
    if mad_sig:
        direction = 'BETTER' if mad_diff > 0 else 'WORSE'
        LOG(f'     ✅ CI excludes 0 ({direction})')
    else:
        LOG(f'     ⚠ CI includes 0 — not significant')

    LOG(f'')
    LOG(f'  Original decision: {original_decision}')

    # Final verdict — combine sign + wilcoxon + per-point CI
    tests_passing = sum([
        sign_sig,
        wilc_sig if p_wilc is not None else False,
        pt_sig,
    ])
    if tests_passing >= 2:
        if median_diff is not None:
            direction = 'BETTER' if median_diff > 0 else 'WORSE'
        else:
            direction = 'BETTER' if pt_diff > 0 else 'WORSE'
        final_verdict = f'SIGNIFICANT ({direction}) — {tests_passing}/3 tests agree'
    elif tests_passing == 1:
        final_verdict = 'MARGINAL — 1 of 3 tests significant'
    else:
        final_verdict = 'NOT SIGNIFICANT — 0 of 3 tests'

    LOG(f'  → FINAL VERDICT: {final_verdict}')

    return {
        'pair': f'{name_a}_vs_{name_b}',
        'description': description,
        'original_decision': original_decision,
        'n_points': int(n),
        'mad_a': mad_a,
        'mad_b': mad_b,
        'delta_mad': float(mad_b - mad_a),
        'sign_test': {
            'n_better': n_better,
            'n_worse': n_worse,
            'n_tied': n_tied,
            'p_value': p_sign,
            'pct_better': pct_better,
            'significant_5pct': bool(sign_sig),
            'threshold_for_5pct': sign_threshold_5pct,
        },
        'wilcoxon': {
            'statistic': w_stat,
            'p_value': p_wilc,
            'median_diff_abs_dH': median_diff,
            'significant_5pct': bool(wilc_sig) if p_wilc is not None else False,
        },
        'per_point_median_bootstrap': {
            'point_estimate': pt_diff,
            'ci_95': [pt_lo, pt_hi],
            'ci_width': pt_hi - pt_lo,
            'significant': bool(pt_sig),
        },
        'paired_mad_bootstrap_weak': {
            'point_estimate': mad_diff,
            'ci_95': [mad_lo, mad_hi],
            'ci_width': mad_hi - mad_lo,
            'significant': bool(mad_sig),
        },
        'final_verdict': final_verdict,
        'tests_passing': tests_passing,
    }


def main():
    LOG('=' * 70)
    LOG('Sign test + Wilcoxon recheck (powerful per-point tests)')
    LOG('=' * 70)
    LOG('On 40 common holdout points, per-point dH from v7/v8/v9b/v11')
    LOG('Three tests per pair:')
    LOG('  1. Sign test (binomial): how many points have smaller |dH| in B?')
    LOG('  2. Wilcoxon signed-rank (uses magnitude, robust to outliers)')
    LOG('  3. Per-point median bootstrap (proper paired test)')
    LOG('Plus weak test for comparison: paired MAD-difference (used in 5.23)')

    # Load all versions
    versions = {}
    for v in ['v7', 'v8', 'v9b', 'v11']:
        p = DATA / f'spb_passport_validation_{v}.csv'
        if p.exists():
            versions[v] = pd.read_csv(p)
        else:
            # try git extraction
            import subprocess
            commit_map = {'v7': '898fb96', 'v8': '30af54b', 'v9b': '79681ae', 'v11': '225feaa'}
            r = subprocess.run(['git', 'show', f'{commit_map[v]}:spb_passport_validation_{v}.csv'],
                             capture_output=True, text=True, cwd='/home/z/my-project')
            if r.returncode == 0:
                p.write_text(r.stdout)
                versions[v] = pd.read_csv(p)
                LOG(f'  extracted {v} from git')

    # Build common holdout (v8 reference)
    v8_hold = versions['v8'][versions['v8'].role == 'holdout'][['lat', 'lon', 'dH']].rename(columns={'dH': 'dH_v8'})
    common = v8_hold.copy()
    for v in ['v7', 'v9b', 'v11']:
        if v not in versions:
            continue
        if 'role' not in versions[v].columns:
            v_data = versions[v][['lat', 'lon', 'dH']].rename(columns={'dH': f'dH_{v}'})
        else:
            v_data = versions[v][versions[v].role == 'holdout'][['lat', 'lon', 'dH']].rename(columns={'dH': f'dH_{v}'})
        common = common.merge(v_data, on=['lat', 'lon'], how='inner')

    LOG(f'\nCommon holdout: {len(common)} points')
    LOG(f'\nMAD per version:')
    for v in ['v7', 'v8', 'v9b', 'v11']:
        col = f'dH_{v}'
        if col in common.columns:
            m = float(np.median(np.abs(common[col] - np.median(common[col]))) * 1.4826)
            LOG(f'  {v}: MAD = {m:.4f}')

    # Three pairs
    pairs = [
        ('v7', 'v8', 'passport train in prior (main improvement, BEST)',
         'kept as BEST (Δ +0.284)'),
        ('v8', 'v9b', 'choose_block_px (was kept, detail 23 hurt)',
         'kept — Δ -0.134 (point estimate)'),
        ('v8', 'v11', 'accept_dsm_with_relief (HURT, REVERTED)',
         'REVERTED — Δ +0.392 (HURT)'),
    ]

    results = []
    for v_a, v_b, description, original_decision in pairs:
        LOG('\n' + '=' * 70)
        r = analyze_pair(v_a, v_b,
                       common[f'dH_{v_a}'].values,
                       common[f'dH_{v_b}'].values,
                       description, original_decision)
        results.append(r)

    # cap_far_field — no data
    LOG('\n' + '=' * 70)
    LOG('cap_far_field (v9 vs v8): NO per-point dH saved')
    LOG('=' * 70)
    LOG('  Cannot run sign test / Wilcoxon without per-point dH.')
    LOG('  Decision based on point estimate Δ MAD +0.457.')
    LOG('  Skip until pipeline re-run with per-point dH saved.')
    results.append({
        'pair': 'v8_vs_v9_cap_far_field',
        'description': 'cap_far_field (REJECTED, no per-point dH)',
        'final_verdict': 'CANNOT TEST — no per-point dH saved',
        'note': 'Pipeline re-run needed (~30 min) to save per-point dH',
    })

    # === SUMMARY ===
    LOG('\n' + '=' * 70)
    LOG('SUMMARY')
    LOG('=' * 70)
    LOG(f'\n{"Pair":<25s} {"n":>4s} {"Sign":>16s} {"Wilcoxon":>14s} {"Per-pt CI":>22s} {"Tests/3":>9s} {"Verdict":>30s}')
    LOG(f'{"-"*25} {"-"*4} {"-"*16} {"-"*14} {"-"*22} {"-"*9} {"-"*30}')
    for r in results:
        if 'sign_test' not in r:
            LOG(f'{r["pair"][:25]:<25s} {"N/A":>4s} {"N/A":>16s} {"N/A":>14s} {"N/A":>22s} {"N/A":>9s} {r["final_verdict"][:30]:>30s}')
            continue
        st = r['sign_test']
        wc = r['wilcoxon']
        pt = r['per_point_median_bootstrap']
        sign_str = f'{st["n_better"]}/{st["n_better"]+st["n_worse"]} (p={st["p_value"]:.3f})'
        wilc_str = f'p={wc["p_value"]:.3f}' if wc['p_value'] is not None else 'N/A'
        pt_str = f'[{pt["ci_95"][0]:+.2f}, {pt["ci_95"][1]:+.2f}]'
        LOG(f'{r["pair"][:25]:<25s} {r["n_points"]:>4d} {sign_str:>16s} {wilc_str:>14s} {pt_str:>22s} {r["tests_passing"]:>9d} {r["final_verdict"][:30]:>30s}')

    # === ANALYSIS ===
    LOG('\n' + '=' * 70)
    LOG('ANALYSIS')
    LOG('=' * 70)
    LOG('\nSign test interpretation (n=40, two-sided, H0: p=0.5):')
    LOG(f'  27/40 better → p=0.038 (significant)')
    LOG(f'  28/40 better → p=0.017 (significant)')
    LOG(f'  26/40 better → p=0.081 (marginal)')
    LOG('')
    LOG('So we need ≥27 out of 40 points to show improvement at 5% level.')

    for r in results:
        if 'sign_test' not in r:
            continue
        LOG(f'\n{r["description"]}:')
        st = r['sign_test']
        LOG(f'  Sign test: {st["n_better"]} better, {st["n_worse"]} worse (out of {st["n_better"]+st["n_worse"]})')
        LOG(f'  Binomial p = {st["p_value"]:.4f}')
        wc = r['wilcoxon']
        if wc['p_value'] is not None:
            LOG(f'  Wilcoxon p = {wc["p_value"]:.4f}, median |dH_A|-|dH_B| = {wc["median_diff_abs_dH"]:+.4f}m')
        LOG(f'  Final: {r["final_verdict"]}')

    # Save
    summary = {
        'experiment': 'Sign test + Wilcoxon recheck (powerful per-point tests)',
        'methodology': 'Per-point tests instead of MAD-difference (weak)',
        'tests_used': [
            '1. Sign test (binomial, H0: P(better)=0.5)',
            '2. Wilcoxon signed-rank (uses magnitude, robust to outliers)',
            '3. Per-point median |dH_A|-|dH_B| bootstrap (proper paired)',
        ],
        'n_holdout': 40,
        'significance_threshold': '27/40 better → p=0.038 (significant at 5%)',
        'results': results,
    }
    out = DATA / 'sign_test_wilcoxon_recheck.json'
    out.write_text(json.dumps(summary, indent=2, default=str))
    LOG(f'\nSaved → {out}')


if __name__ == '__main__':
    main()
