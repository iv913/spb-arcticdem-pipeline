#!/usr/bin/env python3
"""5.47: аффинная геопривязка 422695 (ВО) по 2 якорям + оверлей OSM-дорог.

A1 Опочинина×Средний пр:  px(1425,1320) <-> (221,105) м от центра
A2 Наличная×Шкиперский:    px(1085, 180) <-> (40,340)  м от центра
Сходящееся преобразование (масштаб+поворот+сдвиг). Оверлей — для
визуальной проверки, корректировки якорей.
"""
import json
import math

import cv2
import numpy as np

REPO = "/home/z/my-project/spb45"
IMG = "/home/z/my-project/wr/archi3/full/422695.jpg"
lat0, lon0 = 59.930571, 30.234707
kx = 111320 * math.cos(math.radians(lat0))

A1px = np.array([1425., 1320.]); A1m = np.array([221., 105.])
A2px = np.array([1085., 180.]);  A2m = np.array([40., 340.])

# пиксели -> метры (y вверх)
def px2m(p):
    return np.array([p[0], 1740 - p[1]])

vp = px2m(A2px) - px2m(A1px)
vo = A2m - A1m
scale = np.linalg.norm(vo) / np.linalg.norm(vp)
ang = math.atan2(vo[1], vo[0]) - math.atan2(vp[1], vp[0])
R = np.array([[math.cos(ang), -math.sin(ang)], [math.sin(ang), math.cos(ang)]])
t = A1m - scale * R @ px2m(A1px)
print(f"scale {scale:.4f} м/px, угол {math.degrees(ang):.1f}°")


def px2lonlat(p):
    m = scale * R @ px2m(p) + t
    return lon0 + m[0] / kx, lat0 + m[1] / 111320


def lonlat2px(lon, lat):
    m = np.array([(lon - lon0) * kx, (lat - lat0) * 111320]) - t
    v = np.linalg.inv(scale * R) @ m
    return np.array([v[0], 1740 - v[1]])


# проверка: невязка A2
err2 = np.hypot(*(scale * R @ px2m(A2px) + t - A2m))
print(f"невязка A2: {err2:.2f} м")

# оверлей
im = cv2.imread(IMG)
osm = json.load(open(f"{REPO}/results/osm_422695_5.47.json"))
for r in osm["roads"]:
    col = (0, 160, 255) if r.get("kind") in ("primary", "secondary", "trunk") else (0, 220, 0)
    pts = [tuple(lonlat2px(lon, lat).astype(int)) for lon, lat in r["pts"]]
    for a, b in zip(pts[:-1], pts[1:]):
        cv2.line(im, a, b, col, 2)
# якоря
for p, name in [(A1px, "A1"), (A2px, "A2")]:
    x, y = int(p[0]), int(p[1])
    cv2.circle(im, (x, y), 12, (0, 0, 255), 3)
    cv2.putText(im, name, (x + 14, y), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
cv2.imwrite("/tmp/overlay_422695.png", im)
small = cv2.resize(im, (1250, 870))
cv2.imwrite("/tmp/overlay_422695_small.png", small)
print("overlay saved")
