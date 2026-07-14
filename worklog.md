# Worklog: Большой проспект В.О. — owner map pipeline

---
Task ID: 5.1-fix-visibility
Agent: main
Task: Пользователь сообщил, что на QA-карте owner_qa_map.html "границы дороги правильно отобразились, а вот owner map вообще не попала" (см. скриншот /home/z/my-project/upload/pasted_image_1783954381120.png).

Work Log:
- Проанализировал скриншот попиксельно: из 817×884 px только 16 имели точный цвет road (60,60,60). Остальные размыты до полупрозрачности со спутником.
- Извлёк base64-PNG из HTML: 12182 road px, 5649 sidewalk, 179533 green, 373689 building, 14848 water — геометрия корректна.
- Проверил геолокацию road пикселей: span (59.926349–59.941502, 30.239249–30.290829) — точно совпадает с bounds carriageway.geojson (59.926349–59.941507, 30.239259–30.290820).
- Причина "не попала": alpha=90 для other + alpha=170 для владельцев + folium opacity=0.65 → эффективная видимость 23–43%, на спутнике не видно.
- Исправил qa_map() в scripts/01_owner_map.py:
  · other (code 0): alpha=0 (полностью прозрачен, спутник просвечивает)
  · владельцы 1–5: alpha=230 (почти непрозрачные)
  · mixed boundary: alpha≤110 (виден "шов")
  · folium opacity: 0.65 → 0.95
  · carriageway polygon: weight 2→3, dashArray='5,3' (жирнее пунктир)
  · control points: radius 5→6, weight 1→2, fill_opacity 0.9→0.95, цвета расширены
  · добавлен m.fit_bounds([[s4,w4],[n4,e4]]) — авто-зум на патч
- Перегенерировал owner_qa_map.html: alpha=0 (336587 px), alpha=110 (101918 mixed), alpha=230 (483983 owner).
- VLM-верификация превью: подтверждает видимость тёмного диагонального коридора дороги, красных блоков зданий, зелени вдоль дороги.

Stage Summary:
- Bug был НЕ в геометрии (road пиксели всегда были в правильном месте — span совпадает с carriageway polygon bounds).
- Bug был в VISIBILITY: alpha + opacity давали 23–43% — на спутнике не видно.
- Файл обновлён: /home/z/my-project/download/owner_qa_map.html
- Скрипт сохранён: /home/z/my-project/scripts/01_owner_map.py

Отдельная проблема (НЕ починена в этой задаче): control_points_qa.csv показывает 0/9 точек на road. Причина — точки из Отметки.txt лежат на боковых улицах, а не на Большом пр-те. 9 из 202 точек попали в patch, но owner=other/green у всех. Это DATA-проблема (нужны либо точки именно на Большом пр-те, либо ослабление фильтра до "ближайшая дорога OSM в радиусе R"), не bug owner_map.

Next:
- Пользователь должен открыть новый owner_qa_map.html и убедиться, что owner map виден и совпадает со спутником.
- Затем модуль 02: робастная реконструкция h=f(picket)+g(offset) по владельцу road.
- Параллельно — решить вопрос с контрольными точками (нет валидных на дороге).

---
Task ID: 5.1-fix-water-polygons
Agent: main
Task: Пользователь сообщил "баг в геометрии, обрати внимание на синий" — на owner_qa_map.html водные объекты отображались тонкими 20м-полосами вместо настоящих рек.

Work Log:
- Проанализировал скриншот pasted_image_1783955293395.png: 5093 синих пикселя в 162 кластерах, крупнейшие 1218+1074+972 px. VLM подтвердил, что они не похожи на реальные реки.
- Заглянул в OSM-кэш: 68 water-элементов, из них 40 — `waterway=river|canal` (ЦЕНТРАЛЬНЫЕ ЛИНИИ без ширины), только 4 — `natural=water` way (полигоны), остальное — multipolygon relations.
- Нашёл root cause #1: `_iter_polygons()` для relation фильтровал `role=='inner'` и выкидывал outer! (строка `if m.get('role') == 'inner' or 'geometry' not in m: continue`). Т.е. из relation вообще не строились полигоны — брались только way-полигоны (которых почти нет).
- Нашёл root cause #2: даже если бы outer обрабатывался, каждый way-фрагмент становился отдельным полигоном. У Невы 469 outer-фрагментов → получилось бы 358 крошечных полигонов. Нужна **сшивка** way-фрагментов в замкнутые кольца.
- Нашёл root cause #3: `collect_layers()` для `waterway=river|canal` (линий) делал fallback к buffer 10м. Если relation с тем же name существует — линия не нужна, она порождает фейковую тонкую полосу поверх правильного полигона.
- Переписал `_iter_polygons()`:
  · Корректно разделяет outer и inner по `m['role']`
  · Сшивает way-фрагменты в замкнутые кольца (stitch_rings): жадно склеивает сегменты, у которых конец == начало следующего, с точностью 1e-9°
  · Из outer-колец строит Polygon с подходящими inner-дырами (по `contains(representative_point)`)
  · Корректно обрабатывает MultiPolygon после `buffer(0)`
