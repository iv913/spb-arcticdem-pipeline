#!/usr/bin/env python3
"""
04c_compare_raw_filtered.py — карта сравнения «filtered vs raw» поверхностей В.О.

Три слоя (переключаются в LayerControl):
  1. Filtered — ground_surface.tif (skeleton + diffusion prior + water barrier)
                + priority-flood Z_spill на нём
  2. Raw DEM  — ArcticDEM − shift (DSM с машинами/мостами/кронами)
                + priority-flood Z_spill на нём
  3. Diff     — raw − filtered (что мы отфильтровали: красный = выше, синий = ниже)

Ползунок H работает поверх активного слоя (filtered/raw).
Паспортные точки подсвечивают совпадение/рассогласование на активной поверхности.

Выходы:
  download/vo_compare_map.html     — карта сравнения
  download/vo_compare_qa.json      — метрики
"""
import io, json, base64, heapq, sys
from pathlib import Path
from datetime import datetime
import numpy as np, rasterio
from rasterio.warp import reproject, Resampling
from rasterio.features import geometry_mask
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location('m01', Path(__file__).parent / '01_owner_map.py')
m01 = _ilu.module_from_spec(_spec); _spec.loader.exec_module(m01)
_spec4 = _ilu.spec_from_file_location('libv', Path(__file__).parent / 'lib_vertical.py')
libv = _ilu.module_from_spec(_spec4); _spec4.loader.exec_module(libv)
_spec3 = _ilu.spec_from_file_location('flood03', Path(__file__).parent / '03_flood_map.py')
flood03 = _ilu.module_from_spec(_spec3); _spec3.loader.exec_module(flood03)
OUT = m01.OUT
CACHE = m01.CACHE
DEM_PATH = m01.DEM_PATH
VO_BBOX = m01.MVP_BBOX

REPORT_LEVELS = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0]
KZS_NOTE_LEVEL = 1.6
FORM_SIGMA = 0.107
CLASS_SIGMA = {1: 0.15, 2: 0.30, 3: 0.80}
SLIDER_MIN, SLIDER_MAX, SLIDER_STEP = 0.0, 4.0, 0.1


def log(msg):
    print(f'[{datetime.now():%H:%M:%S}] {msg}', flush=True)


def load_filtered():
    """ground_surface.tif: h, source, detail (already BS-77)."""
    p = OUT / 'ground_surface.tif'
    if not p.exists():
        log(f'ERROR: нет {p}'); sys.exit(1)
    with rasterio.open(str(p)) as src:
        h = src.read(1).astype('float32')
        source = src.read(2).astype('uint8')
        detail = src.read(3).astype('uint8')
        stat = (src.read(4).astype('uint8') if src.count >= 4
                else np.isin(detail, [11,12,13,14,21,22,23,31]).astype('uint8'))
        if src.nodata is not None:
            h[h == src.nodata] = np.nan
        return h, source, detail, stat, src.transform, src.crs


def load_raw_on_grid(transform, crs, shape, shift):
    """ArcticDEM DSM − shift на той же сетке, что ground_surface."""
    out = np.full(shape, np.nan, dtype='float32')
    with rasterio.open(str(DEM_PATH)) as src:
        reproject(source=rasterio.band(src, 1), destination=out,
                  src_nodata=src.nodata, dst_nodata=np.nan,
                  dst_transform=transform, dst_crs=crs,
                  resampling=Resampling.bilinear)
    out -= shift
    return out


def load_shift():
    p = OUT / 'city_calibration.json'
    if p.exists():
        cc = json.loads(p.read_text())
        if cc.get('version') == 'v3':
            return float(cc.get('shift_m', 15.9)), float(cc.get('between_street_mad_m', 0.80))
    return 15.9, 0.80


