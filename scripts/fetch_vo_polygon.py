#!/usr/bin/env python3
"""Fetch V.O. island polygon (relation #1576083) and save to cache."""
import json, requests, time
from pathlib import Path
from shapely.geometry import LineString, Point
from shapely.ops import unary_union
import geopandas as gpd
import sys
sys.path.insert(0, '/home/z/my-project/scripts')
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location('m01', '/home/z/my-project/scripts/01_owner_map.py')
m01 = _ilu.module_from_spec(_spec); _spec.loader.exec_module(m01)

headers = {'User-Agent': 'vo-pipeline/1.0 (research)'}
q1 = '[out:json][timeout:120];relation(1576083);out body;'
rel_data = None
for attempt in range(5):
    try:
        r = requests.post('https://maps.mail.ru/osm/tools/overpass/api/interpreter',
                          data={'data': q1}, headers=headers, timeout=120)
        if r.status_code == 200 and r.text.strip().startswith('{'):
            rel_data = r.json(); break
        print(f'  attempt {attempt+1}: HTTP {r.status_code}')
    except Exception as e:
        print(f'  attempt {attempt+1}: {e}')
    time.sleep(5)
if rel_data is None:
    print('ERROR: cannot fetch relation'); exit(1)
rel = rel_data['elements'][0]
member_ids = [m['ref'] for m in rel['members'] if m['type'] == 'way']
print(f'Relation has {len(member_ids)} way members')

all_ways = []
batch_size = 50
for i in range(0, len(member_ids), batch_size):
    batch = member_ids[i:i+batch_size]
    way_q = '[out:json][timeout:60];(' + ''.join(f'way({wid});' for wid in batch) + ');out geom;'
    for attempt in range(3):
        try:
            r = requests.post('https://maps.mail.ru/osm/tools/overpass/api/interpreter',
                              data={'data': way_q}, headers=headers, timeout=60)
            if r.status_code == 200 and r.text.strip().startswith('{'):
                d = r.json()
                all_ways.extend(e for e in d['elements'] if e.get('type') == 'way')
                break
        except: pass
        time.sleep(3)
    print(f'  batch {i//batch_size + 1}: got {len(all_ways)}/{len(member_ids)} ways')
    time.sleep(2)

ways_dict = {w['id']: w for w in all_ways}
outers, inners = [], []
for m in rel.get('members', []):
    if m['ref'] not in ways_dict: continue
    c = [(p['lon'], p['lat']) for p in ways_dict[m['ref']]['geometry']]
    if len(c) < 2: continue
    (inners if m.get('role') == 'inner' else outers).append(LineString(c))

outer_polys = m01._stitch_rings(outers)
if not outer_polys:
    print('ERROR: stitching failed'); exit(1)
poly = unary_union(outer_polys)
if inners:
    poly = poly.difference(unary_union(m01._stitch_rings(inners)))
if not poly.is_valid:
    poly = poly.buffer(0)

area_km2 = gpd.GeoSeries([poly], crs='EPSG:4326').to_crs('EPSG:32636').iloc[0].area / 1e6
print(f'V.O. polygon: {poly.geom_type}, area {area_km2:.2f} km²')
print(f'Contains Bolshoy (30.265, 59.933): {poly.contains(Point(30.265, 59.933))}')
print(f'Contains Гороховая (30.32, 59.93): {poly.contains(Point(30.32, 59.93))}')

combined = {'version': 0.6, 'generator': 'manual', 'osm3s': rel_data.get('osm3s', {}),
            'elements': [rel] + all_ways}
Path('/home/z/my-project/scripts/dem_cache/osm/vo_polygon.json').write_text(json.dumps(combined))
gdf = gpd.GeoDataFrame({'geometry': [poly]}, crs='EPSG:4326')
gdf.to_file('/home/z/my-project/download/vo_polygon.geojson', driver='GeoJSON')
print('Saved')
