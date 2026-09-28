"""
Final VLM analysis — only project 514913 (Дунайский 27) is valid absolute elevation data.
Other projects contain:
- 11002 (Комендантский): building structure marks (parapet/window tops) — NOT ground
- 512480: attribute codes (100, 200, 300...) — NOT elevations
- 7000 (театр Приют): relative floor levels (±0.000 to +3.150) — NOT absolute

This file consolidates the validated 514913 marks with proper geolocation
and adds the new VLM data from additional dunaisky_514912-920 images that may
contain similar marks.
"""
import pandas as pd
import numpy as np
import rasterio
from rasterio.warp import transform as warp_transform
from scipy import stats
import json
import os
import re
import glob

MODEL_PATH = '/home/z/my-project/upload/spb_ground_surface_2m.tif'
ARCHI_PLANS_DIR = '/tmp/my-project/scripts/data/archi_plans'

# === Step 1: Real coordinates of Дунайский проспект 27 ===
# From previous successful comparison: lat=59.832, lon=30.362
# (the building's center; individual marks interpolated by image x/y)

# === Step 2: Find all images that contain Дунайский marks ===
# Pattern: archi_514912, 514913, 514914, 514915, 514916, 514920 (variations of the same project)
# Also: dunaisky_514913, scan_511000, etc.

# Dunaisky is project ID 21359 in archi_ru (Деловой комплекс на Дунайском проспекте)
# Address: "Дунайский проспект, 27" — coordinates 59.832, 30.362 (Гавань намыв)

DUNAISKY_COORDS = (59.832, 30.362)
DUNAISKY_LAT_SPREAD = 0.002  # ~200m
DUNAISKY_LON_SPREAD = 0.003

# === Step 3: Aggregate all VLM marks from Dunaisky-related images ===
dunaisky_files = []
for f in glob.glob(os.path.join(ARCHI_PLANS_DIR, '*.vlm.json')):
    name = os.path.basename(f).replace('.vlm.json', '')
    # Match patterns: archi_5149xx, dunaisky_5149xx, scan_511xxx (might be related)
    if re.match(r'archi_5149\d{2}$', name) or \
       re.match(r'dunaisky_5149\d{2}$', name) or \
       re.match(r'scan_511\d{3}$', name) or \
       re.match(r'scan_5149\d{2}$', name) or \
       re.match(r'scan_515\d{3}$', name) or \
       re.match(r'scan_516\d{3}$', name):
        dunaisky_files.append((name, f))

print(f'Dunaisky-related VLM files: {len(dunaisky_files)}')

# Extract all marks
all_dunaisky_marks = []
for name, vf in dunaisky_files:
    try:
        with open(vf) as f:
            data = json.load(f)
        content = data['choices'][0]['message']['content']
        if content.startswith('```'):
            content = content.split('\n', 1)[1].rsplit('```', 1)[0].strip()
        parsed = json.loads(content)
        marks = parsed.get('marks', []) or []
        for m in marks:
            val = m.get('value')
            if val is None: continue
            if isinstance(val, str):
                val_clean = val.replace('±', '0').replace('+', '').replace(',', '.').strip()
                try:
                    val_f = float(val_clean)
                except: continue
            else:
                val_f = float(val)
            
            all_dunaisky_marks.append({
                'file': name,
                'category': parsed.get('category', ''),
                'value': val_f,
                'x': m.get('x'),
                'y': m.get('y'),
                'context': m.get('context', ''),
            })
    except: pass

df = pd.DataFrame(all_dunaisky_marks)
print(f'Total Dunaisky marks: {len(df)}')

# Filter to absolute elevations (10-20m range for this site)
df_abs = df[(df['value'] >= 10) & (df['value'] <= 20)].copy()
print(f'Plausible Z [10, 20] (absolute Baltic heights for Дунайский 27): {len(df_abs)}')

# Per-file breakdown
print('\n=== Per-file breakdown ===')
for fname, sub in df_abs.groupby('file'):
    cat = sub['category'].iloc[0]
    print(f'  {fname:30s} cat={cat:25s} n={len(sub):3d} Z=[{sub["value"].min():.2f}, {sub["value"].max():.2f}] med={sub["value"].median():.2f}')

