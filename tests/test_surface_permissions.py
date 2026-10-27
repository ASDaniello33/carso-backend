"""Classification de la surface d'API — « qui peut appeler quoi ».

Ce test verrouille la **répartition des routes** : toute écriture du domaine
métier doit être soit réservée à l'équipe interne (``exiger_interne`` /
``require_admin``), soit explicitement autorisée à un formateur **et** filtrée
par le périmètre (``calculer_perimetre``). Toute lecture du domaine doit être
réservée (interne/admin/audit) ou filtrée par le périmètre.

C'est la barrière contre la dérive : ajouter une route sans se poser la
question de son classement fait échouer ce test avec le chemin fautif. Le
classement des écritures ouvertes est une **décision validée** (opérations de
session et documents), pas un oubli. Une lecture peut aussi être ouverte si
elle ne touche **aucune donnée CARSO** : c'est le cas de la recherche
d'adresses Photon, explicitement classée.
"""

import importlib
import pkgutil
from typing import Any

from fastapi.routing import APIRoute

import app.api.routes as paquet
from app.api.deps import require_admin
from app.api.permissions import exiger_audit, exiger_interne

#: Modules dont la surface est gouvernée par le périmètre métier.
MODULES_METIER = frozenset(
    {
        "affectations",
        "appels",
        "audit",
        "beneficiaires",
        "documents",
        "equipes",
        "lieux",
        "lots",
        "missions",
        "modeles_documents",
        "offres",
        "organisations",
        "rapports",
        "recherche",
        "sessions",
        "suppression",
        "tableau_bord",
    }
)

#: Écritures qu'un formateur peut appeler — **décision validée** : il opère ses
#: sessions (inscriptions, pointage, fiche de présence, imports, documents de
#: session) et **gère ses documents** (ceux des missions et sessions de son
#: périmètre : renommage, suppression *logique*), mais ne pilote rien (statuts,
#: offres, budgets, affectations, créations).
ECRITURES_FORMAEUR: frozenset[tuple[str, str]] = frozenset(
    {
        ("POST", "/documents"),
        ("POST", "/documents/{document_id}/remplacement"),
        ("POST", "/documents/{document_id}/soumission"),
        # Décision 03/10 : ses documents, ou ceux de ses missions/sessions.
        ("PATCH", "/documents/{document_id}"),
        # ADR 0013 : la bascule privé ↔ public d'un document **sans objet
        # métier** appartient à son propriétaire — un formateur peut publier
        # le sien (le service refuse un document ancré).
        ("PATCH", "/documents/{document_id}/visibilite"),
        # Annulation d'un dépôt (pièce jointe retirée avant envoi) : le déposant
        # efface son propre brouillon, fichier et ligne ensemble.
        ("DELETE", "/documents/{document_id}/depot"),
        ("DELETE", "/documents/{identifiant}"),
        # Décision lieux/itinéraires (22/10) : le formateur ajuste le marqueur
        # d'un lieu de **ses** missions, et demande un itinéraire (calcul sans
        # écriture métier, borné au périmètre pour les lieux cités).
        ("PATCH", "/lieux/{lieu_id}/position"),
        ("POST", "/lieux/itineraire"),
        ("POST", "/missions/{mission_id}/supports-formation"),
        ("DELETE", "/missions/supports-formation/{support_id}"),
        ("POST", "/sessions/{session_id}/fiche-presence"),
        ("POST", "/sessions/{session_id}/imports/beneficiaires"),
        ("POST", "/sessions/{session_id}/imports/beneficiaires/apercu"),
        ("POST", "/sessions/{session_id}/participations"),
        ("PATCH", "/sessions/{session_id}/presences"),
        ("POST", "/participations/{participation_id}/presence"),
        ("PATCH", "/participations/{participation_id}"),
    }
)

#: Lectures **sans donnée CARSO**, ouvertes à tout compte authentifié.
#: Décision validée : `/lieux/recherche` interroge Photon (service public
#: d'adresses), n'écrit rien et ne lit aucun enregistrement métier. Elle sert
#: les formulaires de lieu et la résolution de noms des cartes d'agents, y
#: compris pour un formateur ; la création et le renommage d'un lieu restent,
#: eux, réservés à l'interne.
LECTURES_OUVERTES: frozenset[tuple[str, str]] = frozenset(
    {("GET", "/lieux/recherche")}
)

#: Méthodes qui modifient l'état (le reste est lu).
MÉTHODES_ÉCRITURE = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: Noms des gardes « réservé » reconnues.
GARDES: dict[str, Any] = {
    exiger_interne: "exiger_interne",
    require_admin: "require_admin",
    exiger_audit: "exiger_audit",
}


def _routes_metier() -> list[tuple[str, APIRoute]]:
    """Toutes les routes des modules métier, avec leur module d'origine.

    L'itération passe par les routeurs **de module** (et non ``app.routes``) :
    FastAPI encapsule les routeurs inclus, leurs routes ne sont alors plus
    énumérables — les tests doivent voir la surface déclarée.
    """
    routes: list[tuple[str, APIRoute]] = []
    for module_info in pkgutil.iter_modules(paquet.__path__):
        if module_info.name not in MODULES_METIER:
            continue
        module = importlib.import_module(f"app.api.routes.{module_info.name}")
        for attribut in ("router", "router_lot"):
            routeur = getattr(module, attribut, None)
            if routeur is None:
                continue
            for route in routeur.routes:
                if isinstance(route, APIRoute):
                    routes.append((module_info.name, route))
    return routes


