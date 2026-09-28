#!/usr/bin/env python3
"""5.47 A4: геопривязка merged-марок 514913.

Метод (как в 5.34/5.39b): аффинная rel→lon/lat по 77 соответствиям
сравнения (peer's georef), затем:
- невязки аффинного приближения на тех же 77
- санити OSM: марки с контекстом «Дунайский» должны лежать на
  геометрии проспекта (<30 м) — метод пользователя: перекрёстки/углы
  на открытой местности
- финальный CSV с lat/lon

Выход: results/archi_514913_marks_georef_5.47.csv
"""
import csv
import json
import numpy as np

REPO = "/home/z/my-project/spb45"


def fit_affine(rows):
    A = np.array([[r["rel_x"], r["rel_y"], 1.0] for r in rows])
    lat = np.array([float(r["lat"]) for r in rows])
    lon = np.array([float(r["lon"]) for r in rows])
    coef_lat, res_lat, *_ = np.linalg.lstsq(A, lat, rcond=None)
    coef_lon, res_lon, *_ = np.linalg.lstsq(A, lon, rcond=None)
    pred_lat = A @ coef_lat
    pred_lon = A @ coef_lon
    dlat = pred_lat - lat
    dlon = pred_lon - lon
    # в метрах
    mlat = dlat * 111320.0
    mlon = dlon * 111320.0 * np.cos(np.radians(59.83))
    dist = np.hypot(mlat, mlon)
    return coef_lat, coef_lon, dist


def main():
    comp = list(csv.DictReader(open(f"{REPO}/scripts/data/archi_ru_514913_comparison.csv")))
    for r in comp:
        r["rel_x"] = float(r["rel_x"])
        r["rel_y"] = float(r["rel_y"])
    coef_lat, coef_lon, dist = fit_affine(comp)
    print(f"аффинное приближение по {len(comp)} соответствиям:")
    print(f"  медиана невязки {np.median(dist):.1f} м, p90 {np.percentile(dist, 90):.1f}, "
          f"max {dist.max():.1f}")

    marks = list(csv.DictReader(open(f"{REPO}/results/archi_514913_marks_merged_5.47.csv")))
    for m in marks:
        x, y = float(m["x"]), float(m["y"])
        m["lat"] = float(coef_lat @ [x, y, 1.0])
        m["lon"] = float(coef_lon @ [x, y, 1.0])

    lats = [m["lat"] for m in marks]
    lons = [m["lon"] for m in marks]
    print(f"bbox марок: lat {min(lats):.5f}..{max(lats):.5f}, "
          f"lon {min(lons):.5f}..{max(lons):.5f}")
    print(f"  (Дунайский 27: 59.83280, 30.36270)")

    with open(f"{REPO}/results/archi_514913_marks_georef_5.47.csv", "w",
              newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["val", "x", "y", "ctx", "src", "lat", "lon"])
        w.writeheader()
        w.writerows(marks)
    print(f"сохранено {len(marks)} марок -> archi_514913_marks_georef_5.47.csv")

    # аффинные коэффициенты — для воспроизводимости
    json.dump({"coef_lat": list(coef_lat), "coef_lon": list(coef_lon),
               "fit_median_m": float(np.median(dist)),
               "n_fit": len(comp)},
              open(f"{REPO}/results/archi_514913_georef_coef_5.47.json", "w"), indent=1)


if __name__ == "__main__":
    main()
