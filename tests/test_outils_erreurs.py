"""Contrat d'erreur des outils : action explicite et schémas stricts.

Constat (audit du 13/11) : un modèle qui se trompe de champ recevait « champ
requis » (Pydantic ignorait le champ inconnu), et une erreur d'environnement ne
lui disait pas d'arrêter. Les tests fixent les deux règles : refus nommé d'un
champ inconnu, et ``action`` qui dit quoi faire (corriger, changer d'outil,
arrêter). Aucun modèle, aucun réseau.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict

from app.agents.agent_statistique import DEFINITION as STATISTIQUE
from app.agents.outillage import tools_documents_pour
from app.agents.tools.agent_statistique_tools import construire_tools_statistique
from app.core.errors import (
    NotFoundError,
    PermissionDeniedError,
    ServiceExterneError,
    ValidationError,
)
from app.tools.base import (
    TypedTool,
    formater_erreur_outil,
    schema_entree_outil,
    to_langchain_tool,
)


class _Lecture(BaseModel):
    document_id: str


class _Frontend(BaseModel):
    """Schéma frontend : les champs ajoutés par le client restent tolérés."""

    model_config = ConfigDict(extra="allow")

    titre: str


class _Echo(BaseModel):
    texte: str


def _outil(schema: type[BaseModel]) -> TypedTool:
    return TypedTool(
        name="echo",
        description="Rend le texte.",
        input_schema=schema,
        handler=lambda payload: {"texte": payload.get("texte", "ok")},
    )


# --- schémas stricts ---------------------------------------------------------


def test_champ_inconnu_refuse_et_nomme() -> None:
    sortie = _outil(_Echo).invoke({"texte": "a", "document_nom": "x.pdf"})

    assert sortie["ok"] is False
    erreur = sortie["error"]
    assert erreur["action"] == "corriger_argument"
    assert erreur["details"]["champs_inconnus"] == ["document_nom"]
    assert "champ inconnu" in erreur["message"]


def test_schema_avec_extra_allow_garde_son_choix() -> None:
    """Un outil d'affichage tolère les champs du client : la règle ne l'écrase pas."""
    schema = schema_entree_outil(_Frontend)
    assert schema.model_config.get("extra") == "allow"
    sortie = _outil(_Frontend).invoke({"titre": "t", "champ_client": 1})
    assert sortie["ok"] is True


def test_schema_envoye_est_allege_des_libelles() -> None:
    """Le schéma du modèle ne porte ni ``title`` ni ``default`` : rien d'utile perdu."""
    schema = schema_entree_outil(_Lecture).model_json_schema()
    assert "title" not in schema
    for champ in schema["properties"].values():
        assert "title" not in champ
        assert "default" not in champ


def test_schema_strict_est_memorise() -> None:
    """Même classe dérivée à chaque appel : pas de schéma différent d'un tour à l'autre."""
    assert schema_entree_outil(_Lecture) is schema_entree_outil(_Lecture)
    assert schema_entree_outil(_Lecture) is not _Lecture
    assert schema_entree_outil(_Lecture).model_json_schema()["additionalProperties"] is False


def test_tool_langchain_recoit_le_schema_strict() -> None:
    outil = to_langchain_tool(_outil(_Echo))
    assert outil.args_schema is schema_entree_outil(_Echo)


def test_tous_les_schemas_d_outils_sont_stricts() -> None:
    """Chaque schéma monté pour un agent refuse un champ non déclaré.

    Exception assumée : un schéma qui déclare ``extra="allow"`` (outils
    d'affichage) garde sa tolérance ; le test le vérifie au lieu de le subir.
    """
    outils = (
        *construire_tools_statistique(STATISTIQUE, lambda: None),
        *tools_documents_pour(STATISTIQUE, lambda: None),
    )
    non_stricts = [
        outil.name
        for outil in outils
        if outil.input_schema is not None
        and schema_entree_outil(outil.input_schema).model_config.get("extra") != "forbid"
    ]
    assert not non_stricts, f"schémas sans extra=forbid : {non_stricts}"


# --- action d'erreur ---------------------------------------------------------


def test_not_found_demande_de_corriger_l_argument() -> None:
    charge = formater_erreur_outil(NotFoundError("Document introuvable"))
    assert charge["error"]["action"] == "corriger_argument"


def test_permission_denied_demande_de_changer_d_outil() -> None:
    charge = formater_erreur_outil(PermissionDeniedError("Interdit"))
    assert charge["error"]["action"] == "changer_outil"


def test_service_externe_demande_de_ne_pas_reessayer() -> None:
    charge = formater_erreur_outil(ServiceExterneError("Service indisponible"))
    assert charge["error"]["action"] == "ne_pas_reessayer"


def test_action_imposee_par_l_outil_prime() -> None:
    """Une limite d'environnement dit « ne réessaie pas », même en validation."""
    erreur = ValidationError(
        "CSV au-delà du parcours", details={"action": "ne_pas_reessayer", "octets": 1}
    )
    charge = formater_erreur_outil(erreur)
    assert charge["error"]["action"] == "ne_pas_reessayer"


def test_action_inconnue_retombe_sur_corriger_argument() -> None:
    erreur = ValidationError("x", details={"action": "inventée"})
    assert formater_erreur_outil(erreur)["error"]["action"] == "corriger_argument"


def test_outils_suivants_seulement_s_ils_sont_declares() -> None:
    sans = formater_erreur_outil(ValidationError("x"))
    assert "outils_suivants" not in sans["error"]

    avec = formater_erreur_outil(
        ValidationError("x", details={"outils_suivants": ["chercher_dans_document"]})
    )
    assert avec["error"]["outils_suivants"] == ["chercher_dans_document"]


@pytest.mark.parametrize("code_attendu", ["business_validation_error"])
def test_payload_contient_toujours_code_et_action(code_attendu: str) -> None:
    charge = formater_erreur_outil(ValidationError("x"))
    assert charge["error"]["code"] == code_attendu
    assert charge["error"]["action"] in {"corriger_argument", "changer_outil", "ne_pas_reessayer"}
    # ``details`` n'apparaît que s'il y en a : un dict vide n'apprend rien au modèle.
    assert "details" not in charge["error"]


def test_schema_invalide_porte_action_et_erreurs_detaillees() -> None:
    sortie: dict[str, Any] = _outil(_Echo).invoke({})
    assert sortie["ok"] is False
    assert sortie["error"]["action"] == "corriger_argument"
    assert sortie["error"]["details"]["erreurs"][0]["champ"] == "texte"
