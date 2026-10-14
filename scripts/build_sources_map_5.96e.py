#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
5.96e — Интерактивная карта ИСТОЧНИКОВ данных DEM по СПб.
Слои: ATL06 канон v4 (1.06M, сабсэмпл), ATL06 юг, отметки НСПД/полевые,
стрипы ArcticDEM (план 5.84 + STAC s2s041 над эталоном), зоны тиража,
пропуска 5.96d, эталонный коридор Большого пр. В.О.
Подложка Esri World Dark Gray (как 5.96c v2). Выход: download/spb_sources_map_5.96e.html
"""
import json, csv, gzip, glob, math, random
import rasterio

GH = '/tmp/my-project/gh-clone'
OUT = f'{GH}/download/spb_sources_map_5.96e.html'
random.seed(96)

CITY = (29.40, 59.65, 30.70, 60.25)          # W,S,E,N
REF  = (30.19, 59.895, 30.31, 59.97)         # эталонное окно (полная плотность точек)

# ------------------------------------------------------------- sources data
def load_canon():
    """canon v4 unified: в REF — все точки, вне — каждый 14-й."""
    pts, dates = [], {}
    keep_out, keep_ref, tot = 0, 0, 0
    with gzip.open(f'{GH}/scripts/dem_cache/densify_canon_5.93/unified.csv.gz', 'rt') as fh:
        rd = csv.DictReader(fh)
        for r in rd:
            la, lo = float(r['lat']), float(r['lon'])
            tot += 1
            if not (CITY[0] <= lo <= CITY[2] and CITY[1] <= la <= CITY[3]):
                continue
            inref = REF[0] <= lo <= REF[2] and REF[1] <= la <= REF[3]
            if not inref:
                keep_out += 1
                if keep_out % 14:
                    continue
            else:
                keep_ref += 1
            d = r['date'][:6]
            if d not in dates:
                dates[d] = len(dates)
            pts.append([round(lo, 5), round(la, 5), dates[d], int(r['rgt']), 1 if r['cnf_src'] == 'high' else 0])
    return pts, sorted(dates), tot

def load_south():
    pts, dates = [], {}
    n = 0
    with gzip.open(f'{GH}/results/south_dump_val_5.90.csv.gz', 'rt') as fh:
        rd = csv.DictReader(fh)
        for r in rd:
            la, lo = float(r['lat']), float(r['lon'])
            if not (CITY[0] <= lo <= CITY[2] and CITY[1] <= la <= CITY[3]):
                continue
            n += 1
            if n % 5:
                continue
            d = r['date'][:6]
            if d not in dates:
                dates[d] = len(dates)
            try:
                dh = round(float(r['dH2m']), 2)
            except Exception:
                dh = None
            pts.append([round(lo, 5), round(la, 5), dates[d], dh])
    return pts, sorted(dates)

def load_marks():
    out = []
    for r in csv.DictReader(open(f'{GH}/results/marks_val_5.95.csv')):
        try:
            la, lo = float(r['lat']), float(r['lon'])
        except Exception:
            continue
        if not (CITY[0] <= lo <= CITY[2] and CITY[1] <= la <= CITY[3]):
            continue
        dh = r['dH']
        out.append([round(lo, 6), round(la, 6), r['src'], r['district'][:48],
                    round(float(r['z']), 2) if r['z'] else None,
                    round(float(dh), 2) if dh else None, r['note'][:40]])
    return out

def load_zone_bounds():
    zones = []
    for f in sorted(glob.glob(f'{GH}/download/products_5.83/hybrid_2m_*.tif')):
        tag = f.split('hybrid_2m_5.83_')[-1].replace('.tif', '')
        try:
            with rasterio.open(f) as s:
                b = s.bounds
                zones.append({'tag': tag, 'bbox': [round(b.left, 4), round(b.bottom, 4),
                                                   round(b.right, 4), round(b.top, 4)]})
        except Exception:
            pass
    verd = {}
    for line in open(f'{GH}/results/batch_progress_5.83.jsonl'):
        try:
            j = json.loads(line)
        except Exception:
            continue
        t = j.get('tag', '').replace('_5.83', '')
        verd[t] = {'product': (j.get('rule') or {}).get('reason', j.get('product', '')),
                   'use_hybrid': (j.get('rule') or {}).get('use_hybrid'),
                   'mad': (j.get('prior_holdout') or {}).get('mad')}
    for z in zones:
        v = verd.get(z['tag'].replace('_5.83', ''))
        if v:
            z['verdict'] = v
    return zones

print('loading canon...')
canon, canon_dates, canon_tot = load_canon()
print('canon kept', len(canon), '/', canon_tot)
south, south_dates = load_south()
print('south kept', len(south))
marks = load_marks()
zones = load_zone_bounds()
strips_city = json.load(open(f'{GH}/results/density_footprints_5.84.json'))
strips_ref = json.load(open('/tmp/strips_over_ref.json'))
carr = json.load(open(f'{GH}/download/carriageway.geojson'))
gaps = json.load(open(f'{GH}/results/flood_zones_5.96/gap_sectors_5.96d_rects.json'))
json.dump({'canon_tot': canon_tot, 'canon_kept': len(canon), 'south_kept': len(south),
           'marks': len(marks), 'zones': len(zones)},
          open(f'{GH}/results/sources_map_counts_5.96e.json', 'w'))

# компактные rect-слои
def rects(lst):
    return [[[round(w, 4), round(s, 4)], [round(e, 4), round(n, 4)]] for w, s, e, n in lst]

DATA = {
    'canon': canon, 'canonDates': canon_dates,
    'south': south, 'southDates': south_dates,
    'marks': marks, 'zones': zones,
    'stripsCity': rects(strips_city),
    'stripsRef': strips_ref,
    'carr': carr['features'][0]['geometry']['coordinates'],
    'gaps': gaps,
}

HTML = """<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>СПб — источники данных DEM · 5.96e</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
html,body{margin:0;height:100%;background:#0f141a;color:#dfe7ef;
 font:13px/1.45 -apple-system,'Segoe UI',Roboto,Arial,sans-serif}
