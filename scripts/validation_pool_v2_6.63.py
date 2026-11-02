#!/usr/bin/env python3
"""6.63: validation pool v2 — ground marks only, weighted, honest tiers.

Rules from the audit (branch_audit_remediations §2.2/§2.3/§4, repo_base review §4):
  - ground marks and ICESat NEVER mix in one MAD;
  - DWG ICP (false ICP minimum) and Отметки.txt (circular) are OUT;
  - Atayans returns with double-read status as weight (georef verified 02.11);
  - flag_vs_neighbours halves the weight (manual check pending);
  - report dH vs fusion A2 per source x tier, weighted medians.
"""
import csv, sys, numpy as np, rasterio, json, os
sys.path.insert(0, '/tmp/my-project/gh-clone/scripts/handoff_2026-11')
from geoid_egm2008 import Geoid

GH = '/tmp/my-project/gh-clone'
FUSION = '/tmp/my-project/download/mosaic_6.00/spb_terra_fusion_6.00_A2.tif'
GEO_TIF = '/tmp/my-project/data/us_nga_egm08_25.tif'
OUTD = f'{GH}/results/validation_pool_v2'
os.makedirs(OUTD, exist_ok=True)

F = rasterio.open(FUSION)
def fus(lo, la):
    v = np.array([x[0] for x in F.sample(list(zip(lo, la)))], float)
    v[v == -32768] = np.nan
    return v * 0.01
g = Geoid(GEO_TIF)

rows = []

# 1. passport (wall marks, BS-77) — weight by detail class, holdout only for headline
for r in csv.DictReader(open(f'{GH}/scripts/data/spb_passport_validation_v11.csv')):
    det, role = r['detail'], r['role']
    w = {'21': 1.0, '23': 0.5, '24': 0.7}.get(det, 0.3)
    rows.append(dict(source='passport', id=r['key'], lon=r['lon'], lat=r['lat'],
                     h=float(r['hp']), weight=w, tier=f'detail{det}_{role}',
                     t_obs='нивелирование марки, дата не установлена', note=''))

# 2. Kupchino archi 514913 (georeferenced, independent, open area)
for r in csv.DictReader(open(f'{GH}/scripts/data/ALL_independent_points.csv')):
    if r['source'] != 'archi.ru 514913': continue
    rows.append(dict(source='archi_514913', id=r['lat'][:8] + '_' + r['lon'][:8],
                     lon=r['lon'], lat=r['lat'], h=float(r['z_survey']),
                     weight=1.0, tier='vlm_read', t_obs='подоснова листа (2000-е?)',
                     note='геопривязка по адресу'))

# 3. Atayans pool v2 (double read) + weight by status
W = {'confirmed': 1.0, 'near': 0.9, 'conflict': 0.3, 'new_single_read': 0.3, 'agent_only': 0.3}
for r in csv.DictReader(open(f'{GH}/results/atayanc_pool_v2/atayanc_marks_pool_v2.csv')):
    h = r.get('h_best') or ''
    if not h: continue
    w = W.get(r['status'], 0.3)
    if r.get('flag_vs_neighbours') == '1': w *= 0.5
    rows.append(dict(source='atayans_v2', id=r['id'], lon=r['lon'], lat=r['lat'],
                     h=float(h), weight=round(w, 2), tier=r['use'],
                     t_obs='<=1990 (топооснова листа)', note=r['status']))

# dH vs fusion + N
for r in rows:
    lo, la = np.array([float(r['lon'])]), np.array([float(r['lat'])])
    f = fus(lo, la)[0]
    r['fusion_m'] = round(f, 3) if np.isfinite(f) else ''
    r['N_egm2008'] = round(float(g.n(lo, la)[0]), 3)
    r['dH_m'] = round(f - float(r['h']), 3) if np.isfinite(f) else ''

with open(f'{OUTD}/validation_pool_v2.csv', 'w', newline='') as fh:
    w = csv.DictWriter(fh, fieldnames=['source', 'id', 'lon', 'lat', 'h', 'weight',
                                       'tier', 't_obs', 'note', 'fusion_m', 'N_egm2008', 'dH_m'])
    w.writeheader(); w.writerows(rows)

# headline table: weighted median dH per source x tier (fusion valid only)
def wmed(v, w):
    v, w = np.asarray(v), np.asarray(w)
    ok = np.isfinite(v)
    v, w = v[ok], w[ok]
    if v.size == 0: return (float('nan'), 0)
    idx = np.argsort(v); v, w = v[idx], w[idx]
    cw = np.cumsum(w)                    # weighted median: first cumsum >= 0.5*total
    j = min(int(np.searchsorted(cw, 0.5 * cw[-1])), v.size - 1)
    return (float(v[j]), int(v.size))

print(f"{'source':16s} {'tier':22s} {'n':>4s} {'sum_w':>7s} {'w-median dH':>11s}")
agg = {}
for r in rows:
    if r['dH_m'] == '': continue
    agg.setdefault((r['source'], r['tier']), ([], []))
    agg[(r['source'], r['tier'])][0].append(float(r['dH_m']))
    agg[(r['source'], r['tier'])][1].append(float(r['weight']))
for (s, t), (v, w) in sorted(agg.items()):
    m, n = wmed(v, w)
    print(f'{s:16s} {t:22s} {n:4d} {sum(w):7.1f} {m:+11.2f}')
json.dump({f'{s}|{t}': {'n': len(v), 'sum_w': round(sum(w), 1),
                        'w_median_dH': round(wmed(v, w)[0], 3)}
           for (s, t), (v, w) in agg.items()},
          open(f'{OUTD}/pool_v2_summary.json', 'w'), indent=1, ensure_ascii=False)
print(f'\ntotal ground marks: {len(rows)} -> {OUTD}/validation_pool_v2.csv')
