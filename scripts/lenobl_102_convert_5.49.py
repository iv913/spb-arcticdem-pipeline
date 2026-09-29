#!/usr/bin/env python3
"""5.49: батч-конвертация МСК-47 зона 3 -> WGS84 через polygon.top oracle
(EPDG:6334703 = МСК-47 зона 3 Ленинградская область; порядок в входе: Y X).
Выход: results/lenobl_102_georef_5.49.json (+ helmert-fit локальной аффины)."""
import json
import urllib.parse
import urllib.request

import numpy as np

URL = "https://polygon.top/ws/ajax.php"
SC = "EPSG:6334703"


def post(data):
    req = urllib.request.Request(
        URL, data=urllib.parse.urlencode(data).encode(),
        headers={"User-Agent": "Mozilla/5.0 Chrome/124.0",
                 "X-Requested-With": "XMLHttpRequest",
                 "Referer": "https://polygon.top/ws/csc/"})
    return json.load(urllib.request.urlopen(req, timeout=40))


def batch_convert(pairs_xy):
    """pairs_xy: [(x_north, y_east), ...] -> [(lat, lon), ...]"""
    txt = "\n".join(f"{y:.2f} {x:.2f}" for x, y in pairs_xy)
    r = post({"WssType": "csc", "WssMode": "convert", "sourceText": txt,
              "sourceSc": SC, "destSc": "EPSG:4326", "sourceRound": 2,
              "sourceRoundDec": 2, "destRound": 2, "destRoundDec": 8,
              "destChange": "", "sourceChange": ""})
    if r.get("error"):
        raise RuntimeError(r["error"][:200])
    out = []
    for row in r["destTable"]:
        out.append((row["y1"], row["x1"]))  # lat, lon
    return out


def helmert_fit(msk, wgs_latlon):
    """4-параметр. преобразование (scale+rot) МСК->локальные метры (гномическая
    проекция вокруг центра) — для локальной трансформации без oracle."""
    lat0 = np.mean([p[0] for p in wgs_latlon])
    lon0 = np.mean([p[1] for p in wgs_latlon])
    kx, ky = 111320.0, 111320.0 * np.cos(np.radians(lat0))
    E = np.array([(p[1] - lon0) * ky for p in wgs_latlon])
    N = np.array([(p[0] - lat0) * kx for p in wgs_latlon])
    X = np.array([p[0] for p in msk])
    Y = np.array([p[1] for p in msk])
    Xc, Yc = X - X.mean(), Y - Y.mean()
    # N = a*Xc + b*Yc + cN ; E = -b*Xc + a*Yc + cE (поворот+масштаб)
    A = np.column_stack([Xc, Yc, np.ones_like(Xc)])
    solN, *_ = np.linalg.lstsq(A, N, rcond=None)
    solE, *_ = np.linalg.lstsq(A, E, rcond=None)
    predN = A @ solN
    predE = A @ solE
    res = np.hypot(predN - N, predE - E)
    return {"lat0": lat0, "lon0": lon0,
            "solN": solN.tolist(), "solE": solE.tolist(),
            "residual_m": {"max": float(res.max()),
                           "rms": float(np.sqrt((res ** 2).mean()))}}


def helmert_apply(fit, x, y):
    Xc = np.asarray(x) - fit["lat0"] * 0 - np.mean([p[0] for p in [x]]) * 0
    # перерасчёт центроида передаётся явно — см. вызов
    raise NotImplementedError


if __name__ == "__main__":
    route = json.load(open("/home/z/my-project/base/results/lenobl_102_route_5.49.json"))
    verts = route["route"]
    ggs = route["ggs"]
    xy = [(v["x"], v["y"]) for v in verts] + [(g["x"], g["y"]) for g in ggs]
    ll = batch_convert(xy)
    n = len(verts)
    out = {"system": "МСК-47 зона 3 (EPSG:6334703, polygon.top oracle), порядок входа Y X",
           "route_wgs84": [dict(v, lat=ll[i][0], lon=ll[i][1]) for i, v in enumerate(verts)],
           "ggs_wgs84": [dict(g, lat=ll[n + i][0], lon=ll[n + i][1]) for i, g in enumerate(ggs)]}
    fit = helmert_fit(xy, ll)
    out["helmert"] = fit
    json.dump(out, open("/home/z/my-project/base/results/lenobl_102_georef_5.49.json", "w",
                        encoding="utf8"), ensure_ascii=False, indent=1)
    print(f"вершин: {n}, ГГС: {len(ggs)}")
    for v in out["route_wgs84"][:4]:
        print(f"  {v['name']} ПК {v['pk']}: {v['lat']:.6f}, {v['lon']:.6f}")
    print("helmert residual:", fit["residual_m"])
    print("-> results/lenobl_102_georef_5.49.json")
