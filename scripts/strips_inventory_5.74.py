#!/usr/bin/env python3
"""
5.74-B: инвентаризация 2м-стрип ArcticDEM (s2s041) по bbox всего СПб
с пригородами — задел городского расширения карты (2м).

Отличие от download_arcticdem_strips.py: НЕ скачивает, только инвентарь
(сколько стрипов, качество по годам, покрытие мозаикой лучших стрипов
per-pairname). Выход: results/arcticdem_strips_inventory_5.74.json +
CSV. Плюс: оценка покрытия 2м-мозаики v4.1 (tiles 32_73, 32_74...)
через PGC STAC collection arcticdem-mosaics-v4.1-2m.
"""
import json, sys
from pathlib import Path
from datetime import datetime
import pandas as pd

BBOX_SPB_FULL = [29.20, 59.55, 31.20, 60.30]   # СПб с муроспб-пригородами
BBOX_SPB_CITY = [30.05, 59.70, 30.85, 60.05]   # городская черта примерно
OUT = Path('/home/z/my-project/repo/results')

def search(collection, bbox, name):
    import pystac_client
    cat = pystac_client.Client.open("https://stac.pgc.umn.edu/api/v1/")
    try:
        items = list(cat.search(collections=[collection], bbox=bbox).items())
    except Exception as e:
        print(f"  {name}: SEARCH FAIL {str(e)[:120]}")
        return []
    print(f"  {name}: {len(items)} items")
    return items


def main():
    rows = []
    for label, bbox in (('city', BBOX_SPB_CITY), ('full', BBOX_SPB_FULL)):
        items = search("arcticdem-strips-s2s041-2m", bbox, f"strips-2m {label}")
        for it in items:
            p = it.properties
            rows.append(dict(scope=label, kind='strip2m', id=it.id[:80],
                             datetime=p.get('datetime', '')[:10],
                             pairname=(p.get('pgc:pairname') or '')[:40],
                             cloud=p.get('pgc:cloud_area_percent'),
                             valid=p.get('pgc:valid_area_percent'),
                             xtrack=p.get('pgc:is_xtrack', False),
                             dem_href=(it.assets.get('dem') or {}).href if it.assets.get('dem') else ''))
    # мозаика 2м v4.1: какие тайлы покрывают СПб
    mos = search("arcticdem-mosaics-v4.1-2m", BBOX_SPB_FULL, "mosaic-v4.1-2m")
    mosaic_ids = [it.id for it in mos]
    df = pd.DataFrame(rows)
    if len(df):
        df.to_csv(OUT / 'arcticdem_strips_inventory_5.74.csv', index=False)
        for scope in df.scope.unique():
            s = df[df.scope == scope]
            yrs = pd.to_datetime(s.datetime, errors='coerce').dt.year
            print(f"  [{scope}] стрипов {len(s)}, пар {s.pairname.nunique()}, "
                  f"годы {int(yrs.min())}-{int(yrs.max())}, "
                  f"valid>=50: {(s.valid >= 50).mean():.0%}, "
                  f"cloud<=10: {(s.cloud <= 10).mean():.0%}")
    meta = dict(
        date=datetime.now().isoformat(),
        bbox_full=BBOX_SPB_FULL, bbox_city=BBOX_SPB_CITY,
        strips_city=int((df.scope == 'city').sum()) if len(df) else 0,
        strips_full=int((df.scope == 'full').sum()) if len(df) else 0,
        unique_pairs_full=int(df[df.scope == 'full'].pairname.nunique()) if len(df) else 0,
        mosaic_v41_tiles=mosaic_ids,
        years=sorted(set(pd.to_datetime(df.datetime, errors='coerce').dt.year.dropna().astype(int))) if len(df) else [])
    (OUT / 'arcticdem_strips_inventory_5.74.json').write_text(
        json.dumps(meta, ensure_ascii=False, indent=1))
    print(json.dumps({k: v for k, v in meta.items() if k != 'bbox_full'},
                     ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
