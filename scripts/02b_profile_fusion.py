#!/usr/bin/env python3
"""02b_profile_fusion.py — Theil-Sen fusion с по-бинными воротами."""
import json
from pathlib import Path
from datetime import datetime
import numpy as np
import pandas as pd
import matplotlib; matplotlib.use('Agg')
import matplotlib.pyplot as plt

HOME = Path('/home/z/my-project'); OUT = HOME / 'download'
PROF_DIR = OUT / 'profiles'
REF_SOURCE = 'mosaic'; MIN_COMMON = 10; MAX_SHAPE_RMSE = 0.6
CONFIRM_TOL = 0.5; PK_BIN = 5.0; MAX_TILT_M_PER_KM = 5.0
EDGE_TRIM_M = 100.0; GATE_VS_REF = 0.6; RESTORE_TOL = 0.75

def log(msg): print(f'[{datetime.now():%H:%M:%S}] {msg}', flush=True)

def _theil_sen(x, y, max_pairs=20000, seed=0):
    rng = np.random.RandomState(seed); n = len(x)
    ii, jj = np.triu_indices(n, 1)
    if len(ii) > max_pairs:
        sel = rng.choice(len(ii), max_pairs, replace=False); ii, jj = ii[sel], jj[sel]
    dx = x[jj] - x[ii]; ok = np.abs(dx) > 1e-9
    b = float(np.median((y[jj][ok] - y[ii][ok]) / dx[ok]))
    a = float(np.median(y - b * x)); return a, b

def align_and_fuse(profiles, ref=REF_SOURCE, min_common=MIN_COMMON,
                   max_shape_rmse=MAX_SHAPE_RMSE, confirm_tol=CONFIRM_TOL):
    ref_kept = profiles[ref][profiles[ref]['kept']].set_index('b')[['pk','h']]
    corr, accepted, report = {ref: (0.0, 0.0)}, [ref], {}
    for name, df in profiles.items():
        if name == ref: continue
        com = df[df['kept']].set_index('b')[['pk','h']].join(ref_kept, how='inner', lsuffix='_s', rsuffix='_r')
        if len(com) < min_common: report[name] = {'status':'dropped_few_common','n_common':int(len(com))}; continue
        x = com['pk_s'].values.astype(float); d = (com['h_s']-com['h_r']).values.astype(float)
        med = np.median(d); mad = np.median(np.abs(d-med))*1.4826; m = np.abs(d-med) <= 3*max(0.15,mad)
        a, b = _theil_sen(x[m], d[m]); resid = d - (a + b*x)
        madr = np.median(np.abs(resid-np.median(resid)))*1.4826; mm = np.abs(resid) <= 3*max(0.15,madr)
        rmse = float(np.sqrt((resid[mm]**2).mean()))
        rec = {'shift_m':round(a,3),'tilt_m_per_km':round(b*1000,3),'rmse_shape_m':round(rmse,3),'n_common':int(len(com))}
        if rmse > max_shape_rmse: rec['status']='dropped_shape'; report[name]=rec; continue
        if abs(b)*1000 > MAX_TILT_M_PER_KM: rec['status']='dropped_tilt'; report[name]=rec; continue
        corr[name] = (a,b); accepted.append(name); rec['status']='ok'; report[name]=rec

    idx = {n: profiles[n].set_index('b') for n in accepted}
    cover = {n: (float(profiles[n][profiles[n]['kept']]['pk'].min()), float(profiles[n][profiles[n]['kept']]['pk'].max())) for n in accepted}
    allb = sorted(set().union(*[set(profiles[n]['b']) for n in accepted]))
    ref_idx = profiles[ref].set_index('b')
    cands = {}
    for b_ in allb:
        cc = []
        for n in accepted:
            if b_ not in idx[n].index or not bool(idx[n].loc[b_,'kept']): continue
            pk_n = float(idx[n].loc[b_,'pk'])
            if n != ref:
                lo, hi = cover[n]
                if pk_n-lo < EDGE_TRIM_M or hi-pk_n < EDGE_TRIM_M: continue
            a_n, t_n = corr[n]; cc.append((n, float(idx[n].loc[b_,'h'])-(a_n+t_n*pk_n)))
        hr = next((v for n,v in cc if n==ref), None)
        if hr is not None: cc = [(n,v) for n,v in cc if n==ref or abs(v-hr)<=GATE_VS_REF]
        cands[b_] = cc
    prov = {b_: (float(np.median([v for _,v in cc])) if cc else np.nan) for b_,cc in cands.items()}

    rows = []
    for b_ in allb:
        cc = cands[b_]
        pk = float(idx[accepted[0]].loc[b_,'pk']) if b_ in idx[accepted[0]].index else (b_+0.5)*PK_BIN
        if not cc: rows.append({'b':int(b_),'pk':pk,'h_fused':np.nan,'n_src':0,'status':'gap'}); continue
        h = float(np.median([v for _,v in cc]))
        row = {'b':int(b_),'pk':pk,'h_fused':h,'n_src':len(cc)}
        for n,v in cc: row[f'h_{n}'] = v
        ref_pruned = (b_ in ref_idx.index and not bool(ref_idx.loc[b_,'kept']))
        if ref_pruned:
            spread_ok = (len(cc)>=2 and max(v for _,v in cc)-min(v for _,v in cc) <= confirm_tol)
            if spread_ok: row['status']='restored'
            else:
                nb = [prov[bb] for bb in range(b_-3,b_+4) if bb in prov and bb!=b_ and np.isfinite(prov[bb])]
                if len(nb)>=2 and abs(h-np.median(nb))<=RESTORE_TOL: row['status']='restored'
                else: row['status']='rejected_restore'; row['h_fused']=np.nan; row['n_src']=0; rows.append(row); continue
        elif len(cc)>=2 and (max(v for _,v in cc)-min(v for _,v in cc)) <= confirm_tol: row['status']='confirmed'
        else: row['status']='single'
        rows.append(row)
    return pd.DataFrame(rows), corr, report

