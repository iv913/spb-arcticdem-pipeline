#!/usr/bin/env python3
"""03_flood_map.py v2 — поверхность затопления с тайловым рендером + паспортные точки."""
import io, json, base64, heapq, sys
from pathlib import Path
from datetime import datetime
import numpy as np, rasterio
from rasterio.features import geometry_mask
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location('m01', Path(__file__).parent / '01_owner_map.py')
m01 = _ilu.module_from_spec(_spec); _spec.loader.exec_module(m01)
_spec4 = _ilu.spec_from_file_location('libv', Path(__file__).parent / 'lib_vertical.py')
libv = _ilu.module_from_spec(_spec4); _spec4.loader.exec_module(libv)
OUT = m01.OUT
REPORT_LEVELS = [0.5,1.0,1.5,2.0,2.5,3.0]; KZS_NOTE_LEVEL = 1.6; PIXEL_M = 2.0
FORM_SIGMA = 0.107; CLASS_SIGMA = {1:0.15, 2:0.30, 3:0.80}; STREET_END_M = 30.0
SLIDER_MIN, SLIDER_MAX, SLIDER_STEP = 0.0, 4.0, 0.1

def log(msg): print(f'[{datetime.now():%H:%M:%S}] {msg}', flush=True)

def load_shift_sigma():
    p = OUT / 'city_calibration.json'
    if not p.exists(): log('⚠ city_calibration.json не найден — SHIFT_SIGMA=0.80'); return 0.80, None
    cc = json.loads(p.read_text())
    if cc.get('version') != 'v3':
        log(f'⚠ city_calibration v{cc.get("version","v1")} — ДИСКРЕДИТИРОВАНА. SHIFT_SIGMA=0.80.'); return 0.80, cc
    sigma = float(cc.get('between_street_mad_m', 0.80))
    reg = cc.get('regional') or {}
    if reg.get('significant'): log(f'⚠ Региональный сдвиг В.О.−материк={reg.get("delta_m")}м значим!')
    log(f'SHIFT_SIGMA = {sigma:.3f}м (between_street_mad, v3)'); return sigma, cc

def load_surface():
    p = OUT / 'road_surface.tif'
    if not p.exists(): log(f'ERROR: нет {p}'); sys.exit(1)
    with rasterio.open(str(p)) as src:
        h = src.read(1).astype('float32'); source = src.read(2).astype('uint8')
        if src.nodata is not None: h[h == src.nodata] = np.nan
        return h, source, src.transform, src.crs

def water_seed_mask(shape, transform, crs):
    osm = m01.fetch_osm(m01.MVP_BBOX); _, _, waters = m01.collect_layers(osm)
    water_utm = m01.merge_water(waters)
    if water_utm is None or water_utm.is_empty: return np.zeros(shape, dtype=bool)
    import geopandas as gpd
    g = gpd.GeoSeries([water_utm], crs=m01.UTM).to_crs(crs).iloc[0]
    return ~geometry_mask([g.__geo_interface__], transform=transform, out_shape=shape, all_touched=True)

def street_end_seeds(road, transform, crs):
    import shapely, geopandas as gpd
    osm = m01.fetch_osm(m01.MVP_BBOX)
    _, axis_utm, _, _ = m01.build_object_geometry(osm, m01.MVP_BBOX, m01.MVP_NAME)
    axis_dem = gpd.GeoSeries([axis_utm], crs=m01.UTM).to_crs(crs).iloc[0]
    rows, cols = np.where(road)
    xs = transform.c + transform.a*(cols+0.5) + transform.b*(rows+0.5)
    ys = transform.f + transform.d*(cols+0.5) + transform.e*(rows+0.5)
    pk = np.asarray(shapely.line_locate_point(axis_dem, shapely.points(xs, ys)))
    ends = (pk < pk.min()+STREET_END_M) | (pk > pk.max()-STREET_END_M)
    seeds = np.zeros_like(road); seeds[rows[ends], cols[ends]] = True
    log(f'  Seed торцов: {int(seeds.sum()):,} px'); return seeds

