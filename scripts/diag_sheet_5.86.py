#!/usr/bin/env python3
"""5.86c — Диагностические листы «спутник | поверхность модели | quality».

Для кластера (bbox ~500м):
  Панель A — последний оптический снимок ESRI World Imagery (z=18, ~0.3м/пикс),
             дата/разрешение через identify API; точки поверх.
  Панель B — поверхность 2м-мозаики 5.84: hillshade + гипсометрия; точки dH.
  Панель C — quality: canopy30 (зелёный) / sigma>30см (красный) / trust-контур.

Выход: download/diag_5.86/<slug>.png
Кэш тайлов: scripts/dem_cache/imagery/{z}/{x}_{y}.png (вне git).
"""
import numpy as np
import pandas as pd
import rasterio, json, os, io, math, time
import matplotlib
matplotlib.use("Agg")
import numpy as _np
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
TRUST = f"{R}/download/mosaic_5.84/trust_mask_2m_5.84.tif"
QUAL = f"{R}/download/mosaic_5.84/spb_quality_5.84.tif"
CACHE = f"{R}/scripts/dem_cache/imagery"
OUT = f"{R}/download/diag_5.86"
os.makedirs(CACHE, exist_ok=True)
os.makedirs(OUT, exist_ok=True)

ds = rasterio.open(DEM)
dstr = rasterio.open(TRUST)
dsq = rasterio.open(QUAL) if os.path.exists(QUAL) else None

S = requests.Session()
S.headers["User-Agent"] = "spb-dem-qa/5.86"


# ---------------- web mercator tiles ----------------
def ll2tile(lat, lon, z):
    n = 2 ** z
    x = (lon + 180.0) / 360.0 * n
    lr = math.radians(lat)
    y = (1.0 - math.asinh(math.tan(lr)) / math.pi) / 2.0 * n
    return x, y


def fetch_tile(z, x, y, provider="esri"):
    p = f"{CACHE}/{provider}_{z}_{x}_{y}.png"
    if os.path.exists(p):
        return Image.open(p).convert("RGB")
    if provider == "esri":
        url = f"https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
    else:
        url = f"https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}"
    for att in range(3):
        try:
            r = S.get(url, timeout=20)
            if r.status_code == 200 and len(r.content) > 100:
                im = Image.open(io.BytesIO(r.content)).convert("RGB")
                im.save(p)
                return im
        except Exception:
            pass
        time.sleep(0.6 * (att + 1))
    return None


def imagery_crop(lat0, lat1, lon0, lon1, z=18, provider="esri", min_px=900):
    """Сшить тайлы по bbox, вернуть PIL Image (RGB)."""
    x0, y1f = ll2tile(lat0, lon0, z)
    x1, y0f = ll2tile(lat1, lon1, z)
    tx0, tx1 = int(math.floor(x0)), int(math.floor(x1))
    ty0, ty1 = int(math.floor(y0f)), int(math.floor(y1f))
    W = (tx1 - tx0 + 1) * 256
    H = (ty1 - ty0 + 1) * 256
    if W > 12_000 or H > 12_000:
        z -= 1
        return imagery_crop(lat0, lat1, lon0, lon1, z, provider, min_px)
    canvas = Image.new("RGB", (W, H), (30, 30, 30))
    miss = 0
    for tx in range(tx0, tx1 + 1):
        for ty in range(ty0, ty1 + 1):
            t = fetch_tile(z, tx, ty, provider)
            if t is None:
                miss += 1
                continue
            canvas.paste(t, ((tx - tx0) * 256, (ty - ty0) * 256))
    # crop to bbox
    px0 = (x0 - tx0) * 256
    px1 = (x1 - tx0) * 256
    py0 = (y0f - ty0) * 256
    py1 = (y1f - ty0) * 256
    crop = canvas.crop((int(px0), int(py0), int(px1), int(py1)))
    if max(crop.size) < min_px and z > 16:
        return imagery_crop(lat0, lat1, lon0, lon1, z + 1, provider, min_px)
    return crop, miss, z


def imagery_meta(lat, lon):
    """Дата/разрешение последнего снимка ESRI в точке (identify API)."""
    try:
        d = 0.002
        ext = f"{lon-d},{lat-d},{lon+d},{lat+d}"
        geo = json.dumps({"x": lon, "y": lat, "spatialReference": {"wkid": 4326}})
        u = ("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/identify"
             f"?geometry={requests.utils.quote(geo)}&tolerance=1&mapExtent={ext}"
             "&imageDisplay=800,600,96&sr=4326&layers=all&f=json")
        j = S.get(u, timeout=20).json()
        r = j["results"][0]["attributes"]
        return dict(date=r.get("SRC_DATE2") or r.get("DATE (YYYYMMDD)"),
                    res=r.get("RESOLUTION (M)"), src=r.get("SOURCE_INFO"),
                    block=r.get("BlockName"))
    except Exception as e:
        return dict(err=str(e)[:80])


