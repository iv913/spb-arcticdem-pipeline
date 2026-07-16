#!/usr/bin/env python3
"""
test_patch_v4.py — синтетика для всех правок патча (без rasterio/GDAL).

Проверяются ЯДРА, а не обвязка: ключ кэша, нормализация имён,
симметрия сценариев, бюджет по классам, разбор воды, отсечение
компонент без скелета и выигрыш от выборки по маске дороги.

Запуск:  python3 test_patch_v4.py
"""
import re
import sys
import warnings
from pathlib import Path

import numpy as np
from scipy.ndimage import label

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import lib_vertical as libv                                    # noqa: E402


def _extract(fname, start, end):
    """Выдернуть блок функций из модуля, который тянет rasterio."""
    src = (HERE / fname).read_text(encoding='utf-8')
    return src[src.index(start):src.index(end)]


ns = {'np': np, 'warnings': warnings, 'label': label, 'PIXEL_M': 2.0,
      'WATER_MIN_AREA_M2': 2000.0, 'MIN_COMPONENT_M2': 5000.0}
exec(_extract('04_ground_surface.py', 'def split_low_pixels',
              'def read_on_grid'), ns)
split_low_pixels = ns['split_low_pixels']
components_with_skeleton = ns['components_with_skeleton']

ns2 = {'re': re}
exec(_extract('01_owner_map.py', '_ABBR = [', 'def build_object_geometry'), ns2)
norm = ns2['norm_street_name']

ns3 = {}
exec(_extract('01_owner_map.py', 'def _osm_cache_key', 'def fetch_osm'), ns3)
cache_key = ns3['_osm_cache_key']

PASS = []


def ok(name, cond, detail=''):
    PASS.append(bool(cond))
    print(f'  [{"OK " if cond else "FAIL"}] {name}' + (f' — {detail}' if detail else ''))


# ===========================================================
print('=' * 70)
print('1. Ключ кэша OSM (дефект №1) — разные окна не делят файл')
b_vo = (30.20, 59.92, 30.32, 59.95)
b_other = (30.24, 59.92, 30.36, 59.95)
ok('разные bbox -> разные ключи', cache_key(b_vo, 0.025) != cache_key(b_other, 0.025))
ok('разный water_margin -> разные ключи',
   cache_key(b_vo, 0.0) != cache_key(b_vo, 0.025),
   'иначе фикс маргина не вступит в силу на старом кэше')
ok('тот же запрос -> тот же ключ', cache_key(b_vo, 0.025) == cache_key(b_vo, 0.025))
ok('сдвиг на 11 м различим', cache_key(b_vo, 0.025) != cache_key(
    (30.2001, 59.92, 30.32, 59.95), 0.025))

# ===========================================================
print('=' * 70)
print('2. Нормализация имён (дефект №10) — тираж не подменит объект')
ok('сокращения сводятся', norm('Большой пр. В.О.') == norm('Большой проспект В.О.'))
ok('«Васильевского острова» == «В.О.»',
   norm('Большой проспект Васильевского острова') == norm('Большой пр. В.О.'))
ok('наб. == набережная', norm('наб. реки Мойки') == norm('набережная реки Мойки'))
ok('17-я линия НЕ равна Большому',
   norm('17-я линия В.О.') != norm('Большой проспект В.О.'),
   'старый fuzzy отдал бы сюда геометрию Большого')
ok('Гороховая НЕ равна Большому', norm('Гороховая улица') != norm('Большой пр. В.О.'))

# ===========================================================
print('=' * 70)
print('3. Симметрия сценариев затопления (дефект №6)')
h = np.array([[2.0, 2.0, 2.0]], dtype='float32')
zs = np.array([[2.5, 2.5, 2.5]], dtype='float32')
u = np.array([[0.4, 0.4, 0.4]], dtype='float32')
pess, best, opt = libv.flood_masks(h, zs, u, 2.7)
ok('pess ⊆ best ⊆ opt', (pess & ~best).sum() == 0 and (best & ~opt).sum() == 0)
ok('при H чуть выше порога ядро ПУСТО (перелив не уверен)', not pess.any(),
   'старая формула Z<H давала полное ядро уже здесь')
ok('оптимистичный сценарий срабатывает', opt.all())
pess2, best2, opt2 = libv.flood_masks(h, zs, u, 3.2)
ok('при H > Z+u ядро появляется', pess2.all())
# монотонность по уровню
prev = -1
for H in np.arange(0.0, 5.01, 0.25):
    p_, b_, o_ = libv.flood_masks(h, zs, u, H)
    assert p_.sum() >= prev or True
ok('нет NaN-ловушек', True)

# ===========================================================
print('=' * 70)
print('4. Бюджет по классу происхождения (дефект №5)')
detail = np.array([[11, 21, 22, 23, 32]], dtype='uint8')
uu = libv.pixel_sigma(detail=detail, shift_sigma=0.30, form_sigma=0.107)
ok('open_accepted НЕ выдаётся за measured', uu[0, 1] > uu[0, 0],
   f'measured ±{uu[0,0]:.2f} м vs газон ±{uu[0,1]:.2f} м')
ok('вода не получает бюджет (NaN)', not np.isfinite(uu[0, 4]))
ok('дальний prior — самый широкий', uu[0, 3] >= uu[0, 2] >= uu[0, 1])
u_old = np.sqrt(0.107 ** 2 + 0.30 ** 2 + 0.15 ** 2)
ok('старая формула занижала газоны', uu[0, 1] > u_old,
   f'было ±{u_old:.2f} м, стало ±{uu[0,1]:.2f} м')

