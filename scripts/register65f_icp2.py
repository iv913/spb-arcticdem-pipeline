#!/usr/bin/env python3
"""5.65k: FIXED ICP - per-korpus boundary DT + correct inverse + scale bound."""
import json
import math

import numpy as np
from PIL import Image
from scipy import ndimage

RES = "/home/z/my-project/repo/results"
REF = (59.8723521, 30.4691565)
KX = 111320 * math.cos(math.radians(REF[0]))
KY = 111320.0

nom = json.load(open(f"{RES}/nom_poly_korpusa_5.65.json"))
polys = {}
for key, h in nom.items():
    c = h.get("coords")
    if c and h["type"] == "Polygon":
        pts = np.array([((p[0] - REF[1]) * KX, (p[1] - REF[0]) * KY) for p in c[0]])
        polys[key] = pts[:-1] if np.allclose(pts[0], pts[-1]) else pts


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


# per-korpus boundary DT on 0.5 m grid (window +-40 m around polygon)
g = 0.5
kdt = {}
ksample = {}
for kid, kpts in polys.items():
    S = sample_poly(kpts, 0.5)
    ksample[kid] = S
    cx, cy = S.mean(0)
    W = H = int(80 / g) + 1
    m = np.zeros((H, W), dtype=bool)
    ix = ((S[:, 0] - (cx - 40)) / g).astype(int)
    iy = ((cy + 40 - S[:, 1]) / g).astype(int)
    okk = (ix >= 0) & (ix < W) & (iy >= 0) & (iy < H)
    m[iy[okk], ix[okk]] = True
    kdt[kid] = (ndimage.distance_transform_edt(~m) * g, cx - 40, cy + 40, W, H)

pb = np.load(f"{RES}/reg65_plan_walls.npy")
WIN = (470, 980, 1340, 1535)
dorm = pb[(pb[:, 0] > WIN[0]) & (pb[:, 0] < WIN[1]) &
          (pb[:, 1] > WIN[2]) & (pb[:, 1] < WIN[3])]
pm = np.zeros((1666, 2500), dtype=bool)
pm[pb[:, 1].astype(int), pb[:, 0].astype(int)] = True
dt_plan = ndimage.distance_transform_edt(~pm)
rng = np.random.default_rng(11)
dorm_s = dorm[rng.choice(len(dorm), min(900, len(dorm)), replace=False)]
P = np.stack([dorm_s[:, 0], -dorm_s[:, 1]], 1)
meanP = P.mean(0)          # [mx, -my]
mx, my = dorm_s[:, 0].mean(), dorm_s[:, 1].mean()
Pc = P - meanP

results = []
for kid, kpts in polys.items():
    Qc = kpts - kpts.mean(0)
    S = ksample[kid]
    _, _, VtP = np.linalg.svd(Pc, full_matrices=False)
    _, _, VtQ = np.linalg.svd(Qc, full_matrices=False)
    for sgn in (1, -1):
        aP, aQ = VtP[0], VtQ[0] * sgn
        th0 = math.atan2(aQ[1], aQ[0]) - math.atan2(aP[1], aP[0])
        eP, eQ = Pc @ aP, Qc @ aQ
        s0 = (eQ.max() - eQ.min()) / (eP.max() - eP.min())
        for dth in np.arange(-18, 18.1, 2.0):
            for ds in (0.85, 1.0, 1.15, 1.3):
                th_ = th0 + dth
                s_ = s0 * ds
                if not (0.09 <= s_ <= 0.30):
                    continue
                a = math.radians(th_)
                R = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])
                Pw = (R @ Pc.T).T * s_
                t0 = kpts.mean(0) - Pw.mean(0)
                for ddx in (-4, -2, 0, 2, 4):
                    for ddy in (-4, -2, 0, 2, 4):
                        t = t0 + np.array([ddx, ddy])
                        Q = Pw + t
                        dtm, gx0, gy1, W, H = kdt[kid]
                        ix = np.clip(((Q[:, 0] - gx0) / g).astype(int), 0, W - 1)
                        iy = np.clip(((gy1 - Q[:, 1]) / g).astype(int), 0, H - 1)
                        cA = dtm[iy, ix].mean()
                        # world->plan (fixed sign): plan_pt = R^-1 ((S - t)/s) + meanP; y = -(pl_y) + my
                        pl = (R.T @ ((S - t) / s_).T).T
                        px = pl[:, 0] + mx
                        py = -pl[:, 1] + my
                        okp = (px >= 0) & (px < 2500) & (py >= 0) & (py < 1666)
                        cB = dt_plan[py[okp].astype(int), px[okp].astype(int)].mean() * s_ \
                            if okp.sum() > 0.5 * len(S) else 60.0
                        results.append((cA + cB, cA, cB, kid, th_, s_, t))

results.sort(key=lambda x: x[0])
print("TOP-15 (two-way, per-korpus boundary):")
seen = set()
for tot, cA, cB, kid, th_, s_, t in results[:15]:
    tag = f"{kid}:{round(th_)},{round(s_,3)}"
    print(f"  {kid:7s} total={tot:6.2f} (A={cA:5.2f} B={cB:5.2f}) th={th_:7.1f} "
          f"s={s_:.3f} t=({t[0]:7.1f},{t[1]:7.1f})")
json.dump({"top": [(float(tot), float(cA), float(cB), kid, float(th_), float(s_),
                    [float(t[0]), float(t[1])]) for tot, cA, cB, kid, th_, s_, t in results[:40]]},
          open(f"{RES}/reg65_icp2_5.65.json", "w"), indent=1)
print("saved reg65_icp2_5.65.json")
