#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""6.50c — каталог отметок v2: применение исправлений перепроверки 6.50a/b,
отбраковка ненадёжных, дедуп, геопривязка (main + NW affine), CSV/GeoJSON,
аннотированный обзор, валидация против fusion A2."""
import csv, json, math
import numpy as np
import rasterio

CAT = '/home/z/my-project/download/atayanc_6.40/marks_catalog_6.40.csv'
OUTD = '/home/z/my-project/download/atayanc_6.50'
FUSE = '/tmp/my-project/download/mosaic_6.00/spb_terra_fusion_6.00_A2.tif'

# исправленные значения (вычитка 6.50a rs_00..rs_18 + 6.50b rc_00..rc_08)
CORR = {
 'm001':(1.06,'M'),'m004':(3.38,'M'),'m005':(3.87,'M'),'m009':(3.03,'M'),
 'm013':(3.13,'M'),'m017':(2.40,'L'),'m019':(1.20,'M'),'m020':(2.60,'M'),
 'm021':(3.29,'M'),'m022':(2.60,'L'),'m024':(3.06,'M'),'m026':(2.56,'M'),
 'm032':(2.15,'M'),'m035':(6.84,'L'),'m037':(3.07,'M'),'m039':(1.01,'L'),
 'm046':(1.10,'L'),'m051':(2.45,'L'),'m053':(3.39,'M'),'m057':(2.56,'L'),
 'm061':(3.43,'M'),'m062':(4.27,'M'),'m066':(2.79,'M'),'m073':(1.90,'L'),
 'm078':(2.46,'M'),'m084':(3.76,'M'),'m086':(2.55,'L'),'m087':(3.04,'M'),
 'm088':(2.79,'M'),'m091':(3.73,'M'),'m101':(2.94,'M'),'m111':(1.10,'L'),
 'm118':(2.88,'L'),'m119':(3.80,'M'),'m122':(2.56,'M'),'m124':(2.78,'M'),
 'm127':(3.08,'M'),'m130':(3.14,'M'),'m131':(2.50,'L'),'m150':(2.64,'L'),
 'm154':(3.00,'M'),'m156':(3.37,'M'),'m159':(2.60,'L'),'m162':(2.07,'M'),
 'm163':(3.50,'M'),'m164':(3.46,'M'),'m165':(4.12,'M'),'m166':(2.07,'M'),
 'm167':(3.86,'M'),'m168':(3.53,'M'),'m169':(3.85,'M'),'m170':(3.90,'M'),
 'm171':(3.77,'M'),'m173':(3.41,'M'),'m174':(3.43,'M'),'m175':(3.79,'M'),
 'm182':(3.23,'M'),'m188':(2.72,'L'),'m190':(3.13,'L'),'m192':(3.32,'M'),
 'm194':(3.38,'M'),'m198':(3.04,'M'),'m201':(4.02,'L'),'m204':(3.09,'M'),
 'm207':(3.41,'M'),'m213':(3.09,'M'),'m214':(3.09,'M'),'m217':(2.55,'L'),
 'm228':(3.22,'L'),'m230':(2.55,'L'),'m236':(2.56,'L'),'m238':(2.74,'L'),
 'm245':(3.37,'M'),'m252':(2.76,'L'),'m254':(1.31,'L'),'m255':(2.79,'M'),
 'm256':(6.40,'L'),'m257':(6.01,'L'),'m260':(3.42,'L'),'m261':(2.93,'L'),
 'm262':(0.49,'L'),'m264':(2.75,'M'),'m266':(2.75,'L'),'m268':(2.82,'L'),
 'm269':(2.92,'L'),'m272':(4.23,'L'),'m273':(3.35,'L'),'m278':(3.02,'M'),
 'm286':(3.31,'L'),'m289':(3.22,'L'),'m294':(2.43,'M'),'m297':(2.45,'L'),
 'm299':(3.01,'L'),'m300':(3.01,'L'),'m303':(3.58,'L'),'m304':(4.00,'L'),
 'm308':(3.18,'L'),'m315':(2.71,'L'),'m325':(3.02,'L'),'m327':(3.00,'L'),
 'm332':(1.51,'L'),'m333':(3.03,'L'),'m336':(2.88,'L'),'m342':(3.76,'L'),
 'm345':(3.13,'L'),'m348':(2.72,'L'),'m352':(2.86,'L'),'m357':(3.55,'L'),
 'm360':(2.61,'L'),'m364':(2.89,'L'),'m366':(4.02,'L'),'m368':(1.03,'L'),
 'm371':(3.04,'L'),'m373':(2.99,'L'),'m374':(2.85,'L'),'m375':(2.82,'L'),
 'm377':(2.99,'L'),
}
# подтверждены перепроверкой (уверенность повышена, значение без изменений)
CONF_UP = {'m014':'M','m041':'M','m065':'L','m089':'M','m095':'L','m178':'M',
           'm208':'M','m253':'M','m326':'M','m346':'L','m347':'L'}
# отбраковка: нет текста в позиции / двусмысленная привязка / не высота
DROP = {'m023','m027','m063','m064','m068','m081','m104','m105','m135','m157',
        'm183','m184','m218','m219','m221','m225','m227','m232','m233','m235',
        'm244','m246','m258','m263','m265','m270','m275','m276','m277','m279',
        'm284','m287','m288','m293','m302','m307','m312','m313','m314','m317',
        'm331','m337','m340','m349','m351'}

rows = list(csv.DictReader(open(CAT)))
pts = []
for r in rows:
    mid = r['id']
    if mid in DROP:
        continue
    val, conf = float(r['h_bs77_m']), r['conf']
    if mid in CORR:
        val, conf = CORR[mid]
    elif mid in CONF_UP:
        conf = CONF_UP[mid]
    pts.append({'id': mid, 'px': int(r['px']), 'py': int(r['py']), 'val': val,
                'conf': conf, 'src': r['src'],
                'nw': bool(int(r['nw_fragment'])), 'fixed': mid in CORR})
print('после отбраковки:', len(pts), '(было', len(rows), ', брак', len(DROP), ',',
      'исправлено', sum(1 for p in pts if p['fixed']), ')')

# дедуп <14 px (оставляем более уверенный)
RANK = {'H': 3, 'M': 2, 'L': 1}
pts.sort(key=lambda p: -RANK[p['conf']])
final = []
dups = []
for p in pts:
    dup = None
    for q in final:
        if abs(q['px'] - p['px']) < 14 and abs(q['py'] - p['py']) < 14:
            dup = q
            break
    if dup is None:
        final.append(p)
    else:
        dups.append((p['id'], dup['id']))
print('дубли сняты:', dups)
final.sort(key=lambda p: p['id'])

# геопривязка: пикет = буллет слева от текста (~19 px)
geo = json.load(open('/home/z/my-project/download/atayanc_6.30/georef_6.30.json'))
a0, b0, c0, d0, e0, f0 = geo['affine_deg']
nw = json.load(open('/tmp/my-project/results/atayanc_6.30/nw_affine_6.30.json'))
na = nw['aff_m']
MLON = 111320.0 * math.cos(math.radians(59.95))
MLAT = 111132.0
M = np.array([[a0, b0], [d0, e0]])
for p in final:
    bx, by = p['px'] - 19, p['py']
    if p['nw']:
        E = na[0] * bx + na[1] * by + na[2]
        Nn = na[3] * bx + na[4] * by + na[5]
        p['lon'], p['lat'] = E / MLON, Nn / MLAT
    else:
        lon, lat = M @ np.array([bx, by]) + np.array([c0, f0])
        p['lon'], p['lat'] = float(lon), float(lat)

# валидация против fusion A2 (5x5 медиана)
with rasterio.open(FUSE) as fs:
    fvals = []
    for p in final:
        r, c = fs.index(p['lon'], p['lat'])
        if 0 <= r < fs.height and 0 <= c < fs.width:
            w = fs.read(1, window=rasterio.windows.Window(c - 2, r - 2, 5, 5))
            wv = w[w > -9000]
            fvals.append(float(np.median(wv)) / 100.0 if len(wv) else np.nan)
        else:
            fvals.append(np.nan)
fvals = np.array(fvals)
vs = np.array([p['val'] for p in final])
ok = ~np.isnan(fvals)
d = vs[ok] - fvals[ok]
print(f'валидация vs fusion A2: n={int(ok.sum())}/{len(vs)}, bias {d.mean():+.2f} м, '
      f'MAD {np.median(np.abs(d)):.2f} м, p95 {np.percentile(np.abs(d), 95):.2f} м')
cl = np.abs(d) <= 2.0
print(f'в пределах +-2 м: {int(cl.sum())} ({100.0 * cl.sum() / ok.sum():.0f}%), '
      f'bias(кл) {d[cl].mean():+.2f}, MAD(кл) {np.median(np.abs(d[cl])):.2f}')
for p, fv in zip(final, fvals):
    if not np.isnan(fv):
        p['fusion'] = round(fv, 2)
        p['diff'] = round(p['val'] - fv, 2)

# CSV
with open(f'{OUTD}/marks_catalog_6.50.csv', 'w', newline='') as fh:
    w = csv.writer(fh)
    w.writerow(['id', 'px', 'py', 'px_bullet', 'h_bs77_m', 'conf', 'src',
                'nw_fragment', 'lon', 'lat', 'fusion_a2_m', 'diff_m', 'fixed_650'])
    for i, p in enumerate(final):
        w.writerow([p['id'], p['px'], p['py'], p['px'] - 19, p['val'], p['conf'],
                    p['src'], int(p['nw']), round(p['lon'], 6), round(p['lat'], 6),
                    p.get('fusion', ''), p.get('diff', ''), int(p['fixed'])])

feats = []
for p in final:
    props = {'id': p['id'], 'h_bs77': p['val'], 'conf': p['conf'], 'src': p['src'],
             'nw_fragment': p['nw'], 'px_text': [p['px'], p['py']],
             'px_bullet': [p['px'] - 19, p['py']], 'fixed_650': p['fixed']}
    if 'fusion' in p:
        props['fusion_a2'] = p['fusion']
        props['diff'] = p['diff']
    feats.append({'type': 'Feature', 'properties': props,
                  'geometry': {'type': 'Point',
                               'coordinates': [round(p['lon'], 6), round(p['lat'], 6)]}})
json.dump({'type': 'FeatureCollection', 'features': feats},
          open(f'{OUTD}/plan_marks_6.50.geojson', 'w'), ensure_ascii=False)

# обзор с аннотацией
from PIL import Image, ImageDraw, ImageFont
img = Image.open('/home/z/my-project/upload/atayanc-2013-01.jpg').convert('RGB')
dr = ImageDraw.Draw(img)
try:
    f12 = ImageFont.load_default(size=12)
except TypeError:
    f12 = ImageFont.load_default()
for p in final:
    x, y = p['px'], p['py']
    col = {'H': (0, 160, 0), 'M': (0, 90, 220), 'L': (200, 140, 0)}[p['conf']]
    dr.ellipse([x - 4, y - 4, x + 4, y + 4], outline=col, width=2)
    dr.ellipse([x - 23, y - 4, x - 15, y + 4], outline=(0, 170, 170), width=1)
    dr.text((x + 7, y - 14), f"{p['val']:.2f}", fill=(180, 0, 0), font=f12)
img.save(f'{OUTD}/overview_marks_6.50.png')

print('значения: min %.2f max %.2f' % (min(p['val'] for p in final),
                                        max(p['val'] for p in final)))
print('по уверенности:', {k: sum(1 for p in final if p['conf'] == k) for k in 'HML'})
print('исправленных:', sum(1 for p in final if p['fixed']),
      '/ СЗ-фрагмент:', sum(1 for p in final if p['nw']))
print('CSV/GeoJSON/обзор записаны; отметок:', len(final))
