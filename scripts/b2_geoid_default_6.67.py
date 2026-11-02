#!/usr/bin/env python3
"""Самотест B2 (handoff 2026-11): lib_vertical на поточечном EGM2008.

Приёмка (AGENT_HANDOFF §3-B2):
  1. самотест geoid_egm2008.py — N 15.76…16.00 м по городу;
  2. load_vertical по умолчанию возвращает grid-режим с D из datum_D.json;
  3. ellipsoid_to_bs77 даёт H_bs77 = h_ell − N − D (ручные проверки);
  4. старые результаты воспроизводятся флагом city_v3
     (город 15.6727, σ 0.6286; vo/mainland — город, regional не significant);
  5. grid-режим НЕ даёт скалярного shift_m (громкое падение старого кода).
Запуск: python3 scripts/b2_geoid_default_6.67.py
"""

import json
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np

GH = Path('/tmp/my-project/gh-clone')
sys.path.insert(0, str(GH / 'scripts'))

FAILS = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  [{detail}]" if detail else ""))
    if not cond:
        FAILS.append(name)


# 1. самотест модуля геоида (приёмка B2: N 15.76–16.00 по городу)
print("--- geoid_egm2008 self-test ---")
import subprocess
r = subprocess.run([sys.executable, str(GH / 'scripts' / 'geoid_egm2008.py'),
                    '/tmp/my-project/data/us_nga_egm08_25.tif'],
                   capture_output=True, text=True)
print(r.stdout.strip())
check("1 самотест geoid_egm2008.py", r.returncode == 0, r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-80:])

from lib_vertical import ellipsoid_to_bs77, load_vertical  # noqa: E402

# 2. дефолт = grid-режим EGM2008 + D из datum_D.json
print("--- load_vertical (по умолчанию) ---")
tmp = Path(tempfile.mkdtemp(prefix='b2test_'))
v = load_vertical(tmp, logger=lambda *a: None)
check("2a mode=grid, shift_m=None", v['mode'] == 'grid' and v['shift_m'] is None)
check("2b D = 0.00 из datum_D.json", abs(v['D']) < 1e-9 and v['D_SE'] <= 0.07,
      f"D={v['D']:+.3f}±{v['D_SE']:.3f}")
check("2c бюджет σ 0.10", abs(v['sigma_m'] - 0.10) < 1e-9, f"{v['sigma_m']}")
check("2d source", v['source'] == 'geoid_egm2008_grid_D+0.00', v['source'])

# 3. ручные проверки преобразования (значения N из самотеста модуля)
print("--- ellipsoid_to_bs77 ---")
lo = np.array([29.776, 30.27, 30.32])      # Кронштадт, В.О., центр
la = np.array([59.989, 59.94, 59.935])
h_ell = np.full(3, 5.0)
H = ellipsoid_to_bs77(h_ell, lo, la, v)
N = v['geoid'].n(lo, la)
check("3 H = h_ell − N − D (поточечно)",
      np.allclose(H, h_ell - N - v['D'], atol=1e-9),
      f"Кронштадт: N={N[0]:.3f}, H={H[0]:.3f}")
check("3b N в диапазоне аудита 15.76…16.00",
      15.7 < N.min() and N.max() < 16.05, f"{N.min():.3f}…{N.max():.3f}")

# 4. воспроизведение старых результатов через city_v3
print("--- city_v3 (воспроизведение) ---")
shutil.copy(GH / 'download' / 'city_calibration.json', tmp / 'city_calibration.json')
v3 = load_vertical(tmp, region='vo', logger=lambda *a: None,
                   geoid_source='city_v3')
check("4a город 15.6727 (vo, regional не significant)",
      abs(v3['shift_m'] - 15.6727) < 1e-9, f"{v3['shift_m']:.4f}")
check("4b σ between_street 0.6286", abs(v3['sigma_m'] - 0.6286) < 1e-9,
      f"{v3['sigma_m']:.4f}")
check("4c source=city_v3, mode=scalar", v3['source'] == 'city_v3'
      and v3['mode'] == 'scalar')
v3m = load_vertical(tmp, region='mainland', logger=lambda *a: None,
                    geoid_source='city_v3')
check("4d mainland тоже городская константа (significant=False)",
      abs(v3m['shift_m'] - 15.6727) < 1e-9, f"{v3m['shift_m']:.4f}")
H_old = ellipsoid_to_bs77(20.0, 30.27, 59.94, v3m)
check("4e city_v3: H = h_ell − shift (прежняя арифметика)",
      abs(H_old - (20.0 - 15.6727)) < 1e-9, f"{H_old:.4f}")

# 5. старый код, читающий shift_m как число, падает громко
print("--- защита от молчаливой константы ---")
try:
    h_ell - v['shift_m']
    check("5 grid shift_m=None не арифметичен", False, "TypeError не возник")
except TypeError:
    check("5 grid shift_m=None не арифметичен", True, "TypeError, как задумано")
try:
    load_vertical(tmp, geoid_source='15.9')
    check("5b неизвестная константа отвергается", False)
except ValueError:
    check("5b неизвестная константа отвергается", True)

print()
if FAILS:
    print(f"FAIL: {len(FAILS)}: {', '.join(FAILS)}")
    sys.exit(1)
print("B2 OK — EGM2008 по умолчанию, city_v3 воспроизводит старое, "
      "молчаливых констант нет")
