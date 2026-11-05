#!/usr/bin/env python3
"""6.76/A3 — классификация водных фич по уликам суши (по diag_features).

Для каждой фичи: dry_frac = dry_px / water_px (улики conf=data, z>=1.5).
Классы:
  phantom  dry_frac >= 0.5           — полигон в основном на суше
  ribbon   0.05 <= dry_frac < 0.5    — ленты вдоль стенок/мосты (норма для
                                       реки, чинится вычитанием)
  clean    dry_frac < 0.05
Также топ-фантомы по сухой площади. Печать + CSV-классификация.
"""
import csv
import json
from pathlib import Path

REPO = Path('/tmp/my-project/gh-clone')
OUTD = REPO / 'results' / 'water_a3_6.76'
F = list(csv.DictReader(open(OUTD / 'diag_features.csv')))
COMP = list(csv.DictReader(open(OUTD / 'diag_components.csv')))

rows = []
for r in F:
    wp, dp = int(r['water_px']), int(r['dry_px'])
    frac = dp / wp if wp else 0.0
    cls = ('phantom' if frac >= 0.5 else 'ribbon' if frac >= 0.05
           else 'clean' if wp > 0 else 'empty')
    rows.append({'feat': int(r['feat']), 'water_px': wp, 'dry_px': dp,
                 'dry_frac': round(frac, 3), 'class': cls,
                 'water_km2': r['water_km2'], 'dry_km2': r['dry_km2']})

from collections import Counter
cc = Counter(r['class'] for r in rows)
print('классы фич:', dict(cc))

ph = sorted((r for r in rows if r['class'] == 'phantom'),
            key=lambda r: -r['dry_px'])
print(f'\nфантомы (dry_frac>=0.5): {len(ph)} фич, суммарно сухой '
      f'{sum(r["dry_px"] for r in ph)*3.9/1e6:.2f} км2')
for r in ph[:15]:
    print(f"  feat{r['feat']:5d} water {float(r['water_km2']):7.3f} км2 "
          f"dry {float(r['dry_km2']):7.3f} км2 frac {r['dry_frac']:.2f}")

rib = sorted((r for r in rows if r['class'] == 'ribbon'),
             key=lambda r: -r['dry_px'])
print(f'\nленты (0.05<=frac<0.5): {len(rib)} фич, сухой '
      f'{sum(r["dry_px"] for r in rib)*3.9/1e6:.2f} км2')
for r in rib[:10]:
    print(f"  feat{r['feat']:5d} water {float(r['water_km2']):7.2f} км2 "
          f"dry {float(r['dry_km2']):7.3f} км2 frac {r['dry_frac']:.2f}")

clean = [r for r in rows if r['class'] == 'clean']
print(f'\nчистые: {len(clean)} фич, сухой остаток '
      f'{sum(r["dry_px"] for r in clean)*3.9/1e6:.2f} км2')

with open(OUTD / 'feature_classes.csv', 'w', newline='') as fh:
    w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
    w.writeheader()
    w.writerows(rows)

# компоненты-фантомы: z_med > 5 м (точно не вода и не лента у уреза)
bigz = sorted((c for c in COMP if float(c['z_med_m']) > 5.0),
              key=lambda c: -float(c['area_m2']))
print(f'\nкомпонентов с z_med>5 м: {len(bigz)}, площадь '
      f'{sum(float(c["area_m2"]) for c in bigz)/1e6:.2f} км2')
