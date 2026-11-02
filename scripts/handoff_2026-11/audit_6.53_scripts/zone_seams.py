"""Step across the 84 zone bbox edges inside measured data (conf>=10) vs control lines shifted inward.
usage: zone_seams.py <fusion.tif> <conf.tif> <repo>"""
import sys, glob, json, numpy as np, rasterio
from rasterio.windows import Window
F, C, REPO = sys.argv[1:4]
zones = {}
for p in glob.glob(f'{REPO}/results/zone_*_5.83.json'):
    d = json.load(open(p)); zones[d.get('tag', p)] = (d['zone'], d.get('product', '?'))
print(f'zones: {len(zones)}')
fs, cs = rasterio.open(F), rasterio.open(C)
tr = fs.transform; W, H = fs.width, fs.height
def col(lon): return int(round((lon - tr.c) / tr.a))
def row(lat): return int(round((lat - tr.f) / tr.e))
LX, LY = 4, 2
def cross(kind, fixed, a, b):
    """kind 'v': line lon=fixed, lat a..b; 'h': lat=fixed, lon a..b. returns |Δ| array (land, data both sides)"""
    if kind == 'v':
        c0 = col(fixed); r0, r1 = sorted((row(b), row(a)))
        r0, r1 = max(r0, 0), min(r1, H); 
        if c0 - LX < 0 or c0 + LX >= W or r1 - r0 < 10: return np.array([])
        win = Window(c0 - LX, r0, 2 * LX + 1, r1 - r0)
        z = fs.read(1, window=win).astype(np.float32); c = cs.read(1, window=win)
        za, zb, ca, cb = z[:, 0], z[:, -1], c[:, 0], c[:, -1]
    else:
        r0 = row(fixed); c0, c1 = sorted((col(a), col(b)))
        c0, c1 = max(c0, 0), min(c1, W)
        if r0 - LY < 0 or r0 + LY >= H or c1 - c0 < 10: return np.array([])
        win = Window(c0, r0 - LY, c1 - c0, 2 * LY + 1)
        z = fs.read(1, window=win).astype(np.float32); c = cs.read(1, window=win)
        za, zb, ca, cb = z[0], z[-1], c[0], c[-1]
    ok = (za != -32768) & (zb != -32768) & (ca >= 10) & (cb >= 10)
    za, zb = za[ok] * 0.01, zb[ok] * 0.01
    land = (za > 0.7) & (zb > 0.7)
    return np.abs(zb[land] - za[land])
seam, ctrl, per = [], [], []
for tag, (b, prod) in zones.items():
    W0, S0, E0, N0 = b
    for kind, fixed, a, bb, inward in (('v', W0, S0, N0, +0.01), ('v', E0, S0, N0, -0.01),
                                       ('h', S0, W0, E0, +0.005), ('h', N0, W0, E0, -0.005)):
        s = cross(kind, fixed, a, bb); c = cross(kind, fixed + inward, a, bb)
        if s.size: seam.append(s); per.append((tag, prod, kind, fixed, s.size, float(np.median(s)), float((s > 1).mean())))
        if c.size: ctrl.append(c)
S_, C_ = np.concatenate(seam), np.concatenate(ctrl)
def st(a): return (f"n={a.size:>8,d}  median|Δ|={np.median(a):.2f}  p90={np.percentile(a,90):.2f}  p99={np.percentile(a,99):.2f}  "
                   f">0.5m={100*(a>0.5).mean():5.1f}%  >1m={100*(a>1).mean():5.1f}%  >2m={100*(a>2).mean():5.1f}%")
print("=== |Δh| across zone bbox edges (~4 m lag), measured data both sides, land ===")
print("ZONE EDGES ", st(S_)); print("CONTROL    ", st(C_))
per.sort(key=lambda t: -t[6])
print("\nworst edges (share >1 m, n>=200):")
k = 0
for t in per:
    if t[4] >= 200:
        print(f"  {t[0]:>6} ({t[1]}) {t[2]}@{t[3]:.3f}: n={t[4]:5d} median {t[5]:.2f}  >1m {100*t[6]:.0f}%"); k += 1
    if k == 10: break
