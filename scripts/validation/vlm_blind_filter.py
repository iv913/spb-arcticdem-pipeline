#!/usr/bin/env python3
"""VLM BLIND filter — shows ONLY satellite to VLM (no model surface, no dH).

Per AGENT_HANDOFF §1.1 and B4:
  "VLM (если нужен) применяется ко всем точкам, вслепую:
   в запрос не попадают ни dH, ни высота модели; в отчёте обе версии."

Unlike the previous VLM filter (which showed model+satellite side-by-side),
this script shows ONLY the Esri satellite image. VLM classifies what's at the
point without seeing the model. Both versions (before and after filter) are
reported.

Usage:
    python3 scripts/validation/vlm_blind_filter.py <pool_v9.csv> [--out <results.json>]
"""
import csv, json, math, os, subprocess, sys, time, urllib.request, io
from datetime import datetime, timezone
from pathlib import Path
from PIL import Image

DATA = Path("/home/z/my-project/upload/deliverables/data")
OUT_DIR = Path("/home/z/my-project/download/vlm_blind_images")

def fetch_esri(lat, lon, out_path, zoom=18, half_m=100):
    """Fetch Esri satellite tile centered on (lat, lon)."""
    def ll2t(lat, lon, z):
        n = 2**z
        xt = (lon + 180.0) / 360.0 * n
        lr = math.radians(lat)
        yt = (1.0 - math.asinh(math.tan(lr)) / math.pi) / 2.0 * n
        return xt, yt
    M_PER_DEG_LAT = 111320.0
    M_PER_DEG_LON = 111320.0 * math.cos(math.radians(lat))
    dlat = half_m / M_PER_DEG_LAT
    dlon = half_m / M_PER_DEG_LON
    lat_min, lat_max = lat - dlat, lat + dlat
    lon_min, lon_max = lon - dlon, lon + dlon
    x_min_f, y_max_f = ll2t(lat_min, lon_min, zoom)
    x_max_f, y_min_f = ll2t(lat_max, lon_max, zoom)
    x_min = int(math.floor(min(x_min_f, x_max_f)))
    x_max = int(math.ceil(max(x_min_f, x_max_f)))
    y_min = int(math.floor(min(y_min_f, y_max_f)))
    y_max = int(math.ceil(max(y_min_f, y_max_f)))
    if x_max <= x_min: x_max = x_min + 1
    if y_max <= y_min: y_max = y_min + 1
    try:
        if x_max - x_min == 1 and y_max - y_min == 1:
            url = f"https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{zoom}/{y_min}/{x_min}"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=15) as r:
                with open(out_path, 'wb') as f: f.write(r.read())
            return True
        # Multi-tile composite
        tiles = []
        for y in range(y_min, y_max):
            row = []
            for x in range(x_min, x_max):
                url = f"https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{zoom}/{y}/{x}"
                try:
                    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                    with urllib.request.urlopen(req, timeout=15) as r:
                        img = Image.open(io.BytesIO(r.read()))
                        row.append(img)
                except: row.append(None)
            tiles.append(row)
        if not tiles or not tiles[0] or tiles[0][0] is None: return False
        w, h = tiles[0][0].size
        comp = Image.new('RGB', (w * (x_max - x_min), h * (y_max - y_min)), (10, 20, 40))
        for iy, row in enumerate(tiles):
            for ix, t in enumerate(row):
                if t: comp.paste(t, (ix * w, iy * h))
        comp.save(out_path)
        return True
    except: return False

