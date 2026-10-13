#!/usr/bin/env python3
"""5.96b-диагностика-2: покрытие нодата и высоты в нат. разрешении + карта-диагностика."""
import os
os.environ.setdefault('MPLCONFIGDIR', '/tmp/mplcache')
import importlib.util
import numpy as np
import rasterio
from rasterio.windows import from_bounds

_spec = importlib.util.spec_from_file_location('fz', '/tmp/my-project/scripts/flood_zones_5.96.py')
fz = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fz)
MOSAIC, MCLF = fz.MOSAIC, fz.MCLF

BOXES = {
    'Vasilevskiy':   (30.15, 59.940, 30.24, 59.975),
    'Petrogradskiy': (30.28, 59.960, 30.37, 59.990),
    'Kolomna':       (30.26, 59.920, 30.33, 59.940),
    'Kirovskiy':     (30.15, 59.845, 30.28, 59.890),
    'Neva_mid':      (30.30, 59.968, 30.40, 59.985),
    'Lakhta':        (30.05, 59.990, 30.20, 60.030),
    'Strelna':       (29.90, 59.820, 30.00, 59.850),
    'Kronstadt':     (29.70, 59.980, 29.85, 60.020),
    'Pushkin':       (30.15, 59.690, 30.30, 59.730),
    'Guba_south':    (30.00, 59.880, 30.15, 59.920),
    'Gulf_Kotlin_S': (29.85, 59.900, 30.00, 59.950),
}

print(f'{"box":14s} {"nodata%":>8s} {"mclf0%":>7s} {"valid_land%":>11s} {"h p5/p50/p95":>20s}')
for nm, (lo0, la0, lo1, la1) in BOXES.items():
    with rasterio.open(MOSAIC) as s:
        a = s.read(1, window=from_bounds(lo0, la0, lo1, la1, s.transform))
    with rasterio.open(MCLF) as s:
        m = s.read(1, window=from_bounds(lo0, la0, lo1, la1, s.transform))
    tot = a.size
    nd = float((a == -32768).mean())
    m0 = float(((m == 0) & (a != -32768)).mean())
    vl = ((a != -32768) & (m != 0))
    if vl.sum() > 0:
        q = np.percentile(a[vl], [5, 50, 95]) / 100.0
        hs = f'{q[0]:5.2f}/{q[1]:5.2f}/{q[2]:5.2f}'
    else:
        hs = '—'
    print(f'{nm:14s} {nd*100:7.1f}% {m0*100:6.1f}% {vl.mean()*100:10.1f}% {hs:>20s}')

# --- карта-диагностика ---
h8, valid8, excl8, grid_tr = fz.build_grid()
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import LightSource

fig, axes = plt.subplots(1, 2, figsize=(19, 7), constrained_layout=True)
ext = [grid_tr.c, grid_tr.c + h8.shape[1] * grid_tr.a,
       grid_tr.f + h8.shape[0] * grid_tr.e, grid_tr.f]
hv = np.ma.masked_invalid(h8)
im0 = axes[0].imshow(hv, cmap='terrain', vmin=-1, vmax=25, extent=ext, aspect='auto')
plt.colorbar(im0, ax=axes[0], label='h, м BS-77')
axes[0].set_title('h8 (маскировано nodata)')
im1 = axes[1].imshow(valid8.astype(float) - excl8.astype(float) * 0.5, cmap='viridis',
                     vmin=-0.5, vmax=1.0, extent=ext, aspect='auto')
axes[1].set_title('valid8 (1) / excl8 (0.5) / nodata (0)')
for ax in axes:
    ax.set_xlabel('lon'); ax.set_ylabel('lat')
    for nm, (lo0, la0, lo1, la1) in BOXES.items():
        from matplotlib.patches import Rectangle
        ax.add_patch(Rectangle((lo0, la0), lo1 - lo0, la1 - la0, fill=False,
                               edgecolor='red', lw=0.8))
        ax.text(lo0, la1, nm, fontsize=6, color='red')
out = '/tmp/my-project/results/flood_zones_5.96/diag_coverage_5.96.png'
fig.savefig(out, dpi=110)
print('PNG:', out)