def water_seed_mask(shape, transform, crs):
    import geopandas as gpd
    osm = m01.fetch_osm(VO_BBOX)
    _, _, waters = m01.collect_layers(osm)
    water_utm = m01.merge_water(waters)
    seeds = np.zeros(shape, dtype=bool)
    near = np.zeros(shape, dtype=bool)
    in_water = np.zeros(shape, dtype=bool)
    if water_utm is None or water_utm.is_empty:
        return seeds, near, in_water
    g = gpd.GeoSeries([water_utm], crs=m01.UTM).to_crs(crs).iloc[0]
    in_water = ~geometry_mask([g.__geo_interface__], transform=transform,
                               out_shape=shape, all_touched=True)
    g_buf = gpd.GeoSeries([water_utm.buffer(5.0)], crs=m01.UTM).to_crs(crs).iloc[0]
    near = ~geometry_mask([g_buf.__geo_interface__], transform=transform,
                          out_shape=shape, all_touched=True)
    seeds = in_water | (near & np.isfinite(np.zeros(shape)))  # in_water OR near
    return seeds, near, in_water


def spill_levels(h, seeds):
    H, W = h.shape
    Z = np.full((H, W), np.inf, dtype='float64')
    valid = np.isfinite(h)
    pq = []
    for r, c in zip(*np.where(seeds & valid)):
        z = float(h[r, c]); Z[r, c] = z; heapq.heappush(pq, (z, r, c))
    nbrs = ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1))
    while pq:
        z, r, c = heapq.heappop(pq)
        if z > Z[r, c]: continue
        for dr, dc in nbrs:
            rr, cc = r + dr, c + dc
            if 0 <= rr < H and 0 <= cc < W and valid[rr, cc]:
                nz = max(z, float(h[rr, cc]))
                if nz < Z[rr, cc]:
                    Z[rr, cc] = nz; heapq.heappush(pq, (nz, rr, cc))
    Z[~valid | np.isinf(Z)] = np.nan
    return Z.astype('float32')


def encode_u8(a, lo, hi):
    out = np.zeros(a.shape, dtype='uint8'); m = np.isfinite(a)
    out[m] = 1 + np.clip((a[m] - lo) / (hi - lo) * 253, 0, 253).astype('uint8')
    return out


def make_rgba(h8, z8, s8, valid_d):
    """R=h, G=z_spill, B=source (1/2/3), A=255 if valid."""
    rgba = np.zeros((*h8.shape, 4), dtype='uint8')
    rgba[..., 0] = np.where(valid_d, h8, 0)
    rgba[..., 1] = np.where(valid_d, z8, 0)
    rgba[..., 2] = np.where(valid_d & (s8 <= 3), s8, 0)
    rgba[..., 3] = np.where(valid_d, 255, 0)
    return rgba


