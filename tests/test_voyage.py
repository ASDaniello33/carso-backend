"""Tests — lieux cartographiques, recherche Photon, itinéraires ORS.

Ce que ces tests verrouillent :

1. **les réponses externes sont normalisées** — le frontend ne connaît ni le
   format Photon ni le format ORS ; une réponse illisible est une erreur
   explicite, jamais un champ deviné ;
2. **les coordonnées sont une paire** — une latitude sans longitude est refusée
   (elle dessinerait un point au milieu de l'océan), les plages WGS84 tiennent ;
3. **le quota n'est pas dépensé deux fois** — deux calculs identiques ne font
   qu'UN appel au fournisseur ; le résultat est relu du cache PostgreSQL ;
4. **le carburant est exact** — Decimal, aller-retour ×2, surcharge possible
   sans toucher aux réglages ;
5. **le périmètre s'applique** — un formateur lit les lieux de ses missions et
   ajuste leur marqueur ; un lieu d'une autre mission est introuvable (404).

Aucun réseau : les réponses externes sont des fixtures, le client ORS est
bouchonné ou monkeypatché.
"""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.application.dto import LieuInput
from app.application.perimetre import Perimetre
from app.application.services.itineraire_service import (
    ItineraireService,
    PointItineraire,
)
from app.application.services.lieu_service import LieuService, valider_coordonnees
from app.application.services.parametres_voyage_service import (
    ParametresVoyageMiseAJour,
    ParametresVoyageService,
)
from app.core.errors import NotFoundError, ValidationError
from app.domain.execution import Lieu
from app.geo.openrouteservice import interpreter_reponse as interpreter_ors
from app.geo.photon import interpreter_reponse as interpreter_photon

PRÉFIXE = "/api/v1"


def _compte(role: str = "collaborateur"):
    from uuid import uuid4

    from app.domain.identity import Utilisateur

    return Utilisateur(
        email=f"voyage-{uuid4().hex[:8]}@carso.test",
        nom="Voyage",
        prenom="Test",
        password_hash="x",
        role=role,
        statut="actif",
    )


def _brancher(api_client: TestClient, utilisateur) -> None:
    from app.api.deps import get_current_user

    api_client.app.dependency_overrides[get_current_user] = lambda: utilisateur


# --- 1. Normalisation des réponses externes ------------------------------------


def test_photon_rend_des_resultats_normalises() -> None:
    reponse = {
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "osm_type": "N",
                    "osm_id": 254609408,
                    "name": "Toliara",
                    "city": "Toliara I",
                    "state": "Atsimo-Andrefana",
                    "country": "Madagascar",
                },
                "geometry": {"type": "Point", "coordinates": [43.66966, -23.354173]},
            },
            # Un résultat sans position : ignoré, jamais deviné.
            {"type": "Feature", "properties": {"name": "Fantôme"}},
        ]
    }
    resultats = interpreter_photon(reponse)
    assert len(resultats) == 1
    lieu = resultats[0]
    assert lieu.nom == "Toliara"
    assert lieu.latitude == pytest.approx(-23.354173)
    assert lieu.longitude == pytest.approx(43.66966)
    assert "Toliara" in lieu.libelle and "Madagascar" in lieu.libelle


def test_photon_illisible_est_une_erreur_explicite() -> None:
    from app.core.errors import ServiceExterneError

    with pytest.raises(ServiceExterneError):
        interpreter_photon({"inattendu": True})


def test_ors_rend_distance_duree_et_trace() -> None:
    reponse = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"summary": {"distance": 123456.4, "duration": 5400.2}},
                "geometry": {"coordinates": [[43.66, -23.35], [43.67, -23.36]]},
            }
        ],
    }
    itineraire = interpreter_ors(reponse)
    assert itineraire.distance_m == 123456
    assert itineraire.duree_s == 5400
    assert itineraire.coordonnees[0] == [43.66, -23.35]


