#!/usr/bin/env python3
"""
lib_strips.py — многовременной стек ArcticDEM: коregистрация, нижняя
огибающая ПО ВРЕМЕНИ, измеренная пиксельная сигма, сезонное смещение и
по-пиксельный выбор эпохи.

ЗАЧЕМ
-----
Перекрёстное сравнение стрипов было в пилоте (multi_dem_profile ->
02b_profile_fusion) и дало точность формы 0.107 м на Большом проспекте.
При переходе на остров оно выпало: 04a/04/04_vo_pipeline читают одну
мозаику v4.1. Единственный след — константа FORM_SIGMA = 0.107,
измеренная на одной улице и применяемая ко всему острову.

Это важнее, чем было в пилоте, потому что опора теперь строится по
ОТКРЫТОЙ земле — дворы, скверы, площади, парковки, — то есть ровно там,
где стоят машины и лежит снег.

Разделение по артефактам:
  * машины морфология ловит (2-3 пикселя при шаге 2 м, размыкание с
    окном 14 м их срезает) — здесь стрипы лишь подстраховка;
  * СНЕГ морфология не ловит принципиально. Снежный покров — гладкое
    протяжённое плато: фильтр видит рельеф, огибающая видит равномерно
    поднятую поверхность, тест на обрыв не видит ничего. Различить
    можно только сравнением дат.

ГЛАВНЫЙ ПРИНЦИП: тот же, что уже применён в пространстве, только по оси
времени. Машины уезжают, снег тает, ларьки переставляют — всё это
поверхность только ПОДНИМАЕТ. Значит земля — это нижняя огибающая по
датам, а не среднее по ним.

Модуль чистый (numpy/scipy), без rasterio: растровую обвязку делает
драйвер, а ядро проверяется синтетикой отдельно.
"""
import warnings

import numpy as np

# --- коregистрация ---
COREG_MAX_ITER = 5
COREG_TILT_MAX_M_PER_KM = 5.0     # больше — стрип бракуется
COREG_RESID_MAX_M = 0.60          # MAD остатка после подгонки
# --- временная огибающая ---
GROUND_PCTL = 20.0                # нижний перцентиль по датам
MIN_OBS = 3                       # меньше наблюдений — доверия нет
SIGMA_FLOOR_M = 0.10              # пол измеренной сигмы
SIGMA_FEW_OBS_M = 0.40            # надбавка, когда наблюдений мало
# --- сезон ---
WINTER_MONTHS = (11, 12, 1, 2, 3, 4)
EPOCH_GAP_DAYS = 7                # ближе — одна съёмочная сессия, не две даты
MIN_SUMMER_EPOCHS = 2             # меньше — летней огибающей не доверяем


def _mad(x):
    x = x[np.isfinite(x)]
    if x.size == 0:
        return np.nan
    return float(np.median(np.abs(x - np.median(x))) * 1.4826)


