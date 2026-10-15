#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""6.52 — Карта «МЕГА-СШИВКА v3 (РЕВИЗИЯ)»: клип воды + прибрежный барьер 16 м.

Новое против 6.20 (скрин пользователя: пятно затопления НА Неве у Дворцового
моста, кольцо вокруг Заячьего острова):
  · osm_water_6.20 (coastline OSM) кончался на 30.2348 — дельта Невы не
    клипилась. Добавлены ВНУТРЕННИЕ ВОДЫ OSM: PBF BBBike SanktPetersburg
    (2026-10-10, ODbL) -> Нева (кольцо замкнуто хордой за вырезкой, Заячий
    о. вырезан keyhole), Малая Нева/Невки, Мойка/Фонтанка/Грибоедова/
    Обводный/Кронверкский, пруды — 1 416 полигонов, 37.6 км²;
  · все зоны (база 5.96 + fill A1..A4) переклиплены полной водой:
    на внутренних водах было 71.1 км² (база) / 28.2 км² (fill A2) —
    стало 0.17 / 0.03 км².

Выход: gh-clone/download/spb_terra_flood_map_6.21.html
"""
import csv
import glob
import gzip
import json
import math
import os
import random

GH = '/tmp/my-project/gh-clone'
FUS = '/tmp/my-project/results/fusion_6.52'
FUS20 = '/tmp/my-project/results/fusion_6.20'
LEAF_JS = '/tmp/my-project/scripts/data/leaflet/leaflet.min.js'
LEAF_CSS = '/tmp/my-project/scripts/data/leaflet/leaflet.min.css'
OUT = f'{GH}/download/spb_terra_flood_map_6.52.html'
OUT_HOME = '/home/z/my-project/download/spb_terra_flood_map_6.52.html'
random.seed(620)

CITY = (29.40, 59.65, 30.70, 60.25)
REF = (30.19, 59.895, 30.31, 59.97)
LEVELS = [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0]
COLORS = {1.0: '#2288d3', 1.5: '#2fa3b5', 2.0: '#2fb99a', 2.5: '#58c47a',
          3.0: '#96ca56', 3.5: '#c9c344', 4.0: '#e0a83a', 4.5: '#e5803a',
          5.0: '#e05252'}
A_INFO = {
    1: {'name': 'Строгий', 'dist': '≤ 250 м от данных'},
    2: {'name': 'Умеренный', 'dist': '≤ 1 км от данных'},
    3: {'name': 'Смелый', 'dist': '≤ 3 км от данных'},
    4: {'name': 'Максимум', 'dist': 'вся сетка'},
}


def log(m):
    print(m, flush=True)


# ------------------------------------------------------------ base 5.96 (клип2)
with open(f'{FUS}/flood_levels_5.96_barrier16.geojson') as f:
    base_fc = json.load(f)
with open(f'{FUS}/barrier_stats_6.52.json') as f:
    clip_stats = json.load(f)
base_areas = {k: v['after_km2'] for k, v in clip_stats['levels']['база 5.96'].items()}
log(f"base (клип2): {len(base_fc['features'])} уровней")

# ------------------------------------------------------------ fill 6.20 (клип2)
fill_fc = {}
fill_areas = {}
for A in (1, 2, 3, 4):
    with open(f'{FUS}/fill_zones_6.20_A{A}_barrier16.geojson') as f:
        fc = json.load(f)
    fill_fc[A] = {ft['properties']['level_m']: ft for ft in fc['features']}
    fill_areas[A] = {str(ft['properties']['level_m']): ft['properties']['area_km2']
                     for ft in fc['features']}
with open(f'{FUS20}/fusion_report_6.20.json') as f:
    report = json.load(f)
log('fill 4 уровней допущения (клип2) загружены')

# ------------------------------------------------------------ экстраполяция A4−A1
from shapely.geometry import shape as shp, mapping
DIFF = {}
for L in (1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0):
    if L not in fill_fc[4] or L not in fill_fc[1]:
        continue
    g4 = shp(fill_fc[4][L]['geometry'])
    g1 = shp(fill_fc[1][L]['geometry']) if L in fill_fc[1] else None
    if not g4.is_valid:
        g4 = g4.buffer(0)
    if g1 is not None and not g1.is_valid:
        g1 = g1.buffer(0)
    gd = g4.difference(g1) if g1 is not None else g4
    if gd.is_empty:
        continue
    DIFF[L] = mapping(gd)
log(f'экстраполяция A4−A1: {len(DIFF)} уровней')

# ------------------------------------------------------------ DXF Дачное (якоря)
def load_dxf():
    out = []
    with open(f'{GH}/scripts/data/topo_v2.csv') as f:
        rd = csv.reader(f)
        next(rd)
        for i, r in enumerate(rd):
            if i % 3:                      # сабсэмпл 1/3 -> ~775 точек
                continue
            out.append([round(float(r[6]), 6), round(float(r[5]), 6),
                        r[1], round(float(r[4]), 2)])
    return out

# ------------------------------------------------------------ sources
def load_canon():
    pts, dates = [], {}
    keep_out = 0
    with gzip.open(f'{GH}/scripts/dem_cache/densify_canon_5.93/unified.csv.gz', 'rt') as fh:
        rd = csv.DictReader(fh)
        for r in rd:
            la, lo = float(r['lat']), float(r['lon'])
            if not (CITY[0] <= lo <= CITY[2] and CITY[1] <= la <= CITY[3]):
                continue
            inref = REF[0] <= lo <= REF[2] and REF[1] <= la <= REF[3]
            if not inref:
                keep_out += 1
                if keep_out % 25:
                    continue
            d = r['date'][:6]
            if d not in dates:
                dates[d] = len(dates)
            pts.append([round(lo, 5), round(la, 5), dates[d], int(r['rgt']),
                        1 if r['cnf_src'] == 'high' else 0])
    return pts, sorted(dates)


def load_marks():
    out = []
    with open(f'{GH}/results/marks_val_5.95.csv') as f:
        for r in csv.DictReader(f):
            if r['src'] == 'topo_dxf_dachnoe':   # полный набор в слое DXF
                continue
            try:
                la, lo = float(r['lat']), float(r['lon'])
            except Exception:
                continue
            if not (CITY[0] <= lo <= CITY[2] and CITY[1] <= la <= CITY[3]):
                continue
            out.append([round(lo, 6), round(la, 6), r['src'], r['district'][:48],
                        round(float(r['z']), 2) if r['z'] else None,
                        round(float(r['dH']), 2) if r['dH'] else None,
                        r['note'][:40]])
    return out


def load_zone_bounds():
    zones = []
    for f in sorted(glob.glob(f'{GH}/download/products_5.83/hybrid_2m_*.tif')):
        tag = f.split('hybrid_2m_5.83_')[-1].replace('.tif', '')
        try:
            with __import__('rasterio').open(f) as s:
                b = s.bounds
                zones.append({'tag': tag, 'bbox': [round(b.left, 4), round(b.bottom, 4),
                                                   round(b.right, 4), round(b.top, 4)]})
        except Exception:
            pass
    verd = {}
    for line in open(f'{GH}/results/batch_progress_5.83.jsonl'):
        try:
            j = json.loads(line)
        except Exception:
            continue
        t = j.get('tag', '').replace('_5.83', '')
        verd[t] = (j.get('rule') or {}).get('reason', j.get('product', ''))
    for z in zones:
        v = verd.get(z['tag'].replace('_5.83', ''))
        if v:
            z['verdict'] = v
    return zones


log('loading sources...')
canon, canon_dates = load_canon()
log(f'canon kept {len(canon)}')
marks = load_marks()
dxf = load_dxf()
log(f'DXF якорей (сабсэмпл): {len(dxf)}')
zones = load_zone_bounds()
carr = json.load(open(f'{GH}/download/carriageway.geojson'))
gaps = json.load(open(f'{GH}/results/flood_zones_5.96/gap_sectors_5.96d_rects.json'))

SRC = {'canon': canon, 'canonDates': canon_dates, 'marks': marks, 'zones': zones,
       'carr': carr['features'][0]['geometry']['coordinates'], 'gaps': gaps,
       'dxf': dxf, 'dxfTotal': 2326}

# ------------------------------------------------------------ mini chart data
chart = {str(A): {str(L): [round(base_areas.get(str(L), 0.0), 1),
                           round(fill_areas[A].get(str(L), 0.0), 1)] for L in LEVELS}
         for A in (1, 2, 3, 4)}

with open(LEAF_JS) as f:
    leaf_js = f.read()
with open(LEAF_CSS) as f:
    leaf_css = f.read()

v = report['validation']
val_rows = ''.join(
    f"<tr><td>A{A} {A_INFO[A]['name']}</td><td>{A_INFO[A]['dist']}</td>"
    f"<td>{v[f'A{A}']['n_holdout']:,}</td><td>{v[f'A{A}']['mad']}</td>"
    f"<td>{v[f'A{A}'].get('dxf', {}).get('mad', '—')}</td>"
    f"<td>{v[f'A{A}']['med']:+.2f}</td><td>{v[f'A{A}']['p95']}</td>"
    f"<td>{v[f'A{A}'].get('n_holdout_dxf', '—')}</td></tr>".replace(',', ' ')
    for A in (1, 2, 3, 4))

# сравнение Гавань/Декабристов (для панели)
a14 = json.load(open(f'{FUS20}/a1_vs_a4_gavan_decabr_6.20.json'))
def _a14_row(tag):
    r = a14[tag]
    cells = ''.join(f"<td>{r['levels'][lv]['A1']:.2f}</td><td>{r['levels'][lv]['A4']:.2f}</td>"
                    for lv in ('1.0', '3.0', '5.0'))
    return f"<tr><td>{r['name']}</td>{cells}</tr>"
a14_rows = ''.join(_a14_row(t) for t in ('gavan', 'decabristov', 'bolshoy'))

html = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>СПб — единая поверхность 6.52 (ревизия): клип воды + прибрежный барьер</title>
<style>__LEAFCSS__
html,body{margin:0;padding:0;height:100%;font:13px/1.45 -apple-system,'Segoe UI',Roboto,Arial,sans-serif;background:#0f141a;color:#e8ecf2}
#map{position:absolute;inset:0;background:#0f141a}
.panel{position:absolute;z-index:1000;top:12px;left:12px;width:352px;max-height:calc(100% - 24px);overflow:auto;
 background:rgba(15,20,26,.93);border:1px solid #2a3441;border-radius:10px;box-shadow:0 6px 24px rgba(0,0,0,.5);backdrop-filter:blur(6px)}
.panel h1{font-size:15px;margin:0;padding:12px 14px 2px;letter-spacing:.2px}
.panel .sub{padding:0 14px 8px;color:#9fb0c3;font-size:11.5px;line-height:1.4}
.badge{display:inline-block;background:#1d2a38;color:#7fd1ff;border:1px solid #2b4257;border-radius:5px;padding:1px 7px;font-size:10.5px;margin:0 3px 6px 0}
.ctl{padding:8px 14px 4px;border-top:1px solid #223040}
.ctl label.lbl{display:block;color:#9fb0c3;font-size:11px;margin-bottom:4px;text-transform:uppercase;letter-spacing:.5px}
.slider-row{display:flex;align-items:center;gap:10px}
#lvl{flex:1;accent-color:#4dabf7}
#lvlNum{font-size:22px;font-weight:700;min-width:74px;text-align:right;color:#fff;font-variant-numeric:tabular-nums}
#lvlNum small{font-size:11px;color:#8a93a3;font-weight:400}
.ticks{display:flex;justify-content:space-between;color:#5d6b7d;font-size:9.5px;margin-top:2px}
.assump{display:grid;grid-template-columns:1fr 1fr;gap:5px;padding:2px 0 4px}
.assump button{background:#1d2a38;color:#cfe4f7;border:1px solid #2b4257;border-radius:7px;padding:6px 4px;font-size:11px;cursor:pointer;text-align:left;line-height:1.25}
.assump button b{display:block;font-size:11.5px;color:#fff}
.assump button.on{background:#164a6e;border-color:#4dabf7;color:#eaf6ff}
.assump button.on b{color:#7fd1ff}
.row{display:flex;align-items:center;gap:7px;padding:2.5px 14px;cursor:pointer;user-select:none}
.row:hover{background:rgba(255,255,255,.04)}
.row input{accent-color:#4dabf7}
.chip{width:14px;height:14px;border-radius:3px;flex:none;border:1px solid rgba(255,255,255,.25)}
.row .nm{flex:1;font-size:12px}
.row .ar{font-size:10.5px;color:#8a93a3;font-variant-numeric:tabular-nums;text-align:right}
.stats{padding:8px 14px;border-top:1px solid #223040;color:#c3cdd9;font-size:12px}
.stats b{color:#fff;font-variant-numeric:tabular-nums}
.mini{padding:6px 14px 2px;border-top:1px solid #223040}
.mini .cap{color:#9fb0c3;font-size:11px;margin-bottom:2px}
.btns{display:flex;gap:6px;padding:8px 14px 2px;border-top:1px solid #223040}
.btns button{flex:1;background:#1d2a38;color:#cfe4f7;border:1px solid #2b4257;border-radius:6px;padding:5px 4px;font-size:11.5px;cursor:pointer}
.btns button:hover{background:#25384c}
details{border-top:1px solid #223040;padding:8px 14px;color:#aab6c5;font-size:11.5px}
details summary{cursor:pointer;color:#7fd1ff;font-size:12px}
details p{margin:6px 0}
details b{color:#dfe6ee}
details table{border-collapse:collapse;width:100%;font-size:10.3px;margin:6px 0}
details th,details td{border:1px solid #223040;padding:2px 4px;text-align:right;white-space:nowrap}
details th{color:#8a93a3}details td:first-child,details th:first-child{text-align:left}
.foot{padding:6px 14px 10px;color:#5d6b7d;font-size:10px;border-top:1px solid #223040}
.leaflet-popup-content{font:12.5px/1.4 -apple-system,Arial}
.leaflet-container{background:#0f141a}
.leaflet-control-attribution{display:none!important}
.legend-grad{height:10px;border-radius:5px;margin:6px 0 2px;background:linear-gradient(90deg,#2288d3,#2fb99a,#96ca56,#c9c344,#e05252)}
.srcnote{font-size:11px;color:#a8bacc;padding:2px 14px 6px}
@media(max-width:560px){.panel{width:calc(100% - 24px);max-height:56%}}
</style>
</head>
<body>
<div id="map"></div>
<div class="panel">
  <h1>Единая поверхность 6.52 — затопления СПб (ревизия)</h1>
  <div class="sub">Сшивка всех источников + достройка лакун + <b style="color:#fff">клип по воде + прибрежный барьер 16 м</b>. Уровни <b style="color:#fff">+1 … +5 м</b> над нулём БС-77
   <span style="color:#5d6b7d">· мозаика 5.90 2 м + лапласова достройка с якорением ATL06/отметки/DXF · вода: OSM (coastline + внутренние воды) · δ=+16.04 м</span></div>
  <div style="padding:0 14px">
    <span class="badge">этап 6.52 · ревизия</span><span class="badge">клип берега + дельты</span><span class="badge">барьер уреза 16 м</span><span class="badge">+ DXF Дачное</span>
    <span class="badge">holdout MAD 0.25–0.30 м</span>
  </div>
  <div class="ctl">
    <label class="lbl">Уровень воды (метры БС-77)</label>
    <div class="slider-row">
      <input type="range" id="lvl" min="1" max="5" step="0.5" value="1">
      <div id="lvlNum">+1.0 <small>м</small></div>
    </div>
    <div class="ticks"><span>1.0</span><span>2.0</span><span>3.0</span><span>4.0</span><span>5.0</span></div>
  </div>
  <div class="ctl">
    <label class="lbl">Степень допущения (достроенные зоны)</label>
    <div class="assump" id="assump"></div>
    <div class="srcnote" id="assumpNote"></div>
  </div>
  <div class="btns">
    <button id="btnOne">Один уровень</button>
    <button id="btnAll">Все уровни</button>
    <button id="btnNone">Скрыть</button>
  </div>
  <div class="ctl"><label class="lbl">Сценарии: из данных + достроено</label>
    <div class="legend-grad"></div>
  </div>
  <div id="rows"></div>
  <div class="stats">Выбрано: <b id="sumBase">—</b> из данных + <b id="sumFill" style="color:#ffb266">—</b> достроено =
   <b id="sumArea">—</b><br>
   <span style="color:#8a93a3">Ориентиры: нагон 1824 г. ≈ +4.2 м, 1924 г. ≈ +3.7 м; защита КЗС — до +5 м.</span></div>
  <div class="mini"><div class="cap">Площади по уровням, км² (синий — данные, оранжевый — достроено)</div>__CHARTSVG__</div>
  <details>
    <summary>Источники данных (слои)</summary>
    <div style="margin-top:6px">
      <label class="row"><input type="checkbox" id="lCanon"><span class="chip" style="background:#4fc3f7"></span><span class="nm">ATL06 ICESat-2, канон v4 (сабсэмпл)</span></label>
      <label class="row"><input type="checkbox" id="lMarks" checked><span class="chip" style="background:#ffb74d"></span><span class="nm">Отметки НСПД/полевые</span></label>
      <label class="row"><input type="checkbox" id="lDxf" checked><span class="chip" style="background:#f06292"></span><span class="nm">DXF «Дачное» — якоря 6.20 (сабсэмпл 1/3 из __NDXF__)</span></label>
      <label class="row"><input type="checkbox" id="lDiff"><span class="chip" style="background:#ff5252"></span><span class="nm">Экстраполяция (A4 − A1)</span></label>
      <label class="row"><input type="checkbox" id="lZones"><span class="chip" style="background:#90a4ae"></span><span class="nm">Зоны тиража 5.83–5.90 (__NZONES__)</span></label>
      <label class="row"><input type="checkbox" id="lGaps"><span class="chip" style="background:#616161"></span><span class="nm">Пропуски «нет данных» (5.96d)</span></label>
      <label class="row"><input type="checkbox" id="lRef"><span class="chip" style="background:#ffd54f"></span><span class="nm">Эталон: Большой пр. В.О.</span></label>
    </div>
    <p style="margin:6px 0 2px;color:#8a93a3">Клик по карте — ближайшая ATL06-точка (дата, RGT, класс). Валидация достройки — holdout 50% canon-точек и 50% DXF в лакунах:</p>
    <table><tr><th>допущение</th><th>дальность</th><th>n</th><th>MAD</th><th>DXF MAD</th><th>med</th><th>p95</th><th>отм.</th></tr>__VALROWS__</table>
    <p style="margin:6px 0 2px;color:#8a93a3">Гавань/Декабристов: площади достроенных зон, км² (A1 vs A4 — разница = чистая экстраполяция):</p>
    <table><tr><th>район</th><th>+1 м A1</th><th>+1 A4</th><th>+3 A1</th><th>+3 A4</th><th>+5 A1</th><th>+5 A4</th></tr>__A14ROWS__</table>
  </details>
  <details>
    <summary>Методика и ограничения</summary>
    <p><b>Сшивка 6.20.</b> База — гибридная мозаика 5.90 (2 м, БС-77, MAD 0.31 м). Лакуны достраиваются мультисеточным решением Лапласа с притяжением к независимым точкам: __ANCHORPTS__ якорей (ATL06 canon v4 + отметки + <b>DXF «Дачное» 2 326 тчк</b> — полная съёмка юго-западной лакуны, локальный триаж ±6 м, holdout DXF MAD 1.95 м против ~10 м до якорения), демпфирование 0.6, радиус 2 км.</p>
    <p><b>Клип по воде (6.20 + 6.21).</b> Все зоны обрезаны сушей = bbox − вода OSM. В 6.20 — coastline-полигоны (osmdata.openstreetmap.de, ODbL): убраны «стрелы» достройки в заливе у Ломоносова/Кронштадта (A2 +1 м: 5.7→1.7 км²; A3 +1 м: 19.8→2.3 км²). В 6.21 добавлены <b>внутренние воды</b> (PBF BBBike SanktPetersburg, 2026-10-10, ODbL): Нева, Малая Нева и Невки, Мойка, Фонтанка, канал Грибоедова, Обводный, Кронверкский пролив и канал, Ждановка, Карповка, пруды — 1 416 полигонов, 37.6 км²; кольцо Невы замкнуто хордой за границей вырезки, Заячий остров вырезан keyhole-проходом берега. Зоны на акватории дельты (пятно у Дворцового моста, кольцо у Заячьего): база 71.1→0.2 км², fill A2 28.2→0.03 км². Ограничение: о. Котлин в coastline-полигонах не выделен сушей и данных мозаики на нём нет — зоны на острове не показываются.</p>
    <p><b>Ревизия 6.52 — прибрежный барьер 16 м.</b> Диагностика (этап 6.51) показала: зоны +1…+2 м вдоль гранитных набережных — это лента смешанных пикселей сетки ~8 м (гранит 2.5–3.5 м, слитый с водой 0 м, читается 0.3–1.0 м) плюс щели между OSM-водой и линией причала. Реальные отметки гранита — 2.48–3.08 м (медиана 2.76, n=253): физически +1 м их не топит. Фикс: из всех зон вычтена полоса 16 м вдоль воды, обрывки &lt; 0.5 га отброшены. Эффект: +1 м 1.91→1.70 км², +1.5 м 9.02→6.69, +2 м 15.36→11.51, +3 м 39.52→33.44, +5 м 108.83→100.18; доля зоны в 30 м от воды на +1 м 16%→6%. Ограничение честности: сетка ~8 м и «рампа» уреза в самой поверхности остаются — барьер скрывает ленту, а не чинит высоты.</p>
    <p><b>Степень допущения</b> — дальность достройки от измеренных данных: A1 «Строгий» ≤250 м (96 км²), A2 «Умеренный» ≤1 км (328 км²), A3 «Смелый» ≤3 км (679 км²), A4 «Максимум» — вся сетка (1941 км², включая залив/периферию — там доминирует тренд).</p>
    <p><b>Валидация.</b> Holdout 50% canon-точек и 50% DXF внутри лакун: MAD 0.30/0.28/0.25/0.26 м по A1–A4 — достройка не хуже базовой мозаики. Зоны затопления при 6.20 не изменились (кластер DXF — высоты 29–47 м, далеко от порогов 1–5 м), но поверхность в SW-лакуне исправлена на ~10 м. Акватории исключены (h ≤ 0.7 м + клип OSM); компоненты &lt; 1.9 га отброшены; упрощение 25 м; ячейка ~8 м.</p>
    <p><b>Ограничения:</b> без гидродинамики/дренажа/нагона; КЗС неработает (сценарий прорыва); достроенные зоны — интерполяция/экстраполяция, не измерения (в таблице слоёв выделены пунктиром); классы доверия пикселей: 1–4 = достройка по дальности, 10–13 = мозаика (excl/rest/ok/gold).</p>
    <p><b>Не является</b> официальной картой зон затопления.</p>
  </details>
  <div class="foot">spb-arcticdem-pipeline · этапы 6.00→6.52 · вода: © OpenStreetMap (ODbL) · подложка: Esri World Dark Gray (© Esri) · базовые зоны: этап 5.96</div>
</div>
<script>__LEAFJS__</script>
<script>
const BASE = __BASEGJ__;
const FILL = __FILL__;
const DIFF = __DIFF__;
const BASE_AREAS = __BASEAREAS__;
const FILL_AREAS = __FILLAREAS__;
const CHART = __CHART__;
const A_INFO = __AINFO__;
const SRC = __SRC__;
const COLORS = {1.0:'#2288d3',1.5:'#2fa3b5',2.0:'#2fb99a',2.5:'#58c47a',3.0:'#96ca56',3.5:'#c9c344',4.0:'#e0a83a',4.5:'#e5803a',5.0:'#e05252'};
function dk(c){const n=parseInt(c.slice(1),16);const r=(n>>16)*0.72|0,g=((n>>8)&255)*0.72|0,b=(n&255)*0.72|0;return '#'+((r<<16)|(g<<8)|b).toString(16).padStart(6,'0');}

const map = L.map('map',{zoomControl:true,attributionControl:false,preferCanvas:true}).fitBounds([[59.72,29.42],[60.12,30.60]]);
L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}',{maxNativeZoom:16,maxZoom:18}).addTo(map);
L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}',{maxNativeZoom:16,maxZoom:18,opacity:0.85}).addTo(map);
L.control.scale({imperial:false,maxWidth:160}).addTo(map);

// ---- базовые зоны (из данных, этап 5.96)
const baseLayers = {};
for (const f of BASE.features){
  const lv = f.properties.level_m, col = COLORS[lv];
  baseLayers[lv] = L.geoJSON({type:'FeatureCollection',features:[f]}, {
    style:{color:dk(col),weight:1,fillColor:col,fillOpacity:0.52},
    onEachFeature:(ft,lyr)=>{ lyr.bindPopup('<b>Из данных: уровень +'+ft.properties.level_m.toFixed(1)+' м БС-77</b><br>Площадь (клип по воде + барьер 16 м): '+ft.properties.area_km2.toFixed(1)+' км²<br><span style="color:#777">мозаика 5.90, «чаша», вода OSM + барьер уреза 16 м (ревизия 6.52)</span>'); }
  }).addTo(map);
}
// ---- достроенные зоны (6.00), пунктир
const fillLayers = {};
for (const a of [1,2,3,4]){
  fillLayers[a] = {};
  for (const lvS in FILL[a]){
    const lv = parseFloat(lvS), col = COLORS[lv];
    fillLayers[a][lv] = L.geoJSON({type:'FeatureCollection',features:[FILL[a][lvS]]}, {
      style:{color:dk(col),weight:1.2,dashArray:'4,3',fillColor:col,fillOpacity:0.30},
      onEachFeature:(ft,lyr)=>{ lyr.bindPopup('<b>Достроено (этап 6.52, клип + барьер 16 м)</b><br>Допущение A'+ft.properties.a+' — '+A_INFO[ft.properties.a].dist+'<br>Уровень +'+ft.properties.level_m.toFixed(1)+' м · '+ft.properties.area_km2.toFixed(1)+' км²<br><span style="color:#777">лапласова интерполяция + якоря ATL06/отметки/DXF</span>'); }
    });
  }
}

// ---- экстраполяция (A4−A1) — слой на уровень
const diffLayers = {};
for (const lvS in DIFF){
  const lv = parseFloat(lvS);
  diffLayers[lv] = L.geoJSON({type:'FeatureCollection',features:[{type:'Feature',properties:{level_m:lv},geometry:DIFF[lvS]}]}, {
    style:{color:'#ff5252',weight:0.9,dashArray:'1,3',fillColor:'#ff5252',fillOpacity:0.13},
    onEachFeature:(ft,lyr)=>{ lyr.bindPopup('<b>Экстраполяция (A4 − A1)</b><br>Уровень +'+lv.toFixed(1)+' м<br><span style="color:#777">территория попадает в продукт только при максимальном допущении — вне радиуса 3 км от данных</span>'); }
  });
}

let curA = 2;                      // по умолчанию «Умеренный»
const checked = new Set(['1.0']);

function render(){
  const lvSlider = document.getElementById('lvl').valueAsNumber;
  document.getElementById('lvlNum').innerHTML = lvSlider.toFixed(1)+' <small>м</small>';
  let sb=0, sf=0;
  for (const lv of __LEVELS__){
    const on = checked.has(lv.toFixed(1));
    if (on && !map.hasLayer(baseLayers[lv])) baseLayers[lv].addTo(map);
    if (!on && map.hasLayer(baseLayers[lv])) map.removeLayer(baseLayers[lv]);
    const fl = fillLayers[curA][lv];
    if (on && fl && FILL_AREAS[curA][lv.toFixed(1)]>0){ if(!map.hasLayer(fl)) fl.addTo(map); }
    else if (fl && map.hasLayer(fl)) map.removeLayer(fl);
    const showDiff = document.getElementById('lDiff') && document.getElementById('lDiff').checked;
    if (showDiff && on && diffLayers[lv]){ if(!map.hasLayer(diffLayers[lv])) diffLayers[lv].addTo(map); }
    else if (diffLayers[lv] && map.hasLayer(diffLayers[lv])) map.removeLayer(diffLayers[lv]);
    if (on){ sb += (BASE_AREAS[lv.toFixed(1)]||0); sf += (FILL_AREAS[curA][lv.toFixed(1)]||0); }
  }
  const order=[5.0,4.5,4.0,3.5,3.0,2.5,2.0,1.5,1.0]; // низкие уровни поверх старших
  for(const lv of order){
    if(map.hasLayer(baseLayers[lv])) baseLayers[lv].bringToFront();
    if(fillLayers[curA][lv] && map.hasLayer(fillLayers[curA][lv])) fillLayers[curA][lv].bringToFront();
    if(diffLayers[lv] && map.hasLayer(diffLayers[lv])) diffLayers[lv].bringToFront();
  }
  // источники — на задний план (не закрывают зоны)
  if(typeof gCanon!=='undefined') gCanon.eachLayer(l=>l.bringToBack&&l.bringToBack());
  if(typeof gZones!=='undefined') gZones.eachLayer(l=>l.bringToBack&&l.bringToBack());
  if(typeof gGaps!=='undefined') gGaps.eachLayer(l=>l.bringToBack&&l.bringToBack());
  document.getElementById('sumBase').textContent = sb>0? sb.toFixed(1)+' км²':'—';
  document.getElementById('sumFill').textContent = sf>0? sf.toFixed(1)+' км²':'0 км²';
  document.getElementById('sumArea').textContent = (sb+sf)>0? (sb+sf).toFixed(1)+' км²':'—';
  renderRows(); renderChart();
}
function renderRows(){
  const rowsEl=document.getElementById('rows'); rowsEl.innerHTML='';
  for(const lv of __LEVELS__){
    const key=lv.toFixed(1);
    const b=BASE_AREAS[key]||0, f=FILL_AREAS[curA][key]||0;
    const row=document.createElement('div'); row.className='row';
    row.innerHTML='<input type="checkbox" '+(checked.has(key)?'checked':'')+'><span class="chip" style="background:'+COLORS[lv]+'"></span><span class="nm">+'+lv.toFixed(1)+' м</span><span class="ar">'+b.toFixed(1)+(f>0?' <span style="color:#ffb266">+'+f.toFixed(1)+'</span>':'')+' км²</span>';
    row.querySelector('input').addEventListener('change',(e)=>{
      if(e.target.checked) checked.add(key); else checked.delete(key);
      render();
    });
    rowsEl.appendChild(row);
  }
}
function renderChart(){
  const d=CHART[curA];
  const totals=Object.values(d).map(x=>x[0]+x[1]);
  const mx=Math.max(...totals,1);
  let bars='';
  const n=Object.keys(d).length, w=100.0/n;
  let i=0;
  for(const lvS in d){
    const b=d[lvS][0]/mx*62, f=d[lvS][1]/mx*62;
    const lv=parseFloat(lvS);
    if(b>0) bars+='<rect x="'+(i*w+w*0.12)+'" y="'+(70-b)+'" width="'+(w*0.76)+'" height="'+b+'" fill="'+COLORS[lv]+'"><title>+'+lv.toFixed(1)+' м: из данных '+d[lvS][0]+' км²</title></rect>';
    if(f>0.2) bars+='<rect x="'+(i*w+w*0.12)+'" y="'+(70-b-f)+'" width="'+(w*0.76)+'" height="'+f+'" fill="#e8933a" opacity="0.85"><title>+'+lv.toFixed(1)+' м: достроено '+d[lvS][1]+' км²</title></rect>';
    i++;
  }
  document.getElementById('chartBox').innerHTML='<svg viewBox="0 0 100 74" preserveAspectRatio="none" style="width:100%;height:86px">'+bars+'</svg><div style="display:flex;justify-content:space-between;font-size:9.5px;color:#8a93a3">'+Object.keys(d).map(k=>'<span>'+parseFloat(k).toFixed(1)+'</span>').join('')+'</div>';
}
// ---- бегунок допущения
const assumpEl=document.getElementById('assump');
for(const a of [1,2,3,4]){
  const b=document.createElement('button');
  b.id='ab'+a;
  b.innerHTML='<b>A'+a+' · '+A_INFO[a].name+'</b>'+A_INFO[a].dist+' · достроено '+FILL_AREAS[a]['5.0']+' км² при +5 м';
  b.addEventListener('click',()=>{
    curA=a;
    document.querySelectorAll('.assump button').forEach(x=>x.classList.remove('on'));
    b.classList.add('on');
    document.getElementById('assumpNote').textContent='Достроенные зоны: интерполяция/экстраполяция на расстоянии '+A_INFO[a].dist+' от измеренных данных. Holdout MAD '+(__VALS__['A'+a].mad)+' м.';
    render();
  });
  assumpEl.appendChild(b);
}
document.getElementById('ab2').classList.add('on');
document.getElementById('assumpNote').textContent='Достроенные зоны: интерполяция/экстраполяция на расстоянии '+A_INFO[2].dist+' от измеренных данных. Holdout MAD '+(__VALS__['A2'].mad)+' м.';

document.getElementById('lvl').addEventListener('input',(e)=>{
  checked.clear(); checked.add(e.target.valueAsNumber.toFixed(1));
  render();
});
document.getElementById('btnOne').onclick=()=>{
  checked.clear(); checked.add(document.getElementById('lvl').valueAsNumber.toFixed(1)); render();
};
document.getElementById('btnAll').onclick=()=>{
  __LEVELS__.forEach(lv=>checked.add(lv.toFixed(1))); render();
};
document.getElementById('btnNone').onclick=()=>{ checked.clear(); render(); };

// ---- источники
const gMarks=L.layerGroup();
const mColor={otmetki_field:'#ffb74d',topo_pilot:'#ce93d8',topo_dxf_dachnoe:'#f06292',topo95_spot:'#e57373',topo95_contour:'#e57373',topo95_urez:'#e57373'};
SRC.marks.forEach(m=>{
  L.circleMarker([m[1],m[0]],{color:mColor[m[2]]||'#ffb74d',radius:4,weight:1.5,fillColor:mColor[m[2]]||'#ffb74d',fillOpacity:0.85})
   .bindPopup('<b>Отметка</b><br>источник: '+m[2]+'<br>район: '+m[3]+(m[4]!=null?('<br>z: '+m[4]+' м БС-77'):'')+(m[5]!=null?('<br>dH vs DEM: '+m[5]+' м'):'')+(m[6]?('<br>'+m[6]):'')).addTo(gMarks);
});
gMarks.addTo(map);
const gDxf=L.layerGroup();
SRC.dxf.forEach(d=>{
  L.circleMarker([d[1],d[0]],{color:'#f06292',radius:2.2,weight:1,fillColor:'#f06292',fillOpacity:0.8})
   .bindPopup('<b>DXF «Дачное» — якорь сшивки 6.20</b><br>квартал: '+d[2]+'<br>z: '+d[3]+' м БС-77<br><span style="color:#777">всего '+SRC.dxfTotal+' точек (показан сабсэмпл 1/3); до якорения поверхность здесь завышала ~10 м</span>').addTo(gDxf);
});
gDxf.addTo(map);
const gCanon=L.layerGroup();
const idx=new Map();
function cell(la,lo){return Math.round(la*1000)+'_'+Math.round(lo*1000);}
SRC.canon.forEach((p,i)=>{
  const k=cell(p[1],p[0]); if(!idx.has(k))idx.set(k,[]); idx.get(k).push(i);
  L.circleMarker([p[1],p[0]],p[4]?{color:'#4fc3f7',radius:1.7,stroke:false,fillOpacity:0.75}:{color:'#4fc3f7',radius:1.05,stroke:false,fillOpacity:0.5}).addTo(gCanon);
});
// canon по умолчанию ВЫКЛЮЧЕН (галка снята) — точки не должны закрывать зоны
map.on('click',e=>{
  const la=e.latlng.lat,lo=e.latlng.lng;let best=null,bd=1e9;
  for(let dLa=-1;dLa<=1;dLa++)for(let dLo=-1;dLo<=1;dLo++){
    const arr=idx.get(cell(la+dLa*0.0005,lo+dLo*0.0005));if(!arr)continue;
    arr.forEach(i=>{const p=SRC.canon[i];const d=(p[1]-la)*(p[1]-la)+(p[0]-lo)*(p[0]-lo);if(d<bd){bd=d;best=p;}});
  }
  if(best&&bd<2e-7){
    L.popup().setLatLng([best[1],best[0]]).setContent('<b>ATL06 канон v4</b><br>дата: '+SRC.canonDates[best[2]]+'<br>RGT: '+best[3]+'<br>cnf_src: '+(best[4]?'high':'base')).openOn(map);
  }
});
const gZones=L.layerGroup();
SRC.zones.forEach(z=>{
  const hot=z.tag=='LIGO'||z.tag=='AVTOV';
  const st=hot?{color:'#ef5350',weight:1.6,dashArray:'5,3',fill:false}:{color:'#90a4ae',weight:0.6,dashArray:'3,4',fill:false,opacity:0.75};
  let pop='<b>'+z.tag+'</b>'; if(z.verdict) pop+='<br>вердикт: '+z.verdict;
  L.rectangle([[z.bbox[1],z.bbox[0]],[z.bbox[3],z.bbox[2]]],st).bindPopup(pop).addTo(gZones);
});
const gGaps=L.layerGroup();
SRC.gaps.forEach(g=>{
  L.rectangle([[g.lat[0],g.lon[0]],[g.lat[1],g.lon[1]]],{color:'#8d9aa8',weight:1,dashArray:'2,3',fill:false})
   .bindPopup('<b>'+g.name+'</b><br>nodata: '+g.nod_pct+'% ('+g.nod_km2+' км²)<br><span style="color:#777">достроено на этапах 6.00 (A2 по умолчанию)</span>').addTo(gGaps);
});
const gEthal=L.layerGroup();
const carrLngLat=SRC.carr[0].map(p=>[p[1],p[0]]);
L.polygon(carrLngLat,{color:'#ffd54f',weight:3,fill:false}).bindPopup('<b>Эталон: Большой проспект В.О.</b><br>carriageway 30.239–30.291 × 59.926–59.942<br>зона LIGO была prior-only (MAD 0.467) — в 6.00 достроена с якорями ATL06').addTo(gEthal);
L.rectangle([[59.900,30.205],[59.9415,30.280]],{color:'#ffffff',weight:0.9,dashArray:'4,4',fill:false}).bindPopup('о. Декабристов (ядро диагностики)').addTo(gEthal);
const T={'lCanon':gCanon,'lMarks':gMarks,'lZones':gZones,'lGaps':gGaps,'lRef':gEthal,'lDxf':gDxf,'lDiff':null};
Object.keys(T).forEach(id=>{ if(!T[id])return; document.getElementById(id).addEventListener('change',e=>{
  e.target.checked?T[id].addTo(map):map.removeLayer(T[id]);
  if(id==='lDiff')render();
});});

render();
</script>
</body>
</html>
"""

