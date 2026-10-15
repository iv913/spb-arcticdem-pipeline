#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""6.50e — карта поверхности 6.50: план + DSM-хиллшейд + здания + 332 отметки +
затопление +1..+5 (суша по воде OSM 6.21). Выход: atayanc_plot_map_6.50.html."""
import json
import numpy as np
import rasterio
from rasterio.features import rasterize, shapes as rio_shapes
from shapely.geometry import shape, Polygon, mapping
from shapely.ops import unary_union
from PIL import Image
from scipy import ndimage

OUTD = '/tmp/my-project/results/atayanc_6.50'
D50 = '/home/z/my-project/download/atayanc_6.50'
geo = json.load(open('/home/z/my-project/download/atayanc_6.30/georef_6.30.json'))
PB = [30.283288502978362, 59.93759035347949, 30.325441520283814, 59.952163132920404]

with rasterio.open(f'{OUTD}/atayanc_plot_dsm_1m_6.50.tif') as ds:
    dsm = ds.read(1).astype(np.float32) / 100.0
    dsm[dsm < -300] = np.nan
    aff1 = ds.transform
    bnds = ds.bounds
H1, W1 = dsm.shape
DB = [[bnds.bottom, bnds.left], [bnds.top, bnds.right]]
print('DSM bounds:', bnds)

gy, gx = np.gradient(np.nan_to_num(dsm, nan=0.0))
slope = np.pi / 2 - np.arctan(np.hypot(gx * 56050, gy * 111320))
az = 315.0
aspect = np.arctan2(-gx * 56050, gy * 111320)
hs = np.clip(np.cos(np.pi / 2 - slope) * np.cos(np.radians(az) - aspect), 0, 1)
hs[dsm < -100] = 0.5
vmin = 0.3
vmax = min(float(np.nanpercentile(dsm, 90)), 30.0)
tn = np.clip((dsm - vmin) / (vmax - vmin), 0, 1)
stops = [(0.0, (40, 70, 140)), (0.18, (60, 130, 90)), (0.38, (120, 170, 90)),
         (0.55, (220, 220, 130)), (0.75, (190, 140, 90)), (1.0, (250, 250, 250))]
rgbh = np.zeros((*tn.shape, 3), np.uint8)
for i in range(len(stops) - 1):
    t0, c0 = stops[i]
    t1, c1 = stops[i + 1]
    m = (tn >= t0) & (tn <= t1)
    k = (tn[m] - t0) / (t1 - t0 + 1e-9)
    for ch in range(3):
        rgbh[..., ch][m] = (c0[ch] + (c1[ch] - c0[ch]) * k).astype(np.uint8)
rgbh[~np.isfinite(dsm)] = (0, 0, 0)
out = (rgbh * (0.35 + 0.65 * hs[..., None])).astype(np.uint8)
alpha = np.where(np.isfinite(dsm) & (dsm >= 0.3), 255, 0).astype(np.uint8)
rgba = np.dstack([out, alpha])
im_h = Image.fromarray(rgba, 'RGBA').resize((1800, int(1800 * H1 / W1)), Image.LANCZOS)
im_h.save(f'{OUTD}/dsm_hill_6.50.png')
print('hillshade png', im_h.size)

im_p = Image.open('/home/z/my-project/download/atayanc_6.30/atayanc_plan_georef_6.30.tif')
im_p = im_p.resize((1800, int(1800 * im_p.height / im_p.width)), Image.LANCZOS)
im_p.save(f'{OUTD}/plan_web_6.50.png')

# ------------------------------------------------ затопление
water = unary_union([shape(f['geometry']) for f in
                     json.load(open('/tmp/my-project/results/fusion_6.21/inland_water_6.21.geojson'))['features']])
BB = Polygon([(PB[0], PB[1]), (PB[2], PB[1]), (PB[2], PB[3]), (PB[0], PB[3])])
land = BB.difference(water)
land_m = rasterize([(mapping(land), 1)], out_shape=(H1, W1), transform=aff1).astype(bool)

floods = {}
for lv in (1, 2, 3, 4, 5):
    m = (dsm <= lv) & land_m
    m &= ~np.isnan(dsm)
    lab, n = ndimage.label(m)
    if n:
        sizes = ndimage.sum(m, lab, range(1, n + 1))
        keep = np.zeros(n + 1, bool)
        keep[1:] = sizes >= 300
        m2 = keep[lab]
    else:
        m2 = m
    feats = []
    for g, v in rio_shapes(m2.astype(np.uint8), mask=m2, transform=aff1):
        p = shape(g)
        if p.is_empty or p.area < 2e-11:
            continue
        p = p.simplify(3e-5)
        if p.is_empty or p.geom_type != 'Polygon':
            continue
        feats.append({'type': 'Feature', 'properties': {'level': lv,
                     'area_m2': round(p.area * 56050 * 111320, 0)},
                     'geometry': mapping(p)})
    floods[lv] = feats
    print(f'затопление +{lv}: {len(feats)} полигонов, '
          f'{sum(f["properties"]["area_m2"] for f in feats)/1e6:.3f} км2')
json.dump({str(k): v for k, v in floods.items()},
          open(f'{OUTD}/flood_plot_6.50.geojson', 'w'), ensure_ascii=False)

bmap = json.load(open('/home/z/my-project/download/atayanc_6.30/plan_buildings_6.30.geojson'))
marks = json.load(open(f'{D50}/plan_marks_6.50.geojson'))

feats_b = []
import math
for f in bmap['features']:
    if f['properties']['src'] == 'osm_only':
        continue
    g = f['geometry']['coordinates'][0]
    if len(g) > 60:
        step = math.ceil(len(g) / 60)
        g = g[::step] + [g[0]]
    f['geometry']['coordinates'] = [[[round(x, 6), round(y, 6)] for x, y in g]]
    feats_b.append(f)
print('зданий на карту:', len(feats_b))

PLAN_BOUNDS = [[PB[1], PB[0]], [PB[3], PB[2]]]
html = """<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8">
<title>Атаянс 2013 — поверхность участка (6.50)</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
html,body{margin:0;height:100%;background:#0f141a;font-family:system-ui,Segoe UI,Roboto,sans-serif}
#map{height:calc(100% - 132px)}
#panel{color:#cfd8dc;background:#131a22;padding:8px 14px;font-size:13px}
#panel b{color:#fff}
.lay{margin-right:14px}
.leaflet-container{background:#0f141a}
.leaflet-control-attribution{display:none!important}
.pop{font-size:12px}
</style></head><body>
<div id="panel">
<b>6.50 · Поверхность участка по плану «Атаянс-2013» — перепричёсано</b>
(332 отметки, 117 цифр исправлено после вычитки апскейла; DTM = fusion A2 + коррекционное поле по отметкам)
<br>
<label class="lay"><input type="checkbox" id="lPlan" checked> План</label>
<input type="range" id="oPlan" min="0" max="100" value="55" style="width:80px">
<label class="lay"><input type="checkbox" id="lDsm" checked> DTM/DSM 6.50 (1 м)</label>
<input type="range" id="oDsm" min="0" max="100" value="65" style="width:80px">
<label class="lay"><input type="checkbox" id="lBld" checked> Здания</label>
<label class="lay"><input type="checkbox" id="lMrk" checked> Отметки земли (332)</label>
<label class="lay"><input type="checkbox" id="lFl" checked> Затопление +</label>
<select id="lv"><option>1</option><option>2</option><option selected>3</option><option>4</option><option>5</option></select> м (БС-77)
</div>
<div id="map"></div>
<script>
const map = L.map('map',{zoomControl:true,attributionControl:false}).fitBounds(__DSMB__);
L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}',{maxNativeZoom:16,maxZoom:18}).addTo(map);
L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}',{maxNativeZoom:16,maxZoom:18,opacity:0.85}).addTo(map);
L.control.scale({imperial:false,maxWidth:160}).addTo(map);

