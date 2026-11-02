"""Published fusion 6.00 A2 vs ground marks that never entered its anchors. usage: indep.py <fusion> <conf> <repo> <repo53>"""
import sys, csv, numpy as np, rasterio
F, C, REPO, R53 = sys.argv[1:5]
fs, cs = rasterio.open(F), rasterio.open(C)
def samp(lon, lat):
    xy = list(zip(lon, lat))
    z = np.array([v[0] for v in fs.sample(xy)], float); c = np.array([v[0] for v in cs.sample(xy)], int)
    z[z == -32768] = np.nan; return z * 0.01, c
anch = [r for r in csv.DictReader(open(f'{REPO}/results/marks_val_5.95.csv'))]
alon = np.array([float(r['lon']) for r in anch]); alat = np.array([float(r['lat']) for r in anch])
def near_anchor(lon, lat, m=15.0):
    dx = (alon[None, :] - lon[:, None]) * 55700; dy = (alat[None, :] - lat[:, None]) * 111133
    return np.sqrt(dx**2 + dy**2).min(axis=1) <= m
sets = {}
R = list(csv.DictReader(open(f'{REPO}/results/atayanc_6.50/marks_catalog_6.50.csv')))
sets['atayanc 2013 (V.O., plan marks)'] = (np.array([float(r['lon']) for r in R]), np.array([float(r['lat']) for r in R]),
                                          np.array([float(r['h_bs77_m']) for r in R]))
Rh = [r for r in R if r['conf'] == 'H']
sets['  same, reading conf=H only'] = (np.array([float(r['lon']) for r in Rh]), np.array([float(r['lat']) for r in Rh]),
                                       np.array([float(r['h_bs77_m']) for r in Rh]))
A = list(csv.DictReader(open(f'{REPO}/scripts/data/archi_plans_vlm_FINAL.csv')))
A = [r for r in A if r['lon'] and r['lat']]
sets['archi 514913 (Kupchino)'] = (np.array([float(r['lon']) for r in A]), np.array([float(r['lat']) for r in A]),
                                   np.array([float(r['value']) for r in A]))
P = list(csv.DictReader(open(f'{R53}/results/spb_passport_validation_v4.csv')))
plon = np.array([float(r['lon']) for r in P]); plat = np.array([float(r['lat']) for r in P]); php = np.array([float(r['hp']) for r in P])
na = near_anchor(plon, plat)
print(f'passport marks within 15 m of a fusion anchor mark: {na.sum()}/{len(P)}')
sets['passport, NOT near anchors'] = (plon[~na], plat[~na], php[~na])
sets['passport, near anchors (leaky)'] = (plon[na], plat[na], php[na])
def mad(x): return float(np.median(np.abs(x - np.median(x)))) if x.size else float('nan')
print(f"\n{'set':34s} {'zone':6s} {'n':>4s} {'median':>7s} {'MAD':>6s} {'|d|>1m':>7s} {'|d|>1.5m':>8s}")
for name, (lo, la, h) in sets.items():
    z, c = samp(lo, la); d = z - h; ok = np.isfinite(d)
    for lbl, m in (('data', ok & (c >= 10)), ('fill', ok & (c >= 1) & (c <= 4)), ('all', ok)):
        if m.sum() == 0: continue
        x = d[m]
        print(f"{name:34s} {lbl:6s} {m.sum():4d} {np.median(x):+7.2f} {mad(x):6.2f} {100*(np.abs(x)>1).mean():6.0f}% {100*(np.abs(x)>1.5).mean():7.0f}%")

print("\n=== measured area by M-CLF tier (conf 10 excl / 11 rest / 12 ok / 13 gold) ===")
for name in ('atayanc 2013 (V.O., plan marks)', 'archi 514913 (Kupchino)', 'passport, near anchors (leaky)'):
    lo, la, h = sets[name]; z, c = samp(lo, la); d = z - h; ok = np.isfinite(d)
    for t, lbl in ((10, 'excl'), (11, 'rest'), (12, 'ok'), (13, 'gold')):
        m = ok & (c == t)
        if m.sum(): x = d[m]; print(f"  {name[:30]:30s} {lbl:5s} n={m.sum():4d} median {np.median(x):+.2f} MAD {mad(x):.2f} |d|>1m {100*(np.abs(x)>1).mean():3.0f}%")
