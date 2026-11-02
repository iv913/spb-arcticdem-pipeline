"""Plot correction field 6.53 (Gaussian sigma 40 m, KNN 24, conf weights, soft beta) — in-sample vs held-out.
usage: plot_loo.py <marks_di_653.csv>"""
import sys, csv, numpy as np
from scipy.spatial import cKDTree
R = list(csv.DictReader(open(sys.argv[1])))
lon = np.array([float(r['lon']) for r in R]); lat = np.array([float(r['lat']) for r in R])
h = np.array([float(r['h']) for r in R]); ref = np.array([float(r['ref']) for r in R]); d = h - ref
inl = np.array([r['inlier'] == '1' for r in R]); CW = {'H': 1.0, 'M': 0.7, 'L': 0.45}
w = np.array([CW.get(r['conf'], 0.45) for r in R])
P = np.stack([(lon - lon.mean()) * 55700, (lat - lat.mean()) * 111133], 1)
SIG, KS, RMAX, K = 40.0, 0.35, 120.0, 24
def corr_at(i, exclude_r):
    use = inl.copy()
    if exclude_r is not None:
        use &= np.hypot(*(P - P[i]).T) > exclude_r
    idx = np.where(use)[0]
    if idx.size == 0: return 0.0
    dd = np.hypot(*(P[idx] - P[i]).T); o = np.argsort(dd)[:K]; dd, idx = dd[o], idx[o]
    m = dd <= RMAX; dd, idx = dd[m], idx[m]
    if idx.size == 0: return 0.0
    wg = np.exp(-dd**2 / (2 * SIG**2)) * w[idx]; sw = wg.sum()
    beta = sw / (sw + KS); c = (wg * d[idx]).sum() / sw * beta
    return c if beta > 0.05 else 0.0
def rep(tag, e):
    print(f"{tag:46s} n={e.size}  bias {e.mean():+.2f}  median|e| {np.median(np.abs(e)):.2f}  MAD {np.median(np.abs(e-np.median(e))):.2f}  "
          f"|e|>0.5m {100*(np.abs(e)>0.5).mean():3.0f}%  |e|>1m {100*(np.abs(e)>1).mean():3.0f}%")
nn = cKDTree(P).query(P, k=2)[0][:, 1]
print(f"marks: {len(R)} (inliers {inl.sum()}), nearest-neighbour distance median {np.median(nn):.1f} m, <10 m: {100*(nn<10).mean():.0f}%")
rep('no correction (fusion as is)', ref - h)
rep('in-sample (as reported by 6.53)', np.array([ref[i] + corr_at(i, None) - h[i] for i in range(len(R))]))
rep('leave-one-out', np.array([ref[i] + corr_at(i, 0.0) - h[i] for i in range(len(R))]))
for rr in (30.0, 60.0, 120.0):
    rep(f'leave-out radius {rr:.0f} m', np.array([ref[i] + corr_at(i, rr) - h[i] for i in range(len(R))]))
