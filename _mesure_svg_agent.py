"""Sonde (temporaire) : comportement réel du moteur pro face au SVG de l'agent.

Cinq mesures :
1. taille de l'image obtenue dans le DOCX (le schéma ne doit pas être étiré) ;
2. assainissement : foreignObject, référence distante, on*, animation ;
3. cas **viewBox seul** (celui qui sortait distordu en 220 × 160) ;
4. SVG invalide → erreur explicite nommant la figure ;
5. PDF : le schéma est présent (vectoriel) et il tient dans la page.
"""

from __future__ import annotations

import io
import zipfile

from docx import Document as LireDocx

SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 160" width="400" '
    'height="160" role="img" aria-label="Organigramme du projet">'
    "<title>Organigramme du projet</title>"
    '<rect x="150" y="10" width="100" height="34" rx="6" fill="#1E3A5F"/>'
    '<text x="200" y="32" text-anchor="middle" font-family="Arial" font-size="11" '
    'fill="#ffffff">Direction</text>'
    '<line x1="200" y1="44" x2="200" y2="62" stroke="#333" stroke-width="1.5"/>'
    "</svg>"
)

# viewBox SEUL : aucune width/height — le cas mesuré distordu avant réparation.
SVG_VIEWBOX_SEUL = SVG.replace(' width="400" height="160"', "")

# SVG hostile : tout doit disparaître sauf le dessin.
SVG_HOSTILE = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100">'
    "<foreignObject><p>HTML embarqué</p></foreignObject>"
    '<image href="http://exemple.invalid/logo.png" width="10" height="10"/>'
    '<a xlink:href="javascript:alert(1)"><rect width="10" height="10"/></a>'
    '<rect onclick="alert(1)" width="20" height="20" fill="#333"/>'
    '<animate attributeName="x" from="0" to="10" dur="1s"/>'
    "</svg>"
)


def figure(svg: str) -> str:
    return f"<h1>Schéma</h1><figure>{svg}<figcaption>Figure 1 – Organisation</figcaption></figure>"


def mesurer_docx(octets: bytes) -> None:
    document = LireDocx(io.BytesIO(octets))
    shapes = document.inline_shapes
    print(f"     images : {len(shapes)}")
    for forme in shapes:
        largeur_mm, hauteur_mm = forme.width.mm, forme.height.mm
        print(
            f"     taille : {largeur_mm:.1f} x {hauteur_mm:.1f} mm "
            f"(ratio {largeur_mm / hauteur_mm:.2f})"
        )
    texte = "\n".join(p.text for p in document.paragraphs)
    print(f"     texte parasite : {'Direction' in texte or 'HTML embarqué' in texte}")
    print(f"     légende posée  : {'Figure 1 – Organisation' in texte}")
    with zipfile.ZipFile(io.BytesIO(octets)) as z:
        medias = [n for n in z.namelist() if "media" in n]
        print(f"     PNG embarqués  : {len(medias)}")


def main() -> None:  # noqa: C901 - sonde linéaire
    from app.documents.professionnel import rendre_professionnel, verifier
    from app.documents.professionnel.html_builder import preparer

    print("=== 1. DOCX avec width/height (attendu 105.8 x 42.3 mm) ===")
    rendu = rendre_professionnel(figure(SVG), "Schéma", format="docx")
    mesurer_docx(rendu.octets)
    print(f"     avertissements : {list(rendu.avertissements)}")

    print("=== 2. Assainissement du SVG hostile ===")
    corps = preparer(figure(SVG_HOSTILE)).corps
    for interdit in ("foreignObject", "foreignobject", "exemple.invalid", "onclick",
                     "javascript:", "<animate", "animate "):
        print(f"     {interdit!r} présent : {interdit in corps}")
    print(f"     le dessin survit (<rect) : {'<rect' in corps}")
    print(f"     constats verifier() : {verifier(figure(SVG_HOSTILE))}")

    print("=== 3. viewBox SEUL (avant reparation : 220 x 160 distordu) ===")
    rendu = rendre_professionnel(figure(SVG_VIEWBOX_SEUL), "Schéma", format="docx")
    mesurer_docx(rendu.octets)

    print("=== 4. SVG invalide -> erreur explicite ===")
    try:
        rendre_professionnel(
            figure('<svg viewBox="0 0 100 100"><title>Cassé</title><rect x=></svg>'),
            "Schéma",
            format="docx",
        )
        print("     AUCUNE ERREUR (mauvais signe)")
    except Exception as erreur:  # noqa: BLE001 - sonde
        print(f"     {type(erreur).__name__} : {str(erreur)[:160]}")

    print("=== 5. PDF : schema vectoriel present ===")
    rendu = rendre_professionnel(figure(SVG), "Schéma", format="pdf")
    print(f"     pages={rendu.pages} octets={len(rendu.octets)} "
          f"avertissements={list(rendu.avertissements)}")
    # Le PDF doit contenir le dessin (pas une image raster) : le texte du SVG
    # est dessiné par Chromium comme un vrai chemin de texte.
    print(f"     contient '/Image' : {b'/Image' in rendu.octets}")


if __name__ == "__main__":
    main()
