"""Catalogue des fournisseurs et modèles d'IA — service applicatif.

Ce que ces tests verrouillent :

- une **connexion** se règle une fois (protocole, URL de base, clé chiffrée) et
  ses modèles en héritent : changer de modèle ne demande plus de ressaisir la clé ;
- aucune **clé** ne ressort du catalogue (seulement « une clé est enregistrée ») ;
- pas de **doublon** de fournisseur ni de modèle ;
- les **dépendances** avant suppression sont refusées explicitement (modèles
  enregistrés, configuration appliquée au runtime) ;
- la **découverte** est par protocole : listage compatible OpenAI quand la route
  existe, refus explicite sinon (ajout manuel), sans inventer de liste.
"""

from __future__ import annotations

import httpx
import pytest

from app.agents.decouverte_modeles import DecouverteIndisponible, decouvrir_modeles


class _ReponseModeles:
    """Réponse ``/models`` minimale, sans réseau (lecture de la fenêtre publiée)."""

    def __init__(self, modeles: list[dict]) -> None:
        self._modeles = modeles

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"data": self._modeles}
from app.application.services.agent_runtime_service import AgentRuntimeService
from app.application.services.catalogue_modeles_service import CatalogueModelesService
from app.core.chiffrement import dechiffrer
from app.core.errors import ConflictError
from app.core.errors import ValidationError as BusinessValidationError


def _service(session) -> CatalogueModelesService:
    return CatalogueModelesService(session, actor_id="admin-test")


@pytest.fixture(autouse=True)
def _cache_fenetres_vide(monkeypatch: pytest.MonkeyPatch):
    """Cache global vidé, et réseau coupé par défaut.

    Le cache des fenêtres publiées est global : un test ne doit pas nourrir
    l'autre. Et aucun test du catalogue ne doit dépendre d'un vrai accès à
    ``/models`` — ceux qui testent une valeur publiée posent leur propre double.
    """
    from app.agents.providers import _FENETRES_PUBLIEES

    _FENETRES_PUBLIEES.clear()
    monkeypatch.setattr(
        "httpx.get",
        lambda *a, **k: (_ for _ in ()).throw(ConnectionError("hors ligne (test)")),
    )
    yield
    _FENETRES_PUBLIEES.clear()


# --- Fournisseurs -----------------------------------------------------------


def test_fournisseur_enregistre_une_connexion_et_chiffre_la_cle(
    api_session_factory, cle_chiffrement: str
) -> None:
    session = api_session_factory()
    service = _service(session)

    fournisseur = service.ajouter_fournisseur(
        nom="Agrégateur interne",
        protocole="openai_compatible",
        base_url="https://ia.exemple.mg/v1",
        api_key="sk-secret-123",
    )
    session.commit()

    assert fournisseur.api_key_chiffree
    assert "sk-secret-123" not in fournisseur.api_key_chiffree
    assert dechiffrer(fournisseur.api_key_chiffree) == "sk-secret-123"
    # La vue exposée ne porte jamais la clé, seulement sa présence.
    vue = fournisseur.resume()
    assert vue["cle_enregistree"] is True
    assert "sk-secret-123" not in str(vue)


def test_fournisseur_refuse_un_nom_deja_pris(api_session_factory, cle_chiffrement: str) -> None:
    session = api_session_factory()
    service = _service(session)
    service.ajouter_fournisseur(nom="Principal", protocole="openai", api_key="k")
    session.commit()

    with pytest.raises(ConflictError):
        service.ajouter_fournisseur(nom="  principal ", protocole="openai", api_key="k")


def test_fournisseur_compatible_exige_une_url_de_base(api_session_factory) -> None:
    session = api_session_factory()
    service = _service(session)

    with pytest.raises(BusinessValidationError, match="URL de base"):
        service.ajouter_fournisseur(nom="Passerelle", protocole="openai_compatible")


