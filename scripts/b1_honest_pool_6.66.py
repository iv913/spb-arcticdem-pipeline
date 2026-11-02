"""B1 (handoff 2026-11) — пометки _INVALIDATED и честный независимый пул.

Пункты G и C ветки audit-remediations-A-I (NOTES_branch_audit_remediations §1,
NOTES_repo_base_5_67_review §3–4):

  G. агрегат ICP DWG помечен _INVALIDATED (ложный минимум у Ломоносова),
     v3-агрегат тоже;
  C. Отметки.txt выведен из независимого пула (в 5.67 было 1080 → 322);
     здесь: summary-файлы получают пометки, честный пул пересобран в
     independent_pool_honest_6.66.csv (без DWG ICP).

Файлы помечаются, не переписываются: неверные числа остаются рядом с
вердиктом (конвенция 5.67: `_wrong`/`_INVALIDATED` как контрпример).
Писать может только агент B-линии: пул валидации — владение агента B.
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

GH = Path('/tmp/my-project/gh-clone')
DATA = GH / 'scripts' / 'data'
OUT_MD = DATA / 'POOL_INVALIDATION_6.66.md'
OUT_CSV = DATA / 'independent_pool_honest_6.66.csv'

MARK_META = {
    "date": "2026-11-02",
    "by": "B1 handoff 2026-11 (NOTES_branch_audit_remediations §1-G/C; "
          "NOTES_repo_base_5_67_review §3-4)",
}

DROP_SOURCES = {"DWG ICP"}  # ложный минимум ICP, невалидируемо

NOTES_BY_SOURCE = {
    "archi.ru Yandex-expanded":
        "независим, но геопривязка по адресу — слабое звено: MAD 1.29 против "
        "0.42 на обработанном листе 514913 (ревизия 5.67 §5)",
    "archi.ru 514913": "независим, лучший наземный источник (MAD 0.42)",
    "atayanc V.O.": "независим (одиночное чтение эпохи 5.x); для пула v9 "
                    "заменён atayanc_pool_v2 (два чтения, use=ground_truth)",
    "456577 Римского-Корсакова": "независим",
    "Passport holdout": "независим (holdout паспорта, вне якорей)",
}


def mark_json(path: Path, mark: dict) -> bool:
    """Добавить ключ _INVALIDATED в JSON (если ещё нет)."""
    p = GH / path
    d = json.loads(p.read_text(encoding='utf-8'))
    if '_INVALIDATED' in d:
        print(f"  уже помечен: {path}")
        return False
    d['_INVALIDATED'] = mark
    p.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f"  помечен: {path}")
    return True


def main() -> None:
    # 1) v3-агрегат (n=4450, wilcoxon 1.5e-79 — на ICP-подгонке по высоте)
    mark_json('scripts/data/FINAL_CONSOLIDATED_v3_summary.json', {
        **MARK_META,
        "reason": "агрегат n_total=4450 построен на DWG ICP — подгонке съёмок "
                  "по высоте, сошедшейся в ложный минимум у Ломоносова "
                  "(59.855-59.884 / 29.92-29.97, все 5 съёмок в квадрате "
                  "2.5x2.5 км; остаток совмещения контуров 10-19 м; "
                  "crs_fn=-6.19e6 — свободный параметр; все знаки dH "
                  "одинаковы). wilcoxon_p=1.5e-79 — подпись подгонки, "
                  "не согласия. Валидировано ноль отметок.",
        "superseded_by": "scripts/data/independent_pool_honest_6.66.csv",
    })

    # 2) v4-сводка: key_findings + aggregate_independent
    mark_json('scripts/data/FINAL_CONSOLIDATED_v4_summary.json', {
        **MARK_META,
        "key_findings": {
            "regional_bias_pattern": "INVALIDATED — адрес ложного минимума "
                "ICP (Ломоносов, квадрат 2.5 км), а не юг СПб; съёмки по "
                "описанию из разных районов (Охта Парк, Кронштадт, Токсово, "
                "Лигово/Ульянка) и не могут лежать в одном квадрате",
            "circular_method_gap": "INVALIDATED — обе «величины» суть "
                "артефакты ICP-подгонки, не измерения",
            "best_independent_correlation": "INVALIDATED — Отметки.txt "
                "циркулярен: ~166/202 марок лежат в опоре паспорта; "
                "r=0.952 держится диапазоном высот 37 м",
            "central_SPb_MAD": "INVALIDATED — географическое подмножество "
                "циркулярного источника, на утечку не отвечает",
            "icp_dwg_results": "INVALIDATED — 4275 отметок DWG, "
                "валидировано ноль, съёмки остались непривязанными",
        },
        "aggregate_independent": "INVALIDATED — включает DWG ICP (ложный "
            "минимум) и завышен циркулярными источниками; честная замена — "
            "independent_pool_honest_6.66.csv (DWG ICP исключён)",
    })

    # 3) summaries Отметки.txt — снять клеймо «INDEPENDENT»
    for f in ('scripts/data/otmetki_independent_summary.json',
              'scripts/data/otmetki_stratified_summary.json'):
        mark_json(f, {
            **MARK_META,
            "reason": "Отметки.txt циркулярен: тот же файл паспорта, "
                "~166/202 марок внутри опоры; MAD 0.99 — утечка, не "
                "точность; r=0.952 держится диапазоном высот. Из "
                "независимого пула исключён (B1-C); independent=false.",
        })

    # 4) честный независимый пул: ALL_independent_points.csv минус DWG ICP
    rows = list(csv.DictReader(open(DATA / 'ALL_independent_points.csv')))
    kept, dropped = [], []
    for r in rows:
        (dropped if r['source'] in DROP_SOURCES else kept).append(r)
    fields = ['source', 'lat', 'lon', 'z_survey', 'dH', 'abs_dH',
              'independent', 'note']
    with open(OUT_CSV, 'w', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for r in kept:
            w.writerow({**{k: r.get(k, '') for k in fields[:6]},
                        'independent': 'true',
                        'note': NOTES_BY_SOURCE.get(r['source'], '')})
    print(f"  пул: {OUT_CSV.name}: {len(kept)} строк "
          f"(исключено {len(dropped)}: {', '.join(sorted(DROP_SOURCES))})")

    # 5) агрегат по источникам + MD-маркер
    agg = defaultdict(lambda: [0, []])
    for r in kept:
        agg[r['source']][0] += 1
        agg[r['source']][1].append(abs(float(r['abs_dH'])))
    lines = [
        "# Инвалидация источников и честный независимый пул (B1, 6.66)",
        "",
        "Дата: 2026-11-02. Основания: NOTES_branch_audit_remediations §1-G/C,",
        "NOTES_repo_base_5_67_review §3-4. Файлы помечены, не переписаны.",
        "",
        "## Пометки _INVALIDATED",
        "",
        "| файл | что помечено |",
        "|---|---|",
        "| FINAL_CONSOLIDATED_v3_summary.json | весь агрегат n=4450 "
        "(ICP DWG, ложный минимум у Ломоносова; wilcoxon 1.5e-79) |",
        "| FINAL_CONSOLIDATED_v4_summary.json | key_findings "
        "(+5..9 м систематика, circular_method_gap, r=0.952 Отметки, "
        "icp_dwg_results) и aggregate_independent |",
        "| otmetki_independent_summary.json | клеймо INDEPENDENT снято "
        "(циркулярен, ~166/202 в опоре паспорта) |",
        "| otmetki_stratified_summary.json | то же |",
        "",
        "Отметки.txt в ALL_independent_points.csv отсутствует (в эту выгрузку",
        "не попал); пометки расставлены в его summary-файлах. Файл "
        "Отметки.txt в пул v9 не входит.",
        "",
        "## Честный независимый пул (земля): independent_pool_honest_6.66.csv",
        "",
        "| источник | n | median |dH|, м | примечание |",
        "|---|---|---|---|",
    ]
    total = 0
    for src, (n, vals) in sorted(agg.items(), key=lambda kv: -kv[1][0]):
        vals.sort()
        med = vals[len(vals) // 2]
        total += n
        lines.append(f"| {src} | {n} | {med:.2f} | {NOTES_BY_SOURCE.get(src, '')} |")
    lines += [
        f"| **итого** | **{total}** | | |",
        "",
        f"Исключён DWG ICP: {len(dropped)} строк (ложный минимум ICP, "
        "невалидируемо). Отметки.txt в этой выгрузке отсутствовал; в терминах",
        "5.67 честный пул 322 = 226 (archi 514913 + паспорт holdout + ICESat",
        "по RGT) + 96 («Тихая гавань», archi 95211) — 96 отметок пока не "
        "воспроизведены: archi.ru недоступен из среды, см.",
        "docs/tihaya_gavan_95211_blocked_6.66.md. Пул v9 (B4) собирается из",
        "этого файла + atayanc_pool_v2 + ICESat по трекам.",
        "",
    ]
    OUT_MD.write_text("\n".join(lines), encoding='utf-8')
    print(f"  маркер: {OUT_MD.name}")


if __name__ == '__main__':
    main()
