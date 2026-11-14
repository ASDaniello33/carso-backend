from pathlib import Path

p = Path('.scripts/lecture_csv_absolu_v17_1600b4dd.py')
print(p.read_text(encoding='utf-8', errors='replace'))
