#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""5.94 — Протокол выкопировок НСПД/КГА («метод ②») — готов к запуску ИЗ СЕТИ РФ.

Контекст (план 5.66 §6.5, решён в 5.94): прямые источники отметок высот
(НСПД nspd.gov.ru, ПКК pkk.rosreestr.ru, КГА/ИСОГД, gov.spb, РГИС) закрыты
гео-фильтром для внешней сети (results/sources_audit_5.94.json). Потребность
в отметках как единственном валидаторе юга снята этапами 5.90–5.93 (245k
южных точек ATL06, gold MAD 0.24–0.32 по районам); остаточная роль отметок —
независимый (не-ICESat) перекрёстный контроль датума БС-77 и мозаики 5.90.

Этот скрипт НЕ требует сети для планирования: `--plan` печатает полный
протокол. Сетевые операции включаются только флагом `--from-rf` И живым
гейтом (nspd.gov.ru обязан отвечать) — запускать из сети РФ или VPN-РФ.

Схема работ (из сети РФ):
  1) НСПД: кадастровые выкопировки КПТ по 7 кварталам (список TARGETS) —
     PDF/JSON фрагменты с отметками высот углов улиц/отмосток/пола 1-го эт.;
     имя файла: data/nspd_kpt_<tag>_<n>.pdf|json
  2) КГА/ИСОГД: ППТ/ПМТ с чертежами вертикальной планировки (красные
     горизонтали, планировочные отметки проездов) — через официальный
     запрос или ППТ-архивы; имя: data/kga_ppt_<tag>_<n>.pdf
  3) Разметка «метод ②» (тот же протокол, что lenobl 5.47–5.49):
     точки на перекрёстках / углах кварталов / открытой местности,
     schema OUT_SCHEMA, QC как в check_marks().
  4) Валидация: против мозаики 5.90 (dem2m) и канона v4 — MAD/med/p95 по
     кварталам; отчёт results/nspd_marks_val_5.95.json.

Выход (из сети РФ): data/nspd_marks_5.95.csv + results/nspd_marks_val_5.95.json.
"""
import argparse
import datetime as dt
import json
import os
import sys

# ── 7 целевых кварталов (bbox предварительные, уточняются по кадастровому
#    делению НСПД при загрузке; WGS84, lat0,lat1,lon0,lon1)
TARGETS = [
    ("AVOVO",   "Кировский",     (59.845, 59.868, 30.265, 30.315)),
    ("DACHNOE", "Кировский",     (59.830, 59.856, 30.165, 30.225)),
    ("ULYANKA", "Кировский",     (59.818, 59.845, 30.125, 30.185)),
    ("SHUSHARY","Пушкинский",    (59.788, 59.818, 30.345, 30.405)),
    ("KOLPINO", "Колпинский",    (59.733, 59.762, 30.545, 30.615)),
    ("PETERGOF","Петродворцовый",(59.868, 59.896, 29.715, 29.795)),
    ("SESTRORETSK","Курортный",  (60.095, 60.115, 29.745, 29.815)),
]

OUT_SCHEMA = ("lat,lon,z_survey,context,source,rid,page,scale  "
              "# z_survey — абсолютная отметка, м, БС-77; context — "
              "перекрёсток/угол квартала/открытая местность + адрес")

NSPD_ENDPOINT_CANDIDATES = [
    "https://nspd.gov.ru/",
    "https://nspd.gov.ru/api/",
    "https://nspd.gov.ru/map",
]
GATE_HOST = "nspd.gov.ru"


def probe_gate(timeout=6):
    import socket
    try:
        ip = socket.getaddrinfo(GATE_HOST, 443, proto=socket.IPPROTO_TCP)[0][4][0]
    except Exception:
        return False, "DNS_FAIL"
    try:
        with socket.create_connection((ip, 443), timeout=timeout):
            return True, ip
    except Exception as e:
        return False, f"TCP_BLOCK({type(e).__name__})"


def plan():
    print(f"[5.94] протокол выкопировок НСПД/КГА, {dt.date.today()}")
    print("Целевые кварталы (bbox предварительные, WGS84):")
    for tag, distr, (a, b, c, d) in TARGETS:
        print(f"  {tag:<12} {distr:<14} lat {a}–{b}, lon {c}–{d}")
    print("\n1) НСПД (из РФ): КПТ/выкопировки по кварталам -> data/nspd_kpt_<tag>_<n>.pdf|json")
    print("2) КГА/ИСОГД (из РФ): ППТ/ПМТ вертикальной планировки -> data/kga_ppt_<tag>_<n>.pdf")
    print(f"3) Разметка метода ②: {OUT_SCHEMA}")
    print("4) QC: дедуп 0.001°, z∈[0;80] м БС-77, |z−prior30|<15 м триаж (протокол 5.49)")
    print("5) Валидация: против мозаики 5.90 (dem2m, round-конвенция 5.90/5.91) и канона v4")
    print("   -> results/nspd_marks_val_5.95.json (MAD/med/p95 по кварталам и итог)")
    print("\nЗапуск сетевой части: python3 scripts/nspd_fetch_5.94.py --from-rf")


def run_rf():
    ok, info = probe_gate()
    if not ok:
        print(f"[5.94] ГЕЙТ ЗАКРЫТ: {GATE_HOST} -> {info}")
        print("Сетевая часть недоступна из этой сети. Запускать из сети РФ / VPN-РФ.")
        print("Планирование доступно без сети: python3 scripts/nspd_fetch_5.94.py --plan")
        return 2
    print(f"[5.94] гейт {GATE_HOST} открыт ({info}) — сеть РФ. Доступные операции:")
    import urllib.request, ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    for url in NSPD_ENDPOINT_CANDIDATES:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
                print(f"  {url} -> HTTP {r.status}")
        except Exception as e:
            print(f"  {url} -> {type(e).__name__}: {str(e)[:90]}")
    print("\nДальше — ручной цикл: скачать КПТ/выкопировки по TARGETS в data/,")
    print("разметить по методу ② в data/nspd_marks_5.95.csv, прогнать валидацию")
    print("(sample_mosaic_5.90-конвенция) и зафиксировать отчёт 5.95.")
    return 0


def check_marks(path):
    """QC размеченных отметок (offline, вызывается после разметки)."""
    import csv
    rows = list(csv.DictReader(open(path)))
    seen, keep, drop = set(), [], []
    for r in rows:
        key = (round(float(r["lat"]), 3), round(float(r["lon"]), 3))
        z = float(r["z_survey"])
        if key in seen or not (0.0 <= z <= 80.0):
            drop.append(r)
            continue
        seen.add(key)
        keep.append(r)
    return {"n_total": len(rows), "n_keep": len(keep), "n_drop": len(drop),
            "note": "дедуп 0.001° + z∈[0;80] БС-77"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", action="store_true", help="печать протокола (offline)")
    ap.add_argument("--from-rf", action="store_true", help="включить сетевые операции")
    ap.add_argument("--check-marks", help="QC файла data/nspd_marks_5.95.csv")
    a = ap.parse_args()
    if a.check_marks:
        print(json.dumps(check_marks(a.check_marks), ensure_ascii=False, indent=1))
        return 0
    if a.from_rf:
        return run_rf()
    plan()
    return 0


if __name__ == "__main__":
    sys.exit(main())
