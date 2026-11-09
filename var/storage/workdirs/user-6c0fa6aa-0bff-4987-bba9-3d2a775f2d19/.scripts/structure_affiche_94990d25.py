from pathlib import Path
from PIL import Image

cible = Path(r'C:\Users\Asus\Documents\CARSO_AI\backend\var\storage\carso\generated\8a92263f-b914-4933-9209-9e1dc93c54a3\895124117_1536882931801862_6566280990836471475_n.jpg')
im = Image.open(cible).convert('RGB')
w, h = im.size
px = im.load()

def lum(p):
    r, g, b = p
    return 0.299 * r + 0.587 * g + 0.114 * b

# profil par ligne : luminance moyenne + part de pixels tres clairs
print('--- PROFIL LIGNES (tous les 32 px) ---')
for y in range(0, h, 32):
    tot = 0
    clairs = 0
    n = 0
    for x in range(0, w, 8):
        p = px[x, y]
        tot += lum(p)
        if lum(p) > 200:
            clairs += 1
        n += 1
    print(y, 'lum', round(tot / n), 'clair%', round(100 * clairs / n))

print('--- PROFIL COLONNES (tous les 64 px) ---')
for x in range(0, w, 64):
    tot = 0
    clairs = 0
    n = 0
    for y in range(0, h, 8):
        p = px[x, y]
        tot += lum(p)
        if lum(p) > 200:
            clairs += 1
        n += 1
    print(x, 'lum', round(tot / n), 'clair%', round(100 * clairs / n))
