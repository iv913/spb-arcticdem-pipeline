#!/usr/bin/env python3
"""accept.py — протокол приёмки поверхности (AGENT_HANDOFF B5).

Usage:
    python3 scripts/validation/accept.py <pool_v9.csv> [--base <base_pool.csv>] [--out <report.json>]

На вход:
  - pool_v9.csv: валидационный пул (type, source, lat, lon, h_survey, h_model, dH, ...)
  - --base: опционально, базовый пул для парного теста «новое против базы»

На выход: JSON с метриками по §1 AGENT_HANDOFF:
  - земля / ICESat по трекам (раздельно)
  - районы, тиры, полоса 0-5 м
  - парные тесты (знаковый, Уилкоксон, bootstrap CI, порог 0.1 м)
"""
import argparse, csv, json, math, statistics, sys
from collections import defaultdict
from pathlib import Path
import numpy as np

# stats_toolkit
sys.path.insert(0, str(Path(__file__).parent.parent / 'stats_toolkit'))
try:
    from core import sign_test, wilcoxon_signed_rank, bootstrap_ci_median, practical_significance
except ImportError:
    sign_test = wilcoxon_signed_rank = bootstrap_ci_median = practical_significance = None

DISTRICTS = {
    'В.О.': (59.92, 30.15, 59.96, 30.30),
    'Центральный': (59.92, 30.27, 59.95, 30.38),
    'Адмиралтейский': (59.91, 30.27, 59.94, 30.33),
    'Петроградский': (59.95, 30.27, 59.98, 30.34),
    'Калининский': (59.98, 30.36, 60.03, 30.43),
    'Выборгский': (60.00, 30.30, 60.05, 30.40),
    'Приморский': (59.98, 30.13, 60.05, 30.30),
    'Московский': (59.86, 30.28, 59.92, 30.36),
    'Невский': (59.86, 30.40, 59.93, 30.50),
    'Кировский': (59.85, 30.20, 59.92, 30.27),
    'Красносельский': (59.80, 30.10, 59.88, 30.27),
    'Кронштадт': (59.97, 29.70, 60.00, 29.85),
}

def find_district(lat, lon):
    for name, (s, w, n, e) in DISTRICTS.items():
        if s <= lat <= n and w <= lon <= e:
            return name
    return 'Other'

def load_pool(path):
    ground, icesat = [], []
    for r in csv.DictReader(open(path)):
        if r['type'] == 'ground':
            try:
                ground.append({
                    'lat': float(r['lat']), 'lon': float(r['lon']),
                    'h_survey': float(r['h_survey']), 'h_model': float(r['h_model']),
                    'dH': float(r['dH']), 'source': r['source'],
                    'district': find_district(float(r['lat']), float(r['lon'])),
                    'tier': 'gold' if r['source'] == 'passport_holdout' else 'rest',
                    'in_0_5m': 0.0 <= float(r['h_survey']) <= 5.0,
                })
            except: pass
        elif r['type'] == 'icesat':
            try: icesat.append({'dH': float(r['dH']), 'source': r['source'], 'extra': r.get('extra', '')})
            except: pass
    return ground, icesat

def stats(dHs):
    if not dHs: return {'n': 0}
    abs_dHs = [abs(d) for d in dHs]
    r = {
        'n': len(dHs), 'median_dH': round(statistics.median(dHs), 4),
        'mad': round(statistics.median(abs_dHs), 4),
        'mean_abs_dH': round(statistics.mean(abs_dHs), 4),
        'max_abs_dH': round(max(abs_dHs), 4),
        'pct_gt_1m': round(100 * sum(1 for d in abs_dHs if d > 1.0) / len(abs_dHs), 1),
    }
    if len(abs_dHs) > 20:
        r['p95'] = round(sorted(abs_dHs)[int(0.95 * len(abs_dHs))], 4)
    if sign_test and len(dHs) >= 5:
        st = sign_test(dHs)
        r['sign_test_p'] = round(st.p_value, 6)
        r['sign_test_dir'] = st.direction
    if wilcoxon_signed_rank and len(dHs) >= 10:
        w = wilcoxon_signed_rank(dHs)
        if w: r['wilcoxon_p'] = round(w.p_value, 6)
    if bootstrap_ci_median and len(dHs) >= 5:
        b = bootstrap_ci_median(dHs)
        if b: r['bootstrap_ci95'] = b.ci_95
    if practical_significance:
        ps = practical_significance(dHs)
        r['practical_sig'] = ps.practically_significant
    return r

