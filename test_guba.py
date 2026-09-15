#!/usr/bin/env python3
"""test_guba.py — гипсометрия и объём накопления."""
import sys, importlib.util as u
from pathlib import Path
import numpy as np

src = (Path(__file__).parent / '13_nevskaya_guba.py').read_text(encoding='utf-8')
ns = {'np': np}
exec(src[src.index('LEVELS = ['):src.index('def log(')], ns)
exec(src[src.index('def hypsometry'):src.index('def read_masked')], ns)


class G:
    hypsometry = staticmethod(ns['hypsometry'])
    fill_time = staticmethod(ns['fill_time'])
    NEVA_DISCHARGE_M3S = ns['NEVA_DISCHARGE_M3S']


g = G()

PASS = []


def ok(name, cond, detail=''):
    PASS.append(bool(cond))
    print(f'  [{"OK " if cond else "FAIL"}] {name}' + (f' — {detail}' if detail else ''))


AREA = 4.0
print('=' * 72)
print('1. Объём над зеркалом без батиметрии')
z = np.zeros(250_000, dtype='float32')
rows = g.hypsometry(z, AREA, [1.0, 2.0])
for r in rows:
    print(f'  H={r["level_m"]:.1f}: зеркало {r["area_km2"]:.3f} км², '
          f'объём {r["volume_mln_m3"]:.3f} млн м³')
ok('1 км² при H=1 м даёт 1 млн м³', abs(rows[0]['volume_mln_m3'] - 1.0) < 1e-6)
ok('объём линеен по уровню над плоским зеркалом',
   abs(rows[1]['volume_mln_m3'] - 2.0) < 1e-6,
   'дно ниже нуля в приращение не входит — батиметрия не нужна')

print('=' * 72)
print('2. Затопление суши добавляется постепенно')
land = np.linspace(0.01, 3.0, 150_000).astype('float32')
z2 = np.concatenate([z, land])
rows2 = g.hypsometry(z2, AREA, [0.5, 1.0, 2.0, 3.0])
prev_a = 0
for r in rows2:
    print(f'  H={r["level_m"]:.1f}: зеркало {r["area_km2"]:.3f} км², '
          f'объём {r["volume_mln_m3"]:.2f}, ср.глубина {r["mean_depth_m"]:.2f}')
    ok(f'зеркало растёт при H={r["level_m"]}', r['area_km2'] >= prev_a)
    prev_a = r['area_km2']
ok('объём больше, чем только над зеркалом',
   rows2[-1]['volume_mln_m3'] > 3.0,
   'затопленный берег добавляет ёмкость')

print('=' * 72)
print('3. Намыв отнимает ёмкость')
z_nam = z.copy(); z_nam[:50_000] = 3.5
a = g.hypsometry(z_nam, AREA, [2.0])[0]
b = g.hypsometry(z, AREA, [2.0])[0]
print(f'  с намывом {a["volume_mln_m3"]:.2f} млн м³, '
      f'без намыва {b["volume_mln_m3"]:.2f}')
print(f'  отнято {b["volume_mln_m3"] - a["volume_mln_m3"]:.2f} млн м³')
ok('намыв уменьшает объём', a['volume_mln_m3'] < b['volume_mln_m3'])
ok('уменьшение равно площади x уровень',
   abs((b['volume_mln_m3'] - a['volume_mln_m3']) - 0.2 * 2.0) < 1e-6,
   '0.2 км² намыва при H=2 м = 0.4 млн м³')

print('=' * 72)
print('4. Время наполнения стоком')
t, per_day = g.fill_time([216.0, 432.0])
print(f'  сток {g.NEVA_DISCHARGE_M3S:.0f} м³/с = {per_day} млн м³/сут')
print(f'  216 млн м³ -> {t[0]} сут, 432 -> {t[1]} сут')
ok('сутки считаются верно', abs(t[0] - 1.0) < 0.01)
ok('вдвое больший объём — вдвое дольше', abs(t[1] - 2 * t[0]) < 0.02)

print('=' * 72)
print('5. Неопределённость объёма')
r = g.hypsometry(z2, AREA, [2.0], sigma_z=1.9)[0]
print(f'  объём {r["volume_mln_m3"]:.2f} ± {r["volume_sigma_mln_m3"]:.2f} млн м³')
ok('сигма пропорциональна площади',
   abs(r['volume_sigma_mln_m3'] - 1.9 * r['area_km2']) < 0.01,
   'при MAD 1.9 м ошибка объёма сопоставима с самим объёмом на малых H')

print('=' * 72)
print(f'ИТОГО: {sum(PASS)}/{len(PASS)} проверок пройдено')
sys.exit(0 if all(PASS) else 1)
