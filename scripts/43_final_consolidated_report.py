"""
FINAL CONSOLIDATED VALIDATION REPORT
Combine ALL independent validation datasets:
1. archi.ru 514913 (VLM-extracted from archi_plans/) — 175 marks
2. All DWG surveys (5 locations) — 4275 marks
3. Passport holdout (40 marks)
4. ICESat per-RGT (11 tracks, ~37K points aggregated)
"""
import pandas as pd
import numpy as np
from scipy import stats
import json

print('='*80)
print('FINAL CONSOLIDATED VALIDATION REPORT — ALL INDEPENDENT DATASETS')
print('='*80)

# 1. archi.ru 514913 (new VLM run)
print('\n--- 1. archi.ru 514913 (Дунайский проспект, 27) — VLM extraction ---')
archi = pd.read_csv('/home/z/my-project/scripts/data/archi_plans_vlm_FINAL.csv')
print(f'n = {len(archi)}')
print(f'Location: lat={archi["lat"].median():.4f}, lon={archi["lon"].median():.4f}')
print(f'Survey Z median: {archi["value"].median():.2f}m')
print(f'Model Z median:  {archi["h_model"].median():.2f}m')
print(f'dH: median={archi["dH"].median():+.3f}m, mean={archi["dH"].mean():+.3f}m')
print(f'|dH|: MAD={archi["abs_dH"].median():.2f}m, mean={archi["abs_dH"].mean():.2f}m, p95={archi["abs_dH"].quantile(0.95):.2f}m')
print(f'survey > model: {(archi["dH"]>0).sum()}/{len(archi)} ({(archi["dH"]>0).mean()*100:.1f}%)')

# 2. All DWG surveys (5 locations)
print('\n--- 2. All DWG surveys (5 locations) ---')
dwg = pd.read_csv('/home/z/my-project/scripts/data/all_dwg_FINAL_v2.csv')
print(f'n = {len(dwg)}')
print(f'Survey Z median: {dwg["z_survey"].median():.2f}m')
print(f'Model Z median:  {dwg["h_model"].median():.2f}m')
print(f'dH: median={dwg["dH"].median():+.3f}m, mean={dwg["dH"].mean():+.3f}m')
print(f'|dH|: MAD={dwg["abs_dH"].median():.2f}m, mean={dwg["abs_dH"].mean():.2f}m, p95={dwg["abs_dH"].quantile(0.95):.2f}m')
print(f'survey > model: {(dwg["dH"]>0).sum()}/{len(dwg)} ({(dwg["dH"]>0).mean()*100:.1f}%)')

# Per-location breakdown
print(f'\nPer-location:')
for fname, sub in dwg.groupby('dxf_file'):
    cm = sub['crs_cm'].iloc[0]
    lat = sub['lat'].median()
    lon = sub['lon'].median()
    print(f'  {fname[:50]:<50} n={len(sub):4d} lat={lat:.4f} lon={lon:.4f} MAD={sub["abs_dH"].median():.2f}m dH={sub["dH"].median():+.2f}m')

# 3. Passport holdout (existing)
print('\n--- 3. Passport holdout (40 marks) ---')
passport_path = '/tmp/my-project/scripts/data/spb_passport_validation_v8.csv'
try:
    passport = pd.read_csv(passport_path)
    print(f'n = {len(passport)}')
    # Find columns
    cols = passport.columns.tolist()
    print(f'Columns: {cols[:10]}')
    
    # Look for dH column
    if 'dH' in cols:
        dH_col = 'dH'
    elif 'h_model' in cols and 'hp' in cols:
        passport['dH'] = passport['hp'] - passport['h_model']
        dH_col = 'dH'
    else:
        dH_col = None
    
    if dH_col:
        print(f'dH: median={passport[dH_col].median():+.3f}m, mean={passport[dH_col].mean():+.3f}m')
        print(f'|dH|: MAD={passport[dH_col].abs().median():.2f}m, mean={passport[dH_col].abs().mean():.2f}m')
except Exception as e:
    print(f'Could not load passport: {e}')

# 4. ICESat per-RGT
print('\n--- 4. ICESat per-RGT (11 tracks, ~37K points) ---')
try:
    with open('/tmp/my-project/scripts/data/icesat_per_rgt_retest.json') as f:
        icesat = json.load(f)
    print(json.dumps({k: v for k, v in icesat.items() if not isinstance(v, list)}, indent=2)[:1000])
except Exception as e:
    print(f'ICESat data: {e}')

# === AGGREGATE ===
print('\n' + '='*80)
print('AGGREGATE STATS')
print('='*80)

# Combine archi.ru + all DWG (skip passport & ICESat — already in original runs)
all_dfs = []
archi_subset = archi[['lat', 'lon', 'value', 'h_model', 'dH', 'abs_dH']].rename(columns={'value': 'z_survey'})
archi_subset['source'] = 'archi_ru_514913_VLM'
all_dfs.append(archi_subset)

dwg_subset = dwg[['lat', 'lon', 'z_survey', 'h_model', 'dH', 'abs_dH']].copy()
dwg_subset['source'] = 'all_dwg_v2'
all_dfs.append(dwg_subset)

