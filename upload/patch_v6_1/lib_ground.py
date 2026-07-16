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
# --- нижняя огибающая (v6) ---
ENV_PCTL = 5.0                    # перцентиль внутри блока
ENV_NEIGH = 3                     # блоков в окне огибающей (3x3)
ENV_RISE_MAX = 1.50               # м: насколько опора вправе быть выше огибающей
ENV_MIN_PX = 8                    # сколько кандидатов нужно блоку, чтобы он считался
MAX_STEP_M = 0.25                 # м на пиксель: круче — не земля, а обрыв


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
                 rise_max=ENV_RISE_MAX, max_step_m=MAX_STEP_M,
                 block_px=None, **pmf_kw):
    """Опорное поле: земные пиксели на открытых участках.

    Четыре условия (v6 добавила два последних, после того как замер по
    62 паспортным точкам показал, что 34% из них сели на пиксели выше
    6 м БС — крыши и сплошные кроны, при паспортных отметках 1.0-4.1 м):

      1. пиксель прошёл морфологический фильтр (не крона, не машина);
      2. он дальше open_min_m от фасада — иначе стереопара видит наклон
         здания, а не землю;
      3. он не выше rise_max над нижней огибающей крупного масштаба.
         Это ловит то, что размыкание пропускает по построению: корпус,
         которого нет в маске OSM, и сплошную крону размером больше
         окна фильтра — они выглядят как плато, а не как объект;
      4. он не на обрыве: подъём над минимумом окна 3x3 не круче
         max_step_m. Двумерный аналог отсечения резких подъёмов,
         которое работало в продольном профиле.

    Возвращает (anchors, ground, dist_b, env, info).
    """
    cand = ~building & ~water & np.isfinite(dsm)
    ground, _ = pmf_ground(dsm, cand, pixel_m=pixel_m, **pmf_kw)
    dist_b = openness_m(building, pixel_m)
    open0 = ground & (dist_b >= open_min_m)          # опора уровня v5

    block_report = None
    if block_px is None:
        block_px, block_report = choose_block_px(open0, pixel_m, dsm=dsm,
                                                 rise_max=rise_max)
    env, _ = low_envelope(dsm, open0, block_px)
    steep = steep_mask(dsm, pixel_m, max_step_m)

    above = open0 & np.isfinite(env) & ((dsm - env) > rise_max)
    anchors = open0 & ~above & ~steep
    info = {'block_px': int(block_px),
            'block_m': round(block_px * pixel_m, 1),
            'block_report': block_report,
            'rise_max_m': rise_max, 'max_step_m': max_step_m,
            'rejected_above_envelope_px': int(above.sum()),
            'rejected_steep_px': int((open0 & ~above & steep).sum()),
            'anchors_v5_px': int(open0.sum()), 'anchors_px': int(anchors.sum())}
    return anchors, ground, dist_b, env, info


def accept_dsm(dsm, prior, tol_up=0.50, tol_down=1.50):
    """Асимметричная приёмка пикселя как измеренной земли.

    Ошибка DSM односторонняя: из 62 паспортных точек 54 дали dH > 0 и
    лишь одна ушла ниже -0.8 м. Тень, крона, машина, крыша — всё
    ЗАВЫШАЕТ. Значит и допуски должны быть разными: вверх от опоры
    пиксель отпускается неохотно (там кроны), вниз — свободно, потому
    что пиксель ниже опоры чаще означает, что ошиблась опора, а не DSM.
    """
    d = dsm - prior
    return np.isfinite(d) & (d <= tol_up) & (d >= -tol_down)


