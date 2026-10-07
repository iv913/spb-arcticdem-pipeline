#!/usr/bin/env python3
"""5.65c: Building polygons via Nominatim (polygon_geojson) + Esri imagery + plan fetch."""
import io
import json
import math
import time
import urllib.parse
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
OUT = "/home/z/my-project/repo/results"

# --- 1) Nominatim with polygons for the address cluster ---
targets = [
    "Октябрьская набережная 86 корпус 1, Санкт-Петербург",
    "Октябрьская набережная 86 корпус 2, Санкт-Петербург",
    "Октябрьская набережная 86 корпус 3, Санкт-Петербург",
    "Октябрьская набережная 88 корпус 2, Санкт-Петербург",
    "Октябрьская набережная 88 корпус 4, Санкт-Петербург",
    "Октябрьская набережная 88 корпус 5, Санкт-Петербург",
    "Октябрьская набережная 90, Санкт-Петербург",
    "Паткановская улица 5, Санкт-Петербург",
]
polys = {}
for q in targets:
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
        {"q": q, "format": "jsonv2", "limit": 2, "polygon_geojson": 1,
         "polygon_threshold": 0.00002})
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=60) as r:
            res = json.loads(r.read())
        if res:
            hit = res[0]
            geo = hit.get("geojson", {})
            polys[q] = {"lat": float(hit["lat"]), "lon": float(hit["lon"]),
                        "display": hit.get("display_name", "")[:90],
                        "type": hit.get("type"), "geojson_type": geo.get("type"),
                        "coords": geo.get("coordinates")}
            n = len(geo.get("coordinates", [[]])[0]) if geo.get("type") == "Polygon" else -1
            print(f"OK  {q[:52]:54s} ({hit['lat']},{hit['lon']}) {geo.get('type')} n={n}")
        else:
            print(f"MISS {q}")
    except Exception as e:
        print(f"FAIL {q}: {str(e)[:70]}")
    time.sleep(1.3)
json.dump(polys, open(f"{OUT}/nom_poly_sosnovka_5.65.json", "w"), ensure_ascii=False)
print("saved nom_poly_sosnovka_5.65.json")

# --- 2) Esri imagery z17/z18 mosaic around block center (59.8721, 30.4715) ---
def ll2tile(lat, lon, z):
    n = 2 ** z
    x = (lon + 180.0) / 360.0 * n
    y = (1.0 - math.log(math.tan(math.radians(lat)) +
         1 / math.cos(math.radians(lat))) / math.pi) / 2.0 * n
    return x, y

def fetch_esri(lat0, lat1, lon0, lon1, z, fname):
    xt0, yt0 = ll2tile(lat1, lon0, z)   # top-left (lat1 > lat0)
    xt1, yt1 = ll2tile(lat0, lon1, z)
    x0, y0, x1, y1 = int(xt0), int(yt0), int(xt1), int(yt1)
    W, H = (x1 - x0 + 1) * 256, (y1 - y0 + 1) * 256
    try:
        from PIL import Image
    except ImportError:
        import subprocess; subprocess.run(["pip", "install", "-q", "pillow"])
        from PIL import Image
    mosaic = Image.new("RGB", (W, H))
    for tx in range(x0, x1 + 1):
        for ty in range(y0, y1 + 1):
            turl = (f"https://server.arcgisonline.com/ArcGIS/rest/services/"
                    f"World_Imagery/MapServer/tile/{z}/{ty}/{tx}")
            for attempt in range(3):
                try:
                    rq = urllib.request.Request(turl, headers=UA)
                    with urllib.request.urlopen(rq, timeout=30) as rr:
                        tile = Image.open(io.BytesIO(rr.read())).convert("RGB")
                    mosaic.paste(tile, ((tx - x0) * 256, (ty - y0) * 256))
                    break
                except Exception as e:
                    if attempt == 2:
                        print(f"  tile {z}/{ty}/{tx} FAIL {str(e)[:40]}")
            time.sleep(0.05)
    mosaic.save(f"{OUT}/{fname}")
    # world-file style metadata: pixel size + origin
    n = 2 ** z
    res_x = 360.0 / n / 256 * math.cos(math.radians((lat0 + lat1) / 2)) * 111320
    meta = {"z": z, "x0": x0, "y0": y0, "W": W, "H": H,
            "lat_top": lat1, "lat_bot": lat0, "lon_left": lon0, "lon_right": lon1,
            "m_per_px_x": res_x,
            "m_per_px_y": res_x,  # near-equivalent at this latitude
            "lat0_c": (lat0 + lat1) / 2, "lon0_c": (lon0 + lon1) / 2}
    json.dump(meta, open(f"{OUT}/{fname}.json", "w"))
    print(f"esri mosaic {fname}: {W}x{H}, ~{res_x:.2f} m/px")
    return meta

m17 = fetch_esri(59.8680, 59.8765, 30.4620, 30.4830, 17, "esri_sosnovka_z17_5.65.png")
m18 = fetch_esri(59.8705, 59.8742, 30.4660, 30.4760, 18, "esri_sosnovka_z18_5.65.png")

# --- 3) plan image with browser UA ---
try:
    req = urllib.request.Request("https://i.archi.ru/i/314154.jpg", headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        img = r.read()
    open(f"{OUT}/img_314154.jpg", "wb").write(img)
    print(f"plan downloaded: {len(img)} bytes")
except Exception as e:
    print("plan fetch FAIL:", str(e)[:80])
