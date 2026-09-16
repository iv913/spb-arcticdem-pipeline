#!/usr/bin/env python3
"""
14_step_report.py — приёмка ОДНОГО шага плана
"""
import argparse, json, sys
from pathlib import Path
import numpy as np

EXPECTATIONS = {
    'A': {'name': 'карта',
          'expect': 'метрики не меняются; проверка — совпадение с В.О.',
          'watch': ['ничего не должно поехать']},
    'B': {'name': 'намывы по датам',
          'expect': 'площадь намыва растёт выше 21.68 км², появляется Лахта',
          'watch': ['namyv_area_km2', 'bay_volume']},
    'C1': {'name': 'ICESat в опору до build_prior',
           'expect': 'train MAD перестаёт быть нулём; holdout улучшается; доля detail 23 падает',
           'watch': ['icesat_train_mad', 'icesat_holdout_mad', 'n_detail_23']},
    'C2': {'name': 'веса в build_prior',
           'expect': 'класс 23 улучшается; класс 21 не портится',
           'watch': ['detail_23_mad', 'detail_21_mad']},
    'C3': {'name': 'choose_block_px',
           'expect': 'блок выбран по данным, fallback=false',
           'watch': ['block_m', 'fallback']},
    'C4': {'name': 'cap_far_field',
           'expect': 'класс 23 подтягивается (на острове снял +1.51 м)',
           'watch': ['detail_23_median', 'detail_23_mad']},
    'C5': {'name': 'связность рельефа',
           'expect': 'три низкие марки у Смоленки улучшаются, остальные не меняются',
           'watch': ['low_marks_dH', 'other_marks_dH']},
    'C6': {'name': 'снять зажим ниже нуля',
           'expect': 'появляется счётчик отрицательных; метрики не меняются',
           'watch': ['n_below_zero']},
    'D': {'name': 'маска воды',
          'expect': 'зеркало губы ~330 км² вместо 426; два расчёта сходятся',
          'watch': ['mirror_km2', 'bay_calc_gap_pct']},
}

def stats(a, name='dH'):
    a = np.asarray(a, dtype=float)
    a = a[np.isfinite(a)]
    if a.size == 0: return {'n': 0}
    med = float(np.median(a))
    return {'n': int(a.size), 'median': round(med, 3),
            'mad': round(float(np.median(np.abs(a - med)) * 1.4826), 3),
            'rmse': round(float(np.sqrt(np.mean(a ** 2))), 3),
            'frac_gt_1m': round(float(np.mean(np.abs(a) > 1.0)), 3),
            'frac_gt_2m': round(float(np.mean(np.abs(a) > 2.0)), 3),
            'rmse_over_mad': round(float(np.sqrt(np.mean(a ** 2)) / max(1e-6, np.median(np.abs(a - med)) * 1.4826)), 2)}

def common_subset(df_before, df_after, key_cols=('lat', 'lon')):
    import pandas as pd
    kb = df_before[list(key_cols)].round(6).astype(str).agg('_'.join, axis=1)
    ka = df_after[list(key_cols)].round(6).astype(str).agg('_'.join, axis=1)
    common = set(kb) & set(ka)
    b = df_before[kb.isin(common)].copy()
    a = df_after[ka.isin(common)].copy()
    return b, a, len(common)

