"""Modèles d'organisation : inventaire, copie et remplissage.

Handlers qui listent ou décrivent un modèle, le copient pour produire un
document, remplacent un élément ou remplissent ses segments de texte.
Un document d'offre ne se produit jamais à neuf quand un modèle existe.

Extrait de ``build_document_tools`` sans modification de contenu.
"""

from __future__ import annotations

from typing import Any

from app.core.errors import ValidationError


def _lister_modeles(ctx: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Liste les modèles DOCX d'une organisation, sans ouvrir aucun fichier."""
    ctx.policy.require(ctx.identite.agent_id, "document_read")
    moteur = ctx.moteur_documentaire()
    modeles = moteur.lister(
        ctx.uuid(payload["organisation_id"], "organisation_id"),
        type_document=payload.get("type_document") or None,
    )
    return {
        "modeles": modeles,
        "nombre": len(modeles),
        "message": (
            "Choisir le modèle attendu par son `modele_id`, puis appeler "
            "copier_modele_document avec `ancre` + `entity_id` : la copie du "
            "modèle est le document qui sera édité. Aucun modèle pour "
            "l'organisation : le document se produit avec "
            "generer_document_professionnel."
        ),
    }


def _structure_modele(ctx: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Éléments réels d'un modèle : ce que l'agent pourra remplacer."""
    ctx.policy.require(ctx.identite.agent_id, "document_read")
    moteur = ctx.moteur_documentaire()
    organisation_id = ctx.uuid(payload["organisation_id"], "organisation_id")
    modele_id = payload.get("modele_id")
    if modele_id:
        cible = ctx.uuid(modele_id, "modele_id")
    else:
        type_document = payload.get("type_document")
        if not type_document:
            raise ValidationError(
                "Fournir `modele_id`, ou `type_document` pour prendre le "
                "modèle actif de ce type."
            )
        cible = moteur.modele_actif(organisation_id, str(type_document)).id
    return moteur.structure_modele(organisation_id, cible)


def _copier_modele(ctx: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Copie le modèle tel quel dans l'objet métier : c'est la base à éditer."""
    ctx.policy.require(ctx.identite.agent_id, "document_propose")
    moteur = ctx.moteur_documentaire()
    ancre = str(payload["ancre"])
    entity_id = ctx.uuid(payload["entity_id"], "entity_id")
    modele_id = payload.get("modele_id")
    document, modele, elements = moteur.copier_modele_dans_objet(
        ancre,
        entity_id,
        modele_id=ctx.uuid(modele_id, "modele_id") if modele_id else None,
        type_document=payload.get("type_document") or None,
        nom_fichier=payload.get("nom_fichier") or None,
    )
    return {
        "ancre": ancre,
        "entity_id": str(entity_id),
        "document_id": str(document.id),
        "nom": document.nom,
        "version": document.version,
        "statut": document.statut,
        "modele": {
            "modele_id": str(modele.id),
            "nom": modele.nom,
            "version": modele.version,
            "type_document": modele.type_document,
        },
        "nombre_elements": len(elements),
        "elements": [element.resume() for element in elements],
        "message": (
            "Le modèle de l'organisation a été **copié tel quel** dans "
            "l'offre : couverture, en-tête, pied, logo, styles et textes "
            "fixes sont ceux du client. Éditer maintenant cet exemplaire "
            "avec `remplacer_element_model` (texte, style conservé) ou "
            "`remplir_modele` (HTML stylisé + images base64), puis relire "
            "la structure. Le document n'est jamais reconstruit."
        ),
    }


def _obtenir_structure(ctx: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Structure d'un modèle OU d'une copie — un seul geste, deux cibles.

    Fusion de ``obtenir_structure_modele`` et ``obtenir_structure_document``
    (15/11) : les deux outils rendaient les mêmes éléments éditables, seule la
    cible différait. Le dispatch lit les champs fournis, sans rien inventer.
    """
    if payload.get("ancre") is not None:
        return _structure_document(ctx, payload)
    if payload.get("organisation_id") is not None:
        return _structure_modele(ctx, payload)
    raise ValidationError(
        "Préciser la cible : `ancre` + `entity_id` (copie de modèle), ou "
        "`organisation_id` + `modele_id`/`type_document` (modèle)."
    )


def _structure_document(ctx: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Éléments éditables de la copie en cours, avec leurs identifiants."""
    ctx.policy.require(ctx.identite.agent_id, "document_read")
    moteur = ctx.moteur_documentaire()
    return moteur.structure_document(
        str(payload["ancre"]), ctx.uuid(payload["entity_id"], "entity_id")
    )


def _remplacer_element(ctx: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Remplace le texte d'un ou plusieurs éléments : une seule version."""
    ctx.policy.require(ctx.identite.agent_id, "document_propose")
    moteur = ctx.moteur_documentaire()

    def _contenu(valeur: Any) -> Any:
        """Segments Pydantic → dictionnaires (le moteur attend des dicts)."""
        if isinstance(valeur, list):
            return [
                segment if isinstance(segment, dict) else segment.model_dump()
                for segment in valeur
            ]
        return valeur

    if payload.get("elements"):
        remplacements = [
            {"element_id": str(element), "nouveau_element": _contenu(contenu)}
            for element, contenu in zip(
                payload["elements"], payload["nouveaux_elements"], strict=True
            )
        ]
    else:
        remplacements = [
            {
                "element_id": str(payload["element_id"]),
                "nouveau_element": _contenu(payload["nouveau_element"]),
            }
        ]
    return moteur.remplacer_elements(
        str(payload["ancre"]),
        ctx.uuid(payload["entity_id"], "entity_id"),
        remplacements,
    )


def _remplir_modele(ctx: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Insère du contenu HTML stylisé dans la copie du modèle."""
    ctx.policy.require(ctx.identite.agent_id, "document_propose")
    moteur = ctx.moteur_documentaire()
    remplissages = [
        item if isinstance(item, dict) else item.model_dump()
        for item in payload["remplissages"]
    ]
    return moteur.remplir(
        str(payload["ancre"]),
        ctx.uuid(payload["entity_id"], "entity_id"),
        remplissages,
    )


def _revenir_version(ctx: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Restaure une version de l'historique (défaut : la précédente)."""
    ctx.policy.require(ctx.identite.agent_id, "document_propose")
    moteur = ctx.moteur_documentaire()
    return moteur.revenir_version(
        str(payload["ancre"]),
        ctx.uuid(payload["entity_id"], "entity_id"),
        payload.get("version"),
    )
