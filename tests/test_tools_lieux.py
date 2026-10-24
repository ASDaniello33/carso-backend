"""Tools de lieux — disponibilité par agent et comportement hors réseau.

Ces tools répondent à une demande précise : **tous** les agents du catalogue UI
doivent pouvoir chercher un lieu, l'afficher (outil frontend ``afficher_carte``)
et calculer distance et carburant. Le test fige les deux tools backend sur les
six agents ; Photon et OpenRouteService sont simulés (aucun appel réseau).
"""

from __future__ import annotations

from app.agents.agent_statistique import DEFINITION as STATISTIQUE
from app.agents.assistant_formateur import DEFINITION as ASSISTANT
from app.agents.catalogue import AGENT_DEFINITIONS
from app.agents.generaliste_readonly import DEFINITION as GENERALISTE
from app.agents.generateur_offre import DEFINITION as GENERATEUR
from app.agents.agent_personnel import DEFINITION as PERSONNEL
from app.agents.agent_rh import DEFINITION as RH
from app.agents.tools.agent_assistant_formateur_tools import construire_tools_assistant_formateur
from app.agents.tools.agent_generaliste_tools import construire_tools_generaliste
from app.agents.tools.agent_generateur_offre_tools import construire_tools_generateur_offre
from app.agents.tools.agent_lieux_tools import _calculer_itineraire, _rechercher_lieu
from app.agents.tools.agent_rh_tools import construire_tools_rh
from app.agents.tools.agent_statistique_tools import construire_tools_statistique
from app.application.services.itineraire_service import ResultatItineraire
from app.geo.photon import LieuTrouve

_BUILDERS = {
    GENERALISTE.agent_id: construire_tools_generaliste,
    PERSONNEL.agent_id: construire_tools_generaliste,
    RH.agent_id: construire_tools_rh,
    ASSISTANT.agent_id: construire_tools_assistant_formateur,
    STATISTIQUE.agent_id: construire_tools_statistique,
    GENERATEUR.agent_id: construire_tools_generateur_offre,
}


def test_les_six_agents_ont_recherche_et_itineraire() -> None:
    assert len(AGENT_DEFINITIONS) == 6
    for definition in AGENT_DEFINITIONS:
        noms = {tool.name for tool in _BUILDERS[definition.agent_id](definition, None)}
        assert "rechercher_lieu" in noms, definition.agent_id
        assert "calculer_itineraire" in noms, definition.agent_id


def test_rechercher_lieu_ne_cree_rien(monkeypatch) -> None:
    """La recherche rend des suggestions ; aucun lieu n'est écrit (pas de session)."""
    import app.geo.photon as photon

    appels: list[tuple[str, int]] = []

    def _faux_chercher(self, requete, *, limite=5, latitude=None, longitude=None):
        appels.append((requete, limite))
        return [
            LieuTrouve(
                nom="Toliara",
                adresse=None,
                ville="Toliara I",
                pays="Madagascar",
                latitude=-23.35,
                longitude=43.66,
                osm_type="node",
                osm_id=1,
            )
        ]

    monkeypatch.setattr(photon.RechercheLieux, "chercher", _faux_chercher)
    resultat = _rechercher_lieu(None, {"requete": "Toliara", "limite": 3})  # type: ignore[arg-type]

    assert appels == [("Toliara", 3)]
    assert resultat["resultats"][0]["latitude"] == -23.35
    assert "créer" in resultat["note"].lower() or "officiel" in resultat["note"].lower()


def test_calculer_itineraire_passe_par_le_service(monkeypatch) -> None:
    """Le calcul est délégué au service (cache, carburant) — jamais refait ici."""
    import app.application.services.itineraire_service as module

    recu: dict[str, object] = {}

    def _faux_calculer(self, *, origine, destination, aller_retour=False, **kwargs):
        recu["origine"] = (origine.latitude, origine.longitude)
        recu["destination"] = (destination.latitude, destination.longitude)
        recu["aller_retour"] = aller_retour
        return ResultatItineraire(
            distance_m=928_000,
            duree_s=53_820,
            aller_retour=False,
            distance_totale_m=928_000,
            duree_totale_s=53_820,
            litres=74.25,
            cout=0.0,
            devise="MGA",
            origine_libelle="Toliara",
            destination_libelle="Antananarivo",
            geometrie=None,
            depuis_cache=True,
        )

    monkeypatch.setattr(module.ItineraireService, "calculer", _faux_calculer)
    resultat = _calculer_itineraire(
        None,  # type: ignore[arg-type]
        {
            "origine": {"libelle": "Toliara", "latitude": -23.35, "longitude": 43.66},
            "destination": {"libelle": "Antananarivo", "latitude": -18.88, "longitude": 47.51},
            "aller_retour": False,
        },
    )

    assert recu["origine"] == (-23.35, 43.66)
    assert resultat["distance_totale_m"] == 928_000
    assert resultat["litres"] == 74.25
    # Un coût nul sans prix saisi n'est pas un trajet gratuit.
    assert "non chiffré" in resultat["note"].lower()