def main():
    log('='*60); log('02b_profile_fusion: Theil-Sen + per-bin gates'); log('='*60)
    profiles = {}
    for f in sorted(PROF_DIR.glob('profile_*.csv')):
        name = f.stem.replace('profile_',''); df = pd.read_csv(f)
        if not {'b','pk','h','kept'} <= set(df.columns): continue
        df['kept'] = df['kept'].astype(bool); profiles[name] = df
        log(f'  {name}: {len(df)} бинов, kept={int(df["kept"].sum())}')
    fused, corr, report = align_and_fuse(profiles)
    st = fused['status'].value_counts().to_dict()
    log(f'Коррекции: {corr}'); log(f'Бины: {st}')
    fused.to_csv(OUT/'fused_profile.csv', index=False)
    qa = {'corrections':{k:{'shift_m':a,'tilt_m_per_km':b*1000} for k,(a,b) in corr.items()},
          'sources':report, 'bins':st}
    (OUT/'fusion_qa.json').write_text(json.dumps(qa, ensure_ascii=False, indent=2))
    # график
    fig, ax = plt.subplots(figsize=(15,6))
    colors = {'confirmed':'green','restored':'orange','single':'steelblue','gap':'red','rejected_restore':'darkred'}
    for stt,c in colors.items():
        sub = fused[fused['status']==stt]
        if stt=='gap':
            for _,r in sub.iterrows(): ax.axvline(r['pk'],color=c,alpha=0.15,lw=1)
        else: ax.scatter(sub['pk'],sub['h_fused'],s=12,c=c,label=f'{stt}({len(sub)})')
    for name,df in profiles.items():
        kk = df[df['kept']]
        if name in corr: a_n,t_n=corr[name]; ax.plot(kk['pk'],kk['h']-(a_n+t_n*kk['pk']),'.',ms=2,alpha=0.25,label=f'{name}(aligned)')
        else: ax.plot(kk['pk'],kk['h'],'.',ms=1,alpha=0.10,label=f'{name}(drop)')
    ax.legend(fontsize=8,ncol=2); ax.grid(alpha=0.3)
    ax.set_title('Fusion: тени мозаики восстановлены стрипами (Theil-Sen + ворота)')
    plt.tight_layout(); plt.savefig(OUT/'fusion_profile.png',dpi=140)
    log(f'✓ fused_profile.csv, fusion_qa.json, fusion_profile.png')

if __name__ == '__main__': main()
