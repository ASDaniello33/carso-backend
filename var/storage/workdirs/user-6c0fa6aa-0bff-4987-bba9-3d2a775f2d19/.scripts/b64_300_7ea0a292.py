from pathlib import Path
import base64
b64 = base64.b64encode(Path("logo_300.jpg").read_bytes()).decode("ascii")
print("LONGUEUR:", len(b64))
print(b64)
