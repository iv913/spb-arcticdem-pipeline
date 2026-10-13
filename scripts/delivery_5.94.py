#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""5.94 — Пакет поставки продукта в НСПД/КГА (выходная сторона этапа).

Основание: рекомендация 5.91 «поставлять юг с маской mclf_v3 (ok+gold)».
Собирает download/delivery_nspd_5.94/: README.md + manifest.json
(файлы, размеры, sha256, метрики канона v4 / гейта 5.92 / районов юга
5.93). Сама подача невозможна из внешней сети (см.
results/sources_audit_5.94.json) — пакет готовит поставку на стороне,
имеющей выход в сеть РФ.

Запуск из корня репозитория: python3 scripts/delivery_5.94.py
"""
import datetime as dt
import hashlib
import json
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTDIR = os.path.join(REPO, "download", "delivery_nspd_5.94")

PRODUCTS = [
    ("download/mosaic_5.90/spb_hybrid_2m_mosaic_5.90.tif",
     "Гибридная 2м ЦМР/ЦМС СПб (84 зоны; север = мозаика 5.84, юг = пересборка 5.90), Int16, БС-77", True),
    ("download/mosaic_5.90/spb_quality_5.90.tif",
     "Каналы качества (n_cov / sigma_cm / canopy30_cm) к мозаике 5.90 (can30 патч 5.92 применён)", True),
    ("download/mclf_v3/mclf_v3_mask_5.90.tif",
     "Маска M-CLF-v3 (uint8: 0 excl / 1 rest / 2 ok / 3 gold), пороги 5.91 + патч 5.92", True),
    ("download/mclf_v3/mclf_v3_dem_5.90.tif",
     "Clean-DEM (высоты только в классах ok+gold), Int16, БС-77", True),
    ("download/mosaic_5.90/trust_mask_2m_5.90.tif",
     "Маска плотности (trust) 2м", False),
    ("scripts/data/icesat2_canon_v4_5.93.csv.gz",
     "Валидационный канон v4 (ATL06 HIGH+MEDIUM, cnf_src-флаг)", False),
]


def sha256(path, block=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(block)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def load_json(rel):
    p = os.path.join(REPO, rel)
    if not os.path.exists(p):
        return None
    with open(p) as f:
        return json.load(f)


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    dens = load_json("results/densify_5.93.json") or {}
    gate = load_json("results/gate_v3_5.91.json") or {}
    patch = load_json("results/mclf_v3_patch_5.92.json") or {}
    audit = load_json("results/sources_audit_5.94.json") or {}

    canon_v4 = dens.get("canon", {})
    holdout = dens.get("holdout_tiers", {})
    dist_s = {k: v.get("gold") for k, v in (dens.get("districts_south") or {}).items()}
    cls92 = patch.get("cls_global_92") or (gate.get("raster") or {}).get("classes")

    files = []
    for rel, desc, big in PRODUCTS:
        p = os.path.join(REPO, rel)
        if not os.path.exists(p):
            files.append({"path": rel, "error": "NOT FOUND"})
            continue
        entry = {"path": rel, "bytes": os.path.getsize(p), "desc": desc}
        if os.path.getsize(p) < 300 * (1 << 20):
            entry["sha256"] = sha256(p)
        else:
            entry["sha256_note"] = "файл >300MB: sha256 вычисляется на стороне поставки ( delivery вычисляет при выгрузке)"
            entry["sha256"] = sha256(p)
        files.append(entry)

    manifest = {
        "package": "spb-2m-hybrid-delivery",
        "stage": "5.94",
        "date": str(dt.date.today()),
        "crs": "EPSG:4326 (пиксельная сетка), высоты: Балтийская система 1977 (БС-77)",
        "datum_fix": "§6: δ(Балтийская)=+16.04 м применён к ICESat-2 (h_baltic); растровые высоты уже в БС-77",
        "gate_policy": "юг поставляется классами ok+gold (порог GOLD(S) can30<50, 5.91); лесная полоса [50;200) — rest (кэп MAD≤0.45 не пройден)",
        "products": files,
        "metrics": {
            "canon_v4": {
                "base": canon_v4.get("v4_base"), "dsm": canon_v4.get("v4_dsm"),
                "cells_0.001deg": canon_v4.get("v4_cells"),
                "high_base_mad": (dens.get("mad") or {}).get("high_base"),
                "med_base_mad": (dens.get("mad") or {}).get("med_base"),
            },
            "mask_v3_classes_px": cls92,
            "districts_south_gold": dist_s,
            "holdout_tiers": holdout,
        },
        "validation_summary": {
            "north_all_mad": ((gate.get("canon") or {}).get("north") or {}).get("all", {}).get("mad"),
            "south_all_mad": ((gate.get("canon") or {}).get("south") or {}).get("all", {}).get("mad"),
            "note": "детально: results/densify_5.93.md, results/gate_v3_5.91.md, results/mclf_v3_patch_5.92.md",
        },
        "ingest_status": audit.get("summary"),
        "ingest_note": "приём данных НСПД/КГА (обратная связь, выкопировки) требует сети РФ — results/sources_audit_5.94.json",
    }
    mp = os.path.join(OUTDIR, "manifest.json")
    with open(mp, "w") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)

    dsg = dist_s or {}
    readme = f"""# Поставка в НСПД/КГА — гибридная 2м ЦМР/ЦМС Санкт-Петербурга

