#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""5.88 — Интеграция демпа ATL06 5.86e в расширенный валидационный канон
(icesat2_clean_v2) по правилу §6 отчёта 5.87 + реверификация зон 5.83
на новом каноне. Закрывает пункт «датум-фикс §6» главного плана
(датум-сдвиг опровергнут в 5.87 триплет-диагностикой).

Правило 5.88 (results/diag_dates_5.87.md §6):
  1) DTM-канон v2 = |dH|<=3.0 (5.68-C) И canopy30 < 100 см, сезон НЕ ограничен;
  2) кроны canopy30 >= 100 см — отдельный DSM-бакет (leaf-on med +0.70);
  3) зимние хвосты обрезаны |dH|<=3; строгий core = base & sigma<=50 & trust>=1;
  4) ожидание: канон 33,908 -> ~150-210k, MAD контура ~0.32-0.35.

Вход:
  scripts/data/icesat2_atl06_dump_5.86.csv.gz   (691,818 сегм, демп 5.86e)
  scripts/data/icesat2_clean.csv                (старый канон 33,908)
  results/zones_summary_5.83.csv                (21 зона: bbox + MAD 5.83)
  download/mosaic_5.84/spb_hybrid_2m_mosaic_5.84.tif (Int16 см БС-77)

Выход:
  scripts/data/icesat2_clean_v2_5.88.csv.gz     (канон v2, base)
  scripts/data/icesat2_dsm_crowns_v2_5.88.csv.gz(DSM-бакет крон)
  results/canon_v2_5.88.json / .md
  results/zone_reverify_5.88.csv / .png
