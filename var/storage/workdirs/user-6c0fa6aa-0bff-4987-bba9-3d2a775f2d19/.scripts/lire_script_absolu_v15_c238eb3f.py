from pathlib import Path

p = Path('.scripts/lecture_csv_absolu_v15_76612ecc.py')
print(p.read_text(encoding='utf-8', errors='replace'))
