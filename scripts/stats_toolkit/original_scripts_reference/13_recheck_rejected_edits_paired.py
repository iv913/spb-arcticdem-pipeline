#!/usr/bin/env python3
"""
13_recheck_rejected_edits_paired.py
===================================
Перепроверка отвергнутых правок парным бутстрапом с per-point dH.

КОНТЕКСТ (пользователь):
- cap_far_field, choose_block_px, accept_dsm_with_relief — отвергнуты
  на различиях < 0.4м при CI ширине ~1.0м
- Бутстрап показал, что такие различия неразличимы
- Нужно перепроверить парным бутстрапом (одни и те же точки)

PER-POINT dH ИЗ git history:
- spb_passport_validation_v7.csv  — v7 (no passport train)         MAD 1.342
- spb_passport_validation_v8.csv  — v8 (passport train in prior)   MAD 1.058 ← BEST
- spb_passport_validation_v9b.csv — v9b (choose_block_px)         MAD 1.191
- spb_passport_validation_v11.csv — v11 (accept_dsm_with_relief)   MAD 1.450 ← HURT

ИЗ git commit 225feaa:
  v7 (no passport):       1.342
  v8 (passport, blk=60):  1.058 ← BEST
  v9 (passport+cap):      1.515 ← cap HURT (no CSV saved)
  v9b (passport+blk):     1.191 ← choose_block_px slightly worse
  v11 (passport+relief):  1.450 ← relief HURT

ПАРЫ ДЛЯ ПРОВЕРКИ:
1. v7 vs v8      — Δ -0.284 (main improvement, 1.342 → 1.058)
2. v8 vs v9b     — Δ +0.133 (1.058 → 1.191) "kept but detail 23 hurt"
3. v8 vs v11     — Δ +0.392 (1.058 → 1.450) HURT
4. v9 (cap)      — NO per-point dH saved, cannot check
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


def paired_bootstrap_diff(dH_a, dH_b, n_boot=5000, ci=0.95, seed=42):
    """Paired bootstrap of MAD(A) - MAD(B).

    A и B — одни и те же точки (по key/lat/lon). Бутстрапим индексы,
    для каждой выборки считаем MAD(A_sample) - MAD(B_sample).
    """
    dH_a = np.asarray(dH_a, float)
    dH_b = np.asarray(dH_b, float)
    n = len(dH_a)
    assert len(dH_b) == n

    rng = np.random.default_rng(seed)
    boot_diffs = np.empty(n_boot)
    for i in range(n_boot):
        idx = rng.choice(n, size=n, replace=True)
        a_sample = dH_a[idx]
        b_sample = dH_b[idx]
        m_a = np.median(a_sample)
        m_b = np.median(b_sample)
        mad_a = np.median(np.abs(a_sample - m_a)) * 1.4826
        mad_b = np.median(np.abs(b_sample - m_b)) * 1.4826
        boot_diffs[i] = mad_a - mad_b

    point_diff = mad(dH_a) - mad(dH_b)
    alpha = (1 - ci) / 2
    lo, hi = np.percentile(boot_diffs, [alpha * 100, (1 - alpha) * 100])
    # p-value: two-sided
    if point_diff > 0:
        p = np.mean(boot_diffs <= 0)
    else:
        p = np.mean(boot_diffs >= 0)
    p = 2 * min(p, 1 - p)
    return float(point_diff), float(lo), float(hi), float(p)


def load_version(version_name, from_persistent=False):
    """Load a passport_validation CSV by version name.

    Older versions (v7) don't have 'role' column. They use 'detail' to indicate
    role: detail=22 = ICESat train anchor, others = mixed.
    For comparison, we use the SAME points as v8/v9b/v11 holdout.
    """
    paths_to_try = [
        DATA / f'spb_passport_validation_{version_name}.csv',
        Path('/tmp/my-project/scripts/data') / f'spb_passport_validation_{version_name}.csv',
    ]
    if from_persistent:
        paths_to_try.append(
            Path('/home/z/my-project/upload/scripts_persistent') / f'spb_passport_validation_{version_name}.csv'
        )
    for p in paths_to_try:
        if p.exists():
            LOG(f'  loaded {version_name} from {p}')
            df = pd.read_csv(p)
            # If no 'role' column, we'll match points by (lat, lon) later
            return df
    LOG(f'  NOT FOUND: {version_name}')
    return None


def extract_from_git(version_name):
    """Extract CSV from git history (last commit that has it)."""
    import subprocess
    # Find commits that touched the file
    try:
        # Try v9b → commit 79681ae
        # Try v8 → commit 30af54b
        # Try v7 → commit 898fb96
        # Try v11 → commit 225feaa
        commit_map = {
            'v7': '898fb96',
            'v8': '30af54b',
            'v9b': '79681ae',
            'v11': '225feaa',
        }
        commit = commit_map.get(version_name)
        if not commit:
            LOG(f'  no commit known for {version_name}')
            return None
        out_path = DATA / f'spb_passport_validation_{version_name}.csv'
        result = subprocess.run(
            ['git', 'show', f'{commit}:spb_passport_validation_{version_name}.csv'],
            capture_output=True, text=True, cwd='/home/z/my-project'
        )
        if result.returncode == 0 and result.stdout:
            out_path.write_text(result.stdout)
            LOG(f'  extracted {version_name} from git commit {commit} → {out_path}')
            return pd.read_csv(out_path)
        else:
            LOG(f'  git show failed: {result.stderr[:200]}')
            return None
    except Exception as e:
        LOG(f'  git extraction failed: {e}')
        return None


def main():
    LOG('=' * 70)
    LOG('Re-check rejected edits with paired bootstrap (per-point dH)')
    LOG('=' * 70)

    # Load all versions
    LOG('\nLoading versions from git history:')
    versions = {}
    for v in ['v7', 'v8', 'v9b', 'v11']:
        df = load_version(v)
        if df is None:
            df = extract_from_git(v)
        if df is not None:
            versions[v] = df
            # Compute MAD on holdout if 'role' column exists, otherwise on all
            if 'role' in df.columns:
                hold = df[df.role == 'holdout']
                n_hold = len(hold)
                mad_val = mad(hold.dH.values) if n_hold > 0 else float('nan')
            else:
                # v7 has no 'role' column — we'll match by lat/lon to v8 holdout later
                n_hold = '(no role col)'
                mad_val = '(pending)'
            LOG(f'  {v}: {len(df)} rows, holdout={n_hold}, MAD={mad_val}')

    # All versions should share the same holdout points (same lat/lon)
    # Verify by joining on (lat, lon)
    LOG('\nVerifying common holdout points across versions:')

    # Take v8 as reference
    if 'v8' not in versions:
        LOG('ERROR: v8 not loaded')
        return
    v8_hold = versions['v8'][versions['v8'].role == 'holdout'][['lat', 'lon', 'dH']].rename(columns={'dH': 'dH_v8'})

    common = v8_hold.copy()
    for v in ['v7', 'v9b', 'v11']:
        if v not in versions:
            continue
        # For v7 (no role column), match all points
        if 'role' not in versions[v].columns:
            v_data = versions[v][['lat', 'lon', 'dH']].rename(columns={'dH': f'dH_{v}'})
        else:
            v_data = versions[v][versions[v].role == 'holdout'][['lat', 'lon', 'dH']].rename(columns={'dH': f'dH_{v}'})
        common = common.merge(v_data, on=['lat', 'lon'], how='inner')

    LOG(f'  common holdout points across all versions: {len(common)}')

    # Print summary
    LOG(f'\nHoldout MAD per version:')
    for v in ['v7', 'v8', 'v9b', 'v11']:
        col = f'dH_{v}'
        if col in common.columns:
            m = mad(common[col].values)
            LOG(f'  {v}: MAD = {m:.4f} (n={len(common)})')

    # === PAIRED BOOTSTRAP FOR EACH PAIR ===
    LOG('\n' + '=' * 70)
    LOG('PAIRED BOOTSTRAP for each pair')
    LOG('=' * 70)
    LOG(f'\n{"Pair":<25s} {"Δ MAD":>10s} {"95% CI":>26s} {"width":>8s} {"p-value":>10s} {"verdict":>30s}')
    LOG(f'{"-"*25} {"-"*10} {"-"*26} {"-"*8} {"-"*10} {"-"*30}')

    pairs_to_check = [
        ('v7', 'v8', 'passport train in prior (main improvement)'),
        ('v8', 'v9b', 'choose_block_px (was kept, detail 23 hurt)'),
        ('v8', 'v11', 'accept_dsm_with_relief (HURT, REVERTED)'),
    ]

    results = []
    for v_a, v_b, description in pairs_to_check:
        col_a = f'dH_{v_a}'
        col_b = f'dH_{v_b}'
        if col_a not in common.columns or col_b not in common.columns:
            LOG(f'  SKIP {v_a} vs {v_b} — missing data')
            continue

        dH_a = common[col_a].values
        dH_b = common[col_b].values

        # MAD difference: positive means A is WORSE (higher MAD)
        point_diff, ci_lo, ci_hi, p_val = paired_bootstrap_diff(dH_a, dH_b, n_boot=5000)

        if ci_lo > 0:
            verdict = f'{v_a} WORSE than {v_b} (CI > 0)'
        elif ci_hi < 0:
            verdict = f'{v_a} BETTER than {v_b} (CI < 0)'
        else:
            verdict = 'INDISTINGUISHABLE (CI includes 0)'

        LOG(f'{v_a+" vs "+v_b:<25s} {point_diff:>+10.4f} [{ci_lo:>+8.4f}, {ci_hi:>+8.4f}] {ci_hi-ci_lo:>8.4f} {p_val:>10.4f} {verdict:>30s}')

        results.append({
            'pair': f'{v_a}_vs_{v_b}',
            'description': description,
            'n_points': int(len(common)),
            'mad_a': float(mad(dH_a)),
            'mad_b': float(mad(dH_b)),
            'delta_mad': float(point_diff),
            'paired_ci_95': [float(ci_lo), float(ci_hi)],
            'ci_width': float(ci_hi - ci_lo),
            'p_value': float(p_val),
            'verdict': verdict,
        })

    # === ANALYSIS ===
    LOG('\n' + '=' * 70)
    LOG('ANALYSIS')
    LOG('=' * 70)

    for r in results:
        LOG(f'\n{r["description"]}:')
        LOG(f'  Δ MAD = {r["delta_mad"]:+.4f} m (MAD {r["mad_a"]:.3f} → {r["mad_b"]:.3f})')
        LOG(f'  Paired 95% CI: [{r["paired_ci_95"][0]:+.4f}, {r["paired_ci_95"][1]:+.4f}], width={r["ci_width"]:.4f}')
        LOG(f'  p-value: {r["p_value"]:.4f}')
        LOG(f'  Verdict: {r["verdict"]}')

        # Cross-reference with original decision
        pair = r['pair']
        if pair == 'v7_vs_v8':
            original = 'v8 was kept (best, main improvement)'
            LOG(f'  Original decision: {original}')
            if r['paired_ci_95'][1] < 0:
                LOG(f'  ✅ Confirmed: v8 IS statistically better than v7')
            else:
                LOG(f'  ⚠ v8 improvement NOT statistically significant on n={r["n_points"]}')
        elif pair == 'v8_vs_v9b':
            original = 'v9b was kept (detail 23 hurt, but holdout MAD worse 1.058 → 1.191)'
            LOG(f'  Original decision: {original}')
            if r['paired_ci_95'][0] > 0:
                LOG(f'  ✅ Confirmed: choose_block_px IS statistically worse for holdout')
            elif r['paired_ci_95'][1] < 0:
                LOG(f'  ⚠ choose_block_px is actually BETTER (CI < 0) — decision was wrong')
            else:
                LOG(f'  ⚠ choose_block_px change is INDISTINGUISHABLE from v8 — decision uncertain')
        elif pair == 'v8_vs_v11':
            original = 'accept_dsm_with_relief was REVERTED (HURT, 1.058 → 1.450)'
            LOG(f'  Original decision: {original}')
            if r['paired_ci_95'][0] > 0:
                LOG(f'  ✅ Confirmed: accept_dsm_with_relief IS statistically worse')
            elif r['paired_ci_95'][1] < 0:
                LOG(f'  ⚠ accept_dsm_with_relief is actually BETTER — decision was wrong')
            else:
                LOG(f'  ⚠ accept_dsm_with_relief change is INDISTINGUISHABLE — revert uncertain')

    # === CAP_FAR_FIELD ===
    LOG('\n' + '=' * 70)
    LOG('cap_far_field (v9): NO per-point dH saved')
    LOG('=' * 70)
    LOG('  v9 (passport+cap): MAD 1.515 (from commit message)')
    LOG('  v8 baseline:       MAD 1.058')
    LOG('  Δ = +0.457 (HURT)')
    LOG('  No CSV saved → cannot do paired bootstrap.')
    LOG('  Decision: cap_far_field was rejected based on point estimate.')
    LOG('  Note: Δ +0.457 > typical CI width (~0.4-1.0m for n=40)')
    LOG('  → LIKELY genuinely worse, but cannot confirm without per-point dH.')

    cap_far_field_result = {
        'pair': 'v8_vs_v9_cap_far_field',
        'description': 'cap_far_field (REJECTED, no per-point dH)',
        'n_points': None,
        'mad_a': 1.058,  # v8
        'mad_b': 1.515,  # v9
        'delta_mad': 0.457,
        'paired_ci_95': None,
        'ci_width': None,
        'p_value': None,
        'verdict': 'CANNOT VERIFY — no per-point dH saved; Δ > typical CI width, likely genuinely worse',
        'note': 'v9 was short-lived, never had CSV exported. To verify, need to re-run pipeline with cap_far_field enabled.',
    }
    results.append(cap_far_field_result)

    # === SUMMARY ===
    LOG('\n' + '=' * 70)
    LOG('SUMMARY')
    LOG('=' * 70)
    LOG(f'\n{"Edit":<35s} {"Δ MAD":>8s} {"95% CI":>26s} {"p":>8s} {"verdict":>35s}')
    LOG(f'{"-"*35} {"-"*8} {"-"*26} {"-"*8} {"-"*35}')
    for r in results:
        if r.get('paired_ci_95'):
            ci_str = f'[{r["paired_ci_95"][0]:+.3f}, {r["paired_ci_95"][1]:+.3f}]'
            p_str = f'{r["p_value"]:.3f}'
        else:
            ci_str = 'N/A'
            p_str = 'N/A'
        LOG(f'{r["description"][:35]:<35s} {r["delta_mad"]:>+8.3f} {ci_str:>26s} {p_str:>8s} {r["verdict"][:35]:>35s}')

    # Save
    summary = {
        'experiment': 'Re-check rejected edits with paired bootstrap',
        'methodology': 'Paired bootstrap of MAD difference on common holdout points (matched by lat/lon)',
        'n_boot': 5000,
        'common_holdout_n': int(len(common)),
        'results': results,
        'conclusions': [
            f'Common holdout points across v7/v8/v9b/v11: {len(common)}',
            'v7 vs v8 (passport train): main improvement, check if statistically significant',
            'v8 vs v9b (choose_block_px): Δ +0.133, was kept — check if statistically significant',
            'v8 vs v11 (accept_dsm_with_relief): Δ +0.392, was REVERTED — check significance',
            'cap_far_field: cannot verify — no per-point dH saved (v9 short-lived)',
        ],
    }
    out = DATA / 'recheck_rejected_edits_paired.json'
    out.write_text(json.dumps(summary, indent=2, default=str))
    LOG(f'\nSaved → {out}')


if __name__ == '__main__':
    main()