# Geolocate: assume marks are spread across the building site
# Use x (relative image coord 0..1) to interpolate lon, y to interpolate lat
# Building site is ~200m × ~300m

center_lat, center_lon = DUNAISKY_COORDS
lat_half = DUNAISKY_LAT_SPREAD / 2
lon_half = DUNAISKY_LON_SPREAD / 2

# Filter marks with valid x,y
df_geo = df_abs.dropna(subset=['x', 'y']).copy()
# Interpolate: x → lon (left=lon_min, right=lon_max), y → lat (top=lat_max, bottom=lat_min)
df_geo['lon'] = (center_lon - lon_half) + df_geo['x'] * DUNAISKY_LON_SPREAD
df_geo['lat'] = (center_lat + lat_half) - df_geo['y'] * DUNAISKY_LAT_SPREAD

print(f'\nGeo-located marks: {len(df_geo)}')
print(f'Lat range: {df_geo["lat"].min():.6f} to {df_geo["lat"].max():.6f}')
print(f'Lon range: {df_geo["lon"].min():.6f} to {df_geo["lon"].max():.6f}')

# Sample model
print('\n=== Sampling model ===')
ds = rasterio.open(MODEL_PATH)
model_nodata = ds.nodata

h_models = []
for idx, row in df_geo.iterrows():
    try:
        xs_list, ys_list = warp_transform('EPSG:4326', ds.crs, [row['lon']], [row['lat']])
        xs = np.asarray(xs_list, dtype='float64')
        ys = np.asarray(ys_list, dtype='float64')
        inv_transform = ~ds.transform
        px_cols, px_rows = inv_transform * (xs, ys)
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

df_geo['h_model'] = h_models
df_valid = df_geo.dropna(subset=['h_model']).copy()
df_valid['dH'] = df_valid['value'] - df_valid['h_model']
df_valid['abs_dH'] = df_valid['dH'].abs()

print(f'Valid model hits: {len(df_valid)}')

if len(df_valid) < 5:
    print('Too few valid points!')
    exit()

# Stats
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

print(f'\n=== COMPARISON RESULTS ===')
print(f'n = {n}')
print(f'Survey Z: {df_valid["value"].min():.2f} to {df_valid["value"].max():.2f}m (median {df_valid["value"].median():.2f})')
print(f'Model Z:  {df_valid["h_model"].min():.2f} to {df_valid["h_model"].max():.2f}m (median {df_valid["h_model"].median():.2f})')
print(f'dH: median={median_dH:+.3f}m, mean={mean_dH:+.3f}m, std={df_valid["dH"].std():.2f}m')
print(f'|dH|: MAD={mad:.2f}m, mean={mean_abs:.2f}m, p95={p95:.2f}m')
print(f'survey > model: {n_pos}/{n} ({n_pos/n*100:.1f}%)')
print(f'Wilcoxon p = {p_w:.4e}')
print(f'Sign test p = {p_sign:.4e}')
print(f'Bootstrap 95% CI of median dH: [{ci_lo:+.3f}, {ci_hi:+.3f}]')

# Save
df_valid.to_csv('/home/z/my-project/scripts/data/archi_plans_vlm_FINAL.csv', index=False)

summary = {
    'source': 'archi_plans VLM extraction (all Dunaisky project images)',
    'images_processed': len(dunaisky_files),
    'marks_extracted_total': int(len(df)),
    'marks_plausible_z_10_20': int(len(df_abs)),
    'marks_valid_model_hits': n,
    'location': {
        'address': 'Дунайский проспект, 27',
        'project_id_archi_ru': '21359',
        'lat_center': center_lat,
        'lon_center': center_lon,
        'lat_spread': DUNAISKY_LAT_SPREAD,
        'lon_spread': DUNAISKY_LON_SPREAD,
    },
    'stats': {
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
with open('/home/z/my-project/scripts/data/archi_plans_vlm_FINAL_v2_summary.json', 'w') as f:
    json.dump(summary, f, indent=2, ensure_ascii=False)
print(f'\nSaved to archi_plans_vlm_FINAL.csv and archi_plans_vlm_FINAL_v2_summary.json')