def test_fournisseur_refuse_un_protocole_inconnu(api_session_factory) -> None:
    session = api_session_factory()
    service = _service(session)

    with pytest.raises(BusinessValidationError, match="Protocole inconnu"):
        service.ajouter_fournisseur(nom="X", protocole="mistral-maison")


def test_modifier_sans_cle_conserve_la_cle(api_session_factory, cle_chiffrement: str) -> None:
    session = api_session_factory()
    service = _service(session)
    fournisseur = service.ajouter_fournisseur(
        nom="Principal",
        protocole="openai_compatible",
        base_url="https://a.exemple/v1",
        api_key="sk-1",
    )
    session.commit()

    service.modifier_fournisseur(fournisseur.id, nom="Principal+", base_url="https://b.exemple/v1")
    session.commit()

    assert fournisseur.nom == "Principal+"
    assert fournisseur.base_url == "https://b.exemple/v1"
    assert dechiffrer(fournisseur.api_key_chiffree) == "sk-1"


def test_modifier_peut_retirer_la_cle(api_session_factory, cle_chiffrement: str) -> None:
    session = api_session_factory()
    service = _service(session)
    fournisseur = service.ajouter_fournisseur(nom="Principal", protocole="openai", api_key="sk-1")
    session.commit()

    service.modifier_fournisseur(fournisseur.id, conserver_cle=False)
    session.commit()

    assert fournisseur.api_key_chiffree is None


def test_cle_refusee_sans_chiffrement(api_session_factory, sans_chiffrement: None) -> None:
    session = api_session_factory()
    service = _service(session)

    with pytest.raises(BusinessValidationError, match="Chiffrement indisponible"):
        service.ajouter_fournisseur(nom="Principal", protocole="openai", api_key="sk-1")


# --- Modèles ----------------------------------------------------------------


def test_modeles_sans_doublon(api_session_factory, cle_chiffrement: str) -> None:
    session = api_session_factory()
    service = _service(session)
    fournisseur = service.ajouter_fournisseur(nom="Principal", protocole="openai", api_key="k")

    crees, ignores = service.ajouter_modeles(
        fournisseur.id, ["gpt-5.1", "gpt-5.1", " gpt-5-mini ", ""]
    )
    session.commit()
    crees_2, ignores_2 = service.ajouter_modeles(fournisseur.id, ["gpt-5.1", "o3"])
    session.commit()

    assert [modele.nom_modele for modele in crees] == ["gpt-5.1", "gpt-5-mini"]
    assert ignores == ["gpt-5.1"]
    assert [modele.nom_modele for modele in crees_2] == ["o3"]
    assert ignores_2 == ["gpt-5.1"]


def test_supprimer_un_fournisseur_avec_modeles_est_refuse(
    api_session_factory, cle_chiffrement: str
) -> None:
    session = api_session_factory()
    service = _service(session)
    fournisseur = service.ajouter_fournisseur(nom="Principal", protocole="openai", api_key="k")
    service.ajouter_modeles(fournisseur.id, ["gpt-5.1"])
    session.commit()

    with pytest.raises(ConflictError, match="porte"):
        service.supprimer_fournisseur(fournisseur.id)

    # Les modèles retirés, la suppression passe.
    modele = service.modeles.lister_pour(fournisseur.id)[0]
    service.supprimer_modele(modele.id)
    session.commit()
    service.supprimer_fournisseur(fournisseur.id)
    session.commit()
    assert service.lister() == []


