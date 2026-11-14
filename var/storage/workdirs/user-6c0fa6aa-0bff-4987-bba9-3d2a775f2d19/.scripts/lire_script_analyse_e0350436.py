from pathlib import Path

for nom in ['.scripts/analyse_detaillee_5c8fc254.py',
            '.scripts/explorer_csv_d1760743.py',
            '.scripts/explorer_c8085aca.py']:
    p = Path(nom)
    print("="*70)
    print(nom)
    print("="*70)
    if p.exists():
        print(p.read_text(encoding='utf-8', errors='replace'))
    else:
        print("introuvable")
