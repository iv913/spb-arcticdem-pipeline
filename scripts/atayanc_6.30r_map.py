#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""6.30r — QA-карта: план + DSM-хиллшейд + здания + отметки + коридор + затопление.
Выход: atayanc_plot_map_6.30.html + PNG-подложки + flood GeoJSON."""
import json
import numpy as np
import rasterio
from rasterio.transform import Affine as RAff
from rasterio.features import rasterize, shapes as rio_shapes
from shapely.geometry import shape, Polygon, mapping
from shapely.ops import unary_union
from PIL import Image
from scipy import ndimage

OUTD = '/tmp/my-project/results/atayanc_6.30'
geo = json.load(open(f'{OUTD}/georef_6.30.json'))
A = RAff(*geo['affine_deg'])
b0, b1, b2, b3 = 30.283288502978362, 59.93759035347949, 30.325441520283814, 59.952163132920404

# ------------------------------------------------ PNG подложек
with rasterio.open(f'{OUTD}/atayanc_plot_dsm_1m_6.30.tif') as ds:
    dsm = ds.read(1).astype(np.float32) / 100.0
    dsm[dsm < -300] = np.nan
aff1 = ds.transform
H1, W1 = dsm.shape

# холлшейд + гипсометрия
gy, gx = np.gradient(np.nan_to_num(dsm, nan=0.0))
slope = np.pi / 2 - np.arctan(np.hypot(gx * 56050, gy * 111320))
az = 315.0
aspect = np.arctan2(-gx * 56050, gy * 111320)
hs = np.clip(np.cos(np.pi / 2 - slope) * np.cos(np.radians(az) - aspect), 0, 1)
hs[dsm < -100] = 0.5
vmin, vmax = np.nanpercentile(dsm, [1, 99])
vmin, vmax = max(vmin, -2), min(vmax, 45)
tn = np.clip((dsm - vmin) / (vmax - vmin), 0, 1)
# палитра: синий->зелёный->жёлтый->коричневый->белый
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
im_h.save(f'{OUTD}/dsm_hill_6.30.png')
print('hillshade png', im_h.size)

im_p = Image.open(f'{OUTD}/atayanc_plan_georef_6.30.tif')
im_p = im_p.resize((1800, int(1800 * im_p.height / im_p.width)), Image.LANCZOS)
im_p.save(f'{OUTD}/plan_web_6.30.png')

# ------------------------------------------------ затопление
water = unary_union([shape(f['geometry']) for f in
                     json.load(open('/tmp/my-project/results/fusion_6.21/inland_water_6.21.geojson'))['features']])
BB = Polygon([(b0, b1), (b2, b1), (b2, b3), (b0, b3)])
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
          open(f'{OUTD}/flood_plot_6.30.geojson', 'w'), ensure_ascii=False)

# ------------------------------------------------ данные для HTML
bmap = json.load(open(f'{OUTD}/plan_buildings_6.30.geojson'))
marks = json.load(open(f'{OUTD}/plan_marks_6.30.geojson'))

def px_ll(x, y):
    return [round(A.c + A.a * x + A.b * y, 6), round(A.f + A.d * x + A.e * y, 6)]

corridor = [px_ll(858, 80), px_ll(1444, 830), px_ll(2430, 1540)]
corridor_ll = [list(reversed(px_ll(858, 80))), list(reversed(px_ll(1444, 830))),
               list(reversed(px_ll(2430, 1540)))]

# здания для карты: только плановые (не osm_only) — компактно
feats_b = []
for f in bmap['features']:
    if f['properties']['src'] == 'osm_only':
        continue
    g = f['geometry']['coordinates'][0]
    if len(g) > 60:
        import math
        step = math.ceil(len(g) / 60)
        g = g[::step] + [g[0]]
    f['geometry']['coordinates'] = [[[round(x, 6), round(y, 6)] for x, y in g]]
    feats_b.append(f)
print('зданий на карту:', len(feats_b))

BOUNDS = [[b1, b0], [b3, b2]]
html = """<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8">
<title>Атаянс 2013 — поверхность участка (6.30)</title>
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
<b>6.30 · Поверхность участка по ситуационному плану «Атаянс-2013»</b>
(коридор видимости Князь-Владимирского собора, Петроградская сторона — стрелка — Дворцовая наб.)
<br>
<label class="lay"><input type="checkbox" id="lPlan" checked> План</label>
<input type="range" id="oPlan" min="0" max="100" value="55" style="width:80px">
<label class="lay"><input type="checkbox" id="lDsm" checked> DSM 6.30 (1 м)</label>
<input type="range" id="oDsm" min="0" max="100" value="65" style="width:80px">
<label class="lay"><input type="checkbox" id="lBld" checked> Здания плана</label>
<label class="lay"><input type="checkbox" id="lMrk" checked> Отметки земли (50)</label>
<label class="lay"><input type="checkbox" id="lCor" checked> Коридор</label>
<label class="lay"><input type="checkbox" id="lFl" checked> Затопление +</label>
<select id="lv"><option>1</option><option>2</option><option selected>3</option><option>4</option><option>5</option></select> м (БС-77)
</div>
<div id="map"></div>
<script>
const BOUNDS = __BOUNDS__;
const map = L.map('map',{zoomControl:true,attributionControl:false}).fitBounds(BOUNDS);
L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}',{maxNativeZoom:16,maxZoom:18}).addTo(map);
L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}',{maxNativeZoom:16,maxZoom:18,opacity:0.85}).addTo(map);
L.control.scale({imperial:false,maxWidth:160}).addTo(map);

