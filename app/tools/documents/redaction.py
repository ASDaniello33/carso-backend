"""Rédaction et rendu des livrables.

Handlers qui produisent des octets (DOCX, PDF, PPTX, XLSX, HTML) et les
déposent en statut ``proposed``, corrigent un document existant, le
valident ou en rendent les pages en images. La frontière HITL est
inchangée : un agent ne produit jamais un document ``approved``.

Extrait de ``build_document_tools`` sans modification de contenu.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any
from uuid import UUID

from app.application.dto import DocumentUploadInput
from app.core.errors import NotFoundError, ValidationError
from app.documents.spec import DocumentSpec
from app.tools.documents.ancrage import _format_depot
from app.tools.documents.schemas import _FORMAT_PPTX, _FORMATS


def _format_effectif(payload: dict[str, Any]) -> str:
    """Format du rendu : explicite, sinon déduit du nom de fichier, sinon DOCX.

    Un nom ``.pdf`` sans ``format`` était rendu en DOCX puis déposé sous un nom
    ``.pdf`` (ou renommé ``.pdf.docx``) : l'agent demandait un PDF et recevait
    un DOCX. L'extension du nom est une intention explicite — elle fait foi
    quand ``format`` est absent.
    """
    demande = payload.get("format")
    if demande is not None and str(demande).strip():
        return _format_depot(demande)
    nom = str(payload.get("nom_fichier") or "").strip()
    extension = Path(nom).suffix.lower().lstrip(".")
    if extension in _FORMATS:
        return extension
    return "docx"


def _images_de(spec: DocumentSpec) -> Any:
    """Parcourt un spec et rend chaque bloc ``Image`` rencontré."""
    from pydantic import BaseModel

    from app.documents.spec import Image

    def parcourir(valeur: Any) -> Any:
        if isinstance(valeur, Image):
            yield valeur
        elif isinstance(valeur, BaseModel):
            for nom in type(valeur).model_fields:
                yield from parcourir(getattr(valeur, nom))
        elif isinstance(valeur, (list, tuple)):
            for element in valeur:
                yield from parcourir(element)

    return parcourir(spec)


def _fournisseur_images(ctx: Any, spec: DocumentSpec) -> Any:
    """Résout les images d'un spec depuis le stockage (ADR 0005).

    Sans fournisseur, un bloc ``Image(document_id=…)`` ou ``Image(chemin=…)``
    échouait toujours (« Image référencée sans fournisseur d'images ») : un
    agent ne pouvait donc pas coller un PNG déposé dans le dossier du compte.

    Chaque ``document_id`` est résolu par ``DocumentService.obtenir`` — donc
    **avec le périmètre du compte** : une fiche hors périmètre est refusée par
    un message explicite, jamais insérée. Les chemins logiques sont confinés à
    la racine de stockage par ``ImagesDisque``. Les images ``base64`` et ``svg``
    n'ont besoin d'aucun fournisseur et traversent telles quelles.
    """
    from app.documents.assets import ImagesDisque
    from app.documents.paths import resolve_within_root

    service = ctx.service_factory()
    racine = getattr(service.storage, "root", None)
    fournisseur = ImagesDisque(racine=racine)
    for image in _images_de(spec):
        if image.document_id is None:
            continue
        try:
            document = service.obtenir(image.document_id)
        except NotFoundError as erreur:
            raise ValidationError(
                "Image référencée introuvable ou hors de votre périmètre : "
                f"document {image.document_id}",
                details={"document_id": str(image.document_id)},
            ) from erreur
        if racine is None:
            raise ValidationError(
                "Racine de stockage indisponible : impossible de lire l'image "
                f"référencée ({image.document_id}).",
                details={"document_id": str(image.document_id)},
            )
        fournisseur.ajouter_fichier(
            image.document_id, resolve_within_root(racine, document.storage_path)
        )
    return fournisseur


def _generer_libre(ctx: Any, payload: dict[str, Any], fmt: str) -> dict[str, Any]:
    """Corps HTML + classes CARSO → DOCX/PDF (INC-137).

    C'est la voie par défaut de ``generer_document`` : le modèle écrit du
    HTML (ce qu'il fait bien), le serveur tient la typographie, les images
    et les figures. Aucune couverture ni sommaire imposés — d'où la
    différence avec ``generer_document_professionnel``.
    """
    from app.documents.document_libre import rendre_document_libre

    if fmt not in {"docx", "pdf"}:
        raise ValidationError(
            "Le mode HTML produit un DOCX ou un PDF (reçu "
            f"'{fmt}'). Pour un classeur, utiliser importer_tableau_xlsx "
            "ou un document_spec."
        )
    titre = str(payload.get("titre") or "Document")
    rendu = rendre_document_libre(
        payload["html"],
        format=fmt,
        css=payload.get("css"),
        titre=titre,
        auteur=str(payload.get("auteur") or ""),
        graphiques=_specs_graphiques(payload.get("graphiques")) or None,
        illustrations=_specs_illustrations(payload.get("illustrations")) or None,
    )
    extension, _mime = _FORMATS[rendu.format]
    sortie = ctx.deposer(rendu.octets, payload, extension)
    sortie.update(
        {
            "format": rendu.format,
            "modele": "html",
            "graphiques": len(payload.get("graphiques") or []),
            "illustrations": len(payload.get("illustrations") or []),
            "avertissements": rendu.avertissements,
        }
    )
    if rendu.pages:
        sortie["pages"] = rendu.pages
    avertissements = (
        " " + " ".join(rendu.avertissements) if rendu.avertissements else ""
    )
    sortie["message"] = (
        "Document HTML généré et déposé en statut 'proposed' : validation "
        "humaine requise avant approbation. Appeler "
        "proposer_telechargement_document pour offrir le téléchargement."
        + avertissements
    )
    return sortie


def _generer(ctx: Any, payload: dict[str, Any]) -> dict[str, Any]:
    ctx.policy.require(ctx.identite.agent_id, "document_propose")
    _refuser_generation_si_modele(ctx, payload)
    fmt = _format_effectif(payload)
    if payload.get("html"):
        return _generer_libre(ctx, payload, fmt)
    if payload.get("document_spec"):
        return _generer_structurer(ctx, payload, fmt)
    if not payload.get("titre"):
        raise ValidationError(
            "Fournir 'html' (voie recommandée : corps HTML + classes CARSO). "
            "'document_spec' reste accepté pour une structure imposée."
        )
    extension, _mime = _FORMATS[fmt]
    from app.documents.generation import GenerateurDocument

    generateur = GenerateurDocument()
    if fmt == "xlsx":
        octets = generateur.xlsx(
            titre_feuille=payload.get("titre") or "Feuille",
            lignes=payload.get("tableau") or [],
        )
    elif fmt == "docx":
        octets = generateur.docx(
            titre=payload["titre"],
            paragraphes=payload.get("paragraphes"),
            tableau=payload.get("tableau"),
        )
    else:
        octets = generateur.pdf(
            titre=payload["titre"],
            paragraphes=payload.get("paragraphes"),
            tableau=payload.get("tableau"),
        )
    resultat = ctx.deposer(octets, payload, extension)
    if not resultat.get("non_classe"):
        resultat["message"] = (
            "Document généré et déposé en statut 'proposed' : il nécessite "
            "une validation humaine avant approbation. "
            "Appeler proposer_telechargement_document pour offrir le téléchargement."
        )
    else:
        resultat["message"] = (
            "Document généré et déposé en statut 'proposed' sans rattachement "
            f"métier (dossier personnel {resultat['document_id']}). "
            "Appeler proposer_telechargement_document pour offrir le téléchargement."
        )
    return resultat


def _generer_html(ctx: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """HTML → DOCX (fidèle au HTML+CSS) ou PDF (moteur CARSO), dépôt HITL.

    Les figures (INC-123) suivent le même contrat que l'outil professionnel :
    ``graphiques`` / ``illustrations`` remplissent les ancres
    ``<figure data-graphique|data-illustration="id">`` du HTML. Un ``<svg>``
    écrit directement dans le HTML est conservé (rasterisé pour Word, inséré
    comme image dans le PDF) — il n'est plus supprimé en silence.
    """
    ctx.policy.require(ctx.identite.agent_id, "document_propose")
    _refuser_generation_si_modele(ctx, payload)
    from app.documents.generation_html import convertir_html_bytes

    # Le format suit l'intention exprimée par le nom quand il est omis :
    # exiger un champ que le schéma rend obligatoire bloquait un appel
    # légitime (« format : champ requis ») alors que le nom disait déjà .pdf.
    fmt = _format_effectif(payload)
    if fmt not in {"docx", "pdf"}:
        raise ValidationError(
            "Le mode HTML produit un DOCX ou un PDF (reçu "
            f"'{fmt}'). Pour un classeur, utiliser importer_tableau_xlsx ou "
            "un document_spec."
        )
    octets = convertir_html_bytes(
        payload["html"],
        format=fmt,
        css=payload.get("css"),
        graphiques=_specs_graphiques(payload.get("graphiques")) or None,
        illustrations=_specs_illustrations(payload.get("illustrations")) or None,
    )
    extension, _mime = _FORMATS[fmt]
    sortie = ctx.deposer(octets, payload, extension)
    if sortie.get("non_classe"):
        sortie["message"] = (
            "Document HTML converti et déposé en statut 'proposed' sans "
            f"rattachement métier (dossier personnel {sortie['document_id']}). "
            "Validation humaine requise. Appeler proposer_telechargement_document."
        )
    else:
        sortie["message"] = (
            "Document HTML converti et déposé en statut 'proposed' "
            "(validation humaine requise). Appeler proposer_telechargement_document."
        )
    if fmt == "pdf":
        sortie["note_pdf"] = (
            "Le PDF est rendu par le moteur CARSO depuis la structure du HTML "
            "(contenu identique ; pas de convertisseur bureautique sur ce "
            "serveur, la mise en page n'est pas une copie pixel du DOCX)."
        )
    return sortie


def _generer_professionnel(ctx: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Corps HTML + thème → PDF ou DOCX professionnel, déposé en proposition.

    Version CARSO du module ``generation-document`` : le moteur injecte le
    thème, la couverture, le sommaire numéroté et la pagination — l'agent
    n'écrit que le corps. Le rendu reste **en mémoire** (le moteur travaille
    dans un dossier temporaire effacé à la sortie) : aucun chemin disque
    n'est renvoyé et le dépôt passe par ``DocumentService``, statut
    ``proposed`` (règle 6 [C]).

    Le moteur est importé **paresseusement** : ``playwright`` est une
    dépendance optionnelle (extra ``documents_pro``), et le backend doit
    démarrer sans elle. Son absence produit une erreur explicite au premier
    appel, jamais une dégradation silencieuse.
    """
    ctx.policy.require(ctx.identite.agent_id, "document_propose")
    from app.documents.professionnel import rendre_professionnel
    from app.documents.professionnel.html_builder import slugifier

    graphes = _specs_graphiques(payload.get("graphiques"))
    rendu = rendre_professionnel(
        payload["contenu_html"],
        payload["titre"],
        # ``format`` est facultatif au schéma : ``str(None)`` envoyait la
        # chaîne "None" au moteur et échouait sur un appel valide.
        format=_format_effectif(payload),
        auteur=payload.get("auteur") or "",
        theme=payload.get("theme") or "corporate",
        orientation=payload.get("orientation") or "portrait",
        css_supplementaire=payload.get("css_supplementaire"),
        graphiques=graphes or None,
    )
    if rendu.format == "pptx":
        extension, _mime = _FORMAT_PPTX
    else:
        extension, _mime = _FORMATS[rendu.format]
    base = payload.get("nom_fichier") or payload["titre"]
    nom = slugifier(Path(str(base)).stem, "_")[:60] or "document"

    sortie = ctx.deposer(rendu.octets, {**payload, "nom_fichier": nom}, extension)
    documents: dict[str, Any] = {
        "principal": sortie["document_id"],
        "format": rendu.format,
    }
    if rendu.jumeau_pdf is not None:
        # Le jumeau PDF rend le livrable prévisualisable (render_document ne
        # rend que des PDF) : même contenu, même pagination, et **même
        # révision**. Sans `revision_imposee`, le PDF ouvrirait sa propre série
        # (v1) et le livrable se retrouverait à deux numéros différents selon
        # le format (constat du 11/11).
        jumeau = ctx.deposer(
            rendu.jumeau_pdf,
            {
                **payload,
                "nom_fichier": f"{nom}.pdf",
                "revision_imposee": sortie.get("version"),
            },
            ".pdf",
        )
        documents["pdf"] = jumeau["document_id"]

    sortie["documents"] = documents
    sortie["format"] = rendu.format
    sortie["pages"] = rendu.pages
    sortie["theme"] = rendu.theme
    if rendu.format == "pptx":
        # Un deck compte ses diapositives : dire « pages » à l'agent
        # l'inviterait à en rajouter pour tenir une pagination A4.
        sortie["diapositives"] = rendu.pages
        # render_document ne rend que des PDF : le dire plutôt que laisser
        # l'agent découvrir l'échec en appelant l'outil sur un .pptx.
        sortie["limite_apercu"] = (
            "render_document ne prévisualise que les PDF : pour un PPTX, "
            "relis le contenu de chaque diapositive avant de le présenter."
        )
    if graphes:
        # Un classeur ne recoit pas un SVG colle : chaque graphique devient un
        # objet Excel natif (donnees de cellule + graphique lie), donc filtrable
        # et modifiable a l'ouverture. Le dire evite a l'agent de croire a une
        # image figee.
        integration = {
            "pdf": "SVG vectoriel (nettete maximale a l'impression).",
            "docx": "PNG haute resolution (Word ne lit pas le SVG).",
            "pptx": "PNG haute resolution (PowerPoint ne lit pas le SVG).",
            "xlsx": (
                "Graphique Excel natif, lie a une feuille de donnees dediee "
                "(modifiable dans le tableur, pas une image)."
            ),
        }.get(rendu.format, "SVG vectoriel.")
        sortie["graphiques"] = {
            "nombre": len(graphes),
            "ids": list(graphes),
            "integration": integration,
        }
    if rendu.avertissements:
        sortie["avertissements"] = list(rendu.avertissements)
    sortie["conseil"] = rendu.conseil
    libelle = {
        "pptx": "Présentation",
        "xlsx": "Classeur Excel",
    }.get(rendu.format, "Document professionnel")
    sortie["message"] = (
        f"{libelle} généré et déposé en statut 'proposed' sans "
        f"rattachement métier (dossier personnel {sortie['document_id']}). "
        "Validation humaine requise. Appeler proposer_telechargement_document."
        if sortie.get("non_classe")
        else (
            f"{libelle} généré et déposé en statut 'proposed' "
            "(validation humaine requise). Lire ``avertissements`` et les "
            "corriger avant de présenter : appeler render_document puis "
            "proposer_telechargement_document."
        )
    )
    return sortie


def _generer_structurer(ctx: Any, payload: dict[str, Any], fmt: str) -> dict[str, Any]:
    """Génère un vrai livrable depuis un ``DocumentSpec`` (avec validation).

    Le même contenu est déposé en PDF quand le format demandé n'est pas déjà
    le PDF : un livrable remis à un tiers doit être **lisible** (et donc
    prévisualisable) sans dépendre d'un logiciel bureautique.
    """
    from app.documents.renderers import rendre

    spec, reductions = _spec_depuis(payload)
    modele_utilise: dict[str, Any] | None = None
    reference = payload.get("modele")
    if reference:
        # Identité visuelle d'un modèle d'organisation (INC-125) : les
        # polices, couleurs, marges et le gabarit de page viennent du
        # document du client. La structure, elle, reste celle du spec.
        from app.documents.modeles import style_depuis_modele

        if not isinstance(reference, dict):
            reference = reference.model_dump()
        modele, chemin = ctx.service_factory().obtenir_modele(
            ctx.uuid(reference.get("organisation_id"), "organisation_id"),
            str(reference.get("type_document") or ""),
        )
        spec = spec.model_copy(update={"style": style_depuis_modele(chemin)})
        modele_utilise = {
            "id": str(modele.id),
            "nom": modele.nom,
            "version": modele.version,
            "type_document": modele.type_document,
        }
    rapport = None
    if payload.get("valider", True):
        rapport = _valider_spec(spec, payload.get("profil"))

    extension, _mime = _FORMATS[fmt]
    # Les images du spec (document_id / chemin) sont résolues ici : le renderer
    # ne connaît ni la base ni le stockage (séparation des couches).
    fournisseur = _fournisseur_images(ctx, spec)
    sortie = ctx.deposer(rendre(spec, format=fmt, images=fournisseur), payload, extension)
    documents: dict[str, Any] = {"principal": sortie["document_id"], "format": fmt}
    if fmt != "pdf":
        jumeau = ctx.deposer(
            rendre(spec, format="pdf", images=fournisseur),
            {
                **payload,
                "nom_fichier": f"{Path(str(payload['nom_fichier'])).stem}.pdf",
                # Même livrable, même révision : voir le jumeau professionnel.
                "revision_imposee": sortie.get("version"),
            },
            ".pdf",
        )
        documents["pdf"] = jumeau["document_id"]

    sortie["documents"] = documents
    sortie["resume_document"] = spec.resume()
    if modele_utilise is not None:
        sortie["modele"] = modele_utilise
    # Le spec **effectivement rendu** revient à l'agent : sans lui, une
    # correction ciblée suivante n'aurait aucun point de départ (il ne
    # pourrait que régénérer le document entier). Les images inline en
    # base64 sont retirées du retour (poids) et leur absence est dite.
    donnees_spec = spec.model_dump(mode="json")
    images_inline = _nombre_images_inline(donnees_spec)
    sortie["document_spec"] = _sans_base64(donnees_spec)
    avertissements_images = [*reductions]
    if images_inline:
        avertissements_images.append(
            f"{images_inline} image(s) inline (base64) non restituée(s) dans "
            "``document_spec`` : le livrable généré les contient ; pour une "
            "correction, renvoyer l'image avec le spec."
        )
    if rapport is not None:
        sortie["validation"] = _contrat_validation(rapport)
        sortie["warnings"] = [
            *avertissements_images,
            *rapport.messages("erreur"),
            *rapport.messages("avertissement"),
        ][:20]
    elif avertissements_images:
        sortie["warnings"] = avertissements_images
    sortie["message"] = _message_livrable(sortie, rapport is not None and rapport.ok)
    return sortie


def _rendre_et_deposer_professionnel(ctx: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Rend le livrable professionnel et l'habille du message d'agent.

    Point commun de ``generer_document_professionnel`` (action='generer'),
    de la rédaction progressive (action='finaliser') et de la correction par
    section : le dépôt, les avertissements et le message ne sont écrits
    qu'une fois.
    """
    # Images inline (data-URI) : réduites **avant** la mise en page, pour
    # que le DOCX/PPTX/PDF n'embarque pas une photo à la résolution du
    # capteur. La transformation est annoncée avec les avertissements.
    reduction: list[str] = []
    corps = str(payload.get("contenu_html") or "")
    if "data:image/" in corps:
        from app.documents.images_inline import reduire_data_uri

        reduit, reduction = reduire_data_uri(corps)
        if reduction:
            payload = {**payload, "contenu_html": reduit}
    sortie = _generer_professionnel(ctx, payload)
    # Conformité jugée sur les constats du moteur : réduire une image n'est
    # pas un défaut de conformité, seulement une transformation à dire.
    conforme = not sortie.get("avertissements")
    if reduction:
        sortie["avertissements"] = [
            *reduction,
            *(sortie.get("avertissements") or []),
        ]
    sortie["message"] = _message_livrable(sortie, conforme=conforme)
    _enregistrer_source(ctx, 
        sortie,
        corps=str(payload.get("contenu_html") or ""),
        meta=payload,
        graphiques=payload.get("graphiques"),
    )
    return sortie


def _enregistrer_source(ctx: Any, 
    sortie: dict[str, Any],
    *,
    corps: str,
    meta: dict[str, Any],
    graphiques: Any,
) -> None:
    """Mémorise le corps et l'habillage du livrable déposé.

    C'est ce qui rend la correction par section possible : sans source, un
    document ne peut être que régénéré. Un échec d'écriture (disque plein,
    droits) ne fait pas perdre le livrable déjà déposé — la source manquera
    simplement, avec une erreur explicite à la correction.
    """
    from app.documents.sources import enregistrer as _ecrire_source

    specs = [
        entree if isinstance(entree, dict) else entree.model_dump(mode="json")
        for entree in (graphiques or [])
    ]
    racine = ctx.racine_sources_rendus()
    documents = sortie.get("documents") or {}
    principal = documents.get("principal") or sortie.get("document_id")
    jumeau = documents.get("pdf")
    try:
        _ecrire_source(
            racine,
            principal,
            corps=corps,
            meta=meta,
            graphiques=specs,
            principal_id=principal,
            jumeau_id=jumeau,
        )
        if jumeau:
            _ecrire_source(
                racine,
                jumeau,
                corps=corps,
                meta={**meta, "format": "pdf"},
                graphiques=specs,
                principal_id=principal,
                jumeau_id=jumeau,
            )
    except OSError:
        return


def _corriger(ctx: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Applique des corrections ciblées et dépose (ou versionne) le résultat.

    Le patch ne reconstruit pas le document : il modifie la copie du spec,
    qui est ensuite revalidée, puis rendue. Le journal des changements est
    renvoyé tel quel — l'agent peut le montrer à l'utilisateur.
    """
    ctx.policy.require(ctx.identite.agent_id, "document_propose")
    from app.documents.repair import appliquer_patch

    spec, reductions = _spec_depuis(payload)
    corrige, journal = appliquer_patch(spec, payload["operations"])
    rapport = None
    if payload.get("valider", True):
        rapport = _valider_spec(corrige, payload.get("profil"))
    validation = _contrat_validation(rapport) if rapport is not None else None
    avertissements = [
        # Une image inline ajoutée pendant la correction est réduite comme
        # à la génération : la transformation est dite, pas silencieuse.
        *reductions,
        *(
            [
                constat.message
                for constat in rapport.findings
                if constat.gravite != "info"
            ][:10]
            if rapport is not None
            else []
        ),
    ][:12]

    document_id = payload.get("document_id")
    if document_id:
        # Une nouvelle version garde le nom — donc l'extension — du document
        # corrigé : rendre un DOCX sous un nom .pdf produisait « signature
        # binaire invalide ». Le format effectif vient du fichier, pas d'un
        # défaut implicite (l'agent ne peut plus se tromper de format).
        courant = ctx.service_factory().obtenir(
            ctx.uuid(str(document_id), "document_id")
        )
        extension = Path(courant.nom).suffix.lower().lstrip(".")
        if extension in {"docx", "pdf"}:
            fmt = extension
            demande = payload.get("format")
            if demande and str(demande).strip().lower() != fmt:
                avertissements.append(
                    f"Format '{demande}' ignoré : une nouvelle version conserve "
                    f"le format du document corrigé (.{fmt})."
                )
        else:
            raise ValidationError(
                f"Ce document est un .{extension or '?'} : corriger_document ne "
                "produit que des DOCX ou des PDF. Déposer le fichier corrigé "
                "comme un nouveau document pour un autre format.",
                details={"document_id": str(document_id), "extension": extension},
            )
    else:
        fmt = _format_effectif(payload)
    if not document_id:
        if not payload.get("nom_fichier") or not payload.get("type_document"):
            raise ValidationError(
                "Sans 'document_id', fournir 'nom_fichier' et 'type_document' "
                "pour déposer le document corrigé."
            )
        sortie = _generer_structurer(ctx, 
            {
                **payload,
                "document_spec": corrige.model_dump(mode="json"),
                "valider": False,
            },
            fmt,
        )
        resultat = {
            **sortie,
            "operations_appliquees": journal,
        }
    else:
        principal = _versionner(ctx, 
            ctx.uuid(str(document_id), "document_id"), corrige, fmt, payload
        )
        documents: dict[str, Any] = {
            "principal": principal["document_id"],
            "format": principal.get("format", fmt),
        }
        if payload.get("document_pdf_id"):
            jumeau = _versionner(ctx, 
                ctx.uuid(str(payload["document_pdf_id"]), "document_pdf_id"),
                corrige,
                "pdf",
                payload,
            )
            documents["pdf"] = jumeau["document_id"]
        resultat = {
            "document_id": principal["document_id"],
            "version": principal["version"],
            "nom": principal["nom"],
            "documents": documents,
            "operations_appliquees": journal,
            "resume_document": corrige.resume(),
            "message": (
                f"Correction appliquée en nouvelle version (v{principal['version']}) : "
                "la version précédente est conservée. Appeler render_document puis "
                "proposer_telechargement_document, et rafraichir_application."
            ),
        }
    if validation is not None:
        resultat["validation"] = validation
    if validation is not None or avertissements:
        resultat["warnings"] = avertissements
    return resultat


def _versionner(ctx: Any, 
    document_id: UUID, spec: Any, fmt: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """Dépose le document corrigé comme version suivante du document visé."""
    from app.documents.renderers import rendre

    service = ctx.service_factory()
    courant = service.obtenir(document_id)
    # Sécurité de dernier recours : le format du rendu suit l'extension du
    # document versionné. Un appel direct à `_versionner` ne peut donc pas
    # écrire des octets DOCX dans un fichier .pdf.
    extension = Path(courant.nom).suffix.lower().lstrip(".")
    fmt_effectif = extension if extension in {"docx", "pdf"} else fmt
    entree = DocumentUploadInput(
        type_document=courant.type_document,
        nom=courant.nom,
        stream=io.BytesIO(
            rendre(
                spec,
                format=fmt_effectif,
                images=_fournisseur_images(ctx, spec),
            )
        ),
        proposed_by_agent=payload.get("propose_par") or ctx.identite.agent_id,
        created_by=payload.get("propose_par") or ctx.identite.agent_id,
    )
    document = service.remplacer_document(document_id, entree)
    return {
        "document_id": str(document.id),
        "version": document.version,
        "nom": document.nom,
        "statut": document.statut,
        "format": fmt_effectif,
    }


def _verifier(ctx: Any, payload: dict[str, Any]) -> dict[str, Any]:
    ctx.policy.require(ctx.identite.agent_id, "document_read")
    # Validation seule : rien n'est inséré, donc rien à réduire ni à signaler.
    spec, _reductions = _spec_depuis(payload)
    portees = payload.get("portees")
    if portees is not None and not isinstance(portees, list):
        raise ValidationError("portees doit être une liste : structure, contenu, style")
    rapport = _valider_spec(spec, payload.get("profil"), portees=portees)
    return {
        "document_id": None,
        "ok": rapport.ok,
        "validation": _contrat_validation(rapport),
        "findings": [constat.model_dump() for constat in rapport.findings][:40],
        "resume": rapport.resume,
        "message": (
            "Document conforme aux attentes de son profil."
            if rapport.ok
            else "Document NON conforme : corriger les erreurs signalées avant de le présenter."
        ),
    }


def _rendre(ctx: Any, payload: dict[str, Any]) -> dict[str, Any]:
    ctx.policy.require(ctx.identite.agent_id, "document_read")
    service = ctx.service_factory()
    document_id = ctx.uuid(payload["document_id"], "document_id")
    pages = payload.get("pages")
    if pages is not None and not isinstance(pages, list):
        raise ValidationError("pages doit être une liste de numéros de page (1-based)")
    apercu = service.apercu_document(
        document_id,
        pages=[int(page) for page in pages] if pages else None,
        dpi=payload.get("dpi"),
    )
    numeros = [page.numero for page in apercu.pages]
    sortie: dict[str, Any] = {
        "ok": True,
        "document_id": str(apercu.document_id),
        "nom": apercu.nom,
        "nb_pages": apercu.nb_pages,
        "pages": numeros,
        "nb_pages_rendues": len(numeros),
        "dpi": apercu.dpi,
        "tronque": apercu.tronque,
        "warnings": (
            [
                f"Seules {len(numeros)} page(s) sur {apercu.nb_pages} ont été rendues "
                "(plafond de pages du serveur)."
            ]
            if apercu.tronque
            else []
        ),
        "message": (
            "Aperçu prêt : appeler afficher_apercu_document pour montrer les "
            "pages à l'utilisateur dans le fil."
        ),
    }
    return sortie


def _modifier_section(ctx: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Corrige **une** section du livrable rendu, sans le réécrire entier.

    La source (corps HTML + habillage) est cherchée dans la **famille** du
    document : même nom et même type, versions résolues sous périmètre par
    le service. Une correction faite sur une v2 dont la source a été écrite
    pour la v1 ne se perd donc pas.
    """
    from app.documents.sections import assembler, decouper, decrire, normaliser_fragment
    from app.documents.sources import charger_famille
    from app.documents.sources import enregistrer as _ecrire_source

    service = ctx.service_factory()
    document_id = ctx.uuid(payload["document_id"], "document_id")
    # ``obtenir`` applique le périmètre d'accès : un document hors
    # périmètre est refusé ici, jamais édité.
    document = service.obtenir(document_id)
    racine = ctx.racine_sources_rendus()
    trouvee = charger_famille(racine, document_id, service.versions(document_id))
    if trouvee is None:
        raise NotFoundError(
            f"« {document.nom} » n'a pas de source éditable : seul un "
            "document rendu par generer_document_professionnel se corrige "
            "par section (un import ou un document structuré n'a pas de "
            "corps HTML). Régénérer le document pour le modifier."
        )
    source, identifiant_source = trouvee
    meta = dict(source.get("meta") or {})
    format_document = str(meta.get("format") or "pdf")
    morceaux = decouper(str(source.get("corps") or ""))

    action = payload.get("action") or "lister"
    if action == "lister":
        return {
            "action": "lister",
            "document_id": str(document_id),
            "nom": document.nom,
            "format": format_document,
            "sections": decrire(morceaux),
            "message": (
                "Corriger : action='remplacer' (section_index + "
                "contenu_html), 'ajouter' (après section_index) ou "
                "'supprimer'. Le reste du document n'est jamais réécrit."
            ),
        }

    index = payload.get("section_index")
    if index is not None:
        index = int(index)
        # 'ajouter' accepte un index une position au-delà de la dernière
        # section (insertion à la fin) : la borne le dit.
        derniere = len(morceaux) if action in {"remplacer", "supprimer"} else len(morceaux) + 1
        if index > derniere:
            raise ValidationError(
                f"section_index {index} hors bornes : le document contient "
                f"{len(morceaux)} section(s) (indices 1 à {len(morceaux)}). "
                "Appeler action='lister' pour les voir."
            )

    graphiques = list(source.get("graphiques") or [])
    if action == "supprimer":
        del morceaux[int(index) - 1]
        if not morceaux:
            raise ValidationError(
                "Supprimer cette section viderait le document : corriger "
                "son contenu plutôt (action='remplacer')."
            )
    else:
        fragment = normaliser_fragment(
            str(payload.get("contenu_html") or ""), format_document=format_document
        )
        if action == "remplacer":
            morceaux[int(index) - 1] = fragment
        elif index is None:
            morceaux.append(fragment)
        else:
            morceaux.insert(int(index), fragment)
        for entree in payload.get("graphiques") or []:
            spec = (
                entree
                if isinstance(entree, dict)
                else entree.model_dump(mode="json")
            )
            graphiques = [
                existant
                for existant in graphiques
                if (existant.get("id") if isinstance(existant, dict) else None)
                != spec.get("id")
            ] + [spec]

    corps = assembler(morceaux)
    sortie = _rendre_et_deposer_professionnel(ctx, 
        {**meta, "format": format_document, "contenu_html": corps, "graphiques": graphiques}
    )
    try:
        _ecrire_source(
            racine,
            sortie["document_id"],
            corps=corps,
            meta={**meta, "format": format_document},
            graphiques=graphiques,
            principal_id=str(identifiant_source),
            jumeau_id=None,
        )
    except OSError:
        # Le livrable est déposé : une source non réécrite se retrouve
        # depuis la famille de versions au prochain correctif.
        pass
    sortie["action"] = action
    sortie["sections"] = len(morceaux)
    sortie["sections_modifiees"] = [index] if index is not None else [len(morceaux)]
    return sortie


def _refuser_generation_si_modele(ctx: Any, payload: dict[str, Any]) -> None:
    """Interdit un document neuf quand un modèle doit être copié puis édité.

    Règle CARSO portée par le service, pas par le prompt : un document
    rattaché à une offre, une mission ou une session dont l'organisation a
    un modèle ne se génère pas — il se copie (``copier_modele_document``)
    et s'édite. Sans ancre, le document est autonome et rien ne change.
    """
    from app.application.services.moteur_documentaire_service import ANCRES_MODELE

    ancre = payload.get("ancre")
    entity_id = payload.get("entity_id")
    if str(ancre or "") not in ANCRES_MODELE or not entity_id:
        # Une ancre ``organisation`` (ou aucune ancre) ne reçoit pas de copie
        # de modèle : la chaîne modèle ne s'y applique pas.
        return
    ctx.moteur_documentaire().exiger_copie_modele(
        str(ancre),
        ctx.uuid(entity_id, "entity_id"),
        type_document=payload.get("type_document"),
    )


def _specs_graphiques(bruts: Any) -> dict[str, dict[str, Any]]:
    """Convertit les entrées du tool en specs de ``render_chart`` (ordre conservé).

    C'est ici que la forme « outil » (séries nommées en liste, valeurs séparées)
    rejoint la forme « moteur » attendue par ``graphiques.render_chart`` : un seul
    point de traduction, plutôt que deux vocabulaires qui divergent.

    Raises:
        ValidationError: deux graphiques partagent le même ``id`` (l'insertion
            serait ambiguë : le moteur refuserait la seconde ancre).
    """
    specs: dict[str, dict[str, Any]] = {}
    for brut in bruts or []:
        entree = brut if isinstance(brut, dict) else brut.model_dump()
        identifiant = str(entree.get("id") or "").strip()
        if not identifiant:
            raise ValidationError("Chaque graphique doit porter un 'id' non vide.")
        if identifiant in specs:
            raise ValidationError(
                f"Identifiant de graphique en double : {identifiant!r}",
                details={"ids": sorted(specs)},
            )
        spec: dict[str, Any] = {
            "type": entree.get("type"),
            "labels": list(entree.get("labels") or []),
            "title": entree.get("titre") or "",
            "y_label": entree.get("y_label") or "",
            "legende": entree.get("legende") or "",
            "description": entree.get("legende") or entree.get("titre") or "",
            "width": entree.get("largeur", 640),
            "height": entree.get("hauteur", 400),
        }
        series = entree.get("series")
        if series:
            spec["series"] = {
                str(s.get("nom")): list(s.get("valeurs") or [])
                for s in (s if isinstance(s, dict) else s.model_dump() for s in series)
            }
        if entree.get("valeurs") is not None:
            spec["values"] = list(entree["valeurs"])
        specs[identifiant] = spec
    return specs


def _specs_illustrations(bruts: Any) -> dict[str, dict[str, Any]]:
    """Convertit les entrées du tool en specs de ``illustrations.render_illustration``.

    Même rôle que ``_specs_graphiques`` : un seul point de traduction entre la
    forme « outil » (items en liste) et la forme « moteur » où chaque spec est
    rangée sous son identifiant d'ancre.

    Raises:
        ValidationError: identifiant d'illustration vide ou en double.
    """
    specs: dict[str, dict[str, Any]] = {}
    for brut in bruts or []:
        entree = brut if isinstance(brut, dict) else brut.model_dump()
        identifiant = str(entree.get("id") or "").strip()
        if not identifiant:
            raise ValidationError("Chaque illustration doit porter un 'id' non vide.")
        if identifiant in specs:
            raise ValidationError(
                f"Identifiant d'illustration en double : {identifiant!r}",
                details={"ids": sorted(specs)},
            )
        specs[identifiant] = {
            "type": entree.get("type"),
            "titre": entree.get("titre") or "",
            "legende": entree.get("legende") or "",
            "items": [
                item if isinstance(item, dict) else item.model_dump()
                for item in entree.get("items") or []
            ],
        }
    return specs


def _spec_depuis(payload: dict[str, Any]) -> tuple[Any, list[str]]:
    """Construit un ``DocumentSpec`` depuis l'entrée du modèle.

    Les erreurs de schéma sont reformulées en liste champ/problème : l'agent
    corrige précisément au lieu de deviner ce qui n'a pas plu.

    Les images inline trop grandes sont réduites **ici**, avant que le spec
    n'existe : c'est le seul point où l'agent fournit ses données, donc le seul
    où la transformation peut être constatée et **signalée** (INC-134).

    Returns:
        Le spec validé et les phrases décrivant les images réduites (vide si
        aucune ne dépassait les bornes utiles).
    """
    from pydantic import ValidationError as PydanticValidationError

    from app.documents.images_inline import reduire_blocs
    from app.documents.styles import style_par_nom

    donnees = payload.get("document_spec")
    if not isinstance(donnees, dict):
        raise ValidationError(
            "document_spec doit être un objet : {'metadata': {...}, 'blocs': [...]}"
        )
    donnees = dict(donnees)
    reductions: list[str] = []
    if "blocs" in donnees:
        donnees["blocs"], reductions = reduire_blocs(donnees["blocs"])
    if payload.get("style") and not donnees.get("style"):
        donnees["style"] = style_par_nom(str(payload["style"])).model_dump()
    try:
        return DocumentSpec.model_validate(donnees), reductions
    except PydanticValidationError as erreur:
        problemes = [
            {"champ": ".".join(str(partie) for partie in detail["loc"]), "probleme": detail["msg"]}
            for detail in erreur.errors()[:15]
        ]
        msg = "Document structuré invalide : corriger les champs signalés"
        raise ValidationError(msg, details={"erreurs": problemes}) from erreur


def _valider_spec(spec: Any, profil: Any, *, portees: Any = None) -> Any:
    """Valide un spec (profil nommé, ou déduit du document)."""
    from app.documents.profiles import profil_par_nom
    from app.documents.validation_document import valider_document

    return valider_document(
        spec,
        profil=profil_par_nom(str(profil)) if profil else None,
        portees=tuple(portees) if portees else None,
    )


def _contrat_validation(rapport: Any) -> dict[str, Any]:
    """Contrat de retour de validation : état par portée + constats lisibles."""
    return {
        "ok": rapport.ok,
        "structure": rapport.par_portee.get("structure", "skipped"),
        "contenu": rapport.par_portee.get("contenu", "skipped"),
        "style": rapport.par_portee.get("style", "skipped"),
        "profil": rapport.resume.get("profil"),
        "nb_mots": rapport.resume.get("nb_mots"),
        "erreurs": rapport.messages("erreur")[:15],
        "avertissements": rapport.messages("avertissement")[:15],
    }


def _message_livrable(sortie: dict[str, Any], conforme: bool) -> str:
    """Message de retour d'une génération structurée (ce qui reste à faire).

    Un livrable déposé **sans ancre** reste dans le dossier du compte : le dire
    explicitement, faute de quoi l'agent croit le document rangé dans un
    dossier métier et n'essaie jamais de le rattacher.
    """
    suite = (
        "render_document puis afficher_apercu_document pour montrer le rendu, "
        "et proposer_telechargement_document pour le fichier."
    )
    if sortie.get("non_classe"):
        base = (
            f"Document déposé en statut '{sortie.get('statut')}' sans "
            f"rattachement métier (dossier personnel {sortie.get('document_id')}). "
            f"Validation humaine requise. Étape suivante : appeler {suite}"
        )
    else:
        base = (
            f"Document déposé en statut '{sortie.get('statut')}' "
            f"(validation humaine requise). Étape suivante : appeler {suite}"
        )
    if conforme:
        return base
    return (
        f"ATTENTION : le document n'est PAS conforme aux attentes de son profil. "
        f"{base} Corriger d'abord les erreurs listées dans 'validation'."
    )


def _sans_base64(valeur: Any) -> Any:
    """Copie d'un dump **sans** les charges base64 (retour d'outil borné).

    Le spec renvoyé à l'agent sert de point de départ aux corrections ciblées ;
    une image inline de plusieurs mégaoctets n'a rien à y faire, le livrable
    généré la contient déjà. L'avertissement du tool dit quoi renvoyer.
    """
    if isinstance(valeur, dict):
        return {cle: _sans_base64(v) for cle, v in valeur.items() if cle != "base64"}
    if isinstance(valeur, list):
        return [_sans_base64(v) for v in valeur]
    return valeur


def _nombre_images_inline(valeur: Any) -> int:
    """Nombre de charges base64 présentes dans un dump de spec (récursif)."""
    if isinstance(valeur, dict):
        return sum(
            (1 if cle == "base64" and v else 0) + _nombre_images_inline(v)
            for cle, v in valeur.items()
        )
    if isinstance(valeur, list):
        return sum(_nombre_images_inline(v) for v in valeur)
    return 0
