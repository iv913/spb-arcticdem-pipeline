# SPb ArcticDEM Pipeline

Построение 2-метровой модели рельефа Санкт-Петербурга по ArcticDEM v4.1 + Copernicus DEM 30m + ICESat-2 ATL08 (43K точек) + 202 паспортные точки.

## Метрики (v4: PMF + 4-tile Copernicus N59+N60)

| Метрика | Значение |
|---|---|
| Passport MAD | 1.552 м |
| Passport ±2м | 79% |
| ICESat HOLDOUT MAD | 0.316 м |
| Land area | 1941 км² |
| Water area | 590 км² |

## Модуль 13 — Невская губа

| H, м | V, млн м³ | Событие |
|---|---|---|
| 1.6 | 531.4 | КЗС порог |
| 2.93 | 976.5 | 1955 нагон |
| 3.80 | 1268.2 | 1924 нагон |
| 4.21 | 1405.7 | 1824 нагон |

## Структура

```
scripts/
├── spb_full_run_v2.py          # Главный pipeline (PMF + train/holdout split)
├── 13_nevskaya_guba.py         # Модуль 13: объём Невской губы
├── test_guba.py                # 12 unit-тестов
├── lib_ground.py               # PMF, anchor_field, build_prior, accept_dsm
├── lib_geo.py                  # true_pixel_m (поправка EPSG:3413)
├── lib_vertical.py             # Вертикальная привязка
├── lib_profile.py              # Профиль улиц
├── spb_check_geoshift.py       # Проверка сдвига геопривязки
├── spb_check_geoshift_per_tile.py  # Per-tile сдвиг + highways
├── spb_water_mask_analysis.py  # Анализ маски воды
├── spb_create_bay_polygon.py   # Полигон Невской губы
├── spb_missing_17.py           # Анализ 17 недостающих точек
├── gdrive_upload_with_token.py # Загрузка на Google Drive
├── persist.sh                  # Бэкап на Alibaba OSS
└── download_all.sh             # Скачивание всех файлов

results/
├── spb_qa_2m.json              # Финальные метрики v4
├── nevskaya_guba_qa.json       # V(H) для 12 уровней
├── namyv_impact.json           # Влияние намыва В.О.
├── spb_passport_validation_v4.csv  # 202 точки
├── spb_geoshift_per_tile.json  # 121 2D сдвигов
└── spb_water_mask_analysis.json    # Анализ маски воды
```

## Большие файлы

Файлы >100 MB (TIF, Copernicus, OSM) — на Google Drive:
https://drive.google.com/drive/folders/1kYNn--NsobdYCxzd1_7k2MdcrUOnl1XK

## Запуск

```bash
pip install rasterio geopandas shapely pyproj scipy
python3 spb_full_run_v2.py    # ~45 мин с checkpointing
python3 13_nevskaya_guba.py   # модуль 13
python3 test_guba.py          # 12/12 тестов
```
