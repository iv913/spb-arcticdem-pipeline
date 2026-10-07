#!/usr/bin/env python3
"""5.65j: Precise plan-dorm extraction + per-korpus ICP + global validation."""
import json
import math

import numpy as np
from PIL import Image
from scipy import ndimage

RES = "/home/z/my-project/repo/results"
REF = (59.8723521, 30.4691565)
KX = 111320 * math.cos(math.radians(REF[0]))
KY = 111320.0
k2_w = np.array([41.2, -51.1])

# ---------- real korpus polygons (meters, world frame) ----------
nom = json.load(open(f"{RES}/nom_poly_korpusa_5.65.json"))
polys = {}
for key, h in nom.items():
    c = h.get("coords")
    if c and h["type"] == "Polygon":
        pts = np.array([((p[0] - REF[1]) * KX, (p[1] - REF[0]) * KY) for p in c[0]])
        polys[key] = pts[:-1] if np.allclose(pts[0], pts[-1]) else pts

# dense boundary sampling of each polygon
def sample_poly(pts, step=1.0):
    segs = np.roll(pts, -1, axis=0) - pts
    L = np.hypot(segs[:, 0], segs[:, 1])
    out = []
    for i, li in enumerate(L):
        if li < 1e-6:
            continue
        n = max(2, int(li / step))
        t = np.linspace(0, 1, n, endpoint=False)[:, None]
        out.append(pts[i] + t * segs[i])
    return np.concatenate(out, 0)

poly_samples = {k: sample_poly(v) for k, v in polys.items()}

# ---------- world raster + DT (for global check) ----------
meta = json.load(open(f"{RES}/osm_sosnovka_z18_5.65.png.json"))
mpp = meta["m_per_px"]
mask = np.asarray(Image.open(f"{RES}/osm_bld_mask_z18_5.65.png")) > 127
wy0 = (meta["lat_top"] - REF[0]) * KY
wx0 = (meta["lon_left"] - REF[1]) * KX
ys, xs = np.where(mask)
wx = wx0 + xs * mpp
wy = wy0 - ys * mpp
g = 1.0
GX0, GY1 = -260, 200
GW, GH = 521, 461
grid = np.zeros((GH, GW), dtype=bool)
gix = np.clip(((wx - GX0) / g).astype(int), 0, GW - 1)
giy = np.clip(((GY1 - wy) / g).astype(int), 0, GH - 1)
grid[giy, gix] = True
grid = ndimage.binary_closing(grid, np.ones((3, 3)))
dt_w = ndimage.distance_transform_edt(~grid) * g

# ---------- plan: dorm wall points, text-filtered ----------
pb = np.load(f"{RES}/reg65_plan_walls.npy")
WIN = (470, 980, 1340, 1535)
dorm = pb[(pb[:, 0] > WIN[0]) & (pb[:, 0] < WIN[1]) &
          (pb[:, 1] > WIN[2]) & (pb[:, 1] < WIN[3])]
print("dorm wall pts:", len(dorm))
# plan-frame DT of ALL walls (for world->plan term)
pm = np.zeros((1666, 2500), dtype=bool)
pm[pb[:, 1].astype(int), pb[:, 0].astype(int)] = True
dt_plan = ndimage.distance_transform_edt(~pm)
rng = np.random.default_rng(11)
dorm_s = dorm[rng.choice(len(dorm), min(800, len(dorm)), replace=False)]

# ---------- per-korpus ICP ----------
def fit_one(kpts):
    """ICP plan dorm pts -> korpus polygon. Init from principal axes, both axis signs."""
    # plan pts in math frame (y up)
    P = np.stack([dorm_s[:, 0], -dorm_s[:, 1]], 1)
    Pc = P - P.mean(0)
    Qc = kpts - kpts.mean(0)
    # principal axes
    _, _, VtP = np.linalg.svd(Pc, full_matrices=False)
    _, _, VtQ = np.linalg.svd(Qc, full_matrices=False)
    best = None
    for sgn in (1, -1):
        # rotation aligning P major axis to Q major axis (with sign choice)
        aP = VtP[0]
        aQ = VtQ[0] * sgn
        th = math.atan2(aQ[1], aQ[0]) - math.atan2(aP[1], aP[0])
        R0 = np.array([[math.cos(th), -math.sin(th)], [math.sin(th), math.cos(th)]])
        # scale from extents along major axis
        eP = Pc @ aP
        eQ = Qc @ aQ
        s0 = (eQ.max() - eQ.min()) / (eP.max() - eP.min())
        if not (0.05 < s0 < 0.5):
            continue
        for dth in np.arange(-15, 15.1, 3.0):
            for ds in (0.8, 0.9, 1.0, 1.1, 1.25):
                th_ = th + dth
                s_ = s0 * ds
                R = np.array([[math.cos(th_), -math.sin(th_)],
                              [math.sin(th_), math.cos(th_)]])
                Pw = (R @ Pc.T).T * s_
                t0 = kpts.mean(0) - Pw.mean(0)
                for dt_x in (-3, 0, 3):
                    for dt_y in (-3, 0, 3):
                        t = t0 + np.array([dt_x, dt_y])
                        Q = Pw + t
                        # two-way chamfer: Q->poly sample + poly sample->plan walls
                        # poly sample DT in world raster
                        ix = np.clip(((Q[:, 0] - GX0) / g).astype(int), 0, GW - 1)
                        iy = np.clip(((GY1 - Q[:, 1]) / g).astype(int), 0, GH - 1)
                        cA = dt_w[iy, ix].mean()
                        # world->plan: sample poly boundary, inverse to plan frame
                        S = poly_samples[kpts_id] if False else None
                        yield (cA, th_, s_, t)

