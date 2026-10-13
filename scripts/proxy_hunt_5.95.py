#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""5.95b — Охота RU-прокси для гейта НСПД (директива: «так используй прокси рф»).

RU-источники -> дедуп -> тест HTTPS CONNECT к nspd.gov.ru (TLS unverified).
Если RU-кандидатов мало — глобальные списки: alive-тест на нейтральной цели ->
геолокация выхода ip-api /batch (по 100 IP) -> RU-подмножество -> тест гейта.

Выход: results/proxy_pool_5.95.json + консольная сводка.
Прокси используются ТОЛЬКО для обращений к НСПД; git push идёт напрямую.
"""
import concurrent.futures as cf
import datetime as dt
import json
import random
import socket
import sys
import time

import requests
import urllib3
urllib3.disable_warnings()

GATE = "https://nspd.gov.ru/"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}

RU_SOURCES = [
    ("proxyscrape_ru",
     "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http"
     "&timeout=9000&country=RU&ssl=all&anonymity=all", "plain"),
    ("proxylistdl_ru",
     "https://www.proxy-list.download/api/v1/get?type=http&country=RU", "plain"),
    ("geonode_ru",
     "https://proxylist.geonode.com/api/proxy-list?limit=500&page=1&sort_by="
     "lastChecked&sort_type=desc&protocols=http%2Chttps&country=RU", "geonode"),
    ("proxifly_all",
     "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/all/data.json",
     "proxifly"),
]
GLOBAL_SOURCES = [
    ("roosterkid_https",
     "https://raw.githubusercontent.com/roosterkid/openproxylist/main/HTTPS_RAW.txt", "plain"),
    ("monosans_http",
     "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt", "plain"),
    ("thespeedx_http",
     "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt", "plain"),
]


def add_plain(pool, text, src):
    n0 = len(pool)
    for ln in text.splitlines():
        ln = ln.strip().lstrip("*").strip()
        if ln and ln.count(":") == 1 and ln.split(":")[0].count(".") == 3:
            ip, port = ln.split(":")
            if port.isdigit():
                pool.setdefault(f"{ip}:{port}", src)
    return len(pool) - n0


def fetch_sources(sources):
    pool = {}
    for name, url, kind in sources:
        try:
            r = requests.get(url, timeout=30, headers=UA)
            n0 = len(pool)
            if kind == "plain":
                add_plain(pool, r.text, name)
            elif kind == "geonode":
                for it in r.json().get("data", []):
                    p = f"{it.get('ip')}:{it.get('port')}"
                    if it.get("ip") and str(it.get("port", "")).isdigit():
                        pool.setdefault(p, name)
            elif kind == "proxifly":
                for it in r.json():
                    try:
                        if it.get("geolocation", {}).get("country") == "RU" \
                           and it.get("protocol") in ("http", "https"):
                            pool.setdefault(f"{it['ip']}:{it['port']}", name)
                    except Exception:
                        continue
            print(f"  [{name}] HTTP {r.status_code}, +{len(pool)-n0} (итого {len(pool)})")
        except Exception as e:
            print(f"  [{name}] FAIL: {type(e).__name__}: {str(e)[:70]}")
    return pool


def tcp_ok(host, port, timeout=5):
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except Exception:
        return False


def test_neutral(pxy):
    """alive-тест глобального списка на нейтральной цели."""
    host, port = pxy.rsplit(":", 1)
    if not tcp_ok(host, port):
        return None
    try:
        r = requests.get("http://example.com/",
                         proxies={"http": f"http://{pxy}"}, timeout=8, headers=UA)
        if 200 <= r.status_code < 400:
            return pxy
    except Exception:
        pass
    return None


def geolocate(proxies):
    """ip-api /batch: countryCode для списка ip (по 100)."""
    out = {}
    ips = [p.rsplit(":", 1)[0] for p in proxies]
    for i in range(0, len(ips), 100):
        chunk = list(dict.fromkeys(ips[i:i + 100]))
        try:
            r = requests.post("http://ip-api.com/batch?fields=status,countryCode,query",
                              json=chunk, timeout=15, headers=UA)
            for it in r.json():
                if it.get("status") == "success":
                    out[it["query"]] = it.get("countryCode", "??")
        except Exception as e:
            print(f"  ip-api batch {i//100}: FAIL {type(e).__name__}")
        time.sleep(1.2)
    return out


def test_gate(pxy):
    """Полный тест: HTTPS CONNECT к nspd.gov.ru через прокси."""
    host, port = pxy.rsplit(":", 1)
    if not tcp_ok(host, port):
        return None
    t0 = time.time()
    try:
        r = requests.get(GATE, proxies={"http": f"http://{pxy}", "https": f"http://{pxy}"},
                         timeout=16, headers=UA, verify=False, allow_redirects=True)
        ms = int((time.time() - t0) * 1000)
        rec = {"proxy": pxy, "ms": ms, "status": r.status_code, "bytes": len(r.content)}
        if 200 <= r.status_code < 400 and len(r.content) > 800:
            return rec
        rec["weak"] = True
        return rec
    except Exception as e:
        return {"proxy": pxy, "err": f"{type(e).__name__}: {str(e)[:60]}"}


def run_gate_test(cands, label):
    alive, weak, dead = [], 0, 0
    print(f"\n   [{label}] кандидатов: {len(cands)}, тест гейта (40 потоков)")
    with cf.ThreadPoolExecutor(max_workers=40) as ex:
        for res in ex.map(test_gate, cands):
            if res is None or "err" in res:
                dead += 1
            elif res.get("weak"):
                weak += 1
            else:
                alive.append(res)
    alive.sort(key=lambda d: d["ms"])
    print(f"   [{label}] живых: {len(alive)}, слабых: {weak}, мёртвых: {dead}")
    return alive, weak, dead


def main():
    print(f"[5.95b] охота RU-прокси для {GATE}, {dt.datetime.now(dt.UTC):%Y-%m-%d %H:%M} UTC")
    print("1) RU-источники:")
    pool = fetch_sources(RU_SOURCES)

    if len(pool) < 40:
        print(f"\n2) RU-кандидатов мало ({len(pool)}) — глобальный fallback:")
        gpool = fetch_sources(GLOBAL_SOURCES)
        gpool = {k: v for k, v in gpool.items() if k not in pool}
        cands = random.sample(sorted(gpool), min(len(gpool), 2200))
        print(f"   глобальный пул: {len(gpool)}, сэмпл: {len(cands)}")
        alive_g = []
        with cf.ThreadPoolExecutor(max_workers=60) as ex:
            for p in ex.map(test_neutral, cands):
                if p:
                    alive_g.append(p)
        print(f"   alive на нейтральной цели: {len(alive_g)}")
        if alive_g:
            geo = geolocate(alive_g)
            ru = [p for p in alive_g if geo.get(p.rsplit(':', 1)[0]) == "RU"]
            print(f"   геолокация: RU-выходов {len(ru)} из {len(geo)}")
            for p in ru:
                pool.setdefault(p, "global_geo")
    else:
        print("\n2) global fallback не нужен")
    cands = sorted(pool)
    print(f"\n3) RU-пул: {len(cands)}")
    alive, weak, dead = run_gate_test(cands, "gate")
    for a in alive[:12]:
        print(f"   OK {a['proxy']:<26} {a['ms']:>5} ms  HTTP {a['status']}  {a['bytes']} B")
    out = {
        "generated": dt.datetime.now(dt.UTC).isoformat(),
        "target": GATE,
        "candidates": len(cands),
        "pool_sources": pool,
        "alive": alive,
        "alive_n": len(alive),
        "weak_n": weak,
        "dead_n": dead,
        "note": "TLS unverified (как гейт 5.94); прокси только для НСПД",
    }
    with open("results/proxy_pool_5.95.json", "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("\n-> results/proxy_pool_5.95.json")
    return 0 if alive else 1


if __name__ == "__main__":
    sys.exit(main())