def coregister_plane(ref, src, stable_mask, pixel_m=2.0,
                     max_iter=COREG_MAX_ITER,
                     tilt_max=COREG_TILT_MAX_M_PER_KM,
                     resid_max=COREG_RESID_MAX_M):
    """Вертикальная коregистрация стрипа к опорной поверхности плоскостью.

    Подгоняется dh(x, y) = a + b*x + c*y робастно (итеративное отсечение
    по 3*MAD) на пикселях stable_mask — устойчивой земле.

    Почему плоскость, а не Nuth & Kaab. Классический метод оценивает
    горизонтальный сдвиг по зависимости dh/tan(наклон) от экспозиции, и
    ему НУЖЕН рельеф. Васильевский остров плоский: уклон порядка 1 м/км,
    то есть 0.1%, и задача вырождается — оценка горизонтального сдвига
    будет шумом, помноженным на огромный множитель. Поэтому здесь честнее
    вертикальная плоскость: она снимает и постоянное смещение, и наклон
    стрипа, а горизонтальный сдвиг на плоской местности почти не влияет
    на высоту.

    Возвращает (src_corrected, info).
    """
    H, W = ref.shape
    yy, xx = np.mgrid[0:H, 0:W]
    x = (xx - W / 2.0) * pixel_m
    y = (yy - H / 2.0) * pixel_m
    d = src - ref
    use = stable_mask & np.isfinite(d)
    info = {'n': int(use.sum()), 'status': 'ok',
            'shift_m': None, 'tilt_x_m_per_km': None,
            'tilt_y_m_per_km': None,
            'mad_before_m': _mad(d[use]) if use.any() else None,
            'mad_after_m': None}
    if use.sum() < 100:
        info['status'] = 'too_few_stable_px'
        return src.copy(), info

    keep = use.copy()
    coef = np.zeros(3)
    for _ in range(max_iter):
        A = np.stack([np.ones(keep.sum()), x[keep], y[keep]], axis=1)
        coef, *_ = np.linalg.lstsq(A, d[keep], rcond=None)
        fit = coef[0] + coef[1] * x + coef[2] * y
        r = d - fit
        m = _mad(r[use])
        if not np.isfinite(m) or m == 0:
            break
        new = use & (np.abs(r) <= 3 * m)
        if new.sum() < 100 or (new == keep).all():
            keep = new if new.sum() >= 100 else keep
            break
        keep = new

    fit = coef[0] + coef[1] * x + coef[2] * y
    out = src - fit
    resid = (out - ref)[keep]
    info.update({'shift_m': round(float(coef[0]), 3),
                 'tilt_x_m_per_km': round(float(coef[1] * 1000), 3),
                 'tilt_y_m_per_km': round(float(coef[2] * 1000), 3),
                 'mad_after_m': round(_mad(resid), 3),
                 'n_inlier': int(keep.sum())})

    tilt = float(np.hypot(coef[1], coef[2]) * 1000)
    if tilt > tilt_max:
        info['status'] = 'dropped_tilt'
    elif info['mad_after_m'] is not None and info['mad_after_m'] > resid_max:
        info['status'] = 'dropped_resid'
    return out, info


def _z_of(q):
    """Квантиль стандартного нормального распределения (бисекция)."""
    from math import erf, sqrt
    lo, hi = -6.0, 6.0
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if 0.5 * (1 + erf(mid / sqrt(2))) < q:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def _lower_half_sigma(cube, med):
    """Диагностический разброс нижней половины выборки.

    НЕ годится как оценка шума для поправки: при двумодальном
    распределении (снег на половине дат) он меряет расстояние до снега,
    а не шум. Оставлен как индикатор — большие значения помечают
    пиксели, где даты расходятся односторонне.
    """
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        d = med[None, :, :] - cube
        d = np.where(np.isfinite(d) & (d >= 0), d, np.nan)
        return np.nanmedian(d, axis=0) * 1.4826