best_all = []
for kid, kpts in polys.items():
    res = []
    gen = None
    kpts_id = kid
    # replicate fit_one as inline loop (generator misuse avoided)
    P = np.stack([dorm_s[:, 0], -dorm_s[:, 1]], 1)
    Pc = P - P.mean(0)
    Qc = kpts - kpts.mean(0)
    _, _, VtP = np.linalg.svd(Pc, full_matrices=False)
    _, _, VtQ = np.linalg.svd(Qc, full_matrices=False)
    for sgn in (1, -1):
        aP = VtP[0]
        aQ = VtQ[0] * sgn
        th = math.atan2(aQ[1], aQ[0]) - math.atan2(aP[1], aP[0])
        eP = Pc @ aP
        eQ = Qc @ aQ
        s0 = (eQ.max() - eQ.min()) / (eP.max() - eP.min())
        if not (0.05 < s0 < 0.5):
            continue
        for dth in np.arange(-15, 15.1, 3.0):
            for ds in (0.8, 0.9, 1.0, 1.1, 1.25):
                th_ = th + dth
                s_ = s0 * ds
                R = np.array([[math.cos(th_), -math.sin(th_)],
                              [math.sin(th_), math.cos(th_)]])
                Pw = (R @ Pc.T).T * s_
                t0 = kpts.mean(0) - Pw.mean(0)
                for ddx in (-3, 0, 3):
                    for ddy in (-3, 0, 3):
                        t = t0 + np.array([ddx, ddy])
                        Q = Pw + t
                        ix = np.clip(((Q[:, 0] - GX0) / g).astype(int), 0, GW - 1)
                        iy = np.clip(((GY1 - Q[:, 1]) / g).astype(int), 0, GH - 1)
                        cA = dt_w[iy, ix].mean()
                        res.append((cA, th_, s_, t))
    res.sort(key=lambda x: x[0])
    if res:
        c, th_, s_, t = res[0]
        # world->plan term for the best
        a = math.radians(th_)
        R = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])
        Sp = poly_samples[kid]
        rel = (Sp - t) / s_
        pl = (R.T @ rel.T).T
        px = pl[:, 0] + dorm_s[:, 0].mean()
        py = -pl[:, 1] + (-dorm_s[:, 1].mean())
        okp = (px >= 0) & (px < 2500) & (py >= 0) & (py < 1666)
        cB = dt_plan[py[okp].astype(int), px[okp].astype(int)].mean() * s_ if okp.sum() > 50 else 99
        best_all.append((c + cB, c, cB, kid, th_, s_, t))
        print(f"{kid:7s} costA={c:6.2f} costB={cB:6.2f} th={th_:7.1f} s={s_:.3f} "
              f"t=({t[0]:6.1f},{t[1]:6.1f})")

best_all.sort(key=lambda x: x[0])
print("\nRANKING (two-way):")
for tot, cA, cB, kid, th_, s_, t in best_all:
    print(f"  {kid:7s} total={tot:6.2f} (A={cA:.2f} B={cB:.2f}) th={th_:7.1f} s={s_:.3f} "
          f"t=({t[0]:.1f},{t[1]:.1f})")
json.dump({"ranking": [(float(tot), float(cA), float(cB), kid, float(th_), float(s_),
                        [float(t[0]), float(t[1])]) for tot, cA, cB, kid, th_, s_, t in best_all]},
          open(f"{RES}/reg65_icp_5.65.json", "w"), indent=1)
print("saved reg65_icp_5.65.json")
