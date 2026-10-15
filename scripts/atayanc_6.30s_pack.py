#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""6.30s — упаковка и сохранение: home download, PolarFS, gh-clone + push, worklog."""
import json, os, shutil, subprocess

SRC = '/tmp/my-project/results/atayanc_6.30'
HOME_DL = '/home/z/my-project/download/atayanc_6.30'
POLAR_DL = '/tmp/my-project/download/atayanc_6.30'
GH = '/tmp/my-project/gh-clone'

FILES = ['atayanc_plot_map_6.30.html', 'plan_web_6.30.png', 'dsm_hill_6.30.png',
         'atayanc_plot_dsm_1m_6.30.tif', 'atayanc_plan_georef_6.30.tif',
         'plan_buildings_6.30.geojson', 'plan_marks_6.30.geojson', 'flood_plot_6.30.geojson',
         'georef_6.30.json', 'dsm_meta_6.30.json']

README = """# 6.30 — Поверхность участка по ситуационному плану «Атаянс-2013»

Лист: atayanc-2013-01.jpg (НПФ «Атаянс», 2013) — коридор видимости Князь-Владимирского
собора в панораме стрелки В.О. со стороны Дворцовой набережной.

## Продукты
- **atayanc_plot_map_6.30.html** (+2 PNG рядом) — интерактивная карта: план (ползунок
  прозрачности), DSM 1 м (гипсометрия+холлшейд), здания плана с высотами, 50 отметок
  земли с валидацией против рельефа 6.00 A2, коридор, затопление +1..+5 м БС-77.
- **atayanc_plot_dsm_1m_6.30.tif** — DSM участка 1 м, Int16 см, EPSG:4326, БС-77.
  Территория = fusion A2 (6.00); крыши = отметка земли (кольцо 6-18 м) + высота
  (план → OSM height/levels). 5021 здание (239 плановых, 4809 OSM-only).
- **atayanc_plan_georef_6.30.tif** — план, геопривязанный аффиной по 5 GCP
  (собора, Петропавловский собор, Биржевой мост, ростральная колонна, Александровская
  колонна) + ~40 автопарам зданий; RMS ручных ~20 м, MAD 12 м.
- plan_buildings_6.30.geojson — здания: h_plan (18 чисел с плана), h_osm, источник,
  iou снапа, проектные (тёмно-красные) флагом darkred.
- plan_marks_6.30.geojson — 50 отметок земли (БС-77): h_bs77, h_fusion, diff.
- flood_plot_6.30.geojson — затопление +1..+5 м (суша, полигоны ≥300 м2).

## Метрики
- Валидация отметок vs fusion A2: bias +0.33 м, MAD 0.98 м (n=50) — датум согласован
  (БС-77), расхождение в пределах точности эктранной пиковки (±5-8 м) и локального
  шума A2.
- Масштаб листа ~0.78 м/px (X) / 0.75 (Y), поворот -0.3°. Шкала на листе декоративная
  (не соответствует содержимому).
- СЗ-угол листа (стадион им. Ленина, Ждановка, СК «Юбилейный») — отдельный фрагмент
  листа со своим смещением/поворотом (~100-250 м, поворот ~10°); из продукта
  исключён (несматанные СЗ-здания не попали в DSM), геометрия покрывается OSM.

## Затопление участка (суша, БС-77)
+1 м 0.059 км2; +2 м 0.181; +3 м 0.549; +4 м 0.873; +5 м 1.063 км2.

## Методика
1. Геопривязка GCP (5 ручных + автопары по центроидам зданий, робастный аффинный фит).
2. Здания: HSV-маска плана -> полигоны -> снап к OSM (IoU>=0.45 -> геометрия OSM).
3. Высоты: числа плана (визуальное чтение кропов) > OSM height > OSM levels*3.1.
4. DSM: territory fusion A2 (6.00) + крыши (кольцевая земля + высота); вода A2 -> nodata.
5. Затопление: bathtub по DSM-территории, вода OSM 6.21 исключена, связность >=300 м2.

Ограничения: точность геопривязки ~15-25 м (слабые места — СЗ-фрагмент); высоты плана —
проектные 2013 г. (сейчас построено — подтверждено OSM-снапом); отметки считаны с
экрана (возможны единичные ошибки чтения ±0.1 м).
"""

os.makedirs(HOME_DL, exist_ok=True)
os.makedirs(POLAR_DL, exist_ok=True)
open(f'{SRC}/README.md', 'w').write(README)
for f in FILES + ['README.md']:
    for dst in (HOME_DL, POLAR_DL):
        shutil.copy2(f'{SRC}/{f}', f'{dst}/{f}')
        print(f'{dst}/{f}: {os.path.getsize(f"{dst}/{f}")//1024} КБ')

# скрипты -> PolarFS + gh-clone
for s in os.listdir('/home/z/my-project/scripts'):
    if s.startswith('atayanc_6.30'):
        shutil.copy2(f'/home/z/my-project/scripts/{s}', f'/tmp/my-project/scripts/{s}')
        shutil.copy2(f'/home/z/my-project/scripts/{s}', f'{GH}/scripts/{s}')
# результаты (без тяжёлых tmp) -> gh-clone/results/atayanc_6.30
os.makedirs(f'{GH}/results/atayanc_6.30', exist_ok=True)
for f in FILES + ['README.md', 'qa_map_overview.png', 'qa_map_zoom_spit.png',
                  'qa_georef_zoom.png', 'qa_zones.png', 'qa_nw_local.png']:
    p = f'{SRC}/{f}'
    if os.path.exists(p):
        shutil.copy2(p, f'{GH}/results/atayanc_6.30/{f}')
print('скрипты и результаты разложены')
