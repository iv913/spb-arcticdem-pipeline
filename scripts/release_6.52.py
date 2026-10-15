#!/usr/bin/env python3
"""6.52: релиз v1.3 — ревизия: прибрежный барьер + DSM v2 + протокол ревизии."""
import sys
from pathlib import Path
import requests
import re
import subprocess


def _token():
    p = Path("/home/z/my-project/.pat")
    if p.exists():
        return p.read_text().strip()
    url = subprocess.run(
        ["git", "-C", "/tmp/my-project/gh-clone", "remote", "get-url", "origin"],
        capture_output=True, text=True, timeout=15).stdout.strip()
    m = re.search(r":(gh[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+)@", url)
    return m.group(1) if m else None


REPO = "iv913/spb-arcticdem-pipeline"
TOK = _token()
assert TOK, "нет PAT и токена в remote origin"
PAT = TOK
HDR = {"Authorization": f"token {PAT}",
       "Accept": "application/vnd.github+json"}
GH = Path("/tmp/my-project/gh-clone")
TAG = "v1.3"
ASSETS = [
    Path("/home/z/my-project/download/spb_terra_flood_map_6.52.html"),
    Path("/home/z/my-project/download/spb_revision_protocol_6.52.md"),
    Path("/home/z/my-project/download/atayanc_6.52/flood_plot_6.52.geojson"),
    GH / "results/fusion_6.52/barrier_stats_6.52.json",
    GH / "results/fusion_6.52/flood_levels_5.96_barrier16.geojson",
]
BODY = """Ревизия всего проекта по проблемам диагностики 6.51 («почему набережная топится на +1 м»).

**Причина ленты**: сетка flood-модели ~8 м и fusion 2 м не разрешают гранитную стенку набережной — вместо ступеньки «гранит 2.5–3.5 м → вода 0» береговая ячейка читается 0.3–1.0 м; плюс щели между OSM-водой и линией причала. Реальные отметки гранита: медиана 2.76 м (n=253), ниже +1.0 м — ни одной.

**Город 6.52**: из всех зон (база 5.96 + fill A1–A4) вычтена прибрежная полоса 16 м, обрывки <0.5 га отброшены. Эффект: +1 м 1.91→1.70 км², +1.5 м 9.02→6.69, +2 м 15.36→11.51, +3 м 39.52→33.44, +5 м 108.83→100.18; доля зоны в 30 м от воды на +1 м 16%→6%.

**Участок Атаянс 6.52**: DSM v2 — ref-окно p25 только по суше, 2-проходная коррекция (медиана k=24 → отбраковка 55 выбросов → среднее), sigma 60 м; контроль по 332 отметкам bias −0.24 / MAD 0.42 м (всплесков нет). Затопление с барьером 12 м: +1 м 0.051→0.016 км² (−69%), +3 м 0.880→0.654, +5 м 1.519→1.373.

**Протокол ревизии** (spb_revision_protocol_6.52.md) — сводная таблица 16 проблем проекта (закрыт/принят/открыт), все цифры до/после, дорожная карта: мега-задача 5.97 (сшивка 2 м с лестницей допущения A1–A5), гидродинамика, Котлин, OSS-Drive."""


def get_release():
    r = requests.get(f"https://api.github.com/repos/{REPO}/releases/tags/{TAG}",
                     headers=HDR, timeout=30)
    return r.json() if r.status_code == 200 else None


def main():
    rel = get_release()
    if rel is None:
        r = requests.post(f"https://api.github.com/repos/{REPO}/releases",
                          headers=HDR, timeout=60,
                          json=dict(tag_name=TAG, target_commitish="main",
                                    name="v1.3 — 6.52: ревизия (прибрежный барьер + DSM v2 + протокол)",
                                    body=BODY, draft=False, prerelease=False))
        r.raise_for_status()
        rel = r.json()
        print(f"release создан: id={rel['id']}")
    else:
        print(f"release существует: id={rel['id']}")
    url = rel["upload_url"]
    have = {a["name"] for a in rel.get("assets", [])}
    ok_all = True
    for p in ASSETS:
        if p.name in have:
            print(f"  SKIP {p.name} (уже есть)")
            continue
        size = p.stat().st_size
        r = requests.post(url.replace("{?name,label}", f"?name={p.name}"),
                          headers={**HDR, "Content-Type":
                                   "application/octet-stream"},
                          data=p.open("rb"), timeout=3600)
        ok = r.status_code == 201
        ok_all &= ok
        print(f"  {'OK ' if ok else 'ERR'} {p.name} ({size/2**20:.1f} MiB) "
              f"{'' if ok else r.text[:200]}")
        sys.stdout.flush()
    print('DONE' if ok_all else 'PARTIAL')


if __name__ == '__main__':
    main()
