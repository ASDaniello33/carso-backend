from pathlib import Path

base = Path(r'C:\Users\Asus\Documents\CARSO_AI\backend\var\storage\carso')
cible = base / 'generated' / '8a92263f-b914-4933-9209-9e1dc93c54a3' / '895124117_1536882931801862_6566280990836471475_n.jpg'
print('existe:', cible.exists(), cible)

if cible.exists():
    from PIL import Image
    im = Image.open(cible)
    print('format', im.format, 'mode', im.mode, 'size', im.size)
    im2 = im.convert('RGB')
    w, h = im2.size
    print('ratio L/H', round(w / h, 4))

    petit = im2.resize((160, max(1, int(160 * h / w))))
    couleurs = petit.getcolors(maxcolors=1000000)
    couleurs.sort(reverse=True)
    total = sum(n for n, _ in couleurs)
    print('--- TOP 20 COULEURS ---')
    for n, rgb in couleurs[:20]:
        print('#%02X%02X%02X' % rgb, round(100 * n / total, 2), '%')

    print('--- GRILLE 8x10 (moyenne) ---')
    gw, gh = 8, 10
    for gy in range(gh):
        ligne = []
        for gx in range(gw):
            box = (int(gx * w / gw), int(gy * h / gh), int((gx + 1) * w / gw), int((gy + 1) * h / gh))
            r, g, b = im2.crop(box).resize((1, 1)).getpixel((0, 0))
            ligne.append('#%02X%02X%02X' % (r, g, b))
        print(' | '.join(ligne))

    # luminosite par bande horizontale : detecte bandeaux
    print('--- LUMINOSITE PAR BANDE (20 bandes) ---')
    for i in range(20):
        box = (0, int(i * h / 20), w, int((i + 1) * h / 20))
        r, g, b = im2.crop(box).resize((1, 1)).getpixel((0, 0))
        lum = round(0.299 * r + 0.587 * g + 0.114 * b)
        print('bande', i, '#%02X%02X%02X' % (r, g, b), 'lum', lum)
