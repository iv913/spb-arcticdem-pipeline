#!/usr/bin/env python3
"""5.49: ЕГРН-пацели из «Приложения 05-50.pdf» (Том 4): кадастровый номер +
таблица характерных точек (X, Y в МСК1947 з.3). Выход:
results/lenobl_102_parcels_5.49.json"""
import json
import os
import re

import fitz

PDF = ("Том 4. Материалы по обоснованию ППТ. Пояснительная записка/"
       "Том 4. Приложения 05-50.pdf")
OUT = "/home/z/my-project/base/results/lenobl_102_parcels_5.49.json"
KAD = re.compile(r"47:18:\d+:\d+")
XY = re.compile(r"(\d{6})[.,](\d{2})[\s\n]+(\d{7})[.,](\d{2})")


def main():
    os.chdir("/home/z/my-project/wr/lenobl/ppt102")
    doc = fitz.open(PDF)
    parcels, cur = {}, None
    for pno in range(len(doc)):
        txt = doc[pno].get_text("text")
        if "характерных точках границы" in txt:
            km = KAD.search(txt)
            if km:
                cur = km.group(0)
                parcels.setdefault(cur, {"page": pno + 1, "pts": {}})
        if cur is None:
            continue
        for m in XY.finditer(txt):
            x = float(m.group(1) + "." + m.group(2))
            y = float(m.group(3) + "." + m.group(4))
            # отсечь случайные пары не-координат: X в 3xx-4xx тыс., Y в 3.2-3.3 млн
            if not (300000 < x < 500000 and 3200000 < y < 3300000):
                continue
            parcels[cur]["pts"][f"{x:.2f}_{y:.2f}"] = [x, y]
    out = {k: {"page": v["page"], "n": len(v["pts"]),
               "pts": list(v["pts"].values())}
           for k, v in parcels.items()}
    json.dump(out, open(OUT, "w", encoding="utf8"), ensure_ascii=False, indent=1)
    print(f"пацелей: {len(out)}, точек суммарно: {sum(v['n'] for v in out.values())}")
    for k, v in list(out.items())[:10]:
        print(f"  {k}  n={v['n']}  стр.{v['page']}")
    print("->", OUT)


if __name__ == "__main__":
    main()
