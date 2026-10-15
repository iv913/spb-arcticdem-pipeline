#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""6.52c/d — генераторы карт 6.52 из проверенных скриптов 6.21/6.50e
программным патчем строк (город + участок). Пишет build_map_6.52.py и
atayanc_6.52_map.py в scripts/."""
import os

S = '/home/z/my-project/scripts'

# ================= ГОРОД: build_map_6.21.py -> build_map_6.52.py
src = open(f'{S}/build_map_6.21.py', encoding='utf-8').read()
R = [
    ('"""6.21 — Карта «МЕГА-СШИВКА v2»: затопления + клип берега И ДЕЛЬТЫ НЕВЫ.',
     '"""6.52 — Карта «МЕГА-СШИВКА v3 (РЕВИЗИЯ)»: клип воды + прибрежный барьер 16 м.'),
    ("FUS = '/tmp/my-project/results/fusion_6.21'",
     "FUS = '/tmp/my-project/results/fusion_6.52'"),
    ("f'{FUS}/flood_levels_5.96_clip2.geojson'",
     "f'{FUS}/flood_levels_5.96_barrier16.geojson'"),
    ("f'{FUS}/clip2_stats_6.21.json'", "f'{FUS}/barrier_stats_6.52.json'"),
    ("f'{FUS}/fill_zones_6.20_A{A}_clip2.geojson'",
     "f'{FUS}/fill_zones_6.20_A{A}_barrier16.geojson'"),
    ("OUT = f'{GH}/download/spb_terra_flood_map_6.21.html'",
     "OUT = f'{GH}/download/spb_terra_flood_map_6.52.html'"),
    ("OUT_HOME = '/home/z/my-project/download/spb_terra_flood_map_6.21.html'",
     "OUT_HOME = '/home/z/my-project/download/spb_terra_flood_map_6.52.html'"),
    ('<title>СПб — единая поверхность 6.21: затопления + клип берега и дельты Невы</title>',
     '<title>СПб — единая поверхность 6.52 (ревизия): клип воды + прибрежный барьер</title>'),
    ('<h1>Единая поверхность 6.21 — затопления СПб</h1>',
     '<h1>Единая поверхность 6.52 — затопления СПб (ревизия)</h1>'),
    ('клип по воде: берег и дельта Невы</b>',
     'клип по воде + прибрежный барьер 16 м</b>'),
    ('<span class="badge">этап 6.21</span><span class="badge">клип берега + дельты</span>',
     '<span class="badge">этап 6.52 · ревизия</span><span class="badge">клип берега + дельты</span><span class="badge">барьер уреза 16 м</span>'),
    ('Площадь (после клипа по воде): ',
     'Площадь (клип по воде + барьер 16 м): '),
    ('мозаика 5.90, модель «чаша», вода OSM: берег + дельта',
     'мозаика 5.90, «чаша», вода OSM + барьер уреза 16 м (ревизия 6.52)'),
    ('Достроено (этап 6.21, клип по воде)',
     'Достроено (этап 6.52, клип + барьер 16 м)'),
    ('<p><b>Степень допущения</b>',
     '''<p><b>Ревизия 6.52 — прибрежный барьер 16 м.</b> Диагностика (этап 6.51) показала: зоны +1…+2 м вдоль гранитных набережных — это лента смешанных пикселей сетки ~8 м (гранит 2.5–3.5 м, слитый с водой 0 м, читается 0.3–1.0 м) плюс щели между OSM-водой и линией причала. Реальные отметки гранита — 2.48–3.08 м (медиана 2.76, n=253): физически +1 м их не топит. Фикс: из всех зон вычтена полоса 16 м вдоль воды, обрывки &lt; 0.5 га отброшены. Эффект: +1 м 1.91→1.70 км², +1.5 м 9.02→6.69, +2 м 15.36→11.51, +3 м 39.52→33.44, +5 м 108.83→100.18; доля зоны в 30 м от воды на +1 м 16%→6%. Ограничение честности: сетка ~8 м и «рампа» уреза в самой поверхности остаются — барьер скрывает ленту, а не чинит высоты.</p>
    <p><b>Степень допущения</b>'''),
    ('этапы 6.00→6.21 ·', 'этапы 6.00→6.52 ·'),
]
for a, b in R:
    assert a in src, 'NOT FOUND: ' + a[:70]
    src = src.replace(a, b)