def test_photon_client_accepte_un_transport_injecte() -> None:
    """Le **vrai** chemin HTTP : un transport injecté, jamais un mot-clé refusé.

    Régression trouvée en test réel : ``httpx.get(..., transport=...)`` lève un
    ``TypeError`` (seul ``Client`` accepte un transport). Le test bouchonne
    Photon au niveau HTTP et vérifie la requête réellement émise.
    """
    from app.geo.photon import RechercheLieux

    vues: list[httpx.Request] = []

    def repondre(request: httpx.Request) -> httpx.Response:
        vues.append(request)
        return httpx.Response(
            200,
            json={
                "features": [
                    {
                        "properties": {"name": "Toliara", "country": "Madagascar"},
                        "geometry": {"coordinates": [43.66, -23.35]},
                    }
                ]
            },
        )

    client = RechercheLieux(
        "https://photon.test/api",
        timeout_s=1,
        transport=httpx.MockTransport(repondre),
    )
    trouves = client.chercher("Toliara", limite=3)

    assert [t.nom for t in trouves] == ["Toliara"]
    assert len(vues) == 1
    assert vues[0].url.params["q"] == "Toliara"
    assert vues[0].url.params["limit"] == "3"
    assert vues[0].headers["user-agent"].startswith("CARSO-AI")


def test_ors_client_accepte_un_transport_injecte() -> None:
    """Régression : l'appel ORS réel (URL, clé, ordre longitude/latitude)."""
    from app.geo.openrouteservice import ClientItineraires

    vues: list[httpx.Request] = []

    def repondre(request: httpx.Request) -> httpx.Response:
        vues.append(request)
        return httpx.Response(
            200,
            json={
                "features": [
                    {
                        "properties": {"summary": {"distance": 1000, "duration": 600}},
                        "geometry": {"coordinates": [[43.66, -23.35], [43.67, -23.36]]},
                    }
                ]
            },
        )

    client = ClientItineraires(
        "https://ors.test/v2",
        "cle-de-test",
        timeout_s=1,
        transport=httpx.MockTransport(repondre),
    )
    resultat = client.calculer((-23.35, 43.66), (-23.36, 43.67))

    assert (resultat.distance_m, resultat.duree_s) == (1000, 600)
    assert len(vues) == 1
    assert str(vues[0].url).endswith("/v2/directions/driving-car/geojson")
    assert vues[0].headers["authorization"] == "cle-de-test"
    # ORS attend [longitude, latitude] : l'inverse placerait le tracé en mer.
    assert json.loads(vues[0].content)["coordinates"] == [
        [43.66, -23.35],
        [43.67, -23.36],
    ]


def test_ors_sans_itineraire_est_une_erreur_lisible() -> None:
    from app.core.errors import ServiceExterneError

    with pytest.raises(ServiceExterneError) as erreur:
        interpreter_ors({"features": []})
    assert "Aucun itinéraire" in str(erreur.value)


# --- 2. Coordonnées : une paire, des bornes -------------------------------------


def test_coordonnees_partielles_refusees() -> None:
    with pytest.raises(ValidationError):
        valider_coordonnees(-23.35, None)
    with pytest.raises(ValidationError):
        valider_coordonnees(None, 43.66)
    assert valider_coordonnees(None, None) is None


def test_coordonnees_hors_plage_refusees() -> None:
    with pytest.raises(ValidationError):
        valider_coordonnees(91.0, 43.66)
    with pytest.raises(ValidationError):
        valider_coordonnees(-23.35, 181.0)
    paire = valider_coordonnees(-23.3541739, 43.6696611)
    assert paire == (-23.354174, 43.669661)  # arrondie à 6 décimales


def test_creer_un_lieu_avec_coordonnees(api_session_factory: sessionmaker[Session]) -> None:
    session = api_session_factory()
    try:
        lieu = LieuService(session).creer(LieuInput(
                nom= "CCI Tsimenatsy",
                ville="Toliara",
                latitude=-23.3541,
                longitude=43.6696,
            ))
        session.commit()
        relu = session.scalars(select(Lieu).where(Lieu.nom == "CCI Tsimenatsy")).one()
        assert relu.latitude == pytest.approx(-23.3541)
        assert relu.longitude == pytest.approx(43.6696)
        assert lieu.id == relu.id
    finally:
        session.close()


# --- 3. Cache : le quota n'est pas dépensé deux fois ----------------------------


