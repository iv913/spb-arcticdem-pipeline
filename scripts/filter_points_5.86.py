#!/usr/bin/env python3
"""5.86d — Фильтрация «снятых точек» по визуальным классам отказов + quality-каналам.

Классы отказов (из атласа diag_5.86):
  GEO    — якорная геолокация мимо объекта (Коломяги, Энергетиков, Кондратьевский…)
  SITE   — рельеф изменён стройкой после съёмки отметок (Электростанция №2…)
  CANOPY — точка в кронах (30м-канопи > 50 см): валидирует DSM, не DTM
  SIGMA  — низкое доверие стрипа (sigma > 50 см)
  OUT5   — одиночный выброс |dH| > 5 м (после GEO)
  OUTSIDE— вне мозаики 5.84 (отдельный учёт)

Правила: авто-детект GEO = якорь(project) n>=4 c |med|>3м и MAD<3м (однородный сдвиг).
Выход: scripts/data/{canon2m,icesat2}_filtered_5.86.csv, results/filtering_5.86.json
"""
import numpy as np
import pandas as pd
import json, os

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def metrics(dh):
    dh = dh.dropna()
    return dict(n=int(len(dh)), med=round(float(dh.median()), 3),
                mad=round(float((dh - dh.median()).abs().median()), 3),
                p95=round(float(dh.abs().quantile(0.95)), 3),
                share_le_05=round(float((dh.abs() <= 0.5).mean()), 3))


# ================= канон (проектные точки) =================
cn = pd.read_csv(f"{R}/scripts/data/canon2m_5.86.csv")
cn["qa_flags"] = ""
covered = cn.dH2m.notna()
cn.loc[~covered, "qa_flags"] = "OUTSIDE"
print("канон: всего", len(cn), "| вне мозаики:", (~covered).sum())

sub = cn[covered].copy()
proj = sub.source.str.replace("^archi_", "", regex=False)
med_p = sub.groupby(proj).dH2m.agg(["size", "median", lambda x: (x - x.median()).abs().median()])
med_p.columns = ["n", "med", "mad"]
# GEO: однородный сдвиг (n>=4, |med|>3, MAD<3) ЛИБО грубо испорченный якорь (n>=8, |med|>3)
geo_mask = ((med_p.n >= 4) & (med_p.med.abs() > 3.0) & (med_p.mad < 3.0)) | \
           ((med_p.n >= 8) & (med_p.med.abs() > 3.0))
geo_anchors = med_p[geo_mask].index.tolist()
print("GEO-якоря (n>=4, |med|>3, MAD<3):", geo_anchors)

is_geo = proj.isin(geo_anchors)
sub.loc[is_geo, "qa_flags"] += " GEO"
sub.loc[sub.canopy30_cm > 50, "qa_flags"] += " CANOPY"
sub.loc[sub.sigma_cm > 50, "qa_flags"] += " SIGMA"
sub.loc[(sub.dH2m.abs() > 5) & ~is_geo, "qa_flags"] += " OUT5"
sub["qa_flags"] = sub["qa_flags"].str.strip()

for fl in ["GEO", "CANOPY", "SIGMA", "OUT5"]:
    print(f"  {fl}: {sub.qa_flags.str.contains(fl).sum()}")

keep = ~sub.qa_flags.str.contains("GEO|CANOPY|SIGMA|OUT5")
before, after = metrics(sub.dH2m), metrics(sub[keep].dH2m)
print("\nканон ДО :", before)
print("канон ПОСЛЕ (n_keep={}/{}):".format(keep.sum(), len(sub)), after)

cn = cn.merge(sub[["qa_flags"]], left_index=True, right_index=True, how="left", suffixes=("", "_sub"))
cn.to_csv(f"{R}/scripts/data/canon2m_filtered_5.86.csv", index=False)

# ================= ICESat-2 =================
ic = pd.read_csv(f"{R}/scripts/data/icesat2_vs_mosaic_5.86.csv")
ic["qa_flags"] = ""
cov = ic.dH2m.notna()
ic.loc[~cov, "qa_flags"] = "OUTSIDE"
si = ic[cov].copy()
si.loc[si.canopy30_cm > 50, "qa_flags"] += " CANOPY"
si.loc[si.sigma_cm > 50, "qa_flags"] += " SIGMA"
si.loc[si.dH2m.abs() > 2, "qa_flags"] += " LEAK2"  # крыши/смена рельефа в модели
si["qa_flags"] = si["qa_flags"].str.strip()
for fl in ["CANOPY", "SIGMA", "LEAK2"]:
    print(f"  ICESat {fl}: {si.qa_flags.str.contains(fl).sum()}")
keepi = ~si.qa_flags.str.contains("CANOPY|SIGMA|LEAK2")
bi, ai = metrics(si.dH2m), metrics(si[keepi].dH2m)
print("\nICESat ДО :", bi)
print("ICESat ПОСЛЕ (n_keep={}/{}):".format(keepi.sum(), len(si)), ai)

# доверенный сабсет (trust=1)
kt = keepi & (si.trust == 1)
print("ICESat ПОСЛЕ + trust=1:", metrics(si[kt].dH2m))

si.to_csv(f"{R}/scripts/data/icesat2_filtered_5.86.csv", index=False)

json.dump(dict(canon=dict(before=before, after=after, geo_anchors=geo_anchors,
                          dropped=dict(GEO=int(is_geo.sum()),
                                       CANOPY=int(sub.qa_flags.str.contains("CANOPY").sum()),
                                       SIGMA=int(sub.qa_flags.str.contains("SIGMA").sum()),
                                       OUT5=int(sub.qa_flags.str.contains("OUT5").sum()),
                                       OUTSIDE=int((~covered).sum()))),
                    icesat=dict(before=bi, after=ai, after_trust=metrics(si[kt].dH2m),
                                dropped=dict(CANOPY=int(si.qa_flags.str.contains("CANOPY").sum()),
                                             SIGMA=int(si.qa_flags.str.contains("SIGMA").sum()),
                                             LEAK2=int(si.qa_flags.str.contains("LEAK2").sum()),
                                             OUTSIDE=int((~cov).sum())))),
               open(f"{R}/results/filtering_5.86.json", "w"), ensure_ascii=False, indent=1)
print("\nsaved results/filtering_5.86.json")
