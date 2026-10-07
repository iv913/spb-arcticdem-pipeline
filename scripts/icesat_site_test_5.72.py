#!/usr/bin/env python3
"""5.72: Site-level тест приёмки планов (автоматизация правила 5.69), v2.

Урок самотеста v1: круг 500 м на рельефе с грядами смешивает холм и площадку
(Полюстрово: ground_med 27.46 при кампусе ~10) -> ложный REJECT; в плотном
центре ICESat-2 пуст (n=0). В v2 эталон земли — непрерывный FABDEM bare-earth
(5.71, MAD 0.34 на 33908 полевых точках), ICESat-2 clean — кросс-чек.

Правило: |marks_median − DTM_ground_median(p25 окна)| > 5 м -> REJECT_DATUM
(план в относительных/проектных отметках или ошибка геопривязки).

CLI:
  python icesat_site_test_5.72.py --name S --lat 59.86 --lon 30.45 \
      [--values 2.3,3.25] [--radius 300] [--out results/x.json]
Без аргументов — самотест на 4 известных кейсах.
"""
import argparse
import json

import numpy as np
import pandas as pd
import rasterio

REPO = "/home/z/my-project/repo"
DTM = f"{REPO}/scripts/data/consensus_dtm_5.71/bareearth_bs77_cm_5.71.tif"
CLEAN = f"{REPO}/scripts/data/icesat2_clean.csv"
BBOX = (28.35, 59.55, 31.05, 60.30)  # W,S,E,N сетки 5.71


def _win(lat, lon, radius_m):
    r = radius_m / 111320
    rlo = radius_m / (111320 * np.cos(np.radians(lat)))
    return lat - r, lat + r, lon - rlo, lon + rlo


def dtm_ground(lat, lon, radius_m=300):
    with rasterio.open(DTM) as s:
        la0, la1, lo0, lo1 = _win(lat, lon, radius_m)
        w = s.window(lo0, la0, lo1, la1)
        a = s.read(1, window=w).astype("float64")
        a[a == s.nodata] = np.nan
        a[a < -1000] = np.nan
    a = a[np.isfinite(a)] / 100.0
    if a.size < 10:
        return {"n_px": int(a.size), "in_bbox": False}
    return {"n_px": int(a.size), "in_bbox": True,
            "ground_med_bs77": round(float(np.median(a)), 2),
            "ground_q25": round(float(np.quantile(a, 0.25)), 2),
            "ground_min": round(float(a.min()), 2),
            "ground_max": round(float(a.max()), 2)}


def icesat_ground(lat, lon, radius_m=500):
    df = pd.read_csv(CLEAN, usecols=["lat", "lon", "h_icesat_baltic"])
    la0, la1, lo0, lo1 = _win(lat, lon, radius_m)
    s = df[(df.lat >= la0) & (df.lat <= la1) & (df.lon >= lo0) & (df.lon <= lo1)]
    h = s["h_icesat_baltic"].values
    h = h[~np.isnan(h)]
    if len(h) < 3:
        return {"n": int(len(h))}
    med = float(np.median(h))
    return {"n": int(len(h)), "ground_med_bs77": round(med, 2),
            "ground_mad": round(float(np.median(np.abs(h - med))), 2)}


def site_test(name, lat, lon, values=None, radius_m=300):
    dt = dtm_ground(lat, lon, radius_m)
    ic = icesat_ground(lat, lon, max(500, radius_m))
    out = {"name": name, "lat": lat, "lon": lon, "radius_m": radius_m,
           "dtm_ground": dt, "icesat_ground": ic}
    if not values:
        out["decision"] = "GROUND_ONLY"
        return out
    v = np.array(values, dtype=float)
    vmed = float(np.median(v))
    out["marks"] = {"n": int(len(v)), "min": round(float(v.min()), 2),
                    "max": round(float(v.max()), 2), "median": round(vmed, 2)}
    if not dt.get("in_bbox") or dt.get("n_px", 0) < 10:
        out["decision"] = "NO_DTM_COVERAGE"
        return out
    ref = dt["ground_med_bs77"]
    diff = vmed - ref
    out["median_minus_ground"] = round(diff, 2)
    out["decision"] = ("REJECT_DATUM" if abs(diff) > 5.0
                       else ("BORDERLINE" if abs(diff) > 3.0 else "ACCEPT"))
    out["implied_zero_datum_bs77"] = ref
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--name", default="site")
    p.add_argument("--lat", type=float)
    p.add_argument("--lon", type=float)
    p.add_argument("--values", default="")
    p.add_argument("--radius", type=int, default=300)
    p.add_argument("--out", default="")
    a = p.parse_args()
    if a.lat is None:
        cases = [
            ("Дунайский 27 (345639) — план относительный", 59.8225, 30.4072,
             [2.30, 2.45, 2.60, 2.75, 2.90, 3.25]),
            ("ID Moskovskiy (Московский пр. 72)", 59.9127, 30.3182,
             [15.1, 15.3, 15.5, 15.7, 16.0]),
            ("Политехническая + МФК (гряда рядом)", 59.9998, 30.3652,
             [12.0, 12.5, 13.0, 14.2, 15.0]),
            ("Шпалерная 51 (Центр)", 59.9488, 30.3741,
             [7.5, 8.0, 8.2, 9.0]),
            ("Купчино, земля площадки", 59.8225, 30.4072, None),
        ]
        res = [site_test(n, la, lo, v, 300) for n, la, lo, v in cases]
        for r in res:
            print(json.dumps(r, ensure_ascii=False))
        json.dump(res, open(f"{REPO}/results/icesat_site_selftest_5.72.json",
                            "w"), ensure_ascii=False, indent=1)
        print("selftest -> results/icesat_site_selftest_5.72.json")
        return
    vals = [float(x) for x in a.values.split(",") if x.strip()]
    r = site_test(a.name, a.lat, a.lon, vals, a.radius)
    print(json.dumps(r, ensure_ascii=False, indent=1))
    if a.out:
        json.dump(r, open(a.out, "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
