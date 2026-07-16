#!/usr/bin/env python3
"""
lib_ground.py — ядро перевёрнутой иерархии (v5).

ПОЧЕМУ ИЕРАРХИЯ ПЕРЕВЁРНУТА
---------------------------
Замер по 49 паспортным точкам показал, откуда берётся расхождение:

    класс пикселя                       n   медиана dH   MAD
    открытая земля, сырой DSM          11     +0.51 м   0.50
    скелет (улица)                      6     +1.62 м   2.15
    крона/машина -> prior               5     +2.25 м   2.25
    дальний prior                      22     +1.94 м   1.28

Единственный класс, который держится, — сырой ArcticDEM минус сдвиг на
ОТКРЫТОЙ земле. Значит датум верен, а ломается оценщик уровня улицы: на
линиях В.О. при H/W ~ 1 стерео не видит дна каньона, и нижняя огибающая
возвращает уровень фасада или кроны. Прореживание по скользящей медиане
этого не ловит принципиально — когда завышена улица целиком, медиана
завышена вместе с ней.

Поэтому опорой перестают быть улицы. Опора — открытая земля (дворы,
скверы, площади, набережные), у которой есть небо и настоящие отражения.
Улицы становятся ПРОИЗВОДНЫМИ: их уровень принимается из DSM только
если он согласуется с опорным полем, иначе улица «мостится» по полю.

Модуль чистый (numpy/scipy), без rasterio — чтобы его можно было
прогонять синтетикой отдельно от растровой обвязки.
"""
import warnings

import numpy as np
from scipy.ndimage import (distance_transform_edt, label,
                           minimum_filter, maximum_filter)

# --- морфологический фильтр земли ---
PMF_WINDOWS = (1, 3, 7, 15, 31)   # радиусы окна, px
PMF_SLOPE = 0.02                  # В.О. плоский: уклон улиц 0.1-0.3%
PMF_DH0 = 0.25                    # м, стартовый допуск
PMF_DH_MAX = 1.50                 # м, потолок допуска
# --- открытость ---
OPEN_MIN_M = 6.0                  # ближе к фасаду опору не берём


def openness_m(building, pixel_m=2.0):
    """Расстояние до ближайшего здания, м. Мера «видимости неба»:
    у стереопары в каньоне нет дна, и чем ближе фасад, тем сильнее
    наклон здания заливает пиксель."""
    return distance_transform_edt(~building) * pixel_m


def pmf_ground(dsm, mask, pixel_m=2.0, windows=PMF_WINDOWS,
               slope=PMF_SLOPE, dh0=PMF_DH0, dh_max=PMF_DH_MAX):
    """Прогрессивный морфологический фильтр земли (Zhang et al.).

    Серия размыканий (эрозия -> дилатация) с растущим окном. Объект,
    который «срезается» размыканием больше, чем допускает рельеф на этом
    масштабе, помечается как не-земля: крона, машина, киоск, навес.
    Допуск растёт с окном, потому что на большом окне законный перепад
    рельефа больше.

    Возвращает (ground_mask, z_ground): маску земных пикселей и
    сглаженную поверхность после последнего размыкания.
    """
    valid = mask & np.isfinite(dsm)
    z = np.where(valid, dsm, np.nan).astype('float64')
    ground = valid.copy()
    zf = z.copy()
    for w in windows:
        size = 2 * int(w) + 1
        er = minimum_filter(np.where(np.isfinite(zf), zf, np.inf), size=size)
        er = np.where(np.isfinite(er), er, -np.inf)
        op = maximum_filter(er, size=size)
        op = np.where(np.isfinite(op), op, np.nan)
        dh = min(dh_max, slope * (w * pixel_m) + dh0)
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            flagged = valid & np.isfinite(op) & ((zf - op) > dh)
        ground &= ~flagged
        zf = np.where(np.isfinite(op) & np.isfinite(zf),
                      np.minimum(zf, op), zf)
    return ground, zf.astype('float32')


def anchor_field(dsm, building, water, pixel_m=2.0, open_min_m=OPEN_MIN_M,
                 **pmf_kw):
    """Опорное поле: земные пиксели на ОТКРЫТЫХ участках.

    Два условия сразу: (1) пиксель прошёл морфологический фильтр, то
    есть это не крона и не машина; (2) он дальше open_min_m от здания,
    то есть у стереопары был шанс увидеть землю.
    """
    cand = ~building & ~water & np.isfinite(dsm)
    ground, z_open = pmf_ground(dsm, cand, pixel_m=pixel_m, **pmf_kw)
    dist_b = openness_m(building, pixel_m)
    anchors = ground & (dist_b >= open_min_m)
    return anchors, ground, dist_b


