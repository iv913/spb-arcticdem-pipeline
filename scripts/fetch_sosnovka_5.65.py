#!/usr/bin/env python3
"""5.65b: Overpass building footprints (Sosnovka block 86-88 Oktyabrskaya nab) + plan download."""
import json
import urllib.parse
import urllib.request

UA = {"User-Agent": "spb-arcticdem-pipeline/5.65 (flood mapping research)"}
OUT = "/home/z/my-project/repo/results"

# Block of interest: between 86k2 (59.87235,30.46916) and TEC-5 (59.8725,30.5032)
LAT, LON = 59.8721, 30.4715
Q = f"""[out:json][timeout:120];
(
  way["building"](around:700,{LAT},{LON});
  relation["building"](around:700,{LAT},{LON});
  way["highway"](around:700,{LAT},{LON});
);
out body geom;"""

ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
js = None
for ep in ENDPOINTS:
    try:
        data = urllib.parse.urlencode({"data": Q}).encode()
        req = urllib.request.Request(ep, data=data, headers=UA)
        with urllib.request.urlopen(req, timeout=150) as r:
            js = json.loads(r.read())
        print("OVERPASS OK:", ep)
        break
    except Exception as e:
        print("OVERPASS FAIL:", ep, str(e)[:100])

if js is None:
    raise SystemExit("all overpass endpoints failed")

els = js.get("elements", [])
blds, roads = [], []
for e in els:
    t = e.get("tags", {})
    geom = e.get("geometry") or []
    pts = [(g["lon"], g["lat"]) for g in geom if "lat" in g]
    if not pts:
        continue
    if t.get("building"):
        blds.append({"tags": {k: v for k, v in t.items() if k in (
            "building", "name", "addr:street", "addr:housenumber", "building:levels")},
            "poly": pts})
    elif t.get("highway"):
        roads.append({"highway": t.get("highway"), "name": t.get("name", ""),
                      "line": pts})
print(f"buildings: {len(blds)}, roads: {len(roads)}")
for b in blds:
    t = b["tags"]
    if t.get("addr:housenumber") or t.get("name"):
        lats = [p[1] for p in b["poly"]]; lons = [p[0] for p in b["poly"]]
        print(f"  BLD {t.get('addr:street','?')[:24]} {t.get('addr:housenumber',''):6s}"
              f" {t.get('name','')[:30]:32s} n={len(b['poly'])}"
              f" ctr=({sum(lats)/len(lats):.5f},{sum(lons)/len(lons):.5f})")
json.dump({"buildings": blds, "roads": roads},
          open(f"{OUT}/osm_sosnovka_5.65.json", "w"), ensure_ascii=False)
print("saved osm_sosnovka_5.65.json")

# --- plan download ---
try:
    req = urllib.request.Request("https://i.archi.ru/i/314154.jpg", headers=UA)
    with urllib.request.urlopen(req, timeout=90) as r:
        img = r.read()
    open("/home/z/my-project/repo/results/img_314154.jpg", "wb").write(img)
    print(f"plan 314154.jpg downloaded: {len(img)} bytes")
except Exception as e:
    print("plan download FAILED:", e)
