"""Re-run fuse_6.00 anchoring + validation under 4 protocols. usage: repro_protocols.py <workdir> <repo>"""
import sys, time, math, csv, json, numpy as np, pandas as pd, rasterio
from scipy import ndimage
WD, REPO = sys.argv[1:3]
def log(m): print(f'[{time.strftime("%H:%M:%S")}] {m}', flush=True)
MY = 111132.95; MX0 = 111319.49 * math.cos(math.radians(59.95))
A_DIST = {1: 250.0, 2: 1000.0, 3: 3000.0, 4: 1e9}; A_SIGMA = {1: 1.5, 2: 2.0, 3: 3.0, 4: 4.0}
ANCHOR_R, ANCHOR_DAMP = 2000.0, 0.6
z = np.load(f'{WD}/grid8.npz'); h8 = z['h8']; valid8 = z['valid8']; tr = rasterio.Affine(*z['tr']); pub8 = z['pub8']
g0 = np.load(f'{WD}/g0.npy'); shape = h8.shape; holes = ~valid8
u = pd.read_pickle(f'{WD}/unified.pkl')
M = [r for r in csv.DictReader(open(f'{REPO}/results/marks_val_5.95.csv'))]
mlo, mla, mz = [], [], []
for r in M:
    try: mlo.append(float(r['lon'])); mla.append(float(r['lat'])); mz.append(float(r['z']))
    except Exception: pass
