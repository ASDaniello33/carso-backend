from pathlib import Path

for nom in ['.scripts/lecture_csv_relative_e9d492b8.py',
            '.scripts/exploration_csv_ced83bc3.py',
            '.scripts/analyse_mdg_rtfx_csv_3ba3fd74.py']:
    p = Path(nom)
    print("="*70)
    print(nom)
    print("="*70)
    if p.exists():
        print(p.read_text(encoding='utf-8', errors='replace'))
    else:
        print("introuvable")
