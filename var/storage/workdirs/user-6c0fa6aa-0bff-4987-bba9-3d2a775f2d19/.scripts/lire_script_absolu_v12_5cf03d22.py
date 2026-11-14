from pathlib import Path

p = Path('.scripts/lecture_csv_absolu_v12_40f34251.py')
print(p.read_text(encoding='utf-8', errors='replace'))
