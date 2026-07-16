#!/usr/bin/env python3
"""
04_ground_surface.py — расширение поверхности на всё, что не дома и деревья.

v3: water-only barrier (buildings transparent to diffusion),
    skel filtered by source (≤2, no extrapolated).
"""
import json, sys, warnings
from pathlib import Path
from datetime import datetime
import numpy as np, rasterio
from rasterio.warp import reproject, Resampling
from scipy.ndimage import distance_transform_edt, label
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location('m01', Path(__file__).parent / '01_owner_map.py')
m01 = _ilu.module_from_spec(_spec); _spec.loader.exec_module(m01)
_spec4 = _ilu.spec_from_file_location('libv', Path(__file__).parent / 'lib_vertical.py')
libv = _ilu.module_from_spec(_spec4); _spec4.loader.exec_module(libv)
OUT = m01.OUT
CACHE = m01.CACHE            # было захардкожено /home/z/my-project/...
DEM_PATH = m01.DEM_PATH
SKELETON_STACK = [OUT / 'road_surface.tif', OUT / 'vo_surface.tif']
OWNER_TIF = OUT / 'vo_owner_map.tif'
SHIFT_JSON = OUT / 'city_calibration.json'
TOL_ACCEPT = 0.75; WATER_GATE_BS = 0.3; RECON_DIST_M = 40.0
PIXEL_M = 2.0; JACOBI_ITERS = 60
SKEL_ACCEPT_SOURCES = (1, 2)   # только measured+reconstructed кормят prior
SKEL_SANE_BS = (0.0, 9.0)      # пиксельный санитарный гейт скелета, м БС
WATER_H_BS = 0.0               # уровень, на который сажаются водоёмы
WATER_MIN_AREA_M2 = 2000.0     # компонента ниже гейта крупнее -> водоём
MIN_COMPONENT_M2 = 5000.0      # огрызки суши без скелета не тащим

def log(msg): print(f'[{datetime.now():%H:%M:%S}] {msg}', flush=True)

def build_prior(h_known, water_barrier=None, iters_per_level=JACOBI_ITERS):
    """Пирамида nanmean + Jacobi. water_barrier = стены (NaN).
    Здания НЕ барьеры — prior течёт сквозь них (они не в скелете → чисто)."""
    if water_barrier is None:
        water_barrier = np.zeros_like(h_known, dtype=bool)
    levels = [h_known.astype('float64')]
    barr_levels = [water_barrier]
    while min(levels[-1].shape) > 8:
        a = levels[-1]; b = barr_levels[-1]
        H2, W2 = (a.shape[0]+1)//2, (a.shape[1]+1)//2
        pad = np.full((H2*2, W2*2), np.nan)
        pad[:a.shape[0], :a.shape[1]] = a
        blk = pad.reshape(H2, 2, W2, 2)
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            levels.append(np.nanmean(np.nanmean(blk, axis=3), axis=1))
        bpad = np.zeros((H2*2, W2*2), dtype=bool)
        bpad[:b.shape[0], :b.shape[1]] = b
        barr_levels.append(bpad.reshape(H2, 2, W2, 2).any(axis=(1, 3)))
    top = levels[-1]; barr = barr_levels[-1]
    valid_top = np.isfinite(top) & ~barr
    est = np.where(valid_top, top, np.nanmedian(top[valid_top]) if valid_top.any() else 0.0)
    for lev in range(len(levels)-2, -1, -1):
        a = levels[lev]; b = barr_levels[lev]
        up = np.repeat(np.repeat(est, 2, 0), 2, 1)[:a.shape[0], :a.shape[1]]
        # FIX: NaN from water at coarser levels propagates to non-water pixels.
        # Replace NaN upsamples with median of finite values.
        finite_up = up[np.isfinite(up)]
        if finite_up.size > 0:
            up = np.where(np.isfinite(up), up, np.nanmedian(finite_up))
        est = np.where(np.isfinite(a) & ~b, a, up)
        known = np.isfinite(a) & ~b
        for _ in range(iters_per_level):
            p = np.pad(est, 1, mode='edge')
            bp = np.pad(b, 1, constant_values=True)
            n1 = np.where(bp[:-2,1:-1], 0, p[:-2,1:-1])
            n2 = np.where(bp[2:,1:-1], 0, p[2:,1:-1])
            n3 = np.where(bp[1:-1,:-2], 0, p[1:-1,:-2])
            n4 = np.where(bp[1:-1,2:], 0, p[1:-1,2:])
            cnt = ((~bp[:-2,1:-1]).astype(float) + (~bp[2:,1:-1]).astype(float) +
                   (~bp[1:-1,:-2]).astype(float) + (~bp[1:-1,2:]).astype(float))
            cnt[cnt == 0] = 1
            nb = (n1 + n2 + n3 + n4) / cnt
            est = np.where(known, est, np.where(b, np.nan, nb))
    return est.astype('float32')

