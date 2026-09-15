#!/bin/bash
# Re-assemble spb_ground_surface_2m.tif from 32 parts
cat spb_tif_part_*.bin > spb_ground_surface_2m.tif
echo "✓ Final size: $(ls -lh spb_ground_surface_2m.tif | awk '{print $5}')"
