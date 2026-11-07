"""TemplateUnderstandingService — compréhension sémantique d'un modèle (INC-128).

Le LLM reçoit une **représentation** des blocs produits par l'analyseur (jamais le
paquet DOCX) et propose, pour chacun, un rôle : STATIC, DYNAMIC, REPEATABLE ou
CONDITIONAL, avec une clé de champ, une justification et une confiance.

Trois garde-fous, appliqués par le code et pas seulement demandés au prompt :

1. un ``block_id`` qui n'appartient pas à l'analyse est **écarté** et listé dans
   les avertissements ;
2. une ligne répétable proposée sur autre chose qu'un tableau est refusée (le
   rendu ne saurait pas la cloner) ;
3. une réponse qui n'est pas le JSON attendu est **refusée** — jamais remplacée
   par une proposition vide qui laisserait croire à une analyse réussie.

La proposition n'est jamais appliquée directement : elle alimente une
configuration que l'humain valide (``ConfigurationModele.statut``).
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.core.errors import ValidationError
from app.documents.moteur import schemas as S

__all__ = ["PROMPT_SYSTEME", "TemplateUnderstandingService", "json_depuis"]

#: Nombre de blocs envoyés au modèle. Au-delà, la représentation serait plus
#: longue que le document lui-même et le coût n'apporterait rien.
MAX_BLOCS = 160

#: Longueur de texte par bloc : assez pour juger du sens, pas pour recopier.
MAX_CARACTERES_BLOC = 600

PROMPT_SYSTEME = """Tu analyses un modèle de document professionnel fourni par une \
organisation (offre technique, offre financière, manifestation d'intérêt, rapport…).

Tu ne réécris PAS le document. Tu classes des blocs déjà extraits, en te fondant
sur leur SENS et leur fonction dans le document, jamais sur des mots-clés isolés.

Classifications autorisées :
- STATIC : contenu fixe à conserver tel quel (logo, charte, mention
  institutionnelle, clause juridique, coordonnées permanentes, signature).
- DYNAMIC : contenu qui change pour chaque nouvel appel ou projet (titre, nom du
  projet, contexte, objectifs, méthodologie, description d'activités).
- REPEATABLE : tableau dont la structure est conservée mais dont le nombre de
  lignes varie (budget, activités, livrables, membres d'équipe). Uniquement pour
  un bloc de type "tableau" ; indique les colonnes dans "colonnes".
- CONDITIONAL : bloc conservé ou supprimé selon une condition métier (section
  déplacements, partenaires, activité optionnelle).

Règles absolues :
- utilise EXCLUSIVEMENT les block_id fournis ; n'en invente aucun ;
- ne classe pas STATIC une zone qui dépend de l'appel traité ;
- ne classe pas DYNAMIC une clause institutionnelle ou une signature ;
- une présentation de l'organisation rédigée pour un appel précis est DYNAMIC ;
  une mention légale ou un slogan permanent est STATIC ;
- "field_key" est un identifiant court en minuscules, sans accent ni espace
  (ex. "titre_projet", "activites") ;
- "confiance" est un nombre entre 0 et 1, honnête : 0.5 si le bloc est ambigu.

Réponds UNIQUEMENT par un objet JSON, sans texte autour :
{"nom": "...", "type_document": "...", "champs": [
  {"block_id": "block_001", "classification": "DYNAMIC",
   "field_key": "titre_projet", "label": "Titre du projet",
   "type": "texte", "obligatoire": true,
   "instruction": "Titre professionnel adapté à l'appel",
   "colonnes": [], "raison": "...", "confiance": 0.9}
]}
Les valeurs de "type" sont : texte, paragraphe, lignes, booleen.
"""

_CLOTURE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I)


def json_depuis(texte: str) -> dict[str, Any]:
    """Extrait l'objet JSON d'une réponse de modèle (tolère les balises Markdown).

    Raises:
        ValidationError: aucune structure JSON exploitable.
    """
    nettoye = _CLOTURE.sub("", (texte or "").strip())
    debut = nettoye.find("{")
    fin = nettoye.rfind("}")
    if debut < 0 or fin <= debut:
        raise ValidationError(
            "Réponse du modèle sans objet JSON : la proposition est refusée",
            details={"debut_reponse": (texte or "")[:200]},
        )
    try:
        charge = json.loads(nettoye[debut : fin + 1])
    except json.JSONDecodeError as exc:
        raise ValidationError(
            "Réponse du modèle n'est pas un JSON valide : proposition refusée",
            details={"erreur": str(exc)[:200]},
        ) from exc
    if not isinstance(charge, dict):
        raise ValidationError("La proposition doit être un objet JSON")
    return charge


def _representation(analyse: S.AnalyseModele) -> tuple[list[dict[str, Any]], int]:
    """Représentation compacte des blocs pour le modèle (et nombre de blocs omis)."""
    blocs: list[dict[str, Any]] = []
    for bloc in analyse.blocs[:MAX_BLOCS]:
        element: dict[str, Any] = {
            "block_id": bloc.block_id,
            "partie": "corps" if bloc.partie == "word/document.xml" else bloc.partie,
            "type": bloc.type,
            "style": bloc.style.nom,
            "niveau_titre": bloc.niveau_titre,
            "texte": bloc.texte[:MAX_CARACTERES_BLOC],
        }
        if bloc.type == "tableau":
            element["colonnes"] = bloc.colonnes
            element["entete"] = [
                cellule.texte for cellule in (bloc.lignes[0].cellules if bloc.lignes else [])
            ]
            element["lignes"] = len(bloc.lignes)
            element["texte"] = bloc.texte[:MAX_CARACTERES_BLOC]
        if bloc.images:
            element["images"] = bloc.images
        blocs.append(element)
    return blocs, max(0, len(analyse.blocs) - MAX_BLOCS)


def _demande(analyse: S.AnalyseModele, blocs: list[dict[str, Any]], omis: int, nom: str,
             type_document: str | None) -> str:
    """Message utilisateur : le document analysé, tel que le modèle doit le voir."""
    entete = {
        "nom_souhaite": nom,
        "type_document": type_document,
        "sections": [
            {"entete": zone.entete, "pied": zone.pied, "marges_mm": zone.marges_mm}
            for zone in analyse.zones[:3]
        ],
        "placeholders_existants": analyse.placeholders,
        "blocs_omis": omis,
    }
    return (
        "Analyse structurelle du modèle (source de vérité) :\n"
        + json.dumps(blocs, ensure_ascii=False, indent=1)
        + "\n\nContexte du document :\n"
        + json.dumps(entete, ensure_ascii=False, indent=1)
        + "\n\nClasse chaque bloc utile. Omets les blocs vides, les sauts de page et "
        "les titres de section si tu n'as pas de clé pertinente : un bloc absent de "
        "ta réponse sera conservé tel quel."
    )


def _modele_par_defaut() -> Any:
    """Modèle de chat configuré par le déploiement (jamais un modèle inventé)."""
    from app.agents.providers import build_chat_model
    from app.core.config import get_settings

    return build_chat_model(get_settings())


class TemplateUnderstandingService:
    """Classification sémantique d'un modèle déjà analysé."""

    def __init__(self, chat_model: Any | None = None) -> None:
        self._modele = chat_model

    def proposer(
        self,
        analyse: S.AnalyseModele,
        *,
        nom: str = "",
        type_document: str | None = None,
    ) -> S.PropositionModele:
        """Propose une classification, filtrée sur les blocs réellement analysés.

        Raises:
            ValidationError: réponse absente, non JSON, ou non conforme au schéma.
            AgentConfigurationError: aucun modèle de chat configuré et aucun
                modèle injecté.
        """
        from langchain_core.messages import HumanMessage, SystemMessage

        modele = self._modele or _modele_par_defaut()
        blocs, omis = _representation(analyse)
        reponse = modele.invoke(
            [
                SystemMessage(content=PROMPT_SYSTEME),
                HumanMessage(content=_demande(analyse, blocs, omis, nom, type_document)),
            ]
        )
        contenu = getattr(reponse, "content", reponse)
        if not isinstance(contenu, str):  # contenu structuré renvoyé par certains providers
            contenu = json.dumps(contenu, ensure_ascii=False)
        try:
            proposition = S.PropositionModele.model_validate(json_depuis(contenu))
        except ValidationError:
            raise
        except Exception as exc:  # ValidationError Pydantic -> erreur explicite
            raise ValidationError(
                "Proposition du modèle non conforme au schéma attendu",
                details={"erreur": str(exc)[:300]},
            ) from exc
        return self._filtrer(analyse, proposition)

    def _filtrer(
        self, analyse: S.AnalyseModele, proposition: S.PropositionModele
    ) -> S.PropositionModele:
        """Écarte les propositions inapplicables ; le reste est conservé tel quel."""
        connus = {bloc.block_id: bloc for bloc in analyse.blocs}
        avertissements = list(proposition.avertissements)
        gardes: list[S.ChampPropose] = []
        for champ in proposition.champs:
            bloc = connus.get(champ.block_id)
            if bloc is None:
                avertissements.append(
                    f"Block_id inventé, écarté : {champ.block_id!r} "
                    f"(clé {champ.field_key!r})"
                )
                continue
            if champ.classification is S.Classification.REPEATABLE and bloc.type != "tableau":
                avertissements.append(
                    f"{champ.block_id} est un paragraphe : une ligne répétable n'a de "
                    f"sens que sur un tableau (clé {champ.field_key!r} ignorée)."
                )
                continue
            if champ.type == "lignes" and bloc.type != "tableau":
                avertissements.append(
                    f"{champ.block_id} n'est pas un tableau : type 'lignes' refusé."
                )
                continue
            if champ.confiance < 0.5:
                avertissements.append(
                    f"Classification peu sûre pour {champ.block_id} "
                    f"({champ.field_key}, confiance {champ.confiance:.2f}) : à vérifier."
                )
            gardes.append(champ)
        nom = proposition.nom or (analyse.titres[0]["texte"] if analyse.titres else "")
        return S.PropositionModele(
            nom=nom,
            type_document=proposition.type_document,
            champs=gardes,
            avertissements=avertissements,
        )
