#!/usr/bin/env python3
"""03_flood_map.py — карта затопления Большого пр-та В.О."""
import json, sys
from pathlib import Path
from datetime import datetime
import numpy as np, rasterio
from rasterio.features import geometry_mask
from scipy.ndimage import label as ndlabel
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location('m01', Path(__file__).parent / '01_owner_map.py')
m01 = _ilu.module_from_spec(_spec); _spec.loader.exec_module(m01)
OUT = m01.OUT
FLOOD_LEVELS = [0.5,1.0,1.5,2.0,2.5,3.0]; KZS_NOTE_LEVEL = 1.6
MIN_POOL_M2 = 12.0; PIXEL_M = 2.0; FORM_SIGMA = 0.107
SHIFT_SIGMA = 0.80; CLASS_SIGMA = {1:0.15, 2:0.30, 3:0.80}
def log(msg): print(f'[{datetime.now():%H:%M:%S}] {msg}', flush=True)
def load_surface():
    p = OUT / 'road_surface.tif'
    if not p.exists(): log(f'ERROR: нет {p}'); sys.exit(1)
    with rasterio.open(str(p)) as src:
        h = src.read(1).astype('float32'); source = src.read(2).astype('uint8')
        h[h == src.nodata] = np.nan; return h, source, src.transform, src.crs
def water_seed_mask(shape, transform, crs):
    osm = m01.fetch_osm(m01.MVP_BBOX); _, greens, waters = m01.collect_layers(osm)
    water_utm = m01.merge_water(waters)
    if water_utm is None or water_utm.is_empty: return np.zeros(shape, dtype=bool)
    import geopandas as gpd
    g = gpd.GeoSeries([water_utm], crs=m01.UTM).to_crs(crs).iloc[0]
    return ~geometry_mask([g.__geo_interface__], transform=transform, out_shape=shape, all_touched=True)
STREET_END_M = 30.0
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
    seeds = np.zeros_like(road); seeds[rows[ends], cols[ends]] = True; return seeds
def connected_flood(wet, seed_mask):
    lab, n = ndlabel(wet, structure=np.ones((3,3), bool))
    if n == 0: return wet & False
    sl = np.unique(lab[seed_mask & wet]); keep = np.zeros(n+1, dtype=bool)
    keep[sl[sl>0]] = True; sizes = np.bincount(lab.ravel(), minlength=n+1)
    keep &= sizes >= max(1, int(MIN_POOL_M2/(PIXEL_M**2))); keep[0] = False; return keep[lab]
