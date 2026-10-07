#!/usr/bin/env python3
"""5.65i: Direct dorm-building correspondence fit + global pose evaluation."""
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

# ---------- real blobs from OSM mask ----------
meta = json.load(open(f"{RES}/osm_sosnovka_z18_5.65.png.json"))
mpp = meta["m_per_px"]
mask = np.asarray(Image.open(f"{RES}/osm_bld_mask_z18_5.65.png")) > 127
wy0 = (meta["lat_top"] - REF[0]) * KY
wx0 = (meta["lon_left"] - REF[1]) * KX
ys, xs = np.where(mask)
wx = wx0 + xs * mpp
wy = wy0 - ys * mpp
ok = (wx > -220) & (wx < 300) & (wy > -220) & (wy < 200)
wpts_all = np.stack([wx[ok], wy[ok]], 1)
# rasterize to 0.5 m grid for blob labeling
gsc = 0.5
GX0, GY1 = -220, 200
GW = int(520 / gsc) + 1
GH = int(420 / gsc) + 1
gmask = np.zeros((GH, GW), dtype=bool)
gix = ((wpts_all[:, 0] - GX0) / gsc).astype(int)
giy = ((GY1 - wpts_all[:, 1]) / gsc).astype(int)
gmask[giy, gix] = True
gmask = ndimage.binary_closing(gmask, np.ones((3, 3)))
lab, n = ndimage.label(gmask, structure=np.ones((3, 3)))
print("world blobs:", n)
blobs = []
for i in range(1, n + 1):
    bys, bxs = np.where(lab == i)
    if len(bxs) < 60:  # < 15 m2
        continue
    bwx = bxs * gsc + GX0
    bwy = GY1 - bys * gsc
    pts = np.stack([bwx, bwy], 1)
    c = pts.mean(0)
    if np.hypot(*(c - k2_w)) > 170:
        continue
    # PCA
    p0 = pts - c
    cov = p0.T @ p0 / len(p0)
    ev, evec = np.linalg.eigh(cov)
    L = 4 * math.sqrt(ev[1])  # ~length proxy (2 sigma x2)
    Wd = 4 * math.sqrt(ev[0])
    ang = math.degrees(math.atan2(evec[1, 1], evec[0, 1]))
    blobs.append({"c": c, "pts": pts, "len": L, "wid": Wd, "ang": ang,
                  "n": len(pts)})
blobs.sort(key=lambda b: -b["len"])
print(f"blobs near k2: {len(blobs)}")
for b in blobs[:14]:
    print(f"  c=({b['c'][0]:6.1f},{b['c'][1]:6.1f}) len~{b['len']:5.1f} wid~{b['wid']:4.1f} "
          f"ang={b['ang']:6.1f} n={b['n']}")

# ---------- plan dorm outline ----------
pb = np.load(f"{RES}/reg65_plan_walls.npy")
dorm = pb[(pb[:, 0] > 470) & (pb[:, 0] < 960) & (pb[:, 1] > 1350) & (pb[:, 1] < 1530)]
print("plan dorm pts:", len(dorm))
dc = dorm.mean(0)
p0 = np.stack([dorm[:, 0] - dc[0], -(dorm[:, 1] - dc[1])], 1)  # y-flip (math frame)
cov = p0.T @ p0 / len(p0)
ev, evec = np.linalg.eigh(cov)
Lp = 4 * math.sqrt(ev[1])
Wp = 4 * math.sqrt(ev[0])
angp = math.degrees(math.atan2(evec[1, 1], evec[0, 1]))
print(f"plan dorm: len~{Lp:.0f}px wid~{Wp:.0f}px ang={angp:.1f} centroid px=({dc[0]:.0f},{dc[1]:.0f})")

# ---------- try correspondences ----------
dorm_pts_sub = dorm[np.random.default_rng(3).choice(len(dorm), min(900, len(dorm)), replace=False)]