class _ClientDouble:
    """Compte les appels ORS et renvoie toujours le même résultat."""

    def __init__(self, distance_m: int = 100_000) -> None:
        self.appels = 0
        self.distance_m = distance_m

    def calculer(self, origine, destination):
        from app.geo.openrouteservice import ItineraireCalcule

        self.appels += 1
        return ItineraireCalcule(
            distance_m=self.distance_m,
            duree_s=5400,
            coordonnees=[[43.66, -23.35], [43.67, -23.36]],
        )


def _service(session: Session, client: _ClientDouble) -> ItineraireService:
    return ItineraireService(session, client=client)


def test_deux_calculs_identiques_ne_font_qu_un_appel(
    api_session_factory: sessionmaker[Session],
) -> None:
    session = api_session_factory()
    try:
        lieu = LieuService(session).creer(LieuInput(nom="A",
            latitude=-23.35,
            longitude=43.66))
        destination = LieuService(session).creer(LieuInput(nom="B",
            latitude=-23.36,
            longitude=43.67))
        session.commit()
        client = _ClientDouble()
        demande = dict(
            origine=PointItineraire(lieu_id=lieu.id),
            destination=PointItineraire(lieu_id=destination.id),
        )
        premier = _service(session, client).calculer(**demande)
        second = _service(session, client).calculer(**demande)

        assert client.appels == 1  # le second calcul est relu du cache
        assert premier.depuis_cache is False
        assert second.depuis_cache is True
        assert second.distance_m == premier.distance_m
        assert second.geometrie == premier.geometrie
    finally:
        session.close()


def test_deplacer_le_marqueur_invalide_le_trajet(
    api_session_factory: sessionmaker[Session],
) -> None:
    session = api_session_factory()
    try:
        lieu = LieuService(session).creer(LieuInput(nom="A",
            latitude=-23.35,
            longitude=43.66))
        destination = LieuService(session).creer(LieuInput(nom="B",
            latitude=-23.36,
            longitude=43.67))
        session.commit()
        client = _ClientDouble()
        demande = dict(
            origine=PointItineraire(lieu_id=lieu.id),
            destination=PointItineraire(lieu_id=destination.id),
        )
        _service(session, client).calculer(**demande)
        LieuService(session).positionner(lieu.id, -23.40, 43.70)
        session.commit()
        _service(session, client).calculer(**demande)

        assert client.appels == 2  # la position a changé : recalcul justifié
    finally:
        session.close()


# --- 4. Carburant : Decimal, aller-retour, surcharge ----------------------------


def test_carburant_aller_retour_et_surcharge(
    api_session_factory: sessionmaker[Session],
) -> None:
    session = api_session_factory()
    try:
        ParametresVoyageService(session).modifier(
            ParametresVoyageMiseAJour(prix_litre=2_500, devise="MGA")
        )
        session.commit()
        lieu = LieuService(session).creer(LieuInput(nom="A",
            latitude=-23.35,
            longitude=43.66))
        destination = LieuService(session).creer(LieuInput(nom="B",
            latitude=-23.36,
            longitude=43.67))
        session.commit()

        service = _service(session, _ClientDouble(distance_m=100_000))
        simple = service.calculer(
            origine=PointItineraire(lieu_id=lieu.id),
            destination=PointItineraire(lieu_id=destination.id),
        )
        double = service.calculer(
            origine=PointItineraire(lieu_id=lieu.id),
            destination=PointItineraire(lieu_id=destination.id),
            aller_retour=True,
        )
        surcharge = service.calculer(
            origine=PointItineraire(lieu_id=lieu.id),
            destination=PointItineraire(lieu_id=destination.id),
            consommation_l_100km=10.0,
        )

        assert simple.distance_totale_m == 100_000
        assert simple.litres == pytest.approx(8.0)  # 100 km × 8 L/100
        assert simple.cout == pytest.approx(20_000.0)  # 8 L × 2 500 MGA
        assert double.distance_totale_m == 200_000
        assert double.litres == pytest.approx(16.0)
        assert double.cout == pytest.approx(40_000.0)
        assert double.devise == "MGA"
        assert surcharge.litres == pytest.approx(10.0)  # surcharge ponctuelle
        assert double.distance_m == simple.distance_m  # le cache garde le trajet simple
    finally:
        session.close()


