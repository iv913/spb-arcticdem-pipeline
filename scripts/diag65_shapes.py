#!/usr/bin/env python3
"""5.65l: VISUAL diagnosis - plot plan dorm walls vs real korpus outlines to scale."""
import json
import math

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

RES = "/home/z/my-project/repo/results"
REF = (59.8723521, 30.4691565)
KX = 111320 * math.cos(math.radians(REF[0]))
KY = 111320.0

fig, axes = plt.subplots(1, 2, figsize=(16, 7))

# --- left: plan walls in the dorm window (meters at s=0.12 hypothesis) ---
pb = np.load(f"{RES}/reg65_plan_walls.npy")
WIN = (440, 1010, 1330, 1545)
sub = pb[(pb[:, 0] > WIN[0]) & (pb[:, 0] < WIN[1]) & (pb[:, 1] > WIN[2]) & (pb[:, 1] < WIN[3])]
ax = axes[0]
ax.scatter(sub[:, 0], -sub[:, 1], s=0.3, c="k")
ax.set_aspect(1)
ax.set_title("plan walls, dorm window (px, y up)")
ax.grid(True, lw=0.3)

# --- right: real korpus polygons (meters, world frame) ---
nom = json.load(open(f"{RES}/nom_poly_korpusa_5.65.json"))
ax = axes[1]
colors = {"88-к1": "tab:red", "88-к2": "tab:blue", "88-к3": "tab:green",
          "88-к4": "tab:orange", "88-к5": "tab:purple", "86-к1": "tab:brown",
          "86-к3": "tab:cyan"}
for key, h in nom.items():
    c = h.get("coords")
    if not (c and h["type"] == "Polygon"):
        continue
    pts = np.array([((p[0] - REF[1]) * KX, (p[1] - REF[0]) * KY) for p in c[0]])
    ax.plot(np.append(pts[:, 0], pts[0, 0]), np.append(pts[:, 1], pts[0, 1]),
            "-o", ms=3, lw=1.5, color=colors.get(key, "gray"), label=key)
    ax.annotate(key, pts.mean(0), fontsize=9)
ax.plot(41.2, -51.1, "k*", ms=14, label="88k2 addr")
ax.set_aspect(1)
ax.set_title("real korpus polygons (m, world frame)")
ax.grid(True, lw=0.3)
ax.legend(fontsize=8)
ax.set_xlim(-120, 160)
ax.set_ylim(-160, 160)
plt.tight_layout()
plt.savefig(f"{RES}/diag65_shapes.png", dpi=110)
print("saved diag65_shapes.png")

# --- plan crop with EXACT window borders for reference ---
from PIL import Image, ImageDraw
im = Image.open(f"{RES}/img_314154.jpg").convert("RGB")
dr = ImageDraw.Draw(im)
dr.rectangle([WIN[0], WIN[2], WIN[1], WIN[3]], outline=(255, 0, 255), width=4)
im.crop((WIN[0] - 60, WIN[2] - 60, WIN[1] + 60, WIN[3] + 60)).save(f"{RES}/diag65_planwin.png")
print("saved diag65_planwin.png")
