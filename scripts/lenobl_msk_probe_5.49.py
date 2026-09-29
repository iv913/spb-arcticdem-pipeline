#!/usr/bin/env python3
"""5.49: проба МСК-координат в текстах ПМТ/ППТ (arch.lenobl.ru).
Ищем: (1) упоминания местных систем координат, (2) координатные пары X/Y,
(3) подписи координатной сетки на листах. Аргумент: путь к PDF."""
import re
import sys
import json
import collections

import fitz

MSK_WORDS = re.compile(r"МСК[\s\-–—]?\d{1,3}|местн\w* сист\w* координ|СК[- ]?\d{2}\b|система координат", re.I)
# координаты: 6-7-значные целые с 1-2 знаками после разделителя
XY7 = re.compile(r"\b([67]\d{6})[.,](\d{1,2})\b")
# подписи сетки: "6123,45" + "30123" и т.п. (тысячные с запятой)
GRID = re.compile(r"\b([367]\d{1,3})[.,](\d{2})\b")


def scan(path):
    doc = fitz.open(path)
    res = {"file": path, "pages": len(doc), "msk_mentions": collections.Counter(),
           "msk_pages": {}, "xy_pairs": 0, "xy_samples": [], "per_page_xy": collections.Counter()}
    xy_page = collections.Counter()
    for pno in range(len(doc)):
        try:
            txt = doc[pno].get_text("text")
        except Exception:
            continue
        for m in MSK_WORDS.finditer(txt):
            key = m.group(0)[:40]
            res["msk_mentions"][key] += 1
            res["msk_pages"].setdefault(key, []).append(pno + 1)
        hits = XY7.findall(txt)
        if hits:
            xy_page[pno + 1] += len(hits)
            res["xy_pairs"] += len(hits)
            if len(res["xy_samples"]) < 12:
                for h in hits[:3]:
                    res["xy_samples"].append({"page": pno + 1, "x": h[0] + "." + h[1]})
    res["per_page_xy"] = dict(xy_page.most_common(10))
    res["msk_mentions"] = dict(res["msk_mentions"].most_common(10))
    res["msk_pages"] = {k: v[:5] for k, v in res["msk_pages"].items()}
    doc.close()
    return res


if __name__ == "__main__":
    for p in sys.argv[1:]:
        r = scan(p)
        print(json.dumps(r, ensure_ascii=False, indent=1)[:3000])
