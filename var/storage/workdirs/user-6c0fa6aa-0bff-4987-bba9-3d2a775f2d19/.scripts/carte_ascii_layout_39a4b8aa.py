from pathlib import Path
from PIL import Image

cible = Path(r'C:\Users\Asus\Documents\CARSO_AI\backend\var\storage\carso\generated\8a92263f-b914-4933-9209-9e1dc93c54a3\895124117_1536882931801862_6566280990836471475_n.jpg')
im = Image.open(cible).convert('RGB')
w, h = im.size
px = im.load()

def classer(p):
    r, g, b = p
    lum = 0.299*r + 0.587*g + 0.114*b
    if r > 200 and 120 < g < 215 and b < 130:
        return 'O'   # orange
    if lum > 205:
        return 'W'   # blanc
    if lum > 150:
        return 'w'   # clair
    if b > r + 25 and b > 90 and lum < 110:
        return 'B'   # bleu carte
    if lum < 60:
        return '#'   # fond tres sombre
    if r > 90 and r > b and abs(r-g) > 12:
        return 's'   # peau
    return '.'

N = 64
print('    ' + ''.join(str((i//10) % 10) for i in range(N)))
print('    ' + ''.join(str(i % 10) for i in range(N)))
for j in range(N):
    y = int((j + 0.5) * h / N)
    ligne = ''
    for i in range(N):
        x = int((i + 0.5) * w / N)
        ligne += classer(px[x, y])
    print('%3d ' % int(j * h / N) + ligne)
