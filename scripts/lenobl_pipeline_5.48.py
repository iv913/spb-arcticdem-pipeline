#!/usr/bin/env python3
"""5.48: стриминговый конвейер arch.lenobl.ru — скачать → распаковать →
извлечь отметки из векторных PDF (с цветом: чёрные = существующий рельеф,
красные = проектные) → удалить.

Запуск: python3 scripts/lenobl_pipeline_5.48.py [N]  (N — сколько RAR за прогон)
Манифест: results/lenobl_manifest_5.48.json (дозаполнение, idempotent)
"""
import csv
import io
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request

import fitz  # PyMuPDF

REPO = "/home/z/my-project/spb45"
WORK = "/home/z/my-project/wr/lenobl"
MANIFEST = os.path.join(REPO, "results/lenobl_manifest_5.48.json")
MASTER = os.path.join(REPO, "results/lenobl_marks_5.48.csv")
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/124.0"}

# имена PDF с приоритетом
PRIO = re.compile(r"вертикальн|профил|планировк|схема|генплан|геоподоснов|топогр|чертеж", re.I)
NUM_RE = re.compile(r"^\d{1,3}[.,]\d{1,2}$")


def fetch(url, dst, min_mb_check=True):
    if os.path.exists(dst) and os.path.getsize(dst) > 10000:
        return True
    u = urllib.parse.quote(url, safe=":/")
    try:
        req = urllib.request.Request(u, headers=UA)
        with urllib.request.urlopen(req, timeout=600) as r, open(dst, "wb") as f:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
        return os.path.getsize(dst) > 10000
    except Exception as e:
        print(f"    download FAIL: {str(e)[:80]}", flush=True)
        return False


def unpack(rar_path, dest):
    """Многотомные RAR: передаём .part1.rar если есть, иначе сам архив."""
    os.makedirs(dest, exist_ok=True)
    target = rar_path
    m = re.match(r"^(.*\.part)1\.rar$", rar_path, re.I)
    if m:
        target = m.group(1) + "1.rar"
    r = subprocess.run(["/home/z/.local/bin/7zz", "x", "-y", f"-o{dest}", target],
                       capture_output=True, text=True, timeout=1200)
    return "Everything is Ok" in (r.stdout or "") or r.returncode == 0


def walk_files(root, exts):
    for dirpath, _, files in os.walk(root):
        for f in files:
            if f.lower().endswith(exts):
                yield os.path.join(dirpath, f)


def extract_pdf(path):
    """Word-level: value, x, y, color. Возвращает список словарей."""
    out = []
    try:
        doc = fitz.open(path)
    except Exception:
        return out
    for pno in range(len(doc)):
        page = doc[pno]
        try:
            raw = page.get_text("dict")
        except Exception:
            continue
        W, H = page.rect.width, page.rect.height
        if W < 100 or H < 100:
            continue
        for block in raw.get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    t = span["text"].strip()
                    if not NUM_RE.match(t):
                        continue
                    try:
                        v = float(t.replace(",", "."))
                    except ValueError:
                        continue
                    if not (0.3 <= v <= 250):
                        continue
                    col = span.get("color", 0)
                    r = (col >> 16) & 255
                    g = (col >> 8) & 255
                    b = col & 255
                    kind = "black"
                    if r > 130 and g < 110 and b < 110:
                        kind = "red"
                    elif r > 100 and b > 100 and g < 110:
                        kind = "magenta"
                    elif g > 130 and r < 110 and b < 110:
                        kind = "green"
                    elif b > 130 and r < 110 and g < 110:
                        kind = "blue"
                    x0, y0, x1, y1 = span["bbox"]
                    out.append({"pdf": os.path.basename(path), "page": pno + 1,
                                "val": v, "x": round((x0 + x1) / 2 / W, 4),
                                "y": round((y0 + y1) / 2 / H, 4),
                                "kind": kind, "size": round(span["size"], 1)})
    doc.close()
    return out


def extract_dwg_strings(path):
    """Строки DWG (latin1+utf16) — грубые значения (как в 5.41-5.43)."""
    out = set()
    try:
        data = open(path, "rb").read()
    except Exception:
        return []
    for pat in (b"[\x20-\x7e]{4,}", ):
        for m in re.finditer(pat, data):
            s = m.group(0).decode("latin1")
            if NUM_RE.match(s.strip()):
                try:
                    v = float(s.strip().replace(",", "."))
                except ValueError:
                    continue
                if 0.5 <= v <= 200:
                    out.add(v)
    return sorted(out)


