#!/bin/bash
# Process remaining archi_plans PNG files via VLM with retry + backoff
set -e
DATA=/tmp/my-project/scripts/data/archi_plans
PROMPT='Ты инженер-геодезист. На изображении — страница архитектурного/инженерного проекта.
Извлеки ВСЕ отметки высот (абсолютные или относительные), которые видишь на странице:
- отметки рельефа (например "149.85", "Отм. 12.30")
- отметки чистового пола (±0.000, +3.150)
- отметки верха/нижа конструкций (парапет, окна, ограждения)
- отметки земли/дороги/тротуара

Не извлекай:
- размеры (в миллиметрах)
- номера осей/пикетов
- площади

Верни JSON строго в формате:
```json
{
  "category": "engineering_plan|architectural_plan|section|facade|photo|unknown",
  "marks": [
    {"value": "13.85", "x": 0.4, "y": 0.1, "context": "Отметка рельефа на углу участка"}
  ]
}
```
Где x,y — относительные координаты (0..1) от левого верхнего угла.
Если отметок нет — верни пустой массив marks:[].'

# Find files that still need processing (no .vlm.json or empty .vlm.json)
> /tmp/need_vlm.txt
for img in $DATA/*.png; do
  name=$(basename "$img" .png)
  out="$DATA/$name.vlm.json"
  sz=$(stat -c%s "$img")
  if [ "$sz" -lt 10000 ]; then
    # Skip small files (likely blank pages)
    if [ ! -f "$out" ] || [ ! -s "$out" ]; then
      echo '{"choices":[{"message":{"content":"```json\n{\n  \"category\": \"blank\",\n  \"marks\": []\n}\n```"}}]}' > "$out"
    fi
    continue
  fi
  # Skip if already exists with valid content
  if [ -f "$out" ] && [ -s "$out" ]; then
    continue
  fi
  echo "$name" >> /tmp/need_vlm.txt
done

TOTAL=$(wc -l < /tmp/need_vlm.txt)
echo "Files needing VLM processing: $TOTAL"
i=0
SUCCESS=0
FAIL=0

while read -r name; do
  i=$((i+1))
  img="$DATA/$name.png"
  out="$DATA/$name.vlm.json"
  sz=$(stat -c%s "$img")
  
  # Try with retry + exponential backoff
  for attempt in 1 2 3 4 5; do
    if z-ai vision -p "$PROMPT" -i "$img" -o "$out" 2>/dev/null; then
      if [ -f "$out" ] && [ -s "$out" ]; then
        # Validate JSON
        if python3 -c "import json; json.load(open('$out'))" 2>/dev/null; then
          SUCCESS=$((SUCCESS+1))
          echo "[$i/$TOTAL] ✓ $name (${sz}B) attempt=$attempt"
          break
        fi
      fi
    fi
    
    # Failed - clean up and backoff
    rm -f "$out"
    if [ $attempt -lt 5 ]; then
      sleep_time=$((attempt * 5))
      echo "[$i/$TOTAL] ⏳ $name attempt=$attempt, sleeping ${sleep_time}s..."
      sleep $sleep_time
    else
      FAIL=$((FAIL+1))
      echo "[$i/$TOTAL] ✗ $name FAILED after 5 attempts"
    fi
  done
  
  # Always wait between files to avoid rate limit
  sleep 2
done < /tmp/need_vlm.txt

echo ""
echo "=== Done ==="
echo "Total: $i"
echo "Success: $SUCCESS"
echo "Failed: $FAIL"