- Переписал `collect_layers()`:
  · Собирает water-полигоны из `natural=water` (way+relation) и `waterway=riverbank`
  · Собирает water-линии из `waterway=river|canal` отдельно
  · В конце: выкидывает линию, если у её name есть полигон (deduplication)
  · Логирует разбиение (полигонов/линий/имён с полигоном)
- Верификация на Нева-relation: было 358 tiny polygons (total 0.001712 sq.deg) → стало 1 polygon (0.005141 sq.deg), в 3 раза больше площадь. Большая Нева: 82 poly (0.000037) → 1 poly (0.000445), в 12 раз больше.
- Перегенерировал owner_map.tif: water pixels 14848 → **116127** (8x увеличение), other 336587 → 229211 (часть other оказалась водой).

Stage Summary:
- Bug был в геометрии воды, не в прозрачности.
- 3 bug'а в _iter_polygons и collect_layers — все починены.
- Файлы обновлены: scripts/01_owner_map.py, download/owner_map.tif, download/owner_qa_map.html, download/owner_qa.json, download/control_points_qa.csv, download/carriageway.geojson.
- VLM подтвердил: на превью видны большие синие площади на краях картинки (где Нева/Большая Нева вокруг В.О.), красные блоки зданий, тёмный диагональный коридор дороги.

Next:
- Пользователь должен открыть новый owner_qa_map.html и проверить, что синие площади совпадают с реальными реками на спутнике.
- Затем модуль 02: робастная реконструкция h=f(picket)+g(offset) по владельцу road.

---
Task ID: 5.1-fix-crs-rotation
Agent: main
Task: Пользователь сообщил "owner map повёрнут на 90° относительно OSM". VLM подтвердил: растр развёрнут относительно спутника.

Work Log:
- Проанализировал transform DEM (EPSG:3413): a=2.0, b=0, d=0, e=-2.0 — axis-aligned в projected CRS.
- Но проверка конверсии углов патча выявила root cause: EPSG:3413 (polar stereographic) в СПб **развёрнут ~75° относительно true north**.
  · 'East' в 4326 → (+922, +3505) в 3413 (в основном +Y)
  · 'North' в 4326 → (-1848, +486) в 3413 (в основном -X)
- Patch 532×1734 px (1064×3468м) в 3413 — физически ТОНКИЙ и ДЛИННЫЙ, вытянутый по Y.
- В 4326 этот же patch — ШИРОКИЙ (Δlon=0.062°) и КОРОТКИЙ (Δlat=0.016°).
- Folium.raster_layers.ImageOverlay умеет только axis-aligned bbox `[[S,W],[N,E]]` → растягивает ТОНКИЙ-ДЛИННЫЙ png в ШИРОКИЙ-КОРОТКИЙ прямоугольник → поворот на ~75° (выглядит как 90°).
- Решение: перед ImageOverlay перепроецировать owner raster из EPSG:3413 в **EPSG:3857 (Web Mercator)**, который выровнен по true north.
- Использовал rasterio.warp.reproject с Resampling.nearest, pixel_size=2м.
- Изменил qa_map():
  · Считает bounds патча в 3857
  · Создаёт target grid (w_target × h_target) через from_origin
  · reproject для owner и quality
  · RGBA строится из owner_3857/quality_3857
  · Сохраняет owner_map_3857.tif для удобства
  · Bounds в 4326 вычисляются из 3857-патча
- Результат: 532×1734 (3413) → **3471×1833 (3857)** — ориентация сменилась на правильную (широкий, не длинный).
- Spatial verification: тестовые точки (30.2395, 59.9264) и (30.290, 59.941) дают ОДИНАКОВЫЕ owner-значения в обоих растрах → репроекция не внесла ошибок.
- Playwright screenshot HTML → VLM подтвердил: "совпадает, тёмная дорожная полоса лежит поверх реальной дороги, жёлтый полигон обводит ту же дорогу, красные блоки покрывают реальные здания. Смещения или повороты не обнаружены".

Stage Summary:
- Root cause: EPSG:3413 (polar stereographic) развёрнут относительно true north в СПб.
- Folium ImageOverlay не умеет повёрнутые растры → нужно перепроецировать в Web Mercator.
- Файлы обновлены: scripts/01_owner_map.py, download/owner_map.tif (исходный в 3413), download/owner_map_3857.tif (новый, для folium), download/owner_qa_map.html, download/owner_qa.json, download/control_points_qa.csv, download/carriageway.geojson.

Next:
- Пользователь открывает новый owner_qa_map.html. Растр должен совпадать со спутником.
- Затем модуль 02: робастная реконструкция h=f(picket)+g(offset) по владельцу road.


