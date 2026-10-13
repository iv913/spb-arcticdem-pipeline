#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""5.95c — НСПД через RU-прокси: гейт + рекогносцировка API + выгрузка по TARGETS.

Вход: results/proxy_pool_5.95.json (пул живых от proxy_hunt_5.95.py).
Шаги:
  1) гейт через лучший прокси (повторная фиксация доступа);
  2) рекогносцировка портала: HTML + JS-бандлы -> /api/-паттерны, tile-серверы;
  3) пробы найденных эндпоинтов (status-лог);
  4) фичи/КПТ по 7 кварталам TARGETS (nspd_5.94) + запасной ПКК-маршрут
     pkk.rosreestr.ru/api/features/1?bbox=... — всё через RU-прокси.

Выход: data/nspd_proxy_5.95/* + results/nspd_proxy_fetch_5.95.json.
Вежливость: пауза 0.7 с между запросами, ротация прокси при сбоях.
"""
import datetime as dt
import json
import os
import re
import sys
import time

import requests
import urllib3
urllib3.disable_warnings()

sys.path.insert(0, os.path.dirname(__file__))
from nspd_fetch_5_94 import TARGETS  # noqa: E402  (переименованная копия протокола)

UA = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
      "Accept": "*/*",
      "Accept-Language": "ru-RU,ru;q=0.9",
      "Referer": "https://nspd.gov.ru/map"}
OUT_DATA = "data/nspd_proxy_5.95"
OUT_JSON = "results/nspd_proxy_fetch_5.95.json"
PAUSE = 0.7

os.makedirs(OUT_DATA, exist_ok=True)

with open("results/proxy_pool_5.95.json") as f:
    PROXIES = [a["proxy"] for a in json.load(f)["alive"]]

STATE = {"i": 0, "req": 0}
log = []


def rq(url, timeout=(10, 25), max_try=None, save_as=None, referer=None):
    """GET через ротацию RU-прокси; возврат (status, bytes) или (None, err)."""
    tries = max_try or len(PROXIES)
    h = dict(UA)
    if referer:
        h["Referer"] = referer
    for k in range(tries):
        px = PROXIES[(STATE["i"] + k) % len(PROXIES)]
        try:
            r = requests.get(url, proxies={"http": f"http://{px}", "https": f"http://{px}"},
                             timeout=timeout, headers=h, verify=False, allow_redirects=True)
            STATE["req"] += 1
            time.sleep(PAUSE)
            if save_as and 200 <= r.status_code < 400:
                with open(save_as, "wb") as f:
                    f.write(r.content)
            log.append({"url": url, "proxy": px, "status": r.status_code,
                        "bytes": len(r.content)})
            return r.status_code, r.content
        except Exception as e:
            log.append({"url": url, "proxy": px, "err": f"{type(e).__name__}: {str(e)[:60]}"})
            time.sleep(0.4)
    return None, b""