const plan = L.imageOverlay('plan_web_6.50.png', __PLANB__, {opacity:0.55});
const dsm  = L.imageOverlay('dsm_hill_6.50.png', __DSMB__, {opacity:0.65});

const bld = L.geoJSON(__BLD__, {
  style: f => { const h=f.properties.h||0;
    const c = h>=30?'#ff8a65': h>=27?'#ffb74d': h>=22?'#fff176': h>=15?'#aed581':'#4fc3f7';
    return {color:'#111', weight:0.6, fillColor:c, fillOpacity:0.75}; },
  onEachFeature: (f,l)=>{ const p=f.properties;
    l.bindPopup(`<div class=pop><b>Здание</b><br>Отметка плана: ${p.nums||'—'} м<br>
    h_plan: ${p.h_plan??'—'} · h_osm: ${p.h_osm??'—'} (${p.src||'—'})<br>
    площадь: ${p.area_m2} м2</div>`); }
});
const mrk = L.geoJSON(__MRK__, {
  pointToLayer: (f,l)=>L.circleMarker(l,{radius: f.properties.fixed_650?6:5,
      color: f.properties.conf==='H'?'#00e676': f.properties.conf==='M'?'#40c4ff':'#ffd54f',
      weight:1.5, fillColor:'#e53935', fillOpacity:0.95}),
  onEachFeature: (f,l)=>{ const p=f.properties;
    l.bindPopup(`<div class=pop><b>Отметка земли ${p.id}</b>${p.fixed_650?' (исправлено в 6.50)':''}<br>
    БС-77: <b>${p.h_bs77} м</b> · ув.: ${p.conf}<br>
    fusion A2 (до коррекции): ${p.fusion_a2??'—'} м<br>разность: ${p.diff??'—'} м</div>`); }
});