def main():
    log('='*60); log('04c_compare: filtered vs raw DEM (V.O.)'); log('='*60)
    shift, shift_sigma = load_shift()
    log(f'Shift: {shift:.3f}м, σ_shift: {shift_sigma:.3f}м')

    # 1. Filtered surface
    h_f, source_f, detail_f, stat_f, transform, crs = load_filtered()
    valid_f = np.isfinite(h_f)
    log(f'Filtered: {int(valid_f.sum()):,} px')

    # 2. Raw DEM on same grid
    log('Loading raw ArcticDEM on filtered grid...')
    h_raw = load_raw_on_grid(transform, crs, h_f.shape, shift)
    valid_raw = np.isfinite(h_raw)
    log(f'Raw DEM: {int(valid_raw.sum()):,} px, '
        f'h range [{float(np.nanmin(h_raw)):.2f}, {float(np.nanmax(h_raw)):.2f}]')

    # 3. Diff = raw − filtered (only where both finite)
    diff = h_raw - h_f
    valid_diff = valid_f & valid_raw
    log(f'Diff (raw − filtered): {int(valid_diff.sum()):,} px, '
        f'median={float(np.nanmedian(diff[valid_diff])):+.2f}м, '
        f'IQR=[{float(np.nanpercentile(diff[valid_diff], 25)):+.2f}, '
        f'{float(np.nanpercentile(diff[valid_diff], 75)):+.2f}]')

    # 4. Water seeds
    log('Water seeds + 5m береговой контакт...')
    seeds, near_water, in_water = water_seed_mask(h_f.shape, transform, crs)
    seeds_final = in_water | (near_water & valid_f)
    log(f'  Seeds: {int(seeds_final.sum()):,} px')

    # 5. Priority-flood на filtered (уже может быть в vo_ground_flood_surface, но пересчитаем)
    log('Priority-flood на filtered...')
    Z_f = spill_levels(h_f, seeds_final)
    log(f'  Z_f: [{np.nanmin(Z_f):.2f}, {np.nanmax(Z_f):.2f}]')

    # 6. Priority-flood на raw DEM (НЕвалидные пиксели = здания/вода — барьеры)
    # Здания в raw DSM имеют огромные высоты (10-30м) — priority-flood их не пройдёт
    # Это и есть разница: filtered использует skeleton+prior, raw проходит сквозь здания!
    log('Priority-flood на raw DEM...')
    # Limit raw to plausible ground range to prevent huge Z propagation through buildings
    h_raw_clipped = h_raw.copy()
    h_raw_clipped[h_raw_clipped > 15] = np.nan  # clip tall buildings/trees as barriers
    h_raw_clipped[h_raw_clipped < -2] = np.nan
    Z_raw = spill_levels(h_raw_clipped, seeds_final)
    log(f'  Z_raw: [{np.nanmin(Z_raw):.2f}, {np.nanmax(Z_raw):.2f}]')

    # 7. Кодирование в RGBA
    enc_lo = -0.5
    enc_hi = 10.0
    log(f'Encoding h/Z range: [{enc_lo:.1f}, {enc_hi:.1f}]')

    h_f8 = encode_u8(h_f, enc_lo, enc_hi)
    z_f8 = encode_u8(Z_f, enc_lo, enc_hi)
    s_f8 = source_f.copy(); s_f8[~valid_f] = 255

    h_r8 = encode_u8(h_raw, enc_lo, enc_hi)
    z_r8 = encode_u8(Z_raw, enc_lo, enc_hi)
    s_r8 = np.full_like(source_f, 3)  # raw: всё "extrapolated" class (high σ)
    s_r8[~valid_raw] = 255

    # Diff: diverging colormap
    diff_lo, diff_hi = -5.0, 5.0
    diff_disp = np.clip(diff, diff_lo, diff_hi)
    diff_norm = (diff_disp - diff_lo) / (diff_hi - diff_lo)  # 0..1
    diff_rgba = np.zeros((*h_f.shape, 4), dtype='uint8')
    # 0..0.5 = синий (raw ниже), 0.5..1 = красный (raw выше)
    blue_mask = valid_diff & (diff_norm < 0.5)
    red_mask = valid_diff & (diff_norm >= 0.5)
    # Blue: 0..255 intensity (raw lower → darker blue)
    b_intensity = np.where(blue_mask, ((0.5 - diff_norm) * 2 * 255).clip(0, 255).astype('uint8'), 0)
    diff_rgba[blue_mask, 0] = 0
    diff_rgba[blue_mask, 1] = b_intensity[blue_mask] // 3
    diff_rgba[blue_mask, 2] = b_intensity[blue_mask]
    # Red: 0..255 intensity (raw higher → brighter red)
    r_intensity = np.where(red_mask, ((diff_norm - 0.5) * 2 * 255).clip(0, 255).astype('uint8'), 0)
    diff_rgba[red_mask, 0] = r_intensity[red_mask]
    diff_rgba[red_mask, 1] = r_intensity[red_mask] // 4
    diff_rgba[red_mask, 2] = 0
    diff_rgba[valid_diff, 3] = 230

    # 8. Reproject all to EPSG:3857 for display
    log('Warp to EPSG:3857 for display...')
    h_f_d, z_f_d, (w4, s4, e4, n4) = m01._reproject_categorical_for_display(h_f8, z_f8, transform, crs)
    s_f_d, _, _ = m01._reproject_categorical_for_display(s_f8, s_f8.copy(), transform, crs)

    h_r_d, z_r_d, _ = m01._reproject_categorical_for_display(h_r8, z_r8, transform, crs)
    s_r_d, _, _ = m01._reproject_categorical_for_display(s_r8, s_r8.copy(), transform, crs)

    diff_d, _, _ = m01._reproject_categorical_for_display(
        diff_rgba[..., 0], diff_rgba[..., 1], transform, crs)
    diff_d2, _, _ = m01._reproject_categorical_for_display(
        diff_rgba[..., 2], diff_rgba[..., 3], transform, crs)

    # 9. Pack RGBA PNGs
    from PIL import Image
    rgba_f = make_rgba(h_f_d, z_f_d, s_f_d, (h_f_d > 0) & (h_f_d != 255))
    rgba_r = make_rgba(h_r_d, z_r_d, s_r_d, (h_r_d > 0) & (h_r_d != 255))
    # diff: combine diff_d (R+G) and diff_d2 (B+A)
    rgba_diff = np.zeros((*diff_d.shape, 4), dtype='uint8')
    rgba_diff[..., 0] = diff_d
    rgba_diff[..., 1] = diff_d  # placeholder (we packed G into the same band)
    rgba_diff[..., 2] = diff_d2
    rgba_diff[..., 3] = diff_d2

    # Save 3 separate PNGs
    b64_f = base64.b64encode(Image.fromarray(rgba_f).tobytes()).decode() if False else None
    buf_f = io.BytesIO(); Image.fromarray(rgba_f).save(buf_f, format='PNG', optimize=True)
    b64_f = base64.b64encode(buf_f.getvalue()).decode()
    buf_r = io.BytesIO(); Image.fromarray(rgba_r).save(buf_r, format='PNG', optimize=True)
    b64_r = base64.b64encode(buf_r.getvalue()).decode()
    buf_d = io.BytesIO(); Image.fromarray(rgba_diff).save(buf_d, format='PNG', optimize=True)
    b64_d = base64.b64encode(buf_d.getvalue()).decode()
    log(f'PNG sizes: filtered={len(b64_f)/1e6:.1f}МБ, raw={len(b64_r)/1e6:.1f}МБ, diff={len(b64_d)/1e6:.1f}МБ')

    # 10. Паспортные точки (для обоих слоёв)
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
                if not (0 <= r0 < h_f.shape[0] and 0 <= c0 < h_f.shape[1]): continue
                hv_f, zv_f = np.nan, np.nan
                hv_r, zv_r = np.nan, np.nan
                for rad in (0, 2, 5, 8):
                    r1, r2 = max(0, r0-rad), min(h_f.shape[0], r0+rad+1)
                    c1, c2 = max(0, c0-rad), min(h_f.shape[1], c0+rad+1)
                    sub_f = h_f[r1:r2, c1:c2]
                    sub_r = h_raw[r1:r2, c1:c2]
                    if np.isfinite(sub_f).any() or np.isfinite(sub_r).any():
                        if np.isfinite(sub_f).any():
                            ri, ci = np.unravel_index(
                                np.nanargmin(np.abs(sub_f - p['h_pas'])), sub_f.shape)
                            hv_f = float(sub_f[ri, ci])
                            zv_f = float(Z_f[r1+ri, c1+ci])
                        if np.isfinite(sub_r).any():
                            ri2, ci2 = np.unravel_index(
                                np.nanargmin(np.abs(sub_r - p['h_pas'])), sub_r.shape)
                            hv_r = float(sub_r[ri2, ci2])
                            zv_r = float(Z_raw[r1+ri2, c1+ci2])
                        break
                if not (np.isfinite(hv_f) or np.isfinite(hv_r)): continue
                pts_js.append({'lat': round(float(p['lat']), 6),
                               'lon': round(float(p['lon']), 6),
                               'hp': round(float(p['h_pas']), 2),
                               'hs_f': round(hv_f, 2) if np.isfinite(hv_f) else None,
                               'zs_f': round(zv_f, 2) if np.isfinite(zv_f) else None,
                               'hs_r': round(hv_r, 2) if np.isfinite(hv_r) else None,
                               'zs_r': round(zv_r, 2) if np.isfinite(zv_r) else None,
                               'u': 0.80})
            log(f'  Паспортных точек: {len(pts_js)}')
    except Exception as e:
        log(f'  ⚠ точки не вшиты: {e}')

    # 11. Контрольные метрики
    qa_levels_f, qa_levels_r = {}, {}
    for Hl in REPORT_LEVELS:
        # ДЕФЕКТ: знаменателем было valid_f, куда после v4 попадает вода
        # на уровне 0.00 м БС — она залита при ЛЮБОМ уровне, и кривая
        # «filtered» выходила плоской (28.1% от 0.5 до 2.5 м) независимо
        # от того, что происходит с сушей.
        land_f = libv.stat_denominator(stat_band=stat_f) & valid_f
        wet_f = land_f & (Z_f < Hl)
        wet_r = valid_raw & (Z_raw < Hl)
        qa_levels_f[f'{Hl:.1f}'] = round(100*wet_f.sum()/max(1, land_f.sum()), 1)
        qa_levels_r[f'{Hl:.1f}'] = round(100*wet_r.sum()/max(1, valid_raw.sum()), 1)
        log(f'  H={Hl:.1f}: filtered {qa_levels_f[f"{Hl:.1f}"]}%  |  raw {qa_levels_r[f"{Hl:.1f}"]}%')

    # 12. HTML
    us = {1: float(np.sqrt(FORM_SIGMA**2 + shift_sigma**2 + 0.15**2)),
          2: float(np.sqrt(FORM_SIGMA**2 + shift_sigma**2 + 0.30**2)),
          3: float(np.sqrt(FORM_SIGMA**2 + shift_sigma**2 + 0.80**2))}
    html = HTML_TEMPLATE
    for k, v in {'__B64_F__': b64_f, '__B64_R__': b64_r, '__B64_D__': b64_d,
                 '__W4__': repr(w4), '__S4__': repr(s4), '__E4__': repr(e4), '__N4__': repr(n4),
                 '__LO__': repr(enc_lo), '__HI__': repr(enc_hi),
                 '__DLO__': repr(diff_lo), '__DHI__': repr(diff_hi),
                 '__U1__': f'{us[1]:.3f}', '__U2__': f'{us[2]:.3f}', '__U3__': f'{us[3]:.3f}',
                 '__KZS__': f'{KZS_NOTE_LEVEL:.1f}',
                 '__HMIN__': f'{SLIDER_MIN:.1f}', '__HMAX__': f'{SLIDER_MAX:.1f}',
                 '__HSTEP__': f'{SLIDER_STEP:.2f}',
                 '__PTS__': json.dumps(pts_js, ensure_ascii=False)}.items():
        html = html.replace(k, v)
    (OUT / 'vo_compare_map.html').write_text(html, encoding='utf-8')
    log(f'✓ vo_compare_map.html')

    qa = {'object': 'В.О. comparison: filtered vs raw DEM',
          'shift_m': shift, 'shift_sigma_m': shift_sigma,
          'diff_stats': {
              'n_px': int(valid_diff.sum()),
              'median': float(np.nanmedian(diff[valid_diff])),
              'p25': float(np.nanpercentile(diff[valid_diff], 25)),
              'p75': float(np.nanpercentile(diff[valid_diff], 75)),
              'p95': float(np.nanpercentile(diff[valid_diff], 95)),
          },
          'flood_levels_pct': {
              'filtered': qa_levels_f,
              'raw': qa_levels_r,
          },
          'n_passport_points': len(pts_js),
          'products': {'web': 'vo_compare_map.html'}}
    (OUT / 'vo_compare_qa.json').write_text(json.dumps(qa, ensure_ascii=False, indent=2))
    log(f'✓ vo_compare_qa.json')


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Сравнение: filtered vs raw — В.О.</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css"/>
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
<style>html,body,#map{height:100%;margin:0}
#panel{position:fixed;bottom:24px;left:24px;z-index:1000;background:#fff;padding:12px 16px;border-radius:8px;box-shadow:0 2px 12px rgba(0,0,0,.35);font:12px/1.5 sans-serif;width:360px}
#hval{font-size:20px;font-weight:700}#kzs{display:none;color:#b00;font-weight:600}input[type=range]{width:100%}
.lg{display:inline-block;width:14px;height:14px;vertical-align:-2px;border-radius:3px}
.layer-btn{padding:4px 10px;margin:2px;border:1px solid #888;background:#fff;cursor:pointer;border-radius:4px;font:12px sans-serif}
.layer-btn.active{background:#08306b;color:#fff;border-color:#08306b}</style></head><body>
<div id="map"></div>
<div id="panel"><b>В.О. — сравнение filtered vs raw</b><br>
<div style="margin:6px 0">
<button class="layer-btn active" id="btn-filtered">Filtered (skeleton+prior)</button>
<button class="layer-btn" id="btn-raw">Raw DEM</button>
<button class="layer-btn" id="btn-diff">Diff (raw − filtered)</button>
</div>
Уровень воды: <span id="hval">2.0</span> м БС-77 <span id="stats"></span><br>
<input type="range" id="hslider" min="__HMIN__" max="__HMAX__" step="__HSTEP__" value="2.0"><br>
<span id="kzs">⚠ выше __KZS__ м — сценарий отказа/перелива КЗС</span>
<div style="margin-top:6px">
<span class="lg" style="background:#08306b"></span> глубоко
<span class="lg" style="background:#6baed6"></span> мелко
<span class="lg" style="background:rgba(120,170,220,.55)"></span> кайма ±u
<span class="lg" style="background:#999"></span> сухо</div>
<div style="margin-top:4px;color:#666;font-size:11px">
Filtered: skeleton + diffusion prior + water barrier<br>
Raw: ArcticDEM − shift (DSM с машинами/мостами/кронами)<br>
Diff: красный = raw выше, синий = raw ниже (|Δ|>5м насыщен)
</div></div>
<script>
const B64_F="__B64_F__",B64_R="__B64_R__",B64_D="__B64_D__";
const LO=__LO__,HI=__HI__,DLO=__DLO__,DHI=__DHI__,U=[0,__U1__,__U2__,__U3__];
const W4=__W4__,S4=__S4__,E4=__E4__,N4=__N4__;let curH=2.0,curLayer='filtered';
const map=L.map('map',{center:[(S4+N4)/2,(W4+E4)/2],zoom:13});
L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',{attribution:'Esri',maxZoom:19}).addTo(map);
const P=L.Projection.SphericalMercator;const pNW=P.project(L.latLng(N4,W4)),pSE=P.project(L.latLng(S4,E4));
const MX0=pNW.x,MY0=pNW.y,MX1=pSE.x,MY1=pSE.y;
let DW=0,DH=0,hArrF=null,zArrF=null,sArrF=null,hArrR=null,zArrR=null,sArrR=null,grid=null;
let imgF=new Image(),imgR=new Image(),imgD=new Image();let loaded=0;
function loadImg(img,b64,cb){img.onload=cb;img.src="data:image/png;base64,"+b64;}
function decode(img){const t=document.createElement('canvas');t.width=img.width;t.height=img.height;
const tc=t.getContext('2d');tc.drawImage(img,0,0);return tc.getImageData(0,0,img.width,img.height).data;}
function decodeRGBA(img){const d=decode(img);const DW=img.width,DH=img.height;
const h=new Float32Array(DW*DH),z=new Float32Array(DW*DH),s=new Uint8Array(DW*DH);
for(let i=0;i<DW*DH;i++){if(d[4*i+3]===0||d[4*i]===0){h[i]=NaN;continue;}
h[i]=LO+(d[4*i]-1)/253*(HI-LO);z[i]=LO+(d[4*i+1]-1)/253*(HI-LO);s[i]=d[4*i+2];}
return{h:h,z:z,s:s,DW:DW,DH:DH};}
function onAllLoaded(){
DW=imgF.width;DH=imgF.height;
const f=decodeRGBA(imgF),r=decodeRGBA(imgR);
hArrF=f.h;zArrF=f.z;sArrF=f.s;hArrR=r.h;zArrR=r.z;sArrR=r.s;
grid=new FloodGrid({tileSize:256,opacity:0.9,bounds:[[S4,W4],[N4,E4]]}).addTo(map);
initPoints();updateStats();}
loadImg(imgF,B64_F,()=>{loaded++;if(loaded===3)onAllLoaded();});
loadImg(imgR,B64_R,()=>{loaded++;if(loaded===3)onAllLoaded();});
loadImg(imgD,B64_D,()=>{loaded++;if(loaded===3)onAllLoaded();});
function blues(t){return[Math.round(190-160*t),Math.round(220-150*t),Math.round(240-70*t)];}
function diffCol(d){const n=(Math.max(DLO,Math.min(DHI,d))-DLO)/(DHI-DLO);
if(n<0.5){const k=(0.5-n)*2;return[0,Math.round(k*60),Math.round(k*255)];}
else{const k=(n-0.5)*2;return[Math.round(k*255),Math.round(k*40),0];}}
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
const k=dy*DW+dx;
if(curLayer==='diff'){
const hf=hArrF[k],hr=hArrR[k];
if(!isFinite(hf)||!isFinite(hr)){o[i+3]=0;continue;}
const d=hr-hf;const c=diffCol(d);
o[i]=c[0];o[i+1]=c[1];o[i+2]=c[2];o[i+3]=225;
continue;}
const h=curLayer==='filtered'?hArrF[k]:hArrR[k];
if(!isFinite(h)){o[i+3]=0;continue;}
const zs=curLayer==='filtered'?zArrF[k]:zArrR[k];
const sv=curLayer==='filtered'?sArrF[k]:sArrR[k];
const u=U[sv]||0.8,depth=curH-h;
if(curH>zs&&depth>u){const c=blues(Math.min(depth,3)/3);o[i]=c[0];o[i+1]=c[1];o[i+2]=c[2];o[i+3]=225;
}else if(curH>zs-u&&depth>-u){o[i]=120;o[i+1]=170;o[i+2]=220;o[i+3]=120;
}else{o[i]=120;o[i+1]=120;o[i+2]=120;o[i+3]=55;}}}
ctx.putImageData(outd,0,0);}});
const PTS=__PTS__;const ptMarkers=[];
function initPoints(){for(const p of PTS){
const m=L.circleMarker([p.lat,p.lon],{radius:6,color:'#fff',weight:1.5,fillOpacity:0.95,fillColor:'#888'}).addTo(map);
m.bindPopup('');ptMarkers.push([m,p]);}}
function updatePoints(){let mism=0;
for(const [m,p] of ptMarkers){
const hp=p.hp,hs=curLayer==='filtered'?p.hs_f:p.hs_r,zs=curLayer==='filtered'?p.zs_f:p.zs_r;
if(!isFinite(hs)){m.setStyle({fillColor:'#888'});continue;}
const obsWet=curH>hp,predWet=curH>zs;
const agree=(obsWet===predWet)||(Math.abs(curH-hp)<=p.u);
let col;if(!agree){col='#d62728';mism++;}else col=obsWet?'#1f77b4':'#7a7a7a';
m.setStyle({fillColor:col});
m.setPopupContent('h_пасп='+hp.toFixed(2)+' м<br>'+
(curLayer==='filtered'?'<b>Filtered</b>':'<b>Raw</b>')+'<br>'+
'h_модель='+hs.toFixed(2)+' м (dH='+(hs-hp).toFixed(2)+')<br>z_spill='+zs.toFixed(2)+' м<br>при H='+curH.toFixed(1)+': паспорт '+(obsWet?'ПОД водой':'сухо')+', модель '+(predWet?'ПОД водой':'сухо'));}
return mism;}
function updateStats(){if(!hArrF)return;let wet=0,valid=0;
const hArr=curLayer==='filtered'?hArrF:hArrR,zArr=curLayer==='filtered'?zArrF:zArrR;
for(let k=0;k<DW*DH;k++){if(!isFinite(hArr[k]))continue;valid++;if(curH>zArr[k])wet++;}
let statsTxt='';
if(curLayer==='diff'){statsTxt=' — Δ (raw − filtered)';}
else{const mism=updatePoints();
statsTxt=' — под водой '+(100*wet/Math.max(1,valid)).toFixed(1)+'%'+(PTS.length?(' | точки: '+(PTS.length-mism)+'✓ '+(mism?mism+'✗':'')):'');}
document.getElementById('stats').textContent=statsTxt;}
function setLayer(name){
curLayer=name;
document.querySelectorAll('.layer-btn').forEach(b=>b.classList.remove('active'));
document.getElementById('btn-'+name).classList.add('active');
if(grid)grid.redraw();updateStats();}
document.getElementById('btn-filtered').onclick=()=>setLayer('filtered');
document.getElementById('btn-raw').onclick=()=>setLayer('raw');
document.getElementById('btn-diff').onclick=()=>setLayer('diff');
const slider=document.getElementById('hslider');let raf=null;
slider.addEventListener('input',()=>{curH=parseFloat(slider.value);
document.getElementById('hval').textContent=curH.toFixed(1);
document.getElementById('kzs').style.display=curH>__KZS__?'inline':'none';
if(raf)cancelAnimationFrame(raf);raf=requestAnimationFrame(()=>{if(grid)grid.redraw();updateStats();});});
</script></body></html>"""

if __name__ == '__main__':
    main()
