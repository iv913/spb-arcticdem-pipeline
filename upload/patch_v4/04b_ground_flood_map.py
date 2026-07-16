#!/usr/bin/env python3
"""
04b_ground_flood_map.py — priority-flood + HTML tile-render для ground_surface.

Запускается поверх ground_surface.tif (продукт 04_ground_surface.py):
  - h:         банд 1 (BS-77, м)
  - source:    банд 2 (1 measured, 2 reconstructed, 3 extrapolated/prior)
  - detail:    банд 3 (11..13 skel, 21..23 open, 31 suspect_water)

ГЕЙТ 4 (только водные seed): вода OSM + 5м береговой контакт.
ГЕЙТ 3-пост: suspect_water (detail==31) получает source=3 с σ=0.80м.

Выходы:
  download/vo_ground_flood_surface.tif   — h, u, z_spill, source (4 бэнда)
  download/vo_ground_flood_map.html      — тайловый ползунок + паспортные точки
  download/vo_ground_flood_qa.json       — метрики по уровням
"""
import io, json, base64, heapq, sys
from pathlib import Path
from datetime import datetime
import numpy as np, rasterio
from rasterio.features import geometry_mask
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location('m01', Path(__file__).parent / '01_owner_map.py')
m01 = _ilu.module_from_spec(_spec); _spec.loader.exec_module(m01)
_spec3 = _ilu.spec_from_file_location('flood03', Path(__file__).parent / '03_flood_map.py')
flood03 = _ilu.module_from_spec(_spec3); _spec3.loader.exec_module(flood03)
_spec4 = _ilu.spec_from_file_location('libv', Path(__file__).parent / 'lib_vertical.py')
libv = _ilu.module_from_spec(_spec4); _spec4.loader.exec_module(libv)
OUT = m01.OUT

REPORT_LEVELS = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0]
KZS_NOTE_LEVEL = 1.6
FORM_SIGMA = 0.107
CLASS_SIGMA = {1: 0.15, 2: 0.30, 3: 0.80}
VO_BBOX = m01.MVP_BBOX  # (30.20, 59.92, 30.32, 59.95) — тот же, что у owner_map
SLIDER_MIN, SLIDER_MAX, SLIDER_STEP = 0.0, 4.0, 0.1


def log(msg):
    print(f'[{datetime.now():%H:%M:%S}] {msg}', flush=True)


def load_ground():
    p = OUT / 'ground_surface.tif'
    if not p.exists():
        log(f'ERROR: нет {p} — запустите 04_ground_surface.py сначала'); sys.exit(1)
    with rasterio.open(str(p)) as src:
        h = src.read(1).astype('float32')
        source = src.read(2).astype('uint8')
        detail = src.read(3).astype('uint8')
        stat = (src.read(4).astype('uint8') if src.count >= 4
                else np.isin(detail, [11,12,13,21,22,23,31]).astype('uint8'))
        if src.nodata is not None:
            h[h == src.nodata] = np.nan
        return h, source, detail, stat, src.transform, src.crs


def load_shift_sigma():
    # ДЕФЕКТ РЕВЬЮ №4: датум и сигма — из общего lib_vertical (region='vo').
    vert = libv.load_vertical(OUT, region='vo', logger=log)
    return vert['sigma_m'], vert


def water_seed_mask(shape, transform, crs):
    """ГЕЙТ 4: seed только от воды OSM + береговой контакт (5м буфер)."""
    import geopandas as gpd
    osm = m01.fetch_osm(VO_BBOX)
    _, _, waters = m01.collect_layers(osm)
    water_utm = m01.merge_water(waters)
    seeds = np.zeros(shape, dtype=bool)
    if water_utm is None or water_utm.is_empty:
        # ДЕФЕКТ РЕВЬЮ №8: эта ветка возвращала ОДИН массив, а вызов
        # распаковывал три — при пустой воде (реальный сценарий при
        # залипшем кэше OSM) скрипт падал на распаковке вместо внятного
        # сообщения.
        log('  ⚠ вода OSM пуста — seed пустой! Проверьте кэш OSM и '
            'water_margin_deg в модуле 01.')
        return seeds, seeds.copy(), seeds.copy()
    g = gpd.GeoSeries([water_utm], crs=m01.UTM).to_crs(crs).iloc[0]
    # пиксели ВНУТРИ воды
    in_water = ~geometry_mask([g.__geo_interface__], transform=transform,
                               out_shape=shape, all_touched=True)
    seeds |= in_water
    # береговой контакт (дорога/двор в пределах 5м от воды)
    g_buf = gpd.GeoSeries([water_utm.buffer(5.0)], crs=m01.UTM).to_crs(crs).iloc[0]
    near_water = ~geometry_mask([g_buf.__geo_interface__], transform=transform,
                                out_shape=shape, all_touched=True)
    # seeds уже включает in_water; additionally mark road pixels near water as seeds
    # (priority-flood запустится от них в дополнение к самой воде)
    return seeds, near_water, in_water


def spill_levels(h, seeds):
    """Priority-flood: Z[px] = min max высота вдоль пути от ближайшего seed.
    Здания (NaN в h) автоматически исключаются как непроходимые барьеры."""
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