def test_modele_applique_ne_se_supprime_pas(api_session_factory, cle_chiffrement: str) -> None:
    session = api_session_factory()
    service = _service(session)
    fournisseur = service.ajouter_fournisseur(nom="Principal", protocole="openai", api_key="k")
    crees, _ = service.ajouter_modeles(fournisseur.id, ["gpt-5.1"])
    session.commit()

    # La configuration runtime applique ce modèle (porte d'écriture unique).
    AgentRuntimeService(session, actor_id="admin-test").enregistrer(
        provider="openai", model="gpt-5.1", base_url=None, api_key="k"
    )
    session.commit()

    with pytest.raises(ConflictError, match="appliqué au runtime"):
        service.supprimer_modele(crees[0].id)

    # Le fournisseur non plus : il porte des modèles et la configuration
    # appliquée (le premier refus suffit — on ne force pas la cascade).
    with pytest.raises(ConflictError):
        service.supprimer_fournisseur(fournisseur.id)


def test_fournisseur_vide_partageant_l_endpoint_applique_se_supprime(
    api_session_factory, cle_chiffrement: str
) -> None:
    """Deux connexions au même endpoint sont légitimes : seule compte la
    configuration réellement appliquée (son modèle), pas l'URL.

    Régression : la garde comparait protocole + URL, si bien qu'un fournisseur
    vide créé sur l'endpoint déjà actif devenait indélébile.
    """
    session = api_session_factory()
    service = _service(session)
    principal = service.ajouter_fournisseur(nom="Principal", protocole="openai", api_key="k")
    crees, _ = service.ajouter_modeles(principal.id, ["gpt-5.1"])
    session.commit()
    AgentRuntimeService(session, actor_id="admin-test").enregistrer(
        provider="openai", model="gpt-5.1", base_url=None, api_key="k"
    )
    session.commit()

    second = service.ajouter_fournisseur(nom="Même endpoint", protocole="openai", api_key="k")
    session.commit()

    assert [modele.nom_modele for modele in crees] == ["gpt-5.1"]
    service.supprimer_fournisseur(second.id)
    session.commit()

    assert [fournisseur["nom"] for fournisseur in service.lister()] == ["Principal"]


def test_lister_ne_fait_aucun_appel_reseau(
    api_session_factory, cle_chiffrement: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ouvrir le catalogue ne doit jamais attendre un fournisseur injoignable.

    La liste vient de la base ; les fenêtres publiées déjà connues sont reprises
    du cache, les autres s'affichent « non lues » avec la raison « lecture en
    attente » le temps de la lecture groupée.
    """
    monkeypatch.setattr(
        "httpx.get",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("appel réseau interdit")),
    )
    session = api_session_factory()
    service = _service(session)
    fournisseur = service.ajouter_fournisseur(
        nom="Passerelle",
        protocole="openai_compatible",
        base_url="https://ia.exemple.mg/v1",
    )
    service.ajouter_modeles(fournisseur.id, ["modele-maison"])
    session.commit()

    vue = service.lister()[0]["modeles"][0]  # ne lève pas : aucun réseau

    assert vue["fenetre_publiee"] is None
    assert vue["fenetre_provenance"] == "non_lue"
    assert vue["fenetre_raison"] == "lecture des fenêtres en attente"
    session.close()


def test_les_valeurs_lues_survivent_a_la_liste_sans_reseau(
    api_session_factory, cle_chiffrement: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Une valeur déjà connue garde sa provenance, même réseau coupé."""
    monkeypatch.setattr(
        "httpx.get",
        lambda *a, **k: _ReponseModeles(
            [{"id": "modele-maison", "context_length": 1_000_000}]
        ),
    )
    session = api_session_factory()
    service = _service(session)
    fournisseur = service.ajouter_fournisseur(
        nom="Passerelle",
        protocole="openai_compatible",
        base_url="https://ia.exemple.mg/v1",
    )
    service.ajouter_modeles(fournisseur.id, ["modele-maison"])
    session.commit()

    lues = service.fenetres_catalogue()
    assert lues[0]["fenetre_provenance"] == "publiee"

    # Réseau strictement interdit : la liste doit vivre du cache.
    monkeypatch.setattr(
        "httpx.get",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("appel réseau interdit")),
    )
    vue = service.lister()[0]["modeles"][0]
    assert vue["fenetre_publiee"] == 1_000_000
    assert vue["fenetre_effective"] == 1_000_000
    assert vue["fenetre_provenance"] == "publiee"
    session.close()


