#!/usr/bin/env python3
"""02c_full_profile_analysis.py — анализ всего проспекта по accepted+aligned DEM."""
import json
from pathlib import Path
from datetime import datetime
import numpy as np, pandas as pd
import matplotlib; matplotlib.use('Agg')
import matplotlib.pyplot as plt

HOME = Path('/home/z/my-project'); OUT = HOME / 'download'; PROF_DIR = OUT / 'profiles'
FUSION_QA = json.loads((OUT/'fusion_qa.json').read_text())
CORR = {k:(v['shift_m'], v['tilt_m_per_km']/1000.0) for k,v in FUSION_QA['corrections'].items()}
ACCEPTED = [k for k,v in FUSION_QA['sources'].items() if v.get('status')=='ok']
if 'mosaic' not in ACCEPTED: ACCEPTED.insert(0,'mosaic')
DROPPED = [k for k,v in FUSION_QA['sources'].items() if v.get('status')!='ok']
log_msg = lambda m: print(f'[{datetime.now():%H:%M:%S}] {m}', flush=True)

def load_aligned():
    profiles = {}
    for name in ACCEPTED:
        f = PROF_DIR / f'profile_{name}.csv'
        if not f.exists(): continue
        df = pd.read_csv(f); df['kept'] = df['kept'].astype(bool)
        a,b = CORR.get(name,(0.0,0.0)); df['h_aligned'] = df['h']-(a+b*df['pk'])
        profiles[name] = df
    return profiles

def build_table(fused):
    h_cols = [c for c in fused.columns if c.startswith('h_') and c != 'h_fused']
    rows = []
    for _,fr in fused.iterrows():
        if fr['status'] in ('gap','rejected_restore'): continue
        row = {'b':int(fr['b']),'pk':float(fr['pk'])}; vals = []
        for c in h_cols:
            v = fr.get(c, np.nan)
            if pd.notna(v): row[c]=float(v); vals.append(float(v))
            else: row[c]=np.nan
        if vals:
            row['n_src']=len(vals); row['h_median']=float(np.median(vals))
            row['h_std']=float(np.std(vals)) if len(vals)>1 else 0.0
            row['h_min']=float(np.min(vals)); row['h_max']=float(np.max(vals))
        else: row['n_src']=0
        rows.append(row)
    return pd.DataFrame(rows)

def analyze_relief(fused):
    d = fused[fused.status.isin(['confirmed','restored','single'])].copy().sort_values('pk').reset_index(drop=True)
    if len(d)<4: return {}
    h = d.h_fused.values.copy(); sm = h.copy()
    for i in range(len(h)):
        lo,hi = max(0,i-5),min(len(h),i+6); sm[i] = np.median(h[lo:hi])
    d['h_smooth'] = sm; z = np.polyfit(d.pk, d.h_smooth, 1)
    d['slope_local'] = np.gradient(d.h_smooth, d.pk)
    d['zone'] = 'flat'; d.loc[d.slope_local>0.005,'zone']='up'; d.loc[d.slope_local<-0.005,'zone']='down'
    segs = []; cz=d.iloc[0].zone; cs=d.iloc[0].pk; ch=d.iloc[0].h_smooth
    for _,r in d.iterrows():
        if r.zone != cz:
            segs.append({'zone':cz,'pk_start':round(cs,1),'pk_end':round(r.pk,1),'dh_m':round(r.h_smooth-ch,2),
                         'length_m':round(r.pk-cs,1),'slope_pct':round((r.h_smooth-ch)/max(1,r.pk-cs)*100,2)})
            cz=r.zone; cs=r.pk; ch=r.h_smooth
    segs.append({'zone':cz,'pk_start':round(cs,1),'pk_end':round(d.iloc[-1].pk,1),'dh_m':round(d.iloc[-1].h_smooth-ch,2),
                 'length_m':round(d.iloc[-1].pk-cs,1),'slope_pct':round((d.iloc[-1].h_smooth-ch)/max(1,d.iloc[-1].pk-cs)*100,2)})
    segs = [s for s in segs if s['length_m']>=20]
    return {'total_length_m':float(d.pk.iloc[-1]-d.pk.iloc[0]),'dh_total_m':float(d.h_smooth.iloc[-1]-d.h_smooth.iloc[0]),
            'mean_slope_pct':float(z[0]*100),'h_min_m':float(d.h_smooth.min()),'h_max_m':float(d.h_smooth.max()),
            'h_range_m':float(d.h_smooth.max()-d.h_smooth.min()),
            'n_up':int(sum(1 for s in segs if s['zone']=='up')),'n_down':int(sum(1 for s in segs if s['zone']=='down')),
            'n_flat':int(sum(1 for s in segs if s['zone']=='flat')),
            'max_slope_pct':float(max(abs(s['slope_pct']) for s in segs)) if segs else 0,'segments':segs[:30]}

