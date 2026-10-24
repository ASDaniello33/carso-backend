"""Alias de page — ``@MissionActuel``, ``@OffreActuel``, ``@SessionActuel``.

Trois garanties, vérifiées sans navigateur et sans modèle :

1. la page déclarée par l'interface est lue, puis **validée** (type fermé,
   UUID réel) — un identifiant mal formé n'est jamais repris ;
2. les alias sont remplacés par l'identifiant réel dans le **dernier** message
   utilisateur, et un alias sans fiche devient un constat, pas un silence ;
3. le bloc de prompt n'est ajouté qu'aux agents qui ont la recherche web et les
   alias — jamais un mode d'emploi d'un outil absent.
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from app.agents.catalogue import AGENT_DEFINITIONS
from app.agents.harness import build_system_prompt
from app.agents.page_courante import (
    BLOC_PAGE_COURANTE,
    DESCRIPTION_CONTEXTE,
    PageCourante,
    entete_page,
    injecter_page_dans_payload,
    page_depuis_payload,
)
from app.api.agui_pieces_jointes import reecrire_corps

MISSION = "3f2a9c1e-4b7d-4e2a-9c5f-1a2b3c4d5e6f"
OFFRE = "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d"
SESSION = "0b1c2d3e-4f5a-4b6c-9d7e-8f9a0b1c2d3e"


def _payload(
    texte: str = "Génère la fiche technique de @MissionActuel.",
    *,
    page: dict[str, Any] | None = None,
) -> dict[str, Any]:
    contextes = []
    if page is not None:
        contextes.append(
            {
                "description": DESCRIPTION_CONTEXTE,
                "value": json.dumps(page, ensure_ascii=False),
            }
        )
    return {
        "threadId": "fil-1",
        "runId": "run-1",
        "context": contextes,
        "messages": [{"id": "m1", "role": "user", "content": texte}],
    }


def _page(type_: str, identifiant: str | None, route: str) -> dict[str, Any]:
    return {"type": type_, "id": identifiant, "route": route, "alias": "@PageActuel"}


# --- Lecture de la page déclarée ---------------------------------------------


def test_lit_une_mission_ouverte() -> None:
    page = page_depuis_payload(_payload(page=_page("mission", MISSION, f"/missions/{MISSION}")))
    assert page == PageCourante("mission", MISSION, f"/missions/{MISSION}")


def test_refuse_un_identifiant_mal_forme() -> None:
    """Un UUID inventé ne devient jamais un référent."""
    assert page_depuis_payload(_payload(page=_page("mission", "pas-un-uuid", "/missions/x"))) is None


def test_refuse_un_type_inconnu() -> None:
    assert page_depuis_payload(_payload(page=_page("facture", MISSION, "/factures/1"))) is None


def test_ignore_un_item_d_une_autre_description() -> None:
    payload = _payload(page=_page("mission", MISSION, f"/missions/{MISSION}"))
    payload["context"][0]["description"] = "Autre chose"
    assert page_depuis_payload(payload) is None


def test_refuse_une_route_non_absolue() -> None:
    page = page_depuis_payload(_payload(page=_page("page", None, "missions/1")))
    assert page is not None
    assert page.route == ""  # la route est abandonnée, la page reste connue


# --- En-tête de page ----------------------------------------------------------


def test_entete_porte_le_type_l_uuid_et_la_route() -> None:
    page = PageCourante("mission", MISSION, f"/missions/{MISSION}")
    entete = entete_page(page)
    assert entete == f"[Page ouverte : mission {MISSION} (route /missions/{MISSION})]"


def test_entete_offre_et_session() -> None:
    assert "offre de formation" in entete_page(
        PageCourante("offre", OFFRE, f"/offres/{OFFRE}")
    )
    assert "session" in entete_page(
        PageCourante("session", SESSION, f"/sessions/{SESSION}")
    )


def test_page_sans_entite_n_a_pas_d_uuid_dans_l_entete() -> None:
    entete = entete_page(PageCourante("page", None, "/dashboard"))
    assert "page (route /dashboard)" in entete


# --- En-tête du message -------------------------------------------------------


def test_entete_ajoute_sans_toucher_au_texte() -> None:
    """Le message de l'utilisateur reste **exactement** ce qu'il a tapé."""
    payload = _payload(page=_page("mission", MISSION, f"/missions/{MISSION}"))
    sortie = injecter_page_dans_payload(payload)

    assert sortie is not payload
    contenu = sortie["messages"][0]["content"]
    lignes = contenu.splitlines()
    assert lignes[0] == f"[Page ouverte : mission {MISSION} (route /missions/{MISSION})]"
    assert lignes[1] == "Génère la fiche technique de @MissionActuel."


def test_message_sans_page_reste_intact() -> None:
    payload = _payload("Bonjour, où en sont les missions ?")
    assert injecter_page_dans_payload(payload) is payload


def test_alias_sans_page_reste_intact() -> None:
    """Sans fiche ouverte, le message n'est pas modifié : le prompt dit quoi faire."""
    payload = _payload("Rattache le document à @MissionActuel")
    assert injecter_page_dans_payload(payload) is payload


