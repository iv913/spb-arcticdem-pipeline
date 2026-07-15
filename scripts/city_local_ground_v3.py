"""city_local_ground_v3.py — коридорный нижне-огибающий эстиматор."""
import numpy as np
from collections import defaultdict
from rasterio.windows import from_bounds

MAX_OFF_M = 10.0
L_STEPS_M = (40., 80., 150., 250.)
BLOCK_M = 6.0
MIN_PX_BLOCK = 3
MIN_BLOCKS = 6
WATER_GAP_M = 1.5
WATER_MAX_FRAC = 0.30
UPPER_WIN_M = 0.6
GOOD_FRAC = 0.10
ANCHOR_CLASSES = {'primary','secondary','tertiary','residential',
                  'unclassified','living_street','trunk'}

def _ground_from_samples(vals, along, off):
    ba = np.floor(along / BLOCK_M).astype(int)
    bo = np.floor(off / BLOCK_M).astype(int)
    groups = defaultdict(list)
    for v, a, o in zip(vals, ba, bo): groups[(a,o)].append(v)
    med = np.array(sorted(np.median(g) for g in groups.values() if len(g)>=MIN_PX_BLOCK))
    if len(med) < MIN_BLOCKS: return np.nan, int(len(med)), 0.0
    gaps = np.diff(med); cut = 0
    for i, gp in enumerate(gaps):
        if gp > WATER_GAP_M and (i+1) <= WATER_MAX_FRAC * len(med): cut = i+1
    med = med[cut:]
    if len(med) < 2: return np.nan, 0, 0.0
    floor = med[0]; sel = med[med <= floor + UPPER_WIN_M]
    return float(np.median(sel)), int(len(sel)), float(len(sel)/len(med))

def corridor_ground(src, pt_xy_dem, way_line_dem, other_ways_dem=None):
    import shapely
    x0, y0 = pt_xy_dem
    s0 = float(shapely.line_locate_point(way_line_dem, shapely.points(x0, y0)))
    L_way = way_line_dem.length
    meta = {'n_blocks':0, 'L_used_m':0.0, 'qflag':'too_few_blocks', 'n_anchors':0}
    for L in L_STEPS_M:
        a0, a1 = max(0.0, s0-L), min(L_way, s0+L)
        seg = shapely.ops.substring(way_line_dem, a0, a1)
        corridor = seg.buffer(MAX_OFF_M, cap_style=2)
        win = from_bounds(*corridor.bounds, src.transform)
        win = win.round_offsets(op='floor').round_lengths(op='ceil')
        arr = src.read(1, window=win, boundless=True, fill_value=np.nan).astype('float64')
        if src.nodata is not None: arr[arr == src.nodata] = np.nan
        t = src.window_transform(win)
        rr, cc = np.mgrid[0:arr.shape[0], 0:arr.shape[1]]
        xs = t.c + t.a*(cc+0.5) + t.b*(rr+0.5)
        ys = t.f + t.d*(cc+0.5) + t.e*(rr+0.5)
        fin = np.isfinite(arr)
        pts = shapely.points(xs[fin], ys[fin])
        offd = shapely.distance(pts, seg)
        inside = offd <= MAX_OFF_M
        vals = arr[fin][inside]
        along = np.asarray(shapely.line_locate_point(seg, pts[inside]))
        off = np.asarray(offd[inside])
        ground, n_blocks, frac = _ground_from_samples(vals, along, off)
        meta.update({'n_blocks':n_blocks, 'L_used_m':float(a1-a0)})
        if not np.isfinite(ground): continue
        if frac >= GOOD_FRAC:
            n_anchors = 0
            if other_ways_dem:
                for w in other_ways_dem:
                    if w is not way_line_dem and corridor.intersects(w): n_anchors += 1
            meta['n_anchors'] = n_anchors
            if other_ways_dem is not None and n_anchors == 0 and L < L_STEPS_M[-1]: continue
            meta['qflag'] = 'ok' if (other_ways_dem is None or n_anchors > 0) else 'no_anchor'
            return ground, meta
        meta['qflag'] = 'shadow_total'
    return (np.nan if meta['qflag'] != 'ok' else ground), meta

def aggregate_city_shift(df, dh_col='dH_v3', name_col='name',
                         q_col='qflag', max_iter=10):
    ok = df[(df[q_col]=='ok') & np.isfinite(df[dh_col])].copy()
    per = ok.groupby(name_col)[dh_col].agg(['median','count'])
    meds = per['median'].values.astype(float)
    keep = np.ones(len(meds), bool)
    for _ in range(max_iter):
        med = np.median(meds[keep])
        mad = np.median(np.abs(meds[keep]-med))*1.4826
        new = np.abs(meds-med) <= 3*max(0.15, mad)
        if (new==keep).all(): break
        keep = new
    shift = float(np.median(meds[keep]))
    return shift, {
        'n_points_ok': int(len(ok)), 'n_streets': int(len(per)),
        'n_streets_inlier': int(keep.sum()),
        'between_street_mad_m': float(np.median(np.abs(meds[keep]-shift))*1.4826),
        'shift_m': shift,
    }
