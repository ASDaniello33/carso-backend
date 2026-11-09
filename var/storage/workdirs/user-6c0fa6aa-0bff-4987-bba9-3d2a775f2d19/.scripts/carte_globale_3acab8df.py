from pathlib import Path
from PIL import Image

cible = Path(r'C:\Users\Asus\Documents\CARSO_AI\backend\var\storage\carso\generated\8a92263f-b914-4933-9209-9e1dc93c54a3\895124117_1536882931801862_6566280990836471475_n.jpg')
im = Image.open(cible).convert('RGB')
w, h = im.size
px = im.load()

def lum(p):
    return 0.299*p[0] + 0.587*p[1] + 0.114*p[2]

def cat(p):
    r, g, b = p
    l = lum(p)
    if r > 200 and 120 < g < 215 and b < 130:
        return 'O'   # orange
    if l > 225:
        return 'W'   # blanc
    if l > 160:
        return 'w'   # clair
    if b > r + 20 and b > 80 and l < 120:
        return 'B'   # bleu
    if l < 55:
        return '#'   # tres sombre
    if r > g + 20 and r > b + 10:
        return 'p'   # magenta/peau
    return '.'

N = 48
print('     ' + ''.join(str(i % 10) for i in range(N)))
for j in range(N):
    y = int((j + 0.5) * h / N)
    ligne = ''
    for i in range(N):
        x = int((i + 0.5) * w / N)
        ligne += cat(px[x, y])
    print('%4d %s' % (int(j * h / N), ligne))