def main():
    log_msg('='*60); log_msg('02c: ВЕСЬ проспект по accepted+aligned DEM'); log_msg('='*60)
    profiles = load_aligned(); fused = pd.read_csv(OUT/'fused_profile.csv')
    df = build_table(fused); df.to_csv(OUT/'full_profile_per_picket.csv', index=False)
    relief = analyze_relief(fused)
    multi = df[df.n_src>=2]
    qa = {'accepted_sources':ACCEPTED,'dropped_sources':DROPPED,
          'corrections':{k:{'shift_m':v[0],'tilt_m_per_km':v[1]*1000} for k,v in CORR.items()},
          'n_pickets':int(len(df)),'n_2plus':int((df.n_src>=2).sum()),
          'coverage_2plus_pct':float((df.n_src>=2).sum()*100/max(1,len(df))),
          'relief':relief,
          'std_between_accepted':{'mean_m':float(multi.h_std.mean()) if len(multi) else None,
                                  'median_m':float(multi.h_std.median()) if len(multi) else None,
                                  'p95_m':float(multi.h_std.quantile(0.95)) if len(multi) else None,
                                  'max_m':float(multi.h_std.max()) if len(multi) else None}}
    (OUT/'full_profile_analysis.json').write_text(json.dumps(qa,ensure_ascii=False,indent=2,default=str))
    # График 4 панели
    fig, axes = plt.subplots(4,1,figsize=(16,16),constrained_layout=True)
    ax=axes[0]
    for name,p in profiles.items():
        kk=p[p.kept]; ax.plot(kk.pk,kk.h_aligned,'-',lw=2 if name=='mosaic' else 1.5,alpha=0.8,label=name)
    ax.legend(fontsize=9); ax.grid(alpha=0.3); ax.set_title('1. Overlay accepted DEM (Theil-Sen aligned)')
    ax=axes[1]; d=df[df.n_src>=1]; sc=ax.scatter(d.pk,d.h_std,c=d.n_src,cmap='RdYlGn_r',s=40,edgecolor='black',linewidth=0.4)
    ax.set_title('2. std между accepted (детектор машин)'); ax.grid(alpha=0.3); plt.colorbar(sc,ax=ax)
    ax=axes[2]; fo=fused[fused.status.isin(['confirmed','restored','single'])]
    ax.plot(fo.pk,fo.h_fused,'-',lw=2,color='black',label='fused')
    for stt,c in [('confirmed','green'),('restored','orange'),('single','steelblue')]:
        sub=fused[fused.status==stt]
        if len(sub): ax.scatter(sub.pk,sub.h_fused,s=25,c=c,edgecolor='black',linewidth=0.3,label=f'{stt}({len(sub)})')
    ax.legend(fontsize=9); ax.grid(alpha=0.3); ax.set_title('3. Fused профиль')
    ax=axes[3]; d3=fused[fused.status.isin(['confirmed','restored','single'])].sort_values('pk').reset_index(drop=True)
    h=d3.h_fused.values.copy(); sm=h.copy()
    for i in range(len(h)): lo,hi=max(0,i-5),min(len(h),i+6); sm[i]=np.median(h[lo:hi])
    d3['slope']=np.gradient(sm,d3.pk)*100; ax.plot(d3.pk,d3.slope,'-',lw=1.5,color='black')
    ax.axhline(0,color='gray',lw=0.5); ax.axhline(0.5,color='red',lw=0.5,ls='--'); ax.axhline(-0.5,color='blue',lw=0.5,ls='--')
    ax.set_title('4. Уклон fused'); ax.grid(alpha=0.3); ax.set_ylim(-3,3)
    plt.savefig(OUT/'full_profile_analysis.png',dpi=140); plt.close()
    log_msg(f'✓ full_profile_analysis.png/json, per_picket.csv')
    log_msg(f'  relief: dh={relief.get("dh_total_m",0):+.2f}м, slope={relief.get("mean_slope_pct",0):+.3f}%, range={relief.get("h_range_m",0):.2f}м')
    sd=qa['std_between_accepted']
    log_msg(f'  std: mean={sd["mean_m"]:.3f}м, median={sd["median_m"]:.3f}м, max={sd["max_m"]:.3f}м')

if __name__ == '__main__': main()