open(f'{S}/build_map_6.52.py', 'w', encoding='utf-8').write(src)
print('build_map_6.52.py OK')

# ================= УЧАСТОК: atayanc_6.50e_map.py -> atayanc_6.52_map.py
src = open(f'{S}/atayanc_6.50e_map.py', encoding='utf-8').read()
R = [
    ('"""6.50e — карта поверхности 6.50: план + DSM-хиллшейд + здания + 332 отметки +\nзатопление +1..+5 (суша по воде OSM 6.21). Выход: atayanc_plot_map_6.50.html."""',
     '"""6.52d — карта поверхности 6.52 (ревизия): DSM v2 (land-only ref, 2-проходная\nкоррекция, sigma 60) + затопление с прибрежным барьером 12 м. Выход: atayanc_plot_map_6.52.html."""'),
    ("OUTD = '/tmp/my-project/results/atayanc_6.50'",
     "OUTD = '/tmp/my-project/results/atayanc_6.52'"),
    ("with rasterio.open(f'{OUTD}/atayanc_plot_dsm_1m_6.50.tif') as ds:",
     "with rasterio.open(f'{OUTD}/atayanc_plot_dsm_1m_6.52.tif') as ds:"),
    ("im_h.save(f'{OUTD}/dsm_hill_6.50.png')",
     "im_h.save(f'{OUTD}/dsm_hill_6.52.png')"),
    ("im_p.save(f'{OUTD}/plan_web_6.50.png')",
     "im_p.save(f'{OUTD}/plan_web_6.52.png')"),
    ("open(f'{OUTD}/flood_plot_6.50.geojson', 'w')",
     "open(f'{OUTD}/flood_plot_6.52.geojson', 'w')"),
    ("open(f'{OUTD}/atayanc_plot_map_6.50.html', 'w')",
     "open(f'{OUTD}/atayanc_plot_map_6.52.html', 'w')"),
    ("const plan = L.imageOverlay('plan_web_6.50.png', __PLANB__, {opacity:0.55});",
     "const plan = L.imageOverlay('plan_web_6.52.png', __PLANB__, {opacity:0.55});"),
    ("const dsm  = L.imageOverlay('dsm_hill_6.50.png', __DSMB__, {opacity:0.65});",
     "const dsm  = L.imageOverlay('dsm_hill_6.52.png', __DSMB__, {opacity:0.65});"),
    ('<title>Атаянс 2013 — поверхность участка (6.50)</title>',
     '<title>Атаянс 2013 — поверхность участка (6.52, ревизия)</title>'),
    ('<b>6.50 · Поверхность участка по плану «Атаянс-2013» — перепричёсано</b>\n(332 отметки, 117 цифр исправлено после вычитки апскейла; DTM = fusion A2 + коррекционное поле по отметкам)',
     '<b>6.52 · Поверхность участка — РЕВИЗИЯ</b> (DSM v2: ref только по суше, 2-проходная коррекция без всплесков; затопление с барьером 12 м от воды — лента уреза 6.51 убрана; 332 отметки 6.50)'),
    ('<label class="lay"><input type="checkbox" id="lDsm" checked> DTM/DSM 6.50 (1 м)</label>',
     '<label class="lay"><input type="checkbox" id="lDsm" checked> DTM/DSM 6.52 (1 м)</label>'),
]
for a, b in R:
    assert a in src, 'NOT FOUND: ' + a[:70]
    src = src.replace(a, b)
open(f'{S}/atayanc_6.52_map.py', 'w', encoding='utf-8').write(src)
print('atayanc_6.52_map.py OK')
print('ГОТОВО патчер')
