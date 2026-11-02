#!/usr/bin/env python3
"""QA: height steps across the 4096-px block boundaries vs control lines mid-block.
Reads map tiles (R=round((h+2)*10), B=detail class).
Usage: python3 qa_block_seams.py <map/tiles> <zoom, e.g. 14>
Grid origin is rebuilt exactly as spb_full_run_v2.py does (SPB_BBOX, 2 m, BLOCK=4096).
Gate proposal: share of |dH|>1 m on seam lines must not exceed 2x the control lines."""
import sys, math, numpy as np
from pathlib import Path
from PIL import Image
from pyproj import Transformer
TILES = Path(sys.argv[1]); Z = int(sys.argv[2])
to3413 = Transformer.from_crs(4326, 3413, always_xy=True)
to4326 = Transformer.from_crs(3413, 4326, always_xy=True)

# --- product grid, exactly as spb_full_run_v2.py builds it -----------------
from pyproj import Transformer as T
bb = (29.70, 59.75, 30.55, 60.05)
# transform_bounds equivalent: densify the bbox edge
lons = np.r_[np.linspace(bb[0],bb[2],200), np.full(200,bb[2]), np.linspace(bb[2],bb[0],200), np.full(200,bb[0])]
lats = np.r_[np.full(200,bb[1]), np.linspace(bb[1],bb[3],200), np.full(200,bb[3]), np.linspace(bb[3],bb[1],200)]
xs, ys = to3413.transform(lons, lats)
left, bottom, right, top = xs.min(), ys.min(), xs.max(), ys.max()
left = math.floor(left/2)*2; bottom = math.floor(bottom/2)*2
right = math.ceil(right/2)*2; top = math.ceil(top/2)*2
W = int((right-left)/2); H = int((top-bottom)/2)
print(f"grid {W}x{H} (QA says 23146x28369)  left={left} top={top}")
B = 4096*2  # block size, metres

# --- tile sampler -----------------------------------------------------------
cache = {}
def tile(z, x, y):
    k = (z, x, y)
    if k not in cache:
        p = TILES/str(z)/str(x)/f"{y}.png"
        cache[k] = np.asarray(Image.open(p).convert('RGBA')) if p.exists() else None
    return cache[k]
def sample(lon, lat, z=Z):
    n = 2**z
    fx = (lon+180)/360*n
    fy = (1-math.log(math.tan(math.radians(lat))+1/math.cos(math.radians(lat)))/math.pi)/2*n
    tx, ty = int(fx), int(fy)
    a = tile(z, tx, ty)
    if a is None: return np.nan, -1
    px, py = int((fx-tx)*256), int((fy-ty)*256)
    r, g, b, al = a[py, px]
    if al == 0 or r >= 254: return np.nan, int(b)
    return r/10.0 - 2.0, int(b)

def profile_jump(fixed, along, vertical, delta):
    """step across a straight 3413 line. vertical: line x=fixed; else y=fixed."""
    out = []
    for s in along:
        if vertical: pa, pb = (fixed-delta, s), (fixed+delta, s)
        else:        pa, pb = (s, fixed+delta), (s, fixed-delta)   # a = north side
        la = to4326.transform(*pa); lb = to4326.transform(*pb)
        ha, ca = sample(*la); hb, cb = sample(*lb)
        if ca in (21, 23) and cb in (21, 23) and np.isfinite(ha) and np.isfinite(hb):
            out.append((s, hb-ha, ca, cb))
    return out

def run(lines, label, delta):
    allj = []; per = []
    for (fixed, vertical, lo, hi, name) in lines:
        along = np.arange(lo, hi, 20.0)
        j = profile_jump(fixed, along, vertical, delta)
        if len(j) < 30: continue
        d = np.array([t[1] for t in j])
        allj.append(d)
        # systematic offset along the segment, chunked per block (8.2 km)
        per.append((name, len(d), np.median(d), np.median(np.abs(d))))
    a = np.concatenate(allj) if allj else np.array([])
    return a, per

for delta in (6.0, 15.0):
    seams, ctrl = [], []
    for k in range(1, math.ceil(W/4096)):
        x = left + k*B
        seams.append((x, True, bottom, top, f"V{k}"))
        ctrl.append((x - B/2, True, bottom, top, f"Vc{k}"))
    for k in range(1, math.ceil(H/4096)):
        y = top - k*B
        seams.append((y, False, left, right, f"H{k}"))
        ctrl.append((y + B/2, False, left, right, f"Hc{k}"))
    s, sper = run(seams, 'seam', delta); c, cper = run(ctrl, 'ctrl', delta)
    print(f"\n===== delta = ±{delta:.0f} m, zoom {Z}  (land classes 21/23 both sides) =====")
    for lbl, a in (('SEAMS  ', s), ('CONTROL', c)):
        if len(a)==0: print(lbl,'no data'); continue
        print(f"{lbl} n={len(a):6d}  median|Δ|={np.median(np.abs(a)):.2f}  p90|Δ|={np.percentile(np.abs(a),90):.2f}  "
              f"|Δ|>0.5m: {100*(np.abs(a)>0.5).mean():5.1f}%  |Δ|>1m: {100*(np.abs(a)>1).mean():5.1f}%")
    if len(s) and len(c):
        from scipy import stats
        u = stats.mannwhitneyu(np.abs(s), np.abs(c), alternative='greater')
        print(f"Mann-Whitney |Δ|seam > |Δ|ctrl: p={u.pvalue:.3g}")
    if delta == 6.0:
        print("\nper seam line: n, signed median Δ (systematic step), median|Δ|")
        for p in sper: print(f"   {p[0]:>4}: n={p[1]:5d}  median Δ={p[2]:+.2f}  median|Δ|={p[3]:.2f}")
        print("per control line:")
        for p in cper: print(f"   {p[0]:>4}: n={p[1]:5d}  median Δ={p[2]:+.2f}  median|Δ|={p[3]:.2f}")
