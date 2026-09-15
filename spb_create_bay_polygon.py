#!/usr/bin/env python3
"""
spb_create_bay_polygon.py — создать полигон Невской губы для модуля 13.

Граница:
- Запад: створ КЗС (дамба Кронштадт-Ломоносов)
- Восток: устье Невы (Стрелка В.О. / створ Горного института)
- Север: береговая линия (северный берег Финского залива до Кронштадта)
- Юг: береговая линия (южный берег, включая В.О., Петроградскую сторону)

Алгоритм: скачать OSM natural=water + waterway=riverbank в большем bbox
(включая Кронштадт), объединить в один полигон, обрезать по створам.
"""
import json, sys
from pathlib import Path
import requests
import shapely.geometry as sg
import shapely.ops as so
from shapely.geometry import shape, mapping, Polygon, LineString, MultiPolygon

OUT = Path('/home/z/my-project/download')
OUT.mkdir(parents=True, exist_ok=True)

# Larger bbox including Kronstadt + dam
# W, S, E, N
BBOX = (29.50, 59.85, 30.50, 60.10)


def download_water():
    """Download all water polygons + relations in larger bbox."""
    cache = OUT / 'spb_bay_water_raw.json'
    if cache.exists() and cache.stat().st_size > 1000:
        return json.loads(cache.read_text())
    
    W, S, E, N = BBOX
    q = f'''[out:json][timeout:600];
    (
      way["natural"="water"]({S},{W},{N},{E});
      relation["natural"="water"]({S},{W},{N},{E});
      way["waterway"~"^(river|canal|riverbank)$"]({S},{W},{N},{E});
      relation["waterway"~"^(river|canal|riverbank)$"]({S},{W},{N},{E});
    );
    out geom;'''
    
    r = requests.post('https://maps.mail.ru/osm/tools/overpass/api/interpreter',
                      data={'data': q}, timeout=600)
    data = r.json()
    cache.write_text(json.dumps(data))
    print(f'  ✓ {len(data.get("elements", []))} elements')
    return data


def stitch_relations(data):
    """Stitch relation members into polygons, like spb_full_run_v2.osm_to_geojson."""
    ways_by_id = {}
    standalone_ways = []
    for el in data['elements']:
        if el['type'] == 'way' and 'geometry' in el:
            ways_by_id[el['id']] = el
            standalone_ways.append(el)
    
    feats = []
    
    # Relations
    for el in data['elements']:
        if el['type'] != 'relation' or 'members' not in el:
            continue
        outers, inners = [], []
        for m in el['members']:
            if m.get('type') != 'way' or m.get('role') not in ('outer', 'inner'):
                continue
            way = ways_by_id.get(m['ref'])
            if way is None or 'geometry' not in way:
                continue
            coords = [(p['lon'], p['lat']) for p in way['geometry']]
            if len(coords) < 2:
                continue
            if m.get('role') == 'outer':
                outers.append(coords)
            else:
                inners.append(coords)
        if not outers:
            continue
        outer_lines = [LineString(r) for r in outers if r]
        if not outer_lines:
            continue
        merged = so.linemerge(outer_lines) if len(outer_lines) > 1 else outer_lines[0]
        outer_polys = list(so.polygonize(merged)) if merged.geom_type != 'Polygon' else [merged]
        if not outer_polys:
            continue
        inner_lines = [LineString(r) for r in inners if r and len(r) >= 2]
        inner_polys = []
        if inner_lines:
            inner_merged = so.linemerge(inner_lines) if len(inner_lines) > 1 else inner_lines[0]
            inner_polys = list(so.polygonize(inner_merged)) if inner_merged.geom_type != 'Polygon' else [inner_merged]
        for op in outer_polys:
            holes = []
            for ip in inner_polys:
                if op.contains(ip.representative_point()):
                    holes.append(list(ip.exterior.coords))
            try:
                poly = Polygon(op.exterior.coords, holes)
                if not poly.is_valid:
                    poly = poly.buffer(0)
                if poly.is_empty:
                    continue
                if poly.geom_type == 'Polygon':
                    feats.append(poly)
                elif poly.geom_type == 'MultiPolygon':
                    for p in poly.geoms:
                        if not p.is_empty:
                            feats.append(p)
            except Exception:
                pass
    
    # Standalone ways
    for el in standalone_ways:
        geom = el.get('geometry')
        if not geom:
            continue
        coords = [(p['lon'], p['lat']) for p in geom]
        if len(coords) >= 3 and coords[0] == coords[-1]:
            feats.append(Polygon(coords))
    
    return feats


