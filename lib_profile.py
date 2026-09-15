#!/usr/bin/env python3
"""lib_profile.py — общие функции профиля (единственная копия _prune_bins)."""
import numpy as np, pandas as pd
from scipy.ndimage import binary_closing
PK_BIN=5.0; MAX_SHADOW_JUMP=1.0; BASELINE_WIN_BINS=15; BASE_MIN_PERIODS=5
MAX_LONG_SLOPE=0.05; ZONE_CLOSE_BINS=2
def prune_bins(b_arr,pk_arr,h_arr,max_jump=MAX_SHADOW_JUMP,max_slope=MAX_LONG_SLOPE,
               base_win=BASELINE_WIN_BINS,min_periods=BASE_MIN_PERIODS,
               zone_close=ZONE_CLOSE_BINS,pk_bin=PK_BIN):
    b_arr=np.asarray(b_arr,dtype=float);pk_arr=np.asarray(pk_arr,dtype=float)
    h_arr=np.asarray(h_arr,dtype=float);n=len(h_arr);keep=np.ones(n,dtype=bool)
    reason=np.array(['']*n,dtype=object);edge_m=(base_win/2)*pk_bin
    for _ in range(30):
        idx=np.where(keep)[0]
        if len(idx)<4:break
        hv,pkv=h_arr[idx],pk_arr[idx]
        base=pd.Series(hv).rolling(base_win,center=True,min_periods=min_periods).median().values
        up=hv-base;near_edge=((pkv-pk_arr.min())<edge_m)|((pk_arr.max()-pkv)<edge_m)
        bad_jump=np.where(near_edge,up>3*max_jump,up>max_jump)
        bad_jump=np.nan_to_num(bad_jump.astype(float)).astype(bool)
        bad_slope=np.zeros(len(idx),dtype=bool)
        s=np.diff(hv)/np.maximum(np.diff(pkv),1e-6)
        for i in np.where(np.abs(s)>max_slope)[0]:
            j=i if hv[i]>hv[i+1] else i+1;bad_slope[j]=True
        bad=bad_jump|bad_slope
        if not bad.any():break
        keep[idx[bad]]=False;reason[idx[bad_jump]]='shadow_jump'
        reason[idx[bad_slope&~bad_jump]]='slope'
    if (~keep).any():
        bmin=int(b_arr.min());full=np.zeros(int(b_arr.max())-bmin+1,dtype=bool)
        full[(b_arr[~keep]-bmin).astype(int)]=True
        closed=binary_closing(full,structure=np.ones(2*zone_close+1))
        newly=closed&~full;pos={int(bb):i for i,bb in enumerate(b_arr)}
        for bb in np.nonzero(newly)[0]+bmin:
            i=pos.get(int(bb))
            if i is not None and keep[i]:keep[i]=False;reason[i]='zone_fill'
    return keep,reason
def fused_longitudinal(fused_csv_path,pk_bin=PK_BIN,max_gap_bins=4,smooth_half_bins=2):
    fused=pd.read_csv(fused_csv_path)
    need={'b','pk','h_fused','status'}
    if not need<=set(fused.columns):raise ValueError(f'fused_profile.csv: нет {need-set(fused.columns)}')
    prof=pd.DataFrame({'b':fused['b'].astype(int),'pk':fused['pk'].astype(float),
        'h':fused['h_fused'].astype(float),
        'kept':fused['status'].isin(['restored','confirmed','single']),
        'reject_reason':np.where(fused['status'].isin(['gap','rejected_restore']),fused['status'],''),
        'status':fused['status']}).sort_values('b').reset_index(drop=True)
    kept=prof[prof['kept']&np.isfinite(prof['h'])].reset_index(drop=True)
    if len(kept)<4:raise ValueError('fused: <4 бинов')
    hs=kept['h'].values.copy();sm=hs.copy()
    for i in range(len(hs)):
        lo,hi=max(0,i-smooth_half_bins),min(len(hs),i+smooth_half_bins+1);sm[i]=np.median(hs[lo:hi])
    kept=kept.copy();kept['h_smooth']=sm
    prof=prof.merge(kept[['b','h_smooth']],on='b',how='left')
    full=np.arange(prof['b'].min(),prof['b'].max()+1);have=np.isin(full,kept['b'].values)
    gap_len={};i=0
    while i<len(full):
        if not have[i]:
            j=i
            while j<len(full) and not have[j]:j+=1
            for k in range(i,j):gap_len[int(full[k])]=j-i
            i=j
        else:i+=1
    pk_grid=kept['pk'].values;h_grid=kept['h_smooth'].values
    def f_long(q):return np.interp(np.asarray(q,dtype=float),pk_grid,h_grid)
    def is_extrap(q):
        q=np.asarray(q,dtype=float);bq=np.floor(q/pk_bin).astype(int)
        out=(q<pk_grid.min())|(q>pk_grid.max())
        big_gap=np.array([gap_len.get(int(x),0)>max_gap_bins for x in bq]);return out|big_gap
    return f_long,is_extrap,prof
