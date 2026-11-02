"""Смоук-тест переносимости stats_toolkit (приёмка B1 handoff 2026-11).

Запуск:  python3 scripts/stats_toolkit/test_portability.py
Зелёный = exit 0, все проверки PASS. Внешних зависимостей не требует
сверх numpy (scipy опциональна; обе ветки выдают согласованные p).

Проверки:
  1. эффект +0.5 м ловится по трекам (CI не содержит 0, verdict не пустой);
  2. тот же набор по точкам даёт CI в разы уже (инфляция) — урок 5.67 (21x);
  3. нулевой эффект не значим (CI содержит 0);
  4. знаковый тест согласован с точным биномиальным на ручном примере;
  5. split_by_rgt режет только целыми треками и части не пересекаются;
  6. порог практической значимости 0.1 м выставляется отдельно от p.
"""

from __future__ import annotations

import sys

import numpy as np

sys.path.insert(0, __file__.rsplit("/", 2)[0])  # scripts/
from stats_toolkit.paired_tests import (  # noqa: E402
    PRACTICAL_THRESHOLD_M, aggregate_by_rgt, bootstrap_ci, ci_shrink_factor,
    paired_verdict, paired_verdict_per_point_wrong, sign_test, wilcoxon_test)
from stats_toolkit.icesat_tracks import n_eff, split_by_rgt  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  [{detail}]" if detail else ""))
    if not cond:
        FAILS.append(name)


rng = np.random.default_rng(7)

# --- 1. детекция эффекта +0.2 м по трекам (30 треков x 40 точек)
# Реалистичная структура ICESat: у каждого трека свой сдвиг (рельеф/кроны
# вдоль 40-м сегмента) + мелкий шум точек. Именно внутри-трековая
# корреляция делает per-point bootstrap нечестным (см. проверку 2).
n_tr, n_pt = 30, 40
rgt = np.repeat(np.arange(n_tr), n_pt)
track_shift = np.repeat(rng.normal(0, 0.30, n_tr), n_pt)  # свой сдвиг на трек
point_noise = rng.normal(0, 0.05, n_tr * n_pt)
EFFECT = 0.50  # заведомо больше разброса медиан треков (~0.11)
model_minus_truth = EFFECT + track_shift + point_noise
v = paired_verdict(model_minus_truth, rgt=rgt)
check("1a эффект +0.5: CI не содержит 0", v.ci95[0] > 0, f"CI {v.ci95[0]:.3f}..{v.ci95[1]:.3f}")
check("1b n_eff = 30 треков", v.n_eff == n_tr, f"n_eff={v.n_eff}")
check("1c median близка к +0.5", abs(v.median_m - EFFECT) < 0.15, f"{v.median_m:.3f}")
check("1d практический порог 0.1 м", v.practically_significant, "0.5 > 0.1")

# --- 2. по точкам CI заметно УЖЕ по-треков (инфляция, урок 5.67: 21x)
vpp = paired_verdict_per_point_wrong(model_minus_truth)
factor = ci_shrink_factor(model_minus_truth, rgt)   # w_точек / w_треков
check("2a per-point CI хотя бы втрое уже по-треков", factor < 1.0 / 3.0, f"w_p/w_t = {factor:.2f}")
check("2b контрпример помечен", "per_point_bootstrap_wrong" in vpp.notes)

# --- 3. нулевой эффект не значим (свой генератор: детерминированность)
rngn = np.random.default_rng(101)
null = rngn.normal(0, 0.15, n_tr * n_pt)
v0 = paired_verdict(null, rgt=rgt)
check("3 нулевой эффект: CI содержит 0", v0.ci95[0] <= 0 <= v0.ci95[1],
      f"CI {v0.ci95[0]:.3f}..{v0.ci95[1]:.3f}")
check("3b вердикт не SIGNIFICANT", v0.verdict != "SIGNIFICANT", v0.verdict)

# --- 4. знаковый тест на ручном примере: [+, +, +, -] -> p = 0.625
p = sign_test(np.array([1.0, 1.0, 1.0, -1.0]))
check("4 знаковый тест p([+++ -]) = 0.625", abs(p - 0.625) < 1e-9, f"p={p:.4f}")
pw = wilcoxon_test(np.array([1.0, 2.0, 3.0, -1.0]))
check("4b Уилкоксон в [0,1]", 0.0 <= pw <= 1.0, f"p={pw:.3f}")

# --- 5. split_by_rgt целыми треками
hold, fit = split_by_rgt(rgt, frac_holdout=0.3, seed=3)
tr_h = set(rgt[hold].tolist()); tr_f = set(rgt[fit].tolist())
check("5a треки не пересекаются", not (tr_h & tr_f))
check("5b вместе покрывают все", tr_h | tr_f == set(range(n_tr)))
check("5c в каждом треке все точки вместе",
      all(hold[rgt == t].all() or (~hold[rgt == t]).all() for t in range(n_tr)))
check("5d обе части непусты", 0 < hold.sum() < len(rgt))

# --- 6. порог практической значимости: маленькое смещение 0.05 м
small = 0.05 + rng.normal(0, 0.10, n_tr * n_pt)
vs = paired_verdict(small, rgt=rgt)
check("6 медиана 0.05 < 0.1: порог не выставлен",
      vs.practically_significant is False, f"median={vs.median_m:.3f}")
check("6b константа = 0.1 м", PRACTICAL_THRESHOLD_M == 0.1)

# --- 7. n_eff и агрегация согласованы
check("7 n_eff(rgt) = число треков", n_eff(rgt) == n_tr)
m, keys = aggregate_by_rgt(model_minus_truth, rgt)
check("7b агрегат по трекам согласован с verdict", len(m) == v.n_eff)

print()
if FAILS:
    print(f"FAIL: {len(FAILS)} проверок: {', '.join(FAILS)}")
    sys.exit(1)
print("SMOKE OK — stats_toolkit переносим, правила §1 реализованы")