def split_low_pixels(low, pixel_m=PIXEL_M, min_area_m2=WATER_MIN_AREA_M2):
    """Пиксели ниже водного гейта -> (водоёмы, кромки).

    ДЕФЕКТ РЕВЬЮ №1, вторая линия обороны. Если owner-маска воды рваная
    (обрезанный bbox relation Невы + залипший кэш OSM), река уходит в
    open-домен и раньше попадала в поверхность как «земля» на уровне
    prior. Крупная связная компонента ниже нуля БС — это водоём, а не
    «подозрительная кромка причала»; разделяем по площади."""
    lab, n = label(low, structure=np.ones((3, 3)))
    bodies = np.zeros_like(low)
    if n:
        area_px = np.bincount(lab.ravel())
        big = np.where(area_px * pixel_m ** 2 >= min_area_m2)[0]
        big = big[big > 0]
        if big.size: bodies = np.isin(lab, big)
    return bodies, low & ~bodies


def components_with_skeleton(domain, skel_mask, pixel_m=PIXEL_M,
                             min_area_m2=MIN_COMPONENT_M2):
    """Оставить только компоненты суши, где есть скелет.

    После того как вода стала барьером, чужой берег — ОТДЕЛЬНАЯ
    компонента. Если в ней нет ни одного опорного пикселя, prior там
    брать неоткуда и в продукт она идти не должна. Это не замена
    admin-границе (мост может соединить компоненты), а дешёвая вторая
    линия обороны против течи гейта 1."""
    lab, n = label(domain, structure=np.ones((3, 3)))
    keep = np.zeros_like(domain); stats = []
    for i in range(1, n + 1):
        m = lab == i; a = int(m.sum()); ns = int((skel_mask & m).sum())
        stats.append({'component': i, 'px': a,
                      'area_km2': round(a * pixel_m ** 2 / 1e6, 3),
                      'skeleton_px': ns})
        if ns > 0 and a * pixel_m ** 2 >= min_area_m2: keep |= m
    stats.sort(key=lambda x: -x['px'])
    return keep, stats[:10]


def read_on_grid(path, ref_transform, ref_crs, shape, band=1, resamp=Resampling.bilinear):
    with rasterio.open(str(path)) as src:
        out = np.full(shape, np.nan, dtype='float32')
        reproject(source=rasterio.band(src, band), destination=out,
                  src_nodata=src.nodata, dst_nodata=np.nan,
                  dst_transform=ref_transform, dst_crs=ref_crs, resampling=resamp)
    return out