def main():
    log('='*60); log('03_flood_map'); log('='*60)
    h, source, transform, crs = load_surface(); road = np.isfinite(h)
    u = np.full(h.shape, np.nan, dtype='float32')
    for cls, s in CLASS_SIGMA.items():
        m = road & (source==cls); u[m] = np.sqrt(FORM_SIGMA**2 + SHIFT_SIGMA**2 + s**2)
    log(f'Бюджет: meas ±{np.sqrt(FORM_SIGMA**2+SHIFT_SIGMA**2+0.15**2):.2f}, '
        f'rec ±{np.sqrt(FORM_SIGMA**2+SHIFT_SIGMA**2+0.30**2):.2f}, '
        f'ext ±{np.sqrt(FORM_SIGMA**2+SHIFT_SIGMA**2+0.80**2):.2f}м')
    water = water_seed_mask(h.shape, transform, crs)
    seeds = water | street_end_seeds(road, transform, crs)
    qa_levels = {}; layers = {}; total = int(road.sum())
    for H in FLOOD_LEVELS:
        depth = H - h
        wp = connected_flood(road & (depth > u), seeds)
        wb = connected_flood(road & (depth > 0), seeds)
        wo = connected_flood(road & (depth > -u), seeds)
        d = np.where(wb, depth, np.nan)
        qa_levels[f'{H:.1f}'] = {
            'pct_pess': round(100*wp.sum()/total,1), 'pct_best': round(100*wb.sum()/total,1),
            'pct_opt': round(100*wo.sum()/total,1),
            'depth_med': round(float(np.nanmedian(d)),2) if np.isfinite(d).any() else None,
            'depth_max': round(float(np.nanmax(d)),2) if np.isfinite(d).any() else None,
            'kzs': H > KZS_NOTE_LEVEL}
        layers[H] = (wp, wb, wo, depth)
        log(f'  H={H:.1f}м: {qa_levels[f"{H:.1f}"]["pct_best"]}% '
            f'[{qa_levels[f"{H:.1f}"]["pct_pess"]}..{qa_levels[f"{H:.1f}"]["pct_opt"]}%]'
            + (' [КЗС]' if H > KZS_NOTE_LEVEL else ''))
    import io, base64
    from PIL import Image
    import folium
    def layer_png(wp, wb, wo, depth):
        code = np.full(h.shape, 255, dtype='uint8'); code[road] = 0
        d = np.clip(depth, 0, 3.0) / 3.0; code[wp] = (40 + d[wp]*189).astype('uint8')
        code[wo & ~wp] = 230
        cd, _, (w4,s4,e4,n4) = m01._reproject_categorical_for_display(code, code.copy(), transform, crs)
        rgba = np.zeros((*cd.shape, 4), dtype='uint8')
        from matplotlib import colormaps; cmap = colormaps['Blues']
        cm = (cd>=40) & (cd<=229); rgba[cm] = (cmap(0.35+0.65*(cd[cm].astype(float)-40)/189)*255).astype('uint8')
        rgba[cm,3] = 220; rgba[cd==230] = (120,170,220,110); rgba[cd==0] = (120,120,120,60)
        buf = io.BytesIO(); Image.fromarray(rgba).save(buf, format='PNG', optimize=True)
        return base64.b64encode(buf.getvalue()).decode(), (w4,s4,e4,n4)
    fm = None
    for H, (wp,wb,wo,depth) in layers.items():
        b64, (w4,s4,e4,n4) = layer_png(wp,wb,wo,depth)
        if fm is None:
            fm = folium.Map(location=[(s4+n4)/2,(w4+e4)/2], zoom_start=14, control_scale=True, tiles=None)
            fm.get_root().html.add_child(folium.Element('<style>.leaflet-image-layer{image-rendering:pixelated}</style>'))
            folium.TileLayer('OpenStreetMap', name='OSM').add_to(fm)
            folium.TileLayer(tiles='https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', attr='Esri', name='Спутник').add_to(fm)
        tag = ' ⚠КЗС' if H > KZS_NOTE_LEVEL else ''
        folium.raster_layers.ImageOverlay(image=f'data:image/png;base64,{b64}', bounds=[[s4,w4],[n4,e4]], opacity=0.9,
            name=f'H={H:.1f}м{tag} ({qa_levels[f"{H:.1f}"]["pct_best"]}%)', show=(H==2.0)).add_to(fm)
    folium.LayerControl(collapsed=False).add_to(fm)
    um=np.sqrt(FORM_SIGMA**2+SHIFT_SIGMA**2+0.15**2); ur=np.sqrt(FORM_SIGMA**2+SHIFT_SIGMA**2+0.30**2); ux=np.sqrt(FORM_SIGMA**2+SHIFT_SIGMA**2+0.80**2)
    fm.get_root().html.add_child(folium.Element(
        f'<div style="position:fixed;bottom:30px;left:30px;z-index:9999;background:white;padding:10px;border-radius:6px;font-size:11px;max-width:320px">'
        '<b>Затопление, м БС-77</b><br>плотный синий — ядро;<br>'
        f'светлая кайма — неопределённость (meas ±{um:.2f}, rec ±{ur:.2f}, ext ±{ux:.2f}м);<br>'
        f'<b>⚠>{KZS_NOTE_LEVEL}м</b> — отказ КЗС, не прогноз.<br>'
        'Запад (пк 100–1100) — интерполяция, кайма шире.</div>'))
    fm.save(str(OUT / 'flood_map_bolshoy.html')); log(f'✓ flood_map_bolshoy.html')
    qa = {'object': m01.MVP_NAME, 'model': 'bathtub + connectivity',
          'error_budget': {'form': FORM_SIGMA, 'shift': SHIFT_SIGMA, 'class': CLASS_SIGMA}, 'levels': qa_levels}
    (OUT / 'flood_qa.json').write_text(json.dumps(qa, ensure_ascii=False, indent=2)); log(f'✓ flood_qa.json')
if __name__ == '__main__': main()
