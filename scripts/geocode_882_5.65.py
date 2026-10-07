#!/usr/bin/env python3
"""5.65: Geocode Octyabrskaya nab. 88/2 (dormitory TEC-5) + OSM snapshot around it.
New anchor for plan 314154 re-registration (user-provided address)."""
import json
import math
import time
import urllib.parse
import urllib.request

UA = {"User-Agent": "spb-arcticdem-pipeline/5.65 (flood mapping research)"}
OUT = "/home/z/my-project/repo/results"

# --- 1) Nominatim search: structured query for 88/2 ---
def nominatim(q):
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
        {"q": q, "format": "json", "limit": 8, "addressdetails": 1})
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())

queries = [
    "Санкт-Петербург, Октябрьская набережная, 88/2",
    "Октябрьская набережная 88 корпус 2 Санкт-Петербург",
    "Октябрьская набережная 88 Санкт-Петербург",
]
nom_res = {}
for q in queries:
    try:
        res = nominatim(q)
        nom_res[q] = [
            {k: r.get(k) for k in ("lat", "lon", "display_name", "type", "class")}
            for r in res[:5]]
        print(f"NOMINATIM '{q}': {len(res)} hits")
        for r in nom_res[q][:3]:
            print(f"   ({r['lat']}, {r['lon']}) {r['display_name'][:110]}")
    except Exception as e:
        print(f"NOMINATIM '{q}' FAILED: {e}")
    time.sleep(1.2)

json.dump(nom_res, open(f"{OUT}/georef_314154_nominatim_882_5.65.json", "w"),
          ensure_ascii=False, indent=1)

# --- 2) Overpass: addr: nodes around Oktyabrskaya nab 86k2 (ref point from 5.53) ---
REF = (59.8723521, 30.4691565)
Q = """[out:json][timeout:90];
(
  node["addr:housenumber"]["addr:street"~"Октябрьская наб"](around:2500,{lat},{lon});
  way["building"]["addr:housenumber"]["addr:street"~"Октябрьская наб"](around:2500,{lat},{lon});
  way["building"]["addr:housenumber"]["addr:street"~"Новосаратовская"](around:2500,{lat},{lon});
  way["building"]["addr:housenumber"]["addr:street"~"Зольная"](around:2500,{lat},{lon});
  way["building"]["addr:housenumber"]["addr:street"~"Паткановская"](around:2500,{lat},{lon});
);
out body center;"""
url = "https://overpass.kumi.systems/api/interpreter"
data = urllib.parse.urlencode({"data": Q.format(lat=REF[0], lon=REF[1])}).encode()
req = urllib.request.Request(url, data=data, headers=UA)
with urllib.request.urlopen(req, timeout=120) as r:
    js = json.loads(r.read())

lat0, lon0 = REF
kx = 111320 * math.cos(math.radians(lat0))
rows = []
for e in js.get("elements", []):
    t = e.get("tags", {})
    hn = t.get("addr:housenumber")
    if not hn:
        continue
    if e["type"] == "node":
        lat, lon = e["lat"], e["lon"]
    else:
        lat, lon = e.get("center", {}).get("lat"), e.get("center", {}).get("lon")
        if lat is None:
            continue
    wy = (lat - lat0) * 111320
    wx = (lon - lon0) * kx
    rows.append({"street": t.get("addr:street", "?"), "housenumber": hn,
                 "lat": lat, "lon": lon, "wx": round(wx, 1), "wy": round(wy, 1),
                 "building": t.get("building", ""), "name": t.get("name", "")})
rows.sort(key=lambda r: (r["street"], r["housenumber"]))
print(f"\nOVERPASS addr elements: {len(rows)}")
for r in rows:
    print(f"  {r['street'][:26]:28s} {r['housenumber']:8s} wx={r['wx']:7.1f} wy={r['wy']:7.1f}"
          f"  bld={r['building'][:14]} {r['name'][:24]}")
json.dump(rows, open(f"{OUT}/georef_314154_addr_nodes_5.65.json", "w"),
          ensure_ascii=False, indent=1)
print("\nsaved: georef_314154_nominatim_882_5.65.json, georef_314154_addr_nodes_5.65.json")
