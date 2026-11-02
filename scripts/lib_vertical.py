#!/usr/bin/env python3
"""
lib_vertical.py — ЕДИНЫЙ источник вертикального датума и бюджета ошибки.

Зачем модуль появился (дефект ревью №4 и №5):

  №4. Датум выбирался в трёх местах по-разному. 04_vo_pipeline брал
      городской `shift_m`, 04_ground_surface — `regional.vo.shift` при
      significant. Разница ~0.55 м. В итоге prior строился от скелета в
      одном датуме, а `dsm` в guided acceptance сравнивался в другом:
      правило |DSM − prior| <= 0.75 м систематически съедалось сдвигом,
      и газоны уходили в «кроны». Теперь датум выбирает ОДНА функция,
      и все модули обязаны звать её.

  №5. Бюджет неопределённости не соответствовал способу получения
      высоты. Пиксель класса `open_accepted` — это сырой DSM, принятый
      потому, что он в пределах ±TOL_ACCEPT (0.75 м) от prior; ему
      присваивалась σ_класса 0.15 м, то есть он объявлялся точнее, чем
      позволяет сам критерий его приёмки. Теперь σ берётся по КЛАССУ
      ПРОИСХОЖДЕНИЯ (банд detail), а не по грубому source 1/2/3.

  Плюс: FORM_SIGMA = 0.107 м — это точность формы, измеренная на
  Большом проспекте по фьюжну двух стрипов. К газонам и к улицам без
  принятых стрипов она отношения не имеет, поэтому применяется только
  к пикселям скелета (detail 11-13), а не ко всему растру.

B2 (handoff 2026-11): вариант по умолчанию — поточечный EGM2008 + сдвиг D
(одно преобразование на весь проект); константы 15.673/15.9/16.04/16.472
в новом коде запрещены, city_v3 — только воспроизведение старых результатов.
"""
import json
import os
from pathlib import Path

import numpy as np

# --- B2 (handoff 2026-11): одно преобразование на весь проект ---
#
#   H_bs77 = h_ell − N_EGM2008(lon, lat) − D
#
# Вариант ПО УМОЛЧАНИЮ — поточечный геоид с сетки EGM2008 (2.5′) плюс
# один сдвиг D из datum_D.json (B3 закрыт 6.71: D = +0.004 ± 0.046,
# бюджет 0.08 — handoff §3-B3: литература + ICESat-2 у Кронштадта).
# Вариант geoid_source='city_v3' оставлен ТОЛЬКО для воспроизведения
# старых результатов (детерминированный пересчёт прежних продуктов).
#
# NB: в grid-режиме shift_m = None — скалярного сдвига НЕ СУЩЕСТВУЕТ
# (N по городу 15.764…16.004, разброс 0.24 м). Старый код, читающий
# out['shift_m'] как число, должен упасть громко, а не молча считать
# на одной константе. Пересчёт: ellipsoid_to_bs77() или geoid.to_bs77().

try:  # соседний модуль из handoff (канон: scripts/geoid_egm2008.py)
    from geoid_egm2008 import Geoid
except Exception:  # pragma: no cover
    Geoid = None

_REPO = Path(__file__).resolve().parents[1]
_EGM2008_GRID_CANDIDATES = [
    os.environ.get('SPB_EGM2008_GRID', ''),
    '/tmp/my-project/data/us_nga_egm08_25.tif',       # PolarFS data/
    str(_REPO / 'data' / 'us_nga_egm08_25.tif'),
    'data/us_nga_egm08_25.tif',                       # cdn.proj.org, 80 MB
]
_DATUM_D_CANDIDATES = [
    os.environ.get('SPB_DATUM_D_JSON', ''),
    str(_REPO / 'results' / 'datum_D.json'),            # канон (6.71, B3)
    str(_REPO / 'results' / 'datum_6.61' / 'datum_D.json'),  # история 6.65
]
_GEOID_CACHE = {}


def _find_first(candidates, what):
    for c in candidates:
        if c and Path(c).exists():
            return Path(c)
    raise FileNotFoundError(
        f'{what} не найден; задайте путь через env '
        f'({[c for c in candidates if c][0]!r} и далее по списку)')


def _get_geoid():
    if Geoid is None:
        raise ImportError('scripts/geoid_egm2008.py недоступен (rasterio?)')
    if 'grid' not in _GEOID_CACHE:
        _GEOID_CACHE['grid'] = Geoid(_find_first(_EGM2008_GRID_CANDIDATES,
                                                 'сетка EGM2008'))
    return _GEOID_CACHE['grid']


def _load_datum_D(logger):
    """D из приёмки B3 (канон results/datum_D.json, 6.71: D = +0.004 ±
    0.046, бюджет 0.08 — литература + ICESat-2 Кронштадта); при отсутствии
    файла — D=0 с бюджетом 0.10 и предупреждением."""
    for c in _DATUM_D_CANDIDATES:
        if c and Path(c).exists():
            d = json.loads(Path(c).read_text(encoding='utf-8'))
            return float(d['D_m']), float(d.get('SE_m', 0.06)), \
                float(d.get('sigma_budget_m', 0.10)), str(c)
    logger('⚠ datum_D.json не найден — D=0.00 (SE 0.06, бюджет 0.10), '
           'временное значение B3 (Ellmann et al. 2009)')
    return 0.0, 0.06, 0.10, 'default_b3_temporary'


