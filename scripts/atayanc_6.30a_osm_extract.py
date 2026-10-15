#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""6.30a — OSM-здания и ориентиры в bbox плана Атаянс (PBF BBBike).
Выход: results/atayanc_6.30/osm_buildings_6.30.geojson + osm_landmarks_6.30.json
"""
import json, math, os, time
import osmium
from shapely.geometry import LineString, Point, Polygon, mapping
from shapely.ops import linemerge, unary_union

PBF = '/tmp/my-project/data/osm_pbf/sankt_petersburg.osm.pbf'
OUTD = '/tmp/my-project/results/atayanc_6.30'
os.makedirs(OUTD, exist_ok=True)
BBOX = (30.245, 59.928, 30.355, 59.962)
BBOX_POLY = Polygon([(BBOX[0], BBOX[1]), (BBOX[2], BBOX[1]),
                     (BBOX[2], BBOX[3]), (BBOX[0], BBOX[3])])

def log(m): print(f'[{time.strftime("%H:%M:%S")}] {m}', flush=True)

NAMES = ['Собор Владимирской иконы Божией Матери',
         'Собор Владимирской иконы Божией Матери (Соб柱ор)',
         'Здание Биржи', 'Центральный военно-морской музей',
         'Зимний дворец', 'Биржевой мост', 'Дворцовый мост',
         'Зимняя канавка', 'Кунсткамера', 'Ростральная колонна']
LM = []

def btags(t):
    return ('building' in t and t['building'] not in ('no', 'roof')) or \
           t.get('building:part')

rel_ways, rel_info = {}, {}

class RelHandler(osmium.SimpleHandler):
    def relation(self, r):
        t = dict(r.tags)
        if 'multipolygon' not in (t.get('type'), ):
            return
        if not btags(t):
            return
        d = {'outer': [], 'inner': []}
        for m in r.members:
            if m.type == 'w':
                d[m.role if m.role in ('outer', 'inner') else 'outer'].append(m.ref)
        if d['outer']:
            rel_ways[r.id] = d
            rel_info[r.id] = t

    def way(self, w):
        t = dict(w.tags)
        if len(w.nodes) >= 4 and w.nodes[0].ref == w.nodes[-1].ref and btags(t):
            c = [(n.location.lon, n.location.lat) for n in w.nodes]
            p = Polygon(c)
            if not p.is_valid: p = p.buffer(0)
            if not p.is_empty and p.geom_type == 'Polygon' and p.intersects(BBOX_POLY):
                FEATS.append({'tags': t, 'geom': p})

    def node(self, n):
        t = dict(n.tags)
        if n.location.valid() and (t.get('name') in NAMES or
                t.get('man_made') == 'obelisk' and 'ростр' in t.get('name', '').lower()):
            LM.append({'name': t.get('name', ''), 'tags': {k: v for k, v in t.items() if k != 'name'},
                       'lon': n.location.lon, 'lat': n.location.lat})

FEATS = []
log('проход A: связи/ways/узлы...')
t0 = time.time()
RelHandler().apply_file(PBF, locations=True, idx='flex_mem')
log(f'прямых ways-зданий: {len(FEATS)}, связей: {len(rel_ways)}, узлов-ориентиров: {len(LM)}')

need = set()
for d in rel_ways.values():
    need.update(d['outer']); need.update(d['inner'])
way_geo = {}

class WayHandler(osmium.SimpleHandler):
    def way(self, w):
        if w.id in need and len(w.nodes) >= 2:
            way_geo[w.id] = [(n.location.lon, n.location.lat) for n in w.nodes]

log('проход B: геометрии участников связей...')
t0 = time.time()
WayHandler().apply_file(PBF, locations=True, idx='flex_mem')
log(f'геометрий: {len(way_geo)}/{len(need)} за {time.time()-t0:.0f} с')

def assemble(lines):
    if not lines: return []
    merged = linemerge([LineString(r) for r in lines])
    strs = list(merged.geoms) if merged.geom_type == 'MultiLineString' else ([merged] if merged and not merged.is_empty else [])
    out = []
    for s in strs:
        a, b = s.coords[0], s.coords[-1]
        if a != b:
            # для зданий разомкнутые кольца чиним хордой (маленькие контуры)
            pass
        coords = list(s.coords)
        if coords[0] != coords[-1]:
            coords.append(coords[0])
        p = Polygon(coords)
        if not p.is_valid: p = p.buffer(0)
        if not p.is_empty and p.geom_type == 'Polygon':
            out.append(p)
    return out

n_rel = 0
for rid, d in rel_ways.items():
    lines_out = [way_geo[w] for w in d['outer'] if w in way_geo]
    if not lines_out: continue
    shells = assemble(lines_out)
    holes = assemble([way_geo[w] for w in d['inner'] if w in way_geo])
    for s in shells:
        if not s.is_valid: s = s.buffer(0)
        hh = [h for h in holes if h.within(s)]
        if hh: s = s.difference(unary_union(hh))
        parts = list(s.geoms) if s.geom_type == 'MultiPolygon' else [s]
        for p in parts:
            if not p.is_empty and p.intersects(BBOX_POLY):
                FEATS.append({'tags': rel_info[rid], 'geom': p})
                n_rel += 1
log(f'полигонов из связей: {n_rel}')

# клип в bbox и записи
feats, kept = [], 0
for f in FEATS:
    p = f['geom']
    if not p.intersects(BBOX_POLY): continue
    p2 = p.intersection(BBOX_POLY)
    if p2.is_empty: continue
    if p2.geom_type == 'MultiPolygon':
        p2 = max(p2.geoms, key=lambda g: g.area)
    if p2.geom_type != 'Polygon' or p2.area < 1e-10: continue
    t = f['tags']
    kept += 1
    feats.append({'type': 'Feature',
                  'properties': {'name': t.get('name', ''),
                                 'height': t.get('height', t.get('building:height', '')),
                                 'levels': t.get('building:levels', ''),
                                 'building': t.get('building', ''),
                                 'bpart': t.get('building:part', ''),
                                 'osm_area_m2': round(p2.area * 12392.0 * math.cos(math.radians(59.9)) / (0.001**2) * 1e-6, 0)},
                  'geometry': mapping(p2)})
log(f'зданий в bbox: {kept}')
fc = {'type': 'FeatureCollection', 'features': feats}
with open(f'{OUTD}/osm_buildings_6.30.geojson', 'w') as fo:
    json.dump(fc, fo, ensure_ascii=False, separators=(',', ':'))

# линии-ориентиры (мосты, канавка) и ways с именами
LMW = []
class NameWayHandler(osmium.SimpleHandler):
    def way(self, w):
        t = dict(w.tags)
        if t.get('name') in ('Биржевой мост', 'Дворцовый мост', 'Зимняя канавка', 'Река Мойка') or \
           (t.get('leisure') == 'track'):
            c = [(n.location.lon, n.location.lat) for n in w.nodes if n.location.valid()]
            if len(c) >= 2:
                lm = [x for x in c if BBOX_POLY.contains(Point(x))]
                if lm:
                    LMW.append({'name': t.get('name', t.get('leisure', '')),
                                'kind': 'bridge' if 'мост' in t.get('name', '') else ('track' if t.get('leisure') == 'track' else 'water'),
                                'coords': lm})
NameWayHandler().apply_file(PBF, locations=True, idx='flex_mem')
with open(f'{OUTD}/osm_landmarks_6.30.json', 'w') as fo:
    json.dump({'nodes': LM, 'ways': LMW}, fo, ensure_ascii=False, indent=1)
log(f'готово: {len(feats)} зданий, узлов {len(LM)}, именных ways {len(LMW)} -> {OUTD}')
