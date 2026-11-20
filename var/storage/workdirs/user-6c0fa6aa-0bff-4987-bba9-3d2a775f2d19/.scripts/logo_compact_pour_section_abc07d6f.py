from pathlib import Path
import base64
from PIL import Image

src = Path(r"C:\Users\Asus\Documents\CARSO_AI\backend\var\storage\carso\workdir\6c0fa6aa-0bff-4987-bba9-3d2a775f2d19\27b68905-12bb-4175-b77e-fbf62dcbed8d\WhatsApp Image 2026-11-13 at 11.21.46.jpeg")
im = Image.open(src).convert("RGB")

for taille, q in [(300, 82), (260, 80), (220, 78)]:
    r = im.resize((taille, taille), Image.LANCZOS)
    p = Path(f"logo_{taille}.jpg")
    r.save(p, "JPEG", quality=q, optimize=True)
    b64 = base64.b64encode(p.read_bytes()).decode("ascii")
    print(taille, q, p.stat().st_size, "octets ->", len(b64), "caracteres b64")