# --- σ по классу происхождения пикселя (банд detail модуля 04) ---
# 11/12/13 — скелет (дорожная поверхность модуля 02 / быстрого эстиматора)
# 21       — открытая земля, принятая из DSM: |DSM−prior| <= TOL_ACCEPT,
#            равномерное распределение в ±0.75 → σ = 0.75/sqrt(3) ≈ 0.43
# 22/23    — замещено prior (ближняя / дальняя диффузия)
# 31       — кромка ниже водного гейта, заменена prior
# 32       — водоём: не суша, в статистику не идёт (σ не определена)
# 14 — улица, замощенная опорным полем: её уровень взят не из DSM
#      каньона, а из открытой земли рядом; точность — точность опоры
# 42 — намыв: DEM старше местности, высота взята от соседей
DETAIL_SIGMA = {11: 0.15, 12: 0.30, 13: 0.80, 14: 0.60,
                21: 0.45, 22: 0.60, 23: 0.80,
                31: 0.80, 42: 1.50, 32: np.nan, 41: np.nan}
# грубый fallback, если detail отсутствует (старые продукты с source 1/2/3)
SOURCE_SIGMA = {1: 0.15, 2: 0.30, 3: 0.80}
FORM_SIGMA_DEFAULT = 0.107
SKELETON_DETAILS = (11, 12, 13)  # 14 опорное, FORM_SIGMA к нему неприменима


def load_vertical(out_dir, region='vo', logger=print, geoid_source='EGM2008'):
    """Единственная точка выбора вертикального датума (§1 handoff).

    geoid_source='EGM2008' (по умолчанию, единственный для нового кода):
      H_bs77 = h_ell − N_EGM2008(lon, lat) − D; сетка 2.5′ + D из datum_D.json
      (B3: 0.00 ± 0.06, бюджет 0.10). Возвращает mode='grid':
        geoid   — объект Geoid (.n(lon, lat), .to_bs77(lon, lat, h_ell, D))
        D, D_SE — сдвиг датума БС-77 и его SE, м
        sigma_m — бюджет ошибки преобразования (0.10 м)
        shift_m — None: скаляра нет, N по городу 15.764…16.004

    geoid_source='city_v3' — ТОЛЬКО для воспроизведения старых результатов:
      прежнее поведение (константа из city_calibration.json v3: город
      15.6727, региональные при significant). В новом коде запрещено (§1).

    region: 'vo' | 'mainland' | 'city' — используется только в city_v3.
    """
    if geoid_source == 'EGM2008':
        g = _get_geoid()
        D, se, budget, dsrc = _load_datum_D(logger)
        out = {'mode': 'grid',
               'source': f'geoid_egm2008_grid_D{D:+.2f}',
               'grid': str(_find_first(_EGM2008_GRID_CANDIDATES, 'сетка')),
               'datum_source': dsrc,
               'geoid': g, 'D': D, 'D_SE': se,
               'sigma_m': budget, 'sigma_shift_m': budget,
               'shift_m': None, 'version': None, 'significant': None,
               'recipe': 'H_bs77 = h_ell - N_EGM2008(lon, lat) - D'}
        logger(f'Датум: EGM2008 сетка + D={D:+.2f}±{se:.2f} м '
               f'(бюджет {budget:.2f}); N по городу 15.764…16.004')
        return out
    if geoid_source == 'city_v3':
        logger('⚠ city_v3 (константы 15.673/региональные) — ТОЛЬКО '
               'воспроизведение старых результатов; в новом коде запрещено '
               "(handoff §1). Новые продукты: geoid_source='EGM2008'.")
        return _load_city_v3(out_dir, region, logger)
    raise ValueError(
        f"geoid_source={geoid_source!r} не поддерживается: "
        "'EGM2008' (по умолчанию) или 'city_v3' (воспроизведение)")


