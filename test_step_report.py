#!/usr/bin/env python3
"""test_step_report.py — приёмка шага: ловим подмену выборки и обмен контролей."""
import sys, importlib.util as u
from pathlib import Path
import numpy as np, pandas as pd

src = (Path(__file__).parent / '14_step_report.py').read_text(encoding='utf-8')
ns = {'np': np}
exec(src[src.index('def stats'):src.index('def main')], ns)
stats, common_subset = ns['stats'], ns['common_subset']
compare, check_tradeoff = ns['compare'], ns['check_tradeoff']

PASS = []
def ok(name, cond, detail=''):
    PASS.append(bool(cond))
    print(f'  [{"OK " if cond else "FAIL"}] {name}' + (f' — {detail}' if detail else ''))

rng = np.random.default_rng(3)
print('=' * 72)
print('1. Подмена выборки не выдаётся за улучшение')
lat = np.round(rng.uniform(59.8, 60.0, 200), 6)
lon = np.round(rng.uniform(30.0, 30.4, 200), 6)
dH = rng.normal(0.2, 1.5, 200)
before = pd.DataFrame({'lat': lat, 'lon': lon, 'dH': dH})
worst = np.argsort(-np.abs(dH))[:50]
keep = np.setdiff1d(np.arange(200), worst)
after = before.iloc[keep].copy()
naive_b, naive_a = stats(before['dH'])['mad'], stats(after['dH'])['mad']
print(f'  наивно: MAD {naive_b:.3f} -> {naive_a:.3f} (выглядит как улучшение)')
b2, a2, n = common_subset(before, after)
c = compare(b2['dH'], a2['dH'], 'паспорт')
print(f'  на общем множестве (n={n}): MAD {c["before"]["mad"]:.3f} -> {c["after"]["mad"]:.3f} -> {c["verdict"]}')
ok('наивное сравнение показывает ложное улучшение', naive_a < naive_b - 0.1)
ok('на общем множестве улучшения нет', c['verdict'] == 'без изменений',
   'ровно так «улучшались» ICESat 0.441->0.316 и mad мозаики 1.25->2.09')

print('=' * 72)
print('2. Обмен контролей ловится')
cp = {'d_mad': +0.15, 'before': {}, 'after': {}}
cl = {'d_mad': -0.20, 'before': {}, 'after': {}}
tr = check_tradeoff(cp, cl)
print(f'  паспорт {cp["d_mad"]:+.2f}, лазер {cl["d_mad"]:+.2f} -> {tr["status"]}')
ok('обмен распознан', tr['status'] == 'ОБМЕН')
ok('объяснение есть', 'подогнана под лазер' in (tr.get('note') or ''),
   'ровно случай поправки -0.764')
tr2 = check_tradeoff({'d_mad': -0.10, 'before': {}, 'after': {}},
                     {'d_mad': -0.12, 'before': {}, 'after': {}})
ok('согласованное улучшение обменом не считается', tr2['status'] == 'обмена нет')

print('=' * 72)
print('3. Смещение улучшилось, разброс ухудшился')
b3 = rng.normal(0.9, 0.8, 300)
a3 = rng.normal(0.05, 1.3, 300)
c3 = compare(b3, a3, 'тест')
print(f'  медиана {c3["before"]["median"]:+.2f} -> {c3["after"]["median"]:+.2f}, MAD {c3["before"]["mad"]:.2f} -> {c3["after"]["mad"]:.2f}')
print(f'  вердикт: {c3["verdict"]}')
ok('вердикт по разбросу, а не по смещению', c3['verdict'] == 'ХУЖЕ',
   'смещение мы гоняли пять прогонов, а MAD стоял')
ok('предупреждение о подгонке', 'подгонки' in (c3.get('note') or ''))

print('=' * 72)
print('4. Тяжёлые хвосты помечаются')
a4 = np.concatenate([rng.normal(0, 0.3, 950), rng.normal(0, 6.0, 50)])
c4 = compare(rng.normal(0, 0.3, 1000), a4, 'хвосты')
print(f'  RMSE/MAD = {c4["after"]["rmse_over_mad"]}')
ok('хвосты замечены', 'хвосты' in (c4.get('note') or ''),
   'это подпись ICESat holdout: MAD 0.32 при RMSE 3.07')

print('=' * 72)
print(f'ИТОГО: {sum(PASS)}/{len(PASS)} проверок пройдено')
sys.exit(0 if all(PASS) else 1)
