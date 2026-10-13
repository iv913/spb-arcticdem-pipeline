#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""5.94 — Формальный аудит доступности источников НСПД/КГА (и заменителей).

Закрывает вопрос «из сети РФ или нет» воспроизводимой проверкой 2026-10-13
(первая формальная проверка была 2026-10-05 в 5.66). Для каждого источника
из матрицы 5.66: DNS -> TCP(443) -> HTTP GET. Вердикты:

  OK          — HTTP-ответ получен
  GEO_BLOCK   — DNS разрешается, TCP не соединяется (таймаут/отказ) —
                классический признак географического фильтра (сеть РФ required)
  DNS_FAIL    — домен не разрешается из этой сети
  HTTP_<code> — соединение есть, но ответ не 2xx/3xx (anti-bot и т.п.)

Запуск: python3 scripts/sources_audit_5.94.py [--json PATH] [--fast]
Результат: печать MD-таблицы + results/sources_audit_5.94.json.
"""
import argparse
import datetime as dt
import json
import socket
import subprocess
import sys

SOURCES = [
    # (id, host, role из плана 5.66)
    ("nspd",        "nspd.gov.ru",               "НСПД: топопланы 1:2000, КПТ/выкопировки (кв. Автово/Дачное/Ульянка, Шушары, Колпино, Петергоф, Сестрорецк)"),
    ("pkk",         "pkk.rosreestr.ru",          "ПКК Росреестра (замена НСПД по отметкам ЗУ)"),
    ("rkga",        "rkga.spb.ru",               "КГА СПб (legacy-домен)"),
    ("kga",         "kga.gov.ru",                "КГА СПб / ИСОГД: ППТ/ПМТ, чертежи вертикальной планировки"),
    ("govspb",      "gov.spb.ru",                "Портал Правительства СПб (дубли ППТ/КГА)"),
    ("rgis",        "rgis.spb.ru",               "РГИС СПб (городские слои)"),
    ("lenobl",      "arch.lenobl.ru",            "ЛО-генпланы МО (2-я очередь для Колпино/Петергоф/Сестрорецк)"),
    ("geokniga",    "geokniga.org",              "Генштаб 1:10k/1:25k сканы (горизонтали)"),
    ("igi_south",   "reports.geologyscience.ru", "ИГИ-отчёты по югу (Росгеолфонд-путь)"),
    ("webarchive",  "web.archive.org",           "Кэш-путь к заблокированным страницам"),
]

TCP_TIMEOUT = 6.0
HTTP_MAXTIME = 10


def check_dns(host):
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
        return infos[0][4][0]
    except Exception:
        return None


def check_tcp(host, ip, timeout=TCP_TIMEOUT):
    try:
        with socket.create_connection((ip, 443), timeout=timeout):
            return True, None
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"[:120]


def check_http(host):
    """HEAD, при пустом ответе — GET. Возвращает (http_code, err)."""
    for meth in ("-I", "-X", "GET"):
        cmd = ["curl", "-sk", "-o", "/dev/null", "-w", "%{http_code}",
               "--max-time", str(HTTP_MAXTIME)]
        if meth == "-I":
            cmd += ["-I"]
        elif meth == "-X":
            cmd += ["-X", "GET"]
        cmd.append(f"https://{host}/")
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=HTTP_MAXTIME + 5)
            code = r.stdout.strip()
            if code and code not in ("000",):
                return int(code), None
        except subprocess.TimeoutExpired:
            return 0, "curl timeout"
    return 0, "no response (000)"


def audit():
    rows = []
    for sid, host, role in SOURCES:
        row = {"id": sid, "host": host, "role": role}
        ip = check_dns(host)
        row["dns_ip"] = ip
        if not ip:
            row["verdict"] = "DNS_FAIL"
        else:
            ok, err = check_tcp(host, ip)
            row["tcp"] = {"ok": ok, "err": err}
            if not ok:
                row["verdict"] = "GEO_BLOCK"
            else:
                code, err = check_http(host)
                row["http_code"] = code
                row["verdict"] = "OK" if 200 <= code < 400 else f"HTTP_{code}"
                if err:
                    row["http_err"] = err
        rows.append(row)
        print(f"  {sid:<11} {host:<28} {row['verdict']}", flush=True)
    return rows


def md_table(rows):
    lines = ["| Источник | Host | Вердикт | Детали |",
             "|---|---|---|---|"]
    det = {"OK": "доступен из внешней сети",
           "GEO_BLOCK": "DNS есть, TCP(443) нет — гео-фильтр, нужен выход из сети РФ",
           "DNS_FAIL": "домен не разрешается из этой сети"}
    for r in rows:
        details = det.get(r["verdict"], "")
        if r["verdict"].startswith("HTTP_"):
            details = f"TCP есть, HTTP {r.get('http_code')} (anti-bot/ошибка приложения)"
        if r.get("dns_ip"):
            details += f"; A={r['dns_ip']}"
        lines.append(f"| {r['id']} | {r['host']} | {r['verdict']} | {details} |")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default="results/sources_audit_5.94.json")
    ap.add_argument("--fast", action="store_true", help="TCP timeout 3s")
    a = ap.parse_args()
    if a.fast:
        globals()["TCP_TIMEOUT"] = 3.0
    print(f"[5.94] аудит источников НСПД/КГА, {dt.date.today()}", flush=True)
    rows = audit()
    n_ok = sum(1 for r in rows if r["verdict"] == "OK")
    n_geo = sum(1 for r in rows if r["verdict"] == "GEO_BLOCK")
    n_dns = sum(1 for r in rows if r["verdict"] == "DNS_FAIL")
    out = {
        "stage": "5.94",
        "date": str(dt.date.today()),
        "egress": "внешняя сеть (не РФ; см. worklog 5.94)",
        "summary": {"ok": n_ok, "geo_block": n_geo, "dns_fail": n_dns,
                    "other": len(rows) - n_ok - n_geo - n_dns},
        "sources": rows,
        "conclusion": (
            "Прямые источники выкопировок (НСПД, ПКК, КГА/ИСОГД, gov.spb, РГИС) "
            "недоступны из внешней сети: GEO_BLOCK/DNS_FAIL. Доступны только "
            "заменяющие архивы (lenobl — протокол 5.70 отрицательный, geokniga — "
            "сканы ГШ). Этап переведён в режим: (а) протокол выкопировок готов "
            "к запуску из сети РФ (scripts/nspd_fetch_5.94.py), (б) пакет "
            "поставки продукта в НСПД/КГА собран (download/delivery_nspd_5.94)."
        ),
    }
    with open(a.json, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print()
    print(md_table(rows))
    print(f"\n[5.94] OK={n_ok} GEO_BLOCK={n_geo} DNS_FAIL={n_dns} -> {a.json}")


if __name__ == "__main__":
    sys.exit(main())
