"""What makes the data|fill step? Compare boundary steps on the 8 m grid with and without anchor correction.
usage: boundary_decomp.py <workdir> <repo>"""
import sys, runpy, numpy as np
WD, REPO = sys.argv[1:3]
# reuse the protocol module's globals without running its main body: exec up to the first evaluate() call
src = open('/tmp/claude-0/-home-claude/ec58354c-610f-5d5d-a999-7bf1ca225fda/scratchpad/seam/repro_protocols.py').read()
src = src[:src.index("R = {}")]
g = {'__name__': 'x'}; sys.argv = ['x', WD, REPO]; exec(src, g)
np_ = np; ndimage = g['ndimage']; h8, valid8, g0, holes, pub8 = g['h8'], g['valid8'], g['g0'], g['holes'], g['pub8']
corr, d0 = g['anchor_correction'](g['triage'])
land8 = valid8 & (h8 > 0.7); landH = holes & np.isfinite(pub8) & (pub8 > 0.7)
def steps(f):
    out = []
    for ax in (0, 1):
        a = [slice(None)] * 2; b = [slice(None)] * 2; a[ax] = slice(None, -1); b[ax] = slice(1, None); a, b = tuple(a), tuple(b)
        m1 = land8[a] & landH[b]; m2 = landH[a] & land8[b]
        out.append(f[b][m1] - h8[a][m1]); out.append(f[a][m2] - h8[b][m2])
    return np.concatenate(out)          # fill minus data, adjacent 8 m cells
def rep(tag, s):
    a = np.abs(s)
    print(f"{tag:44s} n={s.size:7d} median(fill−data) {np.median(s):+.2f}  median|Δ| {np.median(a):.2f}  p90 {np.percentile(a,90):.2f}  "
          f">0.5m {100*(a>0.5).mean():5.1f}%  >1m {100*(a>1).mean():5.1f}%  fill higher >1m {100*(s>1).mean():5.1f}%")
s_pub = steps(pub8); rep('PUBLISHED (8 m means of release raster)', s_pub)
f0 = ndimage.gaussian_filter(np.where(valid8, h8, g0), 2.0); rep('Laplace fill only (no anchors), A2 smoothing', steps(f0))
rep('Laplace raw g0 (no anchors, no smoothing)', steps(g0))
f1 = ndimage.gaussian_filter(np.where(valid8, h8, g0 + corr), 2.0); rep('Laplace + anchor correction (as published)', steps(f1))
# correction values right next to data
nb = ndimage.binary_dilation(valid8, iterations=1) & holes
c = corr[nb]
print(f"\nanchor correction in fill cells touching data: n={c.size}  median {np.median(c):+.2f}  p90 {np.percentile(c,90):+.2f}  "
      f">+0.5m {100*(c>0.5).mean():.1f}%  >+1m {100*(c>1).mean():.1f}%  <-0.5m {100*(c<-0.5).mean():.1f}%")
# anchor residuals against the Laplace draft (what the correction is made of), by canopy proxy unavailable -> by sign
r = g['resid0'][g['triage'] & holes[g['r_all'], g['c_all']] & ~g['mk']]
print(f"anchor residuals (ATL06 − Laplace draft) in gaps: median {np.median(r):+.2f}  mean {r.mean():+.2f}  p75 {np.percentile(r,75):+.2f}  p90 {np.percentile(r,90):+.2f}  "
      f"share >+2m {100*(r>2).mean():.1f}%  <-2m {100*(r<-2).mean():.1f}%")
