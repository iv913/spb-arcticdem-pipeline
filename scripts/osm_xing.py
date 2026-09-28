#!/usr/bin/env python3
"""Хелпер: пересечения осей дорог по именам из выгрузки OSM.

usage: osm_xing.py <osm.json> <lat0> <lon0> ["Имя A" "Имя B"] ...
Печатает точки пересечения полилиний (в lat/lon и в метрах от центра).
"""
import json
import math
import sys

import numpy as np


def seg_inter(p1, p2, p3, p4):
    d1, d2 = p2 - p1, p4 - p3
    den = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(den) < 1e-12:
        return None
    t = ((p3[0] - p1[0]) * d2[1] - (p3[1] - p1[1]) * d2[0]) / den
    u = ((p3[0] - p1[0]) * d1[1] - (p3[1] - p1[1]) * d1[0]) / den
    if 0 <= t <= 1 and 0 <= u <= 1:
        return p1 + t * d1
    return None


def main():
    path, lat0, lon0 = sys.argv[1], float(sys.argv[2]), float(sys.argv[3])
    names = sys.argv[4:]
    osm = json.load(open(path))
    kx = 111320 * math.cos(math.radians(lat0))

    def m2(lon, lat):
        return np.array([(lon - lon0) * kx, (lat - lat0) * 111320])

    roads = {}
    for r in osm["roads"]:
        if r["name"]:
            roads.setdefault(r["name"], []).append(
                np.array([m2(lon, lat) for lon, lat in r["pts"]]))
    print("имена в выгрузке:", [n for n in roads if any(n.startswith(q[:8]) for q in names)][:20])
    for i in range(0, len(names) - 1, 2):
        na, nb = names[i], names[i + 1]
        A = roads.get(na, [])
        B = roads.get(nb, [])
        if not A or not B:
            # мягкий поиск по подстроке
            ka = [k for k in roads if na[:10].lower() in k.lower()]
            kb = [k for k in roads if nb[:10].lower() in k.lower()]
            print(f"не найдено '{na}'×'{nb}'; похожие: {ka[:3]} × {kb[:3]}")
            continue
        found = []
        for la in A:
            for lb in B:
                for j in range(len(la) - 1):
                    for k in range(len(lb) - 1):
                        p = seg_inter(la[j], la[j + 1], lb[k], lb[k + 1])
                        if p is not None:
                            found.append(p)
        # дедуп
        uniq = []
        for p in found:
            if all(np.hypot(*(p - q)) > 5 for q in uniq):
                uniq.append(p)
        print(f"{na} × {nb}: {len(uniq)} точек")
        for p in uniq[:6]:
            print(f"   метры от центра: ({p[0]:.0f}, {p[1]:.0f})")


if __name__ == "__main__":
    main()
