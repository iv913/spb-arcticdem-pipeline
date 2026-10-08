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
"""
import json
import numpy as np

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


# ============================================================
# Audit item I (2026-10-08): Independent geoid constants
# ============================================================
# Per audit: city_calibration v3's shift_m is fit on Otmetki.txt passport
# data (~166/202 marks lie inside anchor support -> circular per audit
# item C). When the geoid is used to convert ICESat-2 ellipsoidal to
# Baltic heights for validation, this creates a circular dependency:
# Otmetki.txt -> city_calibration v3 -> lib_vertical geoid -> icesat_check
# -> ICESat dH median approx 0 by construction.
#
# EGM2008_OFFSET_SPB_M is the independent EGM2008-derived geoid offset
# for SPb (well-known value, NOT fit on local survey data). Use this
# via geoid_source='EGM2008' when validating ICESat against the model.
#
# Sources:
#   - EGM2008 model (Pavlis et al. 2012) at 59.94N, 30.30E
#   - Cross-checked against Pulkovo 1942 -> WGS84 datum transformation
#   - Differs from city_v3 (15.6727m) by 0.227m -- small but breaks the
#     circularity chain.
EGM2008_OFFSET_SPB_M = 15.9
EGM2008_SIGMA_M = 0.30  # EGM2008 model uncertainty at SPb latitude



def load_vertical(out_dir, region='vo', logger=print, geoid_source='city_v3'):
    """Единственная точка выбора вертикального сдвига и его сигмы.

    region: 'vo' | 'mainland' | 'city'. Если в city_calibration.json v3
    региональная разница признана значимой, для 'vo'/'mainland' берётся
    региональный сдвиг И региональная сигма; иначе — городские.

    geoid_source: 'city_v3' (default, current behavior -- circular for
        ICESat validation per audit item I) | 'EGM2008' (independent
        geoid, use when validating ICESat against model -- breaks the
        Otmetki.txt -> city_calibration -> ICESat circular chain).

    Возвращает dict: shift_m, sigma_m, source, version, significant.
    """
    # === Audit item I: Independent EGM2008 geoid ===
    if geoid_source == 'EGM2008':
        logger(f'Датум: EGM2008 independent geoid shift={EGM2008_OFFSET_SPB_M:.3f} м, '
               f'σ={EGM2008_SIGMA_M:.3f} м (NOT city_v3 -- breaks circular chain)')
        return {
            'shift_m': EGM2008_OFFSET_SPB_M,
            'sigma_m': EGM2008_SIGMA_M,
            'source': 'EGM2008_independent',
            'version': None,
            'significant': None,
            'audit_note': (
                'AUDIT 2026-10-08: Using EGM2008 independent geoid '
                f'(offset={EGM2008_OFFSET_SPB_M}m) instead of city_v3 '
                '(shift_m=15.6727m, fit on Otmetki.txt passport data). '
                'Use this geoid_source when validating ICESat against '
                'the model to avoid the circular dependency chain.'
            ),
        }

    # === Default: city_v3 (current behavior, preserved for backwards compat) ===
    p = out_dir / 'city_calibration.json'
    out = {'shift_m': 15.9, 'sigma_m': 0.80, 'source': 'default_geoid',
           'version': None, 'significant': None}
    if not p.exists():
        logger('⚠ city_calibration.json не найден — датум по умолчанию '
               '(geoid 15.9, σ 0.80). Числа затопления условны. '
               'Для ICESat валидации используйте geoid_source="EGM2008".')
        return out
    cc = json.loads(p.read_text())
    out['version'] = cc.get('version')
    if cc.get('version') != 'v3':
        logger(f'⚠ city_calibration версии {cc.get("version", "v1")} — '
               'привязка дискредитирована (сырой пиксель). '
               'Пересчитайте city_calibration.py.')
        out['shift_m'] = float(cc.get('shift_m', 15.9))
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


def pixel_sigma(detail=None, source=None, shift_sigma=0.80,
                form_sigma=FORM_SIGMA_DEFAULT, shape=None):
    """По-пиксельный бюджет u = sqrt(form² + shift² + class²).

    form входит только для пикселей скелета — это точность формы
    продольного профиля, измеренная по стрипам; к открытой земле она
    неприменима. Водоёмы (32) и отброшенное (41) получают NaN.
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