def test_contenu_en_parties_de_texte_recoit_l_entete() -> None:
    """Un message AG-UI en parties (sans pièce jointe) reçoit l'en-tête aussi."""
    payload = _payload(page=_page("offre", OFFRE, f"/offres/{OFFRE}"))
    payload["messages"][0]["content"] = [
        {"type": "text", "text": "Ajoute le CV à @OffreActuel."}
    ]
    sortie = injecter_page_dans_payload(payload)
    contenu = sortie["messages"][0]["content"]
    assert contenu[0]["text"].startswith("[Page ouverte : offre de formation")
    assert contenu[1]["text"] == "Ajoute le CV à @OffreActuel."


def test_entete_deja_present_n_est_pas_double() -> None:
    payload = _payload(page=_page("mission", MISSION, f"/missions/{MISSION}"))
    une_fois = injecter_page_dans_payload(payload)
    deux_fois = injecter_page_dans_payload(une_fois)
    assert deux_fois["messages"][0]["content"].count("[Page ouverte") == 1


def test_les_messages_anciens_ne_sont_pas_reecrits() -> None:
    payload = _payload(page=_page("mission", MISSION, f"/missions/{MISSION}"))
    payload["messages"].insert(
        0, {"id": "m0", "role": "user", "content": "Ancien @MissionActuel"}
    )
    sortie = injecter_page_dans_payload(payload)
    assert sortie["messages"][0]["content"] == "Ancien @MissionActuel"
    assert sortie["messages"][1]["content"].startswith("[Page ouverte")


def test_corps_agui_reecrit_de_bout_en_bout() -> None:
    """Le middleware AG-UI livre au modèle un message déjà résolu."""
    corps = json.dumps(_payload(page=_page("session", SESSION, f"/sessions/{SESSION}")))
    nouveau = json.loads(reecrire_corps(corps.encode("utf-8")))
    contenu = nouveau["messages"][0]["content"]
    assert SESSION in contenu
    assert "[Page ouverte : session" in contenu


# --- Prompt -------------------------------------------------------------------


def _definition(agent_id: str) -> Any:
    return next(d for d in AGENT_DEFINITIONS if d.agent_id == agent_id)


def test_bloc_page_dans_le_prompt_commun() -> None:
    systeme = build_system_prompt(_definition("agent_generaliste_readonly"))
    assert BLOC_PAGE_COURANTE in systeme
    assert "@MissionActuel" in systeme


def test_bloc_recherche_seulement_pour_un_agent_qui_a_l_outil() -> None:
    avec = _definition("agent_generaliste_readonly")
    assert "langsearch_web_search" in avec.allowed_tools()
    assert "Recherche web : quand, combien, comment" in build_system_prompt(avec)

    sans = replace(avec, tools=tuple(t for t in avec.tools if t != "langsearch_web_search"))
    assert "Recherche web : quand, combien, comment" not in build_system_prompt(sans)


def test_paliers_de_recherche_dans_le_prompt() -> None:
    systeme = build_system_prompt(_definition("agent_personnel"))
    assert "8 appels" in systeme
    assert "15 au maximum" in systeme
    assert "25 au maximum" in systeme
    assert "requetes" in systeme
