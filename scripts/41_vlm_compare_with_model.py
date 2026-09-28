"""
Compare VLM-extracted elevation marks with ArcticDEM model.
Only projects with absolute elevation marks (514913 Дунайский, 11002 Комендантский) are valid.
Projects with relative marks (7000 = ±0.000, +3.150) cannot be compared.
"""
import pandas as pd
import numpy as np
import rasterio
from rasterio.warp import transform as warp_transform
from scipy import stats
import json

MODEL_PATH = '/home/z/my-project/upload/spb_ground_surface_2m.tif'
MARKS_CSV = '/home/z/my-project/scripts/data/archi_plans_vlm_geolocated.csv'

# Load marks
df = pd.read_csv(MARKS_CSV)
print(f'Loaded {len(df)} VLM marks')

# Filter: only absolute elevation marks (Z > 1m AND Z < 100m, exclude ±0.000 / +0.150 style relative)
# Project 7000 has values like -0.15, 1.07, 2.10, 3.15 — clearly relative (±0.000 to +3.150)
# Project 512480 has Z=100 — likely codes
# Project 514913 has Z 12.92-15.88 — absolute Baltic heights ✓
# Project 11002 has Z -1.70 to 45.50 — MIXED (some absolute, some relative)

# Filter: keep only Z in plausible absolute range (1-50m), excluding obvious relative marks
mask = (
    (df['value'] >= 1) & (df['value'] <= 50) &
    # Exclude project 7000 (relative marks)
    (df['project_id'] != '7000') &
    # Exclude project 512480 (codes)
    (df['project_id'] != '512480')
)
df_abs = df[mask].copy()
print(f'After filter (Z 1-50m, exclude relative/code): {len(df_abs)}')

# Per-project breakdown
print('\n=== Per-project absolute marks ===')
for pid, sub in df_abs.groupby('project_id'):
    print(f'  project {pid}: n={len(sub)}, Z=[{sub["value"].min():.2f}, {sub["value"].max():.2f}], med={sub["value"].median():.2f}')

# Sample model at each point
print('\n=== Sampling model ===')
ds = rasterio.open(MODEL_PATH)
model_nodata = ds.nodata
print(f'Model: {ds.bounds}, CRS={ds.crs}')

h_models = []
for idx, row in df_abs.iterrows():
    try:
        xs, ys = warp_transform('EPSG:4326', ds.crs, [row['lon']], [row['lat']])
        inv_transform = ~ds.transform
        px_cols, px_rows = inv_transform * (np.array(xs), np.array(ys))
        r = int(np.floor(px_rows[0]))
        c = int(np.floor(px_cols[0]))
        if 0 <= r < ds.height and 0 <= c < ds.width:
            sample = ds.read(1, window=((r, r+1), (c, c+1)))
            h = float(sample[0, 0])
            if h != model_nodata and not np.isnan(h):
                h_models.append(h)
            else:
                h_models.append(np.nan)
        else:
            h_models.append(np.nan)
    except:
        h_models.append(np.nan)

df_abs['h_model'] = h_models
df_valid = df_abs.dropna(subset=['h_model']).copy()
df_valid['dH'] = df_valid['value'] - df_valid['h_model']
df_valid['abs_dH'] = df_valid['dH'].abs()
print(f'Valid (model hit): {len(df_valid)}')

