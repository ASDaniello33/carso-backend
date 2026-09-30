"""IDs d'appels d'outils AG-UI uniques à la frontière SSE — hors reprise HITL.

Les providers OpenAI-compat réutilisent ``call_0``, ``call_1`` à **chaque**
tour modèle, y compris dans le même run. ``@ag-ui/client`` traite un
``TOOL_CALL_START`` déjà connu comme une mise à jour : il *renomme* l'ancienne
carte au lieu d'en créer une. Les 4ᵉ/5ᵉ outils disparaissent ; le
``MESSAGES_SNAPSHOT`` du HITL reconstruit le fil d'un coup.

- un ``TOOL_CALL_START`` dont l'id a déjà été émis (historique **ou** tour
  précédent de ce run) est suffixé ``call_0::<run_id>`` ;
- ARGS / END / RESULT suivent le dernier START de cet id brut ;
- le snapshot réécrit les appels d'outils des *nouveaux* messages dans
  l'ordre, sans toucher à l'historique déjà affiché ;
- une reprise HITL sans ``interruptId`` (ancien canal) ne renomme rien.

Portée **resserrée** (correctif « fin de stream tronquée ») : cette couche ne
touche plus les ids de **messages** ni ``parent_message_id``. ``ag-ui-langgraph``
0.0.45+ possède son propre registre d'ids publics (``_resolve_public_id``,
``_seed_public_ids_from_input``, ``_translate_snapshot_ids``) qui garantit la
cohérence entre le flux et le ``MESSAGES_SNAPSHOT`` final ; notre double
réécriture créait le décalage (``TEXT_MESSAGE_END`` sans START apparié,
issue CopilotKit #3208 ; bulle d'assistant fantôme parentée à un id minté)
qui faisait disparaître des parties du message en fin de stream.

Le graphe LangGraph conserve les ids bruts. Seul le flux SSE est réécrit.
"""

from __future__ import annotations

from typing import Any

_START_OUTIL = frozenset({"TOOL_CALL_START"})
_CHUNK_OUTIL = frozenset({"TOOL_CALL_CHUNK"})


def _valeur(objet: Any, *cles: str) -> Any:
    if objet is None:
        return None
    if isinstance(objet, dict):
        for cle in cles:
            if cle in objet:
                return objet[cle]
        return None
    for cle in cles:
        if hasattr(objet, cle):
            return getattr(objet, cle)
    return None


def _texte(valeur: Any) -> str | None:
    return valeur if isinstance(valeur, str) and valeur else None


def _extraire_interrupt_id(resume: Any) -> str | None:
    """Id d'interrupt adressé par un ``RunAgentInput.resume``, s'il existe.

    Les entrées AG-UI portent ``interruptId`` ; l'ancien canal
    (``forwardedProps.command``) n'en porte pas — on renvoie alors ``None``.
    """
    premier = resume[0] if isinstance(resume, list) and resume else None
    if not isinstance(premier, dict):
        return None
    identifiant = premier.get("interruptId") or premier.get("interrupt_id")
    return identifiant if isinstance(identifiant, str) and identifiant else None


def ids_historique(messages: Any) -> set[str]:
    """Ids déjà affichés par le client (messages + appels d'outils)."""
    vus: set[str] = set()
    for message in messages or []:
        identifiant = _texte(_valeur(message, "id"))
        if identifiant:
            vus.add(identifiant)
        for appel in _valeur(message, "tool_calls", "toolCalls") or []:
            aid = _texte(_valeur(appel, "id"))
            if aid:
                vus.add(aid)
        rattache = _texte(_valeur(message, "tool_call_id", "toolCallId"))
        if rattache:
            vus.add(rattache)
    return vus


def _type_evenement(event: Any) -> str:
    brut = _valeur(event, "type")
    if brut is None:
        return ""
    return brut.value if hasattr(brut, "value") else str(brut)