def run(pool_csv, base_csv=None):
    ground, icesat = load_pool(pool_csv)
    report = {
        'pool': str(pool_csv), 'total_ground': len(ground), 'total_icesat_rgt': len(icesat),
        'metrics': {}, 'verdict': {},
    }
    # Ground all
    report['metrics']['ground_all'] = stats([p['dH'] for p in ground])
    # By source
    by_s = defaultdict(list)
    for p in ground: by_s[p['source']].append(p['dH'])
    report['metrics']['ground_by_source'] = {s: stats(d) for s, d in sorted(by_s.items())}
    # By district
    by_d = defaultdict(list)
    for p in ground: by_d[p['district']].append(p['dH'])
    report['metrics']['ground_by_district'] = {d: stats(v) for d, v in sorted(by_d.items(), key=lambda x: -len(x[1])) if len(v) >= 3}
    # By tier
    by_t = defaultdict(list)
    for p in ground: by_t[p['tier']].append(p['dH'])
    report['metrics']['ground_by_tier'] = {t: stats(v) for t, v in sorted(by_t.items())}
    # By age (B6: dH vs source age)
    source_ages = {
        'atayanc_v2_confirmed': {'t_obs': '≤1990', 'sigma_t': 5, 'kind': 'ground_marks_pre_1991'},
        'archi_514913': {'t_obs': 'unknown', 'sigma_t': 5, 'kind': 'engineering_plan'},
        'archi_95211_tihaya_gavan': {'t_obs': 'unknown', 'sigma_t': 5, 'kind': 'engineering_plan'},
        'passport_holdout': {'t_obs': 'unknown', 'sigma_t': 10, 'kind': 'wall_mark'},
    }
    by_age = defaultdict(list)
    for p in ground:
        info = source_ages.get(p['source'], {})
        by_age[info.get('t_obs', 'unknown')].append(p['dH'])
    report['metrics']['ground_by_age'] = {age: stats(v) for age, v in sorted(by_age.items()) if len(v) >= 3}
    report['metrics']['ground_by_age']['note'] = 'B6: dH vs source age. Older sources may have real surface changes.'

    # 0-5m band
    report['metrics']['ground_0_5m'] = stats([p['dH'] for p in ground if p['in_0_5m']])
    # ICESat by RGT
    report['metrics']['icesat_rgt'] = stats([p['dH'] for p in icesat])
    report['metrics']['icesat_rgt']['n_eff'] = len(icesat)
    # Paired test
    if base_csv:
        bg, bi = load_pool(base_csv)
        if len(bg) == len(ground):
            diffs = [abs(ground[i]['dH']) - abs(bg[i]['dH']) for i in range(len(ground))]
            report['metrics']['paired_new_vs_base'] = stats(diffs)
            report['metrics']['paired_new_vs_base']['note'] = 'Negative = new is better'
    # Verdict
    g = report['metrics']['ground_all']
    i = report['metrics']['icesat_rgt']
    report['verdict'] = {
        'ground_mad': g.get('mad'), 'ground_n': g.get('n'),
        'icesat_n_eff': i.get('n_eff'), 'icesat_median_dH': i.get('median_dH'),
        'practical_threshold': 0.10,
        'note': 'Per §1.6: verdict by paired tests only. Ground/ICESat separate. ICESat n_eff=RGT count.',
    }
    return report

def main():
    p = argparse.ArgumentParser(description='Surface acceptance test (B5)')
    p.add_argument('pool', help='Validation pool CSV')
    p.add_argument('--base', help='Base pool CSV for paired test')
    p.add_argument('--out', default='', help='Output JSON path')
    a = p.parse_args()
    report = run(a.pool, a.base)
    out = json.dumps(report, indent=2, ensure_ascii=False, default=str)
    if a.out:
        Path(a.out).write_text(out)
        print(f'Report saved: {a.out}', file=sys.stderr)
    else:
        print(out)

if __name__ == '__main__':
    main()
