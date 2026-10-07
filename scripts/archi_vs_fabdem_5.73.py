#!/usr/bin/env python3
"""5.73: Полный archi-канон (1131) vs FABDEM bare-earth — per-anchor таблица.
Цель: корректированная таблица med dH (было: vs M-CLF в 5.66) для калибровки.
"""
import numpy as np
import pandas as pd
import rasterio

REPO = "/home/z/my-project/repo"
DTM = f"{REPO}/scripts/data/consensus_dtm_5.71/bareearth_bs77_cm_5.71.tif"

df = pd.read_csv(f"{REPO}/scripts/data/archi_final_clean_v2.csv")
with rasterio.open(DTM) as s:
    fab = s.read(1).astype("float64")
    tr = s.transform
    nod = s.nodata
H, W = fab.shape
fab[fab == nod] = np.nan
fab[fab < -1000] = np.nan

rows, cols = rasterio.transform.rowcol(tr, df.lon.values, df.lat.values)
rows = np.clip(rows, 0, H - 1)
cols = np.clip(cols, 0, W - 1)
df["h_fab"] = fab[rows, cols] / 100.0
df["dH_fab"] = df.value - df.h_fab  # знак как в 5.66: value - земля

g = df.groupby("project_name").agg(
    n=("value", "size"), lat=("lat", "first"), lon=("lon", "first"),
    val_med=("value", "median"), h_fab=("h_fab", "first"),
    med_dH_fab=("dH_fab", "median"), mad=("dH_fab",
                                         lambda x: np.nanmedian(np.abs(x - np.nanmedian(x)))),
    med_dH_model=("dH", "median")).reset_index()
g = g.sort_values("n", ascending=False)
g.to_csv(f"{REPO}/scripts/data/archi_vs_fabdem_5.73.csv", index=False)
ok = g.dropna(subset=["med_dH_fab"])
print(f"anchors: {len(g)}, with DTM: {len(ok)}")
print(f"GLOBAL: med dH_fab={ok.med_dH_fab.median():.2f}  "
      f"MAD={ok.med_dH_fab.sub(ok.med_dH_fab.median()).abs().median():.2f}")
print(f"per-mark: med dH_fab={df.dH_fab.median():.2f} "
      f"MAD={df.dH_fab.abs().median():.2f} r={np.corrcoef(df.dropna(subset=['h_fab']).value, df.dropna(subset=['h_fab']).h_fab)[0,1]:.3f}")
print("\nТоп-12 якорей (n):")
for _, r in ok.head(12).iterrows():
    print(f"  {r.n:4d} {r.project_name[:36]:36s} val_med={r.val_med:6.2f} "
          f"fab={r.h_fab:6.2f} dH_fab={r.med_dH_fab:+6.2f} "
          f"(dH_model={r.med_dH_model:+6.2f}) MAD={r.mad:.2f}")
worst = ok.reindex(ok.med_dH_fab.abs().sort_values(ascending=False).index).head(6)
print("\nНаибольшие |med dH_fab| (кандидаты на геолокацию/датум):")
for _, r in worst.iterrows():
    print(f"  {r.n:4d} {r.project_name[:36]:36s} dH_fab={r.med_dH_fab:+6.2f} "
          f"(dH_model={r.med_dH_model:+6.2f}) {r.lat:.4f},{r.lon:.4f}")
