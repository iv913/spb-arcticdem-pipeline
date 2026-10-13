#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""5.95d — Глубокая рекогносцировка API НСПД через RU-прокси.

Вход: data/nspd_proxy_5.95/portal.html (сохранён на шаге 5.95c).
1) все /assets/*.js из HTML -> загрузка (кап 25 МБ суммарно);
2) regex: /api/-пути, tile/wms/geoserver-паттерны, внешние api-хосты,
   remoteEntry-чанки module federation (дозагрузка 1 уровня);
3) пробы найденных эндпоинтов (без авторизации) -> статус + семпл.

Выход: results/nspd_api_recon_5.95.json.
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

UA = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
      "Accept": "*/*", "Referer": "https://nspd.gov.ru/map"}
OUT_JSON = "results/nspd_api_recon_5.95.json"
with open("results/proxy_pool_5.95.json") as f:
    PROXIES = [a["proxy"] for a in json.load(f)["alive"]]
STATE = {"i": 0}


def rq(url, timeout=(8, 15), save_as=None, cap=None):
    for k in range(len(PROXIES)):
        px = PROXIES[(STATE["i"] + k) % len(PROXIES)]
        try:
            r = requests.get(url, proxies={"http": f"http://{px}", "https": f"http://{px}"},
                             timeout=timeout, headers=UA, verify=False, allow_redirects=True)
            STATE["i"] = (STATE["i"] + k + 1) % len(PROXIES)
            time.sleep(0.5)
            if 200 <= r.status_code < 400:
                body = r.content if not cap else r.content[:cap]
                if save_as:
                    open(save_as, "wb").write(body)
                return r.status_code, body
            return r.status_code, b""
        except Exception:
            time.sleep(0.3)
    return None, b""


def harvest(txt, bag, src):
    pats = [
        r'"(/api/[A-Za-z0-9_\-./?=&{}%]+)"',
        r"'(/api/[A-Za-z0-9_\-./?=&{}%]+)'",
        r'"(https?://[A-Za-z0-9.-]+/api/[A-Za-z0-9_\-./?=&{}%]+)"',
        r'"(/(?:tiles|tile|wms|ows|geoserver|arcgis|geoportal|adar|egip)'
        r'[A-Za-z0-9_\-./?=&{}%]*)"',
        r'`(/\$\{[^`]{0,60}\})`',          # template-literal api пути
        r'"(/[a-z0-9_-]+/v[12]/[A-Za-z0-9_\-./]+)"',
    ]
    for p in pats:
        for m in re.findall(p, txt):
            if isinstance(m, tuple):
                m = m[0]
            if len(m) < 200:
                bag.setdefault(m, set()).add(src)


def main():
    print(f"[5.95d] глубокая рекогносцировка НСПД, {dt.datetime.now(dt.UTC):%H:%M} UTC")
    html = open("data/nspd_proxy_5.95/portal.html", encoding="utf-8", errors="replace").read()
    srcs = sorted(set(re.findall(r'"(/assets/[^"]+\.js)"', html)))
    print(f"1) бандлов в HTML: {len(srcs)}")

    bag, remotes, total = {}, set(), 0
    js_store = {}
    T0 = time.time()
    for i, s in enumerate(srcs):
        if len(bag) >= 25 or total > 12_000_000 or time.time() - T0 > 300:
            print(f"   ранний стоп: candidates={len(bag)}, {total} B, "
                  f"{int(time.time()-T0)} s")
            break
        st, bb = rq("https://nspd.gov.ru" + s, timeout=(8, 14), cap=2_000_000)
        total += len(bb)
        print(f"   [{i+1}/{len(srcs)}] {s} -> {st} {len(bb)} B")
        if st != 200:
            continue
        js_store[s] = bb.decode("utf-8", "replace")
        harvest(js_store[s], bag, s)
        remotes |= set(re.findall(r'"([A-Za-z0-9_./-]*remoteEntry[A-Za-z0-9_.-]*\.js)"',
                                  js_store[s]))
        remotes |= set(re.findall(r'"(/assets/[^"]*remoteEntry[^"]*)"', js_store[s]))

    # 2) remoteEntry 1-го уровня (module federation -> чанки с api)
    for r_path in sorted(remotes)[:3]:
        if time.time() - T0 > 400:
            break
        u = r_path if r_path.startswith("http") else "https://nspd.gov.ru" + r_path
        st, bb = rq(u, timeout=(8, 14), cap=2_000_000)
        print(f"   remote {r_path} -> {st} {len(bb)} B")
        if st == 200 and bb:
            t = bb.decode("utf-8", "replace")
            js_store[r_path] = t
            harvest(t, bag, r_path)
            # exposed-чанки федерации: "./chunk-xxx.js" -> /assets/...? подставим как есть
            for m in re.findall(r'"(\./[A-Za-z0-9_.-]+\.js)"', t)[:6]:
                base = r_path.rsplit("/", 1)[0]
                full = (base + "/" + m[2:]) if base else "/assets/" + m[2:]
                st2, bb2 = rq("https://nspd.gov.ru" + full, timeout=(8, 14), cap=2_000_000)
                if st2 == 200 and bb2:
                    harvest(bb2.decode("utf-8", "replace"), bag, full)

    eps = {k: sorted(v) for k, v in bag.items()}
    ranked = sorted(eps, key=lambda e: -len(eps[e]))
    print(f"\n2) уникальных кандидатов: {len(eps)}")
    for e in ranked[:30]:
        print(f"   {len(eps[e]):>2}x  {e[:100]}")

    # 3) пробы (без {}, с подстановкой демо-значений)
    demo = {"{z}": "10", "{x}": "614", "{y}": "322"}
    probes = []
    for e in ranked[:15]:
        u = e if e.startswith("http") else "https://nspd.gov.ru" + e
        for k, v in demo.items():
            u = u.replace(k, v)
        u = re.sub(r"\{[^}]*\}", "1", u)
        st, bb = rq(u, timeout=(6, 12), cap=800)
        probes.append({"ep": e, "url": u, "status": st, "bytes": len(bb),
                       "sample": bb[:160].decode("utf-8", "replace") if st == 200 else ""})
        print(f"   probe [{st}] {u[:95]}")

    json.dump({"generated": dt.datetime.now(dt.UTC).isoformat(),
               "bundles_total_bytes": total, "remotes": sorted(remotes),
               "endpoints": eps, "ranked": ranked, "probes": probes},
              open(OUT_JSON, "w"), ensure_ascii=False, indent=1)
    print(f"\n-> {OUT_JSON}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
