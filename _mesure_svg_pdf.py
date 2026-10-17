"""Sonde (temporaire) : géométrie du SVG inline dans le PDF.

La couleur de remplissage est unique (`#FF00FF`) : la boîte englobante des
pixels magenta donne la place réellement occupée par le schéma, sans être
polluée par l'en-tête et le pied de page du thème.
"""

from __future__ import annotations

import io

from PIL import Image

MM = 110 / 25.4  # dpi du rasteriseur
MAGENTA = (255, 0, 255)


def _svg(largeur: int, hauteur: int) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {largeur} {hauteur}">'
        "<title>Schéma</title>"
        f'<rect x="0" y="0" width="{largeur}" height="{hauteur}" fill="#FF00FF"/>'
        "</svg>"
    )


def mesurer(octets_pdf: bytes) -> tuple[float, float, int]:
    """Boîte du magenta en mm (page 1) et nombre de pages."""
    from app.documents.rasterize import rasteriser_pdf

    pages = rasteriser_pdf(octets_pdf, dpi=110, max_pages=10)
    image = Image.open(io.BytesIO(pages[0].octets)).convert("RGB")
    # Un pixel magenta (aucune autre teinte du thème ne s'en approche).
    masque = Image.new("L", image.size, 0)
    masque.putdata(
        [
            255 if r > 200 and g < 90 and b > 200 else 0
            for r, g, b in image.getdata()
        ]
    )
    boite = masque.getbbox()
    if boite is None:
        return 0.0, 0.0, len(pages)
    gauche, haut, droite, bas = boite
    return (droite - gauche) / MM, (bas - haut) / MM, len(pages)


VARIANTES = {
    "actuelle (max-width, height:auto)": "",
    "max-height 140mm": "figure svg { max-height: 140mm; }",
    "width:100% + max-height": "figure svg { width: 100%; height: auto; max-height: 140mm; }",
    "max-w + max-h + height:auto": (
        "figure svg { max-width: 100%; height: auto; max-height: 140mm; }"
    ),
}


def main() -> None:  # noqa: C901 - sonde linéaire
    from app.documents.professionnel import rendre_professionnel

    for nom, css in VARIANTES.items():
        print(f"=== {nom} ===")
        for etiquette, largeur, hauteur in (
            ("normal 400x160", 400, 160),
            ("large 1200x160", 1200, 160),
            ("haut 400x900", 400, 900),
            ("carre 600x600", 600, 600),
        ):
            rendu = rendre_professionnel(
                f"<h1>S</h1><figure>{_svg(largeur, hauteur)}</figure>",
                "S",
                format="pdf",
                css_supplementaire=css,
            )
            l_mm, h_mm, pages = mesurer(rendu.octets)
            print(
                f"     {etiquette:15s} : {l_mm:6.1f} x {h_mm:6.1f} mm "
                f"(attendu {largeur / 96 * 25.4:5.1f} x {hauteur / 96 * 25.4:5.1f}) "
                f"{pages} page(s)"
            )


if __name__ == "__main__":
    main()
