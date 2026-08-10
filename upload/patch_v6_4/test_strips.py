#!/usr/bin/env python3
"""
test_strips.py — синтетика многовременного стека (без rasterio).

Сцена: плоский участок В.О. с уклоном, снятый шесть раз. У каждой даты
свой вертикальный сдвиг и наклон (коregистрация), свои припаркованные
машины (переезжают между датами), свои провалы стерео. Три даты зимние —
на газонах снег +0.25 м. Проверяем, что нижняя огибающая ПО ВРЕМЕНИ
восстанавливает землю там, где мозаика (среднее по датам) её завышает.
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import lib_strips as libs                                      # noqa: E402

PX = 2.0
H, W = 200, 260
yy, xx = np.mgrid[0:H, 0:W]
truth = 3.00 + 0.0009 * (xx * PX)            # 0.9 м/км, как на В.О.

buildings = np.zeros((H, W), bool)
for y0 in (30, 90, 150):
    buildings[y0:y0 + 25, 20:240] = True
lawn = np.zeros((H, W), bool)                # газоны и дворы
lawn[60:88, 30:230] = True
lawn[120:148, 30:230] = True
stable = ~buildings & ~lawn                  # твёрдые покрытия: опора коregистрации

DATES = ['20180712', '20190215', '20200803', '20210118', '20220906', '20230301']
MONTHS = [7, 2, 8, 1, 9, 3]
TRUE_SHIFT = [0.35, -0.62, 0.11, 0.88, -0.24, 0.47]
TRUE_TILT = [0.8, -1.4, 0.3, 2.1, -0.9, 1.2]      # м/км по x
SNOW = 0.25

rng = np.random.default_rng(19)
layers, layers_raw = [], []
for i, dt in enumerate(DATES):
    L = truth.copy()
    L[buildings] = truth[buildings] + 21.0
    if MONTHS[i] in libs.WINTER_MONTHS:
        L[lawn] += SNOW                       # снег лежит на газонах
    for _ in range(70):                       # машины: каждый раз новые места
        r, c = rng.integers(0, H), rng.integers(0, W)
        if buildings[r, c]:
            continue
        L[max(0, r - 1):r + 2, max(0, c - 2):c + 3] += 1.5
    L += rng.normal(0, 0.06, L.shape)
    L += TRUE_SHIFT[i] + TRUE_TILT[i] / 1000.0 * (xx * PX - W * PX / 2)
    void = np.zeros((H, W), bool)             # провал стерео
    r0, c0 = rng.integers(0, H - 40), rng.integers(0, W - 40)
    void[r0:r0 + 35, c0:c0 + 35] = True
    L[void] = np.nan
    layers_raw.append(L)

PASS = []


def ok(name, cond, detail=''):
    PASS.append(bool(cond))
    print(f'  [{"OK " if cond else "FAIL"}] {name}' + (f' — {detail}' if detail else ''))


print('=' * 72)
print('1. Коregистрация плоскостью снимает сдвиг и наклон стрипа')
ref = truth.copy(); ref[buildings] = truth[buildings] + 21.0
infos = []
for i, L in enumerate(layers_raw):
    c, info = libs.coregister_plane(L, ref if False else L * 0 + ref,
                                    stable, pixel_m=PX)
    # коregистрируем СТРИП к опоре: аргументы (ref, src, ...)
    c, info = libs.coregister_plane(ref, L, stable, pixel_m=PX)
    layers.append(c); infos.append(info)
    print(f'  {DATES[i]}: сдвиг {info["shift_m"]:+.2f} (истина {TRUE_SHIFT[i]:+.2f}), '
          f'наклон x {info["tilt_x_m_per_km"]:+.2f} (истина {TRUE_TILT[i]:+.2f}), '
          f'MAD {info["mad_before_m"]:.2f} -> {info["mad_after_m"]:.2f}, {info["status"]}')
err_shift = max(abs(i_['shift_m'] - s) for i_, s in zip(infos, TRUE_SHIFT))
err_tilt = max(abs(i_['tilt_x_m_per_km'] - t) for i_, t in zip(infos, TRUE_TILT))
ok('сдвиг восстановлен точнее 5 см', err_shift < 0.05, f'макс ошибка {err_shift:.3f} м')
ok('наклон восстановлен точнее 0.3 м/км', err_tilt < 0.3, f'макс {err_tilt:.3f}')
ok('все стрипы приняты', all(i_['status'] == 'ok' for i_ in infos))

print('=' * 72)
print('2. Нижняя огибающая по времени против среднего (аналог мозаики)')
noise = float(np.median([i_['mad_after_m'] for i_ in infos]))
print(f'  шум по остатку коregистрации: {noise:.3f} м -> поправка перцентиля '
      f'{abs(libs._z_of(0.20)) * noise:.3f} м')
st = libs.stack_temporal(layers, noise_sigma=noise)
with np.errstate(all='ignore'):
    mosaic_like = np.nanmean(np.stack(layers, axis=0), axis=0)
open_land = ~buildings & (st['n_obs'] >= 3)


def stat(mask, name):
    a = mosaic_like[mask] - truth[mask]
    b = st['ground'][mask] - truth[mask]
    ea, eb = np.nanmedian(a), np.nanmedian(b)
    ma = np.nanmedian(np.abs(a - ea)) * 1.4826
    mb = np.nanmedian(np.abs(b - eb)) * 1.4826
    print(f'  {name:28s} среднее {ea:+.3f} (MAD {ma:.3f}) | '
          f'огибающая {eb:+.3f} (MAD {mb:.3f})')
    return ea, eb, ma, mb


ea, eb, ma, mb = stat(open_land, 'вся открытая земля')
la, lb, _, _ = stat(lawn & open_land, 'газоны (снег 3 даты из 6)')
ok('на всей площади огибающая не хуже', abs(eb) <= abs(ea) + 0.01,
   'загрязнение разрежено, поэтому среднее тут почти не страдает')
ok('огибающая уже по разбросу', mb <= ma + 0.005, f'{mb:.3f} против {ma:.3f}')
ok('снег с газонов снят', abs(lb) < 0.5 * abs(la),
   f'мозаика несёт {la:+.2f} м снега, огибающая {lb:+.2f} м')

print('=' * 72)
print('3. Машины: снимаются временем даже без морфологии')
# пиксели, где хотя бы на одной дате стояла машина
cube = np.stack(layers, axis=0)
with np.errstate(all='ignore'):
    hit = (np.nanmax(cube, axis=0) - truth) > 1.0
hit &= open_land
print(f'  пикселей с машиной хотя бы на одной дате: {int(hit.sum()):,}')
e_hit_mos = np.nanmedian(mosaic_like[hit] - truth[hit])
e_hit_gr = np.nanmedian(st['ground'][hit] - truth[hit])
print(f'  на них: среднее {e_hit_mos:+.2f} м, огибающая {e_hit_gr:+.2f} м')
ok('огибающая убирает вклад машин', abs(e_hit_gr) < 0.5 * abs(e_hit_mos) + 0.05)

print('=' * 72)
print('4. Сезонное смещение измеряется напрямую')
sb = libs.seasonal_bias(layers, MONTHS, lawn & (st['n_obs'] >= 3))
print(f'  зима минус лето на газонах: {sb["winter_minus_summer_m"]:+.3f} м '
      f'(заложено {SNOW:+.2f}), MAD {sb.get("mad_m")}, '
      f'{sb["n_winter"]} зимних / {sb["n_summer"]} летних')
ok('снежное смещение восстановлено', abs(sb['winter_minus_summer_m'] - SNOW) < 0.10)
sb2 = libs.seasonal_bias(layers[:1], MONTHS[:1], lawn)
ok('при одном сезоне честно сообщается, что мерить нечем',
   sb2['winter_minus_summer_m'] is None and 'note' in sb2)

print('=' * 72)
print('5. Измеренная сигма вместо таблицы классов')
sig = libs.temporal_sigma(st['spread'], st['n_obs'],
                          floor=0.0, few_obs_penalty=0.0)
err = np.abs(st['ground'] - truth)
q = np.isfinite(sig) & open_land
lo = sig[q] <= np.nanpercentile(sig[q], 33)
hi = sig[q] >= np.nanpercentile(sig[q], 67)
print(f'  фактическая |ошибка|: в тихих пикселях {np.nanmedian(err[q][lo]):.3f} м, '
      f'в шумных {np.nanmedian(err[q][hi]):.3f} м')
ok('измеренная сигма ранжирует ошибку',
   np.nanmedian(err[q][hi]) > np.nanmedian(err[q][lo]),
   'то есть её можно ставить в бюджет вместо DETAIL_SIGMA')

print('=' * 72)
print('6. По-пиксельный выбор эпохи для намыва')
namyv = np.zeros((H, W), bool); namyv[:, 0:40] = True
lay2 = [l.copy() for l in layers]
for i in range(len(lay2)):                      # намыв отсыпан после 2021
    lay2[i][namyv] = 0.05 if DATES[i] < '20220000' else 3.90
sel, picked = libs.pick_latest_valid(lay2, DATES, where=namyv)
print(f'  выбранные даты на намыве: '
      f'{sorted({DATES[p] for p in np.unique(picked[namyv]) if p >= 0})}')
ok('взята самая поздняя эпоха', np.nanmedian(sel[namyv]) > 3.0,
   f'медиана {np.nanmedian(sel[namyv]):.2f} м вместо 0.05 м у мозаики')
ok('вне маски ничего не подставлено', not np.isfinite(sel[~namyv]).any())

print('=' * 72)
print('7. Смесь со стеком: мозаика остаётся страховкой покрытия')
mos = mosaic_like.copy()
surf, src = libs.blend_with_mosaic(mos, st['ground'], st['n_obs'])
print(f'  из стека {int((src == 1).sum()):,} px, из мозаики {int((src == 2).sum()):,} px')
ok('стек покрывает большую часть', (src == 1).sum() > (src == 2).sum())
ok('дыр не осталось', np.isfinite(surf).all())



# ============================================================
# Эпохи и приоритет лета — по реальному составу манифеста В.О.
# ============================================================
print('=' * 72)
print('8. Сессия из 12 стрипов — это одна эпоха, а не 12 наблюдений')
REAL = [('20210121', 1), ('20210607', 6),
        ('20220309', 3), ('20220310', 3), ('20220312', 3), ('20220314', 3),
        ('20220315', 3), ('20220315', 3), ('20220315', 3), ('20220314', 3),
        ('20220312', 3), ('20220310', 3), ('20220309', 3), ('20220315', 3),
        ('20220624', 6), ('20220713', 7), ('20220719', 7),
        ('20230612', 6), ('20230616', 6), ('20230909', 9)]
rd = [d for d, _ in REAL]; rm = [m for _, m in REAL]
gr = libs.group_epochs(rd)
print(f'  стрипов {len(rd)} -> эпох {len(gr)}: '
      f'{[(rd[g[0]], len(g)) for g in gr]}')
ok('мартовская сессия схлопнута в одну эпоху',
   max(len(g) for g in gr) == 12,
   'иначе нижний перцентиль по 48 «датам» = перцентиль по одной съёмке')
ok('эпох меньше десятка', len(gr) <= 10)

print('=' * 72)
print('9. Шум измеряется внутри сессии, а не через коregистрацию')
sess = list(range(3))                       # три «стрипа» одной сессии
same = [layers[0] + rng.normal(0, 0.06, layers[0].shape) for _ in sess]
n_sess = libs.noise_from_session(same, sess, mask=stable)
print(f'  шум внутри сессии: {n_sess:.3f} м (заложено 0.06 м)')
ok('шум восстановлен', abs(n_sess - 0.06) < 0.02,
   'сессия 2022-03 из 12 стрипов даёт эту оценку почти даром')

print('=' * 72)
print('10. Приоритет лета: там, где есть лето, снега в опоре нет')
# сцена с реальным перекосом состава: 4 зимние эпохи, 2 летние
lay_s, mon_s, dat_s = [], [], []
for k, (mm, dd) in enumerate([(3, '20200310'), (3, '20210312'), (1, '20220118'),
                              (3, '20230314'), (7, '20210715'), (8, '20220820')]):
    L = truth.copy(); L[buildings] = truth[buildings] + 21.0
    if mm in libs.WINTER_MONTHS:
        L[lawn] += SNOW
    L += rng.normal(0, 0.06, L.shape)
    lay_s.append(L); mon_s.append(mm); dat_s.append(dd)
st_all = libs.stack_temporal(lay_s, noise_sigma=0.06)
st_sum = libs.stack_by_epoch(lay_s, dat_s, mon_s, noise_sigma=0.06,
                             min_summer_epochs=2)
e_all = np.nanmedian(st_all['ground'][lawn] - truth[lawn])
e_sum = np.nanmedian(st_sum['ground'][lawn] - truth[lawn])
print(f'  газоны: все эпохи {e_all:+.3f} м, приоритет лета {e_sum:+.3f} м '
      f'(снег {SNOW:+.2f})')
print(f'  эпох всего {st_sum["n_epochs_all"]}, летних {st_sum["n_epochs_summer"]}; '
      f'по лету построено {100 * (st_sum["season_src"] == 1).mean():.0f}% площади')
ok('приоритет лета убирает снег лучше', abs(e_sum) < abs(e_all) + 1e-9)
ok('пиксели помечены источником сезона', (st_sum['season_src'] == 1).any())

print('=' * 72)
print('11. Когда лета мало — честный откат на все эпохи с пометкой')
st_few = libs.stack_by_epoch(lay_s[:4], dat_s[:4], mon_s[:4], noise_sigma=0.06,
                             min_summer_epochs=2)
print(f'  летних эпох {st_few["n_epochs_summer"]} -> '
      f'season_src=2 на {100 * (st_few["season_src"] == 2).mean():.0f}% площади')
ok('без лета всё помечено как смесь сезонов',
   not (st_few['season_src'] == 1).any(),
   'к таким пикселям применяется измеренное сезонное смещение, а не вид, '
   'что снега не было')

print('=' * 72)
print(f'ИТОГО (эпохи): {sum(PASS)}/{len(PASS)} проверок пройдено')
sys.exit(0 if all(PASS) else 1)
