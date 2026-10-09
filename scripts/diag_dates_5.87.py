#!/usr/bin/env python3
"""5.87 — Дата-коррелированная диагностика «модель ↔ ICESat-2» (триплет оптики).

Для топ-кластеров худших расхождений (worst_points_5.86.json → icesat_worst):
  Панель A — оптика ≈ дате ДАННЫХ МОДЕЛИ:
             H2_hybrid-зоны: дата стрипа ArcticDEM (leaf-off, PGC STAC);
             prior-зоны: эпоха приора GLO-30/FABDEM (композит ~2011–2021,
             снимок-представитель). Sentinel-2 TCI, 10 м.
  Панель B — оптика на дату СЪЁМКИ ICESat-2 (ближайшая безоблачная S2).
  Панель C — текущее состояние: ESRI World Imagery (Vivid, ~0.3 м) + identify.
  Панель D — поверхность модели 5.84 (hillshade) + dH точек (dH = ice − модель).

ICESat-2: демп 5.86e (scripts/dem_cache/atl06_tiles/*.csv, сегменты 40м/20м,
h_baltic = h_mean − 16.04). Стрипы: PGC STAC arcticdem-strips-s2s041-2m,
leaf-off Nov–Apr, cloud ≤ 20, valid ≥ 50.
Выход: download/diag_5.87/<slug>_dates.png, results/diag_dates_5.87.json
Резюмируемо: существующие PNG пропускаются (resume), дедуп кластеров <300 м.
"""
import numpy as np
import pandas as pd
import rasterio, json, os, io, math, time, glob
from rasterio.windows import from_bounds, Window
from rasterio.warp import transform_bounds
from shapely.geometry import shape, Point
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
for _f in ("/usr/share/fonts/truetype/chinese/NotoSansSC-Regular.ttf",
           "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
    try: fm.fontManager.addfont(_f)
    except Exception: pass
plt.rcParams["font.sans-serif"] = ["Noto Sans SC", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False
from PIL import Image
import requests

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEM = f"{R}/download/mosaic_5.84/spb_hybrid_2m_mosaic_5.84.tif"
QUAL = f"{R}/download/mosaic_5.84/spb_quality_5.84.tif"
TILES = f"{R}/scripts/dem_cache/atl06_tiles"
CACHE = f"{R}/scripts/dem_cache"
OUT = f"{R}/download/diag_5.87"
RES = f"{R}/results"
os.makedirs(OUT, exist_ok=True)
os.makedirs(f"{CACHE}/imagery", exist_ok=True)
os.makedirs(f"{CACHE}/s2_5.87", exist_ok=True)

DELTA_BALTIC = 16.04
MAX_SHEETS = int(os.environ.get("MAX_SHEETS", "10"))
MONTHS_LEAF_OFF = {11, 12, 1, 2, 3, 4}
CLOUD_MAX = 20.0
VALID_MIN = 50.0
PAD = 1.2e-3  # ~130 м запас вокруг bbox кластера

S = requests.Session()
S.headers["User-Agent"] = "spb-dem-qa/5.87"

ds = rasterio.open(DEM)
dsq = rasterio.open(QUAL) if os.path.exists(QUAL) else None
T, Tq = ds.transform, (dsq.transform if dsq else None)


def sample_pts(lats, lons, src, T_, band=1, nodata=None, scale=1.0):
    vals = np.full(len(lats), np.nan)
    lon = np.asarray(lons, float); lat = np.asarray(lats, float)
    cols = np.floor((lon - T_.c) / T_.a).astype(int)
    rows = np.floor((lat - T_.f) / T_.e).astype(int)
    H, W = src.height, src.width
    inside = (rows >= 0) & (rows < H) & (cols >= 0) & (cols < W)
    if inside.any():
        rr, cc = rows[inside], cols[inside]
        win = Window(int(cc.min()), int(rr.min()),
                     int(cc.max() - cc.min() + 1), int(rr.max() - rr.min() + 1))
        arr = src.read(band, window=win)
        v = arr[rr - rr.min(), cc - cc.min()]
        good = np.ones_like(v, bool) if nodata is None else (v != nodata)
        vals[inside] = np.where(good, v.astype(float) * scale, np.nan)
    return vals


def load_ice():
    fps = sorted(glob.glob(f"{TILES}/tile_*.csv"))
    df = pd.concat([pd.read_csv(f) for f in fps], ignore_index=True)
    df["date"] = pd.to_datetime(df["time_ns"])
    return df


# ---------------- зоны ----------------
def load_zones():
    z = pd.read_csv(f"{RES}/zones_summary_5.83.csv")
    zones = []
    for _, r in z.iterrows():
        b = [float(x) for x in r.bbox.split()]
        zones.append(dict(tag=r.tag, bbox=b,
                          product=str(r["product"]), n_strips=int(r["n_strips"])))
    return zones


def zone_of(lat, lon, zones):
    for z in zones:
        w, s, e, n = z["bbox"]
        if w <= lon <= e and s <= lat <= n:
            return z
    return None


# ---------------- PGC STAC: даты стрипов ----------------
def pgc_strips(bbox):
    fp = f"{CACHE}/pgc_5.87_{bbox[0]:.4f}_{bbox[1]:.4f}_{bbox[2]:.4f}_{bbox[3]:.4f}.json"
    if os.path.exists(fp):
        return json.load(open(fp))
    try:
        r = S.post("https://stac.pgc.umn.edu/api/v1/search",
                   json=dict(collections=["arcticdem-strips-s2s041-2m"],
                             bbox=list(bbox), limit=200), timeout=40)
        items = r.json().get("features", [])
        def _pct(v):
            return None if v is None else round(float(v) * (100.0 if float(v) <= 1.0 else 1.0), 1)
        rows = []
        for it in items:
            p = it["properties"]
            dt = (p.get("datetime") or "")[:10]
            rows.append(dict(date=dt,
                             sensor=(p.get("pgc:sensor") or p.get("platform") or "?")[:8],
                             pairname=(p.get("pgc:pairname") or "")[:36],
                             cloud=_pct(p.get("pgc:cloud_area_percent")),
                             valid=_pct(p.get("pgc:valid_area_percent"))))
        rows = [r0 for r0 in rows if r0["date"]]
        json.dump(rows, open(fp, "w"))
        return rows
    except Exception as e:
        print("  PGC FAIL:", str(e)[:120])
        return []


def strips_leaf_off(rows):
    out = []
    for r0 in rows:
        try:
            m = int(r0["date"].split("-")[1])
        except Exception:
            continue
        if m not in MONTHS_LEAF_OFF:
            continue
        if r0["cloud"] is not None and r0["cloud"] > CLOUD_MAX:
            continue
        if r0["valid"] is not None and r0["valid"] < VALID_MIN:
            continue
        out.append(r0)
    out.sort(key=lambda r0: r0["date"])
    return out


# ---------------- Sentinel-2 (Earth Search) ----------------
def s2_pick(bbox, center, target, window_days=60, cloud_lt=30):
    d0 = (pd.Timestamp(target) - pd.Timedelta(days=window_days)).date()
    d1 = (pd.Timestamp(target) + pd.Timedelta(days=window_days)).date()
    dtw = f"{d0}T00:00:00Z/{d1}T00:00:00Z"
    try:
        r = S.post("https://earth-search.aws.element84.com/v1/search",
                   json=dict(collections=["sentinel-2-l2a"], bbox=[float(x) for x in bbox],
                             datetime=dtw, limit=40,
                             query={"eo:cloud_cover": {"lt": cloud_lt}}), timeout=40)
        feats = r.json().get("features", [])
    except Exception as e:
        print("  S2 search FAIL:", str(e)[:120])
        return None
    pt = Point(*center)
    cands = []
    for it in feats:
        if not shape(it["geometry"]).contains(pt):
            continue
        d = pd.Timestamp(it["properties"]["datetime"][:10])
        cl = float(it["properties"].get("eo:cloud_cover") or 0)
        cands.append((abs((d - pd.Timestamp(target)).days) + cl / 10.0, d, cl, it))
    if not cands:
        return None
    cands.sort(key=lambda x: x[0])
    _, d, cl, it = cands[0]
    return dict(date=str(d.date()), cloud=round(cl, 1), id=it["id"],
                href=it["assets"]["visual"]["href"])


def s2_read(scene, bbox, out_px=420):
    fp = f"{CACHE}/s2_5.87/{scene['id']}_{bbox[0]:.4f}_{bbox[1]:.4f}_{bbox[2]:.4f}_{bbox[3]:.4f}.npz"
    if os.path.exists(fp):
        return np.load(fp)["rgb"]
    try:
        with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
                          GDAL_HTTP_TIMEOUT="60"):
            with rasterio.open(scene["href"]) as src:
                b = transform_bounds("EPSG:4326", src.crs, *bbox)
                w = from_bounds(*b, src.transform)
                arr = src.read([1, 2, 3], window=w, out_shape=(3, out_px, out_px))
        rgb = np.moveaxis(arr, 0, -1).astype(np.uint8)
        np.savez_compressed(fp, rgb=rgb)
        return rgb
    except Exception as e:
        print("  S2 read FAIL:", str(e)[:140])
        return None


# ---------------- ESRI текущее состояние ----------------
def ll2tile(lat, lon, z):
    n = 2 ** z
    x = (lon + 180.0) / 360.0 * n
    y = (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n
    return x, y


def esri_tile(z, x, y):
    p = f"{CACHE}/imagery/esri_{z}_{x}_{y}.png"
    if os.path.exists(p):
        return Image.open(p).convert("RGB")
    url = (f"https://server.arcgisonline.com/ArcGIS/rest/services/"
           f"World_Imagery/MapServer/tile/{z}/{y}/{x}")
    for att in range(3):
        try:
            r = S.get(url, timeout=20)
            if r.status_code == 200 and len(r.content) > 100:
                Image.open(io.BytesIO(r.content)).convert("RGB").save(p)
                return Image.open(p).convert("RGB")
        except Exception:
            pass
        time.sleep(0.5 * (att + 1))
    return None


def esri_crop(bbox, z=18):
    x0, y1f = ll2tile(bbox[1], bbox[0], z)
    x1, y0f = ll2tile(bbox[3], bbox[2], z)
    tx0, tx1 = int(math.floor(x0)), int(math.floor(x1))
    ty0, ty1 = int(math.floor(y0f)), int(math.floor(y1f))
    W = (tx1 - tx0 + 1) * 256; H = (ty1 - ty0 + 1) * 256
    if W > 8000 or H > 8000:
        return None
    canvas = Image.new("RGB", (W, H), (28, 28, 28))
    for tx in range(tx0, tx1 + 1):
        for ty in range(ty0, ty1 + 1):
            t = esri_tile(z, tx, ty)
            if t is not None:
                canvas.paste(t, ((tx - tx0) * 256, (ty - ty0) * 256))
    px0 = int((x0 - tx0) * 256); px1 = int((x1 - tx0) * 256)
    py0 = int((y0f - ty0) * 256); py1 = int((y1f - ty0) * 256)
    return canvas.crop((max(px0, 0), max(py0, 0), min(px1, W), min(py1, H)))


def esri_meta(lat, lon):
    half = 0.002
    try:
        r = S.get(
            "https://server.arcgisonline.com/ArcGIS/rest/services/"
            "World_Imagery/MapServer/identify",
            params=dict(f="json", tolerance=2,
                        geometry=json.dumps(dict(x=lon, y=lat,
                                                 spatialReference=dict(wkid=4326))),
                        geometryType="esriGeometryPoint",
                        sr=4326, layers="all",
                        mapExtent=f"{lon-half},{lat-half},{lon+half},{lat+half}",
                        imageDisplay="600,600,96", returnGeometry="false"),
            timeout=25)
        for it in r.json().get("results", []):
            a = it.get("attributes", {})
            d = a.get("DATE (YYYYMMDD)") or a.get("SRC_DATE") or a.get("SRC_DATE2")
            if d and str(d).strip() not in ("", "Null"):
                ds0 = str(d).strip()
                if ds0.isdigit() and len(ds0) == 8:
                    ds0 = f"{ds0[:4]}-{ds0[4:6]}-{ds0[6:]}"
                return dict(date=ds0,
                            res=a.get("RESOLUTION (M)") or a.get("SRC_RES"),
                            src=(a.get("SOURCE_INFO") or a.get("DESCRIPTION") or "")[:28])
    except Exception:
        pass
    return None


# ---------------- модель: hillshade + точки ----------------
def model_crop(bbox):
    try:
        w = from_bounds(bbox[0], bbox[1], bbox[2], bbox[3], T)
        r0 = int(max(math.floor(w.row_off), 0))
        c0 = int(max(math.floor(w.col_off), 0))
        r1 = int(min(math.ceil(w.row_off + w.height), ds.height))
        c1 = int(min(math.ceil(w.col_off + w.width), ds.width))
        if r1 - r0 < 4 or c1 - c0 < 4:
            return None, None
        arr = ds.read(1, window=Window(c0, r0, c1 - c0, r1 - r0)).astype(float)
        arr[arr <= -32000] = np.nan
        arr *= 0.01
        fill = np.nanmean(arr) if np.isfinite(arr).any() else 0.0
        gy, gx = np.gradient(np.nan_to_num(arr, nan=fill), 2.0, 2.0)
        slope = np.arctan(np.hypot(gx, gy))
        aspect = np.arctan2(-gx, gy)
        az, alt = math.radians(315), math.radians(45)
        hs = np.sin(alt) * np.cos(slope) + np.cos(alt) * np.cos(az - aspect) * np.sin(slope)
        return hs, arr
    except Exception as e:
        print("  model_crop FAIL:", str(e)[:120])
        return None, None


# ---------------- лист ----------------
def fmt(x, nd=0):
    return "—" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x:.{nd}f}"


def sheet(cl, ice, strips, s2_model, s2_ice, esri_img, esri_m, verdicts, fp_png):
    bbox = cl["bbox"]; sl = cl["slug"]
    hs, arr = model_crop(bbox)
    fig, axes = plt.subplots(2, 2, figsize=(13.5, 11.5), constrained_layout=True)
    fig.suptitle(f"{sl} | sev {cl['sev']:.2f} | med dH {cl['med_dH']:+.2f} м "
                 f"({cl['n']} тчк) | зона {cl['zone']} ({cl['product']})",
                 fontsize=12.5, fontweight="bold")
    a = axes[0, 0]
    if s2_model and s2_model.get("rgb") is not None:
        a.imshow(s2_model["rgb"])
        a.set_title(f"A. Данные модели: {s2_model['label']}\n"
                    f"S2 {s2_model['date']}, обл. {s2_model['cloud']}%", fontsize=10)
    else:
        a.text(.5, .5, "нет оптики на дату модели", ha="center", va="center",
               transform=a.transAxes); a.set_title("A. Данные модели", fontsize=10)
    b = axes[0, 1]
    if s2_ice and s2_ice.get("rgb") is not None:
        b.imshow(s2_ice["rgb"])
        b.set_title(f"B. Худшая экспозиция ICESat-2: {ice['worst_date']} "
                    f"(med {ice['worst_med']:+.2f})\n"
                    f"S2 {s2_ice['date']}, обл. {s2_ice['cloud']}%", fontsize=10)
    else:
        b.text(.5, .5, "нет S2 около дат ICESat", ha="center", va="center",
               transform=b.transAxes); b.set_title("B. Дата ICESat-2", fontsize=10)
    c = axes[1, 0]
    if esri_img is not None:
        c.imshow(esri_img)
        md = f", {esri_m['date']}" if esri_m else ""
        c.set_title(f"C. Текущее состояние (ESRI ~0.3м{md})", fontsize=10)
    else:
        c.text(.5, .5, "нет ESRI-тайлов", ha="center", va="center",
               transform=c.transAxes); c.set_title("C. Текущее состояние", fontsize=10)
    d = axes[1, 1]
    if hs is not None:
        d.imshow(hs, cmap="gray", extent=[bbox[0], bbox[2], bbox[1], bbox[3]],
                 aspect="auto")
        if len(ice["df"]):
            sc = d.scatter(ice["df"].lon, ice["df"].lat, c=ice["df"].dH,
                           cmap="RdBu_r", vmin=-2.5, vmax=2.5, s=30,
                           edgecolors="k", linewidths=.4)
            plt.colorbar(sc, ax=d, shrink=.75, label="dH = ice − модель, м")
        d.set_title("D. Модель 5.84 (hillshade) + dH точек", fontsize=10)
        d.tick_params(labelsize=7)
    else:
        d.text(.5, .5, "модель: окно вне мозаики", ha="center", va="center",
               transform=d.transAxes); d.set_title("D. Модель 5.84", fontsize=10)
    for ax in (a, b, c):
        ax.set_xticks([]); ax.set_yticks([])
    facts = [
        f"канопи30 (медиана): {fmt(ice['canopy_med'])} см | σ стрипа: "
        f"{fmt(ice['sigma_med'])} см | trust-покрытие есть",
        f"стрипы ArcticDEM (leaf-off, обл≤{CLOUD_MAX:.0f}%, valid≥{VALID_MIN:.0f}%): " +
        ("; ".join(f"{s0['sensor']} {s0['date']} обл {fmt(s0['cloud'])}%"
                   for s0 in strips[:5]) or "после фильтра нет") +
        f" (всего стрипов в bbox: {ice['n_strips_all']})",
        f"ICESat-2 (bbox-контекст): {ice['n']} тчк, даты {ice['dmin']}…{ice['dmax']}, "
        f"{len(ice['by_date'])} экспозиций; med dH {ice['med']:+.2f} м, MAD {ice['mad']:.2f} м "
        f"(кластер 5.86: n={cl['n']}, med {cl['med_dH']:+.2f}); по датам: " +
        ", ".join(f"{k0}:{v0[0]}т/{v0[1]:+.1f}" for k0, v0 in list(ice['by_date'].items())[:5]),
        "S2 модель: " + (f"{s2_model['id']} {s2_model['date']}" if s2_model else "—") +
        " | S2 ice: " + (f"{s2_ice['id']} {s2_ice['date']}" if s2_ice else "—"),
        "признаки: " + ("; ".join(verdicts) if verdicts else "нет однозначных"),
    ]
    fig.text(.5, .004, "\n".join(facts), ha="center", va="bottom", fontsize=8.3,
             bbox=dict(boxstyle="round,pad=0.45", fc="#f5f5ee", ec="#999"))
    fig.savefig(fp_png, dpi=140)
    plt.close(fig)


# ---------------- вердикты ----------------
def verdicts_of(cl, ice, strips, s2_model):
    v = []
    if ice["canopy_med"] and ice["canopy_med"] > 50:
        v.append("CANOPY(>50см)")
    if ice["sigma_med"] and ice["sigma_med"] > 30:
        v.append("SIGMA(>30см)")
    if ice["n"] and ice["mad"] < 0.35 and abs(ice["med"]) > 0.5:
        v.append("СДВИГ(однородный,MAD<0.35)")
    months = set(pd.to_datetime(list(ice["by_date"].keys())).month)
    if (months & MONTHS_LEAF_OFF) and (months - MONTHS_LEAF_OFF):
        v.append("СЕЗОННОСТЬ(leaf-on+off)")
    if cl["product"].startswith("H2_hybrid") and strips:
        sd = pd.Timestamp(strips[-1]["date"])
        idt = pd.Timestamp(ice["dmax"]) if ice["dmax"] else None
        if idt is not None and (idt - sd).days > 180:
            v.append(f"РАЗРЫВ ДАТ(стрип→ice {(idt - sd).days} дн)")
    if cl["product"].startswith("prior") and ice["med"] < -0.5:
        v.append("ПРИОР: эпоха GLO-30 ~2011-2021 vs ice 2019+")
    return v


# ================= main =================
def main():
    print("== 5.87 дата-коррелированная диагностика ==")
    wp = json.load(open(f"{RES}/worst_points_5.86.json"))
    zones = load_zones()
    ice_all = load_ice()
    print("демп ATL06:", len(ice_all), "сегментов")

    fp_json = f"{RES}/diag_dates_5.87.json"
    out_json = json.load(open(fp_json)) if os.path.exists(fp_json) else []
    done_slugs = {r["slug"] for r in out_json}
    made = len(done_slugs)
    cand = sorted(wp["icesat_worst"], key=lambda c: -c["sev"])
    seen_centers = []
    for cl in cand:
        if made >= MAX_SHEETS:
            break
        lat, lon = cl["lat"], cl["lon"]
        if any(abs(la - lat) * 111 < 0.3 and abs(lo - lon) * 55.7 < 0.3
               for la, lo in seen_centers):
            continue
        bbox = [cl["bbox"][0] - PAD, cl["bbox"][1] - PAD,
                cl["bbox"][2] + PAD, cl["bbox"][3] + PAD]
        slug = f"ice_{lat:.4f}_{lon:.4f}"
        if slug in done_slugs:
            continue
        fp_png = f"{OUT}/{slug}_dates.png"
        t0 = time.time()
        z = zone_of(lat, lon, zones)
        product = z["product"] if z else "?"
        zonetag = z["tag"] if z else "?"
        m = ((ice_all.lat >= bbox[1]) & (ice_all.lat <= bbox[3]) &
             (ice_all.lon >= bbox[0]) & (ice_all.lon <= bbox[2]))
        df = ice_all[m].copy()
        if len(df) == 0:
            print(f"[{slug}] нет ATL06-сегментов — пропуск")
            continue
        df["h_baltic"] = df["h_mean"] - DELTA_BALTIC
        df["h_model"] = sample_pts(df.lat.values, df.lon.values, ds, T,
                                   1, nodata=-32768, scale=0.01)
        if dsq is not None:
            df["sigma"] = sample_pts(df.lat.values, df.lon.values, dsq, Tq, 2,
                                     nodata=65535)
            df["canopy"] = sample_pts(df.lat.values, df.lon.values, dsq, Tq, 3,
                                      nodata=65535)
        else:
            df["sigma"] = np.nan; df["canopy"] = np.nan
        df["dH"] = df["h_baltic"] - df["h_model"]
        df = df[df.h_model.notna() & df.dH.notna()]
        if len(df) == 0:
            print(f"[{slug}] точки вне мозаики — пропуск")
            continue
        gdate = df.groupby(df.date.dt.date).dH
        by_date_n = gdate.size().sort_index()
        by_date_med = gdate.median()
        worst_date = (by_date_med.abs().sort_values(ascending=False).index[0]
                      if len(by_date_med) else df.date.max().date())
        ice = dict(n=int(len(df)), med=round(float(df.dH.median()), 3),
                   mad=round(float((df.dH - df.dH.median()).abs().median()), 3),
                   dmin=str(df.date.min().date()), dmax=str(df.date.max().date()),
                   worst_date=str(worst_date),
                   worst_med=round(float(by_date_med.loc[worst_date]), 3),
                   by_date={str(k): [int(v), round(float(by_date_med.loc[k]), 2)]
                            for k, v in by_date_n.items()},
                   sigma_med=float(df.sigma.median()) if df.sigma.notna().any() else None,
                   canopy_med=float(df.canopy.median()) if df.canopy.notna().any() else None,
                   df=df.reset_index(drop=True))
        strips_all = pgc_strips([cl["bbox"][0], cl["bbox"][1], cl["bbox"][2], cl["bbox"][3]])
        strips = strips_leaf_off(strips_all)
        ice["n_strips_all"] = len(strips_all)
        cl_dict = dict(slug=slug, lat=lat, lon=lon, bbox=bbox, zone=zonetag,
                       product=product, n=cl["n"], sev=cl["sev"],
                       med_dH=cl["med_dH"])
        s2_model = None
        if product.startswith("H2_hybrid") and strips:
            tgt = strips[-1]["date"]
            s2_model = s2_pick(bbox, (lon, lat), tgt)
            lab = f"стрип {tgt}"
        else:
            tgt = "2021-01-15"
            s2_model = s2_pick(bbox, (lon, lat), tgt, window_days=120)
            lab = "эпоха приора ~2021 (GLO-30 композит)"
        if s2_model:
            s2_model["rgb"] = s2_read(s2_model, bbox)
            s2_model["label"] = lab
        s2_ice = None
        if ice.get("worst_date"):
            # снимок на ДАТУ ХУДШЕЙ экспозиции (та, что формирует med кластера)
            s2_ice = s2_pick(bbox, (lon, lat), ice["worst_date"])
            if s2_ice:
                s2_ice["rgb"] = s2_read(s2_ice, bbox)
        esri_img = esri_crop(bbox)
        esri_m = esri_meta(lat, lon)
        vd = verdicts_of(cl_dict, ice, strips, s2_model)
        sheet(cl_dict, ice, strips, s2_model, s2_ice, esri_img, esri_m, vd, fp_png)
        seen_centers.append((lat, lon))
        made += 1
        out_json.append(dict(
            cl_dict,
            ice=dict(n=ice["n"], med=ice["med"], mad=ice["mad"], dmin=ice["dmin"],
                     dmax=ice["dmax"], worst_date=ice["worst_date"],
                     worst_med=ice["worst_med"], by_date=ice["by_date"],
                     sigma_med=ice["sigma_med"], canopy_med=ice["canopy_med"]),
            strips=strips, n_strips_all=len(strips_all),
            s2_model=dict(id=s2_model["id"], date=s2_model["date"],
                          cloud=s2_model["cloud"]) if s2_model else None,
            s2_ice=dict(id=s2_ice["id"], date=s2_ice["date"],
                        cloud=s2_ice["cloud"]) if s2_ice else None,
            esri=esri_m, verdicts=vd, png=fp_png))
        json.dump(out_json, open(fp_json, "w"), ensure_ascii=False, indent=1)
        print(f"[ok] {slug} ({time.time()-t0:.0f}s) med {ice['med']:+.2f} "
              f"n={ice['n']} | {', '.join(vd) or '—'}")
    print(f"готово: {made} листов → {OUT}")


if __name__ == "__main__":
    main()
