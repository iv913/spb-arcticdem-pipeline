#!/usr/bin/env python3
"""6.10b: релиз v1.1 — продукты мега-сшивки ассетами GitHub Release
(растр 473 МБ превышает лимит git 100 МБ; лимит ассета 2 ГиБ)."""
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
DL = Path("/tmp/my-project/download")
TAG = "v1.1"
ASSETS = [
    DL / "mosaic_6.00/spb_terra_fusion_6.00_A2.tif",
    GH / "download/mosaic_6.00/spb_terra_conf_6.00_A2.tif",
    GH / "download/spb_terra_flood_map_6.10.html",
    GH / "results/fusion_6.00/fusion_report_6.00.json",
    GH / "results/fusion_6.00/fill_zones_6.00_A1.geojson",
    GH / "results/fusion_6.00/fill_zones_6.00_A2.geojson",
    GH / "results/fusion_6.00/fill_zones_6.00_A3.geojson",
    GH / "results/fusion_6.00/fill_zones_6.00_A4.geojson",
]
BODY = """Мега-сшивка 6.00 + карта 6.10.

**Единая поверхность** (этап 6.00): мозаика 5.90 (2 м, БС-77) + лапласова
достройка всех лакун (мультисеточная, 8-512 м) с якорением по 1,060,975 точкам
(ATL06 canon v4 + 291 отметка НСПД/полевые, демпф 0.6, триаж 10 м).
Holdout-валидация в лакунах: MAD 0.301/0.282/0.250/0.255 м для A1-A4.

**Степени допущения** (дальность достройки): A1 <=250 м (96 км2),
A2 <=1 км (328 км2), A3 <=3 км (679 км2), A4 вся сетка (1941 км2).

**Карта затоплений 6.10** (самодостаточный HTML, подложка Esri Dark Gray):
бегунок уровня +1...+5 м, бегунок степени допущения A1-A4, раздельный учёт
«из данных / достроено», слои источников (ATL06, отметки, зоны тиража,
пропуска, эталон Б. пр. В.О.).

Ассеты: растр A2 (Int16, см, БС-77, nodata -32768), confidence-классы
(0/1-4 достройка/10-13 мозаика), GeoJSON достроенных зон per A, отчёт.
Базовые зоны затоплений: этап 5.96 (тэг v1.0)."""


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
                                    name="v1.1 — 6.00+6.10: единая поверхность и карта со степенью допущения",
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
        print(f"  {'OK ' if ok else 'ERR'} {p.name} ({size/2**20:.0f} MiB) "
              f"{'' if ok else r.text[:200]}")
        sys.stdout.flush()
    print('DONE' if ok_all else 'PARTIAL')


if __name__ == '__main__':
    main()
