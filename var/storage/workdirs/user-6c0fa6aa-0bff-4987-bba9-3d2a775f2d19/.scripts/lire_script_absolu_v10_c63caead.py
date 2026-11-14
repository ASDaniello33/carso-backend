from pathlib import Path

p = Path('.scripts/lecture_csv_absolu_v10_75c19feb.py')
print(p.read_text(encoding='utf-8', errors='replace'))
