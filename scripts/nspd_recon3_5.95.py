#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""5.95f — Recon v3: куки/заголовки против 403 + офлайн-разбор map-чанков.

1) requests.Session: GET / -> куки; GET /api/config с Origin+XRW+Referer;
2) статусы /api/auth/*, /api/user, /api/me (форма гейта);
3) сохранение map-чанков -> локальный grep: remoteEntry, api-строки, tile-хосты;
4) пробы remoteEntry-путей /map/assets/remoteEntry.js и вариаций.

Выход: results/nspd_api_recon3_5.95.json + data/nspd_proxy_5.95/chunks/*.
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
                     "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")}
CH = "data/nspd_proxy_5.95/chunks"
OUT_JSON = "results/nspd_api_recon3_5.95.json"
ORDER = ["217.114.12.153:443", "88.204.22.105:8080", "62.113.114.246:8443",
         "95.52.246.169:1082"]
os.makedirs(CH, exist_ok=True)


def rq(url, timeout=(8, 14), save_as=None, extra=None, sess=None):
    s = sess or requests
    hdr = dict(UA)
    if extra:
        hdr.update(extra)
    for px in ORDER:
        try:
            r = s.get(url, proxies={"http": f"http://{px}", "https": f"http://{px}"},
                      timeout=timeout, headers=hdr, verify=False, allow_redirects=True)
            time.sleep(0.4)
            if 200 <= r.status_code < 400 and save_as:
                open(save_as, "wb").write(r.content)
            return r.status_code, r.content, r.headers
        except Exception:
            time.sleep(0.3)
    return None, b"", {}


def grep(txt, src, bag):
    pats = [
        r'"(/api/[A-Za-z0-9_\-./?=&{}%]+)"',
        r'"([A-Za-z0-9_\-./]*remoteEntry[A-Za-z0-9_.-]*\.js)"',
        r'"(/assets/[A-Za-z0-9_.-]+\.js)"',
        r'https?://[A-Za-z0-9.-]+/[A-Za-z0-9_\-./{}%]+(?:tiles|tile)[A-Za-z0-9_\-./{}%]*',
        r'(wss?://[A-Za-z0-9._/-]+)',
        r'(adar[A-Za-z0-9_\-./]*)',
        r'(geoserver[A-Za-z0-9_\-./]*)',
        r'(ows[A-Za-z0-9_?=&\-./]*)',
    ]
    for p in pats:
        for m in re.findall(p, txt):
            if isinstance(m, tuple):
                m = m[0]
            if len(str(m)) < 140:
                bag.setdefault(str(m), set()).add(src)


def main():
    print(f"[5.95f] recon v3, {dt.datetime.now(dt.UTC):%H:%M} UTC")
    bag, out = {}, {}

    # 1) куки-сессия против 403
    sess = requests.Session()
    st, bb, hh = rq("https://nspd.gov.ru/", sess=sess)
    cookies = sess.cookies.get_dict()
    print(f"1) GET / -> {st}, куки: {list(cookies)[:5]}")
    out["cookies"] = cookies
    st2, bb2, _ = rq("https://nspd.gov.ru/api/config", sess=sess,
                     extra={"Origin": "https://nspd.gov.ru",
                            "Referer": "https://nspd.gov.ru/map",
                            "X-Requested-With": "XMLHttpRequest",
                            "Accept": "application/json"})
    print(f"   /api/config (с куки+Origin) -> {st2}")
    print("   body:", bb2[:300].decode("utf-8", "replace"))
    out["api_config"] = {"status": st2, "body": bb2[:400].decode("utf-8", "replace")}

    # 2) форма гейта: auth/user
    auths = {}
    for p in ["/api/auth/session", "/api/auth/token", "/api/user", "/api/me",
              "/api/v1/user/info"]:
        sta, bba, _ = rq("https://nspd.gov.ru" + p, sess=sess,
                         extra={"Referer": "https://nspd.gov.ru/map",
                                "Accept": "application/json"})
        auths[p] = {"status": sta, "body": bba[:200].decode("utf-8", "replace")}
        print(f"2) {p} -> {sta}")
    out["auth_probes"] = auths

    # 3) map-чанки офлайн
    chunks = ["/assets/map-CboobsaJ.js", "/assets/map/assets/hostInit-C6bjXNUq.js",
              "/assets/map/assets/index-CfzF3PuT.js",
              "/assets/map/assets/virtualExposes-DqruWeZv.js"]
    for c in chunks:
        stc, bbc, _ = rq("https://nspd.gov.ru" + c, save_as=f"{CH}/{os.path.basename(c)}")
        print(f"3) {c} -> {stc} {len(bbc)} B")
        if stc == 200 and bbc:
            grep(bbc.decode("utf-8", "replace"), c, bag)

    eps = {k: sorted(v) for k, v in bag.items()}
    js_assets = [k for k in eps if k.endswith(".js")]
    interesting = [k for k in eps if "/api/" in k or "adar" in k.lower()
                   or "tile" in k.lower() or "geoserver" in k.lower()
                   or "ows" == k[:3].lower() or k.startswith("wss")]
    print(f"\n4) строк найдено: {len(eps)}; интересных: {len(interesting)}")
    for k in interesting[:25]:
        print(f"   {len(eps[k])}x {k[:110]}")
    print("   js-чанки на дозагрузку:", *[j[:90] for j in js_assets[:15]], sep="\n    ")
    out["strings"] = {k: len(v) for k, v in eps.items()}

    # 5) remoteEntry-пробы
    trials = ["/map/remoteEntry.js", "/map/assets/remoteEntry.js",
              "/assets/map/remoteEntry.js", "/remoteEntry.js"]
    tr = {}
    for t in trials:
        stt, bbt, _ = rq("https://nspd.gov.ru" + t, timeout=(6, 10))
        tr[t] = {"status": stt, "bytes": len(bbt)}
        print(f"5) {t} -> {stt} ({len(bbt)} B)")
    out["remote_tries"] = tr

    out["generated"] = dt.datetime.now(dt.UTC).isoformat()
    json.dump(out, open(OUT_JSON, "w"), ensure_ascii=False, indent=1)
    print(f"\n-> {OUT_JSON}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
