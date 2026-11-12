"""Production et contrôle d'images.

Handlers qui génèrent une image bitmap ou SVG, composent une figure et
contrôlent un SVG avant dépôt. Les helpers de diagnostic restent ici :
ils ne servent qu'à ces handlers.

Extrait de ``build_document_tools`` sans modification de contenu.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.core.errors import ValidationError


def _generer_image(ctx: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Dépose une image **réelle** fournie en base64 (INC-135)."""
    ctx.policy.require(ctx.identite.agent_id, "document_propose")
    from app.documents.image_generee import depuis_base64

    etiquette = _designation_image(payload)
    image = depuis_base64(payload["base64"], designation=etiquette)
    extension = f".{image.extension}"
    depot = ctx.deposer(
        image.octets,
        {
            **payload,
            "nom_fichier": _nom_image(ctx, payload["nom_fichier"], extension),
        },
        extension,
    )
    return _sortie_image(ctx, depot, image, etiquette=etiquette)


def _generer_image_svg(ctx: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Rasterise un SVG d'agent en PNG, le contrôle et le dépose (INC-135/136)."""
    ctx.policy.require(ctx.identite.agent_id, "document_propose")
    from app.documents.image_generee import depuis_svg

    etiquette = _designation_image(payload)
    image = depuis_svg(payload["svg"], designation=etiquette)
    diagnostics = _diagnostics_svg(ctx, image)
    extension = f".{image.extension}"
    depot = ctx.deposer(
        image.octets,
        {
            **payload,
            "nom_fichier": _nom_image(ctx, payload["nom_fichier"], extension),
        },
        extension,
    )
    sortie = _sortie_image(ctx, depot, image, etiquette=etiquette)
    sortie["diagnostics"] = diagnostics
    sortie["pret_a_afficher"] = not _bloquants(ctx, diagnostics)
    if not sortie["pret_a_afficher"]:
        sortie["message"] = (
            f"{len(_bloquants(ctx, diagnostics))} défaut(s) de design bloquant(s) : "
            "corriger le SVG, le recontrôler avec verifier_image_svg, puis "
            "régénérer une version — et seulement ensuite l'afficher. "
            + str(sortie.get("message", ""))
        )
    return sortie


def _composer_image(ctx: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Compose, contrôle et dépose une image depuis un contenu (INC-136)."""
    ctx.policy.require(ctx.identite.agent_id, "document_propose")
    from app.documents.composition_image import CompositionRefusee, composer_svg
    from app.documents.image_generee import depuis_svg

    etiquette = _designation_image(payload)
    try:
        svg = composer_svg(
            titre=payload["titre"],
            sous_titre=payload.get("sous_titre"),
            surtitre=payload.get("surtitre"),
            points=payload.get("points"),
            pied=payload.get("pied"),
            gabarit_nom=payload.get("gabarit"),
            palette_nom=payload.get("palette"),
        )
    except CompositionRefusee as erreur:
        # Erreur de contenu, pas panne : le modèle corrige son appel au tour
        # suivant (même contrat que les autres refus de génération).
        raise ValidationError(str(erreur)) from erreur
    # Échelle 1 : la composition est déjà en pixels finaux (le × 2 de la
    # rasterisation la ferait réduire ensuite, pour rien).
    image = depuis_svg(svg, designation=etiquette, echelle=1.0)
    diagnostics = _diagnostics_svg(ctx, image)
    extension = f".{image.extension}"
    depot = ctx.deposer(
        image.octets,
        {
            **payload,
            "nom_fichier": _nom_image(ctx, payload["nom_fichier"], extension),
        },
        extension,
    )
    sortie = _sortie_image(ctx, depot, image, etiquette=etiquette)
    sortie["gabarit"] = payload.get("gabarit") or "carte"
    sortie["palette"] = payload.get("palette") or "carso"
    sortie["diagnostics"] = diagnostics
    return sortie


def _verifier_image_svg(ctx: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Contrôle un SVG **sans le déposer** : la boucle de correction (INC-136)."""
    ctx.policy.require(ctx.identite.agent_id, "document_propose")
    from app.documents.image_generee import depuis_svg

    image = depuis_svg(payload["svg"], designation="Image")
    diagnostics = _diagnostics_svg(ctx, image)
    bloquants = _bloquants(ctx, diagnostics)
    sortie: dict[str, Any] = {
        "largeur_px": image.largeur,
        "hauteur_px": image.hauteur,
        "octets": image.octets_finis,
        "diagnostics": diagnostics,
        "pret_a_deposer": not bloquants,
    }
    if bloquants:
        sortie["message"] = (
            f"{len(bloquants)} défaut(s) bloquant(s) : corriger le SVG puis "
            "recontrôler. Rien n'a été déposé."
        )
    elif diagnostics:
        sortie["message"] = (
            "Aucun défaut bloquant. Les avertissements restants sont à "
            "corriger si possible. Rien n'a été déposé : appeler "
            "generer_image_svg quand le rendu convient."
        )
    else:
        sortie["message"] = (
            "Aucun défaut mesurable : le SVG peut être déposé avec "
            "generer_image_svg, puis affiché."
        )
    return sortie


def _nom_image(ctx: Any, nom: str, extension: str) -> str:
    """Nom du fichier image : l'extension **réelle** remplace celle annoncée.

    Un ``logo.svg`` fourni à ``generer_image_svg`` devient ``logo.png`` — le
    dépôt n'écrit jamais un PNG sous un nom qui promet du vectoriel.
    """
    tige = Path(str(nom).strip())
    if tige.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}:
        tige = tige.with_suffix("")
    return f"{tige}{extension}"


def _sortie_image(ctx: Any, 
    sortie: dict[str, Any], image: Any, *, etiquette: str = "Image"
) -> dict[str, Any]:
    """Retour commun des tools d'image : fichier réel, taille, avertissements.

    Le message dit **où cliquer ensuite** : une image déposée mais jamais
    affichée serait un fichier que l'utilisateur ne voit pas.
    """
    sortie.update(
        {
            "format": image.extension,
            "largeur_px": image.largeur,
            "hauteur_px": image.hauteur,
            "octets": image.octets_finis,
            "avertissements": list(image.avertissements),
        }
    )
    texte = (
        f"{etiquette} déposée en statut '{sortie.get('statut')}' (validation "
        f"humaine requise) : {image.largeur}×{image.hauteur} px, format "
        f"{image.extension}, {image.octets_finis} octets."
    )
    if sortie.get("non_classe"):
        texte += " Aucune ancre : rangée dans le dossier du compte."
    texte += (
        " Pour que l'utilisateur la voie et la télécharge, appeler "
        "afficher_image avec ce document_id."
    )
    sortie["message"] = texte
    return sortie


def _diagnostics_svg(ctx: Any, image: Any) -> list[dict[str, str]]:
    """Contrôle de design du SVG rendu (INC-136).

    L'analyse porte sur le SVG **assaini** — celui qui a réellement été
    rasterisé — et dans ses coordonnées d'origine (avant réduction), pour
    que « hors cadre » veuille dire quelque chose.
    """
    from app.documents.controle_image import diagnostiquer
    from app.documents.professionnel.rasterisation import taille_svg

    if not getattr(image, "source_svg", None):
        return []
    # Les coordonnées des `<text>` sont celles du SVG, pas celles du PNG
    # (rasterisé à l'échelle 2 puis réduit) : on mesure dans SON espace.
    largeur, hauteur = taille_svg(image.source_svg)
    constats = diagnostiquer(
        image.source_svg,
        largeur=int(largeur),
        hauteur=int(hauteur),
        png=image.octets,
    )
    return [constat.en_dict() for constat in constats]


def _bloquants(ctx: Any, diagnostics: list[dict[str, str]]) -> list[dict[str, str]]:
    return [diag for diag in diagnostics if diag.get("niveau") == "bloquant"]


def _designation_image(payload: dict[str, Any]) -> str:
    """Libellé d'une image dans les avertissements (``Image`` par défaut)."""
    libelle = str(payload.get("designation") or "").strip()
    return libelle or "Image"