def compare(before, after, label):
    sb, sa = stats(before), stats(after)
    if not sb.get('n') or not sa.get('n'): return {'label': label, 'status': 'нет данных'}
    d_mad = sa['mad'] - sb['mad']
    d_med = abs(sa['median']) - abs(sb['median'])
    d_out = sa['frac_gt_2m'] - sb['frac_gt_2m']
    if d_mad < -0.02 and d_out <= 0.01: verdict = 'лучше'
    elif d_mad > 0.02 or d_out > 0.02: verdict = 'ХУЖЕ'
    else: verdict = 'без изменений'
    note = None
    if d_mad > 0.02 and d_med < -0.02:
        note = ('смещение улучшилось, разброс ухудшился — признак подгонки или постоянного сдвига')
    if sa['rmse_over_mad'] > 4:
        note = ((note + '; ') if note else '') + f'RMSE/MAD = {sa["rmse_over_mad"]} — тяжёлые хвосты, несколько процентов точек с большой ошибкой'
    return {'label': label, 'before': sb, 'after': sa, 'd_mad': round(d_mad, 3),
            'd_median_abs': round(d_med, 3), 'd_frac_gt_2m': round(d_out, 3),
            'verdict': verdict, 'note': note}

def check_tradeoff(cmp_passport, cmp_laser):
    if 'before' not in cmp_passport or 'before' not in cmp_laser: return None
    p, l = cmp_passport['d_mad'], cmp_laser['d_mad']
    if p > 0.02 and l < -0.02:
        return {'status': 'ОБМЕН', 'passport_d_mad': p, 'laser_d_mad': l,
                'note': 'паспорт ухудшился, лазер улучшился — правка подогнана под лазер; паспорт задан в БС-77 и является единственным контролем датума'}
    if l > 0.02 and p < -0.02:
        return {'status': 'ОБМЕН', 'passport_d_mad': p, 'laser_d_mad': l,
                'note': 'лазер ухудшился, паспорт улучшился'}
    return {'status': 'обмена нет', 'passport_d_mad': p, 'laser_d_mad': l}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--step', required=True)
    ap.add_argument('--points-before', required=True)
    ap.add_argument('--points-after', required=True)
    ap.add_argument('--laser-before')
    ap.add_argument('--laser-after')
    ap.add_argument('--out', default='step_report.json')
    a = ap.parse_args()

    import pandas as pd
    pb = pd.read_csv(a.points_before)
    pa = pd.read_csv(a.points_after)
    b, aa, n_common = common_subset(pb, pa)
    print(f'Паспорт: было {len(pb)}, стало {len(pa)}, общих {n_common}')
    if n_common < 0.8 * min(len(pb), len(pa)):
        print('  ⚠ пересечение мало — выборки сильно разные')

    cp = compare(b['dH'], aa['dH'], 'паспорт (общее множество)')
    print(f'  {cp["verdict"]}: MAD {cp["before"]["mad"]} -> {cp["after"]["mad"]} ({cp["d_mad"]:+.3f}), вне 2 м {cp["before"]["frac_gt_2m"]:.0%} -> {cp["after"]["frac_gt_2m"]:.0%}')
    if cp.get('note'): print(f'  ⚠ {cp["note"]}')

    cl = {'label': 'лазер'}
    if a.laser_before and a.laser_after:
        lb, la = pd.read_csv(a.laser_before), pd.read_csv(a.laser_after)
        lb2, la2, nl = common_subset(lb, la, key_cols=('x', 'y'))
        print(f'Лазер: было {len(lb)}, стало {len(la)}, общих {nl}')
        cl = compare(lb2['dH'], la2['dH'], 'лазер (общее множество)')
        print(f'  {cl["verdict"]}: MAD {cl["before"]["mad"]} -> {cl["after"]["mad"]} ({cl["d_mad"]:+.3f})')

    tr = check_tradeoff(cp, cl)
    if tr:
        print(f'Обмен контролей: {tr["status"]}')
        if tr.get('note'): print(f'  ⚠ {tr["note"]}')

    exp = EXPECTATIONS.get(a.step, {})
    if exp: print(f'Шаг {a.step} ({exp["name"]}): ожидалось — {exp["expect"]}')

    Path(a.out).write_text(json.dumps({'step': a.step, 'expectation': exp, 'n_common_passport': n_common, 'passport': cp, 'laser': cl, 'tradeoff': tr}, ensure_ascii=False, indent=2))
    print(f'✓ {a.out}')

if __name__ == '__main__':
    main()