# ---------------- model surface ----------------
def dem_crop(lat0, lat1, lon0, lon1, pad_frac=0.06):
    b = ds.bounds
    # если bbox целиком вне мозаики — вернуть NaN-массив (панель «вне покрытия»)
    if (lon1 < b.left or lon0 > b.right or lat1 < b.bottom or lat0 > b.top):
        W = H = 200
        a = np.full((H, W), np.nan)
        q = np.full((3, H, W), np.nan) if dsq is not None else None
        tr = np.zeros((H, W), dtype=np.uint8)
        ext_b = [lon0, lon1, lat0, lat1]
        qext = ext_b
        return a, ext_b, tr, q, qext
    lon0b = max(lon0, b.left); lon1b = min(lon1, b.right)
    lat0b = max(lat0, b.bottom); lat1b = min(lat1, b.top)
    T = ds.transform
    c0 = int((lon0b - T.c) / T.a); c1 = int((lon1b - T.c) / T.a)
    r0 = int((lat1b - T.f) / T.e); r1 = int((lat0b - T.f) / T.e)
    cc0, rr0 = max(0, c0), max(0, r0)
    w = min(c1, ds.width) - cc0
    h = min(r1, ds.height) - rr0
    win = rasterio.windows.Window(cc0, rr0, max(w, 1), max(h, 1))
    a = ds.read(1, window=win).astype(float)
    a[a == -32768] = np.nan
    lon_left = T.c + win.col_off * T.a
    lat_top = T.f + win.row_off * T.e
    tr = dstr.read(1, window=win) if dstr.width > win.col_off + win.width else None
    q = None
    if dsq is not None:
        Tq = dsq.transform
        qc0 = int((lon0b - Tq.c) / Tq.a); qc1 = int((lon1b - Tq.c) / Tq.a)
        qr0 = int((lat1b - Tq.f) / Tq.e); qr1 = int((lat0b - Tq.f) / Tq.e)
        wq = rasterio.windows.Window(max(0, qc0), max(0, qr0), min(qc1, dsq.width) - max(0, qc0), min(qr1, dsq.height) - max(0, qr0))
        q = dsq.read(window=wq).astype(float)
        q[q == 65535] = np.nan
        q_left = Tq.c + wq.col_off * Tq.a
        q_top = Tq.f + wq.row_off * Tq.e
    else:
        q_left, q_top = lon_left, lat_top
    if q is not None:
        qext_ = [q_left, q_left + q.shape[2] * Tq.a, q_top + q.shape[1] * Tq.e, q_top]
    else:
        qext_ = [lon_left, lon_left + a.shape[1] * T.a, lat_top + a.shape[0] * T.e, lat_top]
    return a, [lon_left, lon_left + a.shape[1] * T.a, lat_top + a.shape[0] * T.e, lat_top], tr, q, qext_


def hillshade(a, az=315, alt=45, res_m=2.0):
    gy, gx = np.gradient(np.nan_to_num(a, nan=np.nanmean(a)), res_m)
    azr, altr = math.radians(az), math.radians(alt)
    slope = np.pi / 2 - np.arctan(np.hypot(gx, gy))
    aspect = np.arctan2(-gx, -gy)
    hs = np.sin(altr) * np.sin(slope) + np.cos(altr) * np.cos(slope) * np.cos(azr - aspect)
    return np.clip(hs, 0, 1)


def hypso(a):
    v = a - np.nanmin(a)
    m = np.nanmax(v) if np.nanmax(v) > 0 else 1
    v = v / m
    # мягкая палитра: низ голубовато-серый, верх песочно-коричневый
    import matplotlib.colors as mc
    cmap = plt.get_cmap("terrain")
    rgba = cmap(v * 0.7 + 0.15)
    rgba[..., 3] = 0.55
    return rgba


def to_px(lat, lon, lon_left, lat_top, res_deg_x, res_deg_y, W, H):
    x = (lon - lon_left) / res_deg_x
    y = (lat_top - lat) / (-res_deg_y)
    return x, y


