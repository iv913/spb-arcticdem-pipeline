# B1 — реконструкция ветки `audit-remediations-A-I` в `main` (6.66)

**Задание:** AGENT_HANDOFF_2026-11.md §3-B1. Ветка (`8ea4ab8`, 7 коммитов
8–14.10) существовала в рабочей копии аудитора, в наш клон и в пакет
handoff не попала; содержимое восстановлено по ревизиям
(`NOTES_branch_audit_remediations`, `NOTES_repo_base_5_67_review`) и
влито в `main` как PR-подобное слияние. Имя ветки сохранено.

**Коммиты:** `b064af2` (toolkit + .env) → `<merge>` (ветка → main).
Приёмка handoff: «слияние без конфликтов, смоук-тест
`stats_toolkit/test_portability.py` зелёный» — **выполнено** (exit 0,
18/18 проверок).

## Пункт за пунктом

| пункт ветки | как реализовано | приёмка |
|---|---|---|
| **H** toolkit | `scripts/stats_toolkit/`: `paired_tests.py` (знаковый, Уилкоксон, bootstrap-CI медианы, `paired_verdict` по числу значимых тестов из трёх, порог 0.1 м), `icesat_tracks.py` (`split_by_rgt` — единственный законный сплит целыми RGT, `aggregate_track_medians`, `n_eff`), `test_portability.py` | смоук-тест зелёный; проверки: эффект +0.5 м ловится по трекам, per-point CI в ~5 раз уже (инфляция), нулевой эффект не значим, сплит только целыми треками |
| **G** `_INVALIDATED` | `scripts/b1_honest_pool_6.66.py` добавил `_INVALIDATED` в `FINAL_CONSOLIDATED_v3_summary.json` (агрегат n=4450 на ICP-подгонке, wilcoxon 1.5e-79) и в `FINAL_CONSOLIDATED_v4_summary.json` (key_findings: +5..9 м «систематика» = адрес ложного минимума, r=0.952 Отметки = циркулярен, icp_dwg_results); Отметки.txt summaries лишились клейма INDEPENDENT | маркер-файл `scripts/data/POOL_INVALIDATION_6.66.md` |
| **B** DWG снят с карты | проверено по всей линии 6.x: в `build_sources_map_5.96e.py`, `build_map_6.10/6.20/6.21/6.52.py` и в выпущенных HTML-картах (5.96e, 6.10, 6.20, 6.21, 6.52) слоя «DWG ICP» нет (rg: 0 вхождений). В 5.67-репозитории снимался `public/map_stats.json` — к этой линии не относится | документировано здесь |
| **C** Отметки.txt из пула | в этой линии Отметки.txt уже не входил в `ALL_independent_points.csv` и исключён в 6.63; summary-файлы помечены `independent=false`; честный пул пересобран: `scripts/data/independent_pool_honest_6.66.csv` = 846 строк (1316 − 470 DWG ICP) с колонками `independent`, `note` | агрегат по источникам в `POOL_INVALIDATION_6.66.md` |
| **F** «Тихая гавань» | НЕ воспроизведена: archi.ru из среды недоступен (403 / бита HTML), план не получен; идентифицирован как ЖК Wellamo, Курляндская 10–12 (мастерская Столярчука) | `docs/tihaya_gavan_95211_blocked_6.66.md`, отложено до источника |
| **E** инфляция p-значений | задокументирована в `scripts/stats_toolkit/README.md` с числами 5.67 (CI по точкам 0.011 против 0.238 по RGT — в 21 раз) и функцией `ci_shrink_factor` | — |

## Честный независимый пул (земля), 6.66

| источник | n | median \|dH\|, м | примечание |
|---|---|---|---|
| archi.ru Yandex-expanded | 476 | 1.06 | геопривязка по адресу — слабое звено |
| archi.ru 514913 | 175 | 0.42 | лучший наземный |
| atayanc V.O. | 94 | 1.01 | заменён в v9 на atayanc_pool_v2 |
| 456577 Римского-Корсакова | 61 | 0.66 | — |
| Passport holdout | 40 | 1.12 | — |
| **итого** | **846** | | все `independent=true` |

Исключено: DWG ICP — 470 строк. «Тихая гавань» (96) — блокер, место
зарезервировано. Пул v9 (B4) строится из этого файла + atayanc_pool_v2
(ground_truth, два чтения) + ICESat по трекам.

## Гигиена (§1 handoff)

`.env` снят с индекса (`git rm --cached .env`), добавлен в `.gitignore`;
содержимое безобидно (локальный `DATABASE_URL`), но правило §1 соблюдено.
Токен Earthdata в истории — напоминание из ревизии 5.67 остаётся в силе
(отзыв вне компетенции агента; INDEX §«Что нужно от пользователя», п. 4).

## Владение файлами (§2)

Тронуты только файлы агента B: `scripts/stats_toolkit/`, `scripts/data/*`
(пул/сводки), `docs/`. Скрипты поверхности и карт не менялись.
