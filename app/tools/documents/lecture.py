"""Lecture et inspection des documents déposés.

Handlers qui lisent les métadonnées, un aperçu de texte, une plage, un
élément précis ou une image, et qui bornent une structure trop grosse
avant de la rendre au modèle. Aucun de ces handlers n'écrit : ils
exigent ``document_read`` et délèguent au service documentaire.

Extrait de ``build_document_tools`` sans modification de contenu : la
logique métier reste celle des outils, seule la portée change.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.core.errors import ValidationError
from app.documents.csv_lecture import lire_csv, lire_csv_plage
from app.documents.recherche import chercher_dans_csv
from app.tools.documents.schemas import APERCU_EXTRACTION, APERCU_TEXTE


def _refuser_options_csv(
    extension: str, payload: dict[str, Any], cles: tuple[str, ...]
) -> None:
    """Refuse les options propres au CSV sur un autre format.

    ``separateur``, ``encodage``, ``max_lignes`` et ``colonne`` n'agissent que
    sur un CSV. Les ignorer en silence ferait croire à l'agent qu'ils ont
    produit l'effet demandé — le défaut exact qu'on cherche à supprimer : un
    appel qui « réussit » sans faire ce qui était demandé.

    Raises:
        ValidationError: option fournie sur un document d'un autre format.
    """
    if extension == "csv":
        return
    fournies = [cle for cle in cles if payload.get(cle) not in (None, "")]
    if fournies:
        raise ValidationError(
            f"{', '.join(fournies)} ne s'appliquent qu'à un CSV (ce document est un "
            f"{extension or 'fichier sans extension'})",
            details={"options": fournies, "format": extension},
        )


def _lire(ctx: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Lit un document : métadonnées **et** aperçu borné de son texte.

    Le nom du tool dit « lire » : ne rendre que les métadonnées faisait
    relire l'agent en boucle (incrément 53). L'aperçu reste borné
    (``APERCU_TEXTE``) et dit s'il est tronqué ; pour un contenu précis,
    l'agent enchaîne ``chercher_dans_document`` puis
    ``lire_element_document`` (``hit['relire']``).
    """
    ctx.policy.require(ctx.identite.agent_id, "document_read")
    from app.documents.extraction import supported_extensions

    document_id = ctx.uuid(payload["document_id"], "document_id")
    service = ctx.service_factory()
    document = service.obtenir(document_id)
    _nom_fichier, chemin = service.telecharger(document_id)
    extension = chemin.suffix.lower().lstrip(".")
    _refuser_options_csv(extension, payload, ("separateur", "encodage", "max_lignes"))
    sortie: dict[str, Any] = {
        "document_id": str(document.id),
        "nom": document.nom,
        "statut": document.statut,
        "type_document": document.type_document,
        "mime_type": document.mime_type,
        "version": document.version,
        "taille_octets": chemin.stat().st_size,
    }
    if extension not in supported_extensions():
        return {
            **sortie,
            "texte_lisible": False,
            "apercu_texte": None,
            "message": (
                f"Aucun extracteur de texte pour .{extension or '?'} : le "
                "document existe, son contenu n'est pas lisible par les outils."
            ),
        }
    if extension == "csv":
        # Un CSV n'est pas de la prose : l'aperçu texte de 4 000 caractères
        # (« a;b;c ») ne donnait ni colonnes nommées ni lignes citables, et
        # aucun outil ne savait ensuite le fouiller. On rend la structure.
        table = lire_csv(
            chemin,
            separateur=payload.get("separateur"),
            encodage=payload.get("encodage"),
            max_lignes=payload.get("max_lignes"),
        )
        sortie.update(
            {
                "texte_lisible": True,
                "apercu_texte": None,
                "apercu_tronque": table["tronque"],
                "colonnes": table["colonnes"],
                "separateur": table["separateur"],
                "encodage": table["encodage"],
                "nb_lignes": table["nb_lignes"],
                "nb_donnees": table["nb_donnees"],
                "nb_colonnes": table["nb_colonnes"],
                "apercu_lignes": table["apercu_lignes"],
                "problemes": table["problemes"],
            }
        )
        sortie["message"] = (
            "CSV lu PAR COLONNES : ``apercu_lignes`` rend les premières lignes en "
            "dictionnaires {colonne: valeur} (repère ``ligne`` = ligne du fichier, "
            "en-tête = ligne 1). Pour une valeur précise : chercher_dans_document "
            "(``mot_cle``, et ``colonne`` pour restreindre) puis "
            "lire_element_document avec hit['relire'] ; read_document_range pour "
            "une plage de lignes — jamais relire le fichier entier. "
            "``separateur``/``encodage`` annoncent la détection retenue, "
            "``problemes`` signale une ligne de largeur inégale (séparateur mal "
            "détecté ? le passer explicitement)."
        )
        return sortie

    extrait = service.extraire_texte(document_id, max_chars=APERCU_EXTRACTION)
    apercu = extrait.texte[:APERCU_TEXTE]
    sortie.update(
        {
            "texte_lisible": True,
            "nb_caracteres": extrait.nb_caracteres,
            "nb_pages": extrait.nb_pages,
            "nb_feuilles": extrait.nb_feuilles,
            "apercu_tronque": extrait.tronque or len(apercu) < len(extrait.texte),
            "apercu_texte": apercu,
            "message": (
                "Aperçu borné du DÉBUT du document (tableaux compris pour un "
                "DOCX). Pour un contenu précis : chercher_dans_document "
                "(mot-clé) puis lire_element_document avec hit['relire'] ; "
                "read_document_range pour une plage d'éléments."
            ),
        }
    )
    if extension == "xlsx":
        # Un classeur se lit par feuilles, lignes et en-tête — pas comme un
        # texte continu : la structure est rendue avec l'aperçu.
        sortie["structure_feuilles"] = _structure_feuilles_xlsx(chemin)
        sortie["message"] = (
            "Classeur Excel : ``structure_feuilles`` décrit chaque feuille "
            "(nom, lignes, colonnes, plage, en-tête). Ensuite : "
            "``get_document_structure`` pour le détail, "
            "``chercher_dans_document`` pour localiser une ligne, puis "
            "``read_document_range`` / ``lire_element_document`` sur les "
            "lignes utiles — jamais le classeur entier. ``apercu_texte`` "
            "est borné et rend les valeurs mises en cache (formules non "
            "recalculées)."
        )
    return sortie


