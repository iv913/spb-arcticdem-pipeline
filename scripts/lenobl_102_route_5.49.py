#!/usr/bin/env python3
"""5.49: трасса ВЛ 110 кВ Пикалёвская-1 из «Ведомости прямых и углов поворота»
(Том 4, Приложение 01 .pdf, с.75+) + пункты ГГС (с.9).
Выход: results/lenobl_102_route_5.49.json"""
import json
import os
import re
import sys

import fitz

PDF = ("Том 4. Материалы по обоснованию ППТ. Пояснительная записка/"
       "Том 4. Приложение 01 .pdf")
OUT = "/home/z/my-project/base/results/lenobl_102_route_5.49.json"
XY_SPAN = re.compile(r"^(\d{6})[.,](\d{2})\s+(\d{7})[.,](\d{2})$")
PK = re.compile(r"^\d+\+\d{2}[.,]\d{2}$")
NAME = re.compile(r"^(НТ|ВУ\d+|КТ)$")
GGS_NUM = re.compile(r"^\d{6}[.,]\d{2}$")
GGS_H = re.compile(r"^\d{2,3}[.,]\d{3}$")


def page_lines(doc, pno):
    """Визуальные строки: список [(x, text)] по y-кластерам."""
    raw = doc[pno].get_text("dict")
    spans = []
    for b in raw.get("blocks", []):
        for l in b.get("lines", []):
            for s in l.get("spans", []):
                t = s["text"].strip()
                if t:
                    spans.append((s["bbox"][1], s["bbox"][0], t))
    spans.sort()
    lines, cur, y0 = [], [], None
    for y, x, t in spans:
        if y0 is None or abs(y - y0) < 4:
            cur.append((x, t)); y0 = y if y0 is None else y0
        else:
            lines.append(cur); cur = [(x, t)]; y0 = y
    if cur:
        lines.append(cur)
    return [sorted(l, key=lambda p: p[0]) for l in lines]


def parse_vedomost(path):
    doc = fitz.open(path)
    route = []
    for pno in range(len(doc)):  # ведомость продолжается без заголовка
        for ln in page_lines(doc, pno):
            toks = [t for _, t in ln]
            joined = " ".join(toks)
            mname = re.search(r"\b(НТ|ВУ\d+|КТ)\b", joined)
            if not mname:
                continue
            mpk = re.search(r"(\d+\+\d{2}[.,]\d{2})", joined)
            xy = next((t for t in toks if XY_SPAN.match(t)), None)
            if not (mpk and xy):
                continue
            m = XY_SPAN.match(xy)
            route.append({"name": mname.group(1), "pk": mpk.group(1),
                          "x": float(m.group(1) + "." + m.group(2)),
                          "y": float(m.group(3) + "." + m.group(4)),
                          "page": pno + 1})
    # дедуп (двойной проход страницы) с сохранением порядка
    seen, out = set(), []
    for r in route:
        k = (r["name"], r["pk"])
        if k not in seen:
            seen.add(k); out.append(r)
    return out


def parse_ggs(path):
    doc = fitz.open(path)
    out = []
    for pno in range(len(doc)):
        if "Таблица № 2" not in doc[pno].get_text("text"):
            continue
        txt = doc[pno].get_text("text")
        for m in re.finditer(
                r"(Дыми|Симоново|Рудник|Павловские\s*Концы|Замошье|Сара|Отвал|Сергеево|Срергеево)"
                r"[\s\S]{0,60}?(\d{6})[.,](\d{2})[\s\S]{0,60}?(\d{7})[.,](\d{2})"
                r"[\s\S]{0,60}?(\d{2,3})[.,](\d{3})", txt):
            out.append({"name": m.group(1).replace("\n", " "),
                        "x": float(m.group(2) + "." + m.group(3)),
                        "y": float(m.group(4) + "." + m.group(5)),
                        "h": float(m.group(6) + "." + m.group(7)),
                        "page": pno + 1})
    return out


def validate(route):
    """Расстояния между вершинами vs разность пикетов."""
    rep = []
    def pk2m(pk):
        a, b = pk.split("+")
        return int(a) * 100 + float(b)
    for a, b in zip(route, route[1:]):
        d = ((b["x"] - a["x"]) ** 2 + (b["y"] - a["y"]) ** 2) ** 0.5
        dp = abs(pk2m(b["pk"]) - pk2m(a["pk"]))
        rep.append({"from": a["name"], "to": b["name"],
                    "dist_m": round(d, 2), "dpk_m": round(dp, 2),
                    "diff_m": round(d - dp, 2)})
    return rep


if __name__ == "__main__":
    os.chdir("/home/z/my-project/wr/lenobl/ppt102")
    path = sys.argv[1] if len(sys.argv) > 1 else PDF
    route = parse_vedomost(path)
    ggs = parse_ggs(path)
    rep = validate(route)
    print(f"ВУ: {len(route)}, ГГС: {len(ggs)}")
    for r in route:
        print(f"  {r['name']:5s} ПК {r['pk']:>10s} X {r['x']:.2f} Y {r['y']:.2f}")
    bad = [r for r in rep if abs(r["diff_m"]) > 1]
    print(f"контроль dist vs ΔПК: {len(rep)} сегментов, расхождений >1м: {len(bad)}")
    for r in rep[:6]:
        print("  ", r)
    json.dump({"route": route, "ggs": ggs, "validation": rep,
               "system": "МСК1947 зона 3; высоты Балтийская (отчёт ИГДИ 101-ИГДИ)"},
              open(OUT, "w", encoding="utf8"), ensure_ascii=False, indent=1)
    print("->", OUT)
