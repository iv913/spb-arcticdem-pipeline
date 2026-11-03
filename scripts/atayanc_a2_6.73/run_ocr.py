import subprocess, json, sys, os, time, re
from concurrent.futures import ThreadPoolExecutor

def ocr(job):
    img, prompt, out = job
    for attempt in range(6):
        if os.path.exists(out):
            try:
                d=json.load(open(out)); return (img, d['choices'][0]['message']['content'])
            except Exception: pass
        r = subprocess.run(['z-ai','vision','-p',prompt,'-i',img,'-o',out],
                           capture_output=True, text=True, timeout=120)
        txt = r.stdout + r.stderr
        if '429' in txt or 'Too many requests' in txt:
            time.sleep(3 + attempt*4)
            continue
        try:
            d=json.load(open(out))
            return (img, d['choices'][0]['message']['content'])
        except Exception:
            time.sleep(2)
    return (img, 'ERR')

id_prompt = ("This is a magnified strip of text from a topographic plan contact sheet. "
             "Transcribe the text EXACTLY, character by character, including parentheses, commas and spaces. "
             "Output only the transcription.")

mode = sys.argv[1]
jobs=[]
if mode=='id':
    for s in range(10,15):
        for r in range(6):
            for c in range(4):
                jobs.append((f'tmp_crops/id_s{s}_r{r}c{c}.png', id_prompt, f'tmp_crops/id_s{s}_r{r}c{c}.json'))
else:
    map_prompt = ("Fragment of an old topographic plan. A small RED CIRCLE marks a point. "
                  "Decimal numbers (e.g. 2,14 or 1.06) printed on the plan are elevation labels. "
                  "Find the number printed CLOSEST to the red circle (usually touching the circle). "
                  "Transcribe it exactly, digit by digit, keeping its separator (comma or dot). Do not round. "
                  "If no number is readable near the circle, answer NA. "
                  "Then add '|' and one word about clarity: clear, pale, or cut. "
                  "Output only: NUMBER|clarity")
    for s in range(10,15):
        for r in range(6):
            for c in range(4):
                jobs.append((f'tmp_crops/map_s{s}_r{r}c{c}.png', map_prompt, f'tmp_crops/map_s{s}_r{r}c{c}.json'))

with ThreadPoolExecutor(max_workers=2) as ex:
    res = list(ex.map(ocr, jobs))

for img,txt in res:
    print(os.path.basename(img).replace('.png',''), '=>', txt, flush=True)
