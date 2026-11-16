"""Arguments d'outil invalides : le modèle doit savoir quoi corriger.

Constaté en run réel : `remplir_modele` appelé avec une **liste nue** au lieu de
l'objet `{ancre, entity_id, remplissages}`. LangChain valide les arguments avant
d'appeler le tool ; son message par défaut, « Tool input validation error », ne
dit ni le champ fautif ni la forme attendue — le modèle a réessayé sept fois de
suite, chacune refusée sans information.

Le message est donc construit par nous : forme attendue, champs obligatoires,
action à mener.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from app.tools.base import TypedTool, _resume_schema, to_langchain_tool


class _Remplissage(BaseModel):
    element_id: str = Field(description="Identifiant d'un élément.")
    contenu_html: str = Field(default="", description="Contenu HTML.")


class _EntreeRemplissage(BaseModel):
    """Entrée d'essai, calquée sur ``RemplirModeleInput``."""

    ancre: str = Field(description="Objet visé.")
    entity_id: str = Field(description="Identifiant de l'objet.")
    remplissages: list[_Remplissage] = Field(description="Un contenu par élément.")


def _outil() -> TypedTool:
    return TypedTool(
        name="remplir_modele",
        description="Remplit une copie de modèle.",
        input_schema=_EntreeRemplissage,
        handler=lambda payload: {"ok": True},
    )


def _contenu(resultat: Any) -> dict[str, Any]:
    """Message d'erreur d'un tool LangChain, désérialisé."""
    texte = resultat if isinstance(resultat, str) else str(resultat)
    return json.loads(texte)


def test_une_liste_nue_recoit_la_forme_attendue() -> None:
    outil = to_langchain_tool(_outil())

    # Le modèle se trompe de forme : il envoie la liste des remplissages seule.
    sortie = outil.invoke([{"element_id": "block_065", "contenu_html": "<p>x</p>"}])

    charge = _contenu(sortie)
    assert charge["ok"] is False
    assert charge["error"]["action"] == "corriger_argument"
    forme = charge["error"]["details"]["forme_attendue"]
    assert set(forme["champs"]) == {"ancre", "entity_id", "remplissages"}
    assert sorted(forme["obligatoires"]) == ["ancre", "entity_id", "remplissages"]
    assert "objet, pas une liste" in charge["error"]["message"]


def test_un_champ_inconnu_est_nomme() -> None:
    outil = to_langchain_tool(_outil())

    sortie = outil.invoke(
        {"ancre": "offre", "entity_id": "e", "remplissages": [], "champs": "non"}
    )

    charge = _contenu(sortie)
    assert charge["ok"] is False
    erreurs = charge["error"]["details"]["erreurs"]
    assert any("champs" in str(item.get("champ", "")) for item in erreurs), erreurs


def test_un_payload_correct_passe_toujours() -> None:
    outil = to_langchain_tool(_outil())

    sortie = outil.invoke(
        {
            "ancre": "offre",
            "entity_id": "e",
            "remplissages": [{"element_id": "block_065", "contenu_html": "<p>x</p>"}],
        }
    )

    assert json.loads(str(sortie))["ok"] is True


def test_le_resume_de_schema_ne_recopie_pas_le_schema_entier() -> None:
    """Un message d'erreur reste lisible : jamais 39 k caractères de JSON."""
    from app.tools.documents.schemas import GenererDocumentInput

    resume = _resume_schema(GenererDocumentInput)
    assert len(json.dumps(resume, ensure_ascii=False)) < 4000
    assert "ancre" in resume["champs"] or "format" in resume["champs"]
