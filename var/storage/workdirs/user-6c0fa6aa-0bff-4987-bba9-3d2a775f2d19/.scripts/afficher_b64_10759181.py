from pathlib import Path
b64 = Path("logo_cover_b64.txt").read_text(encoding="ascii")
print("LONGUEUR:", len(b64))
print(b64)