def main():
    print(f"[5.95c] НСПД через RU-прокси ({len(PROXIES)} шт.), "
          f"{dt.datetime.now(dt.UTC):%Y-%m-%d %H:%M} UTC")

    # 1) гейт через прокси
    st, body = rq(GATE_URL := "https://nspd.gov.ru/", save_as=f"{OUT_DATA}/portal.html")
    gate = {"url": GATE_URL, "status": st, "bytes": len(body)}
    print(f"1) гейт: HTTP {st}, {len(body)} B")
    if st != 200:
        json.dump({"gate": gate, "log": log}, open(OUT_JSON, "w"), ensure_ascii=False, indent=1)
        print("гейт закрыт даже через прокси — стоп")
        return 2
    html = body.decode("utf-8", "replace")

    # 2) рекогносцировка: скрипты/бандлы
    srcs = re.findall(r'(?:src|href)="(/[^"]+\.(?:js|json))"', html)
    print(f"2) бандлов в HTML: {len(srcs)}")
    endpoints, tiles = set(), set()
    for s in srcs[:6]:
        u = "https://nspd.gov.ru" + s
        stb, bb = rq(u, timeout=(10, 30), referer="https://nspd.gov.ru/")
        print(f"   {s} -> {stb}, {len(bb)} B")
        if stb != 200:
            continue
        txt = bb.decode("utf-8", "replace")
        endpoints |= set(re.findall(r'["\'](/api/[A-Za-z0-9_?/=&.%-]+)', txt))
        endpoints |= set(re.findall(r'["\'](https?://[A-Za-z0-9.-]*nspd[A-Za-z0-9.-]*'
                                    r'/api/[A-Za-z0-9_?/=&.%-]+)', txt))
        tiles |= set(re.findall(r'["\'](/tiles/[A-Za-z0-9_{}/.?=&%-]+)', txt))
        tiles |= set(re.findall(r'(https?://[A-Za-z0-9.-]+/tiles?/[A-Za-z0-9_{}/.?=&%-]+)', txt))
        if len(bb) > 3_000_000:
            continue
    endpoints = sorted(e for e in endpoints if "{" not in e)[:40]
    tiles = sorted(tiles)[:15]
    print(f"   api-эндпоинтов: {len(endpoints)}; tile-паттернов: {len(tiles)}")

    # 3) пробы эндпоинтов
    probes = []
    for e in endpoints[:20]:
        u = e if e.startswith("http") else "https://nspd.gov.ru" + e
        stp, bp = rq(u, timeout=(8, 15))
        probes.append({"url": e, "status": stp, "bytes": len(bp),
                       "sample": bp[:120].decode("utf-8", "replace") if stp == 200 else ""})
        print(f"   probe {e[:70]:<70} -> {stp} ({len(bp)} B)")

    # 4) фичи по TARGETS: nspd search-паттерны + запасной ПКК
    got = []
    fdirs = []
    # 4a: если найден features/search-эндпоинт с bbox-подстановкой
    feat_ep = [e for e in endpoints
               if re.search(r"feature|search|adar|pkk|parcel", e, re.I)]
    for tag, distr, (la0, la1, lo0, lo1) in TARGETS:
        bbox = f"{lo0},{la0},{lo1},{la1}"  # lon,lat (ПКК-конвенция)
        for e in feat_ep[:4]:
            u = e if e.startswith("http") else "https://nspd.gov.ru" + e
            sep = "&" if "?" in u else "?"
            full = f"{u}{sep}bbox={bbox}"
            fn = f"{OUT_DATA}/nspd_{tag}_{re.sub(r'[^A-Za-z0-9]', '_', e)[:60]}.json"
            stf, bf = rq(full, timeout=(8, 20), save_as=fn,
                         referer="https://nspd.gov.ru/map")
            if stf == 200 and len(bf) > 60:
                got.append({"tag": tag, "url": full, "file": fn, "bytes": len(bf)})
        # 4b: ПКК Росреестра (запасной маршрут)
        u = f"https://pkk.rosreestr.ru/api/features/1?bbox={bbox}"
        fn = f"{OUT_DATA}/pkk_{tag}.json"
        stp, bp = rq(u, timeout=(8, 20), save_as=fn)
        if stp == 200 and len(bp) > 60:
            got.append({"tag": tag, "url": u, "file": fn, "bytes": len(bp)})
        fdirs.append({"tag": tag, "distr": distr, "bbox_lonlat": bbox})

    n_files = len([f for f in os.listdir(OUT_DATA)])
    out = {
        "generated": dt.datetime.now(dt.UTC).isoformat(),
        "proxies": PROXIES,
        "gate": gate,
        "bundles": [s for s in srcs[:6]],
        "endpoints": endpoints,
        "tile_patterns": tiles,
        "probes": probes,
        "targets": fdirs,
        "fetches": got,
        "requests_total": STATE["req"],
        "files_in_outdir": n_files,
        "note": "доступ через публичные RU-прокси; TLS unverified; без авторизации",
    }
    json.dump(out, open(OUT_JSON, "w"), ensure_ascii=False, indent=1)
    print(f"\nфайлов в {OUT_DATA}: {n_files}; выгрузок с данными: {len(got)}")
    print(f"-> {OUT_JSON}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
