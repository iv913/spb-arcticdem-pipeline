"""B3, route 'tide_gauge_icesat': mean ICESat-2 water-surface height near Kronstadt minus EGM2008.

  mean(h_water - N_EGM2008) = D + MSL_BS77(period)

ICESat-2 (ATL03 -> ATL06-SR) and ITRF GNSS heights are both tide-free, so the same D as
GNSS-levelling comes out without a tide-system term. Run:
  python b3_kronstadt_icesat2.py download   # SlideRule, ~10 min, writes *.parquet
  python b3_kronstadt_icesat2.py stats      # writes *_passes.csv and datum_D.json
Needs: sliderule, geopandas, ../geoid_egm2008.py and us_nga_egm08_25.tif (cdn.proj.org).
"""
import sys, json, numpy as np, pandas as pd
REG = {'gulf_west_of_kotlin': (29.40, 29.62, 59.93, 60.06),   # open gulf, outside the dam
       'neva_bay_inside_dam': (29.92, 30.12, 59.92, 60.00)}   # for information: Neva slope
GEOID = 'us_nga_egm08_25.tif'
MSL_BS77 = (0.13, 0.04)   # Kronstadt relative MSL 2018-2026 in BS-77, m: 4.06 cm (1946-2007 mean) + 1.9-2.3 mm/yr
                          # to 2022 (Kronstadt sea-level paper, cyberleninka); sigma incl. interannual variability

def download():
    import sliderule.session as ss
    _o = ss.Session.__init__
    def _i(self, *a, **k):   # behind a proxy: let requests use env proxy + CA bundle
        k.setdefault('trust_env', True); _o(self, *a, **k)
    ss.Session.__init__ = _i
    from sliderule import sliderule, icesat2
    sliderule.init("slideruleearth.io", verbose=False)
    for name, (x0, x1, y0, y1) in REG.items():
        poly = [dict(lon=x0, lat=y0), dict(lon=x1, lat=y0), dict(lon=x1, lat=y1), dict(lon=x0, lat=y1), dict(lon=x0, lat=y0)]
        parms = {"poly": poly, "srt": icesat2.SRT_OCEAN, "cnf": icesat2.CNF_SURFACE_HIGH, "len": 40.0, "res": 20.0,
                 "ats": 3.0, "cnt": 10, "maxi": 5, "t0": "2018-10-14T00:00:00Z", "t1": "2026-10-31T00:00:00Z"}
        icesat2.atl06p(parms).to_parquet(f'{name}.parquet')

def passes(name, g):
    import geopandas as gpd
    mad = lambda x: 1.4826 * np.median(np.abs(x - np.median(x)))
    d = gpd.read_parquet(f'{name}.parquet'); d['t'] = d.index
    d['N'] = g.n(d.geometry.x.values, d.geometry.y.values); d['hN'] = d.h_mean - d.N
    q = d[(d.n_fit_photons >= 20) & (d.h_sigma < 0.05) & (d.rms_misfit < 0.2) &
          (d.w_surface_window_final <= 3.5) & (d.dh_fit_dx.abs() < 0.01) & (d.hN.abs() < 3)]
    p = q.groupby(['rgt', 'cycle']).agg(t=('t', 'median'), n=('hN', 'size'), hN=('hN', 'median'), sd=('hN', mad)).reset_index()
    p = p[(p.n >= 30) & (p.sd < 0.15)]          # one overflight = one sea-level sample
    p.to_csv(f'{name}_passes.csv', index=False)
    return p

def stats():
    sys.path.insert(0, '..'); from geoid_egm2008 import Geoid
    g = Geoid(GEOID)
    p = passes('gulf_west_of_kotlin', g); pin = passes('neva_bay_inside_dam', g)
    m, se = p.hN.mean(), p.hN.std() / np.sqrt(len(p))
    sig_sys = 0.04   # spatial spread west/east half of the box and per-RGT spread (EGM2008 error) + ICESat-2 water bias
    D = m - MSL_BS77[0]; sD = float(np.sqrt(se**2 + MSL_BS77[1]**2 + sig_sys**2))
    out = dict(method='tide_gauge_icesat', D_m=round(D, 3), sigma_D_m=round(sD, 3),
               mean_h_minus_N_m=round(m, 3), se_m=round(se, 3), n_passes=len(p), n_rgt=int(p.rgt.nunique()),
               period=[str(p.t.min().date()), str(p.t.max().date())], msl_bs77_assumed_m=MSL_BS77,
               neva_bay_inside_dam_mean_h_minus_N_m=round(pin.hN.mean(), 3), neva_bay_n_passes=len(pin),
               literature_D_m=[0.00, 0.06], combined_D_m=None)
    w = np.array([1 / 0.06**2, 1 / sD**2]); v = np.array([0.0, D])
    out['combined_D_m'] = [round(float((w * v).sum() / w.sum()), 3), round(float(w.sum() ** -0.5), 3)]
    json.dump(out, open('datum_D.json', 'w'), indent=1); print(json.dumps(out, indent=1))

if __name__ == '__main__':
    {'download': download, 'stats': stats}[sys.argv[1]]()