def build_bay_polygon():
    """Build closed polygon of Невская губа using OSM water."""
    print('Downloading water data...')
    data = download_water()
    print('Stitching relations...')
    polys = stitch_relations(data)
    print(f'  {len(polys)} polygons')
    
    # Filter by bbox
    bbox_poly = sg.box(*BBOX)
    filtered = []
    for p in polys:
        if not p.is_valid:
            p = p.buffer(0)
        if p.is_empty:
            continue
        try:
            inter = p.intersection(bbox_poly)
            if not inter.is_empty:
                if inter.geom_type == 'Polygon':
                    filtered.append(inter)
                elif inter.geom_type == 'MultiPolygon':
                    for g in inter.geoms:
                        if not g.is_empty and g.area > 0.001:  # > 0.001 sq.deg
                            filtered.append(g)
        except Exception:
            pass
    print(f'  after bbox filter: {len(filtered)} polys')
    
    # Find largest polygon (presumably Невская губа + Финский залив)
    largest = max(filtered, key=lambda p: p.area)
    print(f'  largest polygon area: {largest.area:.4f} sq.deg')
    
    # Cut at western boundary (Кронштадтская дамба ~ 29.65°E)
    # КЗС: Кронштадт-Ломоносов, около 29.65°E
    west_cut = 29.65
    bay_cropped = largest.intersection(sg.box(west_cut, BBOX[1], BBOX[2], BBOX[3]))
    print(f'  after west cut (E > {west_cut}): area={bay_cropped.area:.4f}')
    
    # Cut at eastern boundary (устье Невы ~ 30.27°E at Стрелка В.О.)
    # Actually let's NOT cut east - we want to include устье Невы
    # The bay proper extends from КЗС on west to about 30.35°E on east (Горный институт)
    east_cut = 30.35
    bay_final = bay_cropped.intersection(sg.box(west_cut, BBOX[1], east_cut, BBOX[3]))
    print(f'  after east cut (E < {east_cut}): area={bay_final.area:.4f}')
    
    if bay_final.geom_type == 'MultiPolygon':
        # Take largest piece
        bay_final = max(bay_final.geoms, key=lambda p: p.area)
    
    # Save
    gj = {
        'type': 'FeatureCollection',
        'features': [{
            'type': 'Feature',
            'geometry': mapping(bay_final),
            'properties': {
                'name': 'Невская губа',
                'west_boundary': f'створ КЗС ({west_cut}°E)',
                'east_boundary': f'устье Невы ({east_cut}°E)',
                'area_sq_deg': bay_final.area,
            },
        }],
        'crs': {'type': 'name', 'properties': {'name': 'urn:ogc:def:crs:OGC:1.3:CRS84'}},
    }
    out_path = OUT / 'nevskaya_guba.geojson'
    out_path.write_text(json.dumps(gj, ensure_ascii=False))
    print(f'\n✓ Saved: {out_path}')
    print(f'  bounds: {bay_final.bounds}')
    print(f'  area: {bay_final.area:.4f} sq.deg (approximate)')
    
    # Also save to persistent
    persist = Path('/home/z/my-project/upload/scripts_persistent')
    persist.mkdir(parents=True, exist_ok=True)
    (persist / 'nevskaya_guba.geojson').write_text(json.dumps(gj, ensure_ascii=False))
    print(f'✓ Backup: {persist}/nevskaya_guba.geojson')


if __name__ == '__main__':
    build_bay_polygon()
