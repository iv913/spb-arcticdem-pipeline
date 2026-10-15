#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""6.30q — сборка продукта «поверхность участка по плану Атаянс»:
отметки (GeoJSON + валидация против fusion A2), здания (план->снап OSM + высоты),
DSM 1 м (территория A2 + крыши), вспомогательные PNG для карты."""
import json
import numpy as np
import rasterio
from rasterio.transform import Affine as RAff
from rasterio.features import rasterize, shapes as rio_shapes
from shapely.geometry import shape, Point, Polygon, mapping
from shapely.ops import unary_union
from scipy import ndimage
from PIL import Image

OUTD = '/tmp/my-project/results/atayanc_6.30'
FUSE = '/tmp/my-project/download/mosaic_6.00/spb_terra_fusion_6.00_A2.tif'
SRC = '/home/z/my-project/upload/atayanc-2013-01.jpg'

geo = json.load(open(f'{OUTD}/georef_6.30.json'))
A = RAff(*geo['affine_deg'])
print('affine:', A, 'scale_x', geo['scale_mpx_x'])

# ------------------------------------------------ 1. ОТМЕТКИ
MARKS = [
    (1188, 365, 2.39), (1360, 408, 2.62), (1297, 413, 2.43), (1307, 425, 2.68),
    (1348, 447, 2.49), (1427, 458, 2.48), (1380, 498, 2.16), (1410, 518, 2.75),
    (1403, 548, 2.52), (1400, 567, 2.04), (1442, 563, 2.97), (1517, 585, 2.70),
    (1413, 615, 4.00), (1297, 633, 4.64), (1378, 541, 3.75), (1438, 389, 2.46),
    (1523, 397, 2.51),
    (1468, 1025, 4.63), (1468, 1048, 3.86), (1523, 1041, 3.55), (1475, 1072, 3.55),
    (1635, 1067, 2.74), (1685, 1083, 2.74), (1632, 1103, 2.90), (1567, 1127, 3.72),
    (1533, 1130, 3.83), (1657, 1143, 2.89), (1640, 1170, 3.77), (1680, 1187, 3.96),
    (1627, 1245, 3.63), (1610, 1253, 3.83), (1680, 1253, 3.77), (1530, 1272, 4.50),
    (2330, 1381, 2.84), (2262, 1436, 2.80), (2387, 1433, 2.73), (2488, 1338, 3.00),
    (2522, 1425, 3.72), (2537, 1503, 2.83), (2445, 1518, 2.61), (2490, 1545, 3.04),
    (2378, 1641, 2.81),
    (2228, 1818, 2.85), (2367, 1827, 2.85), (2385, 1742, 2.86), (2152, 1887, 2.94),
    (2368, 1918, 2.97), (2458, 1780, 3.09), (2430, 1893, 2.82), (2165, 1862, 2.61),
]
# точки-пикеты: Bullet слева от числа -> сдвигаем px влево на ~12 px (центр пикета)
mark_pts = []
for x, y, v in MARKS:
    px = x - 12
    lon = A.c + A.a * px + A.b * y
    lat = A.f + A.d * px + A.e * y
    mark_pts.append((lon, lat, v))
print('отметок:', len(mark_pts))

# валидация против fusion A2
with rasterio.open(FUSE) as fs:
    fvals = []
    for lon, lat, v in mark_pts:
        r, c = fs.index(lon, lat)
        if 0 <= r < fs.height and 0 <= c < fs.width:
            w = fs.read(1, window=rasterio.windows.Window(c - 2, r - 2, 5, 5))
            wv = w[w > -9000]
            if len(wv):
                fvals.append(np.median(wv) / 100.0)
            else:
                fvals.append(np.nan)
        else:
            fvals.append(np.nan)
fvals = np.array(fvals)
vs = np.array([v for _, _, v in mark_pts], float)
ok = ~np.isnan(fvals)
d = vs[ok] - fvals[ok]
print(f'валидация отметок vs fusion A2: n={ok.sum()}/{len(vs)}, bias={d.mean():+.2f} м, '
      f'MAD={np.median(np.abs(d)):.2f} м, p95(|d|)={np.percentile(np.abs(d), 95):.2f} м')
print('невязки:', np.sort(d).round(2))
for (lon, lat, v), fv in zip(mark_pts, fvals):
    if np.isnan(fv):
        print(f'  nodata: {v:+.2f} @ ({lon:.5f},{lat:.5f})')
# статистика без грубых выбросов (>4 м)
cl = np.abs(d) <= 4.0
if cl.sum() >= 10:
    print(f'без выбросов (>4 м, отброшено {(~cl).sum()}): bias={d[cl].mean():+.2f}, '
          f'MAD={np.median(np.abs(d[cl])):.2f}, n={cl.sum()}')

marks_fc = {'type': 'FeatureCollection', 'features': [
    {'type': 'Feature',
     'properties': {'h_bs77': v, 'h_fusion': None if np.isnan(fv) else round(float(fv), 2),
                    'diff': None if np.isnan(fv) else round(v - float(fv), 2)},
     'geometry': {'type': 'Point', 'coordinates': [round(lon, 6), round(lat, 6)]}}
    for (lon, lat, v), fv in zip(mark_pts, fvals)]}
json.dump(marks_fc, open(f'{OUTD}/plan_marks_6.30.geojson', 'w'), ensure_ascii=False)

# ------------------------------------------------ 2. ЗДАНИЯ
img = Image.open(SRC).convert('HSV')
hsv = np.asarray(img).astype(np.int16)
Hh, Ss, Vv = hsv[..., 0], hsv[..., 1], hsv[..., 2]
bmask = (Hh >= 3) & (Hh <= 38) & (Ss >= 28) & (Vv >= 95) & (Vv <= 250)
rgb = np.asarray(Image.open(SRC).convert('RGB')).astype(np.int16)
darkred = (rgb[..., 0] >= 130) & (rgb[..., 0] <= 215) & (rgb[..., 1] >= 45) & \
          (rgb[..., 1] <= 130) & (rgb[..., 2] >= 20) & (rgb[..., 2] <= 100) & \
          ((rgb[..., 0] - rgb[..., 1]) >= 40)
lab, n = ndimage.label(bmask)
sizes = ndimage.sum(bmask, lab, range(1, n + 1))
com = np.array(ndimage.center_of_mass(bmask, lab, range(1, n + 1)))

# каталог высот с плана (визуальное чтение num_a/b/c)
NUMS = [(866, 324, 28), (948, 352, 28), (938, 392, 32), (708, 454, 32), (802, 446, 28),
        (782, 602, 23), (932, 522, 28), (924, 610, 23), (1030, 418, 23), (1172, 452, 16),
        (1044, 502, 28), (1074, 534, 23), (1046, 612, 23), (1124, 612, 16),
        (1284, 516, 13), (1330, 546, 16), (1270, 622, 13), (1300, 622, 16)]

polys = []
for i in range(n):
    if sizes[i] < 150:      # >= ~80 м2
        continue
    cid = i + 1
    m = (lab == cid)
    geoms = rio_shapes(m.astype(np.uint8), mask=m, transform=A)
    best, ba = None, 0
    for g, _ in geoms:
        a = shape(g).area
        if a > ba:
            best, ba = g, a
    if best is None:
        continue
    cy, cx = com[i]
    px, py = float(cx), float(cy)
    # класс цвета
    vals = rgb[m]
    dr = darkred[m].mean()
    # высоты из каталога: числа внутри/рядом (<=28 px)
    hs = [(x, y, v) for x, y, v in NUMS if (x - px) ** 2 + (y - py) ** 2 <= 30 ** 2]
    polys.append({'px': (px, py), 'geom': shape(best), 'area_m2': ba * 56050 * 111320,
                  'darkred': float(dr), 'nums': hs})
print(f'полигонов зданий (>=150 px): {len(polys)}')

# снап к OSM
osm = json.load(open(f'{OUTD}/osm_buildings_6.30.geojson'))
osm_feats = []
for ft in osm['features']:
    g = shape(ft['geometry'])
    osm_feats.append((g, ft['properties']))
print('OSM зданий:', len(osm_feats))

def h_of(p):
    hh = p.get('height') or p.get('building:height') or ''
    try:
        return float(str(hh).replace(',', '.').replace('м', '').strip())
    except (ValueError, TypeError):
        pass
    lv = p.get('levels') or p.get('building:levels') or ''
    try:
        return float(lv) * 3.1
    except (ValueError, TypeError):
        return None

out_b = []
matched_osm = set()
for p in polys:
    g = p['geom']
    c = g.centroid
    best_iou, best_j = 0.0, -1
    for j, (og, op) in enumerate(osm_feats):
        if og.centroid.distance(c) > 0.0012:
            continue
        if not g.intersects(og):
            continue
        inter = g.intersection(og).area
        iou = inter / (g.area + og.area - inter)
        if iou > best_iou:
            best_iou, best_j = iou, j
    h_plan = max([v for _, _, v in p['nums']], default=None)
    nums_str = '+'.join(str(v) for _, _, v in p['nums']) if p['nums'] else ''
    h_osm = None
    if best_j >= 0:
        matched_osm.add(best_j)
        h_osm = h_of(osm_feats[best_j][1])
    out_b.append({'geom': g, 'iou': best_iou, 'osm_j': best_j, 'h_plan': h_plan,
                  'h_osm': h_osm, 'nums': nums_str, 'darkred': p['darkred'],
                  'area_m2': p['area_m2']})
print(f'снатых к OSM: {sum(1 for b in out_b if b["osm_j"] >= 0)} / {len(out_b)}')

# итоговые атрибуты и высота крыши
bfeat = []
for b in out_b:
    g = b['geom']
    src, h = None, None
    if b['h_plan']:
        src, h = 'plan', b['h_plan']
    elif b['h_osm']:
        src, h = 'osm', b['h_osm']
    use_geom = b['osm_j'] if (b['iou'] >= 0.45 and b['osm_j'] >= 0) else None
    # несматанные в СЗ-фрагменте (грубая зона) — мусор главной аффины, в DSM не давать
    pxn, pyn = b['geom'].centroid.x, b['geom'].centroid.y
    # обратная аффина для px
    Mx = np.array([[A.a, A.b], [A.d, A.e]])
    u, v = np.linalg.solve(Mx, np.array([pxn - A.c, pyn - A.f]))
    in_nw = (u < 1150) and (v < 640)
    if use_geom is None and in_nw:
        continue
    bfeat.append({'g': g if use_geom is None else osm_feats[use_geom][0],
                  'props': {'h': h, 'src': src, 'h_plan': b['h_plan'], 'h_osm': b['h_osm'],
                            'nums': b['nums'], 'iou': round(b['iou'], 2),
                            'area_m2': round(b['area_m2'], 0),
                            'darkred': round(b['darkred'], 2),
                            'snapped': use_geom is not None}})
# OSM-здания без планового аналога (для DSM по высотам OSM)
for j, (og, op) in enumerate(osm_feats):
    if j in matched_osm or og.area * 56050 * 111320 < 120:
        continue
    hh = h_of(op)
    if hh is None:
        continue
    bfeat.append({'g': og, 'props': {'h': hh, 'src': 'osm_only', 'h_plan': None,
                                     'h_osm': hh, 'nums': '', 'iou': None,
                                     'area_m2': round(og.area * 56050 * 111320, 0),
                                     'darkred': None, 'snapped': True}})
print(f'зданий в DSM: {len(bfeat)} (плановых {sum(1 for x in bfeat if x["props"]["src"] != "osm_only")}, '
      f'только OSM {sum(1 for x in bfeat if x["props"]["src"] == "osm_only")})')

fc = {'type': 'FeatureCollection', 'features': [
    {'type': 'Feature', 'properties': p, 'geometry': mapping(x['g'])} for x, p in
    [(x, x['props']) for x in bfeat]]}
json.dump(fc, open(f'{OUTD}/plan_buildings_6.30.geojson', 'w'), ensure_ascii=False)

# ------------------------------------------------ 3. DSM 1 м
with rasterio.open(FUSE) as fs:
    print('fusion res:', fs.res, 'shape:', fs.shape)
    b = fs.bounds
    x0 = max(b.left, min(A.c, A.c + A.a * 3000, A.c + A.b * 2121, A.c + A.a * 3000 + A.b * 2121))
    y0 = max(b.bottom, min(A.f, A.f + A.d * 3000, A.f + A.e * 2121, A.f + A.d * 3000 + A.e * 2121))
    x1 = min(b.right, max(A.c, A.c + A.a * 3000, A.c + A.b * 2121, A.c + A.a * 3000 + A.b * 2121))
    y1 = min(b.top, max(A.f, A.f + A.d * 3000, A.f + A.e * 2121, A.f + A.d * 3000 + A.e * 2121))
    win = rasterio.windows.from_bounds(x0, y0, x1, y1, fs.transform)
    terr = fs.read(1, window=win).astype(np.float32) / 100.0
    ttr = fs.window_transform(win)
print('терр. окно:', terr.shape, float(np.nanmin(terr)), float(np.nanmax(terr)))
terr_bad = (terr < -10) | (terr > 90)
terr[terr_bad] = np.nan

# сетка 1 м в EPSG:4326 (локально: 1 м = 1/MLON °, 1/MLAT °)
MLON = 111320.0 * np.cos(np.radians(59.945))
MLAT = 111132.0
W1 = int((x1 - x0) * MLON)
H1 = int((y1 - y0) * MLAT)
aff1 = RAff(1 / MLON, 0, x0, 0, -1 / MLAT, y1)
# терр -> 1 м
from rasterio.warp import reproject, Resampling
dst = np.zeros((H1, W1), np.float32)
reproject(terr, dst, src_transform=ttr, src_crs='EPSG:4326',
          dst_transform=aff1, dst_crs='EPSG:4326', resampling=Resampling.bilinear)
# крыши
ground = dst.copy()
roofs = np.zeros((H1, W1), np.uint8)
shapes_roof, vals_roof = [], []
for x in bfeat:
    if x['props']['h'] is None:
        continue
    g = x['g']
    # уровень земли = медиана кольца 6-18 м
    ring = g.buffer(18).difference(g.buffer(6))
    msk = rasterize([(mapping(ring), 1)], out_shape=(H1, W1), transform=aff1).astype(bool)
    if msk.sum() < 20:
        msk = rasterize([(mapping(g.buffer(25)), 1)], out_shape=(H1, W1), transform=aff1).astype(bool)
    gv = ground[msk]
    gv = gv[gv > -50]
    if len(gv) < 10:
        continue
    base = float(np.median(gv))
    shapes_roof.append(mapping(g))
    vals_roof.append(base + x['props']['h'])
print('крыш:', len(shapes_roof))
if shapes_roof:
    roof_r = rasterize(list(zip(shapes_roof, vals_roof)), out_shape=(H1, W1),
                       transform=aff1, fill=0, dtype='float32')
    have = roof_r > 0
    dsm = dst.copy()
    dsm[have] = np.maximum(dsm[have], roof_r[have])
else:
    dsm = dst.copy()
nanm = np.isnan(dsm)
dsm[nanm] = -327.68
dsm_i16 = np.round(np.clip(dsm, -100, 900) * 100).astype(np.int16)
prof = dict(driver='GTiff', width=W1, height=H1, count=1, dtype='int16',
            crs='EPSG:4326', transform=aff1, compress='deflate', nodata=-32768)
print('shapes: dsm', dsm_i16.shape, 'prof', H1, W1)
with rasterio.open(f'{OUTD}/atayanc_plot_dsm_1m_6.30.tif', 'w', **prof) as dd:
    dd.write(dsm_i16, 1)
print('DSM готов:', (H1, W1), 'bounds', (x0, y0, x1, y1))
json.dump({'bounds': [x0, y0, x1, y1], 'marks_val': {
    'n': int(ok.sum()), 'bias': float(d.mean()), 'mad': float(np.median(np.abs(d))),
    'p95': float(np.percentile(np.abs(d), 95))}},
    open(f'{OUTD}/dsm_meta_6.30.json', 'w'))
print('ГОТОВО 6.30q')
