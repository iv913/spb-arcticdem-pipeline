#!/usr/bin/env python3
"""
16_stage2_protocol.py
====================
Протокол для Stage 2 (уравнивание источников до сшивки).

КОНТЕКСТ (пользователь):
- Stage 2 начинать с самого начала сохранять per-point dH на каждой итерации
- Потеря CSV для v9 стоила невозможности проверки
- Пусть это будет частью протокола, а не доброй волей

Протокол описывает:
1. Что сохранять на каждой итерации
2. Какие тесты применять
3. Когда правка принимается / отвергается
4. Как проверить с ICESat (43K) И passport (40) — два независимых критерия

Архитектура Stage 2 (по STAGE2_DESIGN.md):
1. Сбор overlaps между источниками (ICESat × DSM, ICESat × паспорт, etc.)
2. МНК для b_j: z_ij = Z_i + b_j + ε
3. Применить b_j к источникам перед build_prior
4. Сшивка как обычно
5. Валидация: per-point dH (passport + ICESat) + sign test

Каждый шаг Stage 2 возвращает:
- per-point dH на passport (n=40)
- per-point dZ на ICESat (n=43K)
- CSV с обеих выборок для future re-test
- sign test + Wilcoxon на обоих выборках
"""
import json
from pathlib import Path
from datetime import datetime
import numpy as np

OUT = Path('/home/z/my-project/upload/scripts_persistent/STAGE2_PROTOCOL.md')


