"""Runtime d'outils partagé (instruction/05 §4 : ToolRegistry, tools typés).

Un tool est la frontière d'appel d'un agent vers un service applicatif
(AGENTS.md §2.5 : Agent → Tool → Service → Repository). Il ne porte aucune
règle métier : il valide son entrée, vérifie la permission, délègue.

``TypedTool`` est le contrat **propre au projet** (schéma Pydantic + handler +
permissions). ``to_langchain_tool`` le projette en tool LangChain consommable par
un deep agent : la validation Pydantic reste à la frontière, donc une sortie de
LLM n'est jamais exécutée sans validation (instruction/05 §11).
"""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from app.core.errors import CarsoError
from app.core.errors import ValidationError as BusinessValidationError


@dataclass(frozen=True)
class TypedTool:
    """Tool typé : nom, description, schéma d'entrée Pydantic, handler.

    ``invoke`` valide le payload via le schéma avant d'appeler le handler —
    la sortie d'un LLM ne doit jamais être consommée sans validation
    (instruction/05 §11 : structured output).
    """

    name: str
    description: str
    handler: Callable[[dict[str, Any]], dict[str, Any]]
    input_schema: type[BaseModel] | None = None
    tags: tuple[str, ...] = field(default=())

    def invoke(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Valide l'entrée puis exécute le handler.

        Les erreurs métier (``CarsoError``) sont renvoyées comme payload
        ``{ok: false, error: ...}`` pour les appels directs (tests, chemin
        déterministe). Le pont LangChain (``to_langchain_tool``) les convertit
        en ``ToolException`` + ``handle_tool_error`` (doc LangChain : seul
        ``ToolException`` devient un Tool Message ; le reste crash le graphe).
        """
        try:
            if self.input_schema is not None:
                validated = self.input_schema.model_validate(payload)
                payload = validated.model_dump()
            resultat = self.handler(payload)
        except CarsoError as erreur:
            return formater_erreur_outil(erreur)
        except PydanticValidationError as erreur:
            return formater_erreur_schema(erreur)
        if isinstance(resultat, dict) and "ok" not in resultat:
            return {"ok": True, **resultat}
        if isinstance(resultat, dict):
            return resultat
        return {"ok": True, "resultat": resultat}


class ToolRegistry:
    """Registre des tools déclarés (par agent ou global). Refuse les doublons."""

    def __init__(self) -> None:
        self._tools: dict[str, TypedTool] = {}

    def register(self, tool: TypedTool) -> TypedTool:
        if tool.name in self._tools:
            msg = f"Tool déjà enregistré: {tool.name!r}"
            raise BusinessValidationError(msg)
        self._tools[tool.name] = tool
        return tool

    def get(self, name: str) -> TypedTool:
        if name not in self._tools:
            msg = f"Tool inconnu: {name!r}"
            raise BusinessValidationError(msg)
        return self._tools[name]

    def names(self) -> list[str]:
        return sorted(self._tools)

    def __len__(self) -> int:
        return len(self._tools)


#: Traduction des codes d'erreur Pydantic en français, pour que l'agent sache
#: **ce qui** est refusé sans avoir à interpréter un message anglais brut.
_MESSAGES_SCHEMA: dict[str, str] = {
    "missing": "champ requis",
    "extra_forbidden": "champ inconnu (aucun champ non prévu n'est accepté)",
    "literal_error": "valeur non autorisée",
    "enum": "valeur non autorisée",
    "uuid_parsing": "identifiant UUID attendu (8-4-4-4-12)",
    "uuid_type": "identifiant UUID attendu",
    "string_type": "texte attendu",
    "string_too_short": "texte trop court",
    "string_too_long": "texte trop long",
    "int_type": "nombre entier attendu",
    "int_parsing": "nombre entier attendu",
    "float_type": "nombre attendu",
    "float_parsing": "nombre attendu",
    "bool_type": "booléen attendu",
    "bool_parsing": "booléen attendu",
    "list_type": "liste attendue",
    "dict_type": "objet attendu",
    "model_type": "objet attendu",
    "date_type": "date attendue (AAAA-MM-JJ)",
    "date_parsing": "date attendue (AAAA-MM-JJ)",
    "date_from_datetime_parsing": "date attendue (AAAA-MM-JJ)",
    "too_short": "trop peu d'éléments",
    "too_long": "trop d'éléments",
    "greater_than": "valeur trop petite",
    "less_than": "valeur trop grande",
    "greater_than_equal": "valeur trop petite",
    "less_than_equal": "valeur trop grande",
}


def _valeur_courte(valeur: Any, limite: int = 120) -> str | None:
    """Valeur reçue, décrite courtement (jamais le contenu complet d'un objet)."""
    if valeur is None:
        return None
    if isinstance(valeur, dict | list | tuple | set | frozenset):
        return f"{type(valeur).__name__} de {len(valeur)} élément(s)"
    texte = " ".join(str(valeur).split())
    return texte[:limite] + ("…" if len(texte) > limite else "")


def _valeurs_attendues(detail: dict[str, Any]) -> str | None:
    """Valeurs acceptées d'un champ à vocabulaire fermé (``Literal``/``enum``)."""
    ctx = detail.get("ctx") or {}
    brute = ctx.get("expected")
    if not isinstance(brute, str):
        return None
    return brute.replace("'", "").replace(" or ", ", ")


def _probleme_lisible(detail: dict[str, Any]) -> str:
    """Problème en français quand le code est connu, message Pydantic sinon."""
    type_ = str(detail.get("type", ""))
    if type_ == "value_error":
        cause = (detail.get("ctx") or {}).get("error")
        if cause is not None:
            return str(cause)
    return _MESSAGES_SCHEMA.get(type_, str(detail.get("msg", "entrée invalide")))


def _type_recu(valeur: Any) -> str | None:
    """Type Python reçu, pour dire « objet attendu, chaîne reçue » sans devine."""
    if isinstance(valeur, dict):
        return "objet"
    if isinstance(valeur, list):
        return "liste"
    if valeur is None:
        return None
    return type(valeur).__name__


def _erreurs_lisibles(erreur: PydanticValidationError) -> list[dict[str, Any]]:
    """Constats de schéma lisibles : champ, problème, valeurs attendues/reçues."""
    lisibles: list[dict[str, Any]] = []
    for detail in erreur.errors()[:15]:
        champ = ".".join(str(partie) for partie in detail.get("loc", ())) or "(racine)"
        entree: dict[str, Any] = {"champ": champ, "probleme": _probleme_lisible(detail)}
        attendues = _valeurs_attendues(detail)
        if attendues:
            entree["valeurs_attendues"] = attendues
        recu = _type_recu(detail.get("input"))
        if recu is not None:
            entree["type_recu"] = recu
        recue = _valeur_courte(detail.get("input"))
        if recue is not None:
            entree["valeur_recue"] = recue
        lisibles.append(entree)
    return lisibles


def formater_erreur_schema(erreur: PydanticValidationError) -> dict[str, Any]:
    """Payload d'erreur d'une entrée refusée par le schéma d'un tool.

    L'agent reçoit de quoi corriger **sans deviner** : le champ fautif, le
    problème en clair, les valeurs acceptées et la valeur reçue. Le tuple brut de
    Pydantic resterait illisible (et non actionnable) pour un modèle.
    """
    erreurs = _erreurs_lisibles(erreur)
    details: list[str] = []
    for item in erreurs[:5]:
        ligne = f"{item['champ']} : {item['probleme']}"
        if item.get("valeurs_attendues"):
            ligne += f" (valeurs acceptées : {item['valeurs_attendues']})"
        details.append(ligne)
    total = len(erreur.errors())
    message = "Entrée de tool invalide — " + " ; ".join(details)
    if total > len(erreurs):
        message += f" (+{total - len(erreurs)} autre(s) problème(s) de schéma)"
    return formater_erreur_outil(
        BusinessValidationError(message, details={"erreurs": erreurs})
    )


def formater_erreur_outil(erreur: CarsoError) -> dict[str, Any]:
    """Payload d'erreur métier (pas de traceback, pas de secret).

    Les ``details`` métier sont inclus : un agent doit savoir **ce qui** est
    refusé (champ invalide, valeurs possibles, format attendu) pour corriger sans
    deviner. Ils ne contiennent ni chemin physique, ni secret, ni trace.
    """
    charge: dict[str, Any] = {
        "ok": False,
        "error": {
            "code": getattr(erreur, "code", "internal_error"),
            "message": str(erreur),
        },
    }
    if erreur.details:
        charge["error"]["details"] = erreur.details
    return charge


def to_langchain_tool(tool: TypedTool) -> Any:
    """Projette un ``TypedTool`` en tool LangChain (usage : deep agents).

    Le tool LangChain porte **exactement** le schéma Pydantic du ``TypedTool`` :
    LangChain valide les arguments du modèle avant d'appeler le handler, et la
    validation est refaite à la frontière par ``TypedTool.invoke`` (défense en
    profondeur). La sortie est sérialisée en JSON, jamais un objet Python
    arbitraire renvoyé au modèle.

    Args:
        tool: tool projet avec un schéma d'entrée Pydantic obligatoire.

    Returns:
        Un ``StructuredTool`` LangChain nommé ``tool.name``.

    Raises:
        ValidationError: tool sans schéma d'entrée — un tool exposé à un LLM
            doit être typé, sans exception.
    """
    if tool.input_schema is None or tool.input_schema is BaseModel:
        msg = (
            f"Le tool {tool.name!r} n'a pas de schéma d'entrée : un tool exposé à "
            "un modèle doit être typé (instruction/05 §11). "
            "Ne jamais passer pydantic.BaseModel lui-même : LangChain plante "
            "sur tool_call_schema (AttributeError: __pydantic_generic_metadata__)."
        )
        raise BusinessValidationError(msg)

    def _run(**kwargs: Any) -> str:
        # invoke() convertit déjà CarsoError → {ok: false} (Tool Message).
        # handle_tool_error ci-dessous ne rattrape QUE ToolException
        # (langchain_core.tools.base.BaseTool.run) — doc officielle.
        resultat = tool.invoke(kwargs)
        if isinstance(resultat, str):
            return resultat
        return json.dumps(resultat, ensure_ascii=False, default=str)

    from langchain_core.tools import StructuredTool

    return StructuredTool.from_function(
        func=_run,
        name=tool.name,
        description=tool.description,
        args_schema=tool.input_schema,
        handle_tool_error=True,
    )
