#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""6.30m — врез СЗ-угла: именные объекты OSM + перебор (масштаб, поворот) корреляцией масок."""
import json, math
import numpy as np
import osmium
from PIL import Image
from scipy import ndimage, signal
from scipy.ndimage import zoom as ndzoom
import rasterio.features as rf
from shapely.geometry import shape, Polygon, mapping
from matplotlib.path import Path

PBF = '/tmp/my-project/data/osm_pbf/sankt_petersburg.osm.pbf'
OUTD = '/tmp/my-project/results/atayanc_6.30'

want_names = ['стадион', 'Ленина', 'Юбилейный', 'Ждановка', 'Карповка',
              'Кронверкский', 'Тучков', 'оопарк']
found = {'way': [], 'node': []}

class H(osmium.SimpleHandler):
    def way(self, w):
        t = dict(w.tags)
        nm = t.get('name', '')
        if any(k.lower() in nm.lower() for k in want_names) or \
           t.get('leisure') in ('stadium', 'track') or t.get('tourism') == 'zoo':
            c = [(n.location.lon, n.location.lat) for n in w.nodes if n.location.valid()]
            if len(c) >= 2:
                found['way'].append({'name': nm, 'kind': t.get('leisure', t.get('waterway', t.get('building', ''))),
                                     'cx': sum(p[0] for p in c) / len(c),
                                     'cy': sum(p[1] for p in c) / len(c), 'n': len(c)})

    def node(self, n):
        t = dict(n.tags)
        nm = t.get('name', '')
        if n.location.valid() and any(k.lower() in nm.lower() for k in want_names):
            found['node'].append({'name': nm, 'lon': n.location.lon, 'lat': n.location.lat})

H().apply_file(PBF, locations=True, idx='flex_mem')
for w in found['way']:
    print(f"W {w['name'][:42]:44s} {w['kind'][:10]:10s} ({w['cx']:.5f},{w['cy']:.5f}) n={w['n']}")
for n in found['node']:
    print(f"N {n['name'][:42]} ({n['lon']:.5f},{n['lat']:.5f})")
json.dump(found, open(f'{OUTD}/nw_osm_features.json', 'w'), ensure_ascii=False, indent=1)

img = Image.open('/home/z/my-project/upload/atayanc-2013-01.jpg').convert('HSV')
hsv = np.asarray(img).astype(np.int16)
pm = (hsv[..., 0] >= 3) & (hsv[..., 0] <= 38) & (hsv[..., 1] >= 28) & (hsv[..., 2] >= 95) & (hsv[..., 2] <= 250)
pz = np.zeros_like(pm)
pz[:640, :1150] = pm[:640, :1150]

MLON = 111320.0 * np.cos(np.radians(59.95))
MLAT = 111132.0
RB = (30.270, 59.940, 30.360, 59.965)
Wm = int((RB[2] - RB[0]) * MLON)
Hm = int((RB[3] - RB[1]) * MLAT)
BB = Polygon([(RB[0], RB[1]), (RB[2], RB[1]), (RB[2], RB[3]), (RB[0], RB[3])])
om = np.zeros((Hm, Wm), bool)
for ft in json.load(open(f'{OUTD}/osm_buildings_6.30.geojson'))['features']:
    g = shape(ft['geometry'])
    if not g.intersects(BB):
        continue
    g2 = g.intersection(BB)
    if g2.is_empty or g2.geom_type != 'Polygon':
        continue
    minx, miny, maxx, maxy = g2.bounds
    x0 = max(0, int((minx - RB[0]) * MLON)); x1 = min(Wm, int((maxx - RB[0]) * MLON) + 1)
    y0 = max(0, int((RB[3] - maxy) * MLAT)); y1 = min(Hm, int((RB[3] - miny) * MLAT) + 1)
    if x1 - x0 < 2 or y1 - y0 < 2:
        continue
    gx, gy = np.meshgrid(np.arange(x0, x1) + 0.5, np.arange(y0, y1) + 0.5)
    pts = np.stack([(gx / MLON + RB[0]).ravel(), (RB[3] - gy / MLAT).ravel()], 1)
    om[y0:y1, x0:x1] |= Path(np.array(ft['geometry']['coordinates'][0])).contains_points(pts).reshape(y1 - y0, x1 - x0)
print('OSM растр:', om.shape, int(om.sum()), 'px')

pm_small = pz[::2, ::2].astype(np.float32)
best = None
for s in np.arange(0.35, 2.05, 0.05):
    for rot in (-9, -6, -3, 0, 3, 6, 9):
        r = ndimage.rotate(pm_small, rot, reshape=False, order=0)
        z = ndzoom(r, s * 0.5, order=0)
        if z.shape[0] >= Hm or z.shape[1] >= Wm or z.sum() < 100:
            continue
        corr = signal.fftconvolve(om.astype(np.float32), z[::-1, ::-1], mode='valid')
        ij = np.unravel_index(np.argmax(corr), corr.shape)
        v = corr[ij] / z.sum()
        if best is None or v > best[0]:
            best = (float(v), float(s), int(rot), int(ij[0]), int(ij[1]))
            print(f'  scale {s:.2f} rot {rot:+d}: score {v:.3f} (offset {ij[1]}мE,{ij[0]}мN)')
print('BEST (score, scale м/px, rot, dy, dx):', best)
json.dump({'best': best, 'rb': RB}, open(f'{OUTD}/nw_inset_6.30.json', 'w'))