def test_lieu_sans_position_refuse_pour_un_trajet(
    api_session_factory: sessionmaker[Session],
) -> None:
    session = api_session_factory()
    try:
        lieu = LieuService(session).creer(LieuInput(nom="Sans position"))
        destination = LieuService(session).creer(LieuInput(nom="B",
            latitude=-23.36,
            longitude=43.67))
        session.commit()
        with pytest.raises(ValidationError) as erreur:
            _service(session, _ClientDouble()).calculer(
                origine=PointItineraire(lieu_id=lieu.id),
                destination=PointItineraire(lieu_id=destination.id),
            )
        assert "n'a pas de position" in str(erreur.value)
    finally:
        session.close()


# --- 5. Périmètre : le formateur voit ses missions ------------------------------


def _perimetre_formateur(mission_ids: set) -> Perimetre:
    return Perimetre(
        role="formateur",
        utilisateur_id=None,
        equipe_id=None,
        missions_autorisees=frozenset(mission_ids),
        equipes_autorisees=frozenset(),
        est_complet=False,
    )


def test_le_formateur_ne_lit_que_les_lieux_de_ses_missions(
    api_session_factory: sessionmaker[Session],
) -> None:
    session = api_session_factory()
    try:
        from app.domain.execution import Mission
        from app.domain.organization import Organisation

        lieu_sien = LieuService(session).creer(LieuInput(nom="Lieu sien"))
        lieu_autre = LieuService(session).creer(LieuInput(nom="Lieu étranger"))
        organisation = Organisation(nom="Organisation du formateur")
        session.add(organisation)
        session.flush()
        mission = Mission(
            organisation_id=organisation.id,
            reference="MISSION-FORMATEUR",
            titre="Mission du formateur",
            statut="en_cours",
            lieu_id=lieu_sien.id,
        )
        session.add(mission)
        session.commit()

        perimetre = _perimetre_formateur({mission.id})
        visibles = LieuService(session).lister_pour_perimetre(perimetre)
        assert [lieu.nom for lieu in visibles] == ["Lieu sien"]

        atteint = LieuService(session).obtenir_pour_perimetre(lieu_sien.id, perimetre)
        assert atteint.id == lieu_sien.id
        with pytest.raises(NotFoundError):
            LieuService(session).obtenir_pour_perimetre(lieu_autre.id, perimetre)
    finally:
        session.close()


# --- 6. Paramètres de voyage -----------------------------------------------------


def test_parametres_defauts_et_bornes(api_session_factory: sessionmaker[Session]) -> None:
    session = api_session_factory()
    try:
        service = ParametresVoyageService(session)
        ligne = service.lire()
        assert float(ligne.consommation_l_100km) == 8.0
        assert ligne.devise == "MGA"

        with pytest.raises(ValidationError):
            service.modifier(ParametresVoyageMiseAJour(consommation_l_100km=0.0))
        with pytest.raises(ValidationError):
            service.modifier(ParametresVoyageMiseAJour(prix_litre=-1))
        with pytest.raises(ValidationError):
            service.modifier(ParametresVoyageMiseAJour(origine_latitude=91.0))
        corrige = service.modifier(
            ParametresVoyageMiseAJour(
                origine_libelle="Siège CARSO — Toliara",
                origine_latitude=-23.3541,
                origine_longitude=43.6696,
            )
        )
        assert corrige.origine_libelle == "Siège CARSO — Toliara"
        assert corrige.origine_latitude == pytest.approx(-23.3541)
    finally:
        session.close()


# --- 7. Surface HTTP -------------------------------------------------------------