def stack_temporal(layers, pctl=GROUND_PCTL, min_obs=MIN_OBS,
                   noise_sigma=None):
    """Пиксельная нижняя огибающая ПО ДАТАМ.

    layers — список 2D-массивов, уже приведённых к общей сетке и
    коregистрированных. NaN = нет данных.

    Берётся нижний перцентиль, а не минимум: минимум ловит провалы
    стерео и одиночные выбросы вниз, а нижний перцентиль устойчив и при
    этом сохраняет односторонний смысл — машина, снег, навес поверхность
    только поднимают.

    Возвращает dict:
      ground   — оценка земли по времени
      n_obs    — сколько дат видели пиксель
      spread   — MAD по датам: ИЗМЕРЕННАЯ неопределённость, а не таблица
      rise     — median - ground: сколько «стоит» над землёй (прокси
                 машин, снега, крон и временных объектов)
    """
    if not layers:
        raise ValueError('пустой стек')
    cube = np.stack([np.asarray(l, dtype='float64') for l in layers], axis=0)
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        n_obs = np.isfinite(cube).sum(axis=0)
        ground = np.nanpercentile(cube, pctl, axis=0)
        med = np.nanmedian(cube, axis=0)
        spread = np.nanmedian(np.abs(cube - med[None, :, :]), axis=0) * 1.4826
    sigma_low = _lower_half_sigma(cube, med)
    if noise_sigma:
        # Нижний перцентиль СМЕЩЁН ВНИЗ на шум: при гауссовом шуме
        # E[p20] = mu - 0.84*sigma. При шести датах и шуме 0.06 м это
        # три сантиметра, но при четырёх датах и реальном шуме стерео
        # 0.3 м — уже четверть метра, и она уйдёт прямо в занижение опоры.
        #
        # ВАЖНО, чем оценивать шум. Соблазнительно взять разброс нижней
        # половины выборки по пикселю, но он ломается ровно там, где
        # многовременной подход и нужен: если снег лежит на половине
        # дат, распределение двумодально, «разброс нижней половины»
        # меряет расстояние до снега и поправка возвращает снег обратно.
        # Поэтому шум задаётся СКАЛЯРОМ извне — его даёт коregистрация
        # (MAD остатка после подгонки плоскости), где он измерен на
        # устойчивой земле и загрязнением не испорчен.
        ground = ground + abs(_z_of(pctl / 100.0)) * float(noise_sigma)

    bad = n_obs < min_obs
    ground = np.where(bad, np.nan, ground).astype('float32')
    spread = np.where(bad, np.nan, spread).astype('float32')
    rise = np.where(bad, np.nan, med - np.nan_to_num(ground, nan=np.nan))
    return {'ground': ground, 'n_obs': n_obs.astype('int16'),
            'spread': spread.astype('float32'),
            'sigma_low': np.where(bad, np.nan, sigma_low).astype('float32'),
            'rise': rise.astype('float32'),
            'median': med.astype('float32')}


def temporal_sigma(spread, n_obs, min_obs=MIN_OBS, floor=SIGMA_FLOOR_M,
                   few_obs_penalty=SIGMA_FEW_OBS_M):
    """Измеренная пиксельная сигма вместо рукописной таблицы классов.

    Сейчас неопределённость назначается по происхождению пикселя
    (DETAIL_SIGMA), то есть по гипотезе о том, как он получен. Разброс
    между датами — это измерение того же самого. Где дат мало, к сигме
    добавляется штраф: три наблюдения не дают права на узкую оценку.
    """
    s = np.asarray(spread, dtype='float32')
    n = np.asarray(n_obs)
    out = np.maximum(np.nan_to_num(s, nan=np.inf), floor)
    penal = np.where(n < min_obs + 2, few_obs_penalty, 0.0)
    out = np.sqrt(out ** 2 + penal ** 2)
    out[~np.isfinite(s)] = np.nan
    return out.astype('float32')


def seasonal_bias(layers, months, stable_mask,
                  winter_months=WINTER_MONTHS):
    """Насколько зимние даты выше летних на устойчивой земле.

    Это прямое измерение снежного смещения. Морфологический фильтр его
    не видит: снег — гладкое протяжённое плато, а не объект. Если стек
    (и, соответственно, мозаика) преимущественно зимний, смещение сидит
    в опоре целиком и безальтернативно — тогда об этом надо писать в
    отчёте, а не молча вычитать.

    Возвращает dict с медианой разности и составом стека по сезонам.
    """
    w = [l for l, m in zip(layers, months) if int(m) in winter_months]
    s = [l for l, m in zip(layers, months) if int(m) not in winter_months]
    out = {'n_winter': len(w), 'n_summer': len(s),
           'winter_minus_summer_m': None, 'n_px': 0}
    if not w or not s:
        out['note'] = ('в стеке нет обоих сезонов — снежное смещение '
                       'измерить нечем')
        return out
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        wm = np.nanmedian(np.stack(w, axis=0), axis=0)
        sm = np.nanmedian(np.stack(s, axis=0), axis=0)
    d = (wm - sm)[stable_mask & np.isfinite(wm) & np.isfinite(sm)]
    if d.size:
        out['winter_minus_summer_m'] = round(float(np.median(d)), 3)
        out['mad_m'] = round(_mad(d), 3)
        out['n_px'] = int(d.size)
    return out