def _lire_image(ctx: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Rend une image **visible** au modèle et renvoie son contenu base64.

    Le texte ne suffit pas : un scan, une capture ou un schéma n'ont pas
    d'extraction fiable. L'image est lue par le service documentaire (donc
    bornée au périmètre du compte), réduite si nécessaire, puis renvoyée
    dans le résultat du tool — le pont LangChain la transforme en contenu
    multimodal (``app/tools/base``).

    **Toujours actif** (constat 09/11) : l'outil ne refuse plus quand
    ``AGENT_VISION=false``. Il rend l'image et sa valeur base64 dans
    ``images[]``, et joint dans ``vision_activee``/``message`` la vérité sur
    ce qui est réellement transmis. Un refus dur privait l'utilisateur de
    l'image alors que le contenu est disponible et borné au compte ; le
    mensonge silencieux, lui, est écarté par le message. Le base64 ne
    voyage jamais en texte JSON — il part en bloc ``image_url``, sinon il
    consommerait des centaines de milliers de tokens.
    """
    ctx.policy.require(ctx.identite.agent_id, "document_read")
    from app.core.config import get_settings
    from app.documents.images import preparer_image

    document_id = ctx.uuid(payload["document_id"], "document_id")
    service = ctx.service_factory()
    document = service.obtenir(document_id)
    mime = (document.mime_type or "").lower()
    if not mime.startswith("image/"):
        raise ValidationError(
            "Ce document n'est pas une image : utiliser lire_document "
            "(texte) ou render_document (pages PDF en images).",
            details={"document_id": str(document_id), "mime_type": mime},
        )
    _nom_fichier, chemin = service.telecharger(document_id)
    image = preparer_image(chemin, mime_type=mime)
    # Déclaratif, jamais deviné : le provider peut accepter la requête et
    # ignorer l'image (constat du 08/11, carré rouge décrit « Inconnue »).
    vision_activee = get_settings().agent_vision
    message = (
        "Image fournie pour observation directe : décris ce que tu "
        "vois et lis ce qui est réellement lisible. Une valeur floue "
        "se signale comme incertaine, elle ne se devine pas."
    )
    if image.transformee:
        message += " L'image a été réduite : un petit texte peut avoir perdu en netteté."
    if not vision_activee:
        message += (
            " Attention : la vision du modèle est désactivée "
            "(AGENT_VISION=false). L'image et sa valeur base64 sont "
            "fournies, mais tu ne peux pas te fier à ce que tu crois "
            "voir : ne décris jamais son contenu de mémoire, et indique "
            "à l'utilisateur que la lecture visuelle demande un modèle "
            "multimodal avec AGENT_VISION=true."
        )
    return {
        "document_id": str(document.id),
        "nom": document.nom,
        "type_document": document.type_document,
        "mime_type": image.mime_type,
        "largeur": image.largeur,
        "hauteur": image.hauteur,
        "taille_octets": image.octets,
        "reduite": image.transformee,
        "vision_activee": vision_activee,
        "message": message,
        "images": [
            {
                "mime": image.mime_type,
                "base64": image.base64,
                "legende": f"Image « {document.nom} »",
            }
        ],
    }


def _inspecter(ctx: Any, payload: dict[str, Any]) -> dict[str, Any]:
    ctx.policy.require(ctx.identite.agent_id, "document_read")
    from app.documents.inspection import structure_document

    service = ctx.service_factory()
    document_id = ctx.uuid(payload["document_id"], "document_id")
    document = service.obtenir(document_id)
    _nom, chemin = service.telecharger(document_id)
    suffixe = chemin.suffix.lower()
    if suffixe in {".docx", ".pdf", ".xlsx"}:
        # Mémoïsé : un même document relu dans le même tour ne repaie pas
        # l'analyse (pdfplumber sur un PDF coûtait plusieurs secondes).
        structure = structure_document(chemin)
    elif suffixe == ".csv":
        # Un CSV n'a ni styles ni pages : sa structure est une table —
        # colonnes, exemples réels et dimensions, lues en flux.
        from app.documents.csv_lecture import structure_csv

        structure = {
            **structure_csv(chemin),
            "unite_pagination": "lignes",
            "nb_pages": None,
        }
    else:
        raise ValidationError(f"Inspection non supportée pour {document.nom}")
    # Le transport vers le modèle est borné (``_borner_structure``) : une
    # carte de 80 000 caractères faisait cesser l'émission du fournisseur au
    # tour suivant. L'inspection elle-même reste complète (API et aperçu
    # humain gardent toute la structure).
    return _borner_structure(
        {"document_id": str(document.id), "nom": document.nom, **structure}
    )


def _lire_plage(ctx: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Lit une ou plusieurs plages d'un document (docx, pdf, xlsx).

    ``plages`` est une **liste** : plusieurs plages sont lues en un appel
    et rendues sous ``plages`` (une entrée par plage, dans l'ordre
    demandé) — même contrat groupé que ``lire_element_document`` pour ses
    index et ``chercher_dans_document`` pour ses mots-clés. Une plage en
    échec est **dite dans son entrée** sans faire perdre les autres
    lectures ; une cause documentaire (PDF chiffré, feuille inconnue…)
    vaut pour toutes et remonte une seule fois. Une plage seule (ou les
    bornes scalaires ``from``/``to``) garde le retour unitaire d'origine.
    """
    ctx.policy.require(ctx.identite.agent_id, "document_read")
    from app.documents.inspection import lire_docx_plage, lire_pdf_plage, lire_xlsx_plage

    service = ctx.service_factory()
    document_id = ctx.uuid(payload["document_id"], "document_id")
    _nom, chemin = service.telecharger(document_id)
    suffixe = chemin.suffix.lower()

    def lire_une(debut: int | None, fin: int | None) -> dict[str, Any]:
        if suffixe == ".csv":
            return lire_csv_plage(chemin, from_row=debut, to_row=fin)
        if suffixe == ".docx":
            return lire_docx_plage(chemin, from_page=debut, to_page=fin)
        if suffixe == ".pdf":
            return lire_pdf_plage(chemin, from_page=debut, to_page=fin)
        if suffixe == ".xlsx":
            return lire_xlsx_plage(
                chemin, sheet=payload.get("sheet"), from_row=debut, to_row=fin
            )
        raise ValidationError(f"Lecture de plage non supportée pour {chemin.suffix}")

    plages = payload.get("plages")
    if plages:
        # Ambiguïté refusée, jamais devinée (même règle que sous_index avec
        # plusieurs index) : la liste OU les bornes scalaires, jamais les deux.
        if any(
            payload.get(cle) is not None
            for cle in ("from_index", "to_index", "from_page", "to_page")
        ):
            raise ValidationError(
                "'plages' et les bornes scalaires sont exclusifs : donner "
                "plages=[{from_page: ..., to_page: ...}, ...] OU from_page/"
                "to_page, jamais les deux",
                details={"nb_plages": len(plages)},
            )
        if len(plages) == 1:
            # Une seule plage : retour unitaire d'origine (rétro-compatibilité
            # des appels et des locators existants).
            payload = {
                **payload,
                **{cle: valeur for cle, valeur in plages[0].items() if valeur is not None},
            }
            plages = None

    if not plages:
        # ``from_page``/``to_page`` sont des alias : même repère que
        # ``from_index``/``to_index`` (pages PDF, paragraphes DOCX, lignes
        # XLSX). L'ancienne forme reste acceptée pour ne rien casser.
        debut = payload.get("from_index")
        if debut is None:
            debut = payload.get("from_page")
        fin = payload.get("to_index")
        if fin is None:
            fin = payload.get("to_page")
        return lire_une(debut, fin)

    # Lecture groupée : une entrée par plage demandée.
    document = service.obtenir(document_id)
    entete: dict[str, Any] = {
        "document_id": str(document.id),
        "nom": document.nom,
        "statut": document.statut,
        "type_document": document.type_document,
        "feuille": payload.get("sheet"),
    }
    lectures: list[dict[str, Any]] = []
    for entree in plages:
        debut = entree.get("from_index")
        if debut is None:
            debut = entree.get("from_page")
        fin = entree.get("to_index")
        if fin is None:
            fin = entree.get("to_page")
        bornes = {"from_index": debut, "to_index": fin}
        if debut is None and fin is None:
            # Une plage sans bornes lirait le document entier : dans une
            # liste, ce n'est jamais ce que l'agent veut — refus explicite.
            lectures.append(
                {
                    "plage": bornes,
                    "lu": False,
                    "erreur": (
                        "Chaque plage de 'plages' doit porter ses bornes "
                        "(from_page/to_page ou from_index/to_index)"
                    ),
                }
            )
            continue
        try:
            resultat = lire_une(debut, fin)
        except ValidationError as refus:
            # L'échec d'une plage est **dit** dans son entrée (hors
            # document, plage inversée…) sans faire perdre les autres.
            lectures.append({"plage": bornes, "lu": False, "erreur": str(refus)})
            continue
        lectures.append({"plage": bornes, "lu": True, **resultat})
    nb_lues = sum(1 for lecture in lectures if lecture["lu"])
    unite = next(
        (lecture.get("unite") for lecture in lectures if lecture.get("unite")), None
    )
    return {
        **entete,
        "unite": unite,
        "nb_plages": len(lectures),
        "nb_lues": nb_lues,
        "nb_echecs": len(lectures) - nb_lues,
        "plages": lectures,
        "message": (
            "Une entrée de 'plages' par plage demandée, dans l'ordre : "
            "chacune garde le contrat d'un appel unitaire (unite, debut, "
            "fin, total, elements/texte). Une entrée 'lu': false porte "
            "'erreur' (les autres lectures restent valables). Une plage "
            "seule rend le retour unitaire d'origine."
        ),
    }


def _chercher(ctx: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Recherche ciblée : pages + extraits bornés (remplace le shell/grep).

    Aucun chemin n'est manipulé ici : le service téléverse dans le stockage
    contrôlé, ``app.documents.recherche`` fouille le fichier en mémoire et
    borne le retour (extraits courts, plafond de résultats).

    ``mot_cle`` est une **liste** : plusieurs mots-clés sont fouillés en un
    seul appel et rendus sous la même forme — une entrée par mot-clé, dans
    l'ordre demandé (``recherches``). Un mot-clé unique garde le retour
    unitaire d'origine (rétro-compatibilité des appels et des tests).
    Aucune erreur n'est avalée : une cause **documentaire** (PDF chiffré,
    feuille inconnue…) vaut pour tous les mots-clés et remonte donc une
    seule fois, pas N fois.
    """
    ctx.policy.require(ctx.identite.agent_id, "document_read")
    from app.documents.recherche import (
        chercher_dans_docx,
        chercher_dans_pdf,
        chercher_dans_xlsx,
    )

    service = ctx.service_factory()
    document_id = ctx.uuid(payload["document_id"], "document_id")
    document = service.obtenir(document_id)
    _nom, chemin = service.telecharger(document_id)
    suffixe = chemin.suffix.lower()
    mots_cles: list[str] = list(payload.get("mot_cle") or [])
    _refuser_options_csv(suffixe.lstrip("."), payload, ("colonne", "separateur"))
    options = {"max_resultats": payload.get("max_resultats")}

    def fouiller(mot_cle: str) -> dict[str, Any]:
        if suffixe == ".csv":
            return chercher_dans_csv(
                chemin,
                mot_cle,
                colonne=payload.get("colonne"),
                separateur=payload.get("separateur"),
                **options,
            )
        if suffixe == ".docx":
            return chercher_dans_docx(chemin, mot_cle, **options)
        if suffixe == ".pdf":
            return chercher_dans_pdf(chemin, mot_cle, **options)
        if suffixe == ".xlsx":
            return chercher_dans_xlsx(
                chemin,
                mot_cle,
                feuille=payload.get("feuille"),
                **options,
            )
        raise ValidationError(
            f"Recherche non supportée pour {document.nom} "
            "(formats acceptés : docx, pdf, xlsx, csv)"
        )

    def enrichir(resultat: dict[str, Any]) -> dict[str, Any]:
        """Ajoute ``document_id`` aux locators ``relire`` des hits.

        L'agent appelle alors lire_element_document **tel quel**, sans
        reconstruire les arguments (c'est ce qui faisait reboucler les
        lectures : traduire « page » en appel d'outil était laissé au modèle).
        """
        for hit in resultat.get("resultats") or []:
            relire = hit.get("relire")
            if isinstance(relire, dict):
                relire["arguments"] = {
                    "document_id": str(document.id),
                    **(relire.get("arguments") or {}),
                }
        return resultat

    commun = {
        "document_id": str(document.id),
        "nom": document.nom,
        "statut": document.statut,
        "type_document": document.type_document,
    }
    if len(mots_cles) == 1:
        return {**commun, **enrichir(fouiller(mots_cles[0]))}

    recherches: list[dict[str, Any]] = []
    for mot_cle in mots_cles:
        resultat = enrichir(fouiller(mot_cle))
        # Le message du moteur décrit le contrat commun : il est porté une
        # seule fois au niveau de l'appel, pas répété par mot-clé.
        recherches.append(
            {cle: valeur for cle, valeur in resultat.items() if cle != "message"}
        )
    return {
        **commun,
        "format": suffixe.lstrip("."),
        "nb_recherches": len(recherches),
        "mots_cles": list(mots_cles),
        "nb_resultats": sum(entree["nb_resultats"] for entree in recherches),
        "nb_correspondances": sum(
            entree["nb_correspondances"] for entree in recherches
        ),
        "tronque": any(entree["tronque"] for entree in recherches),
        "recherches": recherches,
        "message": (
            "Une entrée de 'recherches' par mot-clé, dans l'ordre demandé : "
            "chacune garde le contrat d'un appel unitaire (mot_cle, unite, "
            "total, nb_resultats, tronque, resultats, relire). 'tronque' au "
            "niveau de l'appel = au moins une recherche bornée ; affiner le "
            "mot-clé concerné plutôt que de relire le document en entier."
        ),
    }


def _lire_element(ctx: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Lit un ou plusieurs éléments précis d'un document (docx, pdf, xlsx).

    C'est le pendant de ``chercher_dans_document`` : un tableau, une ligne de
    tableau, un en-tête, une page, une ligne de feuille — tout élément du
    document est lisible, sans relire le document entier.

    Deux formes groupées, exclusives l'une de l'autre :

    - ``index`` : plusieurs positions, **un seul** ``type_element`` ;
    - ``cibles`` : plusieurs cibles hétérogènes, chacune avec son type, son
      index et sa ligne — un appel pour « l'en-tête du tableau 2 + les
      lignes 3 et 12 du tableau 1 + le paragraphe 8 ».

    Un index une cible en échec (hors document…) est **dit dans son entrée**
    sans faire perdre les autres lectures — même contrat que ``read_urls``
    pour ses adresses. Un index unique garde le retour unitaire d'origine
    (forme des locators ``hit['relire']``).
    """
    ctx.policy.require(ctx.identite.agent_id, "document_read")
    from app.documents.elements import lire_element

    service = ctx.service_factory()
    document_id = ctx.uuid(payload["document_id"], "document_id")
    document = service.obtenir(document_id)
    _nom, chemin = service.telecharger(document_id)
    indexes = payload.get("index")
    cibles = payload.get("cibles") or []
    type_element = payload.get("type_element") or "element"
    sous_index = payload.get("sous_index")
    ligne_debut = payload.get("ligne_debut")
    entete = {
        "document_id": str(document.id),
        "nom": document.nom,
        "statut": document.statut,
        "type_document": document.type_document,
    }

    if cibles and indexes:
        raise ValidationError(
            "'index' et 'cibles' sont exclusifs : 'cibles' porte déjà l'index "
            "de chaque élément (et son type, sa ligne)",
            details={"index": indexes, "nb_cibles": len(cibles)},
        )
    if cibles and (sous_index is not None or ligne_debut is not None):
        raise ValidationError(
            "avec 'cibles', sous_index et ligne_debut se portent DANS chaque "
            "cible (les éléments n'ont pas forcément la même ligne)",
            details={"sous_index": sous_index, "ligne_debut": ligne_debut},
        )

    def lire(
        position: int | None,
        type_lu: str = type_element,
        sous: int | None = sous_index,
        debut: int | None = ligne_debut,
        plafond: int | None = None,
    ) -> dict[str, Any]:
        return lire_element(
            chemin,
            index=position,
            type_element=type_lu,
            sous_index=sous,
            ligne_debut=debut,
            feuille=payload.get("feuille"),
            max_lignes=plafond or payload.get("max_lignes"),
            max_caracteres=payload.get("max_caracteres"),
            separateur=payload.get("separateur"),
        )

    if cibles:
        elements: list[dict[str, Any]] = []
        for numero, cible in enumerate(cibles, start=1):
            try:
                resultat = lire(
                    cible.get("index"),
                    cible.get("type_element") or type_element,
                    cible.get("sous_index"),
                    cible.get("ligne_debut"),
                    cible.get("max_lignes"),
                )
            except ValidationError as refus:
                elements.append(
                    {
                        "cible": numero,
                        "index": cible.get("index"),
                        "lu": False,
                        "erreur": str(refus),
                    }
                )
                continue
            elements.append(
                {"cible": numero, "index": cible.get("index"), "lu": True, **resultat}
            )
        nb_lus = sum(1 for element in elements if element["lu"])
        return {
            **entete,
            "nb_demandes": len(cibles),
            "nb_lus": nb_lus,
            "nb_echecs": len(elements) - nb_lus,
            "elements": elements,
            "message": (
                "Une entrée de 'elements' par cible, dans l'ordre demandé : "
                "chaque entrée reprend 'cible' (son numéro) et 'index'. Une "
                "cible 'lu': false porte 'erreur' sans faire perdre les "
                "autres lectures."
            ),
        }

    # Un index unique (ou absent : le moteur dira l'obligation) : retour
    # unitaire inchangé.
    if not indexes or len(indexes) == 1:
        return {**entete, **lire(indexes[0] if indexes else None)}

    # Lecture groupée : deux ambiguïtés refusées, jamais devinées.
    if type_element == "feuille":
        raise ValidationError(
            "type_element='feuille' décrit une feuille nommée : il n'accepte "
            "pas une liste d'index (utiliser type_element='ligne' avec les "
            "index de lignes)",
            details={"type_element": type_element, "index": indexes},
        )
    if sous_index is not None:
        raise ValidationError(
            "sous_index ne s'applique qu'à UN index : utiliser 'cibles' "
            "(une cible par ligne), ou lire le tableau avec ligne_debut",
            details={"index": indexes, "sous_index": sous_index},
        )

    elements = []
    for numero in indexes:
        try:
            resultat = lire(numero)
        except ValidationError as refus:
            # L'échec d'un index est **dit** (message exact : hors document,
            # type absent du format…) sans faire perdre les autres lectures.
            elements.append({"index": numero, "lu": False, "erreur": str(refus)})
            continue
        elements.append({"index": numero, "lu": True, **resultat})
    nb_lus = sum(1 for element in elements if element["lu"])
    return {
        **entete,
        "type_element": type_element,
        "nb_demandes": len(indexes),
        "nb_lus": nb_lus,
        "nb_echecs": len(elements) - nb_lus,
        "elements": elements,
        "message": (
            "Une entrée de 'elements' par index demandé, dans l'ordre : les "
            "extraits sont bornés (max_lignes / max_caracteres). Une entrée "
            "'lu': false porte 'erreur' (les autres lectures restent "
            "valables). Un seul index rend le retour unitaire d'origine."
        ),
    }


def _structure_feuilles_xlsx(chemin: Path) -> list[dict[str, Any]]:
    """Feuilles d'un classeur : nom, dimensions, en-tête (best-effort).

    Un classeur illisible ne fait pas échouer la lecture : l'aperçu borné reste
    disponible et le message renvoie vers ``get_document_structure``. Les
    valeurs de l'en-tête sont bornées comme le reste du retour.
    """
    from app.documents.inspection import inspecter_xlsx

    try:
        structure = inspecter_xlsx(chemin, niveau="resume")
    except ValidationError:
        return []
    return [
        {
            "nom": feuille.get("nom"),
            "lignes": feuille.get("nb_lignes"),
            "colonnes": feuille.get("nb_colonnes"),
            "plage": feuille.get("dimensions"),
            "entete": (feuille.get("entete") or [])[:20],
        }
        for feuille in structure.get("feuilles") or []
    ]


#: Budget, en caractères, du payload ``get_document_structure`` remis au modèle.
#: Mesuré sur un TDR réel de 2,3 Mo : **182 821 caractères** (~46 000 tokens)
#: pour un seul appel. Le tour suivant devenait si lourd que le fournisseur
#: cessait d'émettre — ``StreamChunkTimeoutError`` après 300 s de silence — et
#: « Réessayer » rejouait la même requête (même échec, même tirage). La
#: structure est une **carte**, pas le contenu : les comptages restent exacts,
#: les textes sont bornés, et chaque coupe est nommée dans ``limites``.
BUDGET_STRUCTURE_CARACTERES = 24_000


#: Longueurs conservées par la première passe (bloc, cellule d'en-tête, section).
_EXTRAIT_BLOC = 80


_EXTRAIT_CELLULE = 60


_EXTRAIT_SECTION = 120


#: Tableaux détaillés conservés avant réduction aux dimensions seules.
_TABLEAUX_DETAIL = 40


#: Liens et images conservés avant réduction au comptage seul.
_ANNEXES_DETAIL = 10


def _taille_payload(payload: dict[str, Any]) -> int:
    """Poids réel du payload, tel qu'il sera sérialisé pour le modèle."""
    return len(json.dumps(payload, ensure_ascii=False, default=str))


def _tronquer(valeur: Any, limite: int) -> Any:
    """Tronque un texte en le **disant** : le « … » est le repère de coupe."""
    if not isinstance(valeur, str) or len(valeur) <= limite:
        return valeur
    return valeur[:limite].rstrip() + "…"


def _reduire_textes(payload: dict[str, Any]) -> None:
    """Borne chaque texte : la carte garde le repère, pas le contenu."""
    for bloc in payload.get("hierarchie") or []:
        bloc["texte"] = _tronquer(bloc.get("texte"), _EXTRAIT_BLOC)
        if bloc.get("items"):
            bloc["items"] = [_tronquer(item, _EXTRAIT_BLOC) for item in bloc["items"]]
    for tableau in payload.get("tableaux") or []:
        tableau["entete"] = [
            _tronquer(cellule, _EXTRAIT_CELLULE)
            for cellule in tableau.get("entete") or []
        ]
    for section in payload.get("sections_detaillees") or []:
        section["entete"] = _tronquer(section.get("entete"), _EXTRAIT_SECTION)
        section["pied"] = _tronquer(section.get("pied"), _EXTRAIT_SECTION)
    for cle in ("entetes", "pieds"):
        payload[cle] = [_tronquer(t, _EXTRAIT_SECTION) for t in payload.get(cle) or []]
    for lien in payload.get("liens") or []:
        lien["url"] = _tronquer(lien.get("url"), _EXTRAIT_SECTION)


def _reduire_doublons(payload: dict[str, Any]) -> None:
    """Retire ce que l'inspection répète deux fois.

    ``sections_detaillees`` répète ``sections`` + ``entetes``/``pieds``, et
    chaque tableau figure deux fois (dans ``hierarchie`` à sa position, puis
    dans ``tableaux``). La liste détaillée est conservée ; la hiérarchie ne
    garde que le repère ``element``, qui suffit pour relire le tableau.
    """
    payload.pop("sections_detaillees", None)
    for bloc in payload.get("hierarchie") or []:
        if bloc.get("type") == "tableau":
            for cle in ("entete", "largeurs_mm", "fusions"):
                bloc.pop(cle, None)
    for tableau in (payload.get("tableaux") or [])[_TABLEAUX_DETAIL:]:
        for cle in ("entete", "largeurs_mm", "fusions"):
            tableau.pop(cle, None)


def _reduire_tableaux(payload: dict[str, Any]) -> None:
    """Tableaux réduits à leurs dimensions : le détail se lit à la demande.

    L'en-tête d'un tableau est déjà du **contenu**. Sur un appel réel, les 23
    tableaux d'un TDR pesaient 38 049 caractères à eux seuls — assez pour
    qu'après réduction de la hiérarchie le payload reste deux fois au-dessus
    du budget. Les dimensions (lignes, colonnes, fusions) suffisent à choisir
    **quel** tableau relire avec ``lire_element_document``.
    """
    for tableau in payload.get("tableaux") or []:
        for cle in ("entete", "largeurs_mm", "fusions"):
            tableau.pop(cle, None)


def _echantillonner_hierarchie(payload: dict[str, Any], pas: int) -> None:
    """Garde titres et tableaux, échantillonne le reste un bloc sur ``pas``."""
    vus = 0
    gardes: list[dict[str, Any]] = []
    for bloc in payload.get("hierarchie") or []:
        if bloc.get("type") in ("titre", "tableau"):
            gardes.append(bloc)
            continue
        if vus % pas == 0:
            gardes.append(bloc)
        vus += 1
    payload["hierarchie"] = gardes


def _reduire_annexes(payload: dict[str, Any]) -> None:
    """Liens et images ne gardent qu'un échantillon (les comptages restent)."""
    for cle in ("liens", "images"):
        entrees = payload.get(cle) or []
        if len(entrees) > _ANNEXES_DETAIL:
            payload[cle] = entrees[:_ANNEXES_DETAIL]


def _borner_structure(
    payload: dict[str, Any], budget: int = BUDGET_STRUCTURE_CARACTERES
) -> dict[str, Any]:
    """Ramène sous ``budget`` le payload de ``get_document_structure``.

    L'inspection elle-même reste **complète et exacte** (l'API et l'aperçu
    humain gardent la structure entière) : c'est son transport vers le modèle
    qui est borné. Un payload de 182 821 caractères rend le tour suivant si
    lourd que le fournisseur cesse d'émettre ; le run meurt alors en silence,
    et « Réessayer » rejoue la même requête.

    Les réductions s'appliquent par coût croissant pour l'agent et s'arrêtent
    dès que le budget est tenu ; chacune nomme sa coupe dans ``limites``
    (aucune troncature silencieuse). Les comptages exacts (``nb_elements``,
    ``nb_paragraphes``, ``nb_tableaux``, ``nb_pages``, ``unite_pagination``) et
    le repère ``element`` de chaque bloc survivent : l'agent relit n'importe
    quel emplacement avec ``chercher_dans_document`` / ``read_document_range``.
    """
    if _taille_payload(payload) <= budget:
        return payload

    limites: list[str] = payload.setdefault("limites", [])

    _reduire_textes(payload)
    limites.append(f"textes_tronques_a_{_EXTRAIT_BLOC}_caracteres")
    if _taille_payload(payload) <= budget:
        return payload

    _reduire_doublons(payload)
    limites.append("sections_detaillees_retirees_et_tableaux_non_dupliques")
    if _taille_payload(payload) <= budget:
        return payload

    _reduire_tableaux(payload)
    limites.append("tableaux_reduits_a_leurs_dimensions")
    if _taille_payload(payload) <= budget:
        return payload

    for pas in (2, 4, 8, 16, 32, 64):
        _echantillonner_hierarchie(payload, pas)
        if _taille_payload(payload) <= budget:
            limites.append(f"hierarchie_echantillonnee_un_bloc_sur_{pas}")
            return payload
    limites.append("hierarchie_echantillonnee_un_bloc_sur_64")

    _reduire_annexes(payload)
    limites.append(f"liens_et_images_limites_a_{_ANNEXES_DETAIL}")

    # Dernier recours : la carte garde un préfixe de blocs, et le dit. Le
    # repère ``element`` restant rend la suite lisible par lecture ciblée.
    hierarchie = payload.get("hierarchie") or []
    nombre = len(hierarchie)
    while _taille_payload(payload) > budget and nombre > 20:
        nombre = max(20, nombre // 2)
        payload["hierarchie"] = hierarchie[:nombre]
    if nombre < len(hierarchie):
        limites.append(f"hierarchie_tronquee_a_{nombre}_blocs")

    # Garantie finale : si même les textes pèsent trop, ils partent — il reste
    # les types, les comptages et les repères, ce qui suffit à naviguer.
    if _taille_payload(payload) > budget:
        for bloc in payload.get("hierarchie") or []:
            bloc.pop("texte", None)
            bloc.pop("items", None)
        limites.append("textes_de_hierarchie_retires_pour_tenir_dans_le_budget")
    return payload