def spill_levels(h, seeds):
    H, W = h.shape; Z = np.full((H, W), np.inf, dtype='float64')
    valid = np.isfinite(h); pq = []
    for r, c in zip(*np.where(seeds & valid)):
        z = float(h[r, c]); Z[r, c] = z; heapq.heappush(pq, (z, r, c))
    nbrs = ((-1,0),(1,0),(0,-1),(0,1),(-1,-1),(-1,1),(1,-1),(1,1))
    while pq:
        z, r, c = heapq.heappop(pq)
        if z > Z[r, c]: continue
        for dr, dc in nbrs:
            rr, cc = r+dr, c+dc
            if 0 <= rr < H and 0 <= cc < W and valid[rr, cc]:
                nz = max(z, float(h[rr, cc]))
                if nz < Z[rr, cc]: Z[rr, cc] = nz; heapq.heappush(pq, (nz, rr, cc))
    Z[~valid | np.isinf(Z)] = np.nan; return Z.astype('float32')

def encode_u8(a, lo, hi):
    out = np.zeros(a.shape, dtype='uint8'); m = np.isfinite(a)
    out[m] = 1 + np.clip((a[m] - lo) / (hi - lo) * 253, 0, 253).astype('uint8'); return out

def main():
    log('='*60); log('03_flood_map v2: поверхность + тайловый ползунок + точки'); log('='*60)
    shift_sigma, cc = load_shift_sigma()
    h, source, transform, crs = load_surface(); road = np.isfinite(h)
    log(f'Поверхность: {int(road.sum()):,} px')
    u = np.full(h.shape, np.nan, dtype='float32')
    for cls, s in CLASS_SIGMA.items():
        m = road & (source == cls); u[m] = np.sqrt(FORM_SIGMA**2 + shift_sigma**2 + s**2)
    log('Бюджет: ' + ', '.join(f'{n} ±{np.sqrt(FORM_SIGMA**2+shift_sigma**2+s**2):.2f}м' for n,s in (('measured',0.15),('reconstructed',0.30),('extrapolated',0.80))))
    seeds = water_seed_mask(h.shape, transform, crs) | street_end_seeds(road, transform, crs)
    log('Priority-flood...'); zs = spill_levels(h, seeds)
    log(f'  Z_spill: [{np.nanmin(zs):.2f}, {np.nanmax(zs):.2f}] м БС')
    # GIS-продукт
    tif = OUT / 'flood_surface.tif'
    prof = {'driver':'GTiff','dtype':'float32','count':4,'crs':crs,'transform':transform,'width':h.shape[1],'height':h.shape[0],'compress':'deflate','nodata':-9999}
    with rasterio.open(str(tif), 'w', **prof) as dst:
        for bi, (arr, name) in enumerate([(h,'h_bs77'),(u,'uncertainty_m'),(zs,'z_spill_bs77'),(source.astype('float32'),'source 1/2/3')], start=1):
            a = arr.copy(); a[~np.isfinite(a)] = -9999; dst.write(a.astype('float32'), bi); dst.set_band_description(bi, name)
    log(f'✓ {tif}')
    # Контрольная таблица
    total = int(road.sum()); qa_levels = {}
    for H in REPORT_LEVELS:
        # ДЕФЕКТ РЕВЬЮ №6: неопределённость должна входить и в порог
        # перелива, и в глубину — симметрично для обоих сценариев.
        core, wet, opt = libv.flood_masks(h, zs, u, H)
        core &= road; wet &= road; opt &= road
        depth = np.where(wet, H - h, np.nan)
        qa_levels[f'{H:.1f}'] = {'pct_pess':round(100*core.sum()/total,1),'pct_best':round(100*wet.sum()/total,1),
            'pct_opt':round(100*opt.sum()/total,1),'depth_med':round(float(np.nanmedian(depth)),2) if wet.any() else None,
            'depth_max':round(float(np.nanmax(depth)),2) if wet.any() else None,
            'by_class':{'meas':round(100*(wet&(source==1)).sum()/max(1,(road&(source==1)).sum()),1),
                        'rec':round(100*(wet&(source==2)).sum()/max(1,(road&(source==2)).sum()),1),
                        'ext':round(100*(wet&(source==3)).sum()/max(1,(road&(source==3)).sum()),1)},
            'kzs':H > KZS_NOTE_LEVEL}
        log(f'  H={H:.1f}: {qa_levels[f"{H:.1f}"]["pct_best"]}% [{qa_levels[f"{H:.1f}"]["pct_pess"]}..{qa_levels[f"{H:.1f}"]["pct_opt"]}]' + (' [КЗС]' if H > KZS_NOTE_LEVEL else ''))
    # Дисплей-варп
    enc_lo = float(np.floor(min(np.nanmin(h), np.nanmin(zs)) * 2) / 2 - 0.1)
    enc_hi = float(np.ceil(max(np.nanmax(h), np.nanmax(zs)) * 2) / 2 + 0.1)
    log(f'  Кодирование: [{enc_lo:.1f}, {enc_hi:.1f}], шаг {(enc_hi-enc_lo)/253*100:.1f}см')
    h8 = encode_u8(h, enc_lo, enc_hi); z8 = encode_u8(zs, enc_lo, enc_hi)
    h8d, z8d, (w4, s4, e4, n4) = m01._reproject_categorical_for_display(h8, z8, transform, crs)
    s8 = source.copy(); s8[~road] = 255; s8d, _, _ = m01._reproject_categorical_for_display(s8, s8.copy(), transform, crs)
    from PIL import Image
    rgba = np.zeros((*h8d.shape, 4), dtype='uint8')
    valid_d = (h8d > 0) & (h8d != 255)
    rgba[..., 0] = np.where(valid_d, h8d, 0); rgba[..., 1] = np.where(valid_d, z8d, 0)
    rgba[..., 2] = np.where(valid_d & (s8d <= 3), s8d, 0); rgba[..., 3] = np.where(valid_d, 255, 0)
    buf = io.BytesIO(); Image.fromarray(rgba).save(buf, format='PNG', optimize=True)
    b64 = base64.b64encode(buf.getvalue()).decode()
    log(f'  Дисплей-пак: {h8d.shape[1]}x{h8d.shape[0]} px, {len(b64)/1e6:.1f} МБ')

    # Паспортные точки: живая валидация на ползунке
    pts_js = []
    try:
        cp = m01.load_control_points()
        if len(cp):
            from rasterio.warp import transform as warp_transform
            xs, ys = warp_transform('EPSG:4326', crs, cp['lon'].tolist(), cp['lat'].tolist())
            inv = ~transform
            for (_, p), x, y in zip(cp.iterrows(), xs, ys):
                c0, r0 = inv * (x, y)
                r0, c0 = int(np.floor(r0)), int(np.floor(c0))
                if not (0 <= r0 < h.shape[0] and 0 <= c0 < h.shape[1]): continue
                hv, zv, uv = np.nan, np.nan, np.nan
                for rad in (0, 2, 5, 8):
                    r1, r2 = max(0, r0-rad), min(h.shape[0], r0+rad+1)
                    c1, c2 = max(0, c0-rad), min(h.shape[1], c0+rad+1)
                    sub = h[r1:r2, c1:c2]
                    if np.isfinite(sub).any():
                        ri, ci = np.unravel_index(np.nanargmin(np.abs(sub - p['h_pas'])), sub.shape)
                        hv = float(sub[ri, ci]); zv = float(zs[r1+ri, c1+ci]); uv = float(u[r1+ri, c1+ci])
                        break
                if not np.isfinite(hv): continue
                pts_js.append({'lat': round(float(p['lat']),6), 'lon': round(float(p['lon']),6),
                               'hp': round(float(p['h_pas']),2), 'hs': round(hv,2),
                               'zs': round(zv,2), 'u': round(uv,2)})
            log(f'  Паспортных точек на поверхности: {len(pts_js)}')
    except Exception as e:
        log(f'  ⚠ точки не вшиты: {e}')

    us = {1: float(np.sqrt(FORM_SIGMA**2+shift_sigma**2+0.15**2)), 2: float(np.sqrt(FORM_SIGMA**2+shift_sigma**2+0.30**2)), 3: float(np.sqrt(FORM_SIGMA**2+shift_sigma**2+0.80**2))}
    html = HTML_TEMPLATE
    for k, v in {'__B64__':b64,'__W4__':repr(w4),'__S4__':repr(s4),'__E4__':repr(e4),'__N4__':repr(n4),
        '__LO__':repr(enc_lo),'__HI__':repr(enc_hi),'__U1__':f'{us[1]:.3f}','__U2__':f'{us[2]:.3f}','__U3__':f'{us[3]:.3f}',
        '__KZS__':f'{KZS_NOTE_LEVEL:.1f}','__HMIN__':f'{SLIDER_MIN:.1f}','__HMAX__':f'{SLIDER_MAX:.1f}','__HSTEP__':f'{SLIDER_STEP:.2f}',
        '__TITLE__':m01.MVP_NAME, '__PTS__': json.dumps(pts_js, ensure_ascii=False)}.items():
        html = html.replace(k, v)
    (OUT / 'flood_map_bolshoy.html').write_text(html, encoding='utf-8')
    log(f'✓ flood_map_bolshoy.html (тайловый ползунок + {len(pts_js)} точек)')
    qa = {'object':m01.MVP_NAME,'model':'priority-flood spill levels; tile-render + passport validation',
          'error_budget':{'form':FORM_SIGMA,'shift':shift_sigma,'shift_source':(cc or {}).get('shift_source','default'),'class':CLASS_SIGMA,'total_u':us},
          'report_levels':qa_levels,'n_passport_points':len(pts_js),
          'products':{'gis':'flood_surface.tif (h,u,z_spill,source)','web':'flood_map_bolshoy.html (tile slider + points)'}}
    (OUT / 'flood_qa.json').write_text(json.dumps(qa, ensure_ascii=False, indent=2)); log(f'✓ flood_qa.json')

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Затопление: __TITLE__</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css"/>
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
<style>html,body,#map{height:100%;margin:0}
#panel{position:fixed;bottom:24px;left:24px;z-index:1000;background:#fff;padding:12px 16px;border-radius:8px;box-shadow:0 2px 12px rgba(0,0,0,.35);font:12px/1.5 sans-serif;width:330px}
#hval{font-size:20px;font-weight:700}#kzs{display:none;color:#b00;font-weight:600}input[type=range]{width:100%}
.lg{display:inline-block;width:14px;height:14px;vertical-align:-2px;border-radius:3px}</style></head><body>
<div id="map"></div><div id="panel"><b>__TITLE__ — уровень воды, м БС-77</b><br>
<span id="hval">2.0</span> м <span id="stats"></span><br>
<input type="range" id="hslider" min="__HMIN__" max="__HMAX__" step="__HSTEP__" value="2.0"><br>
<span id="kzs">⚠ выше __KZS__ м — сценарий отказа/перелива КЗС, не прогноз</span>
<div style="margin-top:6px"><span class="lg" style="background:#08306b"></span> глубоко
<span class="lg" style="background:#6baed6"></span> мелко
<span class="lg" style="background:rgba(120,170,220,.55)"></span> кайма ±u
<span class="lg" style="background:#999"></span> сухо<br>
u: measured ±__U1__ / reconstructed ±__U2__ / extrapolated ±__U3__ м.<br>
Тайловый рендер: пересчитываются только видимые тайлы.</div></div>
<script>
const B64="__B64__",LO=__LO__,HI=__HI__,U=[0,__U1__,__U2__,__U3__];
const W4=__W4__,S4=__S4__,E4=__E4__,N4=__N4__;let curH=2.0;
const map=L.map('map',{center:[(S4+N4)/2,(W4+E4)/2],zoom:14});
L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',{attribution:'Esri',maxZoom:19}).addTo(map);
const P=L.Projection.SphericalMercator;const pNW=P.project(L.latLng(N4,W4)),pSE=P.project(L.latLng(S4,E4));
const MX0=pNW.x,MY0=pNW.y,MX1=pSE.x,MY1=pSE.y;
let DW=0,DH=0,hArr=null,zArr=null,sArr=null,grid=null;
const img=new Image();
img.onload=function(){DW=img.width;DH=img.height;const t=document.createElement('canvas');t.width=DW;t.height=DH;
const tc=t.getContext('2d');tc.drawImage(img,0,0);const d=tc.getImageData(0,0,DW,DH).data;
hArr=new Float32Array(DW*DH);zArr=new Float32Array(DW*DH);sArr=new Uint8Array(DW*DH);
for(let i=0;i<DW*DH;i++){if(d[4*i+3]===0||d[4*i]===0){hArr[i]=NaN;continue;}
hArr[i]=LO+(d[4*i]-1)/253*(HI-LO);zArr[i]=LO+(d[4*i+1]-1)/253*(HI-LO);sArr[i]=d[4*i+2];}
grid=new FloodGrid({tileSize:256,opacity:0.9,bounds:[[S4,W4],[N4,E4]]});grid.addTo(map);initPoints();updateStats();};
img.src="data:image/png;base64,"+B64;
function blues(t){return[Math.round(190-160*t),Math.round(220-150*t),Math.round(240-70*t)];}
const FloodGrid=L.GridLayer.extend({
createTile:function(coords){const tile=document.createElement('canvas');tile.width=tile.height=256;this.drawTile(tile,coords);return tile;},
drawTile:function(tile,coords){const z=coords.z,scale=256*Math.pow(2,z);const ctx=tile.getContext('2d');
const outd=ctx.createImageData(256,256),o=outd.data;
for(let py=0;py<256;py++){for(let px=0;px<256;px++){
const wx=(coords.x*256+px+0.5)/scale,wy=(coords.y*256+py+0.5)/scale;
const R=6378137,mx=(wx-0.5)*2*Math.PI*R,my=(0.5-wy)*2*Math.PI*R;
const i=4*(py*256+px);
if(mx<MX0||mx>MX1||my>MY0||my<MY1){o[i+3]=0;continue;}
const dx=Math.floor((mx-MX0)/(MX1-MX0)*DW),dy=Math.floor((MY0-my)/(MY0-MY1)*DH);
if(dx<0||dx>=DW||dy<0||dy>=DH){o[i+3]=0;continue;}
const k=dy*DW+dx,h=hArr[k];if(!isFinite(h)){o[i+3]=0;continue;}
const zs=zArr[k],u=U[sArr[k]]||0.8,depth=curH-h;
if(curH>zs+u&&depth>u){const c=blues(Math.min(depth,3)/3);o[i]=c[0];o[i+1]=c[1];o[i+2]=c[2];o[i+3]=225;
}else if(curH>zs-u&&depth>-u){o[i]=120;o[i+1]=170;o[i+2]=220;o[i+3]=120;
}else{o[i]=120;o[i+1]=120;o[i+2]=120;o[i+3]=55;}}}
ctx.putImageData(outd,0,0);}});
const PTS=__PTS__;const ptMarkers=[];
function initPoints(){for(const p of PTS){
const m=L.circleMarker([p.lat,p.lon],{radius:6,color:'#fff',weight:1.5,fillOpacity:0.95,fillColor:'#888'}).addTo(map);
m.bindPopup('');ptMarkers.push([m,p]);}}
function updatePoints(){let mism=0;
for(const [m,p] of ptMarkers){
const obsWet=curH>p.hp,predWet=curH>p.zs;
const agree=(obsWet===predWet)||(Math.abs(curH-p.hp)<=p.u);
let col;if(!agree){col='#d62728';mism++;}else col=obsWet?'#1f77b4':'#7a7a7a';
m.setStyle({fillColor:col});
m.setPopupContent('h_пасп='+p.hp.toFixed(2)+' м<br>h_модель='+p.hs.toFixed(2)+' м (dH='+(p.hs-p.hp).toFixed(2)+')<br>z_spill='+p.zs.toFixed(2)+' м, ±u='+p.u.toFixed(2)+'<br>при H='+curH.toFixed(1)+': паспорт '+(obsWet?'ПОД водой':'сухо')+', модель '+(predWet?'ПОД водой':'сухо'));}
return mism;}
function updateStats(){if(!hArr)return;let wet=0,valid=0;
for(let k=0;k<DW*DH;k++){if(!isFinite(hArr[k]))continue;valid++;if(curH>zArr[k])wet++;}
const mism=updatePoints();
document.getElementById('stats').textContent=' — под водой '+(100*wet/Math.max(1,valid)).toFixed(1)+'%'+(PTS.length?(' | точки: '+(PTS.length-mism)+'✓ '+(mism?mism+'✗':'')):'');}
const slider=document.getElementById('hslider');let raf=null;
slider.addEventListener('input',()=>{curH=parseFloat(slider.value);
document.getElementById('hval').textContent=curH.toFixed(1);
document.getElementById('kzs').style.display=curH>__KZS__?'inline':'none';
if(raf)cancelAnimationFrame(raf);raf=requestAnimationFrame(()=>{if(grid)grid.redraw();updateStats();});});
</script></body></html>"""

if __name__ == '__main__': main()
