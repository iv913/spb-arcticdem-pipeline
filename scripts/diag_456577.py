#!/usr/bin/env python3
"""Диагностика: нарисовать полигон OSM-здания на плане 456577."""
import json
import math

import cv2
import numpy as np

REPO = "/home/z/my-project/spb45"
IMG = "/home/z/my-project/wr/archi3/full/456577.jpg"
coef = json.load(open(f"{REPO}/results/georef_456577_5.47.json"))
osm = json.load(open(f"{REPO}/results/osm_456577_5.47.json"))

lat0, lon0 = coef["lat0"], coef["lon0"]
kx = 111320 * math.cos(math.radians(59.92))
scale, R = coef["scale"], np.array(coef["R"])
c_p = np.array(coef["c_p"])
shift = np.array(coef["shift"])


def lonlat2px(lon, lat):
    m = np.array([(lon - lon0) * kx, (lat - lat0) * 111320]) - shift
    v = np.linalg.inv(scale * R) @ m
    return c_p + np.array([v[0], -v[1]])


# ближайшее к проекту здание
best, bestd = None, 1e9
for b in osm["buildings"]:
    cx = sum(p[0] for p in b["pts"]) / len(b["pts"])
    cy = sum(p[1] for p in b["pts"]) / len(b["pts"])
    d = math.hypot((cx - lon0) * kx, (cy - lat0) * 111320)
    if d < bestd:
        bestd, best = d, b
print("bld dist to proj point:", round(bestd, 1), "m")

im = cv2.imread(IMG)
for lon, lat in best["pts"]:
    x, y = lonlat2px(lon, lat)
    cv2.circle(im, (int(x), int(y)), 4, (0, 0, 255), -1)
cv2.imwrite("/tmp/diag_456577.png", im)
print("saved /tmp/diag_456577.png")
