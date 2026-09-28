#!/usr/bin/env python3
"""5.47: геопривязка 456577 по 2 углам здания + контроль на 3-м.

Якоря (пиксели плана, читаны с сеткой):
  W  (296, 507)  — западный кончик здания
  SE (1075, 408) — юго-восточный угол (у пандуса)
  S  (460, 690)  — южный угол (контроль)
OSM: ближайшее к проекту здание; его крайние вершины.
"""
import json
import math

import numpy as np

REPO = "/home/z/my-project/spb45"
osm = json.load(open(f"{REPO}/results/osm_456577_5.47.json"))
lat0, lon0 = 59.921389, 30.287699
kx = 111320 * math.cos(math.radians(59.92))

best, bestd = None, 1e9
for b in osm["buildings"]:
    cx = sum(p[0] for p in b["pts"]) / len(b["pts"])
    cy = sum(p[1] for p in b["pts"]) / len(b["pts"])
    d = math.hypot((cx - lon0) * kx, (cy - lat0) * 111320)
    if d < bestd:
        bestd, best = d, b
P = np.array([[ (lon - lon0) * kx, (lat - lat0) * 111320] for lon, lat in best["pts"]])
print(f"здание: {len(P)} вершин, dist {bestd:.0f} м")
w_i = np.argmin(P[:, 0])   # запад
e_i = np.argmax(P[:, 0])   # восток
s_i = np.argmin(P[:, 1])   # юг
print(f"OSM W={P[w_i].round(1)} E={P[e_i].round(1)} S={P[s_i].round(1)}")

# пиксели плана
Wpx = np.array([296., 507.])
SEpx = np.array([1075., 408.])
Spx = np.array([460., 690.])
# в метрическую конвенцию плана (y вверх): my = (H - y), H=1140
H_IMG = 1140
Wm_px = np.array([Wpx[0], H_IMG - Wpx[1]])
SEm_px = np.array([SEpx[0], H_IMG - SEpx[1]])
Sm_px = np.array([Spx[0], H_IMG - Spx[1]])

# similarity по двум точкам: v_план -> v_OSM
v_p = SEm_px - Wm_px
v_o = P[e_i] - P[w_i]
scale = np.linalg.norm(v_o) / np.linalg.norm(v_p)
ang = math.atan2(v_o[1], v_o[0]) - math.atan2(v_p[1], v_p[0])
R = np.array([[math.cos(ang), -math.sin(ang)], [math.sin(ang), math.cos(ang)]])
# сдвиг: W совмещаем точно
t = P[w_i] - scale * R @ Wm_px
print(f"scale={scale:.4f} м/px, angle={math.degrees(ang):.2f} deg")

def px2lonlat(x, y):
    m = np.array([x, H_IMG - y])
    return (lon0 + (scale * R @ m + t)[0] / kx,
            lat0 + (scale * R @ m + t)[1] / 111320)

# контроль на южном углу
s_pred = px2lonlat(*Spx)
s_true = (lon0 + P[s_i][0] / kx, lat0 + P[s_i][1] / 111320)
err = math.hypot((s_pred[0] - s_true[0]) * kx, (s_pred[1] - s_true[1]) * 111320)
print(f"контроль S: предсказан ({s_pred[1]:.6f},{s_pred[0]:.6f}), "
      f"OSM ({s_true[1]:.6f},{s_true[0]:.6f}), ошибка {err:.1f} м")

json.dump({"scale": float(scale), "angle_deg": float(math.degrees(ang)),
           "R": R.tolist(), "t": t.tolist(), "lat0": lat0, "lon0": lon0,
           "H_IMG": H_IMG, "anchors_px": {"W": Wpx.tolist(), "SE": SEpx.tolist(),
                                          "S": Spx.tolist()},
           "check_S_err_m": float(err)},
          open(f"{REPO}/results/georef_456577_5.47.json", "w"), indent=1)
print("коэффициенты сохранены")
