"""Cycle de vie documentaire hors rédaction.

Handlers qui analysent un document de référence, importent un tableau
Excel, renomment ou suppriment (suppression logique) un document.

Extrait de ``build_document_tools`` sans modification de contenu.
"""

from __future__ import annotations

from typing import Any

from app.core.errors import ValidationError


def _reference(ctx: Any, payload: dict[str, Any]) -> dict[str, Any]:
    ctx.policy.require(ctx.identite.agent_id, "document_read")
    from app.documents.reference import analyser_reference

    service = ctx.service_factory()
    document_id = ctx.uuid(payload["document_id"], "document_id")
    document = service.obtenir(document_id)
    _nom, chemin = service.telecharger(document_id)
    niveau = str(payload.get("niveau") or "resume").strip().lower()
    if niveau not in ("resume", "complet"):
        raise ValidationError(
            f"Niveau d'analyse inconnu : {payload.get('niveau')} (attendus : resume, complet)"
        )
    analyse = analyser_reference(chemin, niveau=niveau)
    sortie: dict[str, Any] = {
        "document_id": str(document.id),
        "nom": document.nom,
        "message": (
            "Plan et style de la référence extraits. Réutiliser ``style_spec`` "
            "pour générer un document au même style : le contenu de la "
            "référence n'est jamais recopié."
        ),
        **analyse,
    }
    if chemin.suffix.lower() == ".docx":
        # Inventaire exploitable pour un remplissage fidèle (INC-125) : les
        # placeholders à fournir, les titres à remplir et les styles de titre
        # du client — sans quoi l'agent ne peut que redessiner à l'aveugle.
        from app.documents.modeles import inventaire_modele

        sortie["modele_documentaire"] = inventaire_modele(chemin)
        sortie["message"] = (
            "Plan, style et **structure de modèle** extraits. Pour modifier "
            "fidèlement le DOCX d'une organisation : "
            "``copier_modele_document`` (le fichier est copié tel quel dans "
            "l'offre), puis ``remplacer_element_model`` / "
            "``remplir_modele`` avec les `element_id` de "
            "``obtenir_structure_document``. Le contenu de la référence "
            "n'est jamais recopié."
        )
    return sortie


def _importer_tableau(ctx: Any, payload: dict[str, Any]) -> dict[str, Any]:
    ctx.policy.require(ctx.identite.agent_id, "document_read")
    from app.documents.tableau_xlsx import import_tableau_xlsx

    service = ctx.service_factory()
    document_id = ctx.uuid(payload["document_id"], "document_id")
    document = service.obtenir(document_id)
    _nom, chemin = service.telecharger(document_id)
    if chemin.suffix.lower() != ".xlsx":
        raise ValidationError(
            f"Import de tableau : un classeur .xlsx est attendu (reçu {document.nom})"
        )
    tableau = import_tableau_xlsx(
        chemin,
        feuille=payload.get("feuille"),
        ligne_entete=int(payload.get("ligne_entete") or 0),
        colonne_min=int(payload.get("colonne_min") or 1),
        colonne_max=payload.get("colonne_max"),
        ligne_min=int(payload.get("ligne_min") or 1),
        ligne_max=payload.get("ligne_max"),
        titre_tableau=payload.get("titre_tableau"),
    )
    grille = tableau.grille()
    return {
        "document_id": str(document.id),
        "tableau": tableau.model_dump(mode="json", exclude_none=True),
        "resume": {
            "lignes": len(grille),
            "colonnes": tableau.largeur_grille(),
            "lignes_entete": tableau.nb_lignes_entete(),
        },
        "message": (
            "Placer ce bloc ``tableau`` tel quel dans ``document_spec.blocs`` : "
            "fusions, trames et largeurs du classeur sont conservées. "
            "Ne pas ressaisir les cellules à la main."
        ),
    }


def _renommer(ctx: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Renomme un document : métadonnées seulement, contenu intact.

    Le contenu ne passe jamais par ici — `modifier_metadonnees` ne touche
    que la fiche. Un document à plusieurs versions est refusé par le service
    (le nom est la clé de l'historique) : l'erreur remonte telle quelle.
    """
    ctx.policy.require(ctx.identite.agent_id, "document_propose")
    from app.application.dto import DocumentMetadonneesUpdateInput

    document_id = ctx.uuid(payload["document_id"], "document_id")
    service = ctx.service_factory()
    document = service.modifier_metadonnees(
        document_id, DocumentMetadonneesUpdateInput(nom=payload["nom"])
    )
    return {
        "document_id": str(document.id),
        "nom": document.nom,
        "type_document": document.type_document,
        "version": document.version,
        "statut": document.statut,
        "message": (
            "Document renommé : seul son nom a changé. Contenu, version, "
            "statut et rattachements sont ceux d'avant."
        ),
    }


def _supprimer(ctx: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Supprime un document (corbeille), décision humaine déjà requise.

    L'interruption HITL (``approval_required``) a lieu **avant** l'appel :
    ici, l'accord est acquis. ``decided_by`` porte le compte à l'origine de
    la demande quand il est connu (contexte AG-UI), sinon l'agent — l'audit
    ne prétend jamais qu'un humain a cliqué.
    """
    ctx.policy.require(ctx.identite.agent_id, "document_propose")
    from app.application.dto import DecisionInput
    from app.application.trace import demande_par_effectif

    document_id = ctx.uuid(payload["document_id"], "document_id")
    service = ctx.service_factory()
    document = service.obtenir(document_id)
    decideur = demande_par_effectif() or f"agent:{ctx.identite.agent_id}"
    retires = service.supprimer_document(
        document_id,
        DecisionInput(decided_by=decideur, reason=payload["motif"]),
    )
    return {
        "document_id": str(document_id),
        "nom": document.nom,
        "type_document": document.type_document,
        "fichiers_retires": retires,
        "suppression": "corbeille",
        "message": (
            "Document placé dans la corbeille : le fichier est retiré, la "
            "fiche reste, et la restauration demeure possible."
        ),
    }
