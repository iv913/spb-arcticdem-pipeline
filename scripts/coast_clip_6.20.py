#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""6.20b — Клип зон затопления по береговой линии (суша = bbox − вода OSM).

Проблема (скрин пользователя): «стрелы» достроенных зон тянутся в залив
у Ломоносова/Кронштадта — лакуны-полосы в акватории заполняются Лапласом
и попадают в flood-маску. Решение: все полигоны (база 5.96 + fill 6.20
A1..A4) обрезаются маской суши.

Суша = bbox сетки − osm_water_6.20.geojson (coastline-полигоны OSM,
osmdata.openstreetmap.de, ODbL). Каверзные места:
  - Котлин/Кронштадт: в coastline-полигонах OSM остров не выделен сушей
    (нет и данных мозаики) -> зоны на острове не показываются;
  - восточнее ~30.235 (дельта Невы) coastline-вода кончается -> там клип
    не действует (в сетке данные плотные, артефактов нет).

Выход:
  results/fusion_6.20/flood_levels_5.96_clip.geojson
  results/fusion_6.20/fill_zones_6.20_A{1..4}_clip.geojson
  results/fusion_6.20/clip_stats_6.20.json
"""
import json
import math
import os
import time

from shapely.geometry import shape as shp, box as shbox, mapping
from shapely.ops import unary_union

FUS = '/tmp/my-project/results/fusion_6.20'
BASE = '/tmp/my-project/results/flood_zones_5.96/flood_levels_5.96.geojson'
WATER = os.path.join(FUS, 'osm_water_6.20.geojson')
GBBOX = (29.36, 59.70, 30.5999835, 60.1183)
KM2 = 12392.0 * math.cos(math.radians(59.9))     # deg2 -> km2


def log(m):
    print(f'[{time.strftime("%H:%M:%S")}] {m}', flush=True)


def fc_features(path):
    with open(path) as f:
        return json.load(f)['features']


def clip_fc(feats, land):
    out, stats = [], {}
    for ft in feats:
        g = shp(ft['geometry'])
        if not g.is_valid:
            g = g.buffer(0)
        a0 = g.area * KM2
        gc = g.intersection(land)
        a1 = gc.area * KM2
        lv = ft['properties'].get('level_m')
        key = f"{lv:.1f}" if lv is not None else '?'
        stats[key] = {'before_km2': round(a0, 2), 'after_km2': round(a1, 2)}
        if gc.is_empty or gc.area <= 0:
            continue
        if gc.geom_type == 'Polygon':
            gc = [gc]
        else:
            gc = list(gc.geoms)
        parts = []
        for g2 in gc:
            if g2.geom_type != 'Polygon' or g2.is_empty:
                continue
            rings = [[[round(x, 5), round(y, 5)] for x, y in g2.exterior.coords]]
            rings += [[[round(x, 5), round(y, 5)] for x, y in rr.coords]
                      for rr in g2.interiors]
            parts.append(rings)
        pr = dict(ft['properties'])
        pr['area_km2'] = round(a1, 2)
        pr['area_km2_raw'] = round(a0, 2)
        out.append({'type': 'Feature',
                    'properties': pr,
                    'geometry': {'type': 'MultiPolygon', 'coordinates': parts}})
    return out, stats


def main():
    log('вода OSM...')
    water = unary_union([shp(ft['geometry']) for ft in fc_features(WATER)])
    land = shbox(*GBBOX).difference(water)
    if not land.is_valid:
        log('суша невалидна — buffer(0)...')
        land = land.buffer(0)
    log(f'суша: {land.area*KM2:.1f} км2 из {shbox(*GBBOX).area*KM2:.1f}')

    stats = {'stage': '6.20b', 'land_km2': round(land.area * KM2, 1)}

    log('клип базы 5.96...')
    feats = fc_features(BASE)
    out, st = clip_fc(feats, land)
    stats['base_5.96'] = st
    with open(os.path.join(FUS, 'flood_levels_5.96_clip.geojson'), 'w') as f:
        json.dump({'type': 'FeatureCollection', 'name': 'flood_levels_5.96_clip',
                   'features': out}, f, ensure_ascii=False)
    for k in sorted(st):
        log(f"  база +{k} м: {st[k]['before_km2']} -> {st[k]['after_km2']} км2")

    for A in (1, 2, 3, 4):
        log(f'клип fill A{A}...')
        feats = fc_features(os.path.join(FUS, f'fill_zones_6.20_A{A}.geojson'))
        out, st = clip_fc(feats, land)
        stats[f'fill_A{A}'] = st
        with open(os.path.join(FUS, f'fill_zones_6.20_A{A}_clip.geojson'), 'w') as f:
            json.dump({'type': 'FeatureCollection',
                       'name': f'spb_fill_flood_A{A}_6.20_clip',
                       'features': out}, f, ensure_ascii=False)
        for k in sorted(st):
            log(f"  A{A} +{k} м: {st[k]['before_km2']} -> {st[k]['after_km2']} км2")

    with open(os.path.join(FUS, 'clip_stats_6.20.json'), 'w') as f:
        json.dump(stats, f, ensure_ascii=False, indent=1)
    log('готово')


if __name__ == '__main__':
    main()
