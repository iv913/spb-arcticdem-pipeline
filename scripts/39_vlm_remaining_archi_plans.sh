#!/bin/bash
# Process all 64 unprocessed archi_plans PNG files (>=10KB) via VLM
# Saves results as .vlm.json next to each .png

set -e
DATA=/tmp/my-project/scripts/data/archi_plans
TO_PROCESS=/tmp/to_process.txt

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
TOTAL=$(wc -l < $TO_PROCESS)
i=0
SUCCESS=0
FAIL=0

while read -r name; do
  i=$((i+1))
  img="$DATA/$name.png"
  out="$DATA/$name.vlm.json"
  
  # Skip if already exists
  if [ -f "$out" ] && [ -s "$out" ]; then
    echo "[$i/$TOTAL] SKIP (exists): $name"
    continue
  fi
  
  # Skip if file too small (likely blank)
  sz=$(stat -c%s "$img" 2>/dev/null || echo 0)
  if [ "$sz" -lt 10000 ]; then
    echo "[$i/$TOTAL] SKIP (small ${sz}B): $name"
    # Write empty placeholder
    echo '{"choices":[{"message":{"content":"```json\n{\n  \"category\": \"blank\",\n  \"marks\": []\n}\n```"}}]}' > "$out"
    continue
  fi
  
  echo "[$i/$TOTAL] Processing: $name (${sz}B)"
  
  # Use z-ai CLI for vision
  if z-ai vision -p "$PROMPT" -i "$img" -o "$out" 2>&1 | tail -2; then
    if [ -f "$out" ] && [ -s "$out" ]; then
      SUCCESS=$((SUCCESS+1))
    else
      FAIL=$((FAIL+1))
      echo "  ⚠ No output file"
    fi
  else
    FAIL=$((FAIL+1))
    echo "  ⚠ FAILED"
  fi
  
  # Rate limit: 0.5s between calls
  sleep 0.5
done < $TO_PROCESS

echo ""
echo "=== Done ==="
echo "Total: $i"
echo "Success: $SUCCESS"
echo "Failed: $FAIL"