def pick_latest_valid(layers, dates, where=None, min_date=None):
    """По-пиксельный выбор эпохи: самый поздний слой, где есть данные.

    Нужно там, где мозаика старше местности. На намыве Морского фасада
    мозаика v4.1 (~2019) показывает акваторию до отсыпки; сейчас такие
    пиксели получают высоту интерполяцией от соседей с сигмой 1.5 м.
    Если хотя бы один стрип снят после отсыпки, высоту там надо брать
    измерением, а не догадкой.

    where — маска, в которой применять (например, намыв). Вне неё
    возвращается NaN, чтобы драйвер сам решил, что подставлять.
    """
    order = np.argsort([str(d) for d in dates])[::-1]      # свежие первыми
    H, W = np.asarray(layers[0]).shape
    out = np.full((H, W), np.nan, dtype='float32')
    picked = np.full((H, W), -1, dtype='int16')
    todo = np.ones((H, W), dtype=bool) if where is None else np.asarray(where).copy()
    for rank, i in enumerate(order):
        if min_date is not None and str(dates[i]) < str(min_date):
            continue
        l = np.asarray(layers[i], dtype='float32')
        take = todo & np.isfinite(l)
        out[take] = l[take]
        picked[take] = i
        todo &= ~take
        if not todo.any():
            break
    return out, picked


def blend_with_mosaic(mosaic, stack_ground, n_obs, min_obs=MIN_OBS):
    """Стек там, где дат достаточно; мозаика — где нет.

    Мозаика остаётся страховкой покрытия, но перестаёт быть
    единственным источником. Возвращает (surface, source), где
    source: 1 — многовременная огибающая, 2 — мозаика.
    """
    use = np.isfinite(stack_ground) & (np.asarray(n_obs) >= min_obs)
    out = np.where(use, stack_ground, mosaic).astype('float32')
    src = np.where(use, 1, 2).astype('uint8')
    src[~np.isfinite(out)] = 0
    return out, src


# ============================================================
# Эпохи: 48 стрипов — это НЕ 48 независимых наблюдений
# ============================================================
def _to_ord(d):
    """'20220314' -> порядковый номер дня."""
    from datetime import date
    t = str(d)
    return date(int(t[0:4]), int(t[4:6]), int(t[6:8])).toordinal()


def group_epochs(dates, gap_days=EPOCH_GAP_DAYS):
    """Сгруппировать даты в съёмочные сессии.

    В манифесте В.О. 48 стрипов, но, например, 2022-03-09/10/12/14/15 —
    это двенадцать стрипов одной сессии. У них общее состояние снега,
    общая освещённость, общий сезон. Считать их двенадцатью независимыми
    наблюдениями нельзя: нижний перцентиль по 48 «датам» окажется
    перцентилем по одной мартовской съёмке, а `n_obs` будет врать о
    разнообразии втрое.

    Возвращает список списков индексов, отсортированных по дате.
    """
    idx = sorted(range(len(dates)), key=lambda i: str(dates[i]))
    groups, cur = [], [idx[0]]
    for a, b in zip(idx, idx[1:]):
        if _to_ord(dates[b]) - _to_ord(dates[a]) <= gap_days:
            cur.append(b)
        else:
            groups.append(cur); cur = [b]
    groups.append(cur)
    return groups


def epoch_medians(layers, groups):
    """Медиана внутри сессии -> один слой на эпоху.

    Внутри сессии усреднять правильно: сцена одна и та же, различия —
    шум и геометрия съёмки. Между сессиями усреднять НЕЛЬЗЯ, там разница
    содержательная (снег, машины, стройка).
    """
    out, sizes = [], []
    for g in groups:
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            out.append(np.nanmedian(np.stack([np.asarray(layers[i], dtype='float64')
                                              for i in g], axis=0), axis=0))
        sizes.append(len(g))
    return out, sizes