# Per-project comparison
print('\n=== Per-project comparison ===')
per_project_stats = []
for pid, sub in df_valid.groupby('project_id'):
    lat = sub['lat'].iloc[0]
    lon = sub['lon'].iloc[0]
    n = len(sub)
    if n < 5:
        print(f'  project {pid}: n={n} too few, skipping')
        continue
    
    survey_z_med = sub['value'].median()
    model_z_med = sub['h_model'].median()
    median_dH = sub['dH'].median()
    mean_dH = sub['dH'].mean()
    mad = sub['abs_dH'].median()
    mean_abs = sub['abs_dH'].mean()
    p95 = sub['abs_dH'].quantile(0.95)
    n_pos = int((sub['dH'] > 0).sum())
    n_neg = int((sub['dH'] < 0).sum())
    
    # Pearson correlation
    r, p_r = stats.pearsonr(sub['value'], sub['h_model'])
    rho, p_rho = stats.spearmanr(sub['value'], sub['h_model'])
    
    try:
        stat_w, p_w = stats.wilcoxon(sub['value'], sub['h_model'])
    except:
        p_w = None
    p_sign = stats.binomtest(n_pos, n_pos+n_neg, 0.5).pvalue if n_pos+n_neg > 0 else None
    
    print(f'\n  project {pid} (lat={lat:.4f}, lon={lon:.4f}):')
    print(f'    n={n}, survey Z med={survey_z_med:.2f}m, model Z med={model_z_med:.2f}m')
    print(f'    dH: median={median_dH:+.2f}m, mean={mean_dH:+.2f}m')
    print(f'    |dH|: MAD={mad:.2f}m, mean={mean_abs:.2f}m, p95={p95:.2f}m')
    print(f'    Pearson r={r:.3f} (p={p_r:.2e})')
    print(f'    Spearman ρ={rho:.3f} (p={p_rho:.2e})')
    if p_w:
        print(f'    Wilcoxon p={p_w:.4e}')
    print(f'    Sign test: n_pos={n_pos}, n_neg={n_neg}, p={p_sign:.4e}')
    
    per_project_stats.append({
        'project_id': pid,
        'n': n,
        'lat': float(lat),
        'lon': float(lon),
        'survey_z_median': float(survey_z_med),
        'model_z_median': float(model_z_med),
        'dH_median': float(median_dH),
        'dH_mean': float(mean_dH),
        'abs_dH_mad': float(mad),
        'abs_dH_mean': float(mean_abs),
        'abs_dH_p95': float(p95),
        'pearson_r': float(r),
        'pearson_p': float(p_r),
        'spearman_rho': float(rho),
        'spearman_p': float(p_rho),
        'wilcoxon_p': float(p_w) if p_w else None,
        'sign_test_p': float(p_sign) if p_sign else None,
        'n_pos': n_pos,
        'n_neg': n_neg,
    })

# Aggregate
print('\n\n=== AGGREGATE ===')
n = len(df_valid)
median_dH = float(df_valid['dH'].median())
mean_dH = float(df_valid['dH'].mean())
mad = float(df_valid['abs_dH'].median())
mean_abs = float(df_valid['abs_dH'].mean())
p95 = float(df_valid['abs_dH'].quantile(0.95))
n_pos = int((df_valid['dH'] > 0).sum())
n_neg = int((df_valid['dH'] < 0).sum())

stat_w, p_w = stats.wilcoxon(df_valid['value'], df_valid['h_model'])
p_sign = stats.binomtest(n_pos, n_pos+n_neg, 0.5).pvalue

np.random.seed(42)
boots = [np.median(df_valid['dH'].values[np.random.choice(n, n, replace=True)]) for _ in range(2000)]
ci_lo, ci_hi = float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))

print(f'n = {n}')
print(f'dH: median={median_dH:+.3f}m, mean={mean_dH:+.3f}m, std={df_valid["dH"].std():.2f}m')
print(f'|dH|: MAD={mad:.2f}m, mean={mean_abs:.2f}m, p95={p95:.2f}m')
print(f'survey > model: {n_pos}/{n} ({n_pos/n*100:.1f}%)')
print(f'Wilcoxon p = {p_w:.4e}')
print(f'Sign test p = {p_sign:.4e}')
print(f'Bootstrap 95% CI of median dH: [{ci_lo:+.3f}, {ci_hi:+.3f}]')

# Save
df_valid.to_csv('/home/z/my-project/scripts/data/archi_plans_vlm_comparison.csv', index=False)

summary = {
    'source': 'archi_plans VLM extraction',
    'total_marks_extracted': 272,
    'plausible_z_1_50': int(len(df_abs)),
    'valid_model_hits': n,
    'per_project': per_project_stats,
    'aggregate': {
        'n': n,
        'survey_z_median': float(df_valid['value'].median()),
        'model_z_median': float(df_valid['h_model'].median()),
        'dH_median': median_dH,
        'dH_mean': mean_dH,
        'abs_dH_mad': mad,
        'abs_dH_mean': mean_abs,
        'abs_dH_p95': p95,
        'n_pos': n_pos,
        'n_neg': n_neg,
        'wilcoxon_p': float(p_w),
        'sign_test_p': float(p_sign),
        'bootstrap_ci_95': [ci_lo, ci_hi],
    },
}
with open('/home/z/my-project/scripts/data/archi_plans_vlm_FINAL_summary.json', 'w') as f:
    json.dump(summary, f, indent=2, ensure_ascii=False, default=float)
print(f'\nSaved to archi_plans_vlm_comparison.csv and archi_plans_vlm_FINAL_summary.json')
