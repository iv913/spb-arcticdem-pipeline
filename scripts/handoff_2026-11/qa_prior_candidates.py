#!/usr/bin/env python3
"""Candidate 30 m prior vs current surface on independent ground marks (MAD with bias removed + paired tests).
usage: qa_prior_candidates.py <candidate.tif> <candidate_std.tif|same> <fusion.tif> <conf.tif> <passport.csv> <archi_514913.csv> <atayanc_pool_v2.csv>
Candidate window: read with /vsicurl/ (e.g. GEDTM30 https://s3.opengeohub.org/global/dtm/v1.2/...)."""
import sys, csv, numpy as np, rasterio
from scipy import stats
G = rasterio.open(sys.argv[1]); GS = rasterio.open(sys.argv[2])
F = rasterio.open(sys.argv[3]); C = rasterio.open(sys.argv[4])
def bil(src, lon, lat):
    a = src.read(1); tr = src.transform
    c = (lon - tr.c) / tr.a - 0.5; r = (lat - tr.f) / tr.e - 0.5; c0 = np.floor(c).astype(int); r0 = np.floor(r).astype(int); fc, fr = c - c0, r - r0
    q = lambda i, j: a[np.clip(r0 + i, 0, a.shape[0]-1), np.clip(c0 + j, 0, a.shape[1]-1)].astype(float)
    v = q(0,0)*(1-fc)*(1-fr) + q(0,1)*fc*(1-fr) + q(1,0)*(1-fc)*fr + q(1,1)*fc*fr
    v[v > 1e30] = np.nan; return v
def samp(src, lon, lat, nod, scale):
    v = np.array([x[0] for x in src.sample(list(zip(lon, lat)))], float); v[v == nod] = np.nan; return v * scale
sets = {}
P = list(csv.DictReader(open(sys.argv[5])))
sets['passport (202)'] = ([float(r['lon']) for r in P], [float(r['lat']) for r in P], [float(r['hp']) for r in P])
A = [r for r in csv.DictReader(open(sys.argv[6])) if r['lon']]
sets['archi 514913, Kupchino'] = ([float(r['lon']) for r in A], [float(r['lat']) for r in A], [float(r['value']) for r in A])
T = [r for r in csv.DictReader(open(sys.argv[7])) if r['use'] in ('ground_truth', 'ground_truth_0.1')]
sets['Atayans, double-read (centre)'] = ([float(r['lon']) for r in T], [float(r['lat']) for r in T], [float(r['h_best']) for r in T])
def mad(x): return float(np.median(np.abs(x - np.median(x))))
print(f"{'set':32s} {'model':18s} {'n':>4s} {'median':>7s} {'MAD':>5s} {'|e-med|>1m':>10s}")
for name, (lo, la, h) in sets.items():
    lo, la, h = map(np.array, (lo, la, h))
    g = bil(G, lo, la); f = samp(F, lo, la, -32768, 0.01); c = samp(C, lo, la, 0, 1); sd = bil(GS, lo, la)
    ok = np.isfinite(g) & np.isfinite(f)
    eg, ef = g[ok] - h[ok], f[ok] - h[ok]
    for lbl, e in (('GEDTM30 (EGM2008)', eg), ('fusion 6.00 (BS-77)', ef)):
        print(f"{name:32s} {lbl:18s} {e.size:4d} {np.median(e):+7.2f} {mad(e):5.2f} {100*(np.abs(e-np.median(e))>1).mean():9.0f}%")
    ag, af = np.abs(eg - np.median(eg)), np.abs(ef - np.median(ef))
    w = (ag < af).sum(); l = (ag > af).sum()
    print(f"{'':32s} paired, bias removed: GEDTM30 closer {w} / fusion closer {l}  sign p={stats.binomtest(w, w+l).pvalue:.3g}  "
          f"Wilcoxon p={stats.wilcoxon(ag, af).pvalue:.3g}   GEDTM std vs |error| Spearman {stats.spearmanr(sd[ok], ag).statistic:+.2f}")
