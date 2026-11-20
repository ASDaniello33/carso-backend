from pathlib import Path
import base64

racine = Path(r"C:\Users\Asus\Documents\CARSO_AI\backend\var\storage")
cible = "27b68905-12bb-4175-b77e-fbf62dcbed8d"

trouves = []
try:
    for p in racine.rglob("*"):
        if p.is_file() and cible in str(p):
            trouves.append(p)
except Exception as e:
    print("ERREUR parcours:", type(e).__name__, e)

for p in trouves:
    print("TROUVE:", p, p.stat().st_size)

if trouves:
    f = trouves[0]
    data = f.read_bytes()
    b64 = base64.b64encode(data).decode("ascii")
    print("MIME_DEVINE:", f.suffix)
    print("TAILLE_B64:", len(b64))
    Path("logo_b64.txt").write_text(b64, encoding="ascii")
    print("ECRIT logo_b64.txt")
