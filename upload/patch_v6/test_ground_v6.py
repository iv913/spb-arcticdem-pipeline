#!/usr/bin/env python3
"""
test_ground_v6.py — динамический масштаб огибающей и односторонность.

Сцена продолжает v5, но добавляет две вещи, на которых v5 сломалась в
реальном прогоне (34% паспортных точек сели на пиксели выше 6 м БС при
паспортных отметках 1.0-4.1 м):

  * дворовый корпус 80x160 м, КОТОРОГО НЕТ в маске зданий OSM;
  * сплошная крона 40x80 м.

Оба крупнее максимального окна морфологического фильтра (62 м), поэтому
размыкание видит плато, а не объект, и пропускает их в опору.
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import lib_ground as libg                                      # noqa: E402

PX = 2.0
H, W = 320, 400
yy, xx = np.mgrid[0:H, 0:W]
truth = 3.00 + 0.0008 * (xx * PX)

band = np.full((H, W), 'open', dtype='U12')
for y0, y1 in ((30, 45), (50, 65), (100, 115), (120, 135)):
    band[y0:y1] = 'building'
for y0, y1 in ((45, 50), (115, 120)):
    band[y0:y1] = 'street'

water = np.zeros((H, W), bool); water[:, 390:] = True
building = (band == 'building') & ~water        # маска OSM
street = (band == 'street') & ~water

# --- то, чего в маске OSM НЕТ ---
missing_roof = np.zeros((H, W), bool)
missing_roof[160:250, 60:160] = True              # 180x200 м, +18 м
big_canopy = np.zeros((H, W), bool)
big_canopy[5:80, 250:350] = True                  # 150x200 м, +6 м

dsm = truth.copy()
dsm[building] = truth[building] + 22.0
dsm[street] = truth[street] + 1.80
dsm[missing_roof] = truth[missing_roof] + 18.0
dsm[big_canopy] = truth[big_canopy] + 6.0
dsm[water] = 0.02
rng = np.random.default_rng(11)
dsm += rng.normal(0, 0.05, dsm.shape)

PASS = []


def ok(name, cond, detail=''):
    PASS.append(bool(cond))
    print(f'  [{"OK " if cond else "FAIL"}] {name}' + (f' — {detail}' if detail else ''))


print('=' * 72)
print('1. Что пропускал v5 (только морфология + открытость)')
cand = ~building & ~water & np.isfinite(dsm)
ground, _ = libg.pmf_ground(dsm, cand, pixel_m=PX)
open0 = ground & (libg.openness_m(building, PX) >= 6.0)
print(f'  крыша без OSM попала в опору: '
      f'{100 * (open0 & missing_roof).sum() / missing_roof.sum():.0f}% её площади')
print(f'  сплошная крона попала в опору: '
      f'{100 * (open0 & big_canopy).sum() / big_canopy.sum():.0f}%')
ok('v5 действительно пропускает крышу', (open0 & missing_roof).sum() > 0,
   'плато 180x200 м крупнее максимального окна размыкания 126 м')

print('=' * 72)
print('2. Масштаб огибающей подбирается по данным')
b_px, rep = libg.choose_block_px(open0, PX, dsm=dsm)
print(f'  уклон рельефа по данным: {rep["relief_slope_m_per_km"]:.2f} м/км')
for r in rep['candidates']:
    print(f'    блок {r["block_m"]:5.0f} м -> окно {r["window_m"]:5.0f} м, '
          f'достижимость {r["reach_cover"]:.2f}, рельеф {r["terrain_cost_m"]:.2f} м, '
          f'{"годен" if r["feasible"] else "нет"}')
print(f'  выбран блок {rep["chosen_block_m"]:.0f} м, окно {rep["chosen_window_m"]:.0f} м')
ok('окно накрыло крупнейшее загрязнённое плато (>=200 м)',
   rep['chosen_window_m'] >= 200)

print('=' * 72)
print('3. v6: огибающая + отсечение обрывов')
anchors, ground2, dist_b, env, info = libg.anchor_field(
    dsm, building, water, pixel_m=PX, open_min_m=6.0)
print(f'  опора v5 {info["anchors_v5_px"]:,} px -> v6 {info["anchors_px"]:,} px')
print(f'  отвергнуто выше огибающей: {info["rejected_above_envelope_px"]:,} px, '
      f'на обрывах: {info["rejected_steep_px"]:,} px')
ok('крыша без OSM выброшена из опоры', not anchors[missing_roof].any())
ok('сплошная крона выброшена из опоры', not anchors[big_canopy].any())
true_open = cand & ~missing_roof & ~big_canopy & ~street
keep_frac = (anchors & true_open).sum() / true_open.sum()
print(f'  настоящая открытая земля сохранена: {100 * keep_frac:.1f}%')
ok('настоящая земля сохранена >85%', keep_frac > 0.85)
err = dsm[anchors] - truth[anchors]
print(f'  смещение опоры: {np.median(err):+.3f} м, '
      f'MAD {np.median(np.abs(err - np.median(err))) * 1.4826:.3f} м')
ok('опора несмещена', abs(np.median(err)) < 0.05)

print('=' * 72)
print('4. Опорное поле больше не тянет вверх соседние кварталы')
prior6 = libg.build_prior(np.where(anchors, dsm, np.nan), domain=~water)
prior5 = libg.build_prior(np.where(open0, dsm, np.nan), domain=~water)
# Смотреть надо на сам след крыши: именно эти пиксели попадут в
# поверхность как «земля», и именно там v5 писала уровень кровли.
e5 = np.nanmedian(prior5[missing_roof] - truth[missing_roof])
e6 = np.nanmedian(prior6[missing_roof] - truth[missing_roof])
print(f'  ошибка prior на следе крыши:  v5 {e5:+6.2f} м -> v6 {e6:+6.2f} м')
c5 = np.nanmedian(prior5[big_canopy] - truth[big_canopy])
c6 = np.nanmedian(prior6[big_canopy] - truth[big_canopy])
print(f'  ошибка prior под сплошной кроной: v5 {c5:+6.2f} м -> v6 {c6:+6.2f} м')
ok('v6 убрал завышение поля на крыше', abs(e6) * 5 < abs(e5))
ok('v6 убрал завышение поля под кроной', abs(c6) * 5 < abs(c5))

print('=' * 72)
print('5. Односторонний вердикт улицы')
v, d, n = libg.street_verdict(dsm[street], prior6[street], tol_m=0.50)
print(f'  улица в тени (+1.8 м): вердикт={v}, Δ={d:+.2f} м')
ok('улица выше опоры -> bridged', v == 'bridged')
v2, d2, _ = libg.street_verdict(truth[street], prior6[street] + 6.0, tol_m=0.50)
print(f'  улица на 6 м НИЖЕ опоры: вердикт={v2}, Δ={d2:+.2f} м')
ok('улица ниже опоры -> anchor_suspect, а не bridged', v2 == 'anchor_suspect',
   'ровно случай 15-й линии (Δ = -6.25 м): мостить её было бы заменой '
   'хорошего плохим')

print('=' * 72)
print('6. Асимметричная приёмка DSM')
pr = np.array([[3.0, 3.0, 3.0, 3.0]])
ds = np.array([[3.4, 3.9, 2.2, 1.0]])
a = libg.accept_dsm(ds, pr, tol_up=0.5, tol_down=1.5)
ok('+0.4 над опорой принимается', a[0, 0])
ok('+0.9 над опорой отвергается (крона)', not a[0, 1])
ok('-0.8 под опорой принимается', a[0, 2], 'ниже опоры ошибается чаще опора')
ok('-2.0 под опорой отвергается', not a[0, 3])

print('=' * 72)
print(f'ИТОГО: {sum(PASS)}/{len(PASS)} проверок пройдено')
sys.exit(0 if all(PASS) else 1)
