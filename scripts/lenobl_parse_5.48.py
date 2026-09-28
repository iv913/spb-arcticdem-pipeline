#!/usr/bin/env python3
"""5.48: разбор страницы «Утвержденная документация» arch.lenobl.ru.

Из статического HTML: каждый пункт (распоряжение) + его файлы.
Выход: results/lenobl_items_5.48.json
"""
import html
import json
import re
import urllib.parse

SRC = "/tmp/lenobl_utv2.html"
OUT = "/home/z/my-project/spb45/results/lenobl_items_5.48.json"

h = open(SRC, encoding="utf-8", errors="ignore").read()
# статьи: текст распоряжения — это <p> или текстовый блок, файлы — <a href=media/uploads>
# грубая сегментация по <p>
paras = re.split(r"<(?:p|div)[^>]*>", h)
items = []
cur = None
for p in paras:
    m = re.search(r"(Распоряжение[^<>]{40,400}?)</", p)
    text = html.unescape(re.sub(r"<[^>]+>", " ", p))
    text = re.sub(r"\s+", " ", text).strip()
    if re.match(r"^\s*Распоряжение", text) and len(text) > 60:
        cur = {"title": text[:400], "files": []}
        items.append(cur)
    for fu in re.finditer(r'href="([^"]*media/uploads[^"]+)"', p):
        url = fu.group(1)
        if cur is None:
            cur = {"title": "(без заголовка)", "files": []}
            items.append(cur)
        cur["files"].append(urllib.parse.unquote(url))
# дедуп файлов внутри пункта
for it in items:
    it["files"] = sorted(set(it["files"]))
print("пунктов:", len(items))
nfiles = sum(len(it["files"]) for it in items)
print("файлов:", nfiles)
from collections import Counter
exts = Counter(f.split(".")[-1].lower() for it in items for f in it["files"])
print("типы:", dict(exts))
json.dump(items, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
# архивы
for it in items:
    for f in it["files"]:
        if f.lower().endswith(("rar", "zip", "7z")):
            print("ARCHIVE:", f.split("/")[-1][:80], "|", it["title"][:70])
