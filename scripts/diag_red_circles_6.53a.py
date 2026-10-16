#!/usr/bin/env python3
# 6.53a — диагностика красных обводок на карте 6.52 (+1 м):
#  Биржевая пл. (~59.9405-59.9465, 30.293-30.312) и Университетская наб. (~59.9335-59.9425, 30.272-30.303)
# Для каждой части уровня 1.0: площадь, расстояние до воды OSM, статистика fusion A2 внутри,
# источник (база 5.96 barrier16 / fill A1-A4 barrier16).
import json, numpy as np
from shapely.geometry import shape, box
from shapely.ops import unary_union
import rasterio
from rasterio.warp import transform as wtrans

B16 = '/tmp/my-project/results/fusion_6.52/flood_levels_5.96_barrier16.geojson'
FILL = {a: f'/tmp/my-project/results/fusion_6.52/fill_zones_6.20_A{a}_barrier16.geojson' for a in '1234'}
SEA  = '/tmp/my-project/results/fusion_6.20/osm_water_6.20.geojson'
INL  = '/tmp/my-project/results/fusion_6.21/inland_water_6.21.geojson'
FUSE = '/tmp/my-project/download/mosaic_6.00/spb_terra_fusion_6.00_A2.tif'  # Int16 см, 2 м

# circle-боксы (lat, lon) по скриншоту пользователя
CIRCLES = {
    'C2_Биржевая':  box(30.293, 59.9405, 30.312, 59.9465),
    'C3_Университетская': box(30.272, 59.9335, 30.303, 59.9425),
}

def parts(feat):
    g = shape(feat['geometry'])
    geoms = g.geoms if g.geom_type == 'MultiPolygon' else [g]
    return geoms

def stats_ds(rast, g):
    """статистика fusion (см -> м) внутри геометрии g (по пикселям, чей центр внутри)"""
    if g.bounds[2] < rast.bounds[0] or g.bounds[0] > rast.bounds[2] or \
       g.bounds[3] < rast.bounds[1] or g.bounds[1] > rast.bounds[3]:
        return None
    from rasterio.features import geometry_mask
    win = rasterio.windows.from_bounds(*g.buffer(0.0005).bounds, transform=rast.transform)
    win = win.round_offsets().round_lengths()
    if win.width < 1 or win.height < 1: return None
    data = rast.read(1, window=win, boundless=True, fill_value=-9999)
    wt = rasterio.windows.transform(win, rast.transform)
    m = geometry_mask([g], out_shape=(int(win.height), int(win.width)), transform=wt, invert=True)
    v = data[m & (data > -9000)].astype('float64')
    if v.size == 0: return None
    return dict(n=int(v.size), med=float(np.median(v))/100, p10=float(np.percentile(v,10))/100,
                p90=float(np.percentile(v,90))/100, mx=float(v.max())/100)

print('LOAD...')
MLAT=111132.0; DEG=111320.0*np.cos(np.radians(59.945))
def m2_area(g): return g.area*MLAT*DEG
ds = rasterio.open(FUSE)
sea  = unary_union([shape(f['geometry']).buffer(0) for f in json.load(open(SEA))['features']])
inl  = unary_union([shape(f['geometry']).buffer(0) for f in json.load(open(INL))['features']])
water = unary_union([sea, inl])
print(f'water: sea {sea.area/1e6:.0f} км2, inland {inl.area/1e6:.0f} км2')

rows = []
d = json.load(open(B16))
for f in d['features']:
    if abs(f['properties'].get('level_m',0) - 1.0) > 1e-6: continue
    for i,g in enumerate(parts(f)):
        if m2_area(g) < 400: continue
        src='base5.96'
        rows.append((src, g, f"{f['properties']['level_m']:.1f}", i))
for a in '1234':
    try: fa = json.load(open(FILL[a]))
    except Exception as e: print('fill err', a, e); continue
    feats = fa['features'] if isinstance(fa,dict) and 'features' in fa else (fa if isinstance(fa,list) else [v for k,v in fa.items() if k!='type' and isinstance(v,list)][0])
    for f in feats:
        lv = f['properties'].get('level_m', f['properties'].get('level',-1))
        if abs(float(lv)-1.0) > 1e-6: continue
        for i,g in enumerate(parts(f)):
            if m2_area(g) < 400: continue
            rows.append((f'fillA{a}', g, f'{float(lv):.1f}', i))
print(f'parts @+1м (>=400 м2): {len(rows)}')

def dist_w(g): 
    try: return g.distance(water)*MLAT
    except Exception: return -1

# все части + флаг в круге
print('\n=== ВСЕ ЧАСТИ +1 м (>=400 м2) ===')
hdr=f"{'src':9s} {'i':>3s} {'area_m2':>8s} {'d_water':>8s} {'lon':>9s} {'lat':>9s} {'med':>5s} {'p10':>5s} {'p90':>5s} circle"
print(hdr)
tot=0.0; tot_c=0.0
for src,g,lv,i in sorted(rows, key=lambda r:-m2_area(r[1])):
    a=m2_area(g); tot+=a
    c=g.centroid; dw=dist_w(g)
    s=stats_ds(ds,g) or {}
    inc=[]
    for nm,bb in CIRCLES.items():
        if g.intersects(bb): inc.append(nm.split('_')[0])
    tot_c += a if inc else 0
    print(f"{src:9s} {i:>3d} {a:>8.0f} {dw:>8.1f} {c.x:>9.5f} {c.y:>9.5f} "
          f"{s.get('med',float('nan')):>5.2f} {s.get('p10',float('nan')):>5.2f} {s.get('p90',float('nan')):>5.2f} {','.join(inc)}")
print(f'\nTOTAL {tot/1e6:.3f} км2, в кругах {tot_c/1e6:.3f} км2')

# детали кругов: разбивка тонких лент (периметр/площадь)
print('\n=== ДЕТАЛИ ЧАСТЕЙ В КРУГАХ ===')
for src,g,lv,i in rows:
    if not any(g.intersects(bb) for bb in CIRCLES.values()): continue
    a=m2_area(g); dw=dist_w(g)
    if a < 1000: continue
    # ширина ленты ~ area / (периметр/2)
    w = 2*a/max(g.length*MLAT,1e-9)
    print(f'{src} i={i} area={a:.0f} м2 d_water={dw:.1f} м лента_ширина~{w:.1f} м')

# сколько всего +1 м в полосе 0-45 м от воды (кандидат на барьер 40)
bands=[(16,25),(25,35),(35,45),(45,60)]
print('\n=== ПЛОЩАДЬ +1 м ПО ПОЛОСАМ РАССТОЯНИЯ ДО ВОДЫ ===')
for lo,hi in bands:
    s=0.0
    for src,g,lv,i in rows:
        dw=dist_w(g)
        if lo<=dw<hi: s+=m2_area(g)
    print(f'{lo}-{hi} м: {s/1e6:.3f} км2')
