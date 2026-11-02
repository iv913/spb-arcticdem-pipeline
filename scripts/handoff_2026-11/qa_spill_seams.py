#!/usr/bin/env python3
"""QA: z_spill (G channel) steps across block boundaries vs control lines.
Usage: python3 qa_spill_seams.py <map/tiles>"""
import sys, math, numpy as np
from pathlib import Path
from PIL import Image
from pyproj import Transformer
TILES=Path(sys.argv[1]); Z=14
to4326=Transformer.from_crs(3413,4326,always_xy=True)
left,top=3199794,-827800; W,H=23146,28369; bottom=top-2*H; right=left+2*W; B=8192.0
cache={}
def px(lon,lat):
    n=2**Z; fx=(lon+180)/360*n
    fy=(1-math.log(math.tan(math.radians(lat))+1/math.cos(math.radians(lat)))/math.pi)/2*n
    k=(int(fx),int(fy))
    if k not in cache:
        p=TILES/str(Z)/str(k[0])/f"{k[1]}.png"
        cache[k]=np.asarray(Image.open(p).convert('RGBA')) if p.exists() else None
    a=cache[k]
    if a is None: return None
    return a[int((fy-int(fy))*256), int((fx-int(fx))*256)]
def at(x,y): return px(*to4326.transform(x,y))
def collect(lines, delta=6):
    out=[]
    for fixed,vert in lines:
        rng = np.arange(bottom,top,20.0) if vert else np.arange(left,right,20.0)
        for s in rng:
            a = at(fixed-delta,s) if vert else at(s,fixed+delta)
            b = at(fixed+delta,s) if vert else at(s,fixed-delta)
            if a is None or b is None: continue
            if a[3]==0 or b[3]==0 or a[1]==255 or b[1]==255: continue
            if a[2] not in (21,23) or b[2] not in (21,23): continue
            ga,gb=a[1]/10,b[1]/10   # z_spill, m; 6.5 = dry
            if ga>=6.5 and gb>=6.5: continue          # both dry under 6.5 m: irrelevant
            out.append(abs(gb-ga))
    return np.array(out)
seams=[(left+k*B,True) for k in range(1,6)]+[(top-k*B,False) for k in range(1,7)]
ctrl =[(left+k*B-B/2,True) for k in range(1,6)]+[(top-k*B+B/2,False) for k in range(1,7)]
s=collect(seams); c=collect(ctrl)
for lbl,a in (("SEAMS  ",s),("CONTROL",c)):
    print(f"{lbl} z_spill step: n={len(a):5d}  median={np.median(a):.2f} m  |Δspill|>0.5m={100*(a>0.5).mean():5.1f}%  >1m={100*(a>1).mean():5.1f}%  p95={np.percentile(a,95):.2f}")
