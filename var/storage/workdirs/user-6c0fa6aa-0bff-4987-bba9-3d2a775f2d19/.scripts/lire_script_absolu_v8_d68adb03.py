from pathlib import Path

p = Path('.scripts/lecture_csv_absolu_v8_ebe21a25.py')
print(p.read_text(encoding='utf-8', errors='replace'))
