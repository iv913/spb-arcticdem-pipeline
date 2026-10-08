#!/usr/bin/env bash
# 5.84 полный конвейер: consensus -> GLO->TMP -> 7 зон реген -> мозаика.
# Запуск: setsid nohup bash pipeline_5.84.sh > /tmp/pipeline_5.84.log 2>&1 &
set -u
REPO=/home/z/my-project/repos/spb-arcticdem-pipeline
cd "$REPO"

echo "=== [1/4] consensus rebuild $(date -u +%T) ==="
# усечённые (не растущие) glo-файлы чистим
for f in scripts/data/consensus_dtm_5.71/glo_*.tif; do
  [ -f "$f" ] || continue
  sz1=$(stat -c%s "$f"); sleep 3; sz2=$(stat -c%s "$f") || true
  if [ "$sz1" = "$sz2" ] && [ "$sz1" -lt 40000000 ]; then
    # тайл COG 1x1 градус ~ >40MB не бывает ниже; но бывают и маленькие валидные?
    # эмпирика 5.71: glo тайлы 20-25MB валидны; проверяем gdalinfo
    if ! gdalinfo "$f" > /dev/null 2>&1; then
      echo "  удаляю усечённый $f ($sz1 B)"
      rm -f "$f"
    fi
  fi
done
python3 scripts/rebuild_consensus_5.83prep.py || { echo "CONSENSUS FAILED"; exit 1; }

echo "=== [2/4] GLO tiles -> TMP $(date -u +%T) ==="
mkdir -p scripts/dem_cache/_glo30_tmp
for f in scripts/data/consensus_dtm_5.71/glo_*.tif; do
  b=$(basename "$f"); cp -n "$f" "scripts/dem_cache/_glo30_tmp/${b#glo_}"
done
ls scripts/dem_cache/_glo30_tmp/ | head -8

echo "=== [3/4] регенерация 7 зон $(date -u +%T) ==="
bash scripts/regen_zones_5.84.sh || { echo "ZONES FAILED"; exit 1; }

echo "=== [4/4] мозаика $(date -u +%T) ==="
python3 scripts/mosaic_5.84.py || { echo "MOSAIC FAILED"; exit 1; }

echo "=== PIPELINE 5.84 DONE $(date -u +%FT%TZ) ==="
touch /tmp/pipeline_5.84.done
