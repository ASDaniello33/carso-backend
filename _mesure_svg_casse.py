"""Sonde (temporaire) : ce que le nettoyage fait au SVG, et si resvg le digère.

Question posée : `preparer()` (BeautifulSoup html.parser) met-il `viewBox` en
minuscules ? Et si oui, resvg-py accepte-t-il encore le SVG (XML strict) ?
"""

from __future__ import annotations

import io

from PIL import Image

SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 160" width="400" '
    'height="160" role="img" aria-label="Organigramme">'
    "<title>Organigramme</title>"
    '<rect x="10" y="10" width="100" height="30" rx="6" fill="#1E3A5F"/>'
    '<text x="60" y="30" text-anchor="middle" font-size="11" fill="#fff">Direction</text>'
    '<line x1="60" y1="40" x2="60" y2="80" stroke="#333" stroke-width="1.5"/>'
    "</svg>"
)

# Le même, sans width/height : seul le viewBox donne la taille (cas d'un vrai
# schéma écrit à la main).
SVG_VIEWBOX_SEUL = SVG.replace(' width="400" height="160"', "")

HTML = f"<h1>T</h1><figure>{SVG}</figure><figure>{SVG_VIEWBOX_SEUL}</figure>"


def main() -> None:
    from app.documents.professionnel.html_builder import preparer, _racine
    from app.documents.professionnel.rasterisation import rasteriser_svg, taille_svg
    from bs4 import BeautifulSoup

    doc = preparer(HTML)
    print("--- corps nettoyé (premiers 300 car.) ---")
    print(doc.corps[:300].replace("><", ">\n<"))
    print("--- viewBox conservé (casse exacte) :", "viewBox" in doc.corps, "|", "viewbox" in doc.corps)
    print("--- viewbox minuscule présent      :", "viewbox" in doc.corps)
    print("--- taille_svg sur le corps nettoyé:", taille_svg(doc.corps))

    soup = BeautifulSoup(doc.corps, "html.parser")
    for i, svg in enumerate(soup.find_all("svg"), 1):
        texte = str(svg)
        print(f"--- SVG {i} : rasterisation resvg ---")
        try:
            png = rasteriser_svg(texte)
            image = Image.open(io.BytesIO(png))
            print(f"    OK — PNG {image.size}")
        except Exception as erreur:  # noqa: BLE001 - sonde
            print(f"    ÉCHEC — {type(erreur).__name__}: {erreur}")


if __name__ == "__main__":
    main()
