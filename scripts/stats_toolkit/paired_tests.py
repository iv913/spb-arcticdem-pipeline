"""stats_toolkit.paired_tests — парные тесты для вердиктов по §1 handoff 2026-11.

Правила, которые реализует модуль (AGENT_HANDOFF_2026-11.md, §1 «Валидация»):
1. вердикт NEVER по точечному MAD — только парные тесты;
2. ICESat агрегируется по трекам (RGT) целиком, n_eff = число RGT;
3. порог практической значимости 0.1 м — отдельно от p-значений;
4. вердикт = число значимых тестов из трёх: знаковый, Уилкоксон,
   bootstrap-CI медианы (0 / 1 / >=2 из 3).

Соглашение по именам из ревизии 5.67 (NOTES_repo_base_5_67_review.md §2):
расчёт по точкам вместо треков оставлен как контрпример
(`paired_verdict_per_point_wrong`) с флагом `per_point_bootstrap_wrong` —
его CI в 20+ раз уже и в отчёт попадать не должен.

Зависимости: numpy; scipy используется, если доступна (иначе точный
биномиальный знаковый тест и нормальная аппроксимация Уилкоксона).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

PRACTICAL_THRESHOLD_M = 0.1  # порог практической значимости, м (5.67)

try:  # scipy опциональна — toolkit должен переноситься
    from scipy import stats as _sps
except Exception:  # pragma: no cover
    _sps = None


# ---------------------------------------------------------------- агрегация

def aggregate_by_rgt(diffs, rgt):
    """Медиана dH по каждому треку (RGT). Возвращает (медианы_по_RGT, ключи).

    diffs, rgt — последовательности одинаковой длины. Точки одного трека
    схлопываются в одно наблюдение: именно это снимает инфляцию p-значений.
    """
    diffs = np.asarray(diffs, float)
    rgt = np.asarray(rgt)
    if diffs.shape != rgt.shape:
        raise ValueError("diffs и rgt должны быть одной длины")
    keys, meds = [], []
    for k in np.unique(rgt):
        keys.append(k)
        meds.append(float(np.median(diffs[rgt == k])))
    return np.asarray(meds), keys


def n_eff(rgt) -> int:
    """Эффективный объём выборки = число уникальных треков (RGT)."""
    return int(len(np.unique(np.asarray(rgt))))


# ------------------------------------------------------------------- тесты

def _binom_two_sided(k: int, n: int) -> float:
    """Точный двусторонний биномиальный тест (без scipy)."""
    def pmf(i):
        return math.comb(n, i) * 0.5 ** n
    p_obs = pmf(k)
    return min(1.0, sum(pmf(i) for i in range(n + 1) if pmf(i) <= p_obs + 1e-12))


def sign_test(diffs) -> float:
    """Двусторонний знаковый тест (ненулевые разности)."""
    d = np.asarray(diffs, float)
    d = d[~np.isnan(d)]
    npos = int(np.sum(d > 0))
    nneg = int(np.sum(d < 0))
    n = npos + nneg
    if n == 0:
        return 1.0
    k = max(npos, nneg)
    if _sps is not None:
        return float(_sps.binomtest(k, n, 0.5).pvalue)
    return _binom_two_sided(k, n)


def wilcoxon_test(diffs) -> float:
    """Уилкоксон для нулевой медианы разностей."""
    d = np.asarray(diffs, float)
    d = d[~np.isnan(d)]
    d = d[d != 0]
    if d.size == 0:
        return 1.0
    if _sps is not None:
        return float(_sps.wilcoxon(d).pvalue)
    # нормальная аппроксимация с поправкой на связки
    ranks = np.argsort(np.argsort(np.abs(d))) + 1.0
    t = ranks[d > 0].sum()
    mu = d.size * (d.size + 1) / 4.0
    sigma = math.sqrt(d.size * (d.size + 1) * (2 * d.size + 1) / 24.0)
    z = (t - mu) / sigma
    return 2.0 * (1.0 - 0.5 * (1.0 + math.erf(abs(z) / math.sqrt(2.0))))


def bootstrap_ci(diffs, n_boot: int = 10000, seed: int = 42,
                 stat=np.median) -> tuple:
    """Percentile-bootstrap CI для медианы. Ресэмплирует целиком
    переданные наблюдения: по трекам — если поданы медианы по RGT,
    по точкам — если поданы точки (не делайте этого с ICESat)."""
    d = np.asarray(diffs, float)
    d = d[~np.isnan(d)]
    if d.size == 0:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, d.size, size=(n_boot, d.size))
    boots = stat(d[idx], axis=1)
    return (float(np.percentile(boots, 2.5)),
            float(np.percentile(boots, 97.5)))


# ---------------------------------------------------------------- вердикт

@dataclass
class Verdict:
    n: int                    # число наблюдений, поданных в тест
    n_eff: int                # число треков (или n, если RGT не задан)
    median_m: float
    ci95: tuple
    sign_p: float
    wilcoxon_p: float
    n_tests_significant: int
    practically_significant: bool   # |median| > 0.1 м
    verdict: str                    # SIGNIFICANT / MARGINAL / NOT_SIGNIFICANT
    notes: list = field(default_factory=list)

    def as_dict(self):
        return {
            "n": self.n, "n_eff": self.n_eff,
            "median_m": self.median_m, "ci95": list(self.ci95),
            "sign_p": self.sign_p, "wilcoxon_p": self.wilcoxon_p,
            "n_tests_significant": self.n_tests_significant,
            "practically_significant": self.practically_significant,
            "verdict": self.verdict, "notes": self.notes,
        }


def paired_verdict(diffs, rgt=None, threshold: float = PRACTICAL_THRESHOLD_M,
                   alpha: float = 0.05) -> Verdict:
    """Вердикт парного сравнения «новое против базы» (§1.6 handoff).

    diffs — знаковые разности (модель − истина или новая − база);
    rgt — треки, если наблюдения ICESat (обязательно: агрегация по точкам
    запрещена §1.2). Вердикт по числу значимых тестов из трёх.
    """
    d = np.asarray(diffs, float)
    ok = ~np.isnan(d)
    d = d[ok]
    if rgt is not None:
        r = np.asarray(rgt)[ok]
        track_medians, keys = aggregate_by_rgt(d, r)
        unit = track_medians
        eff = len(keys)
    else:
        unit = d
        eff = d.size
    ci = bootstrap_ci(unit)
    sp = sign_test(unit)
    wp = wilcoxon_test(unit)
    med = float(np.median(unit)) if unit.size else float("nan")
    sig = int(sp < alpha) + int(wp < alpha) + int(
        math.isfinite(ci[0]) and math.isfinite(ci[1]) and (ci[0] > 0 or ci[1] < 0))
    verdict = "SIGNIFICANT" if sig >= 2 else ("MARGINAL" if sig == 1 else "NOT_SIGNIFICANT")
    notes = []
    if rgt is None:
        notes.append("rgt не задан — проверьте, что наблюдения не ICESat (§1.2)")
    return Verdict(d.size, eff, med, ci, sp, wp, sig,
                   bool(abs(med) > threshold), verdict, notes)


def paired_verdict_per_point_wrong(diffs) -> Verdict:
    """КОНТРПРИМЕР (имя из ревизии 5.67): парный тест по точкам ICESat.

    Оставлен, чтобы в отчёте показать, во сколько раз CI по точкам уже
    CI по трекам (`ci_shrink_factor`). В вердикты не включать.
    """
    d = np.asarray(diffs, float)
    d = d[~np.isnan(d)]
    ci = bootstrap_ci(d)
    sp = sign_test(d)
    wp = wilcoxon_test(d)
    med = float(np.median(d)) if d.size else float("nan")
    sig = int(sp < 0.05) + int(wp < 0.05) + int(ci[0] > 0 or ci[1] < 0)
    verdict = "SIGNIFICANT" if sig >= 2 else ("MARGINAL" if sig == 1 else "NOT_SIGNIFICANT")
    v = Verdict(d.size, d.size, med, ci, sp, wp, sig,
                bool(abs(med) > PRACTICAL_THRESHOLD_M), verdict,
                notes=["per_point_bootstrap_wrong"])
    return v


def ci_shrink_factor(diffs, rgt) -> float:
    """Во сколько раз CI по точкам уже CI по трекам (демонстрация инфляции)."""
    d = np.asarray(diffs, float)
    r = np.asarray(rgt)
    ok = ~np.isnan(d)
    _, keys = aggregate_by_rgt(d[ok], r[ok])
    tm = np.array([np.median(d[ok][r[ok] == k]) for k in keys])
    ci_t = bootstrap_ci(tm, seed=1)
    ci_p = bootstrap_ci(d[ok], seed=1)
    w_t = ci_t[1] - ci_t[0]
    w_p = ci_p[1] - ci_p[0]
    return float(w_p / w_t) if w_t > 0 else float("inf")
