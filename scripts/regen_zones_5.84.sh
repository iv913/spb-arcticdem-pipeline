#!/usr/bin/env bash
# 5.84-а: регенерация 7 якорных зон 5.76-5.79 замороженным рецептом 5.83
# (протокол 5.80-81: leaf-off STAC -> open-offsets -> blend -> H2 med31 clip1).
# Кэш клипов каждой зоны удаляется после успеха (дисковая гигиена).
set -u
REPO=/home/z/my-project/repos/spb-arcticdem-pipeline
cd "$REPO"

run_zone() {
  local TAG=$1 W=$2 S=$3 E=$4 N=$5
  local P="download/products_5.83/hybrid_2m_5.83_${TAG}.tif"
  if [ -f "$P" ]; then echo "[regen] skip $TAG (продукт есть)"; return 0; fi
  echo "[regen] === $TAG ($W $S $E $N) $(date -u +%H:%M:%S) ==="
  python3 scripts/batch_rollout_5.83.py zone "$W" "$S" "$E" "$N" "$TAG"
  local rc=$?
  if [ $rc -eq 0 ] && [ -f "$P" ]; then
    rm -rf "scripts/dem_cache/strips_${TAG}"
    echo "[regen] $TAG OK, кэш удалён"
  else
    echo "[regen] $TAG FAILED rc=$rc (кэш сохранён для resumable)"
    return $rc
  fi
}

run_zone PILOT 30.24 60.01 30.34 60.06   # пилот дюны (5.75/5.76)
run_zone SESTR 30.12 60.01 30.24 60.06   # Сестрорецк-запад (5.77/5.79)
run_zone RZHEV 30.36 60.00 30.46 60.06   # A Ржевка/Охта (5.79)
run_zone OZER  30.07 59.98 30.17 60.04   # B Озерки/Коломяги (5.79)
run_zone STREL 30.05 59.92 30.15 59.97   # Z1 Стрельна/Петергоф-зап (5.79)
run_zone AVTOV 30.21 59.96 30.31 60.01   # Z2 Автово/Дачное (5.79)
run_zone LIGO  30.21 59.90 30.31 59.95   # Z3 Лигово-юг (5.79)
echo "[regen] ВСЕ ЗОНЫ ЗАВЕРШЕНЫ $(date -u +%FT%TZ)"