def build_prior(h_known, domain=None, iters_per_level=60):
    """Пирамида nanmean вниз + МАСКИРОВАННЫЙ Jacobi вверх.

    domain — где prior имеет смысл и через что он вправе течь. Соседи
    вне домена не участвуют в среднем, поэтому вода работает барьером:
    уровень одного берега не протекает на другой.
    """
    h_known = np.asarray(h_known, dtype='float64')
    if domain is None:
        domain = np.ones(h_known.shape, dtype=bool)
    known = np.isfinite(h_known) & domain

    vals = [np.where(known, h_known, np.nan)]
    doms = [domain.copy()]
    while min(vals[-1].shape) > 8:
        a, d = vals[-1], doms[-1]
        H2, W2 = (a.shape[0] + 1) // 2, (a.shape[1] + 1) // 2
        pa = np.full((H2 * 2, W2 * 2), np.nan)
        pd = np.zeros((H2 * 2, W2 * 2), dtype=bool)
        pa[:a.shape[0], :a.shape[1]] = a
        pd[:d.shape[0], :d.shape[1]] = d
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            vals.append(np.nanmean(np.nanmean(
                pa.reshape(H2, 2, W2, 2), axis=3), axis=1))
        doms.append(pd.reshape(H2, 2, W2, 2).any(axis=3).any(axis=1))

    top, dtop = vals[-1], doms[-1]
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        fill = (np.nanmedian(top[dtop])
                if np.isfinite(top[dtop]).any() else 0.0)
    est = np.where(np.isfinite(top), top, fill)

    for lev in range(len(vals) - 2, -1, -1):
        a, d = vals[lev], doms[lev]
        up = np.repeat(np.repeat(est, 2, 0), 2, 1)[:a.shape[0], :a.shape[1]]
        est = np.where(np.isfinite(a), a, up)
        est = np.nan_to_num(est, nan=fill)
        k = np.isfinite(a)
        free = d & ~k
        if not free.any():
            continue
        for _ in range(iters_per_level):
            v = np.where(d, est, 0.0)
            w = d.astype('float64')
            pv = np.pad(v, 1, mode='edge')
            pw = np.pad(w, 1, mode='edge')
            num = (pv[:-2, 1:-1] + pv[2:, 1:-1] + pv[1:-1, :-2] + pv[1:-1, 2:])
            den = (pw[:-2, 1:-1] + pw[2:, 1:-1] + pw[1:-1, :-2] + pw[1:-1, 2:])
            nb = np.divide(num, den, out=est.copy(), where=den > 0)
            est = np.where(free, nb, est)

    out = est.astype('float32')
    out[~domain] = np.nan
    return out


def split_low_pixels(low, adjacent_to=None, pixel_m=2.0, min_area_m2=2000.0):
    """Пиксели ниже водного гейта -> (водоёмы, кромки).

    Компонента признаётся водоёмом, если она И крупная, И примыкает к
    известной воде (owner-маска). Требование примыкания добавлено после
    того, как пять паспортных точек на набережной сели на пиксели,
    объявленные водой: одной площади мало, кромка причала тоже бывает
    большой и связной.
    """
    lab, n = label(low, structure=np.ones((3, 3)))
    bodies = np.zeros_like(low)
    if n:
        area_px = np.bincount(lab.ravel())
        big = set(int(i) for i in np.where(
            area_px * pixel_m ** 2 >= min_area_m2)[0] if i > 0)
        if adjacent_to is not None and adjacent_to.any():
            grown = maximum_filter(adjacent_to.astype('uint8'), size=5) > 0
            touching = set(int(i) for i in np.unique(lab[grown]) if i > 0)
            big &= touching
        if big:
            bodies = np.isin(lab, sorted(big))
    return bodies, low & ~bodies


def components_with_skeleton(domain, anchor_mask, pixel_m=2.0,
                             min_area_m2=5000.0):
    """Оставить компоненты суши, в которых есть опора."""
    lab, n = label(domain, structure=np.ones((3, 3)))
    keep = np.zeros_like(domain)
    stats = []
    for i in range(1, n + 1):
        m = lab == i
        a = int(m.sum())
        na = int((anchor_mask & m).sum())
        stats.append({'component': i, 'px': a,
                      'area_km2': round(a * pixel_m ** 2 / 1e6, 3),
                      'anchor_px': na})
        if na > 0 and a * pixel_m ** 2 >= min_area_m2:
            keep |= m
    stats.sort(key=lambda s: -s['px'])
    return keep, stats[:10]


def street_verdict(h_street_bs, h_anchor_bs, tol_m=0.50, min_px=20):
    """Согласуется ли измеренная улица с опорным полем.

    Это замена per-street санитарного гейта по абсолютной высоте: тот
    ловил только грубые провалы (13-16 м БС), а равномерное завышение
    улицы на 1.5-2 м проходило насквозь, потому что сравнивать было не
    с чем. Теперь есть с чем — с независимой опорой рядом.

    Возвращает (verdict, delta, n): 'measured' | 'bridged' | 'no_data'.
    """
    m = np.isfinite(h_street_bs) & np.isfinite(h_anchor_bs)
    n = int(m.sum())
    if n < min_px:
        return 'no_data', float('nan'), n
    delta = float(np.median(h_street_bs[m] - h_anchor_bs[m]))
    return ('measured' if abs(delta) <= tol_m else 'bridged'), delta, n


def nearest_finite(arr, r0, c0, radii=(0, 2, 5, 8)):
    """Ближайший ГЕОМЕТРИЧЕСКИ пиксель с конечным значением.

    Раньше QA паспортных точек выбирало в радиусе пиксель, БЛИЖАЙШИЙ ПО
    ВЫСОТЕ к паспортной отметке (`nanargmin(|sub - h_pas|)`). Это метрика,
    подыгрывающая модели: если центральный пиксель NaN, поиск находил в
    окрестности самый удобный. Теперь берётся ближайший по расстоянию —
    результат честный, пусть и хуже.

    Возвращает (r, c) или None.
    """
    H, W = arr.shape
    for rad in radii:
        r1, r2 = max(0, r0 - rad), min(H, r0 + rad + 1)
        c1, c2 = max(0, c0 - rad), min(W, c0 + rad + 1)
        sub = arr[r1:r2, c1:c2]
        fin = np.isfinite(sub)
        if not fin.any():
            continue
        rr, cc = np.nonzero(fin)
        d2 = (rr + r1 - r0) ** 2 + (cc + c1 - c0) ** 2
        i = int(np.argmin(d2))
        return int(rr[i] + r1), int(cc[i] + c1)
    return None
