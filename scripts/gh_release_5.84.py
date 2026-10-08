#!/usr/bin/env python3
"""5.84: бэкап продуктов мозаики в GitHub Releases (третий офсайт-слой;
Drive-аплоад недоступен: OOB-поток отозван Google, refresh-токены мертвы).
PAT берётся из /home/z/my-project/.pat. Лимит GH: 2 GiB на ассет."""
import json, sys
from pathlib import Path
import requests

REPO = "iv913/spb-arcticdem-pipeline"
PAT = Path("/home/z/my-project/.pat").read_text().strip()
HDR = {"Authorization": f"token {PAT}",
       "Accept": "application/vnd.github+json"}
HOME = Path(__file__).resolve().parents[1]
TAG = "5.84-mosaic"
ASSETS = [
    HOME / "download/mosaic_5.84/spb_hybrid_2m_mosaic_5.84.tif",
    HOME / "download/mosaic_5.84/spb_quality_5.84.tif",
    HOME / "download/mosaic_5.84/trust_mask_2m_5.84.tif",
    HOME / "download/mosaic_5.84/density_grid_5.84.tif",
    HOME / "results/mosaic_5.84.json",
    HOME / "results/mosaic_5.84.md",
    HOME / "results/zones_summary_5.83.csv",
]


def get_existing_release():
    r = requests.get(f"https://api.github.com/repos/{REPO}/releases/tags/{TAG}",
                     headers=HDR, timeout=30)
    return r.json() if r.status_code == 200 else None


def create_release():
    body = ("5.84: мозаика 21 зонного продукта 2м (14 зон 5.83 + 7 регенератов "
            "5.76-5.79) + quality (n_cov/sigma/canopy30) + density trust mask. "
            "Конвенция: Int16 см БС-77, nodata -32768; quality nodata 65535. "
            "Отчёт: results/mosaic_5.84.md")
    r = requests.post(f"https://api.github.com/repos/{REPO}/releases",
                      headers=HDR, timeout=30,
                      json=dict(tag_name=TAG, target_commitish="main",
                                name="5.84 — мозаика 2м + trust mask",
                                body=body, draft=False, prerelease=False))
    r.raise_for_status()
    return r.json()


def upload(url_tmpl, path):
    name = path.name
    size = path.stat().st_size
    if size >= 2 * 1024**3:
        print(f"  SKIP {name}: {size/2**30:.2f} GiB >= 2 GiB (лимит GH)")
        return False
    r = requests.post(url_tmpl.replace("{?name,label}", f"?name={name}"),
                      headers={**HDR, "Content-Type":
                               "application/octet-stream"},
                      data=path.open("rb"), timeout=3600)
    ok = r.status_code == 201
    print(f"  {'OK ' if ok else 'ERR'} {name} ({size/2**20:.0f} MiB) "
          f"{r.status_code if not ok else ''}")
    return ok


def main():
    rel = get_existing_release()
    if rel is None:
        rel = create_release()
        print(f"release создан: id={rel['id']} tag={TAG}")
    else:
        print(f"release существует: id={rel['id']}")
    url = rel["upload_url"]
    have = {a["name"] for a in rel.get("assets", [])}
    ok_all = True
    for p in ASSETS:
        if not p.exists():
            print(f"  НЕТ файла {p.name}")
            ok_all = False
            continue
        if p.name in have:
            print(f"  есть {p.name}")
            continue
        ok_all &= upload(url, p)
    print("BACKUP", "OK" if ok_all else "ЧАСТИЧНО")
    sys.exit(0 if ok_all else 2)


if __name__ == "__main__":
    main()