def test_cycle_http_lieu_recherche_position(
    api_client: TestClient, api_session_factory: sessionmaker[Session], monkeypatch
) -> None:
    compte = _compte()
    session = api_session_factory()
    try:
        session.add(compte)
        session.commit()
    finally:
        session.close()
    _brancher(api_client, compte)

    # La recherche ne crée rien : elle ne fait que suggérer.
    class _Reponse:
        status_code = 200

        def json(self):
            return {
                "features": [
                    {
                        "properties": {"name": "Toliara", "country": "Madagascar"},
                        "geometry": {"coordinates": [43.66966, -23.354173]},
                    }
                ]
            }

    import app.geo.photon as photon

    monkeypatch.setattr(photon.httpx, "get", lambda *a, **k: _Reponse())
    recherche = api_client.get(f"{PRÉFIXE}/lieux/recherche", params={"q": "Toliara"})
    assert recherche.status_code == 200, recherche.text
    assert recherche.json()[0]["latitude"] == pytest.approx(-23.354173)

    creation = api_client.post(
        f"{PRÉFIXE}/lieux",
        json={"nom": "CCI Tsimenatsy", "latitude": -23.3541, "longitude": 43.6696},
    )
    assert creation.status_code == 201, creation.text
    lieu = creation.json()
    assert lieu["latitude"] == pytest.approx(-23.3541)

    position = api_client.patch(
        f"{PRÉFIXE}/lieux/{lieu['id']}/position",
        json={"latitude": -23.36, "longitude": 43.67},
    )
    assert position.status_code == 200, position.text
    assert position.json()["latitude"] == pytest.approx(-23.36)

    audit = api_client.get(
        f"{PRÉFIXE}/audit", params={"action": "lieu.position_modifiee"}
    )
    assert audit.status_code == 200, audit.text
    assert "lieu.position_modifiee" in audit.text
    assert "lieu.position_modifiee" in audit.text


def test_itineraire_http_utilise_le_cache(
    api_client: TestClient, api_session_factory: sessionmaker[Session], monkeypatch
) -> None:

    compte = _compte()
    session = api_session_factory()
    try:
        session.add(compte)
        session.commit()
        lieu_a = LieuService(session).creer(LieuInput(nom="A",
            latitude=-23.35,
            longitude=43.66))
        lieu_b = LieuService(session).creer(LieuInput(nom="B",
            latitude=-23.36,
            longitude=43.67))
        session.commit()
        identifiants = (str(lieu_a.id), str(lieu_b.id))
    finally:
        session.close()
    _brancher(api_client, compte)

    appels = {"n": 0}

    class _Reponse:
        status_code = 200

        def json(self):
            return {
                "features": [
                    {
                        "properties": {"summary": {"distance": 100000.0, "duration": 5400.0}},
                        "geometry": {"coordinates": [[43.66, -23.35], [43.67, -23.36]]},
                    }
                ]
            }

    import app.geo.openrouteservice as ors

    # La clé n'existe pas dans l'environnement de test : on la pose pour que
    # le client atteigne le faux `Client.post` (sinon il s'arrête avant).
    monkeypatch.setenv("HEIGIT_API_KEY", "cle-de-test")
    # ``get_settings`` est mémoïsé : sans purge, la clé posée ici serait
    # invisible du premier appel déjà fait par un autre test.
    from app.core.config import get_settings as _get_settings

    _get_settings.cache_clear()

    def _faux_post(self, *args, **kwargs):
        appels["n"] += 1
        return _Reponse()

    # Le client passe par `httpx.Client(...).post` (pour accepter un transport
    # injecté) : c'est cette méthode que le bouchon remplace.
    monkeypatch.setattr(ors.httpx.Client, "post", _faux_post)
    corps = {
        "origine": {"lieu_id": identifiants[0]},
        "destination": {"lieu_id": identifiants[1]},
        "aller_retour": True,
    }
    premier = api_client.post(f"{PRÉFIXE}/lieux/itineraire", json=corps)
    assert premier.status_code == 200, premier.text
    second = api_client.post(f"{PRÉFIXE}/lieux/itineraire", json=corps)

    assert appels["n"] == 1  # une seule dépense de quota
    assert premier.json()["distance_totale_m"] == 200_000
    assert premier.json()["geometrie"] is not None
    assert second.json()["depuis_cache"] is True