def global_cost(th, s, t):
    a = math.radians(th)
    ca, sa = math.cos(a), math.sin(a)
    rel = np.stack([dorm_pts_sub[:, 0] - dc[0], -(dorm_pts_sub[:, 1] - dc[1])], 1)
    R = np.array([[ca, -sa], [sa, ca]])
    pw = (R @ rel.T).T * s + t
    ix = np.clip(((pw[:, 0] - GX0) / gsc).astype(int), 0, GW - 1)
    iy = np.clip(((GY1 - pw[:, 1]) / gsc).astype(int), 0, GH - 1)
    hit = gmask[iy, ix]
    # distance to nearest world blob pixel via DT of gmask
    return hit.mean()


# world DT for cost
dt_w = ndimage.distance_transform_edt(~gmask) * gsc
ix = np.clip(((wpts_all[:, 0] - GX0) / gsc).astype(int), 0, GW - 1)
iy = np.clip(((GY1 - wpts_all[:, 1]) / gsc).astype(int), 0, GH - 1)
sub_w = wpts_all[np.random.default_rng(5).choice(len(wpts_all), 800, replace=False)]


def two_way(th, s, t):
    a = math.radians(th)
    ca, sa = math.cos(a), math.sin(a)
    R = np.array([[ca, -sa], [sa, ca]])
    rel = np.stack([dorm_pts_sub[:, 0] - dc[0], -(dorm_pts_sub[:, 1] - dc[1])], 1)
    pw = (R @ rel.T).T * s + t
    ix = np.clip(((pw[:, 0] - GX0) / gsc).astype(int), 0, GW - 1)
    iy = np.clip(((GY1 - pw[:, 1]) / gsc).astype(int), 0, GH - 1)
    cA = dt_w[iy, ix].mean()
    Rw = R.T
    rel_w = (sub_w - t) / s
    pl = (Rw @ rel_w.T).T
    px = pl[:, 0] + dc[0]
    py = -pl[:, 1] + dc[1]
    okp = (px >= 0) & (px < 2500) & (py >= 0) & (py < 1666)
    if okp.sum() < 100:
        return 99.0
    return cA  # dorm-only one-way for now; global check later


cands = []
for b in blobs:
    if b["len"] < 25:
        continue
    s0 = b["len"] / Lp  # m per px from lengths
    if not (0.05 < s0 < 0.45):
        continue
    for ang_sign in (1, -1):
        th0 = b["ang"] - ang_sign * angp
        for dth in (-12, -6, 0, 6, 12):
            th = th0 + dth
            a = math.radians(th)
            R = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])
            # translation so centroids coincide
            rel = np.stack([dorm_pts_sub[:, 0] - dc[0], -(dorm_pts_sub[:, 1] - dc[1])], 1)
            base = (R @ rel.T).T * s0
            t0 = b["c"] - base.mean(0)
            for dtx in (-8, -4, 0, 4, 8):
                for dty in (-8, -4, 0, 4, 8):
                    t = t0 + np.array([dtx, dty])
                    c = two_way(th, s0, t)
                    cands.append((c, th, s0, t[0], t[1], b["c"].tolist(), b["len"]))
cands.sort(key=lambda x: x[0])
print("TOP-12 dorm-correspondence poses:")
for c, th, s, tx, ty, bc, bl in cands[:12]:
    print(f"  cost={c:.3f} th={th:7.1f} s={s:.3f} t=({tx:6.1f},{ty:6.1f}) blob_c=({bc[0]:.0f},{bc[1]:.0f}) blen={bl:.0f}")
json.dump({"top": [(float(c), float(th), float(s), float(tx), float(ty)) for c, th, s, tx, ty, _, _ in cands[:40]],
           "plan_dorm": {"centroid_px": dc.tolist(), "len_px": Lp, "wid_px": Wp, "ang": angp},
           "blobs": [{"c": b["c"].tolist(), "len": b["len"], "wid": b["wid"], "ang": b["ang"]} for b in blobs[:20]]},
          open(f"{RES}/reg65_dormfit_5.65.json", "w"), indent=1)
print("saved reg65_dormfit_5.65.json")