def main():
    protocol = """# Stage 2 — Protocol for source equalization

**Дата:** 2026-09-24
**Контекст:** Уравнивание источников ДО сшивки. Совместное МНК для b_j
по overlaps между источниками.

## 1. Что сохранять на каждой итерации

### Обязательно (для будущих re-test):

| Файл | Содержание | Размер |
|---|---|---|
| `stage2_iter_{N}_passport_dH.csv` | per-point dH на 40 holdout | ~5 KB |
| `stage2_iter_{N}_passport_train_dH.csv` | per-point dH на 166 train (для диагностики) | ~10 KB |
| `stage2_iter_{N}_icesat_dH.csv` | per-point dZ на 43K ICESat | ~3 MB |
| `stage2_iter_{N}_source_biases.json` | оценённые b_j с CI | <1 KB |
| `stage2_iter_{N}_metrics.json` | MAD/RMSE/median по классам | <5 KB |

### Если правка по lib_ground (cap_far_field, accept_dsm_with_relief и т.п.):
- Сохранять **ДО и ПОСЛЕ** правки на тех же точках
- Названия: `stage2_iter_{N}_before_*.csv`, `stage2_iter_{N}_after_*.csv`

### Урок от v9 (cap_far_field):
- CSV не сохранён → 5.23 не смог перепроверить
- Затраты: 30 мин перезапуска pipeline (который сейчас бесполезен)
- **CSV сохраняется ВСЕГДА, даже если правка не прошла**

## 2. Тесты для приёмки итерации

### На passport (n=40):
1. **Sign test** (binomial): threshold 27/40 → p<0.05
2. **Wilcoxon signed-rank** (robust to outliers, RMSE/MAD ~10)
3. **Per-point median bootstrap** |dH_before|-|dH_after|, 95% CI

### На ICESat (n=37K paired):
1. **Sign test**: threshold 50.5% better → p<0.05
2. **Wilcoxon signed-rank**
3. **Per-point median bootstrap** |dZ_before|-|dZ_after|

### Совмещённый вердикт:
- **Принимается**: ≥2/3 тестов значимы на BOTH выборках
- **Отвергается**: ≥2/3 тестов значимо WORSE на EITHER выборке
- **Marginal**: 1/3 тестов значим → обсуждение

## 3. Порядок итераций Stage 2

### Итерация 1: Baseline без правок
- Запустить pipeline (v9b) БЕЗ изменений
- Сохранить per-point dH (passport) и per-point dZ (ICESat)
- Это baseline для всех будущих сравнений

### Итерация 2: Source biases b_j
- Оценить b_j через overlaps:
  - ICESat × DSM (43K пар в радиусе 5м)
  - ICESat × Паспорт (~150 пар в радиусе 50м)
  - Паспорт × DSM (202 пары)
  - Copernicus × ICESat (43K)
  - Copernicus × Паспорт (202)
- Применить `z_j_corrected = z_j - b_j` ко всем источникам
- Запустить pipeline
- Сравнить с baseline (итерация 1)

### Итерация 3: OSM building heights как 5-й источник
- Добавить OSM heights (σ≈2м) как weighted anchor
- Coverage ~20% площади СПб
- Ожидание: улучшение ~3% на city-wide (по coverage)
- На ICESat можно проверить точнее

### Итерация 4 (если 2-3 прошли): Sentinel-2 SCL water
- Заменить `cop<0.5` маску на SCL class 6 (water)
- SCL ~270 км² vs cop<0.5 425 км² vs truth 329 км²
- Должно приблизить зеркало к истине

### Итерация 5 (если 4 прошла): NDVI на буфер зданий
- Деревья у фасада мешают sampling h_roof
- NDVI на буфер вокруг здания, не внутрь

### Итерация 6 (если 5 прошла): per-district floor_h
- На 4K OSM зданий с height tag (не levels)
- Кластеризация по x,y → per-district floor_h
- Уменьшит σ_source для OSM heights

## 4. Когда STOP

- Все 3 теста (sign + Wilcoxon + bootstrap) на BOTH выборках дают NOT SIGNIFICANT на 3 итерациях подряд
- Или если итерация показала SIGNIFICANT WORSE — откатить к предыдущей

## 5. Что НЕ делать

- Не выносить вердикт на основе MAD-разности (слабый тест)
- Не сравнивать источники в одиночку с моделью (нужен paired test)
- Не использовать σ_s, подобранный по test (только LOO)
- Не забывать сохранять per-point dH ДО правки
- Не применять глобальные правки к полю после сшивки (см. секцию A)

## 6. Как воспроизвести re-test (после сброса сессии)

```python
# scripts/17_stage2_validate_iter.py
import pandas as pd
from scipy import stats

def validate_iter(N, prev_N=None):
    if prev_N is None:
        prev_N = N - 1
    # Load per-point dH
    iter_dH = pd.read_csv(f'data/stage2_iter_{N}_passport_dH.csv')
    prev_dH = pd.read_csv(f'data/stage2_iter_{prev_N}_passport_dH.csv')
    # Merge by (lat, lon)
    merged = iter_dH.merge(prev_dH, on=['lat', 'lon'], suffixes=('_new', '_prev'))
    # Sign test
    better = (merged.dH_new.abs() < merged.dH_prev.abs()).sum()
    worse = (merged.dH_new.abs() > merged.dH_prev.abs()).sum()
    p_sign = stats.binomtest(better, better+worse, 0.5).pvalue
    # Wilcoxon
    diff = merged.dH_prev.abs() - merged.dH_new.abs()
    diff = diff[diff != 0]
    w_stat, p_wilc = stats.wilcoxon(diff)
    # ICESat
    iter_ice = pd.read_csv(f'data/stage2_iter_{N}_icesat_dH.csv')
    prev_ice = pd.read_csv(f'data/stage2_iter_{prev_N}_icesat_dH.csv')
    # ... same tests on ICESat
    return {
        'passport_sign_test_p': p_sign,
        'passport_wilcoxon_p': p_wilc,
        # ...
    }
```

## 7. Резюме: что проверено к этому моменту

| Версия | passport (n=40) | ICESat (n=37K) |
|---|---|---|
| v7 (no passport train) | baseline | n/a (no surface saved) |
| v8 (passport train) | **SIGNIFICANT BETTER vs v7** (Wilcoxon p=0.007) | n/a |
| v9b (choose_block_px) | MARGINAL WORSE vs v8 (p=0.035 sign, p=0.080 Wilc) | n/a |
| v11 (accept_dsm_w_relief) | **SIGNIFICANT WORSE vs v8** (Wilcoxon p=0.028) | n/a |
| v9 (cap_far_field) | CANNOT TEST (no CSV) | n/a |
| **current** (spb_ground_surface_2m.tif) | n/a | **SIGNIFICANT BETTER vs raw ArcticDEM** (p<1e-100) |

## 8. Что ICESat уже показал

На 37K paired points (vs 40 passport):
- Sign test: 22 499 better (60.6%), p ≈ 0
- Wilcoxon: p = 7.96e-100, median improvement +0.080m
- 95% CI [+0.078, +0.086], width 0.008m (vs 1.0m на passport)
- Power: 34× more sensitive

Это значит: текущий pipeline (v8/v9b/v11) действительно лучше v4-baseline
на 43K ICESat точек с высокой статистической значимостью.

## 9. Что делать СЕЙЧАС

1. Запустить pipeline с сохранением per-point dH на ВСЕХ выборках (passport + ICESat)
2. Это даст baseline (итерация 1) для Stage 2
3. Реализовать сбор overlaps (STAGE2_DESIGN.md, шаг 1)
4. Запустить итерацию 2 (source biases b_j)
5. Сравнить с итерацией 1 по протоколу выше

## 10. Технические заметки

- Сохранять CSV в `scripts/data/stage2_iter_{N}_*.csv` (git tracked)
- После каждой итерации: commit + push to GitHub
- Drive upload через `upload_to_gdrive_v2.py`
- Worklog append: `Task ID: 5.25, Agent: main, ...`
"""
    OUT.write_text(protocol)
    print(f'Wrote → {OUT}')
    print(f'Size: {OUT.stat().st_size} bytes')


if __name__ == '__main__':
    main()
