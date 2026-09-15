#!/bin/bash
# Download all SPb ArcticDEM Pipeline files from tmpfiles.org
# Usage: bash download_all.sh [output_dir]
# Files expire after ~1 hour from upload time

OUT="${1:-./spb_pipeline_files}"
mkdir -p "$OUT"
cd "$OUT"

# Function: download a file from tmpfiles.org
# Arg 1: ID, Arg 2: filename
dl() {
    local id="$1"
    local name="$2"
    echo "→ $name..."
    # Get the token URL from the page
    local token_url=$(curl -sL --max-time 30 "https://tmpfiles.org/$id/$name" | grep -oP 'href="[^"]*download[^"]*"' | head -1 | sed 's/href="//;s/"//')
    if [ -z "$token_url" ]; then
        echo "  ✗ Failed to get token URL"
        return 1
    fi
    curl -sL --max-time 600 -o "$name" "$token_url"
    if [ -s "$name" ]; then
        echo "  ✓ $(ls -lh $name | awk '{print $5}')"
    else
        echo "  ✗ Empty file"
    fi
}

# === 1. Скрипты ===
dl wowNmIOTRIPe 13_nevskaya_guba.py
dl wLwOmhOcRgqD lib_geo.py
dl wrw7mFOxRINq lib_ground.py
dl w7wqmSO6R9Eg lib_profile.py
dl wRwUmzOYRTpQ lib_vertical.py
dl wHw2mCOYI2Ac spb_check_geoshift.py
dl w0wqmOO5IxeF spb_check_geoshift_per_tile.py
dl w1wgmwOQIfl9 spb_create_bay_polygon.py
dl wQwAmROmIj6X spb_full_run_v2.py
dl wIw8meOEINZW spb_missing_17.py
dl wmwHmfO2I2cC spb_missing_17_no_tiff.py
dl wkwDmcOZIlnh spb_water_mask_analysis.py
dl w3wJmZOWILMP test_guba.py

# === 2. Результаты ===
dl wjwRmNOiI00B spb_qa_2m.json
dl wvwtmKOeI1ox nevskaya_guba_qa.json
dl wEwLmBOaIK3B spb_passport_validation_v4.csv
dl wGwtmpO0IiaS spb_passport_validation_v3.csv
dl wowHm2OiIBJV spb_geoshift_per_tile.json
dl w3wXm6OdIS5w spb_geoshift_stats.json
dl wJwpmaO5Iot6 spb_geoshift_check.csv
dl w5wXmyOeI3bj spb_missing_17_analysis.csv
dl wLwImJOlI6yY spb_water_mask_analysis.json
dl wewgmUOrR7Q6 README.md

# === 3. ZIP архивы ===
dl wvwbmDOkio9I spb_pipeline_scripts.zip
dl wfwwmtOAFFJQ nevskaya_guba.zip
dl w0wFmwOrFA7B osm_water.zip
dl wBwsmiOsFmjk osm_green.zip
dl wgw6mIOyFv3Z osm_buildings.zip

# === 4. Copernicus DEM 30m ===
dl wuwnmSOOIikC cop_n59_00_e029_00.tif
dl wbwEmbOfINmU cop_n59_00_e030_00.tif
dl wgw5mcOSICqw cop_n60_00_e029_00.tif
dl wKwhmrOVIfxf cop_n60_00_e030_00.tif
dl wAwKmSONIipx cop_spb_3413_30m.tif

# === 5. Pipeline checkpoint ===
dl wswom7OhFM1A spb_progress.json

# === 6. spb_ground_surface_2m.tif (32 parts) ===
echo ""
echo "=== Downloading TIF parts (32 files, will re-assemble) ==="
TIF_PARTS=(
    "wRwOmTOv2EWC:spb_tif_part_aa.bin"
    "w1wemXOc2gXG:spb_tif_part_ab.bin"
    "wewtmwOW2N1v:spb_tif_part_ac.bin"
    "wFwMmVOI21hP:spb_tif_part_ad.bin"
    "wHw4mXOg2e8g:spb_tif_part_ae.bin"
    "w0wAmgOCGSoa:spb_tif_part_af.bin"
    "wwwTmuOlGktl:spb_tif_part_ag.bin"
    "wdwOm9OyGAzg:spb_tif_part_ah.bin"
    "wxwqmwOyGySI:spb_tif_part_ai.bin"
    "wgwnmvO3G8Po:spb_tif_part_aj.bin"
    "wvwqmCOLBBgg:spb_tif_part_ak.bin"
    "w9w8mWOYBgtZ:spb_tif_part_al.bin"
    "w9wYmrOgBKih:spb_tif_part_am.bin"
    "w2wQmIOLBxsi:spb_tif_part_an.bin"
    "wPwOmaODB8m6:spb_tif_part_ao.bin"
    "wPw4mpOABgx4:spb_tif_part_ap.bin"
    "wuwPmEOwstee:spb_tif_part_aq.bin"
    "wJwrmLOWs7nA:spb_tif_part_ar.bin"
    "wCwlmUOrs2dY:spb_tif_part_as.bin"
    "wcwQmgOMsAye:spb_tif_part_at.bin"
    "wjwOmEOnsJ1o:spb_tif_part_au.bin"
    "wnwvmuOQs6Rr:spb_tif_part_av.bin"
    "wmwGmeOGsO2E:spb_tif_part_aw.bin"
    "wtwImOOch5eH:spb_tif_part_ax.bin"
    "w4w2mgOnhwU6:spb_tif_part_ay.bin"
    "wawumGOehaWf:spb_tif_part_az.bin"
    "wMwGmrOXhbvv:spb_tif_part_ba.bin"
    "w7wTmvODhSOO:spb_tif_part_bb.bin"
    "wPwYmuO0h1k5:spb_tif_part_bc.bin"
    "w0wimgOghwpV:spb_tif_part_bd.bin"
    "wZwBmTOXk0Kl:spb_tif_part_be.bin"
    "wJw5mBOdkzL6:spb_tif_part_bf.bin"
)

for entry in "${TIF_PARTS[@]}"; do
    id="${entry%%:*}"
    name="${entry##*:}"
    dl "$id" "$name"
done

# Re-assemble TIF
echo ""
echo "=== Re-assembling spb_ground_surface_2m.tif ==="
cat spb_tif_part_*.bin > spb_ground_surface_2m.tif
rm spb_tif_part_*.bin
echo "✓ Final size: $(ls -lh spb_ground_surface_2m.tif | awk '{print $5}')"

echo ""
echo "=== Done. All files in $OUT ==="
ls -lh "$OUT"
