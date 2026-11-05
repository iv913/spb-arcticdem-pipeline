#!/usr/bin/env python3
"""6.76/A3 — что говорит OSM PBF о «воде» в квадрате фантома feat2
(30.2936-30.3067, 60.0474-60.0654, Лесная/Выборгская сторона)?"""
import osmium
from shapely.geometry import Polygon, box

PBF = '/tmp/my-project/data/osm_pbf/sankt_petersburg.osm.pbf'
BB = (30.28, 60.04, 30.32, 60.07)
tb = box(*BB)

WATER = lambda t: (t.get('natural') in ('water', 'wetland')
                   or t.get('waterway') in ('riverbank', 'dock', 'river',
                                            'canal', 'stream')
                   or t.get('landuse') in ('reservoir', 'basin')
                   or 'water' in t)

hits = []


class W(osmium.SimpleHandler):
    def way(self, w):
        t = dict(w.tags)
        if not WATER(t):
            return
        try:
            cs = [(n.lon, n.lat) for n in w.nodes]
        except Exception:
            return
        if len(cs) >= 3:
            p = Polygon(cs)
            if p.intersects(tb):
                hits.append(('way', t, p.area * 1.21e10))


W().apply_file(PBF, locations=True, idx='flex_mem')
print(f'water-подобных ways в квадрате: {len(hits)}')
for kind, t, a in hits[:10]:
    print(' ', kind, {k: v for k, v in list(t.items())[:5]},
          f'{a:.0f} м2')
if not hits:
    print('  -> OSM НЕ имеет водных полигонов в квадрате фантома')
