#!/usr/bin/env python3
"""Effective resolution of the release surface, measured from the data: RMS of the ~10 m high-pass residual
per ~32x32 m block. A 30 m prior or 8 m fill resampled to 2 m has ~0 energy at 2-10 m scales.
usage: effres.py <fusion.tif> <conf.tif>"""
import sys, numpy as np, rasterio
from rasterio.windows import Window
from scipy.ndimage import uniform_filter
F, C = sys.argv[1:3]
BY, BX = 16, 32                      # ~32 m x 32 m (px ~2 m lat, ~1 m lon)
rows = []                            # per block: hp_rms, median h, conf_mode, lat
with rasterio.open(F) as fs, rasterio.open(C) as cs:
    W, H = fs.width, fs.height; tr = fs.transform; S = 512; M = 8
    for r0 in range(0, H - BY, S):
        a0 = max(r0 - M, 0); a1 = min(r0 + S + M, H)
        z = fs.read(1, window=Window(0, a0, W, a1 - a0)).astype(np.float32); c = cs.read(1, window=Window(0, a0, W, a1 - a0))
        fin = z != -32768; z = np.where(fin, z * 0.01, np.nan)
        zf = np.where(fin, z, 0); w = fin.astype(np.float32)
        m = uniform_filter(zf, (5, 9)) / np.maximum(uniform_filter(w, (5, 9)), 1e-6)
        hp = np.where(fin & (uniform_filter(w, (5, 9)) > 0.99), z - m, np.nan)
        o = r0 - a0; nrow = min(S, H - r0) // BY
        hp = hp[o:o + nrow * BY, :(W // BX) * BX]; zz = z[o:o + nrow * BY, :(W // BX) * BX]; cc = c[o:o + nrow * BY, :(W // BX) * BX]
        sh = (nrow, BY, W // BX, BX)
        hp4 = hp.reshape(sh); z4 = zz.reshape(sh); c4 = cc.reshape(sh)
        n = np.isfinite(hp4).sum(axis=(1, 3))
        rms = np.sqrt(np.nanmean(hp4 ** 2, axis=(1, 3)))
        med = np.nanmedian(z4.reshape(nrow, BY, -1, BX).transpose(0, 2, 1, 3).reshape(nrow, W // BX, -1), axis=2)
        cmax = c4.max(axis=(1, 3)); cmin = np.where(c4 == 0, 255, c4).min(axis=(1, 3))
        lat = tr.f + (r0 + (np.arange(nrow) + 0.5) * BY) * tr.e
        lon = tr.c + (np.arange(W // BX) + 0.5) * BX * tr.a
        ok = n >= 0.8 * BY * BX
        ii, jj = np.where(ok)
        rows.append(np.stack([rms[ii, jj], med[ii, jj], cmax[ii, jj], cmin[ii, jj], lat[ii], lon[jj]], 1))
R = np.concatenate(rows)
rms, med, cmax, cmin, lat, lon = R.T
np.save('/tmp/my-project/gh-clone/results/qa_baseline/effres_blocks.npy', R)
land = med > 0.7
band = land & (med <= 5.0)
DET = 0.03   # m: below this the block carries no 2-10 m relief (int16 cm quantization ~0.003 m)
def rep(tag, m):
    if m.sum() == 0: return
    km2 = m.sum() * 32 * 32 / 1e6
    print(f"{tag:42s} blocks {m.sum():8d} (~{km2:7.1f} km2)  with 2-m detail {100*(rms[m]>DET).mean():5.1f}%  "
          f"smooth (≈8-30 m effective) {100*(rms[m]<=DET).mean():5.1f}%   median hp-RMS {np.median(rms[m]):.3f} m")
print("=== whole land (h>0.7 m) ===")
rep('all land', land)
rep('  measured (conf 10-13)', land & (cmin >= 10))
rep('  fill A1-A2 (conf 1-4)', land & (cmax <= 4) & (cmax >= 1))
print("=== flood band 0.7 < h <= 5 m ===")
rep('flood band, all', band)
rep('  measured (conf 10-13)', band & (cmin >= 10))
rep('    of which tier gold (13)', band & (cmin == 13))
rep('    of which tier rest (11)', band & (cmin == 11) & (cmax == 11))
rep('  fill A1-A2 (conf 1-4)', band & (cmax <= 4) & (cmax >= 1))
for name, (x0, y0, x1, y1) in {'V.O.': (30.18, 59.925, 30.30, 59.955), 'centre (Admiralteysky/Central)': (30.29, 59.92, 30.38, 59.95),
                              'Petrogradsky': (30.27, 59.955, 30.33, 59.975), 'Kupchino': (30.34, 59.82, 30.42, 59.85),
                              'Lakhta/Primorsky shore': (30.13, 59.98, 30.25, 60.01)}.items():
    m = band & (lon >= x0) & (lon <= x1) & (lat >= y0) & (lat <= y1)
    rep(f'flood band — {name}', m)