Стадия: 5.94, дата: {dt.date.today()}. Пакет подготовлен по рекомендации
этапа 5.91 («поставлять юг с маской mclf_v3 ok+gold»).

## Состав (детали и sha256 — в manifest.json)

| Продукт | Описание |
|---|---|
| spb_hybrid_2m_mosaic_5.90.tif | Гибридная 2м ЦМР/ЦМС, 84 зоны, Int16, БС-77 |
| spb_quality_5.90.tif | n_cov / sigma_cm / canopy30_cm (патч can30 5.92) |
| mclf_v3_mask_5.90.tif | Пер-пиксельный гейт качества: 0 excl / 1 rest / 2 ok / 3 gold |
| mclf_v3_dem_5.90.tif | Clean-DEM: высоты только в ok+gold |
| trust_mask_2m_5.90.tif | Маска плотности (trust) |
| icesat2_canon_v4_5.93.csv.gz | Валидационный канон v4 (ATL06 HIGH+MEDIUM) |

## Ключевые метрики

- Канон v4: base {canon_v4.get('v4_base')} точек ({canon_v4.get('v4_cells')} ячеек 0.001°), DSM-кроны {canon_v4.get('v4_dsm')};
  MAD high-base {(dens.get('mad') or {}).get('high_base')} м, med-base {(dens.get('mad') or {}).get('med_base')} м.
- Классы маски (px): gold {((cls92 or {}).get('gold')):,} / ok {((cls92 or {}).get('ok')):,} / rest {((cls92 or {}).get('rest')):,} / excl {((cls92 or {}).get('excl')):,}.
- Южные районы (gold, MAD): {', '.join(f"{k} {v['mad']}" for k, v in dsg.items() if v)}.
- Датум: высоты в БС-77; для сопоставления с ICESat-2 — δ=+16.04 м (§6).

## Политика качества поставки (юг)

Южные prior-only зоны поставляются классами ok+gold (GOLD(S): canopy30<50 см).
Лесная полоса canopy30∈[50;200) — класс rest: кэп MAD≤0.45 не пройден (5.91),
поставке не подлежит до пересъёмки/досъёмки.

## Обратный канал (выкопировки)

Приём/выгрузка через НСПД/ПКК/ИСОГД требует выхода из сети РФ
(aудит 2026-10-13: {json.dumps(audit.get('summary') or {{}}, ensure_ascii=False)}).
Протокол выкопировок готов: scripts/nspd_fetch_5.94.py --plan.
"""
    rp = os.path.join(OUTDIR, "README.md")
    with open(rp, "w") as f:
        f.write(readme)

    print(f"[5.94] пакет: {OUTDIR}")
    for fn in sorted(os.listdir(OUTDIR)):
        p = os.path.join(OUTDIR, fn)
        print(f"  {fn} ({os.path.getsize(p)} B)")
    bad = [f for f in files if "error" in f]
    print(f"продуктов: {len(files)}, не найдено: {len(bad)}")
    if bad:
        for b in bad:
            print("  MISSING:", b["path"])
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
