#!/usr/bin/env python3
"""Per-point EGM2008 geoid undulation for SPb (replaces the constant 15.9 / 15.673 / 16.04 / 16.472).

Grid: https://cdn.proj.org/us_nga_egm08_25.tif  (2.5', 80 MB, NGA EGM2008, public)
      Reachable from the agent environment (verified 2026-11-02).

Heights:
    h_ell      — ellipsoidal (WGS84 / ITRF), e.g. ICESat-2 h_te / h_mean, ArcticDEM
    N(lon,lat) — EGM2008 undulation from the grid (15.76..16.00 m over SPb)
    H_egm      = h_ell - N
    H_bs77     = H_egm - D,   D = BS-77 datum offset vs the EGM2008 geoid (ONE constant
                 for the whole project, fitted on >= 20 trigonometric/levelling points,
                 never on model output or on marks that sit in the anchors)

Usage:
    from geoid_egm2008 import Geoid
    g = Geoid('/path/us_nga_egm08_25.tif')
    N = g.n(lon_array, lat_array)               # metres, bilinear
    D, se, n = g.fit_datum_offset(lon_tp, lat_tp, h_ell_tp, H_bs77_tp)
    H = g.to_bs77(lon, lat, h_ell, D)

Self-test:  python3 geoid_egm2008.py /path/us_nga_egm08_25.tif
"""
import sys
import numpy as np
import rasterio


class Geoid:
    def __init__(self, grid_path):
        with rasterio.open(grid_path) as s:
            self.a = s.read(1).astype(np.float64)
            self.tr = s.transform

    def n(self, lon, lat):
        lon = np.asarray(lon, float); lat = np.asarray(lat, float)
        c = (lon - self.tr.c) / self.tr.a - 0.5
        r = (lat - self.tr.f) / self.tr.e - 0.5
        c0 = np.floor(c).astype(int); r0 = np.floor(r).astype(int)
        fc, fr = c - c0, r - r0
        a = self.a
        return (a[r0, c0] * (1 - fc) * (1 - fr) + a[r0, c0 + 1] * fc * (1 - fr)
                + a[r0 + 1, c0] * (1 - fc) * fr + a[r0 + 1, c0 + 1] * fc * fr)

    def fit_datum_offset(self, lon, lat, h_ell, H_bs77):
        """D = median(h_ell - N - H_bs77) on independent levelling points; returns (D, robust SE, n)."""
        d = np.asarray(h_ell, float) - self.n(lon, lat) - np.asarray(H_bs77, float)
        d = d[np.isfinite(d)]
        mad = np.median(np.abs(d - np.median(d))) * 1.4826
        return float(np.median(d)), float(1.2533 * mad / np.sqrt(max(d.size, 1))), int(d.size)

    def to_bs77(self, lon, lat, h_ell, D):
        return np.asarray(h_ell, float) - self.n(lon, lat) - D


if __name__ == '__main__':
    g = Geoid(sys.argv[1])
    pts = {'Kronstadt': (29.776, 59.989), 'V.O.': (30.27, 59.94), 'Centre': (30.32, 59.935),
           'Lakhta': (30.20, 60.00), 'Pulkovo': (30.33, 59.77), 'Sestroretsk': (29.96, 60.09),
           'Kolpino': (30.60, 59.75)}
    lo = np.array([p[0] for p in pts.values()]); la = np.array([p[1] for p in pts.values()])
    for k, v in zip(pts, g.n(lo, la)):
        print(f'N {k:12s} {v:7.3f} m')
    v = g.n(lo, la)
    print(f'spread over the city: {v.max() - v.min():.3f} m')
    assert 15.7 < v.min() < v.max() < 16.05, 'unexpected undulation for SPb'
    print('OK')
