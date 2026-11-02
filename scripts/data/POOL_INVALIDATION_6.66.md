# Инвалидация источников и честный независимый пул (B1, 6.66)

Дата: 2026-11-02. Основания: NOTES_branch_audit_remediations §1-G/C,
NOTES_repo_base_5_67_review §3-4. Файлы помечены, не переписаны.

## Пометки _INVALIDATED

| файл | что помечено |
|---|---|
| FINAL_CONSOLIDATED_v3_summary.json | весь агрегат n=4450 (ICP DWG, ложный минимум у Ломоносова; wilcoxon 1.5e-79) |
| FINAL_CONSOLIDATED_v4_summary.json | key_findings (+5..9 м систематика, circular_method_gap, r=0.952 Отметки, icp_dwg_results) и aggregate_independent |
| otmetki_independent_summary.json | клеймо INDEPENDENT снято (циркулярен, ~166/202 в опоре паспорта) |
| otmetki_stratified_summary.json | то же |

Отметки.txt в ALL_independent_points.csv отсутствует (в эту выгрузку
не попал); пометки расставлены в его summary-файлах. Файл Отметки.txt в пул v9 не входит.

## Честный независимый пул (земля): independent_pool_honest_6.66.csv

| источник | n | median |dH|, м | примечание |
|---|---|---|---|
| archi.ru Yandex-expanded | 476 | 0.99 | независим, но геопривязка по адресу — слабое звено: MAD 1.29 против 0.42 на обработанном листе 514913 (ревизия 5.67 §5) |
| archi.ru 514913 | 175 | 0.42 | независим, лучший наземный источник (MAD 0.42) |
| atayanc V.O. | 94 | 1.14 | независим (одиночное чтение эпохи 5.x); для пула v9 заменён atayanc_pool_v2 (два чтения, use=ground_truth) |
| 456577 Римского-Корсакова | 61 | 1.08 | независим |
| Passport holdout | 40 | 1.18 | независим (holdout паспорта, вне якорей) |
| **итого** | **846** | | |

Исключён DWG ICP: 470 строк (ложный минимум ICP, невалидируемо). Отметки.txt в этой выгрузке отсутствовал; в терминах
5.67 честный пул 322 = 226 (archi 514913 + паспорт holdout + ICESat
по RGT) + 96 («Тихая гавань», archi 95211) — 96 отметок пока не воспроизведены: archi.ru недоступен из среды, см.
docs/tihaya_gavan_95211_blocked_6.66.md. Пул v9 (B4) собирается из
этого файла + atayanc_pool_v2 + ICESat по трекам.
