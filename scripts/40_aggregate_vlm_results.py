"""
Aggregate all VLM results from archi_plans directory.
- Extract marks with elevation values
- Filter to plausible Z range (0-50m for SPb)
- Geolocate using bbox.xml or by pattern matching (street names, addresses)
- Compare with model
"""
import os
import json
import re
import glob
import pandas as pd
import numpy as np
from collections import Counter, defaultdict

DATA_DIR = '/tmp/my-project/scripts/data/archi_plans'

# Find all VLM JSON files
vlm_files = sorted(glob.glob(os.path.join(DATA_DIR, '*.vlm.json')))
print(f'Total VLM JSON files: {len(vlm_files)}')

# Aggregate all marks
all_marks = []
categories = Counter()
marks_by_file = defaultdict(list)
parse_errors = 0

for vf in vlm_files:
    name = os.path.basename(vf).replace('.vlm.json', '')
    try:
        with open(vf) as f:
            data = json.load(f)
        content = data['choices'][0]['message']['content']
        # Strip markdown code fence
        if content.startswith('```'):
            content = content.split('\n', 1)[1].rsplit('```', 1)[0].strip()
        parsed = json.loads(content)
        cat = parsed.get('category', 'unknown')
        categories[cat] += 1
        marks = parsed.get('marks', []) or []
        for m in marks:
            # Extract value
            val = m.get('value')
            if val is None:
                continue
            # Coerce to float
            if isinstance(val, str):
                # Handle ±0.000, ±0.500 etc.
                val_clean = val.replace('±', '0').replace('+', '').replace(',', '.').strip()
                try:
                    val_f = float(val_clean)
                except:
                    continue
            else:
                val_f = float(val)
            
            all_marks.append({
                'file': name,
                'category': cat,
                'value': val_f,
                'value_raw': str(val),
                'x': m.get('x'),
                'y': m.get('y'),
                'context': m.get('context', '')[:200],
            })
            marks_by_file[name].append(val_f)
    except Exception as e:
        parse_errors += 1

print(f'\nParse errors: {parse_errors}')
print(f'\n=== Categories ===')
for c, n in categories.most_common():
    n_with_marks = sum(1 for f, marks in marks_by_file.items() 
                      if any(m['category'] == c for m in [{'category': c, 'value': v} for v in marks]))
    print(f'  {c:25s}: {n:4d} files')

print(f'\n=== Total marks found: {len(all_marks)} ===')
df = pd.DataFrame(all_marks)
if len(df) > 0:
    print(f'Value range: {df["value"].min():.2f} to {df["value"].max():.2f}')
    print(f'Value percentiles 5/25/50/75/95: {df["value"].quantile([0.05, 0.25, 0.5, 0.75, 0.95]).values}')
    
    # Filter: plausible SPb elevations
    # SPb elevations: -10m (underground) to 175m (Dudergof)
    # But most ground elevations: 0-30m
    # Building floors: ±0.000 to 30m relative
    plausible = df[(df['value'] >= -10) & (df['value'] <= 200)]
    print(f'\nPlausible Z [-10, 200]: {len(plausible)} / {len(df)}')
    
    # Subset by Z range
    print(f'\nBy Z range:')
    print(f'  Z < 0 (underground/relative negative): {len(df[df["value"] < 0])}')
    print(f'  Z = 0 (zero level): {len(df[df["value"] == 0])}')
    print(f'  Z 0-30 (typical SPb ground): {len(df[(df["value"] >= 0) & (df["value"] <= 30)])}')
    print(f'  Z 30-100 (hills/structures): {len(df[(df["value"] > 30) & (df["value"] <= 100)])}')
    print(f'  Z 100-200 (Dudergof heights): {len(df[(df["value"] > 100) & (df["value"] <= 200)])}')
    print(f'  Z > 200 (likely codes/error): {len(df[df["value"] > 200])}')
    
    # Files with most marks
    print(f'\n=== Files with most marks ===')
    by_file = df.groupby('file').size().sort_values(ascending=False)
    for fname, n in by_file.head(15).items():
        sub = df[df['file'] == fname]
        cat = sub['category'].iloc[0]
        z_med = sub['value'].median()
        z_min = sub['value'].min()
        z_max = sub['value'].max()
        print(f'  {fname[:55]:<55} cat={cat:25s} n={n:3d} Z=[{z_min:.2f}, {z_max:.2f}] med={z_med:.2f}')

# Save aggregated
df.to_csv('/home/z/my-project/scripts/data/archi_plans_vlm_all_marks.csv', index=False)
print(f'\nSaved {len(df)} marks to archi_plans_vlm_all_marks.csv')

# Summary stats
summary = {
    'total_vlm_files': len(vlm_files),
    'parse_errors': parse_errors,
    'categories': dict(categories),
    'total_marks': len(df),
    'plausible_marks_z_10_200': int(len(plausible)) if len(df) > 0 else 0,
    'value_range': [float(df['value'].min()), float(df['value'].max())] if len(df) > 0 else None,
    'value_median': float(df['value'].median()) if len(df) > 0 else None,
}
with open('/home/z/my-project/scripts/data/archi_plans_vlm_summary.json', 'w') as f:
    json.dump(summary, f, indent=2, ensure_ascii=False)
print('Saved summary to archi_plans_vlm_summary.json')