class CorrespondanceIds:
    """Table brut → public, valable pour un seul run AG-UI."""

    def __init__(
        self,
        *,
        historique: set[str],
        suffixe: str,
        figer: bool,
        interrupt_id: str | None = None,
    ) -> None:
        self._historique = set(historique)
        self._suffixe = suffixe or "run"
        self._figer = figer
        #: Interrupt repris par ce run (HITL) : les tool calls du tour repris
        #: sont suffixés avec, pour rester distinguables du tour interrompu.
        self._interrupt_id = interrupt_id
        #: Table brut → public, **tool calls uniquement** (jamais les ids de
        #: messages : ils appartiennent au reminting interne d'ag-ui-langgraph).
        self._table: dict[str, str] = {}
        #: STARTs émis pour chaque id brut, dans l'ordre (un run, plusieurs tours).
        self._starts: dict[str, list[str]] = {}

    @classmethod
    def depuis_entree(cls, entree: Any) -> CorrespondanceIds:
        messages = _valeur(entree, "messages") or []
        resume = _valeur(entree, "resume")
        run_id = _texte(_valeur(entree, "run_id", "runId")) or "run"
        return cls(
            historique=ids_historique(messages),
            suffixe=run_id,
            figer=bool(resume),
            interrupt_id=_extraire_interrupt_id(resume),
        )

    def public_nouveau(self, brut: str) -> str:
        """START d'un appel d'outil : minter si l'id a déjà été émis.

        Reprise HITL (``interrupt_id`` connu) : l'id brut du tour interrompu
        figure dans l'historique affiché — le tool call **repris** doit rester
        visible comme NOUVELLE carte, sinon le provider réutilise l'entrée du
        tour interrompu (arguments/resultat anciens) et le rendu client reste
        vide. On suffixe donc avec l'id d'interrupt au lieu de figer.
        """
        if self._figer and self._interrupt_id is None:
            self._table[brut] = brut
            self._starts.setdefault(brut, [brut])
            return brut
        if (
            self._figer
            and self._interrupt_id is not None
            and brut in self._historique
        ):
            return self._minter(brut)
        if brut in self._starts or brut in self._historique:
            return self._minter(brut)
        self._table[brut] = brut
        self._starts[brut] = [brut]
        return brut

    def _minter(self, brut: str) -> str:
        occupes = self._historique | {p for ps in self._starts.values() for p in ps}
        # Le premier START conserve l'id brut ; seuls les suivants sont minés.
        deja_mintes = sum(1 for p in self._starts.get(brut, []) if p != brut)
        n = deja_mintes + 1
        mint = f"{brut}::{self._suffixe}" if n == 1 else f"{brut}::{self._suffixe}::{n}"
        while mint in occupes:
            n += 1
            mint = f"{brut}::{self._suffixe}::{n}"
        self._table[brut] = mint
        self._starts.setdefault(brut, []).append(mint)
        return mint

    def _resoudre(self, brut: str, *, est_start: bool, est_chunk: bool) -> str:
        if est_start:
            return self.public_nouveau(brut)
        if est_chunk and brut not in self._table:
            return self.public_nouveau(brut)
        return self.public_suite(brut) or brut

    def public_suite(self, brut: str | None) -> str | None:
        """ARGS / END / snapshot : appliquer la table, ne jamais inventer."""
        if not brut:
            return brut
        return self._table.get(brut, brut)

    def appliquer(self, event: Any) -> Any:
        """Réécrit les ids d'un événement SSE, ou le rend tel quel."""
        type_event = _type_evenement(event)
        changements: dict[str, Any] = {}

        appel = _texte(_valeur(event, "tool_call_id", "toolCallId"))
        if appel:
            public = self._resoudre(
                appel,
                est_start=type_event in _START_OUTIL,
                est_chunk=type_event in _CHUNK_OUTIL,
            )
            if public != appel:
                changements["tool_call_id"] = public

        # Ids de messages et parent_message_id : volontairement intouchés —
        # le reminting interne d'ag-ui-langgraph (0.0.45+) les résout déjà et
        # garantit l'appariement START/CONTENT/END ainsi que la correspondance
        # avec le MESSAGES_SNAPSHOT final. Une réécriture ici désappariait le
        # flux (bulle fantôme, fin de stream tronquée).

        snapshot = _valeur(event, "messages")
        if snapshot:
            reecrits = self._snapshot(list(snapshot))
            if reecrits != list(snapshot):
                changements["messages"] = reecrits

        return _avec(event, changements)

    def _snapshot(self, messages: list[Any]) -> list[Any]:
        """Réécrit les appels d'outils des *nouveaux* messages (ids de messages
        intouchés, voir docstring du module)."""
        assigne: dict[str, int] = {}
        dernier: dict[str, str] = {}
        sortie: list[Any] = []
        for message in messages:
            identifiant = _texte(_valeur(message, "id"))
            if identifiant and identifiant in self._historique:
                sortie.append(message)
                continue
            sortie.append(self._message_nouveau(message, assigne, dernier))
        return sortie

    def _message_nouveau(
        self,
        message: Any,
        assigne: dict[str, int],
        dernier: dict[str, str],
    ) -> Any:
        changements: dict[str, Any] = {}
        identifiant = _texte(_valeur(message, "id"))
        if identifiant:
            public = self.public_suite(identifiant)
            if public != identifiant:
                changements["id"] = public
        rattache = _texte(_valeur(message, "tool_call_id", "toolCallId"))
        if rattache:
            public = dernier.get(rattache) or self.public_suite(rattache)
            if public != rattache:
                # Même clé que l'entrée d'origine (snake_case ou camelCase) :
                # écrire l'autre variante ajouterait un champ ignoré du client.
                cle = (
                    "tool_call_id"
                    if _valeur(message, "tool_call_id") is not None
                    else "toolCallId"
                )
                changements[cle] = public
        cle_appels = "tool_calls" if _valeur(message, "tool_calls") is not None else "toolCalls"
        appels = _valeur(message, "tool_calls", "toolCalls")
        if appels:
            nouveaux = []
            change = False
            for appel in appels:
                aid = _texte(_valeur(appel, "id"))
                if not aid:
                    nouveaux.append(appel)
                    continue
                publics = self._starts.get(aid) or [self._table.get(aid, aid)]
                index = assigne.get(aid, 0)
                public = publics[index] if index < len(publics) else publics[-1]
                assigne[aid] = index + 1
                dernier[aid] = public
                if public == aid:
                    nouveaux.append(appel)
                    continue
                change = True
                nouveaux.append(_avec(appel, {"id": public}))
            if change:
                changements[cle_appels] = nouveaux
        return _avec(message, changements) if changements else message


def _avec(objet: Any, changements: dict[str, Any]) -> Any:
    if not changements:
        return objet
    copier = getattr(objet, "model_copy", None)
    if callable(copier):
        return copier(update=changements)
    if isinstance(objet, dict):
        return {**objet, **changements}
    for cle, valeur in changements.items():
        setattr(objet, cle, valeur)
    return objet


class AgentFluxIdsUniques:
    """Décorateur : le clone par requête réécrit le flux SSE, pas le graphe."""

    def __init__(self, interne: Any) -> None:
        self._interne = interne

    def __getattr__(self, nom: str) -> Any:
        return getattr(self._interne, nom)

    def clone(self) -> AgentFluxIdsUniques:
        cloner = getattr(self._interne, "clone", None)
        interne = cloner() if callable(cloner) else self._interne
        return AgentFluxIdsUniques(interne)

    async def run(self, input_data: Any):
        mapper = CorrespondanceIds.depuis_entree(input_data)
        async for event in self._interne.run(input_data):
            yield mapper.appliquer(event)


__all__ = [
    "AgentFluxIdsUniques",
    "CorrespondanceIds",
    "ids_historique",
]
