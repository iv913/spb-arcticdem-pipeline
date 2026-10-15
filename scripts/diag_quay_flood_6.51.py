#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Диагностика: почему набережные «топятся» уже на +1 м.
Проверяем 3 гипотезы на данных:
  H1: лента смешанных пикселей у уреза (8 м сетка fusion блендит гранит набережной с водой)
  H2: щели между OSM-водой 6.21 и фактической линией причала (остаток 0.17 км2)
  H3: набережная реально ниже +1 м (опровергается отметками каталога 6.50)
"""
import json, csv
import numpy as np
import rasterio
from shapely.geometry import shape, Point, box
from shapely.ops import unary_union

FLOOD = '/tmp/my-project/results/atayanc_6.50/flood_plot_6.50.geojson'
FUSE  = '/tmp/my-project/download/mosaic_6.00/spb_terra_fusion_6.00_A2.tif'
WATER = '/tmp/my-project/results/fusion_6.21/inland_water_6.21.geojson'
CAT   = '/home/z/my-project/download/atayanc_6.50/marks_catalog_6.50.csv'
OUT   = '/home/z/my-project/results/atayanc_6.51'

# ---------- 1. Геометрия +1 м: как далеко от воды лежит затопленное?
fj = json.load(open(FLOOD))
def feats_for(lv):
    key = ('%g' % lv)
    for k, v in fj.items():
        if float(k) == float(lv):
            return [shape(f['geometry']) for f in v]
    return []
lv1 = feats_for(1.0)
lv1_u = unary_union(lv1) if lv1 else None
a1 = lv1_u.area if lv1_u is not None else 0.0
print('== Участок 6.50: полигонов +1 м: %d, площадь %.4f км2' % (len(lv1), a1 * 1e6 / 1e6))

fj2 = json.load(open(FLOOD))
for lv in (2.0, 3.0):
    s = feats_for(lv)
    if s:
        print('   для сравнения +%d м: %d полигонов, %.3f км2' % (lv, len(s), unary_union(s).area * 1e6 / 1e6))

water_u = unary_union([shape(f['geometry']) for f in json.load(open(WATER))['features']])
if lv1_u is not None:
    # буферы вокруг воды: 15 и 30 м (градусы)
    DEG = 111320.0 * np.cos(np.radians(59.945))
    b15 = water_u.buffer(15 / DEG)
    b30 = water_u.buffer(30 / DEG)
    a_in15 = lv1_u.intersection(b15).area
    a_in30 = lv1_u.intersection(b30).area
    print('   +1 м в 15 м от воды: %.1f%%   в 30 м: %.1f%%' %
          (100 * a_in15 / a1, 100 * a_in30 / a1))
    # средняя ширина ленты: площадь / длина границы с водой
    edge_len_m = lv1_u.intersection(b15.boundary).length * DEG if a_in15 > 0 else 0
    if edge_len_m > 0:
        print('   длина контакта с 15-м береговой полосой ~ %.0f м -> средняя ширина ленты ~ %.1f м' %
              (edge_len_m, a1 * 1e6 / edge_len_m))

# ---------- 2. Профили поперёк набережных: вода -> берег
rows = list(csv.DictReader(open(CAT, encoding='utf-8')))
quay_marks = [r for r in rows if 'наб' in (r.get('street') or r.get('addr') or
             r.get('address') or r.get('name') or '').lower()]
if not quay_marks:
    # fallback: все отметки, потом отфильтруем по близости к воде
    quay_marks = rows
print('\n== Отметки на набережных в каталоге: %d' % len(quay_marks))

with rasterio.open(FUSE) as fs:
    pass

# сэмплируем fusion base по профилям: от точки воды в сторону отметки
import math
DEG = 111320.0 * np.cos(np.radians(59.945))
MLAT = 111132.0

def sample_base(lon, lat):
    try:
        with rasterio.open(FUSE) as fs:
            v = next(fs.sample([(lon, lat)]))
        return v[0] / 100.0
    except Exception:
        return np.nan

def nearest_water_dir(lon, lat, maxr_deg=0.004):
    """азимут к ближайшей воде: грубый скан по 36 направлениям"""
    best = None
    for adeg in range(0, 360, 10):
        a = math.radians(adeg)
        for rm in (10, 20, 30, 40, 60, 80):
            dx = rm * math.sin(a) / DEG
            dy = rm * math.cos(a) / MLAT
            p = Point(lon + dx, lat + dy)
            if water_u.contains(p):
                if best is None or rm < best[1]:
                    best = (adeg, rm)
                break
    return best

print('\n== Профили fusion base (см/100) поперёк набережных: '
      'значения на -15, -8, -4 м (вода) и +2..+40 м от уреза в сторону отметки')
sel = quay_marks[:40]
shown = 0
for r in sel:
    lon, lat = float(r['lon']), float(r['lat'])
    h = float(r['h_bs77_m'])
    nw = nearest_water_dir(lon, lat)
    if nw is None:
        continue
    adeg, rm = nw
    a = math.radians(adeg)
    prof = []
    for off in (-15, -8, -4, 2, 5, 10, 15, 25, 40):
        dx = (rm + off) * math.sin(a) / DEG
        dy = (rm + off) * math.cos(a) / MLAT
        prof.append(sample_base(lon + dx, lat + dy))
    prof_s = ' '.join(('%+.1f' % v) if v == v else '  nan ' for v in prof)
    print('  %s h=%s | вода(-15..-4): %s | берег(+2..+40): %s' %
          (r.get('id', '?'), r.get('h_bs77_m'), prof_s[:34], prof_s[34:]))
    shown += 1
    if shown >= 12:
        break

# ---------- 3. Отметки на набережных: реальные высоты гранита
print('\n== Отметки БС-77 на набережных (каталог 6.50) vs порог +1 м:')
vals = []
for r in quay_marks:
    lon, lat = float(r['lon']), float(r['lat'])
    if water_u.buffer(25 / DEG).contains(Point(lon, lat)) is False:
        h = float(r['h_bs77_m'])
        vals.append((r.get('id', '?'), h))
vals.sort(key=lambda t: t[1])
if vals:
    arr = np.array([v for _, v in vals])
    print('   n=%d  min %.2f  p25 %.2f  медиана %.2f  p75 %.2f  max %.2f' %
          (len(arr), arr.min(), np.percentile(arr, 25), np.median(arr),
           np.percentile(arr, 75), arr.max()))
    print('   ниже +1.0 м: %d из %d' % (int((arr < 1.0).sum()), len(arr)))
    print('   примеры низких:', [(i, round(v, 2)) for i, v in vals[:6]])
    print('   примеры высоких:', [(i, round(v, 2)) for i, v in vals[-6:]])

# ---------- 4. Городская карта 6.21: остаток зон на внутренних водах/щелях
FZ = '/tmp/my-project/results/fusion_6.21/flood_levels_5.96_clip2.geojson'
fzc = json.load(open(FZ))
lv1c = []
for f in fzc['features']:
    if abs(float(f['properties'].get('level_m', 0)) - 1.0) < 0.01:
        g = shape(f['geometry'])
        if not g.is_valid:
            g = g.buffer(0)
        lv1c.append(g)
if lv1c:
    u1 = unary_union(lv1c)
    print('\n== Город 6.21, уровень +1 м (clip2): %d полигонов, %.2f км2' %
          (len(lv1c), u1.area * 1e6 / 1e6))
    # дельта Невы: сколько из +1 м лежит в 30 м от воды (щели вдоль набережных)
    b30c = water_u.buffer(30 / DEG)
    a30 = u1.intersection(b30c).area
    print('   из них в 30 м от воды OSM: %.2f км2 (%.0f%%)' %
          (a30 * 1e6 / 1e6, 100 * a30 / u1.area))
print('\nГОТОВО')
