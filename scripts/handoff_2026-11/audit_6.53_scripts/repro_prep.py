"""Step 1-3: rebuild the 6.00 inputs from published rasters + repo dumps, run the published Laplace fill.
usage: repro_prep.py <fusion.tif> <conf.tif> <repo_root> <outdir>"""
import sys, os, time, math, numpy as np, pandas as pd, rasterio
from rasterio.windows import Window
from scipy import ndimage
F, C, REPO, OUT = sys.argv[1:5]
FX, FY = 8, 4
def log(m): print(f'[{time.strftime("%H:%M:%S")}] {m}', flush=True)

# ---------- 1. h8 / valid8 (mosaic part = conf>=10) and pub8 (whole published surface) ----------
p8 = f'{OUT}/grid8.npz'
if not os.path.exists(p8):
    with rasterio.open(F) as fs, rasterio.open(C) as cs:
        W, H = fs.width, fs.height; tr2 = fs.transform
        ny, nx = H // FY, W // FX
        sv = np.zeros((ny, nx), np.float64); cv = np.zeros((ny, nx), np.int32)
        sa = np.zeros((ny, nx), np.float64); ca = np.zeros((ny, nx), np.int32)
        conf8 = np.zeros((ny, nx), np.uint8)
        S = FY * 256
        for r0 in range(0, ny * FY, S):
            h = min(S, ny * FY - r0)
            z = fs.read(1, window=Window(0, r0, nx * FX, h)).astype(np.float32)
            c = cs.read(1, window=Window(0, r0, nx * FX, h))
            fin = z != -32768; z = z * 0.01
            v = fin & (c >= 10)
            rs = slice(r0 // FY, (r0 + h) // FY)
            shp = (h // FY, FY, nx, FX)
            sv[rs] = np.where(v, z, 0).reshape(shp).sum(axis=(1, 3)); cv[rs] = v.reshape(shp).sum(axis=(1, 3))
            sa[rs] = np.where(fin, z, 0).reshape(shp).sum(axis=(1, 3)); ca[rs] = fin.reshape(shp).sum(axis=(1, 3))
            # dominant conf class of the cell (max is fine: fill 1-4 never mixes with data 10-13 inside a hole cell)
            conf8[rs] = c.reshape(shp).max(axis=(1, 3))
            log(f'grid8 rows {r0}/{ny*FY}')
    h8 = np.where(cv > 0, sv / np.maximum(cv, 1), 0).astype(np.float32)
    valid8 = cv >= max(1, int(0.25 * FX * FY))
    pub8 = np.where(ca > 0, sa / np.maximum(ca, 1), np.nan).astype(np.float32)
    tr8 = rasterio.Affine(tr2.a * FX, 0, tr2.c, 0, tr2.e * FY, tr2.f)
    np.savez(p8, h8=h8, valid8=valid8, pub8=pub8, conf8=conf8, tr=np.array(tr8)[:6])
    log(f'grid8 saved {h8.shape}, valid {valid8.mean()*100:.1f}%')

# ---------- 2. unified anchor set, exactly as densify_canon_5.93 step 1 ----------
pu = f'{OUT}/unified.pkl'
if not os.path.exists(pu):
    D = f'{REPO}/scripts/data'
    cols = ["lat", "lon", "rgt", "cycle", "gt", "h_baltic", "date"]
    hn = pd.read_csv(f'{D}/icesat2_atl06_dump_5.86.csv.gz', usecols=cols); hn['cnf'] = 'high'
    hs = pd.read_csv(f'{D}/icesat2_atl06_dump_south_5.89.csv.gz', usecols=cols); hs['cnf'] = 'high'
    dm = pd.read_csv(f'{D}/icesat2_atl06_dump_med_5.93.csv.gz', usecols=cols); dm['cnf'] = 'med'
    u = pd.concat([hn, hs, dm], ignore_index=True)
    u['date'] = u.date.astype(str).str.replace('.0', '', regex=False)
    k = (u.rgt.astype(int).astype(str) + '_' + u.cycle.astype(int).astype(str) + '_' + u['gt'].astype(str) + '_' +
         u.date.astype(str) + '_' + u.lat.round(4).astype(str) + '_' + u.lon.round(4).astype(str))
    n0 = len(u); u = u.loc[~k.duplicated(keep='first')].reset_index(drop=True)
    CITY = (29.30, 59.60, 30.75, 60.25)
    u = u[(u.lon >= CITY[0]) & (u.lon <= CITY[2]) & (u.lat >= CITY[1]) & (u.lat <= CITY[3])].reset_index(drop=True)
    u.to_pickle(pu)
    log(f'unified {n0} -> {len(u)} in CITY (published report: canon_points 1,060,684)')

# ---------- 3. Laplace fill, verbatim from fuse_6.00.py ----------
def block_mean(h, valid, k):
    ny, nx = h.shape; ny2, nx2 = ny // k, nx // k
    hh = h[:ny2 * k, :nx2 * k].reshape(ny2, k, nx2, k).astype(np.float32)
    vv = valid[:ny2 * k, :nx2 * k].reshape(ny2, k, nx2, k)
    s = np.where(vv, hh, 0.0).sum(axis=(1, 3)); c = vv.sum(axis=(1, 3), dtype=np.int32)
    return np.where(c > 0, s / np.maximum(c, 1), 0.0).astype(np.float32), c >= max(1, int(0.25 * k * k))
def upsample_to(g, shape):
    rows = np.clip(np.arange(shape[0]) // 4, 0, g.shape[0] - 1); cols = np.clip(np.arange(shape[1]) // 4, 0, g.shape[1] - 1)
    return g[np.ix_(rows, cols)]
def diffuse(h, fixed, iters):
    for _ in range(iters):
        hp = np.pad(h, 1, mode='edge')
        s = (hp[:-2, 1:-1] + hp[2:, 1:-1] + hp[1:-1, :-2] + hp[1:-1, 2:]) * 0.25
        h[~fixed] = s[~fixed]
    return h
pg = f'{OUT}/g0.npy'
if not os.path.exists(pg):
    z = np.load(p8); h8, valid8 = z['h8'], z['valid8']
    g1h, g1v = block_mean(h8, valid8, 4); g2h, g2v = block_mean(g1h, g1v, 4); g3h, g3v = block_mean(g2h, g2v, 4)
    ind = ndimage.distance_transform_edt(~g3v, return_distances=False, return_indices=True)
    g3 = diffuse(np.where(g3v, g3h, g3h[tuple(ind)]), g3v, 200); log('G3 done')
    g2 = diffuse(np.where(g2v, g2h, upsample_to(g3, g2h.shape)), g2v, 300); log('G2 done')
    g1 = diffuse(np.where(g1v, g1h, upsample_to(g2, g1h.shape)), g1v, 300); log('G1 done')
    g0 = np.where(valid8, h8, upsample_to(g1, h8.shape)).astype(np.float32)
    g0 = diffuse(g0, valid8, 300); np.save(pg, g0); log('G0 done')
log('prep complete')
