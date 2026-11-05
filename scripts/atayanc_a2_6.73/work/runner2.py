import json, os, subprocess, time, random, sys

BASE = '/tmp/my-project/gh-clone/scripts/atayanc_a2_6.73'
BUDGET = float(sys.argv[1]) if len(sys.argv) > 1 else 520.0
PROMPT = """You are transcribing an old topographic plan (contact sheet cell).
Image 1: TOP = dark bar with white text - an ID like "m215 (2500,1800)". BOTTOM = plan fragment with a small RED CIRCLE marking a point.
Image 2: high zoom around that red circle (same cell).
A printed decimal number (elevation mark) is printed next to the red circle, usually touching it; typically 2 decimals, separator comma or dot.
Answer STRICTLY on 3 lines:
ID=<text of the top bar, exact>
VALUE=<the decimal number printed CLOSEST to the red circle; copy digit by digit, do not invent; if unclear append ?>
OTHERS=<all other printed decimal numbers visible in image 1 bottom, comma-separated, or ->
"""

def valid(out):
    if not os.path.exists(out): return False
    try:
        data = json.load(open(out))
        c = data.get('choices', [{}])[0].get('message', {}).get('content', '')
        return bool(c) and 'VALUE=' in c
    except Exception:
        return False

t0 = time.time()
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
print(f'pending: {len(todo)}; budget {BUDGET}s', flush=True)
wait = 12
for k, idx in enumerate(todo):
    out = f'{BASE}/work/vlm/{idx}.json'
    while True:
        if time.time() - t0 > BUDGET:
            print('TIME BUDGET REACHED', flush=True); sys.exit(0)
        r = subprocess.run(['z-ai','vision','-p',PROMPT,'-i',f'{BASE}/work/cells/{idx}_main.png','-i',f'{BASE}/work/cells/{idx}_tight.png','-o',out],
                           capture_output=True, text=True, timeout=300)
        if valid(out):
            content = json.load(open(out))['choices'][0]['message']['content']
            with open(resfile,'a') as f:
                f.write(json.dumps({'idx': idx, 'content': content})+'\n')
            print(f'[{k+1}/{len(todo)}] {idx} OK  ({time.time()-t0:.0f}s)', flush=True)
            wait = 12
            time.sleep(1.0)
            break
        errline = (r.stderr or '').strip().splitlines()
        err = errline[-1][:100] if errline else 'empty'
        print(f'[{k+1}/{len(todo)}] {idx} fail: {err}', flush=True)
        time.sleep(wait + random.uniform(0,4))
        wait = min(wait*1.5, 75)
print('ALL DONE IN BUDGET', flush=True)
