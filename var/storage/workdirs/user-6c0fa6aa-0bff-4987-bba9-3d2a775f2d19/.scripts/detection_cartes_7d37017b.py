from pathlib import Path
from PIL import Image

cible = Path(r'C:\Users\Asus\Documents\CARSO_AI\backend\var\storage\carso\generated\8a92263f-b914-4933-9209-9e1dc93c54a3\895124117_1536882931801862_6566280990836471475_n.jpg')
im = Image.open(cible).convert('RGB')
w, h = im.size
px = im.load()

def proche(p, ref, tol=26):
    return all(abs(p[i] - ref[i]) <= tol for i in range(3))

CARTE = (0x3B, 0x43, 0x82)
FOND = (0x27, 0x21, 0x61)
ORANGE = (0xFB, 0xAD, 0x41)

# masque carte, echantillonne
ech = 4
W, H = w // ech, h // ech
masque = [[False] * W for _ in range(H)]
for j in range(H):
    for i in range(W):
        masque[j][i] = proche(px[i * ech, j * ech], CARTE, 30)

# composantes connexes (4-connexite) sur le masque
vus = [[False] * W for _ in range(H)]
comps = []
for j in range(H):
    for i in range(W):
        if masque[j][i] and not vus[j][i]:
            pile = [(i, j)]
            vus[j][i] = True
            minx = maxx = i
            miny = maxy = j
            n = 0
            while pile:
                x, y = pile.pop()
                n += 1
                if x < minx: minx = x
                if x > maxx: maxx = x
                if y < miny: miny = y
                if y > maxy: maxy = y
                for dx, dy in ((1,0),(-1,0),(0,1),(0,-1)):
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < W and 0 <= ny < H and masque[ny][nx] and not vus[ny][nx]:
                        vus[ny][nx] = True
                        pile.append((nx, ny))
            if n > 400:
                comps.append((n, minx*ech, miny*ech, maxx*ech, maxy*ech))

comps.sort(key=lambda c: (c[2], c[1]))
print('--- BLOCS COULEUR CARTE (aire, x0,y0,x1,y1) ---')
for c in comps:
    n, x0, y0, x1, y1 = c
    print('aire', n, 'x', x0, '->', x1, 'y', y0, '->', y1, 'w', x1-x0, 'h', y1-y0)

# zones orange
print('--- ZONES ORANGE (echantillon) ---')
for j in range(0, H, 2):
    ligne = ''
    for i in range(0, W, 2):
        ligne += 'O' if proche(px[i*ech, j*ech], ORANGE, 40) else '.'
    if 'O' in ligne:
        print(j*ech, ligne)
