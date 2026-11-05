import json, os, subprocess, time, random, sys

BASE = '/tmp/my-project/gh-clone/scripts/atayanc_a2_6.73'
PROMPT = """You are transcribing an old topographic plan (contact sheet cell).
Image 1: TOP = dark bar with white text - an ID like "m215 (2500,1800)". BOTTOM = plan fragment with a small RED CIRCLE marking a point.
Image 2: high zoom around that red circle (same cell).
A printed decimal number (elevation mark) is printed next to the red circle, usually touching it; typically 2 decimals, separator comma or dot.
Answer STRICTLY on 3 lines:
ID=<text of the top bar, exact>
VALUE=<the decimal number printed CLOSEST to the red circle; copy digit by digit, do not invent; if unclear append ?>
OTHERS=<all other printed decimal numbers visible in image 1 bottom, comma-separated, or ->
"""

def run_one(idx):
    out = f'{BASE}/work/vlm/{idx}.json'
    main = f'{BASE}/work/cells/{idx}_main.png'
    tight = f'{BASE}/work/cells/{idx}_tight.png'
    cmd = ['z-ai', 'vision', '-p', PROMPT, '-i', main, '-i', tight, '-o', out]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    ok = False
    if os.path.exists(out):
        try:
            data = json.load(open(out))
            content = data.get('choices', [{}])[0].get('message', {}).get('content', '')
            if content and 'VALUE=' in content:
                ok = True
        except Exception:
            ok = False
    return ok, r

def main():
    os.makedirs(f'{BASE}/work/vlm', exist_ok=True)
    meta = json.load(open(f'{BASE}/work/meta.json'))
    idxs = [m['idx'] for m in meta]
    done = set()
    resfile = f'{BASE}/work/results.jsonl'
    if os.path.exists(resfile):
        for line in open(resfile):
            try: done.add(json.loads(line)['idx'])
            except Exception: pass
    todo = [i for i in idxs if i not in done]
    print(f'todo: {len(todo)}', flush=True)
    backoffs = [15, 30, 45, 60, 90, 120, 120, 120, 180, 180, 240, 240, 300, 300]
    for k, idx in enumerate(todo):
        attempt = 0
        while True:
            ok, r = run_one(idx)
            if ok:
                data = json.load(open(f'{BASE}/work/vlm/{idx}.json'))
                content = data['choices'][0]['message']['content']
                with open(resfile, 'a') as f:
                    f.write(json.dumps({'idx': idx, 'content': content}) + '\n')
                print(f'[{k+1}/{len(todo)}] {idx} OK', flush=True)
                break
            else:
                wait = backoffs[min(attempt, len(backoffs)-1)] + random.uniform(0, 5)
                attempt += 1
                err = (r.stderr or '').strip().splitlines()[-1] if (r.stderr or '').strip() else 'unknown'
                print(f'[{k+1}/{len(todo)}] {idx} FAIL a{attempt}: {err[:120]} -> sleep {wait:.0f}', flush=True)
                time.sleep(wait)
                if attempt > 25:
                    print(f'{idx}: GIVING UP after 25 attempts', flush=True)
                    break
        time.sleep(1.5 + random.uniform(0, 2))

if __name__ == '__main__':
    main()