def main():
    log('='*60); log('04_ground_surface v3: water-barrier only, skel filtered'); log('='*60)
    if not OWNER_TIF.exists(): log(f'ERROR: нет {OWNER_TIF}'); sys.exit(1)
    with rasterio.open(str(OWNER_TIF)) as o:
        owner = o.read(1); transform, crs, shape = o.transform, o.crs, (o.height, o.width)
    OWNER = m01.OWNER_CODES; log(f'Сетка: {shape[1]}x{shape[0]}')

    # ДЕФЕКТ РЕВЬЮ №4: датум выбирает ОДНА функция, общая с vo_pipeline.
    vert = libv.load_vertical(OUT, region='vo', logger=log)
    shift = vert['shift_m']; shift_sigma = vert['sigma_m']

    dsm_ell = read_on_grid(DEM_PATH, transform, crs, shape)
    dsm = dsm_ell - shift

    # Скелет: приоритетный стек + ФИЛЬТР по качеству (source ≤ 2)
    skel = np.full(shape, np.nan, dtype='float32'); skel_src = np.zeros(shape, dtype='uint8')
    rej = {'source': 0, 'sane': 0}
    for p in reversed([q for q in SKELETON_STACK if q.exists()]):
        hh = read_on_grid(p, transform, crs, shape, band=1, resamp=Resampling.nearest)
        ss = read_on_grid(p, transform, crs, shape, band=2, resamp=Resampling.nearest)
        ss = np.nan_to_num(ss).astype('uint8')
        fin = np.isfinite(hh)
        # ДЕФЕКТ РЕВЬЮ №9: было `ss <= SKEL_MAX_SOURCE`, а nan_to_num
        # превращает nodata банда source в 0 — и 0 <= 2 истинно, то есть
        # пиксель с НЕИЗВЕСТНЫМ классом попадал в опору диффузии.
        ok_src = np.isin(ss, SKEL_ACCEPT_SOURCES)
        # пиксельный санитарный гейт: страховка от любой будущей течи
        # (в центре СПб улиц выше ~9 м БС нет, ниже 0 м — вода/артефакт)
        ok_sane = (hh >= SKEL_SANE_BS[0]) & (hh <= SKEL_SANE_BS[1])
        m = fin & ok_src & ok_sane
        skel[m] = hh[m]; skel_src[m] = ss[m]
        rej['source'] += int((fin & ~ok_src).sum())
        rej['sane'] += int((fin & ok_src & ~ok_sane).sum())
        log(f'  скелет += {p.name}: принято {int(m.sum()):,} из {int(fin.sum()):,} px')
    log(f'  отвергнуто: source(3/nodata)={rej["source"]:,}, '
        f'вне [{SKEL_SANE_BS[0]}, {SKEL_SANE_BS[1]}]м БС={rej["sane"]:,}')
    if not np.isfinite(skel).any(): log('ERROR: скелет пуст после гейта'); sys.exit(1)

    # Барьер для диффузии: ТОЛЬКО вода (здания прозрачны!)
    building = owner == OWNER['building']
    on_skel = np.isfinite(skel)
    water_osm = owner == OWNER['water']
    low = np.isfinite(dsm) & ~building & ~on_skel & (dsm <= WATER_GATE_BS)
    bodies, edges = split_low_pixels(low)
    water = water_osm | bodies
    log(f'  Вода: owner={int(water_osm.sum()):,} px, '
        f'+компоненты ниже {WATER_GATE_BS}м БС={int(bodies.sum()):,} px '
        f'({bodies.sum()*PIXEL_M**2/1e6:.2f} км²), кромки={int(edges.sum()):,} px')
    if int(bodies.sum()) > max(1, int(water_osm.sum())):
        log('  ⚠ водоёмов по высоте больше, чем в owner-маске: water-relation '
            'обрезан bbox либо подхвачен старый кэш OSM. Удалите '
            'dem_cache/osm/owner_map_osm*.json и перезапустите 01.')

    log('Prior (диффузия от скелета, water-only barrier)...')
    prior = build_prior(skel, water_barrier=water)

    # ДЕФЕКТ РЕВЬЮ (вторая линия обороны гейта 1): компоненты суши без
    # скелета в продукт не идут.
    land = ~water
    keep_land, comp_stats = components_with_skeleton(land, on_skel)
    dropped = land & ~keep_land
    log(f'  Компоненты суши (топ-10): {comp_stats}')
    log(f'  отброшено «без скелета/мелкое»: {int(dropped.sum()):,} px '
        f'({dropped.sum()*PIXEL_M**2/1e6:.2f} км²)')

    open_mask = keep_land & ~building & ~on_skel & np.isfinite(dsm) & np.isfinite(prior)
    accept = open_mask & (np.abs(dsm - prior) <= TOL_ACCEPT) & (dsm > WATER_GATE_BS)
    suspect = open_mask & edges
    replace = open_mask & ~accept & ~suspect
    dist_px = distance_transform_edt(~on_skel); near = dist_px * PIXEL_M <= RECON_DIST_M

    surface = np.full(shape, np.nan, dtype='float32'); source = np.zeros(shape, dtype='uint8')
    detail = np.zeros(shape, dtype='uint8'); stat = np.zeros(shape, dtype='uint8')
    sk = on_skel & keep_land
    surface[sk] = skel[sk]; source[sk] = np.where(skel_src[sk] > 0, skel_src[sk], 2)
    detail[sk] = 10 + source[sk]; stat[sk] = 1
    surface[accept] = dsm[accept]; source[accept] = 1; detail[accept] = 21; stat[accept] = 1
    surface[replace] = prior[replace]; source[replace] = np.where(near[replace], 2, 3)
    detail[replace] = np.where(near[replace], 22, 23); stat[replace] = 1
    surface[suspect] = prior[suspect]; source[suspect] = 3; detail[suspect] = 31; stat[suspect] = 1
    # ДЕФЕКТ РЕВЬЮ №8: водоёмы должны быть ВАЛИДНЫМ доменом на уровне
    # воды, а не NaN. При NaN seed модуля 03/04b садится на несуществующий
    # пиксель (spill_levels требует np.isfinite(h) под seed), Z_spill
    # выходит пустым, и приходится обходить это буфером берегового
    # контакта. В статистику вода при этом НЕ идёт (stat=0).
    surface[water] = WATER_H_BS; source[water] = 3; detail[water] = 32; stat[water] = 0
    detail[dropped & (detail == 0)] = 41       # чужой берег: вне продукта
    # здания остаются NaN -> барьеры для priority-flood

    n = {k: int((detail == v).sum()) for k, v in
         (('skel_meas',11),('skel_rec',12),('skel_ext',13),
          ('open_accepted',21),('canopy_to_prior',22),('far_prior',23),
          ('suspect_edge',31),('water_body',32),('no_skeleton',41))}
    tot = int(np.isfinite(surface).sum()); n_stat = int((stat == 1).sum())
    log(f'Поверхность: {tot:,} px; в статистике {n_stat:,} px '
        f'({n_stat*PIXEL_M**2/1e6:.2f} км²); {n}')
    open_total = n['open_accepted'] + n['canopy_to_prior'] + n['far_prior']
    log(f'  открытая земля принята: {100*n["open_accepted"]/max(1,open_total):.0f}%')
    if n_stat * PIXEL_M**2 / 1e6 > 20:
        log('  ⚠ площадь суши >20 км² — для Васильевского с о. Декабристов '
            'ожидается ~15–19 км². Проверьте гейт 1 (полигон острова).')

    out = OUT / 'ground_surface.tif'
    prof = {'driver':'GTiff','dtype':'float32','count':4,'crs':crs,'transform':transform,
            'width':shape[1],'height':shape[0],'compress':'deflate','nodata':-9999}
    with rasterio.open(str(out), 'w', **prof) as dst:
        a = surface.copy(); a[~np.isfinite(a)] = -9999
        dst.write(a, 1); dst.write(source.astype('float32'), 2)
        dst.write(detail.astype('float32'), 3); dst.write(stat.astype('float32'), 4)
        dst.set_band_description(1, 'h_bs77 ground')
        dst.set_band_description(2, 'source 1/2/3 (контракт модуля 03)')
        dst.set_band_description(3, 'detail 11-13 skel / 21-23 open / 31 edge / 32 water / 41 dropped')
        dst.set_band_description(4, 'stat 1=учитывать в % затопления')
    log(f'✓ {out}')
    log('  ВАЖНО: проценты «под водой» считать по band4==1 (вода исключена)')
    (OUT / 'ground_surface_qa.json').write_text(json.dumps(
        {'px': n, 'total_px': tot, 'stat_px': n_stat,
         'stat_area_km2': round(n_stat*PIXEL_M**2/1e6, 3),
         'shift_m': shift, 'shift_sigma_m': shift_sigma,
         'datum_source': vert['source'], 'tol_accept_m': TOL_ACCEPT,
         'skeleton': {'accepted_sources': list(SKEL_ACCEPT_SOURCES),
                      'sane_bs': list(SKEL_SANE_BS), 'rejected': rej},
         'water': {'osm_px': int(water_osm.sum()), 'bodies_px': int(bodies.sum()),
                   'edges_px': int(edges.sum()), 'level_bs': WATER_H_BS},
         'land_components_top10': comp_stats,
         'skeleton_stack': [p.name for p in SKELETON_STACK if p.exists()]}, ensure_ascii=False, indent=2))
    log(f'✓ ground_surface_qa.json')

if __name__ == '__main__': main()