chart_svg = ''  # рисуется клиентом (renderChart), контейнер:
html = (html.replace('__LEAFCSS__', leaf_css)
            .replace('__LEAFJS__', leaf_js)
            .replace('__CHARTSVG__', '<div id="chartBox"></div>')
            .replace('__BASEGJ__', json.dumps(base_fc, ensure_ascii=False, separators=(',', ':')))
            .replace('__FILL__', json.dumps({str(a): {str(l): ft for l, ft in fill_fc[a].items()}
                                             for a in (1, 2, 3, 4)}, ensure_ascii=False, separators=(',', ':')))
            .replace('__DIFF__', json.dumps({str(k): v for k, v in DIFF.items()},
                                            ensure_ascii=False, separators=(',', ':')))
            .replace('__BASEAREAS__', json.dumps(base_areas))
            .replace('__FILLAREAS__', json.dumps({str(a): fill_areas[a] for a in (1, 2, 3, 4)}))
            .replace('__CHART__', json.dumps(chart))
            .replace('__AINFO__', json.dumps({str(a): A_INFO[a] for a in (1, 2, 3, 4)}, ensure_ascii=False))
            .replace('__VALS__', json.dumps(report['validation']))
            .replace('__LEVELS__', json.dumps(LEVELS))
            .replace('__SRC__', json.dumps(SRC, ensure_ascii=False, separators=(',', ':')))
            .replace('__VALROWS__', val_rows)
            .replace('__A14ROWS__', a14_rows)
            .replace('__NDXF__', str(len(dxf) * 3 - 2))
            .replace('__NZONES__', str(len(zones)))
            .replace('__ANCHORPTS__', f"{report['anchor_points_total']:,}"))
html = html.replace(" '__LEVELS__'", " __LEVELS__")  # no-op guard
with open(OUT, 'w', encoding='utf-8') as f:
    f.write(html)
import shutil
shutil.copyfile(OUT, OUT_HOME)
print('OK', OUT, f'{os.path.getsize(OUT)/1e6:.2f} MB')
print('OK', OUT_HOME)
