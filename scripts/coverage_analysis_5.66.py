#!/usr/bin/env python3
"""
5.66-A: Анализ пространственного покрытия независимого валидационного набора.
Цель: количественно выделить зоны СПб со слабой валидацией (мало/нет точек).

Набор: scripts/data/archi_final_expanded_v2.csv (1168 отметок, 33 проекта СПб).
Метод:
  1) Привязка каждой точки к районам СПб по полигональным bbox (приближённые,
     консервативные, но достаточные для группировки точек).
  2) Сетка 2 км x 2 км (0.02° x 0.0127° ~ 2 км) — подсчёт отметок по ячейкам.
  3) Список «слабых» зон: районы с n < порога или плотность < порога.
Результат: results/validation_coverage_5.66.md + scripts/data/coverage_by_cell_5.66.csv
"""
import csv
import json
import math
from collections import Counter, defaultdict

CSV = "scripts/data/archi_final_expanded_v2.csv"

# Приближённые bbox районов СПб (lat_min, lat_max, lon_min, lon_max).
# Границы консервативны: точки между bbox попадают в «межрайон» и проверяются вручную.
DISTRICTS = {
    "Кронштадтский":        (59.95, 60.02, 29.66, 29.83),
    "Курортный":            (60.09, 60.31, 29.55, 30.20),
    "Приморский":           (59.95, 60.11, 30.13, 30.46),
    "Выборгский":           (59.95, 60.08, 30.20, 30.46),
    "Калининский":          (59.94, 60.07, 30.26, 30.52),
    "Петроградский":        (59.94, 60.03, 30.19, 30.35),
    "Центральный":          (59.88, 60.02, 30.26, 30.45),
    "Василеостровский":     (59.90, 60.01, 29.95, 30.32),
    "Адмиралтейский":       (59.84, 59.95, 30.19, 30.37),
    "Московский":           (59.77, 59.92, 30.20, 30.44),
    "Фрунзенский":          (59.77, 59.92, 30.34, 30.62),
    "Невский":              (59.80, 59.94, 30.38, 30.75),
    "Красногвардейский":    (59.87, 60.02, 30.38, 30.65),
    "Красносельский":       (59.74, 59.89, 29.95, 30.29),
    "Кировский":            (59.75, 59.91, 30.10, 30.33),
    "Колпинский":           (59.66, 59.79, 30.45, 30.72),
    "Пушкинский":           (59.60, 59.80, 30.05, 30.47),
    "Петродворцовый":       (59.80, 59.98, 29.85, 30.15),
    "Ломоносов (ЛО)":       (59.88, 59.93, 29.72, 29.83),
}

def district_of(lat, lon):
    for name, (la0, la1, lo0, lo1) in DISTRICTS.items():
        if la0 <= lat <= la1 and lo0 <= lon <= lo1:
            return name
    return "вне bbox районов"

rows = list(csv.DictReader(open(CSV)))
print(f"Точек всего: {len(rows)}")

by_d = Counter()
by_proj_d = defaultdict(set)
uncovered = []
for r in rows:
    lat, lon = float(r["lat"]), float(r["lon"])
    d = district_of(lat, lon)
    by_d[d] += 1
    by_proj_d[d].add(r["project_name"])
    if d == "вне bbox районов":
        uncovered.append((lat, lon, r["project_name"]))

print("\n== Распределение отметок по районам ==")
for d, n in by_d.most_common():
    print(f"{n:5d}  {d:22s} проектов: {len(by_proj_d[d])}")

if uncovered:
    print("\n== Точки вне bbox (проверить вручную) ==")
    for lat, lon, p in sorted(set(uncovered)):
        print(f"  {lat:.5f},{lon:.5f}  {p} ({sum(1 for u in uncovered if u[:2]==(lat,lon))} отм.)")

# Сетка 2x2 км: ячейка 0.02° lat x 0.0127° lon (~2.2 x 2.0 км на 59.9)
CELL_LA, CELL_LO = 0.02, 0.0127
cells = Counter()
for r in rows:
    lat, lon = float(r["lat"]), float(r["lon"])
    cells[(round(lat / CELL_LA), round(lon / CELL_LO))] += 1

n_cells = len(cells)
areas = Counter()
for (i, j), n in cells.items():
    if n >= 50: areas[">=50"] += 1
    elif n >= 20: areas["20-49"] += 1
    elif n >= 5: areas["5-19"] += 1
    else: areas["1-4"] += 1
print(f"\n== Сетка 2x2 км: {n_cells} непустых ячеек ==")
print(areas)

# Проверка соответствия h_model vs value по районам (средний dH)
dH = defaultdict(list)
for r in rows:
    dH[district_of(float(r["lat"]), float(r["lon"]))].append(float(r["dH"]))
print("\n== dH по районам (медиана, MAD, n) ==")
for d in sorted(dH, key=lambda x: -len(dH[x])):
    v = sorted(dH[d]); n = len(v)
    med = v[n // 2] if n % 2 else 0.5 * (v[n//2 - 1] + v[n//2])
    mad = sorted(abs(x - med) for x in v)[n // 2]
    print(f"{d:22s} n={n:4d}  med dH={med:+.2f} м  MAD={mad:.2f} м")

# Сохранить ячейки
with open("scripts/data/coverage_by_cell_5.66.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["cell_i", "cell_j", "lat_center", "lon_center", "n_marks"])
    for (i, j), n in sorted(cells.items(), key=lambda kv: -kv[1]):
        w.writerow([i, j, round(i * CELL_LA, 4), round(j * CELL_LO, 4), n])
print("\nOK: scripts/data/coverage_by_cell_5.66.csv")