def vlm_classify_blind(img_path):
    """Send ONLY satellite to VLM. No model surface, no dH, no height info."""
    prompt = (
        "What is at the CENTER of this satellite image? "
        "Reply with ONE word: water / building / tree / road / empty / bridge / other. "
        "Do not describe anything else."
    )
    out_json = '/tmp/_vlm_blind.json'
    cmd = ['z-ai', 'vision', '-p', prompt, '-i', str(img_path), '-o', out_json]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=60, text=True)
        if r.returncode != 0 and '429' in (r.stderr or ''):
            print(f"    Rate limit, waiting 60s")
            time.sleep(60)
            r = subprocess.run(cmd, capture_output=True, timeout=60, text=True)
        if not os.path.exists(out_json): return None, 'no_response'
        d = json.load(open(out_json))
        return d['choices'][0]['message']['content'].strip().lower(), None
    except Exception as e:
        return None, str(e)

def classify_blind(content):
    """Classify VLM response into filter flag."""
    if not content: return 'INCONCLUSIVE', 'no response'
    c = content.lower()
    if any(w in c for w in ['water', 'canal', 'river', 'lake', 'sea']): return 'WATER', 'water body'
    if any(w in c for w in ['tree', 'vegetation', 'forest', 'park']): return 'TREE', 'tree canopy'
    if any(w in c for w in ['building', 'roof', 'structure']): return 'BUILDING', 'building'
    if 'bridge' in c: return 'BRIDGE', 'bridge'
    if any(w in c for w in ['road', 'asphalt', 'empty', 'parking', 'open']): return 'OPEN', 'open ground'
    return 'OTHER', f'other: {content[:40]}'

def main():
    pool_csv = sys.argv[1] if len(sys.argv) > 1 else str(DATA / 'validation_pool_v9.csv')
    out_json = sys.argv[2] if len(sys.argv) > 2 else str(DATA / 'vlm_blind_results.json')
    
    rows = list(csv.DictReader(open(pool_csv)))
    ground = [r for r in rows if r['type'] == 'ground']
    print(f"Pool: {len(ground)} ground marks")
    
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    state = {'processed': {}, 'started_at': datetime.now(timezone.utc).isoformat()}
    if os.path.exists(out_json):
        state = json.load(open(out_json))
    
    for i, r in enumerate(ground):
        pid = f"blind_{r['source']}_{float(r['lat']):.5f}_{float(r['lon']):.5f}"
        if pid in state.get('processed', {}): continue
        
        lat, lon = float(r['lat']), float(r['lon'])
        print(f"[{i+1}/{len(ground)}] {pid}")
        
        # Fetch ONLY satellite (no model!)
        sat_path = OUT_DIR / f"sat_{i+1:04d}.png"
        ok = fetch_esri(lat, lon, sat_path)
        if not ok:
            print(f"  SKIP: sat fetch failed")
            state['processed'][pid] = {'lat': lat, 'lon': lon, 'vlm_flag': 'SKIP_NO_SAT', 'vlm_response': ''}
            json.dump(state, open(out_json, 'w'), indent=2, ensure_ascii=False)
            continue
        
        # VLM classify — BLIND (no model, no dH)
        content, err = vlm_classify_blind(sat_path)
        if err:
            flag = 'INCONCLUSIVE'
            reason = f'VLM err: {err}'
        else:
            flag, reason = classify_blind(content)
        
        print(f"  VLM: {content} → {flag}")
        
        state['processed'][pid] = {
            'lat': lat, 'lon': lon, 'source': r['source'],
            'h_survey': float(r['h_survey']), 'dH': float(r['dH']),
            'vlm_flag': flag, 'vlm_response': content or '',
            'reason': reason,
            'processed_at': datetime.now(timezone.utc).isoformat(),
        }
        json.dump(state, open(out_json, 'w'), indent=2, ensure_ascii=False)
        try: sat_path.unlink()  # save disk
        except: pass
    
    # Summary
    from collections import Counter
    flags = Counter(p.get('vlm_flag', '?') for p in state['processed'].values())
    print(f"\n=== VLM Blind Filter Summary ===")
    print(f"Total: {len(state['processed'])}")
    for f, c in sorted(flags.items(), key=lambda x: -x[1]):
        print(f"  {f}: {c}")

if __name__ == '__main__':
    main()
