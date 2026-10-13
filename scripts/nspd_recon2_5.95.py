#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""5.95e — Прицельная рекогносцировка НСПД v2 (мало запросов, быстрый прокси).

1) /robots.txt — пути под /api/;
2) /map HTML — свой список чанков (diff с портальной);
3) mf-runtime чанк — remoteEntry-URLы федерации;
4) топ-3 крупных app-чанка -> regex api-путей;
5) пробы: найденные + правдоподобные гадания (bbox/демо).

Выход: results/nspd_api_recon2_5.95.json.
"""
import datetime as dt
import json
import re
import sys
import time

import requests
import urllib3
urllib3.disable_warnings()

UA = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
      "Accept": "*/*", "Referer": "https://nspd.gov.ru/map"}
OUT_JSON = "results/nspd_api_recon2_5.95.json"
ORDER = ["217.114.12.153:443", "88.204.22.105:8080", "62.113.114.246:8443",
         "95.52.246.169:1082"]


def rq(url, timeout=(8, 14), cap=None):
    for px in ORDER:
        try:
            r = requests.get(url, proxies={"http": f"http://{px}", "https": f"http://{px}"},
                             timeout=timeout, headers=UA, verify=False, allow_redirects=True)
            time.sleep(0.4)
            if 200 <= r.status_code < 400:
                return r.status_code, (r.content if not cap else r.content[:cap])
            return r.status_code, b""
        except Exception:
            time.sleep(0.3)
    return None, b""


def harvest(txt, bag, src):
    pats = [
        r'"(/api/[A-Za-z0-9_\-./?=&{}%]+)"',
        r"'(/api/[A-Za-z0-9_\-./?=&{}%]+)'",
        r'"(/adar[A-Za-z0-9_\-./?=&{}%]*)"',
        r'"(https?://[A-Za-z0-9.-]+/(?:api|tiles|ows|geoserver)'
        r'[A-Za-z0-9_\-./?=&{}%]*)"',
        r'"(/(?:tiles|wms|ows|geoportal|egip)[A-Za-z0-9_\-./?=&{}%]*)"',
        r'baseURL[:=]["\']([^"\']{3,80})["\']',
        r'apiUrl[:=]["\']([^"\']{3,80})["\']',
    ]
    for p in pats:
        for m in re.findall(p, txt):
            if len(m) < 120:
                bag.setdefault(m, set()).add(src)


def main():
    print(f"[5.95e] recon v2, {dt.datetime.now(dt.UTC):%H:%M} UTC")
    bag, report = {}, {}

    # 1) robots.txt
    st, bb = rq("https://nspd.gov.ru/robots.txt")
    robots = bb.decode("utf-8", "replace") if st == 200 else ""
    report["robots"] = {"status": st, "text": robots[:1500]}
    print(f"1) robots.txt -> {st} ({len(bb)} B)")
    for ln in robots.splitlines():
        if "/api" in ln or "Disallow: /" in ln:
            print("   ", ln[:110])

    # 2) /map шелл
    st, bb = rq("https://nspd.gov.ru/map")
    map_html = bb.decode("utf-8", "replace") if st == 200 else ""
    report["map_shell"] = {"status": st, "bytes": len(bb)}
    msrcs = sorted(set(re.findall(r'"(/assets/[^"]+\.js)"', map_html)))
    print(f"2) /map -> {st}, чанков: {len(msrcs)}")

    # 3) mf-runtime: remotes
    st, bb = rq("https://nspd.gov.ru/assets/_mf_0_nspd_mf_2_modules_mf_1_main__mf_v__"
                "runtimeInit__mf_v__-D-sOsdXi.js", cap=300_000)
    mf = bb.decode("utf-8", "replace") if st == 200 else ""
    report["mf_runtime"] = {"status": st, "bytes": len(bb)}
    remotes = sorted(set(re.findall(r'"([^"]*remoteEntry[^"]*\.js)"', mf)))
    hosts = sorted(set(re.findall(r'https?://[A-Za-z0-9.-]+\.(?:gov\.ru|ru|[a-z]{2,})'
                                  r'[A-Za-z0-9_\-./]*', mf)))[:25]
    print(f"3) mf-runtime -> {st}; remotes: {remotes[:6]}")
    if hosts:
        print("   внешние хосты:", *[h[:90] for h in hosts[:10]], sep="\n    ")
    report["remotes"] = remotes
    report["ext_hosts"] = hosts
    harvest(mf, bag, "mf-runtime")

    # 4) топ app-чанков (крупные из /map + непохожие на loadShare)
    cands = [s for s in msrcs if "loadShare" not in s and "remoteEntry" in s]
    cands += [s for s in msrcs if "loadShare" not in s][:6]
    if remotes:
        cands = [r for r in remotes if r.startswith("/")] + cands
    seen, total, T0 = set(), 0, time.time()
    for s in cands[:8]:
        if total > 6_000_000 or time.time() - T0 > 330:
            break
        u = s if s.startswith("http") else "https://nspd.gov.ru" + s
        if u in seen:
            continue
        seen.add(u)
        st2, bb2 = rq(u, cap=2_000_000)
        total += len(bb2)
        print(f"4) {s[-70:]} -> {st2} {len(bb2)} B")
        if st2 == 200 and bb2:
            harvest(bb2.decode("utf-8", "replace"), bag, s)
            for m in re.findall(r'"(/assets/[A-Za-z0-9_.-]+\.js)"',
                                bb2.decode("utf-8", "replace"))[:12]:
                msrcs.append(m)

    eps = {k: sorted(v) for k, v in bag.items()}
    ranked = sorted(eps, key=lambda e: -len(eps[e]))
    print(f"\n5) кандидатов: {len(ranked)}")
    for e in ranked[:25]:
        print(f"   {len(eps[e]):>2}x  {e[:105]}")

    # 6) пробы: найденные + гадания
    guesses = [
        "/api/adar/search?query=78:5:1234:12",
        "/api/adar/pkk/features/1?bbox=30.28,59.85,30.30,59.86",
        "/api/pkk/features/1?bbox=30.28,59.85,30.30,59.86",
        "/api/features/1?bbox=30.28,59.85,30.30,59.86",
        "/api/geoportal/layers",
        "/api/layers",
        "/api/config",
        "/api/v2/search?query=Автово",
        "/api/adar/layers",
        "/api/egip/search?query=Автово",
    ]
    probes = []
    urls = []
    for e in ranked[:12]:
        u = e if e.startswith("http") else "https://nspd.gov.ru" + e
        u = re.sub(r"\{[^}]*\}", "1", u)
        urls.append(u)
    urls += ["https://nspd.gov.ru" + g for g in guesses]
    for u in urls[:22]:
        st3, bb3 = rq(u, timeout=(6, 12), cap=1200)
        tag = "JSON!" if (st3 == 200 and bb3[:1] in (b"{", b"[")) else ""
        probes.append({"url": u, "status": st3, "bytes": len(bb3), "json": bool(tag),
                       "sample": bb3[:200].decode("utf-8", "replace") if st3 == 200 else ""})
        print(f"   probe [{st3}] {tag:5} {u[:100]}")

    json.dump({"generated": dt.datetime.now(dt.UTC).isoformat(), "report": report,
               "endpoints": eps, "ranked": ranked, "probes": probes},
              open(OUT_JSON, "w"), ensure_ascii=False, indent=1)
    print(f"\n-> {OUT_JSON}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