def test_fenetres_catalogue_parallelise_les_fournisseurs(
    api_session_factory, cle_chiffrement: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deux fournisseurs lents ne s'additionnent pas : ils sont lus en parallèle."""
    import time

    from app.agents.providers import _LectureFenetre

    session = api_session_factory()
    service = _service(session)
    for nom in ("A", "B"):
        fournisseur = service.ajouter_fournisseur(
            nom=f"Fournisseur {nom}",
            protocole="openai_compatible",
            base_url=f"https://{nom.lower()}.exemple/v1",
        )
        service.ajouter_modeles(fournisseur.id, [f"modele-{nom.lower()}"])
    session.commit()

    def _lente(**params):
        time.sleep(0.5)  # simule un fournisseur qui met 500 ms à répondre
        return {
            nom: _LectureFenetre(1_000_000, True) for nom in params["modeles"]
        }

    monkeypatch.setattr("app.agents.providers.fenetres_publiees", _lente)

    debut = time.perf_counter()
    lues = service.fenetres_catalogue()
    duree = time.perf_counter() - debut

    assert len(lues) == 2
    assert all(lue["fenetre_provenance"] == "publiee" for lue in lues)
    # En série : 1,0 s au minimum. En parallèle : ~0,5 s.
    assert duree < 0.9, duree
    session.close()


def test_fenetre_declaree_persistee_et_relue(
    api_session_factory, cle_chiffrement: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le catalogue porte la fenêtre de chaque modèle, pas seulement l'appliqué."""
    monkeypatch.setattr(
        "httpx.get",
        lambda *a, **k: _ReponseModeles([{"id": "modele-maison", "context_length": None}]),
    )
    session = api_session_factory()
    service = _service(session)
    fournisseur = service.ajouter_fournisseur(
        nom="Passerelle",
        protocole="openai_compatible",
        base_url="https://ia.exemple.mg/v1",
    )
    crees, _ = service.ajouter_modeles(fournisseur.id, ["modele-maison"])
    session.commit()

    service.modifier_fenetre_modele(crees[0].id, fenetre_contexte_jetons=128_000)
    session.commit()

    vue = service.lire_modele(crees[0].id)
    assert vue["fenetre_contexte_jetons"] == 128_000
    assert vue["fenetre_effective"] == 128_000
    assert vue["fenetre_provenance"] == "manuelle"

    # La liste du catalogue porte la même valeur, et l'audit l'a consignée.
    catalogue = service.lister()
    assert catalogue[0]["modeles"][0]["fenetre_effective"] == 128_000
    from sqlalchemy import select

    from app.domain.document import AuditEvent
    from app.domain.enums import ActionAudit

    actions = [e.action for e in session.scalars(select(AuditEvent)).all()]
    assert ActionAudit.MODELE_IA_MODIFIE.value in actions

    # ``null`` efface la déclaration : le repli reprend la main.
    service.modifier_fenetre_modele(crees[0].id, fenetre_contexte_jetons=None)
    session.commit()
    assert service.lire_modele(crees[0].id)["fenetre_contexte_jetons"] is None
    session.close()


def test_fenetre_declaree_hors_limites_refusee(
    api_session_factory, cle_chiffrement: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Aucun appel réseau : la lecture de la fenêtre publiée n'est pas le sujet.
    monkeypatch.setattr(
        "httpx.get", lambda *a, **k: (_ for _ in ()).throw(ConnectionError("hors ligne"))
    )
    session = api_session_factory()
    service = _service(session)
    fournisseur = service.ajouter_fournisseur(nom="Principal", protocole="openai", api_key="k")
    crees, _ = service.ajouter_modeles(fournisseur.id, ["gpt-5.1"])
    session.commit()

    for valeur in (0, 999, 100_000_001):
        with pytest.raises(BusinessValidationError, match="hors limites"):
            service.modifier_fenetre_modele(crees[0].id, fenetre_contexte_jetons=valeur)
    session.rollback()
    assert service.modeles.obtenir_avec_fournisseur(crees[0].id).fenetre_contexte_jetons is None
    session.close()


def test_fenetre_publiee_prime_sur_la_declaration(
    api_session_factory, cle_chiffrement: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Une donnée publiée ne se contredit pas depuis le catalogue."""
    monkeypatch.setattr(
        "httpx.get",
        lambda *a, **k: _ReponseModeles(
            [{"id": "modele-maison", "context_length": 1_048_576}]
        ),
    )
    session = api_session_factory()
    service = _service(session)
    fournisseur = service.ajouter_fournisseur(
        nom="Passerelle",
        protocole="openai_compatible",
        base_url="https://ia.exemple.mg/v1",
    )
    crees, _ = service.ajouter_modeles(fournisseur.id, ["modele-maison"])
    session.commit()

    service.modifier_fenetre_modele(crees[0].id, fenetre_contexte_jetons=128_000)
    session.commit()

    vue = service.lire_modele(crees[0].id)
    assert vue["fenetre_publiee"] == 1_048_576
    assert vue["fenetre_effective"] == 1_048_576
    assert vue["fenetre_provenance"] == "publiee"
    session.close()


def test_lecture_impossible_affiche_le_repli(
    api_session_factory, cle_chiffrement: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "httpx.get", lambda *a, **k: (_ for _ in ()).throw(ConnectionError("blip"))
    )
    session = api_session_factory()
    service = _service(session)
    fournisseur = service.ajouter_fournisseur(nom="Principal", protocole="openai", api_key="k")
    service.ajouter_modeles(fournisseur.id, ["gpt-5.1"])
    session.commit()

    from app.core.config import get_settings

    vue = service.lister()[0]["modeles"][0]
    assert vue["fenetre_publiee"] is None
    # Repli de déploiement (200 000 par défaut) : la valeur exacte vient des
    # réglages, pas d'un chiffre figé dans le test.
    assert vue["fenetre_effective"] == get_settings().agent_max_input_tokens
    assert vue["fenetre_provenance"] == "non_lue"
    session.close()


def test_preparer_application_reprend_la_fenetre_declaree(
    api_session_factory, cle_chiffrement: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "httpx.get",
        lambda *a, **k: _ReponseModeles([{"id": "modele-maison", "context_length": None}]),
    )
    session = api_session_factory()
    service = _service(session)
    fournisseur = service.ajouter_fournisseur(
        nom="Passerelle",
        protocole="openai_compatible",
        base_url="https://ia.exemple.mg/v1",
        api_key="sk-abc",
    )
    crees, _ = service.ajouter_modeles(fournisseur.id, ["modele-maison"])
    service.modifier_fenetre_modele(crees[0].id, fenetre_contexte_jetons=64_000)
    session.commit()

    params = service.preparer_application(crees[0].id)
    assert params["fenetre_contexte_jetons"] == 64_000
    session.close()


def test_preparer_application_reprend_la_connexion(
    api_session_factory, cle_chiffrement: str
) -> None:
    session = api_session_factory()
    service = _service(session)
    fournisseur = service.ajouter_fournisseur(
        nom="Passerelle",
        protocole="openai_compatible",
        base_url="https://ia.exemple.mg/v1",
        api_key="sk-abc",
    )
    crees, _ = service.ajouter_modeles(fournisseur.id, ["modele-maison"])
    session.commit()

    params = service.preparer_application(crees[0].id)

    assert params["provider"] == "openai_compatible"
    assert params["model"] == "modele-maison"
    assert params["base_url"] == "https://ia.exemple.mg/v1"
    assert params["api_key"] == "sk-abc"


# --- Découverte -------------------------------------------------------------


def _client(reponse: httpx.Response) -> httpx.AsyncClient:
    """Client httpx dont la seule requête rend ``reponse`` (aucun réseau)."""

    def _rendre(request: httpx.Request) -> httpx.Response:
        request_url = request.url
        assert request_url.path.endswith("/models")
        return reponse

    return httpx.AsyncClient(transport=httpx.MockTransport(_rendre))


def test_decouverte_compatible_openai_liste_les_modeles() -> None:
    import asyncio

    reponse = httpx.Response(
        200, json={"data": [{"id": "modele-a"}, {"id": "modele-b"}, {"id": "modele-a"}]}
    )
    client = _client(reponse)

    modeles = asyncio.run(
        decouvrir_modeles(
            protocole="openai_compatible",
            base_url="https://ia.exemple.mg/v1",
            api_key="sk-abc",
            client=client,
        )
    )

    assert modeles == ["modele-a", "modele-b"]


def test_decouverte_route_absente_invite_a_l_ajout_manuel() -> None:
    import asyncio

    client = _client(httpx.Response(404, json={"error": "not found"}))

    with pytest.raises(DecouverteIndisponible, match="à la main"):
        asyncio.run(
            decouvrir_modeles(
                protocole="openai_compatible",
                base_url="https://ia.exemple.mg/v1",
                api_key=None,
                client=client,
            )
        )


def test_decouverte_cle_refusee_est_une_erreur_de_configuration() -> None:
    import asyncio

    from app.agents.providers import AgentConfigurationError

    client = _client(httpx.Response(401, json={"error": "unauthorized"}))

    with pytest.raises(AgentConfigurationError, match="Clé refusée"):
        asyncio.run(
            decouvrir_modeles(
                protocole="openai",
                base_url=None,
                api_key="mauvaise",
                client=client,
            )
        )


def test_decouverte_google_genai_liste_les_modeles_de_conversation() -> None:
    """Gemini : aucun URL exigée, et seuls les modèles de chat sont retenus."""
    import asyncio

    vus: dict[str, object] = {}

    def _rendre(request: httpx.Request) -> httpx.Response:
        vus["url"] = str(request.url)
        vus["cle"] = request.headers.get("x-goog-api-key")
        vus["autorisation"] = request.headers.get("authorization")
        return httpx.Response(
            200,
            json={
                "models": [
                    {
                        "name": "models/gemini-3-flash",
                        "supportedGenerationMethods": ["generateContent", "countTokens"],
                    },
                    {
                        "name": "models/text-embedding-004",
                        "supportedGenerationMethods": ["embedContent"],
                    },
                    {
                        "name": "models/gemini-3-flash",
                        "supportedGenerationMethods": ["generateContent"],
                    },
                ]
            },
        )

    modeles = asyncio.run(
        decouvrir_modeles(
            protocole="google_genai",
            base_url=None,
            api_key="cle-google",
            client=httpx.AsyncClient(transport=httpx.MockTransport(_rendre)),
        )
    )

    assert modeles == ["gemini-3-flash"]
    assert vus["url"] == "https://generativelanguage.googleapis.com/v1beta/models"
    assert vus["cle"] == "cle-google"
    assert vus["autorisation"] is None


def test_decouverte_google_genai_complete_une_url_sans_version() -> None:
    """Une URL Gemini sans ``/v1beta`` ne doit pas produire un 404 évitable."""
    import asyncio

    vus: dict[str, object] = {}

    def _rendre(request: httpx.Request) -> httpx.Response:
        vus["url"] = str(request.url)
        return httpx.Response(200, json={"models": []})

    asyncio.run(
        decouvrir_modeles(
            protocole="google_genai",
            base_url="https://generativelanguage.googleapis.com",
            api_key=None,
            client=httpx.AsyncClient(transport=httpx.MockTransport(_rendre)),
        )
    )

    assert vus["url"] == "https://generativelanguage.googleapis.com/v1beta/models"
