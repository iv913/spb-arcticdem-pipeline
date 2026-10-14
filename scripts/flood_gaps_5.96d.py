#!/usr/bin/env python3
"""5.96d v2: «пропущенные сектора» на карте затоплений = крупные nodata-области
footprint мозаики 5.90, окружённые полигонами затопления (на карте выглядят
тёмными прямоугольниками без оранжевой заливки). v1 ошибочно искал дыры только
внутри полигонов — модель «чаша» уже исключает nodata, поэтому там пусто."""
import json

import numpy as np
import rasterio
from affine import Affine
from rasterio.features import rasterize
from scipy import ndimage

MOS = '/tmp/my-project/gh-clone/download/mosaic_5.90/spb_hybrid_2m_mosaic_5.90.tif'
GJ = '/tmp/my-project/results/flood_zones_5.96/flood_levels_5.96.geojson'
OUT_PNG = '/home/z/my-project/download/flood_map_gaps_5.96d.png'
OUT_JSON = '/tmp/my-project/results/flood_zones_5.96/gap_sectors_5.96d.json'

DS = 16
MIN_KM2 = 0.25

with rasterio.open(MOS) as src:
    rows, cols = src.height // DS, src.width // DS
    arr = src.read(1, out_shape=(rows, cols))
    tr = src.transform * Affine.scale(src.width / cols, src.height / rows)
    nod = (arr == -32768)

m_per_deg_lat = 110574.0
m_per_deg_lon = 111320.0 * np.cos(np.radians(59.9))
cell_km2 = abs(tr.a) * m_per_deg_lon * abs(tr.e) * m_per_deg_lat / 1e6
print(f'grid {cols}x{rows}, cell {cell_km2*1e6:.0f} m2, nodata {nod.mean()*100:.1f}%')

gj = json.load(open(GJ))
f5 = [f['geometry'] for f in gj['features'] if f['properties']['level_m'] == 5.0]
flood = rasterize([(g, 1) for g in f5], out_shape=(rows, cols), transform=tr).astype(bool)
flood_km2 = flood.sum() * cell_km2
print(f'flood+5.0: {flood_km2:.1f} km2 (stats say 118.8)')

lab_buf = ndimage.binary_dilation(flood, iterations=16)  # буфер ~260 м вокруг зон
lab, n = ndimage.label(nod & lab_buf)
st = ndimage.generate_binary_structure(2, 2)
objs = ndimage.find_objects(lab)
comps = []
for sl, i in zip(objs, range(1, n + 1)):
    npix = int((lab[sl] == i).sum())
    area = npix * cell_km2
    if area < MIN_KM2:
        continue
    r0, r1 = max(0, sl[0].start - 3), min(rows, sl[0].stop + 3)
    c0, c1 = max(0, sl[1].start - 3), min(cols, sl[1].stop + 3)
    ring = (lab[r0:r1, c0:c1] == i)
    ring_d = ndimage.binary_dilation(ring, st, iterations=3) & ~ring
    nb_val = (ring_d & ~nod[r0:r1, c0:c1]).sum()
    nb_flood = (ring_d & flood[r0:r1, c0:c1]).sum()
    adj = nb_flood / max(1, nb_val)
    lon0, lat1 = tr * (sl[1].start, sl[0].start)
    lon1, lat0 = tr * (sl[1].stop, sl[0].stop)
    comps.append(dict(area=round(area, 2), adj=round(float(adj), 2),
                      lon0=round(lon0, 4), lon1=round(lon1, 4),
                      lat0=round(lat0, 4), lat1=round(lat1, 4),
                      clat=round((lat0 + lat1) / 2, 4), clon=round((lon0 + lon1) / 2, 4)))
comps.sort(key=lambda c: -c['area'])
print(f'компонент >= {MIN_KM2} km2: {len(comps)}; топ-12:')
for i, c in enumerate(comps[:12], 1):
    print(f"{i:2d}. {c['area']:7.2f} km2  окружено затоплением на {c['adj']*100:3.0f}%  "
          f"lon [{c['lon0']:.4f},{c['lon1']:.4f}] lat [{c['lat0']:.4f},{c['lat1']:.4f}]  "
          f"center ({c['clat']:.4f},{c['clon']:.4f})")

json.dump(comps, open(OUT_JSON, 'w'), ensure_ascii=False, indent=1)

