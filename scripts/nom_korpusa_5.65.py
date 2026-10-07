#!/usr/bin/env python3
"""5.65d: Nominatim plain-number queries with polygons -> корпус footprints."""
import json
import re
import time
import urllib.parse
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
OUT = "/home/z/my-project/repo/results"

hits = {}
for hn in ("86", "88"):
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
        {"q": f"Октябрьская набережная {hn}, Санкт-Петербург", "format": "jsonv2",
         "limit": 12, "polygon_geojson": 1, "polygon_threshold": 0.00002})
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        res = json.loads(r.read())
    print(f"=== дом {hn}: {len(res)} hits")
    for hit in res:
        dn = hit.get("display_name", "")
        m = re.search(r"к(\d+)", dn)
        korp = f"к{m.group(1)}" if m else "-"
        geo = hit.get("geojson", {})
        key = f"{hn}-{korp}"
        coords = geo.get("coordinates")
        if geo.get("type") == "MultiPolygon" and coords:
            coords = coords[0]
        hits[key] = {"lat": float(hit["lat"]), "lon": float(hit["lon"]),
                     "display": dn[:110], "type": geo.get("type"), "coords": coords}
        n = len(coords[0]) if (geo.get("type") == "Polygon" and coords) else -1
        print(f"  {key:6s} ({hit['lat']:.6f},{hit['lon']:.6f}) {geo.get('type'):10s} n={n}  {dn[:80]}")
    time.sleep(1.4)
json.dump(hits, open(f"{OUT}/nom_poly_korpusa_5.65.json", "w"), ensure_ascii=False)
print("saved nom_poly_korpusa_5.65.json")
