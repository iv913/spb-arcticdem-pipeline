#!/usr/bin/env python3
"""5.49: валидация профиля трассы (468 чёрных отметок) против GLO-30.
Станция -> lat/lon по полилинии ВУ (линейная интерполяция, t гасит масштаб);
выборка GLO-30 через rasterio/vsicurl. Выход:
results/lenobl_102_profile_glo30_5.49.csv, results/lenobl_102_validation_5.49.json,
results/lenobl_102_profile_5.49.geojson"""
import csv
import json
import math

import numpy as np
import rasterio

BASE = "/home/z/my-project/base"
ROUTE = f"{BASE}/results/lenobl_102_georef_5.49.json"
PROF = f"{BASE}/results/lenobl_102_profile_5.49.csv"

GLO_TILES = [
    "https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N59_00_E033_00_DEM/ Copernicus_DSM_COG_10_N59_00_E033_00_DEM.tif",
]
# корректные URL без пробела соберём функцией


def glo_url(ns, ew):
    ns2 = ns.replace("_00", "") + "_00"
    ew2 = ew.replace("_00", "") + "_00"
    return (f"https://copernicus-dem-30m.s3.amazonaws.com/"
            f"Copernicus_DSM_COG_10_{ns2}_{ew2}_DEM/"
            f"Copernicus_DSM_COG_10_{ns2}_{ew2}_DEM.tif")


def pk2m(pk):
    a, b = pk.split("+")
    return int(a) * 100 + float(b)


def station_to_ll(verts, st):
    """verts: [{pk, lat, lon}] — линейная интерполяция по сегменту."""
    for a, b in zip(verts, verts[1:]):
        s0, s1 = pk2m(a["pk"]), pk2m(b["pk"])
        if s0 <= st <= s1:
            t = (st - s0) / (s1 - s0) if s1 > s0 else 0
            return (a["lat"] + t * (b["lat"] - a["lat"]),
                    a["lon"] + t * (b["lon"] - a["lon"]))
    return None


def main():
    d = json.load(open(ROUTE))
    verts = d["route_wgs84"]
    prof = [(float(r["station_m"]), float(r["h_black_m"]))
            for r in csv.DictReader(open(PROF))]
    pts = []
    for st, h in prof:
        ll = station_to_ll(verts, st)
        if ll is None:
            ll = station_to_ll(verts, min(max(st, 0), pk2m(verts[-1]["pk"]) - 1))
        if ll is not None:
            pts.append((st, h, ll[0], ll[1]))
    print(f"профиль: {len(pts)} точек с координатами; "
          f"lat {min(p[2] for p in pts):.4f}..{max(p[2] for p in pts):.4f}, "
          f"lon {min(p[3] for p in pts):.4f}..{max(p[3] for p in pts):.4f}")

    # GLO-30: нужные тайлы
    lats = [p[2] for p in pts]
    lons = [p[3] for p in pts]
    tiles = set()
    for la, lo in zip(lats, lons):
        ns = f"N{int(math.floor(la)):02d}"
        ew = f"E{int(math.floor(lo)):03d}"
        tiles.add(glo_url(ns, ew))
    srcs = []
    for u in sorted(tiles):
        try:
            srcs.append(rasterio.open(u))
            print("GLO-30 tile ok:", u.split("/")[-1])
        except Exception as e:
            print("tile FAIL:", u.split("/")[-1], str(e)[:80])

    def sample(la, lo):
        for s in srcs:
            if s.bounds.left <= lo <= s.bounds.right and s.bounds.bottom <= la <= s.bounds.top:
                row, col = s.index(lo, la)
                win = rasterio.windows.Window(col - 1, row - 1, 3, 3)
                a = s.read(1, window=win).astype("float64")
                a[a < -1000] = np.nan
                v = np.nanmean(a)  # билинейная аппроксимация средним 3x3
                return float(v) if not math.isnan(v) else None
        return None

    rows = []
    for st, h, la, lo in pts:
        g = sample(la, lo)
        if g is not None:
            rows.append({"station_m": st, "h_black": h, "h_glo30": g,
                         "dH": h - g, "lat": la, "lon": lo})
    dh = np.array([r["dH"] for r in rows])
    if len(rows) == 0:
        raise SystemExit("GLO-30 недоступен — проверки не выполнены")
    med = float(np.median(dh))
    mad = float(np.median(np.abs(dh - med)))
    print(f"\nВалидация GLO-30 (n={len(rows)}):")
    print(f"  dH = h_black - h_GLO30: median {med:+.2f} м, MAD {mad:.2f} м, "
          f"p95(|dH|) {np.percentile(np.abs(dh), 95):.2f} м")
    print(f"  корреляция: {np.corrcoef([r['h_black'] for r in rows], [r['h_glo30'] for r in rows])[0,1]:.3f}")
    # по третям трассы
    for lo_i, hi_i in [(0, 8000), (8000, 16000), (16000, 24000)]:
        seg = [r for r in rows if lo_i <= r["station_m"] < hi_i]
        if seg:
            s = np.array([r["dH"] for r in seg])
            m = np.median(s)
            print(f"  ПК{lo_i//100+1:03d}-{hi_i//100:03d}: n={len(seg)} "
                  f"median {m:+.2f}, MAD {np.median(np.abs(s-m)):.2f}")

    with open(f"{BASE}/results/lenobl_102_profile_glo30_5.49.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["station_m", "h_black", "h_glo30", "dH", "lat", "lon"])
        w.writeheader()
        w.writerows(rows)

    ggs_val = []
    for g in d["ggs_wgs84"]:
        gg = sample(g["lat"], g["lon"])
        if gg is not None:
            ggs_val.append({"name": g["name"], "h_survey": g["h"],
                            "h_glo30": gg, "dH": g["h"] - gg})
    print("\nГГС пункты (независимый контроль):")
    for g in ggs_val:
        print(f"  {g['name']:16s} h={g['h_survey']:7.3f} GLO30={g['h_glo30']:7.2f} "
              f"dH={g['dH']:+.2f}")

    json.dump({"profile_vs_glo30": {
        "n": len(rows), "median_dH": med, "mad": mad,
        "corr": float(np.corrcoef([r['h_black'] for r in rows],
                                  [r['h_glo30'] for r in rows])[0, 1]),
        "note": "dH = чёрная отметка (Балтийская 1977) минус GLO-30 (EGM2008)"},
        "ggs_vs_glo30": ggs_val,
        "tiles": sorted(tiles)},
        open(f"{BASE}/results/lenobl_102_validation_5.49.json", "w"), indent=1)

    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"station_m": r["station_m"],
                                           "h_black": r["h_black"],
                                           "h_glo30": r["h_glo30"]},
         "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]}}
        for r in rows]}
    json.dump(fc, open(f"{BASE}/results/lenobl_102_profile_5.49.geojson", "w"))
    print("\n-> CSV/JSON/GEOJSON сохранены")


if __name__ == "__main__":
    main()
