#!/usr/bin/env python3
# 5.70: Независимая перепроверка lenobl 5.49 (этап ③ приёмка) + триаж 12 decrees
# Пересчёт pandas заявленных метрик: n=468, med dH=-2.66, MAD=1.68, corr=0.997
import json, re
import numpy as np
import pandas as pd

REPO = '/home/z/my-project/repo'
out = {}

# ---------- 1. Профиль 102-р vs GLO-30 ----------
df = pd.read_csv(f'{REPO}/results/lenobl_102_profile_glo30_5.49.csv')
n = len(df)
med = df['dH'].median()
mad = (df['dH'] - med).abs().median()          # MAD вокруг медианы
mad_raw = df['dH'].abs().median()              # как в 5.49 (|dH| median)
corr = np.corrcoef(df['h_black'], df['h_glo30'])[0, 1]
p95 = df['dH'].abs().quantile(0.95)
out['profile'] = {
    'n': n, 'median_dH': round(med, 3), 'MAD_around_median': round(mad, 3),
    'MAD_raw_median_abs': round(mad_raw, 3), 'corr': round(corr, 4),
    'abs_dH_p95': round(p95, 2),
    'lat_span': [round(df.lat.min(), 4), round(df.lat.max(), 4)],
    'lon_span': [round(df.lon.min(), 4), round(df.lon.max(), 4)],
    'station_span_m': [float(df.station_m.min()), float(df.station_m.max())],
}
# канопи-байас сегменты (из 5.49: -1.70 населёнка / -4.31 лес) — проверим грубо:
# нижняя огибающая (min h_black по окну станций) ~ земля; dH_ground = min-огибающая - GLO-30
w = 10
gb = df.groupby(df.index // w)['h_black'].min()
gg = df.groupby(df.index // w)['h_glo30'].first()
dH_ground = (gb - gg)
out['profile_ground_envelope'] = {
    'n_windows': int(len(dH_ground)),
    'median_dH_ground': round(float(dH_ground.median()), 3),
}

# ---------- 2. ГГС пункты ----------
with open(f'{REPO}/results/lenobl_102_validation_5.49.json') as f:
    v = json.load(f)
ggs = pd.DataFrame(v['ggs_vs_glo30'])
ok = ggs[ggs['dH'].abs() <= 1.1]
out['ggs'] = {
    'n': len(ggs), 'within_pm1.1': len(ok),
    'max_abs_ok': round(ggs[ggs['dH'].abs() < 10]['dH'].abs().max(), 3),
    'outliers': ggs[ggs['dH'].abs() > 5][['name', 'dH']].to_dict('records'),
}

# ---------- 3. Триаж 12 decrees: локация объектов ----------
with open(f'{REPO}/results/lenobl_manifest_5.48.json') as f:
    man = json.load(f)
items = man if isinstance(man, list) else man.get('decrees', man.get('items', []))
loc_kw = {
    'spb_border': ['всеволож', 'токсово', 'дубровка', 'ваганово', 'новосаратовск',
                   'яв looming'],  # районы, прилегающие к СПб
    'far_east': ['пикал', 'бокситогор', 'тихвин', 'волхов', 'волосов', 'луг'],
    'gulf_belt': ['сосновобор', 'кингисепп', 'ломоносов', 'вистино', 'уст-луга'],
}
triage = []
for it in items:
    rid = it.get('rid', '?')
    pdfs = it.get('pdfs', [])
    names = ' | '.join(pdfs) if isinstance(pdfs, list) else str(pdfs)
    if isinstance(pdfs, list) and pdfs and isinstance(pdfs[0], dict):
        names = ' | '.join(p.get('name', p.get('pdf', '')) for p in pdfs)
    tl = (it.get('title', '') + ' ' + names).lower()
    hits = {k: [w for w in ws if w in tl] for k, ws in loc_kw.items()}
    hits = {k: v for k, v in hits.items() if v}
    triage.append({'rid': rid, 'black': it.get('black'), 'red': it.get('red'),
                   'n_pdfs': len(pdfs) if isinstance(pdfs, list) else '?',
                   'kw_hits': hits})
out['decree_triage'] = triage

# имена PDF — покажем первые уникальные для контекста
uniq_names = set()
for it in items:
    pdfs = it.get('pdfs', [])
    if isinstance(pdfs, list):
        for p in pdfs:
            nm = p.get('name', p.get('pdf', '')) if isinstance(p, dict) else str(p)
            if nm: uniq_names.add(nm[:90])
out['sample_pdf_names'] = sorted(uniq_names)[:40]

with open(f'{REPO}/results/lenobl_570_verify.json', 'w') as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(json.dumps(out, ensure_ascii=False, indent=1)[:4200])