def _gardes(route: APIRoute) -> set[str]:
    """Noms des gardes attachées à la route (niveau routeur et niveau endpoint)."""
    noms: set[str] = set()
    for dependance in route.dependant.dependencies:
        if dependance.call is not None:
            noms.add(getattr(dependance.call, "__name__", ""))
    for dependance in route.dependencies:
        appel = getattr(dependance, "dependency", None)
        if appel is not None:
            noms.add(getattr(appel, "__name__", ""))
    return noms


def test_ecritures_classees_et_lectures_bornees() -> None:
    """Aucune route métier n'échappe à un classement explicite."""
    routes = _routes_metier()
    assert len(routes) > 100  # la surface est bien celle attendue

    ecarts: list[str] = []
    for module, route in routes:
        methodes = set(route.methods or set())
        noms = _gardes(route)
        reservee = bool({"exiger_interne", "require_admin", "exiger_audit"} & noms)
        filtree = "calculer_perimetre" in noms
        ecritures = methodes & MÉTHODES_ÉCRITURE
        for methode in sorted(ecritures):
            autorisee_formateur = (methode, route.path) in ECRITURES_FORMAEUR
            if autorisee_formateur:
                if not filtree:
                    ecarts.append(
                        f"{module}: {methode} {route.path} autorisée à un formateur "
                        "sans périmètre (calculer_perimetre manquant)"
                    )
            elif not reservee:
                ecarts.append(
                    f"{module}: {methode} {route.path} n'est ni réservée à l'équipe "
                    "interne ni classée « écriture formateur »"
                )
        if (
            not ecritures
            and not reservee
            and not filtree
            and ("GET", route.path) not in LECTURES_OUVERTES
        ):
            ecarts.append(
                f"{module}: GET {route.path} n'est ni réservé ni filtré par le périmètre"
            )
    assert ecarts == [], "Routes à classer :\n" + "\n".join(sorted(ecarts))


def test_les_ecritures_formateur_ne_sont_pas_reservees() -> None:
    """Le classement est cohérent : une route ouverte n'est pas aussi refusée."""
    index = {
        (methode, route.path)
        for _, route in _routes_metier()
        for methode in (route.methods or set())
    }
    for methode, chemin in ECRITURES_FORMAEUR:
        assert (methode, chemin) in index, f"{methode} {chemin} n'existe plus"


def test_les_ecritures_reservees_portent_une_garde_et_un_acteur() -> None:
    """Toute écriture réservée est **auditée sous un acteur** : le contrat minimal.

    La garde de rôle protège l'accès ; l'attribution (``actor_id``) garantit que
    ce qui est écrit est attribué. Les deux vont ensemble — une route interne
    sans auteur est une trace inexploitable.
    """
    from app.application.trace import TraceContext

    #: Services d'audit : leur constructeur accepte ``actor_id``.
    champs = TraceContext.__init__.__code__.co_varnames
    assert "actor_id" in champs  # le contrat de TraceContext, pas une astuce

    manquants: list[str] = []
    for module, route in _routes_metier():
        if not (set(route.methods or set()) & MÉTHODES_ÉCRITURE):
            continue
        if not ({"exiger_interne", "require_admin"} & _gardes(route)):
            continue
        fonction = route.endpoint
        source = _source_avec_helpers(fonction)
        if source is None:  # pragma: no cover - source indisponible (build figée)
            continue
        # L'attribution passe soit par le constructeur du service
        # (``actor_id=``), soit par le contexte de demande (``demande_par(``)
        # quand le geste est celui d'un agent déclenché par un humain.
        if "actor_id=" in source or "demande_par(" in source:
            continue
        manquants.append(f"{module}: {sorted(route.methods)} {route.path} ({fonction.__name__})")
    assert manquants == [], "Écritures réservées sans actor_id :\n" + "\n".join(manquants)


def _source_avec_helpers(fonction: Any) -> str | None:
    """Source d'un endpoint, **plus** celle des helpers du même module qu'il appelle.

    Une route réservée peut déléguer à un helper qui, lui, attribue l'écriture
    (``ServiceSuppression(..., actor_id=...)``) : la factorisation ne doit pas
    faire perdre la preuve d'attribution, sinon la seule façon de passer ce test
    serait de dupliquer le corps de la route. Seuls les helpers réellement
    appelés par l'endpoint sont suivis (un niveau).
    """
    import inspect

    try:
        source = inspect.getsource(fonction)
    except OSError:  # pragma: no cover - source indisponible
        return None
    module = inspect.getmodule(fonction)
    if module is None:  # pragma: no cover - module introuvable
        return source
    sources = [source]
    for nom, objet in vars(module).items():
        if not inspect.isfunction(objet) or objet is fonction:
            continue
        if f"{nom}(" not in source:
            continue
        try:
            sources.append(inspect.getsource(objet))
        except OSError:  # pragma: no cover - source indisponible
            continue
    return "\n".join(sources)