def jitter_dup(cn, meters=14):
    """Совпадающие координаты (якорные отметки) слегка разнести, чтобы были видны."""
    import numpy as _np
    if cn is None or not len(cn):
        return cn
    c = cn.copy()
    key = c.lat.round(6).astype(str) + "_" + c.lon.round(6).astype(str)
    for k, idx in c.groupby(key).groups.items():
        j = _np.arange(len(idx))
        ang = j * 2.399963  # golden angle
        r = meters * _np.sqrt((j + 0.7) / len(idx))
        dlat = r * _np.cos(ang) / 111320.0
        dlon = r * _np.sin(ang) / (111320.0 * _np.cos(_np.radians(c.loc[idx[0], "lat"])))
        c.loc[idx, "lat"] = c.loc[idx, "lat"].values + dlat
        c.loc[idx, "lon"] = c.loc[idx, "lon"].values + dlon
    return c


# ---------------- sheet ----------------
def sheet(cluster, icesat_df, tag):
    lat, lon = cluster["lat"], cluster["lon"]
    half = cluster.get("half", 0.0028)  # ~310м по широте
    lat0, lat1 = lat - half, lat + half
    lon0, lon1 = lon - half / 0.55, lon + half / 0.55
    slug = cluster.get("slug") or f"c{tag}"
    sat, miss, z = imagery_crop(lat0, lat1, lon0, lon1, z=18)
    satg, missg, zg = imagery_crop(lat0, lat1, lon0, lon1, z=18, provider="google")
    meta = imagery_meta(lat, lon)
    a, ext_b, tr, q, qext = dem_crop(lat0, lat1, lon0, lon1)
    H, W = a.shape
    hs = hillshade(a, res_m=2.0)
    if tr is not None and tr.shape == a.shape:
        hs_untrust = tr.astype(bool)
    else:
        hs_untrust = None

    # quality composite
    if q is not None:
        canopy = q[2] / 100.0
        sigma = q[1] / 100.0
        ncov = q[0]
        qc_img = np.ones((q.shape[1], q.shape[2], 3)) * 0.92
        qc_img[ncov <= 1] = 0.75  # нет покрытия стрипами
        qc_img[canopy > 0.5] = [0.45, 0.75, 0.35]
        qc_img[sigma > 0.30] = [0.85, 0.30, 0.25]
        qc_img[(canopy > 0.5) & (sigma > 0.30)] = [0.75, 0.55, 0.15]
        trust = tr.astype(bool) if tr is not None else np.ones_like(a, bool)
    else:
        qc_img = None

    sub = icesat_df[((icesat_df.lat > lat0) & (icesat_df.lat < lat1) & (icesat_df.lon > lon0) & (icesat_df.lon < lon1))]
    fig, axes = plt.subplots(1, 3, figsize=(19.5, 6.9), constrained_layout=True)
    extent = [lon0, lon1, lat0, lat1]

    # A: satellite
    ax = axes[0]
    ax.imshow(sat, extent=[lon0, lon1, lat0, lat1])
    cnA = jitter_dup(cluster.get("_pts"))
    if cnA is not None and len(cnA):
        ax.scatter(cnA.lon, cnA.lat, c=cnA.dH2m, cmap="RdBu_r", vmin=-5, vmax=5,
                   s=14 + 6 * np.clip(cnA.dH2m.abs(), 0, 12), edgecolors="k", linewidths=0.5)
    ax.scatter(sub.lon, sub.lat, c=sub.dH2m, cmap="RdBu_r", vmin=-5, vmax=5,
               marker="^", s=9, edgecolors="none", alpha=0.85)
    capt = f"ESRI World Imagery z={z}" + (f", {meta.get('date')}" if meta.get("date") else "") + \
           (f", {meta.get('res')} м/пикс" if meta.get("res") else "") + \
           (f", {meta.get('src')}" if meta.get("src") else "")
    ax.set_title(f"A. Оптика (последний снимок)\n{capt}", fontsize=10)

    # B: model
    ax = axes[1]
    ax.imshow(hs, extent=ext_b, cmap="gray", vmin=0.15, vmax=1.0)
    rgb = hypso(a)
    ax.imshow(rgb, extent=ext_b)
    if hs_untrust is not None:
        import matplotlib.colors as _mc
        un = _np.zeros(a.shape + (4,))
        un[~hs_untrust] = [0, 0, 0, 0.30]  # затемнить untrusted (dens<6)
        ax.imshow(un, extent=ext_b)
    cn = cluster.get("_pts")
    cn = jitter_dup(cn)
    if cn is not None and len(cn):
        sc = ax.scatter(cn.lon, cn.lat, c=cn.dH2m, cmap="RdBu_r", vmin=-5, vmax=5,
                        s=14 + 6 * np.clip(cn.dH2m.abs(), 0, 12), edgecolors="k", linewidths=0.4)
    sc2 = ax.scatter(sub.lon, sub.lat, c=sub.dH2m, cmap="RdBu_r", vmin=-5, vmax=5,
                     marker="^", s=9, edgecolors="none", alpha=0.85)
    ax.set_title("B. Модель 2м (5.84): hillshade + гипсометрия\n"
                 "точки: ● проектные, ▲ ICESat-2 (цвет = dH −5…+5 м)", fontsize=10)

    # C: quality
    ax = axes[2]
    if qc_img is not None:
        ax.imshow(qc_img, extent=qext)
    else:
        ax.imshow(np.ones((50, 50, 3)) * 0.9, extent=extent)
        ax.text(0.5, 0.5, "quality_5.84.tif ещё грузится", transform=ax.transAxes, ha="center")
    ax.set_title("C. Quality: зелёный — canopy30>0.5 м,\nкрасный — sigma>30 см, серый — нет стрипов", fontsize=10)

    for ax in axes:
        ax.set_xlim(lon0, lon1); ax.set_ylim(lat0, lat1)
        ax.set_xlabel("lon", fontsize=8); ax.set_ylabel("lat", fontsize=8)
        ax.tick_params(labelsize=7)

    med = cluster.get("med_dH"); sev = cluster.get("sev"); n = cluster.get("n")
    name = cluster.get("name", slug)
    try:
        fig.suptitle(f"{name} — n={n}, med dH={med:+.2f} м, mean|dH|={sev} м, "
                     f"({lat:.4f}, {lon:.4f})", fontsize=13, fontweight="bold")
    except TypeError:
        fig.suptitle(f"{name} — n={n}, med dH={med} м, ({lat:.4f}, {lon:.4f})", fontsize=13, fontweight="bold")
    p = f"{OUT}/{slug}.png"
    fig.savefig(p, dpi=150)
    plt.close(fig)
    return dict(slug=slug, png=p, sat_miss=miss + missg, meta=meta, dem_crop_stats=dict(
        hmin=round(float(np.nanmin(a)), 1), hmax=round(float(np.nanmax(a)), 1)))


