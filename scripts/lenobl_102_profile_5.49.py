#!/usr/bin/env python3
"""5.49: продольный профиль трассы ВЛ 110 кВ Пикалёвская-1 — извлечение
«чёрных отметок» (существующий рельеф) с листов Приложения 04.pdf.
Метод: ПК-подписи -> робастный линейный fit station(x); плотнейший ряд
чёрных числовых меток -> (станция, высота). Выход:
results/lenobl_102_profile_5.49.csv"""
import csv
import os
import re

import fitz
import numpy as np

os.chdir("/home/z/my-project/wr/lenobl/ppt102")

PDF = ("Том 4. Материалы по обоснованию ППТ. Пояснительная записка/"
       "Том 4. Приложение 04.pdf")
OUT = "/home/z/my-project/base/results/lenobl_102_profile_5.49.csv"
NUM = re.compile(r"^(\d{1,3})[.,](\d{1,2})$")
PK = re.compile(r"ПК\s*(\d+)\+(\d+(?:[.,]\d+)?)$")


def page_spans(page):
    raw = page.get_text("dict")
    seen = set()
    spans = []
    for b in raw.get("blocks", []):
        for l in b.get("lines", []):
            for s in l.get("spans", []):
                t = s["text"].strip()
                if not t:
                    continue
                key = (t, round(s["bbox"][0] / 3), round(s["bbox"][1] / 3))
                if key in seen:
                    continue
                seen.add(key)
                spans.append({"y": s["bbox"][1], "x": (s["bbox"][0] + s["bbox"][2]) / 2,
                              "t": t, "c": s.get("color", 0), "sz": s["size"]})
    return spans


def robust_fit(xs, ss):
    xs, ss = np.asarray(xs), np.asarray(ss)
    keep = np.ones(len(xs), bool)
    sol = np.array([0.0, ss.mean() if len(ss) else 0])
    for _ in range(4):
        A = np.column_stack([xs[keep], np.ones(keep.sum())])
        if len(A) < 2:
            break
        sol, *_ = np.linalg.lstsq(A, ss[keep], rcond=None)
        r = np.abs(xs * sol[0] + sol[1] - ss)
        thr = max(3.0, 2.5 * np.median(r[keep]) + 1e-9)
        keep = r < thr
    return sol, keep


def main():
    doc = fitz.open(PDF)
    all_pts = []
    pages_used = []
    for pi in range(len(doc)):
        page = doc[pi]
        W = page.rect.width
        if W < 3000:
            continue  # продольные профили — широкие листы
        spans = page_spans(page)
        pks = []
        for s in spans:
            m = PK.match(s["t"].replace(" ", ""))
            if m and s["sz"] > 5:
                pks.append((s["x"], int(m.group(1)) * 100 + float(m.group(2).replace(",", "."))))
        if len(pks) < 3:
            continue
        sol, keep = robust_fit([p[0] for p in pks], [p[1] for p in pks])
        # ряд чёрных числовых меток: плотнейший y-бин
        nums = [s for s in spans if NUM.match(s["t"])
                and s["c"] in (0, 0x000000) and 5 <= s["sz"] <= 13]
        if not nums:
            continue
        bins = {}
        for s in nums:
            bins.setdefault(round(s["y"] / 8), []).append(s)
        best_y = max(bins, key=lambda k: len(bins[k]))
        row = bins[best_y]
        # соседние бины того же ряда (ряд может «рваться»)
        row = [s for s in nums if abs(s["y"] - best_y * 8) < 9]
        if len(row) < 25:
            continue
        pts = []
        for s in row:
            m = NUM.match(s["t"])
            v = float(m.group(1) + "." + m.group(2))
            st = sol[0] * s["x"] + sol[1]
            if 0 <= v <= 300 and -100 <= st <= 24500:
                pts.append((st, v))
        if len(pts) >= 25:
            all_pts.extend(pts)
            pages_used.append({"page": pi + 1, "n_pk": len(pks),
                               "pk_resid_max": float(np.abs(np.asarray(
                                   [p[0] * sol[0] + sol[1] for p in pks] if False else
                                   [p[0] * sol[0] + sol[1] for p in pks]) -
                                   np.asarray([p[1] for p in pks])).max()) if False else None,
                               "n_pts": len(pts),
                               "scale_m_px": round(sol[0], 4),
                               "st_min": round(min(p[0] for p in pts)),
                               "st_max": round(max(p[0] for p in pts))})
            print(f"стр.{pi+1}: ПК-меток {len(pks)}, scale {sol[0]:.4f} м/px, "
                  f"точек {len(pts)}, станции {min(p[0] for p in pts):.0f}–{max(p[0] for p in pts):.0f}")
    # дедуп по станции (перекрытия листов)
    all_pts.sort()
    dedup = []
    for st, v in all_pts:
        if dedup and abs(st - dedup[-1][0]) < 3:
            if abs(v - dedup[-1][1]) > 0.01 and len(dedup) > 1:
                # перекрытие с разным значением — берём среднее, если очень близко
                if abs(st - dedup[-1][0]) < 1:
                    dedup[-1] = ((dedup[-1][0] + st) / 2, (dedup[-1][1] + v) / 2)
            continue
        dedup.append((st, v))
    with open(OUT, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["station_m", "h_black_m"])
        for st, v in dedup:
            w.writerow([round(st, 2), v])
    print(f"ИТОГО: {len(dedup)} точек профиля -> {OUT}")
    print("листы:", pages_used)


if __name__ == "__main__":
    main()
