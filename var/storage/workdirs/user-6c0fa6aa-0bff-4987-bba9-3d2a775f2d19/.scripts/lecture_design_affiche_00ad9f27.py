from pathlib import Path

candidats = [
    Path('/storage/generated/8a92263f-b914-4933-9209-9e1dc93c54a3/895124117_1536882931801862_6566280990836471475_n.jpg'),
]

trouve = None
for c in candidats:
    try:
        existe = c.exists()
        print('chemin', c, 'exists=', existe)
        if existe:
            trouve = c
    except Exception as e:
        print('erreur acces', c, type(e).__name__, e)

if trouve is None:
    print('AUCUN FICHIER ACCESSIBLE')
else:
    try:
        from PIL import Image
        im = Image.open(trouve)
        print('format', im.format, 'mode', im.mode, 'size', im.size)
        im2 = im.convert('RGB')
        w, h = im2.size
        print('ratio', round(w / h, 4))
        # palette dominante
        petit = im2.resize((120, int(120 * h / w)))
        couleurs = petit.getcolors(maxcolors=1000000)
        couleurs.sort(reverse=True)
        total = sum(n for n, _ in couleurs)
        print('--- TOP COULEURS ---')
        for n, rgb in couleurs[:15]:
            print(rgb, '#%02X%02X%02X' % rgb, round(100 * n / total, 2), '%')
        # grille 6x8 : couleur moyenne par case
        print('--- GRILLE 6x8 (moyenne RGB) ---')
        gw, gh = 6, 8
        for gy in range(gh):
            ligne = []
            for gx in range(gw):
                box = (int(gx * w / gw), int(gy * h / gh), int((gx + 1) * w / gw), int((gy + 1) * h / gh))
                case = im2.crop(box).resize((1, 1))
                r, g, b = case.getpixel((0, 0))
                ligne.append('#%02X%02X%02X' % (r, g, b))
            print(' | '.join(ligne))
    except Exception as e:
        print('ERREUR LECTURE IMAGE', type(e).__name__, e)