def test_itineraire_expose_les_troncons_classes(
    api_client: TestClient, api_session_factory: sessionmaker[Session], monkeypatch
) -> None:
    """La réponse HTTP porte les portions colorées, avec leurs indices.

    Le tracé a quatre points ; ORS (simulé) déclare deux voies ; Overpass
    (simulé) donne un bon état puis une piste classée par ``tracktype``.
    """
    from app.geo.openrouteservice import ItineraireCalcule

    compte = _compte()
    session = api_session_factory()
    try:
        session.add(compte)
        session.commit()
        lieu_a = LieuService(session).creer(
            LieuInput(nom="Toliara", latitude=-23.35, longitude=43.66)
        )
        lieu_b = LieuService(session).creer(
            LieuInput(nom="Antananarivo", latitude=-18.88, longitude=47.51)
        )
        session.commit()
        identifiants = (str(lieu_a.id), str(lieu_b.id))
    finally:
        session.close()
    _brancher(api_client, compte)

    from datetime import UTC, datetime, timedelta

    import app.geo.openrouteservice as ors
    import app.geo.overpass as overpass
    from app.domain.geo import OsmVoie

    monkeypatch.setattr(
        ors.ClientItineraires,
        "calculer",
        lambda self, origine, destination: ItineraireCalcule(
            distance_m=1000,
            duree_s=60,
            coordonnees=[[43.66, -23.35], [43.67, -23.36], [43.68, -23.37], [43.69, -23.38]],
            extras={"osmium": [[0, 1, 101], [1, 3, 102]]},
        ),
    )

    def _overpass_interdit(*args, **kwargs):
        raise AssertionError("Overpass ne doit pas être appelé pendant la requête")

    monkeypatch.setattr(overpass.ClientOverpass, "tags_voies", _overpass_interdit)
    monkeypatch.setattr(overpass.ClientOverpass, "tags_autour", _overpass_interdit)

    # Les tags exacts sont déjà en cache PostgreSQL : la requête doit classer
    # sans aucun appel réseau — l'affinage Overpass est une tâche d'arrière-plan.
    session = api_session_factory()
    try:
        session.add_all(
            [
                OsmVoie(
                    osm_id=101,
                    tags={"highway": "primary", "smoothness": "good"},
                    expire_le=datetime.now(UTC) + timedelta(days=1),
                ),
                OsmVoie(
                    osm_id=102,
                    tags={"highway": "track", "tracktype": "grade3"},
                    expire_le=datetime.now(UTC) + timedelta(days=1),
                ),
            ]
        )
        session.commit()
    finally:
        session.close()

    reponse = api_client.post(
        f"{PRÉFIXE}/lieux/itineraire",
        json={
            "origine": {"lieu_id": identifiants[0]},
            "destination": {"lieu_id": identifiants[1]},
        },
    )
    assert reponse.status_code == 200, reponse.text
    troncons = reponse.json()["troncons"]
    assert [troncon["niveau"] for troncon in troncons] == ["bon", "mauvais"]
    assert troncons[0]["nature"] == "physique"
    assert troncons[1]["estime"] is True  # tracktype = indicateur secondaire
    assert troncons[0]["osmid"] == 101
    # Couverture continue de la géométrie : aucun trou à la jointure.
    assert troncons[0]["debut"] == 0
    assert troncons[-1]["fin"] == 3
    for precedent, suivant in zip(troncons, troncons[1:], strict=False):
        assert suivant["debut"] == precedent["fin"]
    assert all(troncon["distance_m"] > 0 for troncon in troncons)


def test_itineraire_sans_cle_rend_un_message_actionnable(
    api_client: TestClient, api_session_factory: sessionmaker[Session], monkeypatch
) -> None:
    compte = _compte()
    session = api_session_factory()
    try:
        session.add(compte)
        session.commit()
        lieu_a = LieuService(session).creer(LieuInput(nom="A",
            latitude=-23.35,
            longitude=43.66))
        lieu_b = LieuService(session).creer(LieuInput(nom="B",
            latitude=-23.36,
            longitude=43.67))
        session.commit()
        identifiants = (str(lieu_a.id), str(lieu_b.id))
    finally:
        session.close()
    _brancher(api_client, compte)

    import app.geo.openrouteservice as ors

    monkeypatch.setattr(ors.httpx.Client, "post", lambda self, *a, **k: (_ for _ in ()).throw(
        __import__("app.core.errors", fromlist=["ServiceExterneError"]).ServiceExterneError(
            "Service d'itinéraires non configuré : renseignez HEIGIT_API_KEY",
            details={"http": 401},
        )
    ))
    refus = api_client.post(
        f"{PRÉFIXE}/lieux/itineraire",
        json={
            "origine": {"lieu_id": identifiants[0]},
            "destination": {"lieu_id": identifiants[1]},
        },
    )
    assert refus.status_code == 502, refus.text
    assert "HEIGIT_API_KEY" in refus.json()["error"]["message"]
