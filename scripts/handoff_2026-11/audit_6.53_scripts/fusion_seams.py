"""Seams in spb_terra_fusion_6.00_A2: data(conf 10-13) vs fill(conf 1-4) boundaries, ~8 m lag. Streamed histograms.
usage: fusion_seams.py <fusion.tif> <conf.tif>"""
import sys, numpy as np, rasterio
from rasterio.windows import Window
F, C = sys.argv[1], sys.argv[2]
H_WATER = 0.7; LX, LY = 8, 4
EDGES = np.arange(-30, 30.0001, 0.01)
hist = {}
def add(k, v):
    if v.size:
        h, _ = np.histogram(np.clip(v, -29.999, 29.999), EDGES)
        hist[k] = hist.get(k, 0) + h
with rasterio.open(F) as fs, rasterio.open(C) as cs:
    W, H = fs.width, fs.height; S = 512
    for r0 in range(0, H, S):
        h = min(S + LY, H - r0); win = Window(0, r0, W, h)
        z = fs.read(1, window=win).astype(np.float32); c = cs.read(1, window=win)
        z[z == -32768] = np.nan; z *= 0.01
        land = np.isfinite(z) & (z > H_WATER)
        data = (c >= 10) & land; fill = (c >= 1) & (c <= 4) & land
        for axis in (1, 0):
            if axis == 1: sl_a, sl_b = (slice(None), slice(None, -LX)), (slice(None), slice(LX, None))
            else:         sl_a, sl_b = (slice(None, -LY), slice(None)), (slice(LY, None), slice(None))
            d = z[sl_b] - z[sl_a]
            da, db, fa, fb, cb, ca = data[sl_a], data[sl_b], fill[sl_a], fill[sl_b], c[sl_b], c[sl_a]
            add('DD', d[da & db]); add('FF', d[fa & fb])
            df = da & fb; fd = fa & db
            add('DF', np.concatenate([d[df], -d[fd]]))          # signed fill - data
            for k in (1, 2, 3, 4):
                add(f'DF_A{k}', np.concatenate([d[df & (cb == k)], -d[fd & (ca == k)]]))
mid = (EDGES[:-1] + EDGES[1:]) / 2
def q(hh, p, absval=True):
    x = np.abs(mid) if absval else mid
    o = np.argsort(x); cs = np.cumsum(hh[o]); return x[o][np.searchsorted(cs, p * cs[-1])]
def st(k):
    hh = hist[k]; n = hh.sum(); a = np.abs(mid)
    return (f"n={n:>11,d}  median|Δ|={q(hh,.5):5.2f}  p90={q(hh,.9):5.2f}  p99={q(hh,.99):6.2f}  "
            f">0.5m={100*hh[a>0.5].sum()/n:5.1f}%  >1m={100*hh[a>1].sum()/n:5.1f}%  >2m={100*hh[a>2].sum()/n:5.1f}%")
print("=== |Δh| over ~8 m, land only (h>0.7 m) ===")
for k in ('DD', 'FF', 'DF', 'DF_A1', 'DF_A2', 'DF_A3', 'DF_A4'):
    if k in hist: print(f"{k:6s} {st(k)}")
hh = hist['DF']; n = hh.sum()
print(f"\nsigned (fill − data) across boundary: median {q(hh,.5,False):+.2f}   "
      f"fill lower by >1 m: {100*hh[mid<-1].sum()/n:.1f}%   fill higher by >1 m: {100*hh[mid>1].sum()/n:.1f}%")
np.savez('/tmp/claude-0/-home-claude/ec58354c-610f-5d5d-a999-7bf1ca225fda/scratchpad/seam/fusion_hist.npz', edges=EDGES, **{k: v for k, v in hist.items()})