def noise_from_session(layers, group, mask=None):
    """Шум стерео, измеренный внутри одной съёмочной сессии.

    Лучший из доступных источников: сцена та же, снег тот же, машины те
    же, поэтому расхождение между стрипами одной сессии — это чистый шум
    (плюс геометрия). Сессия 2022-03 из двенадцати стрипов даёт такую
    оценку почти даром. Она нужна как скаляр для поправки нижнего
    перцентиля.
    """
    if len(group) < 2:
        return None
    cube = np.stack([np.asarray(layers[i], dtype='float64') for i in group], axis=0)
    # Оценка по ПАРНЫМ разностям, а не по отклонениям от медианы.
    # При малом числе стрипов MAD от выборочной медианы систематически
    # занижен (для трёх наблюдений — почти вдвое), и шум вышел бы меньше
    # реального, а поправка перцентиля — недостаточной. Для гауссова
    # шума median|X - Y| = 0.9539*sigma, и эта оценка от объёма выборки
    # не зависит.
    vals = []
    k = cube.shape[0]
    for a in range(k):
        for b in range(a + 1, k):
            d = cube[a] - cube[b]
            if mask is not None:
                d = d[mask]
            d = d[np.isfinite(d)]
            if d.size:
                vals.append(float(np.median(np.abs(d))))
    if not vals:
        return None
    # d = X - Y ~ N(0, 2*sigma^2)  =>  median|d| = 0.6745 * sigma * sqrt(2)
    #                                             = 0.9539 * sigma
    return float(np.median(vals) / 0.9539)


def stack_by_epoch(layers, dates, months, pctl=GROUND_PCTL, min_obs=MIN_OBS,
                   noise_sigma=None, prefer_summer=True,
                   min_summer_epochs=MIN_SUMMER_EPOCHS,
                   winter_months=WINTER_MONTHS, gap_days=EPOCH_GAP_DAYS):
    """Полная схема: сессии -> эпохи -> огибающая, с приоритетом лета.

    Почему приоритет лета. Снег поверхность только поднимает, поэтому
    нижняя огибающая его снимает — НО лишь там, где есть достаточно
    бесснежных наблюдений. В манифесте В.О. 71% стрипов зимние и 48%
    мартовские; значит найдутся пиксели, покрытые только зимой, и там
    огибающая честно вернёт уровень снега.

    Поэтому: где летних эпох хватает — огибающая строится по ним; где
    нет — по всем, и пиксель помечается (`season_src = 2`), чтобы к нему
    можно было применить измеренное сезонное смещение и расширить сигму,
    а не делать вид, что снега не было.

    Возвращает dict со всеми полями stack_temporal плюс:
      season_src — 1 лето, 2 смесь сезонов, 0 нет данных
      n_epochs_summer / n_epochs_all — сколько эпох участвовало
    """
    groups = group_epochs(dates, gap_days)
    ep_layers, sizes = epoch_medians(layers, groups)
    ep_months = [int(months[g[0]]) for g in groups]
    ep_dates = [str(dates[g[0]]) for g in groups]
    summer = [i for i, m in enumerate(ep_months) if m not in winter_months]

    all_st = stack_temporal(ep_layers, pctl=pctl, min_obs=min_obs,
                            noise_sigma=noise_sigma)
    if not prefer_summer or len(summer) < min_summer_epochs:
        all_st.update({'season_src': np.where(np.isfinite(all_st['ground']),
                                              2, 0).astype('uint8'),
                       'epochs': ep_dates, 'epoch_sizes': sizes,
                       'n_epochs_summer': len(summer),
                       'n_epochs_all': len(ep_layers)})
        return all_st

    sum_st = stack_temporal([ep_layers[i] for i in summer], pctl=pctl,
                            min_obs=min(min_obs, len(summer)),
                            noise_sigma=noise_sigma)
    use_summer = np.isfinite(sum_st['ground'])
    out = {}
    for k in ('ground', 'spread', 'rise', 'median', 'sigma_low'):
        out[k] = np.where(use_summer, sum_st[k], all_st[k]).astype('float32')
    out['n_obs'] = np.where(use_summer, sum_st['n_obs'],
                            all_st['n_obs']).astype('int16')
    out['season_src'] = np.where(use_summer, 1,
                                 np.where(np.isfinite(all_st['ground']), 2,
                                          0)).astype('uint8')
    out.update({'epochs': ep_dates, 'epoch_sizes': sizes,
                'n_epochs_summer': len(summer), 'n_epochs_all': len(ep_layers)})
    return out
