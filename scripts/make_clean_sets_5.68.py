#!/usr/bin/env python3
"""
5.68-C: Канонический фильтрованный ICESat-2 набор + пересчёт всех метрик
после исключения аномальных якорей archi-набора.

Выходы:
  scripts/data/icesat2_clean.csv        (|dH|<=3м, наземный фон)
  scripts/data/archi_final_clean_v2.csv (1168 - 37 исключённых = 1131)
  results/anomaly_recheck_5.68_summary.json
"""
import json
import numpy as np
import pandas as pd

# --- 1) ICESat-2 clean ---
ic = pd.read_csv("scripts/data/icesat2_corrected.csv")
clean = ic[ic.abs_dH_corrected <= 3.0].copy()
clean.to_csv("scripts/data/icesat2_clean.csv", index=False)
print(f"ICESat clean: {len(clean)} из {len(ic)} "
      f"(MAD {clean.abs_dH_corrected.median():.2f}, "
      f"med dH {clean.dH_corrected.median():+.3f}, "
      f"r {np.corrcoef(clean.h_icesat_baltic, clean.h_model)[0,1]:.4f})")

# Зонные метрики по чистому набору
zones = {
    "Кронштадт": (59.95, 60.02, 29.66, 29.83),
    "Курортный (Сестрорецк-Зеленогорск)": (60.09, 60.31, 29.55, 30.20),
    "Пушкинский": (59.60, 59.80, 30.05, 30.47),
    "Колпинский": (59.66, 59.79, 30.45, 30.72),
    "Петродворцовый": (59.80, 59.98, 29.85, 30.15),
    "Кировский (юг)": (59.75, 59.87, 30.10, 30.33),
    "Красносельский": (59.74, 59.89, 29.95, 30.29),
    "Фрунзенский (Купчино)": (59.77, 59.90, 30.30, 30.60),
    "Невский (восток)": (59.80, 59.94, 30.38, 30.75),
    "Приморский": (59.95, 60.11, 30.13, 30.46),
    "Василеостровский": (59.90, 60.01, 29.95, 30.32),
    "Центральный": (59.88, 60.02, 30.26, 30.45),
}
zone_stats = {}
for z, (la0, la1, lo0, lo1) in zones.items():
    s = clean[(clean.lat >= la0) & (clean.lat <= la1) &
              (clean.lon >= lo0) & (clean.lon <= lo1)]
    if len(s) == 0:
        zone_stats[z] = None
        continue
    zone_stats[z] = dict(n=int(len(s)),
                         mad=round(float(s.abs_dH_corrected.median()), 3),
                         med_dH=round(float(s.dH_corrected.median()), 3))
for z, v in zone_stats.items():
    print(f"  {z:38s} {v}")

# --- 2) archi canon после исключений ---
EXCLUDE = {
    "АНО Футбольный клуб «Коломяги»": "геокод улицы без дома, рельеф холмистый (17.4 vs план 0.7-8.0)",
    "Многофункциональный комплекс на территории Электростанции N2": "отметки негрунтовые (0-36.73, этажи/конструкции)",
    "Деловой центр": "отметки негрунтовые (14.85-25.2); модель подтверждена ICESat (bias +0.5)",
    "БЦ на Поклонной горе": "n=2, трек в 0.77 км — статистически незначимо",
}
archi = pd.read_csv("scripts/data/archi_final_expanded_v2.csv")
mask_excl = archi.project_name.isin(EXCLUDE)
out = archi[~mask_excl].copy()
out.to_csv("scripts/data/archi_final_clean_v2.csv", index=False)
mad = out.abs_dH.median()
med = out.dH.median()
r = np.corrcoef(out.h_model, [float(v) for v in out.value])[0, 1]
print(f"\narchi canon: {len(out)} из {len(archi)} (исключено {mask_excl.sum()})")
print(f"MAD={mad:.2f}, med dH={med:+.2f}, r={r:.3f}")

# ID Moskovskiy — помечен site-level (остаётся)
n_site = int((out.project_name == "ID Moskovskiy").sum())
print(f"ID Moskovskiy: {n_site} отм. оставлены с флагом site-level")

summary = dict(
    icesat_clean_n=int(len(clean)),
    icesat_clean_mad=round(float(clean.abs_dH_corrected.median()), 3),
    icesat_clean_med_dH=round(float(clean.dH_corrected.median()), 4),
    zones=zone_stats,
    archi_excluded={k: v for k, v in EXCLUDE.items()},
    archi_excluded_n=int(mask_excl.sum()),
    archi_clean_n=int(len(out)),
    archi_clean_mad=round(float(mad), 3),
    archi_clean_med_dH=round(float(med), 3),
    archi_clean_r=round(float(r), 4),
    site_level_flag=dict(ID_Moskovskiy_n=n_site,
                         reason="адрес «Московский пр. 72» без подтверждения площадки"),
    grand_total=38602,
)
json.dump(summary, open("results/anomaly_recheck_5.68_summary.json", "w"),
          ensure_ascii=False, indent=1)
print("\nOK: results/anomaly_recheck_5.68_summary.json")