def street_verdict(h_street_bs, h_anchor_bs, tol_m=0.50, min_px=20):
    """Согласуется ли измеренная улица с опорным полем.

    v6: вердикт асимметричен. Улица ВЫШЕ опоры — это каньон без
    отражений от дна, её надо мостить. Улица НИЖЕ опоры на метры
    физически невозможна: проезжая часть не лежит на шесть метров ниже
    соседних дворов. Такой случай означает, что завышена ОПОРА, и
    мостить улицу её значением было бы заменой хорошего плохим —
    ровно это и произошло с 15-й линией (Δ = -6.25 м).

    Возвращает (verdict, delta, n):
      'measured'       — сходится с опорой;
      'bridged'        — улица выше опоры, мостим;
      'anchor_suspect' — улица ниже опоры, опору в этом месте не
                         применяем и помечаем для разбора.
    """
    m = np.isfinite(h_street_bs) & np.isfinite(h_anchor_bs)
    n = int(m.sum())
    if n < min_px:
        return 'no_data', float('nan'), n
    delta = float(np.median(h_street_bs[m] - h_anchor_bs[m]))
    if delta > tol_m:
        return 'bridged', delta, n
    if delta < -tol_m:
        return 'anchor_suspect', delta, n
    return 'measured', delta, n


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


def split_low_pixels(low, adjacent_to=None, pixel_m=2.0, min_area_m2=2000.0,
                     dsm=None, body_median_max=0.15):
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
        if big and dsm is not None:
            # Третье условие: медиана компоненты должна быть ниже нуля БС.
            # Кромка причала лежит на +0.1..+0.3 м и связной бывает не
            # хуже реки — в прогоне v6 две паспортные точки снова сели на
            # «воду» с dH = -3.33 м.
            big = {i for i in big
                   if np.nanmedian(dsm[lab == i]) <= body_median_max}
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