# ---- именованные прямоугольники (опорные пропущенные сектора) ----
def rect_stats(name, lon_a, lon_b, lat_a, lat_b):
    c0 = int((lon_a - tr.c) / tr.a)
    c1 = int((lon_b - tr.c) / tr.a)
    r0 = int((lat_b - tr.f) / tr.e)
    r1 = int((lat_a - tr.f) / tr.e)
    sub_nod = nod[r0:r1, c0:c1]
    sub_flood = flood[r0:r1, c0:c1]
    km2 = sub_nod.size * cell_km2
    print(f'  {name}: nodata {sub_nod.mean()*100:5.1f}% ({sub_nod.sum()*cell_km2:6.1f} км²), '
          f'затопление+5 {sub_flood.mean()*100:4.1f}%')
    return dict(name=name, lon=(lon_a, lon_b), lat=(lat_a, lat_b),
                nod_pct=round(float(sub_nod.mean() * 100), 1),
                nod_km2=round(float(sub_nod.sum() * cell_km2), 1),
                flood_pct=round(float(sub_flood.mean() * 100), 1))

print('Опорные сектора (доля nodata в прямоугольнике):')
rects = [
    ('Васильевский остров: Гавань — остров Декабристов', 30.200, 30.313, 59.931, 59.965),
    ('Васильевский остров: север (Наличная — Морской фасад)', 30.208, 30.300, 59.955, 59.966),
    ('Полюстрово — Бестужевка — Ржевка (Выборгский/Красногвард.)', 30.313, 30.440, 59.955, 60.020),
    ('Малая Охта — правый берег Невы', 30.340, 30.470, 59.940, 59.970),
    ('Купчино север (Фрунзенский р-н)', 30.380, 30.470, 59.880, 59.920),
    ('Кронштадт: запад острова Котлин', 29.730, 29.840, 59.980, 60.010),
    ('Сестрорецк — Тарховка — Дюны', 29.900, 30.000, 60.020, 60.100),
    ('Лисий Нос — Знаменка (северный берег)', 30.150, 30.220, 59.970, 60.020),
    ('Стрельна — Урицк (юго-запад, вне скриншота)', 29.950, 30.130, 59.790, 59.870),
]
rect_stats_list = [rect_stats(*r) for r in rects]
json.dump(rect_stats_list, open(OUT_JSON.replace('.json', '_rects.json'), 'w'),
          ensure_ascii=False, indent=1)

# ---- аннотированный PNG (v3) ----
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

img = np.zeros((rows, cols, 3), dtype=np.uint8)
img[nod] = (100, 112, 128)          # нет данных — светлый сине-серый
img[~nod] = (26, 29, 34)            # валидная суша без затопления — почти чёрный
img[flood & ~nod] = (224, 82, 52)   # зона затопления +5 — оранжевый
lon_min, lat_max = tr * (0, 0)
lon_max, lat_min = tr * (cols, rows)
extent = [lon_min, lon_max, lat_min, lat_max]

fig, ax = plt.subplots(figsize=(15, 15 * rows / cols), constrained_layout=True)
ax.imshow(img, extent=extent, origin='upper', interpolation='nearest')
ax.set_facecolor('#14161a')
for i, r in enumerate(rect_stats_list, 1):
    lon_a, lon_b = r['lon']
    lat_a, lat_b = r['lat']
    ax.add_patch(Rectangle((lon_a, lat_a), lon_b - lon_a, lat_b - lat_a,
                           fill=False, ec='#ffd166', lw=1.4, ls='--'))
    ax.text((lon_a + lon_b) / 2, (lat_a + lat_b) / 2, str(i), color='#ffd166',
            fontsize=13, ha='center', va='center', weight='bold',
            bbox=dict(fc='#000000dd', ec='#ffd166', lw=0.8, pad=2.2))
legend = '\n'.join(f"{i}. {r['name']} — nodata {r['nod_pct']}% ({r['nod_km2']} км²)"
                   for i, r in enumerate(rect_stats_list, 1))
ax.text(0.012, 0.985, legend, transform=ax.transAxes, color='#e8ecf2', fontsize=8.6,
        ha='left', va='top', linespacing=1.45,
        bbox=dict(fc='#0f141ae0', ec='#2a3441', pad=4))
ax.set_xlim(29.36, lon_max)
ax.set_ylim(lat_max, lat_min)
ax.set_title('Затопление +5.0 м (оранжевый) · валидная суша без затопления (тёмная) · «пропущенные сектора» = нет данных ЦМР (серо-голубой)\n'
             'жёлтый пунктир — опорные окна, где плотность стрипов ArcticDEM 2 м ниже порога тиража (dens ≥ 6)',
             color='#e8ecf2', fontsize=10.5)
ax.tick_params(colors='#8a93a3', labelsize=8)
for s in ax.spines.values():
    s.set_color('#333a44')
fig.patch.set_facecolor('#14161a')
fig.savefig(OUT_PNG, dpi=130, facecolor=fig.get_facecolor())
print('PNG:', OUT_PNG)