"""
import numpy as np
import pandas as pd
import rasterio, json, os, sys

R = "/tmp/my-project/gh-clone"
DUMP = f"{R}/scripts/data/icesat2_atl06_dump_5.86.csv.gz"
DEM = f"{R}/download/mosaic_5.84/spb_hybrid_2m_mosaic_5.84.tif"
OLD = f"{R}/scripts/data/icesat2_clean.csv"
ZONES = f"{R}/results/zones_summary_5.83.csv"
OUT_DATA = f"{R}/scripts/data"
OUT_RES = f"{R}/results"

# ------------------------------------------------------------------ helpers
def metrics(dh):
    dh = np.asarray(dh, float)
    dh = dh[np.isfinite(dh)]
    if not len(dh):
        return dict(n=0)
    return dict(n=int(len(dh)),
                med=round(float(np.median(dh)), 3),
                mad=round(float(np.median(np.abs(dh - np.median(dh)))), 3),
                p95=round(float(np.percentile(np.abs(dh), 95)), 3),
                share_le_05=round(float((np.abs(dh) <= 0.5).mean()), 3),
                share_le_03=round(float((np.abs(dh) <= 0.3).mean()), 3))

# ------------------------------------------------------------------ load
print("загрузка демпа...", flush=True)
dump = pd.read_csv(DUMP)
cov = dump[dump.dH2m.notna()].copy()
print(f"демп {len(dump)}, покрыто мозаикой {len(cov)}", flush=True)

cov["season"] = np.where(cov.date.astype(str).str[4:6].isin(
    ["11", "12", "01", "02", "03", "04"]), "leaf_off", "leaf_on")
# блок-паритет train/holdout — как в 5.83 (0.02° x 0.0127°)
blk = (np.floor(cov.lat / 0.02).astype(int) +
       np.floor(cov.lon / 0.0127).astype(int))
cov["blk_half"] = (blk % 2).values
cov["cell"] = cov.lat.round(3).astype(str) + "_" + cov.lon.round(3).astype(str)

# ------------------------------------------------------------------ canon v2
base = (cov.dH2m.abs() <= 3.0) & (cov.canopy30_cm < 100)
core = base & (cov.sigma_cm <= 50) & (cov.trust >= 1)
dsm = (cov.dH2m.abs() <= 3.0) & (cov.canopy30_cm >= 100)

canon = cov[base].copy()
canon["core"] = core[base].values
crowns = cov[dsm].copy()

n_base, n_core, n_dsm = len(canon), int(core[base].sum()), len(crowns)
print(f"канон v2 base: {n_base} | core: {n_core} | DSM-кроны: {n_dsm}",
      flush=True)

# ------------------------------------------------------------------ metrics
res = dict(
    protocol="v2: |dH2m|<=3 & canopy30<100 (сезон любой); core: +sigma<=50 & trust>=1; DSM: canopy>=100",
    delta_baltic=16.04,
    counts=dict(dump_raw=int(len(dump)), covered=int(len(cov)),
                v2_base=n_base, v2_core=n_core, v2_dsm_crowns=n_dsm,
                old_canon=33908),
    v2_base=metrics(canon.dH2m),
    v2_core=metrics(canon[canon.core].dH2m),
    v2_dsm_crowns=metrics(crowns.dH2m),
    v2_base_train=metrics(canon[canon.blk_half == 0].dH2m),
    v2_base_holdout=metrics(canon[canon.blk_half == 1].dH2m),
    v2_core_train=metrics(canon[canon.core & (canon.blk_half == 0)].dH2m),
    v2_core_holdout=metrics(canon[canon.core & (canon.blk_half == 1)].dH2m),
    by_season={},
    uniq_cells=dict(
        n_cells=int(canon.cell.nunique()),
        cell_median=metrics(canon.groupby("cell").dH2m.median()),
        core_cell_median=metrics(canon[canon.core].groupby("cell").dH2m.median())),
)

for ses in ("leaf_off", "leaf_on"):
    m = canon.season == ses
    res["by_season"][ses] = dict(
        base=metrics(canon[m].dH2m),
        core=metrics(canon[m & canon.core].dH2m),
        dsm=metrics(crowns[crowns.season == ses].dH2m))

# районы (bbox-словарь 5.86, консистентно с dump_5.86.json)
dz = {
    "Кировский":        (59.83, 30.14, 59.90, 30.32),
    "Красносельский":   (59.79, 30.10, 59.88, 30.30),
    "Пушкинский":       (59.70, 30.22, 59.80, 30.45),
    "Колпинский":       (59.70, 30.42, 59.80, 30.62),
    "Петродворцовый":   (59.84, 29.88, 59.93, 30.13),
    "Кронштадтский":    (59.95, 29.60, 60.02, 29.80),
    "Курортный":        (60.09, 29.85, 60.20, 30.30),
}
res["districts_v2"] = {}
for name, (a, b, c, d) in dz.items():
    m = canon[(canon.lat >= a) & (canon.lat < c) & (canon.lon >= b) & (canon.lon < d)]
    mc = m[m.core]
    res["districts_v2"][name] = dict(
        base=metrics(m.dH2m), core=metrics(mc.dH2m),
        dsm=metrics(crowns[(crowns.lat >= a) & (crowns.lat < c) &
                           (crowns.lon >= b) & (crowns.lon < d)].dH2m))

# старый канон на мозаике (для честного сравнения контуров) — числа 5.86
try:
    old_vs = pd.read_csv(f"{OUT_DATA}/icesat2_vs_mosaic_5.86.csv")
    res["old_canon_on_mosaic"] = metrics(old_vs.dH2m)
    ok = old_vs.dH2m.notna()
    res["old_canon_on_mosaic_covered"] = dict(n=int(ok.sum()))
except Exception as e:
    print("старый канон vs мозаика CSV не найден:", e)

# ------------------------------------------------------------------ save canon CSVs
keep = ["lat", "lon", "rgt", "cycle", "gt", "date", "season", "h_baltic",
        "dem2m", "dH2m", "canopy30_cm", "sigma_cm", "n_cov", "trust",
        "blk_half", "core"]
canon[keep].to_csv(f"{OUT_DATA}/icesat2_clean_v2_5.88.csv.gz",
                   index=False, compression="gzip")
crowns[keep[:-1]].assign(dsm=True, core=False).to_csv(
    f"{OUT_DATA}/icesat2_dsm_crowns_v2_5.88.csv.gz",
    index=False, compression="gzip")
print("CSV сохранены", flush=True)

# ------------------------------------------------------------------ zone reverification
zs = pd.read_csv(ZONES)
rows = []
for _, z in zs.iterrows():
    tag = z["tag"]
    try:
        W_, S_, E_, N_ = [float(v) for v in z["bbox"].split()]
    except Exception:
        continue
    m = canon[(canon.lon >= W_) & (canon.lon <= E_) &
              (canon.lat >= S_) & (canon.lat <= N_)]
    mt, mv = m[m.blk_half == 0], m[m.blk_half == 1]
    rows.append(dict(
        tag=tag, product=z["product"],
        old_product_mad=(None if pd.isna(z["product_mad"]) else float(z["product_mad"])),
        old_prior_mad=float(z["prior_mad"]),
        v2_n=len(m), v2_train=metrics(mt.dH2m), v2_holdout=metrics(mv.dH2m),
        verdict_old=("hybrid" if "hybrid" in str(z["product"])
                     else "prior")))
zr = pd.DataFrame(rows)
zr.drop(columns=["v2_train", "v2_holdout"]).to_csv(
    f"{OUT_RES}/zone_reverify_5.88.csv", index=False)
for r in rows:
    res.setdefault("zones_v2", {})[r["tag"]] = dict(
        n=r["v2_n"], train=r["v2_train"], holdout=r["v2_holdout"],
        old_product_mad=r["old_product_mad"], old_prior_mad=r["old_prior_mad"])

json.dump(res, open(f"{OUT_RES}/canon_v2_5.88.json", "w"),
          ensure_ascii=False, indent=1)
print("JSON сохранён", flush=True)

# ------------------------------------------------------------------ figure
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

fig, axes = plt.subplots(1, 2, figsize=(14, 6), constrained_layout=True)
zr2 = zr[zr.v2_n > 0].copy()
x = np.arange(len(zr2))
ax = axes[0]
ax.bar(x - 0.2, [v if v else np.nan for v in zr2.old_prior_mad],
       0.4, label="prior MAD (5.83, старый канон)", color="#b8c4d9")
ax.bar(x + 0.2, [h["mad"] for h in zr2.v2_holdout], 0.4,
       label="holdout MAD (v2-канон, мозаика)", color="#1f6f43")
ax.set_xticks(x)
ax.set_xticklabels(zr2.tag, rotation=60, ha="right", fontsize=8)
ax.set_ylabel("MAD, м")
ax.set_title("Реверификация зон 5.83: MAD старый канон → v2-канон")
ax.legend(fontsize=8)
ax.grid(axis="y", alpha=0.3)

ax = axes[1]
cats = ["base\n(все сезоны)", "base\nleaf-off", "base\nleaf-on",
        "core\n(золото)", "DSM-кроны"]
vals = [n_base,
        res["by_season"]["leaf_off"]["base"]["n"],
        res["by_season"]["leaf_on"]["base"]["n"],
        n_core, n_dsm]
mads = [res["v2_base"]["mad"],
        res["by_season"]["leaf_off"]["base"]["mad"],
        res["by_season"]["leaf_on"]["base"]["mad"],
        res["v2_core"]["mad"],
        res["v2_dsm_crowns"]["mad"]]
bars = ax.bar(cats, vals, color=["#2c5f8a", "#7ba2c4", "#7ba2c4",
                                 "#1f6f43", "#a56b2a"])
for b, v, m in zip(bars, vals, mads):
    ax.text(b.get_x() + b.get_width() / 2, v, f"{v/1000:.0f}k\nMAD {m:.2f}",
            ha="center", va="bottom", fontsize=8)
ax.set_ylabel("точек")
ax.set_title("Состав канона v2 (5.88)")
ax.grid(axis="y", alpha=0.3)
fig.savefig(f"{OUT_RES}/zone_reverify_5.88.png", dpi=150)
plt.close(fig)
print("PNG сохранён", flush=True)

# ------------------------------------------------------------------ console summary
print("\n=== ИТОГ 5.88 ===")
for k in ("v2_base", "v2_core", "v2_dsm_crowns", "v2_base_train",
          "v2_base_holdout", "v2_core_train", "v2_core_holdout"):
    v = res[k]
    print(f"{k:18s} n={v['n']:7d} med={v['med']:+.3f} MAD={v['mad']:.3f} "
          f"p95={v['p95']:.3f} share<=0.5={v['share_le_05']:.3f}")
print(f"uniq cells: {res['uniq_cells']['n_cells']}, "
      f"cell-median MAD {res['uniq_cells']['cell_median']['mad']}")
for ses, v in res["by_season"].items():
    print(f"{ses:9s} base n={v['base']['n']:7d} MAD={v['base']['mad']:.3f} "
          f"med={v['base']['med']:+.3f} | core MAD={v['core']['mad']:.3f} "
          f"| dsm med={v['dsm']['med']:+.3f}")
print("\nзоны (holdout v2):")
for _, r in zr2.iterrows():
    h = r.v2_holdout
    print(f"  {r.tag:12s} n={h['n']:6d} MAD={h['mad']:.3f} "
          f"(old product MAD {r.old_product_mad}, prior {r.old_prior_mad})")
print("\nрайоны v2 (base/core):")
for name, v in res["districts_v2"].items():
    b, c = v["base"], v["core"]

    def fmt(d, key):
        return f"{d[key]:.3f}" if key in d else "  —  "

    def fn(d):
        return f"{d['n']:6d}" if "n" in d else "     0"
    print(f"  {name:16s} base n={fn(b)} MAD={fmt(b, 'mad')} | "
          f"core n={fn(c)} MAD={fmt(c, 'mad')} | dsm n={v['dsm'].get('n', 0)}")