# ============================================================
# v6: нижняя огибающая с ДИНАМИЧЕСКИМ масштабом
# ============================================================
def _coarse_quantile(arr, mask, block_px, q):
    H, W = arr.shape
    b = int(block_px)
    h2, w2 = -(-H // b), -(-W // b)
    pad = np.full((h2 * b, w2 * b), np.nan)
    pad[:H, :W] = np.where(mask & np.isfinite(arr), arr, np.nan)
    blocks = pad.reshape(h2, b, w2, b).transpose(0, 2, 1, 3).reshape(h2, w2, -1)
    cnt = np.isfinite(blocks).sum(axis=2)
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        out = np.nanpercentile(blocks, q, axis=2)
    return out, cnt


def estimate_relief_slope(dsm, open_mask, pixel_m=2.0, block_px=25,
                          min_px=ENV_MIN_PX, q_grad=25.0):
    """Робастная оценка уклона рельефа, м/м.

    Считается по нижнему квантилю в блоках: медиана модуля разности
    между соседними блоками, делённая на шаг. Медиана устойчива к тому,
    что часть блоков стоит на крышах.
    """
    c, cnt = _coarse_quantile(dsm, open_mask, block_px, 20.0)
    c[cnt < min_px] = np.nan
    step = block_px * pixel_m
    d = []
    for a, b_ in ((c[:, :-1], c[:, 1:]), (c[:-1, :], c[1:, :])):
        v = np.abs(a - b_)
        d.append(v[np.isfinite(v)])
    d = np.concatenate(d) if d else np.array([0.0])
    if d.size == 0:
        return 0.0
    # ДЕФЕКТ v6-прогона: здесь стояла МЕДИАНА модуля градиента, и она
    # оказалась загрязнена ровно тем, что огибающая должна убирать. В
    # прогоне выше огибающей отвергнуто 1.4 млн из 3.19 млн пикселей
    # предварительной опоры — 44%. При такой доле медиана уже сидит на
    # границе загрязнения: оценка вышла 8.19 м/км, то есть 34 м перепада
    # по острову, тогда как по 37 согласованным улицам размах медиан
    # даёт 0.8-1.1 м/км. Низкий перцентиль устойчив к любой доле
    # загрязнения меньше (100 - q)%.
    return float(np.percentile(d, q_grad) / step)


def choose_block_px(open_mask, pixel_m=2.0, dsm=None,
                    candidates=(10, 15, 25, 40, 60, 90),
                    rise_max=ENV_RISE_MAX, neigh=ENV_NEIGH,
                    terrain_budget=0.30, target_cover=0.90, min_px=ENV_MIN_PX):
    """Подобрать масштаб огибающей ПО ДАННЫМ.

    Логика (после разбора v5): окно ограничено СНИЗУ застройкой и
    СВЕРХУ рельефом, причём на Васильевском рельеф почти не ограничивает.
    Профиль Большого даёт 0.89 м/км: даже окно 500 м стоит 0.45 м
    законного перепада. Загрязнение же бывает крупным — дворовый корпус,
    которого нет в OSM, или сплошная крона на несколько дворов.

    Поэтому берём НАИБОЛЬШИЙ блок, у которого рельефная плата остаётся
    ниже terrain_budget от допуска rise_max. Это максимизирует
    устойчивость к большим загрязнённым плато, а именно на них v5 и
    сломалась. Дополнительно требуем, чтобы у почти каждого блока в
    окне neigh x neigh нашёлся блок с открытой землёй — иначе минимум
    по соседям будет взят по крышам.

    Возвращает (block_px, отчёт).
    """
    slope = (estimate_relief_slope(dsm, open_mask, pixel_m)
             if dsm is not None else 0.0)
    H, W = open_mask.shape
    report = {'relief_slope_m_per_km': round(slope * 1000, 3), 'candidates': []}
    feasible = []
    for b in candidates:
        h2, w2 = -(-H // b), -(-W // b)
        pad = np.zeros((h2 * b, w2 * b), dtype='int32')
        pad[:H, :W] = open_mask.astype('int32')
        cnt = pad.reshape(h2, b, w2, b).transpose(0, 2, 1, 3).sum(axis=(2, 3))
        nonempty = cnt >= min_px
        reach = maximum_filter(nonempty.astype('uint8'), size=neigh) > 0
        dom = cnt >= 0
        cover = float((reach & dom).sum()) / max(1, dom.sum())
        cost = slope * neigh * b * pixel_m
        okc = (cover >= target_cover) and (cost <= terrain_budget * rise_max)
        report['candidates'].append(
            {'block_m': round(b * pixel_m, 1),
             'window_m': round(neigh * b * pixel_m, 1),
             'reach_cover': round(cover, 3),
             'terrain_cost_m': round(cost, 3), 'feasible': bool(okc)})
        if okc:
            feasible.append(b)
    if feasible:
        chosen = max(feasible)
        report['fallback'] = False
    else:
        # Ни один кандидат не прошёл. Раньше здесь молча брался средний,
        # и «динамический масштаб» вырождался в константу — именно это и
        # случилось в прогоне v6 (все candidates feasible=false, выбран
        # 80 м). Теперь выбираем по наилучшей достижимости и говорим об
        # этом громко.
        best = max(report['candidates'], key=lambda c: (c['reach_cover'],
                                                        -c['window_m']))
        chosen = int(round(best['block_m'] / pixel_m))
        report['fallback'] = True
        report['fallback_reason'] = (
            'ни один кандидат не удовлетворил обоим критериям; '
            f'взят лучший по достижимости ({best["reach_cover"]:.2f})')
    report['chosen_block_m'] = round(chosen * pixel_m, 1)
    report['chosen_window_m'] = round(neigh * chosen * pixel_m, 1)
    return int(chosen), report


def low_envelope(dsm, open_mask, block_px, pctl=ENV_PCTL, neigh=ENV_NEIGH,
                 min_px=ENV_MIN_PX):
    """Нижняя огибающая рельефа на крупном масштабе.

    Идея ровно та, что «минимальный пиксель и есть настоящая земля», но
    устойчивая к шуму: в каждом блоке берётся не минимум, а нижний
    перцентиль, затем минимум по окну 3x3 блока, затем билинейная
    интерполяция обратно на пиксели.

    Крыша дворового корпуса, которого нет в OSM, и сплошная крона
    возвышаются над этой огибающей на 5-15 м — то, что морфологическое
    размыкание с окном 62 м не ловило, потому что видело плато, а не
    объект.
    """
    H, W = dsm.shape
    b = int(block_px)
    h2, w2 = -(-H // b), -(-W // b)
    pad = np.full((h2 * b, w2 * b), np.nan)
    pad[:H, :W] = np.where(open_mask & np.isfinite(dsm), dsm, np.nan)
    blocks = pad.reshape(h2, b, w2, b).transpose(0, 2, 1, 3).reshape(h2, w2, -1)
    cnt = np.isfinite(blocks).sum(axis=2)
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        coarse = np.nanpercentile(blocks, pctl, axis=2)
    coarse[cnt < min_px] = np.nan

    # заполнить пустые блоки и взять минимум по окну соседних блоков
    filled = coarse.copy()
    if np.isnan(filled).any():
        filled = build_prior(filled, domain=np.ones_like(filled, dtype=bool),
                             iters_per_level=40)
    env_c = minimum_filter(filled, size=neigh, mode='nearest')

    # билинейно вернуть на пиксельную сетку
    yy = (np.arange(H) + 0.5) / b - 0.5
    xx = (np.arange(W) + 0.5) / b - 0.5
    y0 = np.clip(np.floor(yy).astype(int), 0, h2 - 1)
    x0 = np.clip(np.floor(xx).astype(int), 0, w2 - 1)
    y1 = np.clip(y0 + 1, 0, h2 - 1)
    x1 = np.clip(x0 + 1, 0, w2 - 1)
    wy = np.clip(yy - y0, 0, 1)[:, None]
    wx = np.clip(xx - x0, 0, 1)[None, :]
    env = (env_c[np.ix_(y0, x0)] * (1 - wy) * (1 - wx) +
           env_c[np.ix_(y1, x0)] * wy * (1 - wx) +
           env_c[np.ix_(y0, x1)] * (1 - wy) * wx +
           env_c[np.ix_(y1, x1)] * wy * wx)
    return env.astype('float32'), env_c


def steep_mask(dsm, pixel_m=2.0, max_step_m=MAX_STEP_M):
    """Локальный обрыв: подъём над минимумом окна 3x3 круче допустимого.

    Двумерный аналог того же приёма, которым в профиле отсекались резкие
    подъёмы: настоящая земля в центре Петербурга не растёт на четверть
    метра за два метра по горизонтали, а край крыши, кроны или борт
    машины — растёт.
    """
    mn = minimum_filter(np.where(np.isfinite(dsm), dsm, np.inf), size=3)
    return np.isfinite(dsm) & np.isfinite(mn) & ((dsm - mn) > max_step_m)


def cap_far_field(prior, env, dist_m, recon_dist_m=40.0, allow_above=0.50):
    """Ограничить опорное поле снизу-вверх там, где опоры рядом нет.

    Замер v6: класс 23 (дальняя опора) даёт +1.51 м при MAD 0.71 — то
    есть в глубине кварталов, куда измеренная опора не достаёт, поле
    систематически завышено. Причина в том, что диффузия — это среднее,
    а ошибка DSM односторонняя: подмешиваются только завышенные соседи.

    Нижняя огибающая крупного масштаба по построению лежит НИЖЕ, и в
    дальней зоне она — более честная оценка. Поле в дальней зоне
    ограничивается сверху величиной env + allow_above.
    """
    far = np.isfinite(dist_m) & (dist_m > recon_dist_m) & np.isfinite(env)
    out = prior.copy()
    cap = env + allow_above
    fix = far & np.isfinite(prior) & (prior > cap)
    out[fix] = cap[fix]
    return out, int(fix.sum())