def main():
    log('='*60); log('04b_ground_flood_map: priority-flood на ground_surface'); log('='*60)
    shift_sigma, vert = load_shift_sigma()
    h, source, detail, stat, transform, crs = load_ground()
    valid = np.isfinite(h)
    land = libv.stat_denominator(stat_band=stat) & valid
    log(f'Ground surface: {int(valid.sum()):,} px; суша для статистики '
        f'{int(land.sum()):,} px ({int(land.sum())*4/1e6:.2f} км²)')

    # ДЕФЕКТ РЕВЬЮ №5: неопределённость — ПО КЛАССУ ПРОИСХОЖДЕНИЯ (detail),
    # а не по грубому source. Пиксель `open_accepted` принят потому, что
    # он в пределах ±TOL_ACCEPT=0.75м от prior — объявлять его σ=0.15м
    # (класс measured) значит утверждать точность, которой критерий его
    # приёмки не даёт. FORM_SIGMA (0.107м, измерена на Большом по фьюжну
    # стрипов) применяется только к пикселям скелета.
    u = libv.pixel_sigma(detail=detail, shift_sigma=shift_sigma,
                         form_sigma=FORM_SIGMA)
    u[~valid] = np.nan
    log('Бюджет неопр. по классам: ' + ', '.join(
        f'{code}:±{np.nanmedian(u[detail == code]):.2f}м'
        for code in (11, 12, 13, 21, 22, 23, 31)
        if (detail == code).any()))

    # ГЕЙТ 4: seed вода + береговой контакт
    log('ГЕЙТ 4: water seed + береговой контакт (5м)...')
    seeds, near_water, in_water = water_seed_mask(h.shape, transform, crs)
    # Seed = сама вода (h=NaN в зданиях, но вода OSM имеет h_fin в prior)
    # + пиксели dry ground рядом с водой (ворота затопления)
    dry_near = near_water & valid
    seeds_final = in_water | dry_near
    log(f'  Seed: всего {int(seeds_final.sum()):,} px '
        f'(в воде: {int(in_water.sum()):,}, dry у берега: {int(dry_near.sum()):,})')

    log('Priority-flood...')
    zs = spill_levels(h, seeds_final)
    log(f'  Z_spill: [{np.nanmin(zs):.2f}, {np.nanmax(zs):.2f}] м БС')

    # GIS-продукт
    tif = OUT / 'vo_ground_flood_surface.tif'
    prof = {'driver': 'GTiff', 'dtype': 'float32', 'count': 4, 'crs': crs,
            'transform': transform, 'width': h.shape[1], 'height': h.shape[0],
            'compress': 'deflate', 'nodata': -9999}
    with rasterio.open(str(tif), 'w', **prof) as dst:
        for bi, (arr, name) in enumerate(
            [(h, 'h_bs77 ground'),
             (u, 'uncertainty_m'),
             (zs, 'z_spill_bs77'),
             (source.astype('float32'), 'source 1/2/3')], start=1):
            a = arr.copy(); a[~np.isfinite(a)] = -9999
            dst.write(a.astype('float32'), bi); dst.set_band_description(bi, name)
    log(f'✓ {tif}')

    # Контрольная таблица
    # ДЕФЕКТ №7: знаменатель — только суша (водоёмы исключены).
    # ДЕФЕКТ №6: pess/opt симметричны по u (раньше u входила в порог
    # перелива только у оптимистичного сценария).
    total = int(land.sum()); qa_levels = {}
    for Hl in REPORT_LEVELS:
        pess, wet, opt = libv.flood_masks(h, zs, u, Hl)
        pess &= land; wet &= land; opt &= land
        depth = np.where(wet, Hl - h, np.nan)
        qa_levels[f'{Hl:.1f}'] = {
            'pct_pess': round(100*pess.sum()/max(1, total), 1),
            'pct_best': round(100*wet.sum()/max(1, total), 1),
            'pct_opt': round(100*opt.sum()/max(1, total), 1),
            'depth_med': round(float(np.nanmedian(depth)), 2) if wet.any() else None,
            'depth_max': round(float(np.nanmax(depth)), 2) if wet.any() else None,
            'by_class': {
                'meas': round(100*(wet & (source == 1)).sum()/max(1, (land & (source == 1)).sum()), 1),
                'rec': round(100*(wet & (source == 2)).sum()/max(1, (land & (source == 2)).sum()), 1),
                'ext': round(100*(wet & (source == 3)).sum()/max(1, (land & (source == 3)).sum()), 1),
            },
            'kzs': Hl > KZS_NOTE_LEVEL,
        }
        log(f'  H={Hl:.1f}: {qa_levels[f"{Hl:.1f}"]["pct_best"]}% '
            f'[{qa_levels[f"{Hl:.1f}"]["pct_pess"]}..{qa_levels[f"{Hl:.1f}"]["pct_opt"]}]'
            + (' [КЗС]' if Hl > KZS_NOTE_LEVEL else ''))

    # Дисплей-варп
    enc_lo = float(np.floor(min(np.nanmin(h), np.nanmin(zs[np.isfinite(zs)])) * 2) / 2 - 0.1) \
        if np.isfinite(zs).any() else float(np.floor(np.nanmin(h)*2)/2 - 0.1)
    enc_hi = float(np.ceil(max(np.nanmax(h), np.nanmax(zs[np.isfinite(zs)])) * 2) / 2 + 0.1) \
        if np.isfinite(zs).any() else float(np.ceil(np.nanmax(h)*2)/2 + 0.1)
    log(f'  Кодирование: [{enc_lo:.1f}, {enc_hi:.1f}]')

    def enc(a):
        out = np.zeros(a.shape, dtype='uint8'); m = np.isfinite(a)
        out[m] = 1 + np.clip((a[m]-enc_lo)/(enc_hi-enc_lo)*253, 0, 253).astype('uint8')
        return out
    h8 = enc(h); z8 = enc(zs)
    h8d, z8d, (w4, s4, e4, n4) = m01._reproject_categorical_for_display(h8, z8, transform, crs)
    s8 = source.copy(); s8[~valid] = 255
    s8d, _, _ = m01._reproject_categorical_for_display(s8, s8.copy(), transform, crs)
    from PIL import Image
    rgba = np.zeros((*h8d.shape, 4), dtype='uint8')
    vd = (h8d > 0) & (h8d != 255)
    rgba[..., 0] = np.where(vd, h8d, 0)
    rgba[..., 1] = np.where(vd, z8d, 0)
    rgba[..., 2] = np.where(vd & (s8d <= 3), s8d, 0)
    rgba[..., 3] = np.where(vd, 255, 0)
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
                        ri, ci = np.unravel_index(
                            np.nanargmin(np.abs(sub - p['h_pas'])), sub.shape)
                        hv = float(sub[ri, ci])
                        zv = float(zs[r1+ri, c1+ci])
                        uv = float(u[r1+ri, c1+ci])
                        break
                if not np.isfinite(hv): continue
                pts_js.append({'lat': round(float(p['lat']), 6),
                               'lon': round(float(p['lon']), 6),
                               'hp': round(float(p['h_pas']), 2),
                               'hs': round(hv, 2),
                               'zs': round(zv, 2),
                               'u': round(uv, 2)})
            log(f'  Паспортных точек на поверхности: {len(pts_js)}')
    except Exception as e:
        log(f'  ⚠ точки не вшиты: {e}')

    # HTML несёт только source (3 класса), а u теперь считается по detail.
    # Чтобы легенда и канва не расходились с GeoTIFF, в шаблон уходит
    # МЕДИАНА фактической u внутри каждого класса source.
    us = {}
    for cls in (1, 2, 3):
        m = valid & (source == cls) & np.isfinite(u)
        us[cls] = float(np.median(u[m])) if m.any() else \
            float(np.sqrt(shift_sigma**2 + libv.SOURCE_SIGMA[cls]**2))
    log('  u в HTML (медианы по source): ' +
        ', '.join(f'{c}:±{us[c]:.2f}м' for c in (1, 2, 3)))
    html = flood03.HTML_TEMPLATE
    for k, v in {'__B64__': b64, '__W4__': repr(w4), '__S4__': repr(s4),
                 '__E4__': repr(e4), '__N4__': repr(n4),
                 '__LO__': repr(enc_lo), '__HI__': repr(enc_hi),
                 '__U1__': f'{us[1]:.3f}', '__U2__': f'{us[2]:.3f}', '__U3__': f'{us[3]:.3f}',
                 '__KZS__': f'{KZS_NOTE_LEVEL:.1f}',
                 '__HMIN__': f'{SLIDER_MIN:.1f}', '__HMAX__': f'{SLIDER_MAX:.1f}',
                 '__HSTEP__': f'{SLIDER_STEP:.2f}',
                 '__TITLE__': 'В.О. — ground surface (дворы+площади)',
                 '__PTS__': json.dumps(pts_js, ensure_ascii=False)}.items():
        html = html.replace(k, v)
    (OUT / 'vo_ground_flood_map.html').write_text(html, encoding='utf-8')
    log(f'✓ vo_ground_flood_map.html (тайловый ползунок + {len(pts_js)} точек)')

    qa = {'object': 'В.О. ground surface (газоны+дворы+площади)',
          'model': 'priority-flood on ground_surface; tile-render; water-seed only (gate 4)',
          'error_budget': {'form_skeleton_only': FORM_SIGMA,
                           'shift': shift_sigma,
                           'datum_source': vert.get('source'),
                           'detail_sigma': {str(k): (None if not np.isfinite(v) else v)
                                            for k, v in libv.DETAIL_SIGMA.items()}},
          'report_levels': qa_levels,
          'n_passport_points': len(pts_js),
          'ground_px': int(valid.sum()),
          'denominator_px_land': total,
          'products': {'gis': 'vo_ground_flood_surface.tif',
                       'web': 'vo_ground_flood_map.html'}}
    (OUT / 'vo_ground_flood_qa.json').write_text(json.dumps(qa, ensure_ascii=False, indent=2))
    log(f'✓ vo_ground_flood_qa.json')


if __name__ == '__main__':
    main()
