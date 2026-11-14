from pathlib import Path

for nom in ['.scripts/analyse_mdg_rtfx_csv_py_7eedd348.py',
            '.scripts/exploration_csv_6da97c45.py',
            '.scripts/exploration_csv_facb5371.py',
            '.scripts/localiser_piece_jointe_39e0ace6.py',
            '.scripts/chercher_csv_large_c3bc1893.py']:
    p = Path(nom)
    print("="*70)
    print(nom)
    print("="*70)
    if p.exists():
        print(p.read_text(encoding='utf-8', errors='replace'))
    else:
        print("introuvable")