const plan = L.imageOverlay('plan_web_6.30.png', BOUNDS, {opacity:0.55});
const dsm  = L.imageOverlay('dsm_hill_6.30.png', BOUNDS, {opacity:0.65});

const bld = L.geoJSON(__BLD__, {
  style: f => { const h=f.properties.h||0;
    const c = h>=30?'#ff8a65': h>=27?'#ffb74d': h>=22?'#fff176': h>=15?'#aed581':'#4fc3f7';
    return {color:'#111', weight:0.6, fillColor:c, fillOpacity:0.75}; },
  onEachFeature: (f,l)=>{ const p=f.properties;
    l.bindPopup(`<div class=pop><b>Здание (план Атаянс)</b><br>Отметка плана: ${p.nums||'—'} м<br>
    h_plan: ${p.h_plan??'—'} · h_osm: ${p.h_osm??'—'} (источник: ${p.src||'—'})<br>
    снап к OSM: ${p.snapped?'да (IoU '+(p.iou??'—')+')':'нет'}<br>
    площадь: ${p.area_m2} м2${p.darkred>0.5?'<br><b>проектное (тёмно-красное)</b>':''}</div>`); }
});
const mrk = L.geoJSON(__MRK__, {
  pointToLayer: (f,l)=>L.circleMarker(l,{radius:5,color:'#fff',weight:1.5,fillColor:'#e53935',fillOpacity:0.95}),
  onEachFeature: (f,l)=>{ const p=f.properties;
    l.bindPopup(`<div class=pop><b>Отметка земли (план)</b><br>БС-77: <b>${p.h_bs77} м</b><br>
    рельеф 6.00 A2: ${p.h_fusion??'—'} м<br>разность: ${p.diff??'—'} м</div>`); }
});
L.polyline(__COR__, {color:'#42a5f5',weight:3,opacity:0.9,dashArray:'8,6'})
  .bindPopup('Коридор видимости собора (по плану)');

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
  const base=L.layerGroup();
  const s=document.getElementById('lFl').checked?setFlood(document.getElementById('lv').value):flGroup.clearLayers();
}
function apply(){
  if(document.getElementById('lPlan').checked) plan.addTo(map); else map.removeLayer(plan);
  if(document.getElementById('lDsm').checked) dsm.addTo(map); else map.removeLayer(dsm);
  if(document.getElementById('lBld').checked) bld.addTo(map); else map.removeLayer(bld);
  if(document.getElementById('lMrk').checked) mrk.addTo(map); else map.removeLayer(mrk);
  if(document.getElementById('lCor').checked) cor.addTo(map); else map.removeLayer(cor);
  if(document.getElementById('lFl').checked) flGroup.addTo(map); else map.removeLayer(flGroup);
  sync();
}
plan.setOpacity(0.55); dsm.setOpacity(0.65);
const cor = L.polyline(__COR__, {color:'#42a5f5',weight:3,opacity:0.9,dashArray:'8,6'});
dsm.addTo(map); plan.addTo(map); bld.addTo(map); mrk.addTo(map); cor.addTo(map); flGroup.addTo(map);
document.getElementById('lPlan').onchange=apply; document.getElementById('lDsm').onchange=apply;
document.getElementById('lBld').onchange=apply; document.getElementById('lMrk').onchange=apply;
document.getElementById('lCor').onchange=apply;
document.getElementById('lFl').onchange=apply; document.getElementById('lv').onchange=sync;
document.getElementById('oPlan').oninput=sync; document.getElementById('oDsm').oninput=sync;
</script></body></html>"""
html = html.replace('__BOUNDS__', json.dumps(BOUNDS))
html = html.replace('__BLD__', json.dumps({'type': 'FeatureCollection', 'features': feats_b},
                                          ensure_ascii=False, separators=(',', ':')))
html = html.replace('__MRK__', json.dumps(marks, ensure_ascii=False, separators=(',', ':')))
html = html.replace('__COR__', json.dumps(corridor_ll))
html = html.replace('__FLOOD__', json.dumps({str(k): v for k, v in floods.items()},
                                            ensure_ascii=False, separators=(',', ':')))
open(f'{OUTD}/atayanc_plot_map_6.30.html', 'w').write(html)
import os
print('HTML:', os.path.getsize(f'{OUTD}/atayanc_plot_map_6.30.html') // 1024, 'КБ')
print('ГОТОВО 6.30r')
