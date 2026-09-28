#!/usr/bin/env python3
"""5.47: выгрузка OSM-геометрии (API 0.6 map) для зон геопланов.

Для каждого плана: bbox вокруг координат проекта -> XML -> roads/water/
buildings -> results/osm_<pid>_5.47.json
"""
import json
import os
import re
import urllib.request
import xml.etree.ElementTree as ET

REPO = "/home/z/my-project/spb45"
OUT = os.path.join(REPO, "results")

PLANS = {
    "456577": {"proj": "19768", "lat": 59.921389, "lon": 30.287699, "r": 0.004},
    "314154": {"proj": "15315", "lat": 59.872340, "lon": 30.469802, "r": 0.005},
    "448114": {"proj": "19557", "lat": 59.864641, "lon": 30.502834, "r": 0.008},
    "422695": {"proj": "18834", "lat": 59.930571, "lon": 30.234707, "r": 0.006},
    "493731": {"proj": "20773", "lat": 59.850605, "lon": 30.304640, "r": 0.004},
    "257941": {"proj": "10234", "lat": 59.977440, "lon": 30.228290, "r": 0.005},
    "456638": {"proj": "19769", "lat": 59.931591, "lon": 30.378868, "r": 0.004},
    "317703": {"proj": "15443", "lat": 60.030858, "lon": 30.320734, "r": 0.005},
}


def fetch_osm(lat, lon, r):
    bbox = f"{lon - r},{lat - r * 0.6},{lon + r},{lat + r * 0.6}"
    url = ("https://api.openstreetmap.org/api/0.6/map?bbox=" + bbox)
    req = urllib.request.Request(url, headers={"User-Agent": "spb-dem/5.47"})
    xml = urllib.request.urlopen(req, timeout=120).read()
    root = ET.fromstring(xml)
    nodes = {n.get("id"): (float(n.get("lon")), float(n.get("lat")))
             for n in root.findall("node")}
    feats = {"roads": [], "water": [], "buildings": []}
    for w in root.findall("way"):
        refs = [nd.get("ref") for nd in w.findall("nd")]
        pts = [nodes[r] for r in refs if r in nodes]
        if len(pts) < 2:
            continue
        tags = {t.get("k"): t.get("v") for t in w.findall("tag")}
        name = tags.get("name", "")
        rec = {"name": name, "pts": pts}
        if "building" in tags:
            feats["buildings"].append(rec)
        elif "highway" in tags:
            rec["kind"] = tags.get("highway")
            feats["roads"].append(rec)
        elif tags.get("natural") in ("water", "bay") or "waterway" in tags or \
                tags.get("landuse") in ("basin",) or tags.get("water" ) is not None:
            feats["water"].append(rec)
    return feats


def main():
    for pid, p in PLANS.items():
        out = os.path.join(OUT, f"osm_{pid}_5.47.json")
        if os.path.exists(out):
            print(pid, "cached")
            continue
        try:
            feats = fetch_osm(p["lat"], p["lon"], p["r"])
            json.dump(feats, open(out, "w"))
            print(f"{pid}: roads={len(feats['roads'])} water={len(feats['water'])} "
                  f"bld={len(feats['buildings'])}")
        except Exception as e:
            print(f"{pid}: FAIL {str(e)[:80]}")


if __name__ == "__main__":
    main()