def _load_city_v3(out_dir, region='vo', logger=print):
    """Прежнее поведение (до B2), изъятое из load_vertical как есть.

    Отличие от дореформенного кода: молчаливый дефолт 15.9 удалён — без
    city_calibration.json city_v3 падает, а не считает на константе.
    """
    p = Path(out_dir) / 'city_calibration.json'
    out = {'mode': 'scalar', 'shift_m': None, 'sigma_m': 0.80,
           'source': 'city_v3', 'version': None, 'significant': None,
           'geoid': None, 'D': None}
    if not p.exists():
        raise FileNotFoundError(
            f'{p} не найден. Воспроизведение старых результатов требует '
            'city_calibration.json v3; константа-дефолт 15.9 удалена (§1).')
    cc = json.loads(p.read_text())
    out['version'] = cc.get('version')
    if cc.get('version') != 'v3':
        logger(f'⚠ city_calibration версии {cc.get("version", "v1")} — '
               'привязка дискредитирована (сырой пиксель). '
               'Пересчитайте city_calibration.py.')
        out['shift_m'] = float(cc.get('shift_m', 0.0))
        out['source'] = f'city_calibration_{cc.get("version", "v1")}'
        return out
    shift = float(cc['shift_m'])
    sigma = float(cc.get('between_street_mad_m', 0.80))
    src = 'city_v3'
    reg = cc.get('regional') or {}
    out['significant'] = reg.get('significant')
    if region in ('vo', 'mainland') and reg.get('significant'):
        r = reg.get(region) or {}
        if r.get('shift') is not None:
            shift = float(r['shift'])
            if r.get('mad') is not None:
                sigma = float(r['mad'])
            src = f'regional_{region}_v3'
            logger(f'Датум: региональный ({region}) — Δ к городскому '
                   f'{shift - float(cc["shift_m"]):+.3f} м, σ {sigma:.3f} м')
    out.update({'shift_m': shift, 'sigma_m': sigma, 'source': src})
    logger(f'Датум: shift={shift:.3f} м, σ_привязки={sigma:.3f} м ({src})')
    return out


def ellipsoid_to_bs77(h_ell, lon, lat, vert):
    """h_ell → H_BS77 по одному преобразованию из load_vertical.

    grid-режим (EGM2008): H = h_ell − N(lon, lat) − D — поточечно.
    city_v3 (воспроизведение): H = h_ell − shift_m (константа).
    """
    h = np.asarray(h_ell, float)
    if vert.get('mode') == 'grid':
        return vert['geoid'].to_bs77(np.asarray(lon, float),
                                     np.asarray(lat, float), h, vert['D'])
    if vert.get('shift_m') is None:
        raise ValueError('vert без mode=grid и без shift_m — перезовите '
                         'load_vertical; молча подставлять константу нельзя')
    return h - float(vert['shift_m'])


def pixel_sigma(detail=None, source=None, shift_sigma=0.80,
                form_sigma=FORM_SIGMA_DEFAULT, shape=None):
    """По-пиксельный бюджет u = sqrt(form² + shift² + class²).

    form входит только для пикселей скелета — это точность формы
    продольного профиля, измеренная по стрипам; к открытой земле она
    неприменима. Водоёмы (32) и отброшенное (41) получают NaN.
    NB (B2): в grid-режиме передавайте shift_sigma=vert['sigma_m'] (0.10).
    """
    if detail is not None:
        d = np.asarray(detail)
        u = np.full(d.shape, np.nan, dtype='float32')
        for code, s in DETAIL_SIGMA.items():
            if not np.isfinite(s):
                continue
            m = d == code
            if not m.any():
                continue
            f = form_sigma if code in SKELETON_DETAILS else 0.0
            u[m] = np.sqrt(f ** 2 + shift_sigma ** 2 + s ** 2)
        return u
    s_arr = np.asarray(source)
    u = np.full(s_arr.shape, np.nan, dtype='float32')
    for code, s in SOURCE_SIGMA.items():
        m = s_arr == code
        if m.any():
            u[m] = np.sqrt(form_sigma ** 2 + shift_sigma ** 2 + s ** 2)
    return u


def flood_masks(h, z_spill, u, level):
    """Три согласованные маски затопления при уровне `level`.

    Дефект ревью №6: раньше оптимистичный сценарий применял u и к
    глубине, и к порогу перелива, а пессимистичный — только к глубине
    (`wet & (H-h) > u` при `Z < H`). Вилка была смещена вверх.
    Теперь неопределённость входит В ОБА условия симметрично:

      pessimistic: перелив уверенно пройден (Z + u < H) И глубина > +u
      best:        Z < H и h < H
      optimistic:  перелив мог пройти (Z − u < H) И глубина > −u
    """
    fin = np.isfinite(h) & np.isfinite(z_spill)
    uu = np.where(np.isfinite(u), u, 0.0)
    depth = level - h
    best = fin & (z_spill < level) & (depth > 0)
    pess = fin & ((z_spill + uu) < level) & (depth > uu)
    opt = fin & ((z_spill - uu) < level) & (depth > -uu)
    return pess, best, opt


def stat_denominator(stat_band=None, detail=None, valid=None):
    """Пиксели, которые ИМЕЮТ ПРАВО попадать в знаменатель процентов.

    Дефект ревью №7: `total = np.isfinite(surface).sum()` включал воду
    и подозрительные кромки, то есть проценты «под водой» считались в
    том числе от площади Невы. Приоритет: явный банд stat -> detail ->
    просто валидные пиксели.
    """
    if stat_band is not None:
        return np.asarray(stat_band) == 1
    if detail is not None:
        d = np.asarray(detail)
        return np.isin(d, [11, 12, 13, 14, 21, 22, 23, 31, 42])
    return np.asarray(valid)