# ---------------- run ----------------
worst = json.load(open(f"{R}/results/worst_points_5.86.json"))
canon = pd.read_csv(f"{R}/scripts/data/canon2m_5.86.csv")
canon = canon[canon.dH2m.notna()]
icesat = pd.read_csv(f"{R}/scripts/data/icesat2_vs_mosaic_5.86.csv")
icesat = icesat[icesat.dH2m.notna()]

def attach(c, df):
    lat, lon = c["lat"], c["lon"]
    pts = df[((df.lat - lat).abs() < 0.003) & ((df.lon - lon).abs() < 0.005)]
    c["_pts"] = pts

sel = []
# канонные кластеры: топ по n*sev + обязательные аномалии
cn_sorted = sorted(worst["canon_worst"], key=lambda c: c["n"] * c["sev"], reverse=True)
for c in cn_sorted[:8]:
    c = dict(c); c["slug"] = f"canon_{c['lat']:.4f}_{c['lon']:.4f}"
    attach(c, canon); sel.append(c)
for a in worst.get("anomalies_566", []):
    if a.get("med_now") is not None or a["name"] in ("Кубинская", "Деловой центр (Моск.)"):
        c = dict(name=a["name"], lat=a["lat"], lon=a["lon"], n=a.get("n_near") or a["n"],
                 med_dH=a.get("med_now") or a["med_566"], sev=abs(a.get("med_now") or a["med_566"]),
                 slug="anom_" + a["name"].split()[0].lower().replace("№2", "2"))
        attach(c, canon); sel.append(c)
for c in worst["icesat_worst"][:6]:
    c = dict(c); c["slug"] = f"ice_{c['lat']:.4f}_{c['lon']:.4f}"
    c["name"] = f"ICESat-2 кластер"
    attach(c, icesat); sel.append(c)

# дедуп по близости
final, used = [], []
for c in sel:
    if all((abs(c["lat"] - u[0]) > 0.004) or (abs(c["lon"] - u[1]) > 0.008) for u in used):
        used.append((c["lat"], c["lon"]))
        final.append(c)

print(f"листов: {len(final)}")
manifest = []
for i, c in enumerate(final):
    try:
        r = sheet(c, icesat, i)
        r.update(name=c.get("name", c["slug"]), n=c["n"], med=c["med_dH"])
        manifest.append(r)
        print(f"  [{i+1}/{len(final)}] {c['slug']} ok (miss={r['sat_miss']})")
    except Exception as e:
        print(f"  [{i+1}/{len(final)}] {c['slug']} FAIL: {e}")
        import traceback; traceback.print_exc()
json.dump(manifest, open(f"{OUT}/manifest.json", "w"), ensure_ascii=False, indent=1)
print("saved", OUT)
