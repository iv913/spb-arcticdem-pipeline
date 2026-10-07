#!/usr/bin/env python3
"""Recovery 5.83-prep: icesat2_clean.csv из icesat2_corrected.csv (канон 5.68-C).
Фильтр |dH_corrected| <= 3.0 м. Сверка с отчётом anomaly_recheck_5.68.md."""
import pandas as pd

SRC = 'scripts/data/icesat2_corrected.csv'
DST = 'scripts/data/icesat2_clean.csv'

ic = pd.read_csv(SRC)
clean = ic[ic.abs_dH_corrected <= 3.0].copy()
clean.to_csv(DST, index=False)
print(f'ICESat clean: {len(clean)} из {len(ic)} '
      f'(MAD {clean.abs_dH_corrected.median():.2f}, '
      f'med dH {clean.dH_corrected.median():+.3f}, '
      f'r {pd.Series(clean.h_icesat_baltic).corr(clean.h_model):.4f})')
