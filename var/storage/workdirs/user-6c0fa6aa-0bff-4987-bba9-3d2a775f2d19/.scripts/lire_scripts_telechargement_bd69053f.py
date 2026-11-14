from pathlib import Path

for nom in ['.scripts/telecharger_et_lire_04bf4be7.py',
            '.scripts/get_modele_from_storage_237e8c1f.py',
            '.scripts/download_modele_ff161ee6.py',
            '.scripts/explorer_espace_958e835b.py',
            '.scripts/explorer_espace_e952f134.py']:
    p = Path(nom)
    print("="*70)
    print(nom)
    print("="*70)
    if p.exists():
        print(p.read_text(encoding='utf-8', errors='replace'))
    else:
        print("introuvable")