const floods = __FLOOD__;
const flGroup = L.layerGroup();
function setFlood(lv){
  flGroup.clearLayers();
  L.geoJSON({type:'FeatureCollection',features:floods[lv]||[]},{
    style:{color:'#29b6f6',weight:1,fillColor:'#29b6f6',fillOpacity:0.45},
    onEachFeature:(f,l)=>l.bindPopup(`<div class=pop>Затопление +${f.properties.level} м БС-77<br>площадь: ${f.properties.area_m2} м2</div>`)
  }).addTo(flGroup);
}
setFlood('3');

function sync(){
  const p=document.getElementById('lPlan').checked, d=document.getElementById('lDsm').checked;
  if(p) plan.setOpacity(document.getElementById('oPlan').value/100);
  if(d) dsm.setOpacity(document.getElementById('oDsm').value/100);
  if(document.getElementById('lFl').checked) setFlood(document.getElementById('lv').value);
  else flGroup.clearLayers();
}
function apply(){
  if(document.getElementById('lPlan').checked) plan.addTo(map); else map.removeLayer(plan);
  if(document.getElementById('lDsm').checked) dsm.addTo(map); else map.removeLayer(dsm);
  if(document.getElementById('lBld').checked) bld.addTo(map); else map.removeLayer(bld);
  if(document.getElementById('lMrk').checked) mrk.addTo(map); else map.removeLayer(mrk);
  if(document.getElementById('lFl').checked) flGroup.addTo(map); else map.removeLayer(flGroup);
  sync();
}
plan.setOpacity(0.55); dsm.setOpacity(0.65);
dsm.addTo(map); plan.addTo(map); bld.addTo(map); mrk.addTo(map); flGroup.addTo(map);
document.getElementById('lPlan').onchange=apply; document.getElementById('lDsm').onchange=apply;
document.getElementById('lBld').onchange=apply; document.getElementById('lMrk').onchange=apply;
document.getElementById('lFl').onchange=apply; document.getElementById('lv').onchange=sync;
document.getElementById('oPlan').oninput=sync; document.getElementById('oDsm').oninput=sync;
</script></body></html>"""
html = html.replace('__DSMB__', json.dumps(DB))
html = html.replace('__PLANB__', json.dumps(PLAN_BOUNDS))
html = html.replace('__BLD__', json.dumps({'type': 'FeatureCollection', 'features': feats_b},
                                          ensure_ascii=False, separators=(',', ':')))
html = html.replace('__MRK__', json.dumps(marks, ensure_ascii=False, separators=(',', ':')))
html = html.replace('__FLOOD__', json.dumps({str(k): v for k, v in floods.items()},
                                            ensure_ascii=False, separators=(',', ':')))
open(f'{OUTD}/atayanc_plot_map_6.50.html', 'w').write(html)
import os
print('HTML:', os.path.getsize(f'{OUTD}/atayanc_plot_map_6.50.html') // 1024, 'КБ')
print('ГОТОВО 6.50e')
