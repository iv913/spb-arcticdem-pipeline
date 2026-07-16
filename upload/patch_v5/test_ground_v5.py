#!/usr/bin/env python3
"""
test_ground_v5.py — синтетика перевёрнутой иерархии (без rasterio).

Сцена — квартал Васильевского в миниатюре: плоский рельеф с уклоном
0.08%, корпуса домов 22 м, две УЗКИЕ улицы шириной 10 м, у которых в
DSM нет отражений от дна (стереотень заливает каньон на +1.8 м), и
открытые участки — двор, площадь, парк — с деревьями и машинами.

Проверяем ровно то, ради чего иерархия переворачивалась: опора,
собранная по открытой земле, восстанавливает уровень улицы, которую
сама улица о себе сообщить не может.
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import lib_ground as libg                                      # noqa: E402

PX = 2.0
H, W = 200, 300
yy, xx = np.mgrid[0:H, 0:W]

# --- истинный рельеф: 3.00 м + уклон 0.08% вдоль x ---
truth = 3.00 + 0.0008 * (xx * PX)

# --- полосы застройки и улиц по y ---
band = np.zeros((H, W), dtype='U12')
band[:] = 'open'
for y0, y1 in ((30, 45), (50, 65), (100, 115), (120, 135)):
    band[y0:y1] = 'building'
for y0, y1 in ((45, 50), (115, 120)):
    band[y0:y1] = 'street'          # 5 px = 10 м, каньон H/W ~ 2.2

water = np.zeros((H, W), bool)
water[:, 290:] = True               # акватория у восточного края
building = (band == 'building') & ~water
street = (band == 'street') & ~water

# --- DSM ---
dsm = truth.copy()
dsm[building] = truth[building] + 22.0
dsm[street] = truth[street] + 1.80          # стереотень: дна не видно
dsm[water] = 0.02
rng = np.random.default_rng(7)
canopy = np.zeros((H, W), bool)
for _ in range(40):                          # кроны 10-14 м в поперечнике
    cy, cx = rng.integers(0, H), rng.integers(0, 285)
    r = rng.integers(3, 7)
    m = ((yy - cy) ** 2 + (xx - cx) ** 2 <= r * r) & ~building & ~street
    canopy |= m
dsm[canopy] = truth[canopy] + 5.0
cars = np.zeros((H, W), bool)
for _ in range(60):                          # машины 4-6 м
    cy, cx = rng.integers(0, H), rng.integers(0, 285)
    m = (np.abs(yy - cy) <= 1) & (np.abs(xx - cx) <= 2) & ~building & ~water
    cars |= m
cars &= ~canopy
dsm[cars] = truth[cars] + 1.50
dsm += rng.normal(0, 0.05, dsm.shape)        # шум стерео

PASS = []


def ok(name, cond, detail=''):
    PASS.append(bool(cond))
    print(f'  [{"OK " if cond else "FAIL"}] {name}' + (f' — {detail}' if detail else ''))


print('=' * 72)
print('1. Морфологический фильтр земли: кроны и машины вон, земля на месте')
cand = ~building & ~water & np.isfinite(dsm)
ground, _ = libg.pmf_ground(dsm, cand, pixel_m=PX)
true_ground = cand & ~canopy & ~cars & ~street
print(f'  земля сохранена : {100 * (ground & true_ground).sum() / true_ground.sum():.1f}%')
print(f'  кроны отсеяны   : {100 * (~ground & canopy & cand).sum() / (canopy & cand).sum():.1f}%')
print(f'  машины отсеяны  : {100 * (~ground & cars & cand).sum() / (cars & cand).sum():.1f}%')
ok('земля сохранена >90%', (ground & true_ground).sum() / true_ground.sum() > 0.90)
ok('кроны отсеяны >90%', (~ground & canopy & cand).sum() / (canopy & cand).sum() > 0.90)
ok('машины отсеяны >70%', (~ground & cars & cand).sum() / (cars & cand).sum() > 0.70)

print('=' * 72)
print('2. Опора: только открытые пиксели, каньон в опору не попадает')
anchors, ground2, dist_b = libg.anchor_field(dsm, building, water,
                                             pixel_m=PX, open_min_m=6.0)
print(f'  опорных пикселей: {int(anchors.sum()):,} '
      f'({100 * anchors.sum() / cand.sum():.0f}% кандидатов)')
ok('улица-каньон не в опоре', not anchors[street].any(),
   'каждый её пиксель ближе 6 м к фасаду')
ok('опора не берёт кроны', not (anchors & canopy).any())
err_a = dsm[anchors] - truth[anchors]
print(f'  ошибка опоры: медиана {np.median(err_a):+.3f} м, '
      f'MAD {np.median(np.abs(err_a - np.median(err_a))) * 1.4826:.3f} м')
ok('опора несмещена (|медиана| < 5 см)', abs(np.median(err_a)) < 0.05)

print('=' * 72)
print('3. Опорное поле накрывает каньон')
prior = libg.build_prior(np.where(anchors, dsm, np.nan), domain=~water)
err_p = prior[street] - truth[street]   # улица целиком на суше
print(f'  prior на улице: медиана {np.nanmedian(err_p):+.3f} м, '
      f'RMSE {np.sqrt(np.nanmean(err_p ** 2)):.3f} м')
ok('prior восстановил уровень улицы (RMSE < 0.25 м)',
   np.sqrt(np.nanmean(err_p ** 2)) < 0.25)
ok('вода осталась барьером (prior там NaN)', not np.isfinite(prior[water]).any())

print('=' * 72)
print('4. Вердикт улицы: каньон опознан и замощён')
h_street = dsm[street]                     # то, что улица сообщает о себе
h_anchor = prior[street]
verdict, delta, n = libg.street_verdict(h_street, h_anchor, tol_m=0.50)
print(f'  вердикт={verdict}, Δ={delta:+.2f} м по {n} px')
ok('улица признана несогласованной', verdict == 'bridged')
ok('Δ близка к заложенной тени +1.80 м', abs(delta - 1.80) < 0.25)

err_before = np.nanmedian(h_street - truth[street])
err_after = np.nanmedian(h_anchor - truth[street])
print(f'  ошибка улицы ДО моста : {err_before:+.2f} м')
print(f'  ошибка улицы ПОСЛЕ    : {err_after:+.2f} м')
ok('мост уменьшил ошибку более чем в 10 раз',
   abs(err_after) * 10 < abs(err_before))

print('=' * 72)
print('5. Широкая улица не должна мостится зря (контроль ложного срабатывания)')
wide = np.zeros((H, W), bool)
wide[65:88] = True                          # 46 м, есть отражения от дна
h_wide = truth[wide] + rng.normal(0, 0.08, wide.sum())
v2, d2, n2 = libg.street_verdict(h_wide, prior[wide], tol_m=0.50)
print(f'  вердикт={v2}, Δ={d2:+.2f} м')
ok('широкая улица остаётся measured', v2 == 'measured')

print('=' * 72)
print('6. Водоёмы: нужна не только площадь, но и примыкание к воде')
low = np.isfinite(dsm) & ~building & (dsm <= 0.3)
bodies, edges = libg.split_low_pixels(low, adjacent_to=water, pixel_m=PX)
quay = np.zeros((H, W), bool)
quay[150:190, 250:285] = True               # большая низкая площадка вдали
low2 = low | quay
bodies2, edges2 = libg.split_low_pixels(low2, adjacent_to=water, pixel_m=PX)
ok('акватория опознана', bodies[water].any())
ok('низкая площадка вдали от воды НЕ стала водоёмом',
   not bodies2[quay].any(),
   'ровно тот случай, когда пять паспортных точек садились на «воду»')

print('=' * 72)
print('7. Честное снятие паспортной точки')
arr = np.full((10, 10), np.nan)
arr[5, 5] = 10.0        # далеко, но «удобно» по высоте
arr[4, 4] = 3.0         # ближе геометрически
rc = libg.nearest_finite(arr, 4, 3)
ok('берётся ближайший геометрически, а не по высоте', rc == (4, 4),
   f'получено {rc}')

print('=' * 72)
print(f'ИТОГО: {sum(PASS)}/{len(PASS)} проверок пройдено')
sys.exit(0 if all(PASS) else 1)
