"""stats_toolkit.icesat_tracks — работа с треками ICESat-2 по правилу §1.2.

Запрет handoff: «делить точки ICESat случайно по точкам — только треками
(RGT) целиком». Модуль даёт единственный законный способ разбиения и
агрегации: split_by_rgt / aggregate_track_medians / n_eff.

Ключ трека — (rgt, cycle, gt)? Нет: RGT целиком (rgt без cycle), если
не сказано иное. Циклы одного RGT — зависимые наблюдения одного трека.
ground track (gt) при желании передаётся в составе ключа.
"""

from __future__ import annotations

import numpy as np

__all__ = ["track_key", "track_keys_from_df", "split_by_rgt",
           "aggregate_track_medians", "n_eff", "track_median_dh"]


def track_key(rgt, cycle=None, gt=None):
    """Ключ трека. По умолчанию — RGT целиком (циклы не разделяют трек)."""
    if gt is not None:
        return (int(rgt), int(gt)) if cycle is None else (int(rgt), int(gt))
    return int(rgt)


def track_keys_from_df(df, rgt_col="rgt", cycle_col=None, gt_col=None):
    """Массив ключей трека для DataFrame (pandas) или последовательностей."""
    rgt = np.asarray(df[rgt_col] if hasattr(df, "__getitem__") and rgt_col in df
                     else df)
    if cycle_col and cycle_col in getattr(df, "columns", []):
        cycle = np.asarray(df[cycle_col])
    else:
        cycle = None
    if gt_col and gt_col in getattr(df, "columns", []):
        gt = np.asarray(df[gt_col])
    else:
        gt = None
    return np.array([track_key(a, c, g) for a, c, g in
                     zip(rgt, cycle if cycle is not None else [None] * len(rgt),
                         gt if gt is not None else [None] * len(rgt))],
                    dtype=object)


def split_by_rgt(rgt, frac_holdout: float = 0.3, seed: int = 0):
    """Разбить наблюдения на два множества ЦЕЛЫМИ ТРЕКАМИ.

    Возвращает (маска_holdout, маска_fit). Точки одного RGT всегда вместе.
    Гарантирует оба множества непустыми, если треков >= 2.
    """
    rgt = np.asarray(rgt)
    keys = list(dict.fromkeys(rgt.tolist()))
    if len(keys) < 2:
        raise ValueError("нужен хотя бы один трек в каждой части")
    rng = np.random.default_rng(seed)
    order = rng.permutation(len(keys))
    n_hold = max(1, min(len(keys) - 1, int(round(len(keys) * frac_holdout))))
    hold_keys = {keys[i] for i in order[:n_hold]}
    hold = np.array([k in hold_keys for k in rgt.tolist()])
    return hold, ~hold


def aggregate_track_medians(diffs, rgt):
    """Медиана dH по каждому треку -> (медианы, ключи)."""
    diffs = np.asarray(diffs, float)
    rgt = np.asarray(rgt)
    keys, meds = [], []
    for k in dict.fromkeys(rgt.tolist()):
        keys.append(k)
        meds.append(float(np.median(diffs[rgt == k])))
    return np.asarray(meds), keys


def track_median_dh(diffs, rgt):
    """Совместимо по имени с 5.67-конвенцией (icesat_per_rgt_retest.json):
    словарь {ключ_трека: медиана dH}."""
    meds, keys = aggregate_track_medians(diffs, rgt)
    return {k: m for k, m in zip(keys, meds)}


def n_eff(rgt) -> int:
    """n_eff = число треков (RGT), не точек. «~10-12, not 37K» (5.67)."""
    return int(len(dict.fromkeys(np.asarray(rgt).tolist())))