lo = np.r_[u.lon.values, mlo]; la = np.r_[u.lat.values, mla]; zz = np.r_[u.h_baltic.values, mz].astype(np.float64)
mk = np.r_[np.zeros(len(u), bool), np.ones(len(mlo), bool)]; rgt = np.r_[u.rgt.values.astype(int), -np.ones(len(mlo), int)]
log(f'points: canon {len(u)}, marks {len(mlo)}')
def upsample_to(g, shp):
    rows = np.clip(np.arange(shp[0]) // 4, 0, g.shape[0] - 1); cols = np.clip(np.arange(shp[1]) // 4, 0, g.shape[1] - 1)
    return g[np.ix_(rows, cols)]
def grid_of(la, lo):
    c = np.clip(((lo - tr.c) / tr.a).astype(np.int64), 0, shape[1] - 1); r = np.clip(((la - tr.f) / tr.e).astype(np.int64), 0, shape[0] - 1)
    return r, c
r_all, c_all = grid_of(la, lo)
def anchor_correction(sel):          # verbatim logic of fuse_6.00.anchor_correction on the subset `sel`
    r, c = r_all[sel], c_all[sel]; inhole = holes[r, c]
    resid = zz[sel] - g0[r, c].astype(np.float64)
    ny1, nx1 = shape[0] // 4, shape[1] // 4
    r1 = np.clip(r[inhole] // 4, 0, ny1 - 1); c1 = np.clip(c[inhole] // 4, 0, nx1 - 1); flat = r1 * nx1 + c1
    sums = np.bincount(flat, weights=resid[inhole], minlength=ny1 * nx1); cnts = np.bincount(flat, minlength=ny1 * nx1)
    has = (cnts > 0).reshape(ny1, nx1); mean = (sums / np.maximum(cnts, 1)).reshape(ny1, nx1)
    ind = ndimage.distance_transform_edt(~has, return_distances=False, return_indices=True)
    rf = ndimage.gaussian_filter(np.where(has, mean, mean[tuple(ind)]).astype(np.float32), 1.5)
    d1 = ndimage.distance_transform_edt(~has, sampling=(abs(tr.e) * MY * 4, abs(tr.a) * MX0 * 4))
    d0 = upsample_to(d1.astype(np.float32), shape)
    return np.where(d0 <= ANCHOR_R, upsample_to(rf, shape) * ANCHOR_DAMP, 0.0).astype(np.float32), d0
dist = ndimage.distance_transform_edt(~valid8, sampling=(abs(tr.e) * MY, abs(tr.a) * MX0)).astype(np.float32)
resid0 = zz - g0[r_all, c_all]; triage = np.abs(resid0) <= 10.0
inhole_all = holes[r_all, c_all] & ~mk & triage
rng = np.random.default_rng(600); cand = np.where(inhole_all)[0]; sel = rng.permutation(cand.size)
hold = np.zeros(len(lo), bool); hold[cand[sel[:cand.size // 2]]] = True
log(f'triage dropped {(~triage).sum()}, holdout {hold.sum()}')
def evaluate(corr, ev, tag, keep_f=False):
    out = {}; fA2 = None
    for A in (1, 2, 3, 4):
        use = holes & (dist <= A_DIST[A])
        sm = ndimage.gaussian_filter(np.where(valid8, h8, g0 + corr), A_SIGMA[A])
        fh = sm[r_all, c_all]; inA = use[r_all, c_all]
        m = ev & inA; e = zz[m] - fh[m]
        if keep_f and A == 2: fA2 = np.where(use, sm, np.nan).astype(np.float32)
        if e.size:
            out[f'A{A}'] = dict(n=int(e.size), med=round(float(np.median(e)), 3), mad=round(float(np.median(np.abs(e - np.median(e)))), 3),
                               p95=round(float(np.percentile(np.abs(e), 95)), 3), rmse=round(float(np.sqrt((e ** 2).mean())), 3),
                               gt1=round(float((np.abs(e) > 1).mean() * 100), 1), n_rgt=int(len(np.unique(rgt[m]))))
        del sm
    log(f'{tag}: ' + '  '.join(f"{k} MAD {v['mad']} p95 {v['p95']} >1m {v['gt1']}%" for k, v in out.items()))
    return out, fA2
R = {}
# P0: published-reported numbers for reference are in fusion_report_6.00.json
corr_all, d0_all = anchor_correction(triage)
R['P1_as_published'], fA2_pub = evaluate(corr_all, hold, 'P1 as published (holdout inside anchors)', keep_f=True)
corr_fix, _ = anchor_correction(triage & ~hold)
R['P2_holdout_excluded'], _ = evaluate(corr_fix, hold, 'P2 holdout truly excluded')
del corr_fix
R['P0_no_anchoring'], _ = evaluate(np.zeros(shape, np.float32), inhole_all, 'P0 Laplace only, no anchors')
# P3: leave-tracks-out, 5 folds grouped by RGT
urgt = np.unique(rgt[rgt >= 0]); rs = np.random.default_rng(7); rs.shuffle(urgt); folds = np.array_split(urgt, 5)
acc = {A: [] for A in (1, 2, 3, 4)}; per_rgt = {}
for k, fr in enumerate(folds):
    infold = np.isin(rgt, fr)
    corr_k, _ = anchor_correction(triage & ~infold)
    for A in (1, 2, 3, 4):
        use = holes & (dist <= A_DIST[A])
        sm = ndimage.gaussian_filter(np.where(valid8, h8, g0 + corr_k), A_SIGMA[A])
        m = inhole_all & infold & use[r_all, c_all]
        e = zz[m] - sm[r_all, c_all][m]; acc[A].append(e)
        if A == 4:
            for g in np.unique(rgt[m]): per_rgt[int(g)] = float(np.median(np.abs(e[rgt[m] == g])))
        del sm
    log(f'P3 fold {k+1}/5 done ({len(fr)} RGT)')
    del corr_k
R['P3_leave_tracks_out'] = {}
for A in (1, 2, 3, 4):
    e = np.concatenate(acc[A])
    if e.size:
        R['P3_leave_tracks_out'][f'A{A}'] = dict(n=int(e.size), med=round(float(np.median(e)), 3), mad=round(float(np.median(np.abs(e - np.median(e)))), 3),
                                              p95=round(float(np.percentile(np.abs(e), 95)), 3), rmse=round(float(np.sqrt((e ** 2).mean())), 3),
                                              gt1=round(float((np.abs(e) > 1).mean() * 100), 1))
pr = np.array(list(per_rgt.values()))
R['P3_per_rgt_median_abs_A4'] = dict(n_rgt=int(pr.size), median=round(float(np.median(pr)), 3), p25=round(float(np.percentile(pr, 25)), 3),
                                     p75=round(float(np.percentile(pr, 75)), 3), max=round(float(pr.max()), 3))
log('P3: ' + json.dumps(R['P3_leave_tracks_out']))
# reproduction check of the published raster (A2 fill) on the 8 m grid
m = np.isfinite(fA2_pub) & np.isfinite(pub8) & holes
dd = fA2_pub[m] - pub8[m]
R['repro_vs_published_A2'] = dict(n_cells=int(m.sum()), median=round(float(np.median(dd)), 3), mad=round(float(np.median(np.abs(dd - np.median(dd)))), 3),
                                   p95_abs=round(float(np.percentile(np.abs(dd), 95)), 3))
log(f"repro vs published A2: {R['repro_vs_published_A2']}")
# 2 km cutoff seam on the PUBLISHED surface (8 m cells, fill only, land)
land = np.isfinite(pub8) & (pub8 > 0.7) & holes
def contour_step(th):
    out = []
    for ax in (0, 1):
        a = [slice(None)] * 2; b = [slice(None)] * 2; a[ax] = slice(None, -1); b[ax] = slice(1, None); a, b = tuple(a), tuple(b)
        cross = ((d0_all[a] <= th) != (d0_all[b] <= th)) & land[a] & land[b]
        out.append(np.abs(pub8[b][cross] - pub8[a][cross]))
    return np.concatenate(out)
R['cutoff_2km'] = {}
for th in (1500.0, 2000.0, 2500.0):
    s = contour_step(th)
    if s.size: R['cutoff_2km'][f'{int(th)}m'] = dict(n=int(s.size), median=round(float(np.median(s)), 3), p95=round(float(np.percentile(s, 95)), 3),
                                                   gt05=round(float((s > 0.5).mean() * 100), 2), gt1=round(float((s > 1).mean() * 100), 2))
inner = holes & land & (d0_all > 50)
s_bg = []
for ax in (0, 1):
    a = [slice(None)] * 2; b = [slice(None)] * 2; a[ax] = slice(None, -1); b[ax] = slice(1, None); a, b = tuple(a), tuple(b)
    mm = inner[a] & inner[b]; s_bg.append(np.abs(pub8[b][mm] - pub8[a][mm]))
s_bg = np.concatenate(s_bg)
R['cutoff_2km']['background_fill'] = dict(n=int(s_bg.size), median=round(float(np.median(s_bg)), 3), p95=round(float(np.percentile(s_bg, 95)), 3),
                                          gt05=round(float((s_bg > 0.5).mean() * 100), 2), gt1=round(float((s_bg > 1).mean() * 100), 2))
log('cutoff: ' + json.dumps(R['cutoff_2km']))
json.dump(R, open(f'{WD}/protocols.json', 'w'), indent=1)
log('DONE')
