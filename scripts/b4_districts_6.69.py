#!/usr/bin/env python3
"""6.69/B4 — районы СПб из OSM PBF для разбивки пула v9 по районам.

Двухпроходное извлечение административных границ (boundary=administrative,
admin_level 4..9, с именем) из sankt_petersburg.osm.pbf (BBBike, 10.10.2026):

  проход 1: relations-границы + их member-way id + именованные way-границы;
  проход 2: координаты нужных ways (members обычно без тегов boundary);
  сборка колец (stitch по совпадению концов), клип bbox города, GeoJSON.

usage: python3 scripts/b4_districts_6.69.py
выход: results/validation_pool_v9/districts_osm.geojson
"""

import json
from pathlib import Path

import osmium
from shapely.geometry import Polygon, MultiPolygon
from shapely.ops import unary_union

PBF = '/tmp/my-project/data/osm_pbf/sankt_petersburg.osm.pbf'
OUT = Path('/tmp/my-project/gh-clone/results/validation_pool_v9')
OUT.mkdir(parents=True, exist_ok=True)

# bbox СПб (с Кронштадтом и Пулково)
CITY = (29.55, 59.70, 30.75, 60.15)
LEVELS = ('4', '5', '6', '7', '8', '9')


class RelPass(osmium.SimpleHandler):
    """Проход 1: границы-отношения и границы-way, члены отношений."""

    def __init__(self):
        super().__init__()
        self.rels = []           # (tags, [(way_id, role)])
        self.boundary_ways = {}  # id -> tags (только именованные границы)
        self.member_ids = set()

    def relation(self, r):
        t = r.tags
        if (t.get('boundary') == 'administrative'
                and t.get('admin_level') in LEVELS and t.get('name')):
            mem = [(m.ref, m.role) for m in r.members
                   if m.type == 'w' and m.role in ('outer', 'inner', '')]
            if mem:
                self.rels.append((dict(t), mem))
                self.member_ids.update(w for w, _ in mem)

    def way(self, w):
        t = w.tags
        if (t.get('boundary') == 'administrative'
                and t.get('admin_level') in LEVELS and t.get('name')):
            self.boundary_ways[w.id] = dict(t)


class WayPass(osmium.SimpleHandler):
    """Проход 2: координаты всех ways, что нужны (members + границы)."""

    def __init__(self, needed):
        super().__init__()
        self.needed = needed
        self.coords = {}

    def way(self, w):
        if w.id in self.needed:
            self.coords[w.id] = [(n.lon, n.lat) for n in w.nodes]


def stitch(segments):
    """Соединить обрывки в замкнутые кольца по совпадению концов."""
    segs = [list(s) for s in segments if len(s) >= 2]
    rings = []
    while segs:
        cur = segs.pop(0)
        changed = True
        while changed and cur[0] != cur[-1]:
            changed = False
            for i, s in enumerate(segs):
                if s[0] == cur[-1]:
                    cur += s[1:]; segs.pop(i); changed = True; break
                if s[-1] == cur[-1]:
                    cur += list(reversed(s))[1:]; segs.pop(i); changed = True; break
                if s[-1] == cur[0]:
                    cur = s[:-1] + cur; segs.pop(i); changed = True; break
                if s[0] == cur[0]:
                    cur = list(reversed(s))[:-1] + cur; segs.pop(i); changed = True; break
        if len(cur) >= 4 and cur[0] == cur[-1]:
            rings.append(cur)
    return rings


def assemble(rels, ways_tags, coords):
    feats = []
    for tags, mem in rels:
        outer, inner = [], []
        for wid, role in mem:
            c = coords.get(wid)
            if not c:
                continue
            (inner if role == 'inner' else outer).append(c)
        outer_r = stitch(outer)
        inner_r = stitch(inner)
        if not outer_r:
            continue
        polys = []
        for o in outer_r:
            try:
                p = Polygon(o)
            except Exception:
                continue
            if not p.is_valid:
                p = p.buffer(0)
            if p.is_empty:
                continue
            holes = [i for i in inner_r
                     if Polygon(i).representative_point().within(p)]
            polys.append(p if not holes else
                         Polygon(o, [h for h in holes
                                     if Polygon(h).is_valid]))
        if not polys:
            continue
        feats.append((tags, unary_union(polys) if len(polys) > 1 else polys[0]))
    # одиночные way-границы (кольца)
    for wid, tags in ways_tags.items():
        c = coords.get(wid)
        if not c or c[0] != c[-1] or len(c) < 4:
            continue
        try:
            p = Polygon(c)
            if not p.is_valid:
                p = p.buffer(0)
            if not p.is_empty:
                feats.append((tags, p))
        except Exception:
            pass
    return feats


def main():
    h1 = RelPass()
    h1.apply_file(PBF)
    print(f"проход 1: границ-отношений {len(h1.rels)}, "
          f"way-границ {len(h1.boundary_ways)}, "
          f"member-ways {len(h1.member_ids)}")

    needed = set(h1.member_ids) | set(h1.boundary_ways)
    h2 = WayPass(needed)
    h2.apply_file(PBF, locations=True, idx='flex_mem')
    print(f"проход 2: координаты получены для {len(h2.coords)}/{len(needed)} ways")

    feats = assemble(h1.rels, h1.boundary_ways, h2.coords)
    print(f"собрано полигонов: {len(feats)}")

    box = Polygon([(CITY[0], CITY[1]), (CITY[2], CITY[1]),
                   (CITY[2], CITY[3]), (CITY[0], CITY[3])])
    seen = {}
    for tags, poly in feats:
        try:
            inter = poly.intersection(box)
        except Exception:
            continue
        if inter.is_empty or inter.area < 1e-10:
            continue
        key = (tags.get('name', ''), tags.get('admin_level', ''))
        seen[key] = unary_union([seen[key], inter]) if key in seen else inter

    out = [{'type': 'Feature',
            'properties': {'name': n, 'admin_level': l},
            'geometry': p.__geo_interface__}
           for (n, l), p in sorted(seen.items())]
    fc = {'type': 'FeatureCollection', 'features': out}
    (OUT / 'districts_osm.geojson').write_text(
        json.dumps(fc, ensure_ascii=False), encoding='utf-8')
    print(f"выход: {OUT / 'districts_osm.geojson'} — {len(out)} границ")
    for f in out:
        p = f['properties']
        print(f"  L{p['admin_level']}  {p['name']}")


if __name__ == '__main__':
    main()
