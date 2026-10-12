#!/usr/bin/env python3
"""5.90: карта южного расширения — 2 панели (DEM юга + can30-канал)."""
import numpy as np
import rasterio
import rasterio.windows as W
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from rasterio.enums import Resampling

plt.rcParams['font.sans-serif'] = ['DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

SB = (30.10, 59.70, 30.62, 59.90)          # W, S, E, N юга
OUT = '/tmp/my-project/gh-clone/results/south_map_5.90.png'

DZ = {"Кировский":      (59.83, 30.14, 59.90, 30.32),
      "Красносельский": (59.79, 30.10, 59.88, 30.30),
      "Пушкинский":     (59.70, 30.22, 59.80, 30.45),
      "Колпинский":     (59.70, 30.42, 59.80, 30.62)}

with rasterio.open('/tmp/my-project/gh-clone/download/mosaic_5.90/'
                   'spb_hybrid_2m_mosaic_5.90.tif') as s:
    win = W.from_bounds(*SB, s.transform)
    dem = s.read(1, window=win, out_shape=(1600, 4160),
                 resampling=Resampling.average).astype('float32')
    tr = W.transform(win, s.transform, (1600, 4160)) if False else None
    # transform out_shape
    tr = s.window_transform(win) * rasterio.Affine.scale(
        win.width / 4160, win.height / 1600)
    dem[dem == -32768] = np.nan
with rasterio.open('/tmp/my-project/gh-clone/download/mosaic_5.90/'
                   'spb_quality_5.90.tif') as s:
    win = W.from_bounds(*SB, s.transform)
    q = s.read(3, window=win, out_shape=(1600, 4160),
               resampling=Resampling.average).astype('float32')
    q[q >= 65535] = np.nan

fig, axes = plt.subplots(2, 1, figsize=(11, 8.6),
                         constrained_layout=True)
ext = [SB[0], SB[2], SB[1], SB[3]]

im0 = axes[0].imshow(dem / 100.0, extent=ext, origin='upper',
                     cmap='terrain', vmin=-1, vmax=100)
axes[0].set_title('Мозаика 5.90, юг: DEM 2 м, БС-77 '
                  '(prior v2 = FABDEM+model, 63 зоны prior-only)',
                  fontsize=11)
plt.colorbar(im0, ax=axes[0], label='м', shrink=0.85)
im1 = axes[1].imshow(q, extent=ext, origin='upper', cmap='YlGn',
                     vmin=0, vmax=800)
axes[1].set_title('Канал can30 (GLO30−FABDEM, см) — гейт M-CLF: '
                  '<50 см = OK-тир', fontsize=11)
plt.colorbar(im1, ax=axes[1], label='см', shrink=0.85)

for ax in axes:
    for k, (s_, w_, n_, e_) in DZ.items():
        ax.add_patch(plt.Rectangle((w_, s_), e_ - w_, n_ - s_,
                                   fill=False, ec='k', lw=0.7, ls='--'))
        ax.text(w_ + 0.004, n_ - 0.004, k, fontsize=8, va='top')
    ax.set_xlim(SB[0], SB[2])
    ax.set_ylim(SB[1], SB[3])
    ax.set_ylabel('широта')
axes[1].set_xlabel('долгота')
fig.savefig(OUT, dpi=130)
print('saved', OUT)