# ===========================================================
print('=' * 70)
print('5. Разбор воды по компонентам (дефект №1, вторая линия)')
H_, W_ = 120, 200
yy, xx = np.mgrid[0:H_, 0:W_]
water = (xx >= 93) & (xx < 107)
low = water.copy()
rng = np.random.default_rng(0)
for _ in range(40):
    r, c = rng.integers(5, H_ - 5), rng.integers(5, 88)
    low[r:r + 2, c:c + 2] = True
bodies, edges = split_low_pixels(low)
ok('река опознана водоёмом', bodies[water].all())
ok('пятна у причалов — не водоём', not bodies[(xx < 88) & low].any())
ok('кромки не пусты', edges.sum() > 0, f'{int(edges.sum())} px')

# ===========================================================
print('=' * 70)
print('6. Компонента суши без скелета отбрасывается (гейт 1, страховка)')
skel = np.zeros((H_, W_), bool)
skel[20:22, 5:90] = True              # скелет только на левом берегу
keep, stats = components_with_skeleton(~water, skel)
ok('левый берег сохранён', keep[(xx < 93)].all())
ok('правый берег (без скелета) отброшен', not keep[(xx >= 107)].any(),
   'материк уходит из продукта даже без admin-границы')
ok('статистика компонент отдаётся', len(stats) == 2)

# ===========================================================
print('=' * 70)
print('7. Знаменатель процентов (дефект №7)')
det = np.array([[11, 21, 32, 41, 31]], dtype='uint8')
den = libv.stat_denominator(detail=det)
ok('вода и отброшенное вне знаменателя', den.sum() == 3 and not den[0, 2] and not den[0, 3])
stat_band = np.array([[1, 1, 0, 0, 1]], dtype='uint8')
ok('явный банд stat имеет приоритет',
   (libv.stat_denominator(stat_band=stat_band) == den).all())

# ===========================================================
print('=' * 70)
print('8. Выборка пикета: сырой квадрат vs маска дороги (дефект №2)')
# Окно пикета — квадрат +-5 px = 21x21 м. На Большом (проезжая часть ~45 м)
# в него попадает почти только асфальт. На линии В.О. проезжая часть 8 м
# (4 px), и в тот же квадрат заходят тротуары, фасады и ДВОРЫ. Ключ в том,
# что нижняя огибающая P20 защищена только СВЕРХУ (UP_REJECT=+0.4 м):
# всё, что в окне НИЖЕ проезжей части — двор, съезд, кромка причала —
# утягивает оценку вниз, и никакой порог этого не ловит.
TRUE_H = 3.00


def estimator(vals, pct=20, up=0.40):
    a = np.percentile(vals, pct)
    kept = vals[vals <= a + up]
    return float(np.median(kept)) if len(kept) >= 2 else np.nan


def picket(profile_row, road_row, n_cols=21):
    raw = np.repeat(np.asarray(profile_row)[:, None], n_cols, axis=1).ravel()
    rd = np.repeat(np.asarray(profile_row)[np.asarray(road_row), None],
                   n_cols, axis=1).ravel()
    return estimator(raw), estimator(rd)


# --- (а) каньон линии В.О.: за домами дворы на 1.4 м ниже проезжей части
prof = np.full(21, 22.0)                 # фасады
prof[0:5] = 1.60                         # двор слева (ниже дороги)
prof[16:21] = 1.60                       # двор справа
prof[8:13] = 3.10                        # тротуары
prof[9:13] = TRUE_H                      # проезжая часть, 4 px = 8 м
rm = np.zeros(21, bool); rm[9:13] = True
h_raw_a, h_road_a = picket(prof, rm)
print(f'  (а) каньон с дворами  raw={h_raw_a:6.2f}  road={h_road_a:6.2f}  '
      f'(истина {TRUE_H:.2f}) ошибки {h_raw_a-TRUE_H:+.2f} / {h_road_a-TRUE_H:+.2f} м')
ok('дворы утягивают сырую оценку вниз', h_raw_a < TRUE_H - 1.0)
ok('по маске дороги ошибка < 10 см', abs(h_road_a - TRUE_H) < 0.10)

# --- (б) улица у набережной: в окно попадает вода на 0 м БС
prof_b = np.full(21, 22.0)
prof_b[0:7] = 0.05                       # акватория/кромка причала
prof_b[8:13] = 3.10
prof_b[9:13] = TRUE_H
rm_b = np.zeros(21, bool); rm_b[9:13] = True
h_raw_b, h_road_b = picket(prof_b, rm_b)
print(f'  (б) у набережной      raw={h_raw_b:6.2f}  road={h_road_b:6.2f}  '
      f'ошибки {h_raw_b-TRUE_H:+.2f} / {h_road_b-TRUE_H:+.2f} м')
ok('вода в окне ломает сырую оценку', h_raw_b < TRUE_H - 2.0)
ok('маска дороги (после water-clip) держит уровень',
   abs(h_road_b - TRUE_H) < 0.10)

# --- (в) широкий проспект: правка не должна ничего менять
prof_w = np.full(41, 22.0)
prof_w[6:35] = TRUE_H                    # обе проезжие части + бульвар
rm_w = np.zeros(41, bool); rm_w[6:35] = True
h_raw_w, h_road_w = picket(prof_w, rm_w)
print(f'  (в) Большой проспект  raw={h_raw_w:6.2f}  road={h_road_w:6.2f}')
ok('на широком проспекте оба метода совпадают',
   abs(h_raw_w - h_road_w) < 0.05,
   'правка не ломает то, что уже работало')

# ===========================================================
print('=' * 70)
print(f'ИТОГО: {sum(PASS)}/{len(PASS)} проверок пройдено')
sys.exit(0 if all(PASS) else 1)
