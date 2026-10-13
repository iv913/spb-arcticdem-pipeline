#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""5.95g — Recon v4 (финальный): полная браузерная мимикрия, per-proxy, api-хост.

Гипотеза: 403 на /api/* — репутационный/WAF-гейт шлюза. Проверка:
  1) /api/config и /api/adar/search через КАЖДЫЙ прокси отдельно
     с полным набором sec-ch-ua/sec-fetch заголовков;
  2) альтернативный хост api.nspd.gov.ru;
  3) вердикт для отчёта.

Выход: results/nspd_api_recon4_5.95.json.
"""
import datetime as dt
import json
import sys
import time

import requests
import urllib3
urllib3.disable_warnings()

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
H = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Origin": "https://nspd.gov.ru",
    "Referer": "https://nspd.gov.ru/map",
    "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "X-Requested-With": "XMLHttpRequest",
}
PROXIES = ["217.114.12.153:443", "88.204.22.105:8080", "62.113.114.246:8443",
           "95.52.246.169:1082"]
OUT_JSON = "results/nspd_api_recon4_5.95.json"


def rq(url, px):
    try:
        r = requests.get(url, proxies={"http": f"http://{px}", "https": f"http://{px}"},
                         timeout=(8, 14), headers=H, verify=False, allow_redirects=True)
        time.sleep(0.4)
        return {"status": r.status_code, "bytes": len(r.content),
                "body": r.content[:300].decode("utf-8", "replace")}
    except Exception as e:
        return {"status": None, "err": f"{type(e).__name__}: {str(e)[:70]}"}


def main():
    print(f"[5.95g] recon v4 per-proxy, {dt.datetime.now(dt.UTC):%H:%M} UTC")
    out = {"generated": dt.datetime.now(dt.UTC).isoformat(), "per_proxy": {}}
    urls = [
        "https://nspd.gov.ru/api/config",
        "https://nspd.gov.ru/api/adar/search?query=78:5:1234:12",
        "https://nspd.gov.ru/api/adar/pkk/features/1?bbox=30.28,59.85,30.30,59.86",
    ]
    hit = False
    for px in PROXIES:
        res = {}
        for u in urls:
            res[u] = rq(u, px)
            s = res[u]["status"]
            if s and s not in (403, 404):
                hit = True
            print(f"  {px:<22} {u.split('nspd.gov.ru')[1][:55]:<55} -> {s}")
        out["per_proxy"][px] = res

    out["api_host_alt"] = {}
    for px in PROXIES[:2]:
        r = rq("https://api.nspd.gov.ru/api/config", px)
        out["api_host_alt"][px] = r
        print(f"  api.nspd.gov.ru via {px} -> {r['status']}")

    out["verdict"] = ("API_OPEN" if hit else
                      "API_403_ALL: шлюз НСПД закрывает /api/* даже с RU-выхода "
                      "публичных прокси — репутационный/авторизационный гейт, "
                      "не чисто гео")
    print("\nВЕРДИКТ:", out["verdict"])
    json.dump(out, open(OUT_JSON, "w"), ensure_ascii=False, indent=1)
    print(f"-> {OUT_JSON}")
    return 0 if hit else 1


if __name__ == "__main__":
    sys.exit(main())