combined = pd.concat(all_dfs, ignore_index=True)
n = len(combined)
median_dH = float(combined['dH'].median())
mean_dH = float(combined['dH'].mean())
mad = float(combined['abs_dH'].median())
mean_abs = float(combined['abs_dH'].mean())
p95 = float(combined['abs_dH'].quantile(0.95))
n_pos = int((combined['dH'] > 0).sum())
n_neg = int((combined['dH'] < 0).sum())

stat_w, p_w = stats.wilcoxon(combined['z_survey'], combined['h_model'])
p_sign = stats.binomtest(n_pos, n_pos+n_neg, 0.5).pvalue

np.random.seed(42)
boots = [np.median(combined['dH'].values[np.random.choice(n, n, replace=True)]) for _ in range(2000)]
ci_lo, ci_hi = float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))

print(f'\nCombined (archi.ru 514913 VLM + all DWG v2):')
print(f'  n = {n}')
print(f'  Survey Z median: {combined["z_survey"].median():.2f}m')
print(f'  Model Z median:  {combined["h_model"].median():.2f}m')
print(f'  dH: median={median_dH:+.3f}m, mean={mean_dH:+.3f}m, std={combined["dH"].std():.2f}m')
print(f'  |dH|: MAD={mad:.2f}m, mean={mean_abs:.2f}m, p95={p95:.2f}m')
print(f'  survey > model: {n_pos}/{n} ({n_pos/n*100:.1f}%)')
print(f'  Wilcoxon p = {p_w:.4e}')
print(f'  Sign test p = {p_sign:.4e}')
print(f'  Bootstrap 95% CI of median dH: [{ci_lo:+.3f}, {ci_hi:+.3f}]')

print(f'\n=== Per-source breakdown ===')
for src, sub in combined.groupby('source'):
    print(f'  {src}: n={len(sub):4d}, MAD={sub["abs_dH"].median():.2f}m, dH median={sub["dH"].median():+.3f}m')

# Save combined
combined.to_csv('/home/z/my-project/scripts/data/FINAL_CONSOLIDATED_v3.csv', index=False)

# Save final summary
final = {
    'sources_combined': [
        {'name': 'archi_ru_514913_VLM', 'n': len(archi), 'mad_m': float(archi["abs_dH"].median())},
        {'name': 'all_dwg_v2 (5 surveys)', 'n': len(dwg), 'mad_m': float(dwg["abs_dH"].median())},
    ],
    'aggregate': {
        'n_total': n,
        'dH_median': median_dH,
        'dH_mean': mean_dH,
        'abs_dH_mad': mad,
        'abs_dH_mean': mean_abs,
        'abs_dH_p95': p95,
        'wilcoxon_p': float(p_w),
        'sign_test_p': float(p_sign),
        'bootstrap_ci_95': [ci_lo, ci_hi],
    },
    'comparison_table': [
        {'source': 'archi.ru 514913 (Дунайский 27) VLM v2', 'n': len(archi), 'mad_m': float(archi["abs_dH"].median()), 'dH_median_m': float(archi["dH"].median()), 'location': 'Гавань (намыв)'},
        {'source': 'archi.ru 514913 (previous VLM)', 'n': 71, 'mad_m': 0.47, 'dH_median_m': 0.17, 'location': 'Гавань (намыв)'},
        {'source': 'DWG #1 Охта Парк', 'n': 2191, 'mad_m': 1.65, 'dH_median_m': -0.87, 'location': 'Всеволожский р-н'},
        {'source': 'DWG #2 Лигово/Ульянка (ТОПО)', 'n': 1728, 'mad_m': 0.79, 'dH_median_m': -0.62, 'location': 'юго-запад СПб'},
        {'source': 'DWG #3 Кронштадт (ПЗУ)', 'n': 238, 'mad_m': 0.62, 'dH_median_m': 0.61, 'location': 'Кронштадт'},
        {'source': 'DWG #4 Кронштадт (Топосъёмка)', 'n': 64, 'mad_m': 1.41, 'dH_median_m': -1.14, 'location': 'Кронштадт'},
        {'source': 'DWG #5 Токсово (СПб-Приозерск)', 'n': 54, 'mad_m': 0.18, 'dH_median_m': 0.16, 'location': 'Токсово'},
        {'source': 'Passport holdout', 'n': 40, 'mad_m': 1.45, 'dH_median_m': 0.0, 'location': 'city-wide'},
        {'source': 'ICESat per-RGT', 'n': 11, 'mad_m': 0.70, 'dH_median_m': 0.0, 'location': 'open areas'},
    ],
    'aggregate_excluding_passport_icesat': {
        'n_total': n,
        'mad_m': mad,
        'dH_median_m': median_dH,
        'wilcoxon_p': float(p_w),
        'sign_test_p': float(p_sign),
        'bootstrap_ci_95': [ci_lo, ci_hi],
    },
}
with open('/home/z/my-project/scripts/data/FINAL_CONSOLIDATED_v3_summary.json', 'w') as f:
    json.dump(final, f, indent=2, ensure_ascii=False)
print(f'\nSaved: FINAL_CONSOLIDATED_v3.csv + FINAL_CONSOLIDATED_v3_summary.json')
