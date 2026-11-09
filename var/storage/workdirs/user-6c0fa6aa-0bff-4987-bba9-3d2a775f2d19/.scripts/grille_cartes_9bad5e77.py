from pathlib import Path
from PIL import Image

cible = Path(r'C:\Users\Asus\Documents\CARSO_AI\backend\var\storage\carso\generated\8a92263f-b914-4933-9209-9e1dc93c54a3\895124117_1536882931801862_6566280990836471475_n.jpg')
im = Image.open(cible).convert('RGB')
w, h = im.size
px = im.load()

def lum(p):
    return 0.299*p[0] + 0.587*p[1] + 0.114*p[2]

# region carte : x 212..2044, y 608..2044
X0, Y0, X1, Y1 = 212, 608, 2044, 2044
print('=== LUMINOSITE MOYENNE PAR LIGNE DANS LA CARTE (pas 8) ===')
prec = None
for y in range(Y0, Y1, 8):
    tot = 0; n = 0
    for x in range(X0, X1, 8):
        tot += lum(px[x, y]); n += 1
    m = tot / n
    if prec is not None and abs(m - prec) > 12:
        print('  transition y', y, 'lum', round(m), '(avant', round(prec), ')')
    prec = m

print('=== LUMINOSITE MOYENNE PAR COLONNE DANS LA CARTE (pas 8) ===')
prec = None
for x in range(X0, X1, 8):
    tot = 0; n = 0
    for y in range(Y0, Y1, 8):
        tot += lum(px[x, y]); n += 1
    m = tot / n
    if prec is not None and abs(m - prec) > 12:
        print('  transition x', x, 'lum', round(m), '(avant', round(prec), ')')
    prec = m

# detection des pastilles claires (avatars) : pixels tres clairs dans la carte
print('=== PASTILLES CLAIRES (avatars) ===')
ech = 6
W = (X1 - X0) // ech
H = (Y1 - Y0) // ech
masque = [[lum(px[X0 + i*ech, Y0 + j*ech]) > 150 for i in range(W)] for j in range(H)]
vus = [[False]*W for _ in range(H)]
comps = []
for j in range(H):
    for i in range(W):
        if masque[j][i] and not vus[j][i]:
            pile = [(i, j)]; vus[j][i] = True
            minx = maxx = i; miny = maxy = j; n = 0
            while pile:
                x, y = pile.pop(); n += 1
                minx = min(minx, x); maxx = max(maxx, x)
                miny = min(miny, y); maxy = max(maxy, y)
                for dx, dy in ((1,0),(-1,0),(0,1),(0,-1)):
                    nx, ny = x+dx, y+dy
                    if 0 <= nx < W and 0 <= ny < H and masque[ny][nx] and not vus[ny][nx]:
                        vus[ny][nx] = True; pile.append((nx, ny))
            if n > 150:
                comps.append((n, X0+minx*ech, Y0+miny*ech, X0+maxx*ech, Y0+maxy*ech))
comps.sort(key=lambda c: (c[2], c[1]))
for c in comps[:30]:
    n, x0, y0, x1, y1 = c
    print('clair aire', n, 'x', x0, '->', x1, 'y', y0, '->', y1, 'w', x1-x0, 'h', y1-y0)
print('nb', len(comps))
