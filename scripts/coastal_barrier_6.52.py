#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""6.52a — РЕВИЗИЯ: прибрежный барьер 16 м для городской карты затоплений.

Проблема (диагностика 6.51): вдоль гранитных набережных зоны +1 м — это лента
смешанных пикселей сетки 5.96 (~8 м): fusion блендит гранит (2.5-3.5 м) с водой
(0 м) -> береговая ячейка читается 0.3-1.0 м. Плюс щели между OSM-водой и
фактическим причалом. Реальный гранит сух (медиана отметок 2.76 м, n=253).

Фикс: из всех зон (база 5.96 клип2 + fill A1-A4 клип2) вычитается полоса
water.buffer(16 м); обрывки < 0.5 га отбрасываются. Пересчёт площадей,
валидация: доля зоны в 30 м от воды должна упасть ~16% -> ~0-2%.

Выход: results/fusion_6.52/flood_levels_5.96_barrier16.geojson
       results/fusion_6.52/fill_zones_6.20_A{1..4}_barrier16.geojson
       results/fusion_6.52/barrier_stats_6.52.json
"""
import json, os
import numpy as np
from shapely.geometry import shape, mapping, box
from shapely.ops import unary_union

FUS21 = '/tmp/my-project/results/fusion_6.21'
FUS20 = '/tmp/my-project/results/fusion_6.20'
OUTD = '/tmp/my-project/results/fusion_6.52'
os.makedirs(OUTD, exist_ok=True)

DEG = 111320.0 * np.cos(np.radians(59.945))
MLAT = 111132.0
BAND_M = 16.0          # ширина прибрежного барьера
MIN_PART_M2 = 5000.0   # обрывки после вычитания

def m2(g):
    return g.area * DEG * MLAT

# ---------- полная вода (море 6.20 + внутренние 6.21)
sea = unary_union([shape(f['geometry']).buffer(0)
                   for f in json.load(open(f'{FUS20}/osm_water_6.20.geojson'))['features']])
inl = unary_union([shape(f['geometry']).buffer(0)
                   for f in json.load(open(f'{FUS21}/inland_water_6.21.geojson'))['features']])
water = unary_union([sea, inl])
print('вода: море %.1f + внутренние %.1f = %.1f км2' %
      (m2(sea) / 1e6, m2(inl) / 1e6, m2(water) / 1e6))
W16 = water.buffer(BAND_M / DEG)
print('буфер +%d м готов' % BAND_M)

def clean_parts(g, min_m2=MIN_PART_M2):
    """отфильтровать полигоны-обрывки, вернуть (geom, n_parts, area_m2)"""
    if g.is_empty:
        return None, 0, 0.0
    if g.geom_type == 'Polygon':
        polys = [g]
    else:
        polys = list(g.geoms)
    keep = [p for p in polys if m2(p) >= min_m2]
    if not keep:
        return None, 0, 0.0
    u = unary_union(keep) if len(keep) > 1 else keep[0]
    return u, len(keep), m2(u)

def barrierize(path_in, path_out, prop_level='level_m', extra_props=None):
    """вычесть барьер из всех уровней файла, пересчитать площади"""
    fc = json.load(open(path_in))
    out_feats, rows = [], {}
    for ft in fc['features']:
        lv = ft['properties'][prop_level]
        g = shape(ft['geometry'])
        if not g.is_valid:
            g = g.buffer(0)
        before = m2(g)
        gb = g.difference(W16)
        gclean, nparts, after_m2 = clean_parts(gb)
        props = {prop_level: lv, 'area_km2': round(after_m2 / 1e6, 3),
                 'parts': nparts, 'area_km2_raw': round(before / 1e6, 3)}
        if extra_props:
            props.update({k: ft['properties'][k] for k in extra_props
                          if k in ft['properties']})
        rows[str(float(lv))] = {'before_km2': round(before / 1e6, 2),
                                'after_km2': round(after_m2 / 1e6, 2),
                                'parts': nparts}
        if gclean is not None:
            out_feats.append({'type': 'Feature', 'properties': props,
                              'geometry': mapping(gclean)})
        print('  %s=%s: %.2f -> %.2f км2 (%d частей)' %
              (prop_level, lv, before / 1e6, after_m2 / 1e6, nparts))
    json.dump({'type': 'FeatureCollection', 'features': out_feats},
              open(path_out, 'w'), ensure_ascii=False)
    return rows

print('\n== База 5.96 (клип2) -> барьер')
base_rows = barrierize(f'{FUS21}/flood_levels_5.96_clip2.geojson',
                       f'{OUTD}/flood_levels_5.96_barrier16.geojson')

fill_rows = {}
for A in (1, 2, 3, 4):
    print(f'\n== Fill A{A} (клип2) -> барьер')
    fill_rows[A] = barrierize(f'{FUS21}/fill_zones_6.20_A{A}_clip2.geojson',
                              f'{OUTD}/fill_zones_6.20_A{A}_barrier16.geojson',
                              extra_props=['a'])

# ---------- валидация фикса: доля зоны в 30 м от воды до/после
W30 = water.buffer(30.0 / DEG)
b = json.load(open(f'{FUS21}/flood_levels_5.96_clip2.geojson'))
a = json.load(open(f'{OUTD}/flood_levels_5.96_barrier16.geojson'))
def share30(fc, lv):
    for ft in fc['features']:
        if float(ft['properties']['level_m']) == lv:
            g = shape(ft['geometry']).buffer(0)
            if g.is_empty:
                return 0.0
            return 100.0 * g.intersection(W30).area / g.area
    return None
print('\n== Валидация: доля зоны в 30 м от воды, %% (до -> после)')
valid = {}
for lv in (1.0, 2.0, 3.0):
    s0, s1 = share30(b, lv), share30(a, lv)
    valid[str(lv)] = [s0, s1]
    print('  +%.1f м: %.0f%% -> %.0f%%' % (lv, s0 or 0, s1 or 0))

# ---------- экстраполяция A4-A1 после барьера (для сводки)
from shapely.geometry import shape as shp
ext = {}
for lv in (1.0, 3.0, 5.0):
    def get(fc, lv):
        for ft in fc['features']:
            if float(ft['properties']['level_m']) == lv:
                g = shp(ft['geometry']).buffer(0)
                return g
        return None
    g4, g1 = get(a if False else json.load(open(f'{OUTD}/fill_zones_6.20_A4_barrier16.geojson')), lv), \
             get(json.load(open(f'{OUTD}/fill_zones_6.20_A1_barrier16.geojson')), lv)
    if g4 is None:
        continue
    gd = g4.difference(g1) if g1 is not None else g4
    ext[str(lv)] = round(m2(gd) / 1e6, 2)
print('экстраполяция A4-A1 после барьера, км2:', ext)

stats = {'band_m': BAND_M, 'min_part_m2': MIN_PART_M2,
         'levels': {'база 5.96': base_rows,
                    **{f'fill A{A}': fill_rows[A] for A in (1, 2, 3, 4)}},
         'ribbon_share30_before_after_pct': valid,
         'ext_A4mA1_km2_after': ext}
json.dump(stats, open(f'{OUTD}/barrier_stats_6.52.json', 'w'), ensure_ascii=False, indent=1)
print('\nГОТОВО 6.52a ->', OUTD)
