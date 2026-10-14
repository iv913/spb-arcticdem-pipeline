#!/usr/bin/env python3
"""6.20: релиз v1.2 — карта с клипом берега + DXF-якоря + экстраполяция."""
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
TAG = "v1.2"
ASSETS = [
    GH / "download/spb_terra_flood_map_6.20.html",
    GH / "results/fusion_6.20/fusion_report_6.20.json",
    GH / "results/fusion_6.20/clip_stats_6.20.json",
    GH / "results/fusion_6.20/a1_vs_a4_gavan_decabr_6.20.json",
    GH / "results/fusion_6.20/osm_water_6.20.geojson",
    GH / "results/fusion_6.20/fill_zones_6.20_A1_clip.geojson",
    GH / "results/fusion_6.20/fill_zones_6.20_A2_clip.geojson",
    GH / "results/fusion_6.20/fill_zones_6.20_A3_clip.geojson",
    GH / "results/fusion_6.20/fill_zones_6.20_A4_clip.geojson",
]
BODY = """6.20 — береговая линия + DXF-якоря + слой экстраполяции.

**Клип береговой линии**: все зоны затопления (база 5.96 + достроенные
6.20 A1-A4) обрезаны сушей = bbox − coastline-полигоны OSM
(osmdata.openstreetmap.de, ODbL, данные 2026-10-14). «Стрелы» достройки
в заливе у Ломоносова/Кронштадта убраны: A2 +1 м 5.7→1.7 км², A3 +1 м
19.8→2.3 км² (−88%), A4 +1 м 54.3→21.4 км². Каверзы: Котлин в
coastline-полигонах не выделен сушей (и данных мозаики нет) — зоны на
острове не показываются; восточнее ~30.235 (дельта Невы) клип не действует.

**DXF «Дачное» как якоря**: полный набор 2 326 точек съёмки (Дудергофская
возвышенность, z 29-47 м, 10 км от данных, только A4) в сшивке 6.20.
Локальный триаж ±6 м (отброшено 131). Holdout DXF (n=1 038): MAD 1.95 м —
до якорения поверхность завышала ~10.5 м (улучшение ×5). Якоря всего:
1 063 267 (canon v4 + отметки + DXF).

**A4 vs A1 (разница = экстраполяция)**: Гавань В.О. — +3 м 1.58 vs 0.54 км²
(×3, экстраполяция доминирует); о. Декабристов — 0.37 vs 0.30 км² (почти в
данных); эталон Большого пр. В.О. — сухой на всех уровнях/допущениях.

**Карта 6.20**: два бегунка (уровень, степень допущения) + слой «DXF
Дачное — якоря» + слой «Экстраполяция (A4−A1)» (красная штриховка) +
таблицы валидации и сравнения районов. Растр A2 не менялся (см. v1.1)."""


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
                                    name="v1.2 — 6.20: клип береговой линии + DXF-якоря + слой экстраполяции",
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
