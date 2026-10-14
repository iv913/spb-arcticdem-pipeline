#!/usr/bin/env python3
"""5.96c: сборка самодостаточной интерактивной карты зон затопления СПб.
Leaflet инлайнится; GeoJSON и статистика встраиваются; подложки — только
свободнораспространяемые: OSM (ODbL), OpenTopoMap (CC-BY-SA), Sentinel-2
cloudless 2020 (EOX, CC BY 4.0). Бренд «Leaflet» из атрибуции убран."""
import json
import os

GJ = '/tmp/my-project/results/flood_zones_5.96/flood_levels_5.96.geojson'
STATS = '/tmp/my-project/results/flood_zones_5.96/flood_stats_5.96.json'
LEAF_JS = '/tmp/my-project/scripts/data/leaflet/leaflet.min.js'
LEAF_CSS = '/tmp/my-project/scripts/data/leaflet/leaflet.min.css'
OUT = '/tmp/my-project/gh-clone/download/spb_flood_map_5.96.html'

COLORS = {
    1.0: '#2288d3', 1.5: '#2fa3b5', 2.0: '#2fb99a', 2.5: '#58c47a',
    3.0: '#96ca56', 3.5: '#c9c344', 4.0: '#e0a83a', 4.5: '#e5803a', 5.0: '#e05252',
}


def darker(c):
    r, g, b = int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)
    return f'#{int(r*0.72):02x}{int(g*0.72):02x}{int(b*0.72):02x}'


with open(GJ) as f:
    fc = json.load(f)
with open(STATS) as f:
    st = json.load(f)
with open(LEAF_JS) as f:
    leaf_js = f.read()
with open(LEAF_CSS) as f:
    leaf_css = f.read()

lv = st['levels']
max_area = max(r['area_km2'] for r in lv) or 1

# SVG-мини-график площадей
bars = ''
for r in lv:
    w = 100.0 / len(lv)
    h = max(2.0, r['area_km2'] / max_area * 68)
    x = lv.index(r) * w + w * 0.12
    c = COLORS[r['level_m']]
    bars += (f'<rect x="{x:.1f}" y="{70-h:.1f}" width="{w*0.76:.1f}" height="{h:.1f}" '
             f'fill="{c}" rx="1"><title>+{r["level_m"]:.1f} м — {r["area_km2"]:.1f} км²</title></rect>')
chart_svg = ('<svg viewBox="0 0 100 74" preserveAspectRatio="none" style="width:100%;height:86px">'
             f'{bars}</svg>'
             '<div style="display:flex;justify-content:space-between;font-size:9.5px;color:#8a93a3">'
             + ''.join(f'<span>{r["level_m"]:.1f}</span>' for r in lv) + '</div>')

levels_js = json.dumps([r['level_m'] for r in lv])
areas_js = json.dumps({str(r['level_m']): r['area_km2'] for r in lv}, ensure_ascii=False)
geojson_js = json.dumps(fc, ensure_ascii=False, separators=(',', ':'))

html = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>СПб — потенциальные зоны затопления +1…+5 м (АрктикДЕМ 2 м, БС-77) — этап 5.96</title>
<style>__LEAFCSS__
html,body{margin:0;padding:0;height:100%;font:13px/1.45 -apple-system,'Segoe UI',Roboto,Arial,sans-serif;background:#0f141a;color:#e8ecf2}
#map{position:absolute;inset:0;inset-block-start:0;inset-inline-start:0;width:100%;height:100%;background:#0f141a}
.panel{position:absolute;z-index:1000;inset-block-start:12px;inset-inline-start:12px;width:344px;max-height:calc(100% - 24px);overflow:auto;
 background:rgba(15,20,26,.92);border:1px solid #2a3441;border-radius:10px;box-shadow:0 6px 24px rgba(0,0,0,.5);backdrop-filter:blur(6px)}
.panel h1{font-size:15.5px;margin:0;padding:12px 14px 2px;letter-spacing:.2px}
.panel .sub{padding:0 14px 8px;color:#9fb0c3;font-size:11.5px;line-height:1.4}
.badge{display:inline-block;background:#1d2a38;color:#7fd1ff;border:1px solid #2b4257;border-radius:5px;padding:1px 7px;font-size:10.5px;margin:0 3px 6px 0}
.ctl{padding:8px 14px 4px;border-top:1px solid #223040}
.ctl label{display:block;color:#9fb0c3;font-size:11px;margin-bottom:4px;text-transform:uppercase;letter-spacing:.5px}
.slider-row{display:flex;align-items:center;gap:10px}
#lvl{flex:1;accent-color:#4dabf7}
#lvlNum{font-size:22px;font-weight:700;min-width:74px;text-align:right;color:#fff;font-variant-numeric:tabular-nums}
#lvlNum small{font-size:11px;color:#8a93a3;font-weight:400}
.ticks{display:flex;justify-content:space-between;color:#5d6b7d;font-size:9.5px;margin-top:2px}
.row{display:flex;align-items:center;gap:7px;padding:2.5px 14px;cursor:pointer;user-select:none}
.row:hover{background:rgba(255,255,255,.04)}
.row input{accent-color:#4dabf7}
.chip{width:14px;height:14px;border-radius:3px;flex:none;border:1px solid rgba(255,255,255,.25)}
.row .nm{flex:1;font-size:12px}
.row .ar{font-size:11px;color:#8a93a3;font-variant-numeric:tabular-nums}
.stats{padding:8px 14px;border-top:1px solid #223040;color:#c3cdd9;font-size:12px}
.stats b{color:#fff;font-variant-numeric:tabular-nums}
.mini{padding:6px 14px 2px;border-top:1px solid #223040}
.mini .cap{color:#9fb0c3;font-size:11px;margin-bottom:2px}
details{border-top:1px solid #223040;padding:8px 14px;color:#aab6c5;font-size:11.5px}
details summary{cursor:pointer;color:#7fd1ff;font-size:12px}
details p{margin:6px 0}
details b{color:#dfe6ee}
.legend-grad{height:10px;border-radius:5px;margin:6px 0 2px;background:linear-gradient(90deg,#2288d3,#2fb99a,#96ca56,#c9c344,#e05252)}
.foot{padding:6px 14px 10px;color:#5d6b7d;font-size:10px;border-top:1px solid #223040}
.leaflet-popup-content{font:12.5px/1.4 -apple-system,Arial}
.leaflet-popup-content b{color:#0b63a3}
.leaflet-container{background:#0f141a}
.btns{display:flex;gap:6px;padding:8px 14px 2px;border-top:1px solid #223040}
.btns button{flex:1;background:#1d2a38;color:#cfe4f7;border:1px solid #2b4257;border-radius:6px;padding:5px 4px;font-size:11.5px;cursor:pointer}
.btns button:hover{background:#25384c}
@media(max-width:560px){.panel{width:calc(100% - 24px);max-height:56%}}
</style>
</head>
<body>
<div id="map"></div>
<div class="panel">
  <h1>Потенциальные зоны затопления Санкт-Петербурга</h1>
  <div class="sub">Сценарии нагонных подъёмов <b style="color:#fff">+1 … +5 м</b> над нулём БС-77
   <span style="color:#5d6b7d">· гибридная ЦМР 2 м (ArcticDEM, этап 5.90) · датум БС-77 (δ=+16.04 м)</span></div>
  <div style="padding:0 14px">
    <span class="badge">этап 5.96</span><span class="badge">модель «чаша»</span><span class="badge">ячейка ~8 м</span>
    <span class="badge">MAD 0.31 м (ATL06)</span>
  </div>
  <div class="ctl">
    <label>Уровень воды (метры БС-77)</label>
    <div class="slider-row">
      <input type="range" id="lvl" min="1" max="5" step="0.5" value="1">
      <div id="lvlNum">+1.0 <small>м</small></div>
    </div>
    <div class="ticks"><span>1.0</span><span>2.0</span><span>3.0</span><span>4.0</span><span>5.0</span></div>
  </div>
  <div class="btns">
    <button id="btnOne">Один уровень</button>
    <button id="btnAll">Все уровни</button>
    <button id="btnNone">Скрыть</button>
  </div>
  <div class="ctl"><label>Сценарии (площадь потенциального затопления)</label>
    <div class="legend-grad"></div>
  </div>
  <div id="rows"></div>
  <div class="stats">Зона выбранного(ых) уровня(ей): <b id="sumArea">—</b><br>
   <span style="color:#8a93a3">Ориентиры: катастрофические нагоны 1824 г. ≈ +4.2 м, 1924 г. ≈ +3.7 м; расчётная защита КЗС — до +5 м.</span></div>
  <div class="mini"><div class="cap">Площади по уровням, км²</div>__CHARTSVG__</div>
  <details>
    <summary>Методика и ограничения</summary>
    <p><b>Данные:</b> __MOSAIC__ — гибридная мозаика 2 м (север 5.84 + юг 5.90), высоты в БС-77 (датум-фикс δ=+16.04 м, валидация ATL06/nakarte: MAD 0.31 м / 5∕5 знаков).</p>
    <p><b>Модель:</b> гидростатическая «чаша» — потенциально затоплено всё, что ниже уровня <i>L</i> и является сушей. Ячейка ~8 м (агрегация 8×4, приём ≥25% валидных пикселей). Акватории исключены по высоте (h ≤ 0.7 м) и по классам доверия M-CLF-v3; пятна &lt; 1.9 га отброшены; упрощение контуров 25 м.</p>
    <p><b>Ограничения:</b> без гидродинамики, дренажа и ветрового нагона; КЗС предполагается неработающей (сценарий прорыва/отказа); footprint ЦМР фрагментарен — белые зоны карты означают «нет данных», а не «безопасно»; русла рек в ЦМР местами завышены (DSM) — полигоны над акваториями игнорировать; вертикальная точность ±0.3 м; площади округлены (~±5%).</p>
    <p><b>Не является</b> официальной картой зон затопления; для планирования использовать гидродинамические модели и нормативные документы.</p>
  </details>
  <div class="foot">spb-arcticdem-pipeline · этап 5.96 · файл spb_flood_map_5.96.html · подложки свободные: © OpenStreetMap contributors · © OpenTopoMap (CC-BY-SA) · Sentinel-2 cloudless © EOX IT Services (Copernicus)</div>
</div>
<script>__LEAFJS__</script>
<script>
const FC = __GEOJSON__;
const AREAS = __AREAS__;
const LEVELS = __LEVELS__;
const COLORS = {1.0:'#2288d3',1.5:'#2fa3b5',2.0:'#2fb99a',2.5:'#58c47a',3.0:'#96ca56',3.5:'#c9c344',4.0:'#e0a83a',4.5:'#e5803a',5.0:'#e05252'};
function dk(c){const n=parseInt(c.slice(1),16);const r=(n>>16)*0.72|0,g=((n>>8)&255)*0.72|0,b=(n&255)*0.72|0;return '#'+((r<<16)|(g<<8)|b).toString(16).padStart(6,'0');}

const map = L.map('map',{zoomControl:true}).fitBounds([[59.72,29.42],[60.12,30.60]]);
map.attributionControl.setPrefix(false); // убрать бренд «Leaflet» из правого нижнего угла
const osm = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'© OpenStreetMap contributors'});
const topo = L.tileLayer('https://tile.opentopomap.org/{z}/{x}/{y}.png',{maxZoom:17,attribution:'© OpenStreetMap, SRTM · © OpenTopoMap (CC-BY-SA)'});
const s2 = L.tileLayer('https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2020_3857/default/g/{z}/{y}/{x}.jpg',{maxNativeZoom:14,maxZoom:18,attribution:'Sentinel-2 cloudless © EOX IT Services (Contains modified Copernicus Sentinel data)'});
osm.addTo(map);
L.control.layers({'OSM Standard':osm,'OpenTopoMap':topo,'Спутник (Sentinel-2)':s2},{},{position:'topright'}).addTo(map);
L.control.scale({imperial:false,maxWidth:160}).addTo(map);

const layers = {};
const sorted = [...FC.features].sort((a,b)=>b.properties.level_m-a.properties.level_m); // верхние уровни рисуем снизу
for (const f of FC.features){
  const lv = f.properties.level_m;
  const col = COLORS[lv];
  layers[lv] = L.geoJSON({type:'FeatureCollection',features:[f]}, {
    style:{color:dk(col),weight:1,fillColor:col,fillOpacity:0.52},
    onEachFeature:(ft,lyr)=>{ lyr.bindPopup('<b>Уровень +'+ft.properties.level_m.toFixed(1)+' м БС-77</b><br>Потенциальное затопление: '+ft.properties.area_km2.toFixed(1)+' км²<br><span style="color:#777">модель «чаша», без гидродинамики</span>'); }
  });
}
const checked = new Set(['1.0']);

function render(){
  let s=0;
  for (const lv of LEVELS){
    const on = checked.has(lv.toFixed(1));
    if (on && !map.hasLayer(layers[lv])) layers[lv].addTo(map);
    if (!on && map.hasLayer(layers[lv])) map.removeLayer(layers[lv]);
    if (on) s += AREAS[lv.toFixed(1)];
  }
  // порядок: от нижних слоёв к верхним — старшие уровни снизу
  const order=[...LEVELS].reverse();
  for(const lv of order){ if(map.hasLayer(layers[lv])) layers[lv].bringToBack(); }
  const sel=[...checked].map(Number).sort((a,b)=>a-b);
  document.getElementById('lvlNum').innerHTML = (document.getElementById('lvl').valueAsNumber).toFixed(1)+' <small>м</small>';
  document.getElementById('sumArea').textContent = s>0 ? s.toFixed(1)+' км²' : '—';
}
const rowsEl=document.getElementById('rows');
for(const lv of LEVELS){
  const key=lv.toFixed(1);
  const row=document.createElement('div'); row.className='row';
  row.innerHTML='<input type="checkbox" id="cb'+key+'" '+(checked.has(key)?'checked':'')+'><span class="chip" style="background:'+COLORS[lv]+'"></span><span class="nm">+'+lv.toFixed(1)+' м</span><span class="ar">'+AREAS[key].toFixed(1)+' км²</span>';
  row.querySelector('input').addEventListener('change',(e)=>{
    if(e.target.checked) checked.add(key); else checked.delete(key);
    render();
  });
  rowsEl.appendChild(row);
}
document.getElementById('lvl').addEventListener('input',(e)=>{
  checked.clear(); checked.add(e.target.valueAsNumber.toFixed(1));
  document.querySelectorAll('#rows input').forEach(cb=>{cb.checked = checked.has(cb.id.slice(2));});
  render();
});
document.getElementById('btnOne').onclick=()=>{
  const cur=document.getElementById('lvl').valueAsNumber.toFixed(1);
  checked.clear(); checked.add(cur);
  document.querySelectorAll('#rows input').forEach(cb=>{cb.checked = checked.has(cb.id.slice(2));});
  render();
};
document.getElementById('btnAll').onclick=()=>{
  LEVELS.forEach(lv=>checked.add(lv.toFixed(1)));
  document.querySelectorAll('#rows input').forEach(cb=>{cb.checked=true;});
  render();
};
document.getElementById('btnNone').onclick=()=>{
  checked.clear();
  document.querySelectorAll('#rows input').forEach(cb=>{cb.checked=false;});
  render();
};
render();
</script>
</body>
</html>
"""

html = (html.replace('__LEAFCSS__', leaf_css)
            .replace('__LEAFJS__', leaf_js)
            .replace('__CHARTSVG__', chart_svg)
            .replace('__MOSAIC__', st['mosaic'])
            .replace('__GEOJSON__', geojson_js)
            .replace('__AREAS__', areas_js)
            .replace('__LEVELS__', levels_js))

with open(OUT, 'w', encoding='utf-8') as f:
    f.write(html)
print('OK', OUT, f'{os.path.getsize(OUT)/1e6:.2f} MB')
