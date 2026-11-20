from pathlib import Path
import base64
from PIL import Image

src = Path(r"C:\Users\Asus\Documents\CARSO_AI\backend\var\storage\carso\workdir\6c0fa6aa-0bff-4987-bba9-3d2a775f2d19\27b68905-12bb-4175-b77e-fbf62dcbed8d\WhatsApp Image 2026-11-13 at 11.21.46.jpeg")

im = Image.open(src).convert("RGB")
print("ORIGINE:", im.size)

# le logo est carre ; on vise une definition suffisante pour une couverture A4
cible = im.resize((420, 420), Image.LANCZOS)
out = Path("logo_cover.jpg")
cible.save(out, "JPEG", quality=88, optimize=True)
print("REDUIT:", cible.size, out.stat().st_size, "octets")

b64 = base64.b64encode(out.read_bytes()).decode("ascii")
Path("logo_cover_b64.txt").write_text(b64, encoding="ascii")
print("TAILLE_B64:", len(b64))