#map{position:absolute;inset:0;background:#0f141a}
.leaflet-control-attribution{display:none!important}
#panel{position:absolute;top:10px;left:10px;z-index:1000;width:344px;max-height:94%;
 overflow:auto;background:rgba(13,18,24,.93);border:1px solid #2a3644;border-radius:10px;
 padding:12px 14px;box-shadow:0 4px 18px rgba(0,0,0,.5)}
#panel h1{font-size:15.5px;margin:0 0 2px;color:#ffd54f}
#panel .sub{color:#8fa3b8;font-size:11.5px;margin-bottom:8px}
.sect{border-top:1px solid #223041;margin-top:9px;padding-top:7px}
.lbl{font-size:12px;color:#cfe3f5;margin:3px 0 2px;font-weight:600}
.leg{display:flex;align-items:center;gap:6px;margin:2.5px 0;font-size:12px;cursor:pointer;user-select:none}
.leg input{accent-color:#4fc3f7}
.sw{width:13px;height:13px;border-radius:3px;flex:0 0 13px}
table{border-collapse:collapse;width:100%;font-size:10.8px;margin-top:4px}
th,td{border:1px solid #223041;padding:2.5px 4px;text-align:right;white-space:nowrap}
th{color:#8fa3b8;font-weight:600}td:first-child,th:first-child{text-align:left}
.note{font-size:11.3px;color:#a8bacc;margin:4px 0}
.note b{color:#ffd54f}
.leaflet-popup-content-wrapper,.leaflet-popup-tip{background:#141c26;color:#dfe7ef}
.leaflet-popup-content{font-size:12px;margin:8px 12px}
.leaflet-control-scale-line{background:rgba(13,18,24,.8);color:#cfe3f5;border-color:#3a4a5c}
#btn{position:absolute;top:10px;left:368px;z-index:1000;background:rgba(13,18,24,.93);
 color:#cfe3f5;border:1px solid #2a3644;border-radius:8px;padding:6px 10px;cursor:pointer;font-size:12px}
</style></head><body>
<div id="map"></div>
<button id="btn" onclick="p=document.getElementById('panel');p.style.display=(p.style.display=='none'?'block':'none')">панель</button>
<div id="panel">
<h1>Источники данных DEM — Санкт-Петербург</h1>
<div class="sub">этап 5.96e · канон ATL06 v4 + НСПД/полевые отметки + стрипы ArcticDEM · высоты в метрах БС-77</div>

<div class="sect"><div class="lbl">Слои</div>
<label class="leg"><input type="checkbox" id="lCanon" checked><span class="sw" style="background:#4fc3f7"></span>ATL06 ICESat-2, канон v4 (__CANON_TOT__→__CANON_KEPT__ тчк)</label>
<label class="leg"><input type="checkbox" id="lSouth" checked><span class="sw" style="background:#81c784"></span>ATL06 юг, валидация 5.90 (__SOUTH_KEPT__ тчк)</label>
<label class="leg"><input type="checkbox" id="lMarks" checked><span class="sw" style="background:#ffb74d"></span>Отметки НСПД/полевые (291)</label>
<label class="leg"><input type="checkbox" id="lStrips" checked><span class="sw" style="background:#26c6da"></span>Стрипы ArcticDEM — план 5.84 (348)</label>
<label class="leg"><input type="checkbox" id="lStripsRef" checked><span class="sw" style="background:#ffee58"></span>Стрипы над эталоном (STAC s2s041, 12)</label>
<label class="leg"><input type="checkbox" id="lZones" checked><span class="sw" style="background:#90a4ae"></span>Зоны тиража 5.83–5.90 (__NZONES__)</label>
<label class="leg"><input type="checkbox" id="lGaps"><span class="sw" style="background:#616161"></span>Пропуски «нет данных» (5.96d, 9)</label>
<label class="leg"><input type="checkbox" id="lRef" checked><span class="sw" style="background:#ffd54f"></span>Эталон: Большой пр. В.О. + боксы</label>
</div>

<div class="sect"><div class="lbl">Эталон и соседние сектора (2м мозаика 5.90 + M-CLF)</div>
<table><tr><th>сектор</th><th>nodata</th><th>excl</th><th>rest</th><th>gold</th><th>h мед</th><th>ATL06</th><th>отм.</th></tr>
<tr><td>Б. пр. В.О. (эталон)</td><td>0%</td><td>0%</td><td>74%</td><td>26%</td><td>4.57</td><td>1347</td><td>17</td></tr>
<tr><td>О. Декабристов (ядро)</td><td>7%</td><td>7%</td><td>55%</td><td>38%</td><td>3.43</td><td>5532</td><td>22</td></tr>
<tr><td>Гавань</td><td>37%</td><td>37%</td><td>55%</td><td>9%</td><td>3.33</td><td>2590</td><td>6</td></tr>
<tr><td>Север В.О.</td><td>47%</td><td>47%</td><td>27%</td><td>27%</td><td>1.86</td><td>1883</td><td>1</td></tr>
<tr><td>В.О. целиком</td><td>22%</td><td>22%</td><td>50%</td><td>28%</td><td>3.54</td><td>18935</td><td>69</td></tr></table>
</div>

<div class="sect"><div class="note"><b>Почему эталон «вылетел» из 2м-стека.</b>
Зона тиража LIGO (30.21–30.31 × 59.90–59.95), накрывающая Б. проспект на 100%, собрана
<b>prior-only</b>: вердикт 5.83 — <code>stack_rejected_or_empty</code>, продукт = prior_v2_30m
(апскейл 30 м), MAD 0.467, p95 2.60 м. Причина: над эталоном в коллекции s2s041 всего
12 стрипов — 2 мартовских 2022 (снег), 1 июльский 2022 (листва), 1 сентябрьский 2020
(облака 28%) и 8 апрельских 2014 (облака до 61%, valid от 35%). Гейты cloud≤0.3,
valid≥0.5, leaf-off и MAD-фильтр против ATL06 оставляют &lt;6 согласуемых стрипов —
ниже порога тиража dens≥6. Класс rest (74%) = валидные вне гейта доверия, gold (26%)
получен can30-патчем 5.92. На карту затоплений коридор попал целиком (0% nodata, 0% excl)
и на уровнях +1…+3 м сухой по высоте (~4.6 м БС-77) — это физика, а не пропуск данных.</div>
<div class="note"><b>О. Декабристов.</b> Ядро острова валидно на 93%, excl 7% — узкие кромки;
+1 м затапливает 28% бокса, +3 м — 40%. Тёмные пятна южнее/севернее — это Гавань (37% excl)
и север В.О. (47%), они вне ядра острова.</div>
</div>

<div class="sub" style="margin-top:8px">подложка: Esri World Dark Gray Base + Reference (© Esri)</div>
</div>

<script>
const D = __DATA__;
const map = L.map('map',{zoomControl:true,attributionControl:false,preferCanvas:true})
 .fitBounds([[59.72,29.42],[60.12,30.60]]);
L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}',
 {maxNativeZoom:16,maxZoom:18}).addTo(map);
L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}',
 {maxNativeZoom:16,maxZoom:18,opacity:0.85}).addTo(map);
L.control.scale({imperial:false,maxWidth:160}).addTo(map);

// ---- стрипы план 5.84
const gStrips=L.layerGroup();
D.stripsCity.forEach(b=>{L.rectangle([b[0],b[1]],{color:'#26c6da',weight:0.6,fill:false,interactive:false}).addTo(gStrips);});
gStrips.addTo(map);
// ---- стрипы STAC над эталоном (дедуп по id)
const gRef=L.layerGroup();
const seen={};
D.stripsRef.carr.concat(D.stripsRef.decab).forEach(s=>{
 if(seen[s.id])return; seen[s.id]=1;
 const latlngs=s.poly.map(p=>[p[1],p[0]]);
 L.polygon(latlngs,{color:'#ffee58',weight:0.8,fill:false,opacity:0.85})
  .bindPopup('<b>'+s.id.split('_').slice(0,3).join('_')+'</b><br>дата: '+(s.dt||'').slice(0,10)+
   '<br>valid: '+(100*s.valid).toFixed(0)+'% · clouds: '+(100*s.cloud).toFixed(0)+'%').addTo(gRef);
});
gRef.addTo(map);
// ---- зоны тиража
const gZones=L.layerGroup();
D.zones.forEach(z=>{
 const hot=z.tag=='LIGO'||z.tag=='AVTOV';
 const st=hot?{color:'#ef5350',weight:1.6,dashArray:'5,3',fill:false}
             :{color:'#90a4ae',weight:0.6,dashArray:'3,4',fill:false,opacity:0.75};
 let pop='<b>'+z.tag+'</b>';
 if(z.verdict) pop+='<br>вердикт: '+z.verdict.product+(z.verdict.mad!=null?('<br>prior holdout MAD: '+z.verdict.mad+' м'):'');
 L.rectangle([[z.bbox[1],z.bbox[0]],[z.bbox[3],z.bbox[2]]],st).bindPopup(pop).addTo(gZones);
});
gZones.addTo(map);
// ---- пропуска 5.96d
const gGaps=L.layerGroup();
D.gaps.forEach(g=>{
 L.rectangle([[g.lat[0],g.lon[0]],[g.lat[1],g.lon[1]]],{color:'#8d9aa8',weight:1,dashArray:'2,3',fill:false})
  .bindPopup('<b>'+g.name+'</b><br>nodata: '+g.nod_pct+'% ('+g.nod_km2+' км²)').addTo(gGaps);
});
// ---- эталон
const gEthal=L.layerGroup();
const carrLngLat=D.carr[0].map(p=>[p[1],p[0]]);
L.polygon(carrLngLat,{color:'#ffd54f',weight:3,fill:false}).bindPopup(
 '<b>Эталон владельца: Большой проспект В.О.</b><br>carriageway 30.239–30.291 × 59.926–59.942<br>'+
 'зона тиража LIGO (prior-only) · ATL06 1347 тчк · отметок 17').addTo(gEthal);
L.rectangle([[59.900,30.205],[59.9415,30.280]],{color:'#ffffff',weight:0.9,dashArray:'4,4',fill:false})
 .bindPopup('о. Декабристов (ядро диагностики)').addTo(gEthal);
L.rectangle([[59.931,30.200],[59.956,30.260]],{color:'#ffffff',weight:0.8,dashArray:'2,5',fill:false})
 .bindPopup('Гавань В.О.').addTo(gEthal);
L.rectangle([[59.955,30.208],[59.966,30.300]],{color:'#ffffff',weight:0.8,dashArray:'2,5',fill:false})
 .bindPopup('Север В.О. (Наличная — Морской фасад)').addTo(gEthal);
gEthal.addTo(map);

// ---- точки: канвас + nearest-lookup для попапов
const idx=new Map(); // ключ ячейки ~55м -> [i,...]
function cell(la,lo){return Math.round(la*1000)+'_'+Math.round(lo*1000);}
const canonMk={color:'#4fc3f7',radius:1.05,stroke:false,fillOpacity:0.5};
const canonHi={color:'#4fc3f7',radius:1.7,stroke:false,fillOpacity:0.75};
const cPts=L.layerGroup(); // high-cnf рисуем тем же слоем
const cArr=D.canon;
D.canon.forEach((p,i)=>{
 const k=cell(p[1],p[0]); if(!idx.has(k))idx.set(k,[]); idx.get(k).push(i);
 L.circleMarker([p[1],p[0]],(p[4]?canonHi:canonMk)).addTo(cPts);
});
cPts.addTo(map);
const southMk={color:'#81c784',radius:1.15,stroke:false,fillOpacity:0.5};
const sPts=L.layerGroup();
D.south.forEach(p=>{L.circleMarker([p[1],p[0]],southMk).addTo(sPts);});
sPts.addTo(map);
// nearest canon-точка при клике
map.on('click',e=>{
 const la=e.latlng.lat,lo=e.latlng.lng;let best=null,bd=1e9;
 for(let dLa=-1;dLa<=1;dLa++)for(let dLo=-1;dLo<=1;dLo++){
  const k=cell(la+dLa*0.0005,lo+dLo*0.0005);const arr=idx.get(k);if(!arr)continue;
  arr.forEach(i=>{const p=cArr[i];const d=(p[1]-la)*(p[1]-la)+(p[0]-lo)*(p[0]-lo);
   if(d<bd){bd=d;best=p;}});
 }
 if(best&&bd<2e-7){
  L.popup().setLatLng([best[1],best[0]]).setContent(
   '<b>ATL06 канон v4</b><br>дата: '+D.canonDates[best[2]]+'<br>RGT: '+best[3]+
   '<br>cnf_src: '+(best[4]?'high':'base')).openOn(map);
 }
});
// ---- отметки НСПД
const gMarks=L.layerGroup();
const mColor={otmetki_field:'#ffb74d',topo_pilot:'#ce93d8',topo_dxf_dachnoe:'#f06292',
 topo95_spot:'#e57373',topo95_contour:'#e57373',topo95_urez:'#e57373'};
D.marks.forEach(m=>{
 L.circleMarker([m[1],m[0]],{color:mColor[m[2]]||'#ffb74d',radius:4,weight:1.5,
  fillColor:mColor[m[2]]||'#ffb74d',fillOpacity:0.85})
 .bindPopup('<b>Отметка</b><br>источник: '+m[2]+'<br>район: '+m[3]+
  (m[4]!=null?('<br>z: '+m[4]+' м БС-77'):'')+(m[5]!=null?('<br>dH vs DEM: '+m[5]+' м'):'')+
  (m[6]?('<br>'+m[6]):'')).addTo(gMarks);
});
gMarks.addTo(map);

// ---- тумблеры
const T={'lCanon':cPts,'lSouth':sPts,'lMarks':gMarks,'lStrips':gStrips,
 'lStripsRef':gRef,'lZones':gZones,'lGaps':gGaps,'lRef':gEthal};
Object.keys(T).forEach(id=>{document.getElementById(id).addEventListener('change',e=>{
 e.target.checked?T[id].addTo(map):map.removeLayer(T[id]);});});
</script></body></html>"""

for k, v in [('__DATA__', json.dumps(DATA, ensure_ascii=False, separators=(',', ':'))),
             ('__CANON_TOT__', f'{canon_tot:,}'), ('__CANON_KEPT__', f'{len(canon):,}'),
             ('__SOUTH_KEPT__', f'{len(south):,}'), ('__NZONES__', str(len(zones)))]:
    HTML = HTML.replace(k, v)

open(OUT, 'w').write(HTML)
import os
print('saved', OUT, round(os.path.getsize(OUT) / 1e6, 2), 'MB')
