#!/usr/bin/env python3
"""5.48: дедуп отметок lenobl + сводка по распоряжениям."""
import csv
import json
import re
from collections import Counter, defaultdict

REPO = "/home/z/my-project/spb45"
SRC = f"{REPO}/results/lenobl_marks_5.48.csv"
OUT = f"{REPO}/results/lenobl_marks_5.48_dedup.csv"
MAN = f"{REPO}/results/lenobl_manifest_5.48.json"

man = json.load(open(MAN))
titles = {}
for v in man.values():
    rid = v.get("id")
    if rid and v.get("title"):
        titles[rid] = v["title"]

seen = set()
rows = []
for r in csv.DictReader(open(SRC)):
    key = (r["rid"], r["pdf"], r["page"], r["val"], r["x"], r["y"], r["kind"])
    if key in seen:
        continue
    seen.add(key)
    rows.append(r)

with open(OUT, "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=["rid", "pdf", "page", "val", "x", "y",
                                      "kind", "size"])
    w.writeheader()
    w.writerows(rows)
print(f"было {sum(1 for _ in open(SRC))-1}, стало {len(rows)} (дедуп)")

# сводка по rid
by = defaultdict(lambda: {"black": 0, "red": 0, "other": 0, "vals": set(),
                          "pdfs": set(), "pages": set()})
for r in rows:
    k = r["kind"] if r["kind"] in ("black", "red") else "other"
    d = by[r["rid"]]
    d[k] += 1
    d["vals"].add(float(r["val"]))
    d["pdfs"].add(r["pdf"])
    d["pages"].add(r["page"])
summary = []
for rid, d in sorted(by.items(), key=lambda kv: -kv[1]["black"]):
    summary.append({
        "rid": rid,
        "title": titles.get(rid, "")[:200],
        "black": d["black"], "red": d["red"], "other": d["other"],
        "unique_values": len(d["vals"]),
        "pdfs": len(d["pdfs"]), "pages": len(d["pages"]),
        "vmin": round(min(d["vals"]), 2), "vmax": round(max(d["vals"]), 2),
    })
json.dump(summary, open(f"{REPO}/results/lenobl_summary_5.48.json", "w"),
          ensure_ascii=False, indent=1)
tot_black = sum(s["black"] for s in summary)
tot_red = sum(s["red"] for s in summary)
print(f"\nраспоряжений с отметками: {len(summary)}")
print(f"black (существующий рельеф): {tot_black}, red (проектные): {tot_red}")
for s in summary[:15]:
    print(f"  {s['rid']:8s} black={s['black']:6d} red={s['red']:5d} "
          f"uniq={s['unique_values']:4d} [{s['vmin']}-{s['vmax']}] "
          f"pdfs={s['pdfs']:3d} | {s['title'][:52]}")