def extract_docx(path):
    """Отметки из таблиц/текста docx (пояснительные записки)."""
    import docx as _d
    out = []
    try:
        d = _d.Document(path)
    except Exception:
        return out
    nums = []
    for para in d.paragraphs:
        for tok in re.findall(r"\b\d{1,3}[.,]\d{1,2}\b", para.text):
            nums.append(float(tok.replace(",", ".")))
    for tbl in d.tables:
        for row in tbl.rows:
            for cell in row.cells:
                for tok in re.findall(r"\b\d{1,3}[.,]\d{1,2}\b", cell.text):
                    nums.append(float(tok.replace(",", ".")))
    return sorted({v for v in nums if 0.3 <= v <= 250})


def process_group(g, idx):
    os.makedirs(WORK, exist_ok=True)
    rid = re.search(r"№\s*(\d+-р)", g["title"])
    rid = rid.group(1) if rid else f"idx{idx}"
    local = []
    ok = True
    for u in g["files"]:
        dst = os.path.join(WORK, u.split("/")[-1])
        if not fetch(u, dst):
            ok = False
            break
        local.append(dst)
    if not ok:
        for p in local:
            try:
                os.remove(p)
            except OSError:
                pass
        return {"id": rid, "status": "download_fail", "title": g["title"][:160]}
    dest = os.path.join(WORK, "unpacked_" + rid)
    target = None
    for p in local:
        if re.search(r"\.part0*1\.rar$", p, re.I):
            target = p
            break
    if target is None:
        target = next((p for p in local
                       if os.path.basename(p).lower() == g["base"]), local[0])
    ok = unpack(target, dest)
    if not ok:
        for p in local:
            try:
                os.remove(p)
            except OSError:
                pass
        return {"id": rid, "status": "unpack_fail", "title": g["title"][:160]}
    pdfs = sorted(walk_files(dest, (".pdf",)))
    text_pdfs = []
    marks = []
    for p in pdfs:
        got = extract_pdf(p)
        if got:
            text_pdfs.append(os.path.basename(p))
            marks.extend(got)
    dwg_vals = []
    for p in walk_files(dest, (".dwg",)):
        dwg_vals.extend(extract_dwg_strings(p))
    docx_vals = []
    for p in walk_files(dest, (".docx",)):
        docx_vals.extend(extract_docx(p))
    docx_vals = sorted(set(docx_vals))
    n_bl = sum(1 for m in marks if m["kind"] == "black")
    n_rd = sum(1 for m in marks if m["kind"] == "red")
    rec = {"id": rid, "status": "ok", "n_pdfs": len(pdfs),
           "n_text_pdfs": len(text_pdfs), "text_pdfs": [os.path.basename(p) for p in text_pdfs][:8],
           "marks_total": len(marks), "marks_black": n_bl, "marks_red": n_rd,
           "dwg_values": len(dwg_vals), "docx_values": len(docx_vals),
           "title": g["title"][:200],
           "rars_mb": round(sum(os.path.getsize(p) for p in local) / 1e6, 1)}
    with open(MASTER, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["rid", "pdf", "page", "val", "x", "y",
                                          "kind", "size"])
        if f.tell() == 0:
            w.writeheader()
        for m in marks:
            m2 = dict(m); m2["rid"] = rid
            w.writerow(m2)
    subprocess.run(["rm", "-rf", dest], timeout=180)
    for p in local:
        try:
            os.remove(p)
        except OSError:
            pass
    return rec


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    groups = json.load(open(f"{REPO}/results/lenobl_groups_all_5.48.json"))
    manifest = json.load(open(MANIFEST)) if os.path.exists(MANIFEST) else {}
    done_pairs = {m.get("group_base", "") for m in manifest.values()
                  if m.get("status") == "ok"}
    todo = [g for g in groups if g["mb"] > 0 and
            (g["base"] + "|" + g["title"][:60]) not in done_pairs]
    print(f"к обработке: {len(todo)} из {len(groups)}", flush=True)
    for k, g in enumerate(todo[:n], 1):
        print(f"[{k}] {g['mb']:.1f} MB {g['title'][:70]}", flush=True)
        t0 = time.time()
        try:
            rec = process_group(g, k)
        except Exception as e:
            rec = {"id": "?", "status": f"error: {str(e)[:80]}",
                   "title": g["title"][:160]}
        rec["group_base"] = g["base"] + "|" + g["title"][:60]
        rec["sec"] = round(time.time() - t0)
        manifest[g["base"] + "|" + str(k)] = rec
        json.dump(manifest, open(MANIFEST, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print(f"    -> {rec['status']} textPDF={rec.get('n_text_pdfs', 0)} "
              f"black={rec.get('marks_black', 0)} red={rec.get('marks_red', 0)} "
              f"dwg={rec.get('dwg_values', 0)} docx={rec.get('docx_values', 0)} "
              f"({rec['sec']}с)", flush=True)
    print("manifest ->", MANIFEST)


if __name__ == "__main__":
    main()
