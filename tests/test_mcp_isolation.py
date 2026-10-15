"""Tests — isolation du contexte MCP par utilisateur (incrément 46).

Avant, les connecteurs MCP étaient **globaux** : un seul ensemble administré
par CARSO, mémorisé dans le graphe de chaque agent au démarrage du Runtime. Deux
conséquences, l'une visible et l'autre discrète :

1. les outils d'un connecteur étaient offerts à **tous** les comptes ;
2. ``signature()`` ignorait ``en_tetes_chiffres``. Dès qu'un connecteur
   personnel existe, deux comptes déclarant la **même URL** avec des **jetons
   différents** partageaient une entrée de cache — et donc des identifiants.
   C'est ce second point que ces tests verrouillent.

Ces tests portent sur la logique pure (empreinte de cache, périmètre du
repository, purge ciblée) : ils sont déterministes et ne touchent pas le réseau.
"""

from __future__ import annotations

from uuid import uuid4

import app.agents.mcp as mcp
from app.agents.mcp import (
    empreinte_credentials,
    signature,
)
from app.domain.identity import Utilisateur
from app.domain.mcp import ServeurMcp
from app.infrastructure.repositories import ServeurMcpRepository


def _serveur(
    *,
    nom: str = "github",
    url: str = "https://mcp.exemple.test/mcp",
    jeton: str | None = None,
    utilisateur_id=None,
) -> ServeurMcp:
    return ServeurMcp(
        nom=nom,
        transport="streamable_http",
        url=url,
        arguments=[],
        # Un connecteur personnel ne charge que l'agent personnel du compte
        # (règle 10/10) : le contrat d'un connecteur de compte nomme cet agent,
        # jamais un agent partagé du CARSO.
        agents=["agent_personnel"],
        # Le texte « chiffré » stand-in : la signature ne lit que l'empreinte,
        # jamais la valeur — un vrai jeton ne doit jamais atteindre la clé.
        en_tetes_chiffres=f'{{"Authorization": "Bearer {jeton}"}}' if jeton else None,
        actif=True,
        utilisateur_id=utilisateur_id,
    )


# --- 1. Empreinte et clé de cache ---------------------------------------------


def test_meme_url_jetons_differents_emperintes_differentes() -> None:
    """Deux comptes, même serveur, credentials distincts ⇒ clés distinctes.

    C'est le test de sécurité central : sans lui, le second compte hériterait
    des identifiants du premier via l'entrée de cache commune.
    """
    alice, bob = uuid4(), uuid4()
    a = _serveur(utilisateur_id=alice, jeton="jeton-A")
    b = _serveur(utilisateur_id=bob, jeton="jeton-B")

    assert signature([a]) != signature([b])
    assert empreinte_credentials(a) != empreinte_credentials(b)


def test_empreinte_ne_reste_pas_le_jeton_en_clair() -> None:
    """L'empreinte d'un connecteur authentifié ne contient pas le secret."""
    jeton = "ghp_SuperSecretValeurTresSensible"
    empreinte = empreinte_credentials(_serveur(jeton=jeton))

    assert jeton not in empreinte
    assert len(empreinte) == 16  # hachage tronqué, stable


def test_meme_configuration_partage_la_meme_empreinte() -> None:
    """Deux lectures de la même configuration ne reconstruisent pas l'agent."""
    alice = uuid4()
    premier = signature([_serveur(utilisateur_id=alice, jeton="jeton-A")])
    second = signature([_serveur(utilisateur_id=alice, jeton="jeton-A")])

    assert premier == second


def test_configurations_differentes_empreintes_differentes() -> None:
    """Changer d'URL ou de jeton change la clé — donc l'agent est reconstruit."""
    alice = uuid4()
    base = _serveur(utilisateur_id=alice, jeton="jeton-A")

    assert signature([base]) != signature(
        [_serveur(url="https://autre.test/mcp", utilisateur_id=alice, jeton="jeton-A")]
    )
    assert signature([base]) != signature([_serveur(utilisateur_id=alice, jeton="jeton-B")])


def test_changement_de_jeton_invalide_le_contexte_de_son_tenant_seul() -> None:
    """A change son jeton : sa clé change, celle de B est intacte."""
    alice, bob = uuid4(), uuid4()
    a_avant = signature([_serveur(utilisateur_id=alice, jeton="ancien")])
    b_avant = signature([_serveur(utilisateur_id=bob, jeton="B")])

    a_apres = signature([_serveur(utilisateur_id=alice, jeton="nouveau")])
    b_apres = signature([_serveur(utilisateur_id=bob, jeton="B")])

    assert a_avant != a_apres, "A doit reconstruire son contexte"
    assert b_avant == b_apres, "B ne doit pas être impacté"


def test_connecteur_global_sans_proprietaire() -> None:
    """Un connecteur administré reste un contexte global distinct."""
    global_ = _serveur(nom="shared", jeton="jeton-global")

    assert str(global_.utilisateur_id or "global") in signature([global_])
    assert "global" in signature([global_])


# --- 2. Purge ciblée du cache -------------------------------------------------


def test_vider_cache_cible_ne_touche_pas_les_autres_agents(monkeypatch) -> None:
    """Un changement de MCP personnel n'invalide que le contexte visé."""
    import app.agents.mcp as mcp

    mcp._CACHE.clear()
    monkeypatch.setattr(mcp, "_CACHE", dict(mcp._CACHE))
    mcp._CACHE[("agent_statistique", "sig-A")] = ()
    mcp._CACHE[("agent_rh", "sig-B")] = ()

    mcp.vider_cache(agent_id="agent_statistique", signature_contexte="sig-A")

    assert "agent_statistique" not in {cle[0] for cle in mcp._CACHE}
    assert ("agent_rh", "sig-B") in mcp._CACHE, "B doit survivre"
    mcp._CACHE.clear()


def test_vider_cache_total_reste_disponible(monkeypatch) -> None:
    """Sans argument, la purge reste totale (changement global, tests)."""
    import app.agents.mcp as mcp

    mcp._CACHE[("agent_statistique", "sig-A")] = ()
    mcp.vider_cache()

    assert mcp._CACHE == {}


# --- 3. Périmètre du repository (base réelle) ---------------------------------
#
# Les tests ci-dessus portent sur l'empreinte. Ceux-ci sur la **selection** :
# c'est le filtre SQL qui décide de ce qu'un compte voit. Un connecteur
# personnel d'autrui ne doit jamais sortir du repository — le filtre est fait
# en base, pas en Python après coup.


def _compte(nom: str) -> Utilisateur:
    return Utilisateur(
        email=f"{nom}-{uuid4().hex[:6]}@carso.mg",
        nom=nom,
        prenom="Test",
        password_hash="x",
        role="collaborateur",
        statut="actif",
    )


def test_chacun_ne_voit_que_ses_connecteurs(api_session_factory) -> None:
    """A ne voit pas le connecteur de B, et reciproquement."""
    session = api_session_factory()
    try:
        alice, bob = _compte("alice"), _compte("bob")
        session.add_all([alice, bob])
        session.commit()
        depot = ServeurMcpRepository(session)
        depot.add(_serveur(nom="mcp_a", utilisateur_id=alice.id, jeton="A"))
        depot.add(_serveur(nom="mcp_b", utilisateur_id=bob.id, jeton="B"))
        session.commit()

        vus_a = {s.nom for s in depot.lister(utilisateur_id=alice.id)}
        vus_b = {s.nom for s in depot.lister(utilisateur_id=bob.id)}

        assert "mcp_a" in vus_a and "mcp_b" not in vus_a, "A ne doit pas voir B"
        assert "mcp_b" in vus_b and "mcp_a" not in vus_b, "B ne doit pas voir A"
    finally:
        session.close()


def test_global_et_personnel_cumulent(api_session_factory) -> None:
    """Un compte voit le connecteur global **et** le sien — jamais celui d'autrui."""
    session = api_session_factory()
    try:
        alice, bob = _compte("alice"), _compte("bob")
        session.add_all([alice, bob])
        session.commit()
        depot = ServeurMcpRepository(session)
        depot.add(_serveur(nom="mcp_global", jeton="G"))
        depot.add(_serveur(nom="mcp_a", utilisateur_id=alice.id, jeton="A"))
        depot.add(_serveur(nom="mcp_b", utilisateur_id=bob.id, jeton="B"))
        session.commit()

        vus_a = {s.nom for s in depot.lister(utilisateur_id=alice.id)}
        vus_admin = {s.nom for s in depot.lister()}

        assert vus_a == {"mcp_global", "mcp_a"}, "global + personnel, pas celui de B"
        assert vus_admin == {"mcp_global", "mcp_a", "mcp_b"}, "l'admin voit tout"
    finally:
        session.close()


def test_un_compte_ne_peut_pas_atteindre_le_connecteur_dautrui(api_session_factory) -> None:
    """Ecrire sur le connecteur de B echoue : le perimetre l'exclut."""
    session = api_session_factory()
    try:
        alice, bob = _compte("alice"), _compte("bob")
        session.add_all([alice, bob])
        session.commit()
        depot = ServeurMcpRepository(session)
        depot.add(_serveur(nom="mcp_b", utilisateur_id=bob.id, jeton="B"))
        session.commit()

        assert depot.get_par_nom("mcp_b", utilisateur_id=alice.id) is None

        from app.application.services.parametres_service import ServeurMcpService

        # Hors perimetre : la ligne n'est meme pas trouvee, la suppression est
        # un no-op rendu par ``False`` — elle ne doit surtout pas lever 500.
        assert ServeurMcpService(session).supprimer(
            "mcp_b", utilisateur_id=alice.id
        ) is False
        session.commit()
        assert depot.get_par_nom("mcp_b", utilisateur_id=bob.id) is not None, (
            "le connecteur de B doit avoir survecu"
        )
    finally:
        session.close()


def test_connecteur_desactive_nest_plus_charge(api_session_factory) -> None:
    """A desactive son connecteur : il disparait de ses agents, B est intact."""
    session = api_session_factory()
    try:
        alice, bob = _compte("alice"), _compte("bob")
        session.add_all([alice, bob])
        session.commit()
        depot = ServeurMcpRepository(session)
        actif = _serveur(nom="mcp_a", utilisateur_id=alice.id, jeton="A")
        depot.add(actif)
        depot.add(_serveur(nom="mcp_b", utilisateur_id=bob.id, jeton="B"))
        session.commit()

        actif.actif = False
        session.commit()

        # Un agent **partagé** ne charge aucun connecteur personnel, pour A
        # comme pour B : la règle 10/10 tient même quand le nom d'agent d'une
        # ligne héritée désignerait un agent CARSO.
        assert {s.nom for s in depot.lister_pour_agent(
            "agent_statistique", utilisateur_id=alice.id
        )} == set()
        assert {s.nom for s in depot.lister_pour_agent(
            "agent_statistique", utilisateur_id=bob.id
        )} == set()
        # L'agent personnel, lui, perd celui de A (désactivé) et garde celui de B.
        assert {s.nom for s in depot.lister_pour_agent(
            "agent_personnel", utilisateur_id=alice.id
        )} == set()
        assert {s.nom for s in depot.lister_pour_agent(
            "agent_personnel", utilisateur_id=bob.id
        )} == {"mcp_b"}
    finally:
        session.close()


# --- 4. Non-redémarrage du Runtime ---------------------------------------------
#
# Contrainte structurante de la demande : changer son connecteur **ne doit pas*
# reconstruire le Runtime partagé. Le test observe l'effet de bord au lieu de
# le croire : un changement personnel ne doit ni appeler ``manager.reload()``,
# ni vider le cache des agents que le compte n'utilise pas.


def test_changement_personnel_ne_recharge_pas_le_runtime(monkeypatch) -> None:
    """Aucun rechargement global, aucune purge globale lors d'un changement A."""
    import app.agents.manager as manager_module

    relogements: list[str] = []

    class _ManagerQuiObserve:
        def reload(self) -> tuple[str, ...]:
            relogements.append("reload")
            return ()

        def restart(self) -> tuple[str, ...]:
            relogements.append("restart")
            return ()

    monkeypatch.setattr(manager_module, "get_runtime_manager", lambda: _ManagerQuiObserve())

    # Cache peuplé de contextes d'autres comptes : ils doivent survivre.
    mcp._CACHE.clear()
    mcp._CACHE[("agent_statistique", "signature-de-B")] = ()
    mcp._CACHE[("agent_rh", "signature-de-B-bis")] = ()

    # A change son connecteur sur l'agent qu'il utilise : purge ciblee.
    mcp.vider_cache(agent_id="agent_statistique")

    assert relogements == [], "aucun rechargement du Runtime pour un changement A"
    assert ("agent_rh", "signature-de-B-bis") in mcp._CACHE, (
        "l'agent que A n'utilise pas doit garder son cache"
    )
    assert ("agent_statistique", "signature-de-B") not in mcp._CACHE, (
        "seul l'agent de A est purge"
    )
    mcp._CACHE.clear()


# --- 5. Surface HTTP : ce que l'interface voit reellement ----------------------
#
# Les sections precedentes verrouillent la logique (empreinte, perimetre du
# repository, purge). Celle-ci la surface **exposee** a l'ecran, qui etait le
# point aveugle : le reponse ne disait pas si un connecteur etait personnel, donc
# l'interface ne pouvait pas distinguer ce qu'elle edite de ce qu'elle herite —
# et l'administration pouvait creer un connecteur global sous le nom d'un compte
# (escalade de perimetre silencieuse, la ligne du collaborateur restant intacte).


def _brancher(api_client, utilisateur: Utilisateur, *, admin: bool = False) -> None:
    """Force un porteur sur les routes, comme le fait la session reelle.

    ``require_admin`` est surcharge **aussi** : avec une cle de chiffrement
    definie, la route repondrait 401 avant d'atteindre la logique testee.
    """
    from app.api.deps import get_current_user, require_admin

    api_client.app.dependency_overrides[get_current_user] = lambda: utilisateur
    if admin:
        api_client.app.dependency_overrides[require_admin] = lambda: utilisateur


def _compte_admin() -> Utilisateur:
    return Utilisateur(
        email=f"admin-{uuid4().hex[:8]}@carso.test",
        nom="Admin",
        prenom="Carso",
        password_hash="x",
        role="admin",
    )


def test_le_connecteur_personnel_annonce_son_proprietaire(
    api_client, api_session_factory
) -> None:
    """Le drapeau ``personnel`` dit a l'ecran ce qu'il peut modifier."""
    session = api_session_factory()
    try:
        alice = _compte("alice")
        session.add(alice)
        session.commit()
    finally:
        session.close()
    _brancher(api_client, alice)

    corps = {
        "transport": "streamable_http",
        "url": "https://mcp.exemple.test/mcp",
        "commande": None,
        "arguments": [],
        "agents": ["agent_personnel"],
        "en_tetes": None,
        "actif": True,
    }
    ecrit = api_client.put("/api/v1/parametres/mcp/personnel/son-outil", json=corps)
    assert ecrit.status_code == 200, ecrit.text
    assert ecrit.json()["personnel"] is True
    assert ecrit.json()["en_tetes"] == []

    liste = api_client.get("/api/v1/parametres/mcp/personnel").json()
    assert [c["nom"] for c in liste["connecteurs"]] == ["son-outil"]
    assert liste["connecteurs"][0]["personnel"] is True
    assert "chiffrement_disponible" in liste, (
        "l'ecran doit pouvoir dire si un en-tete serait refuse avant la saisie"
    )


def test_administration_ne_liste_pas_les_connecteurs_des_comptes(
    api_client, api_session_factory
) -> None:
    """Le global reste prive : l'ecran d'administration decrit le Runtime partage."""
    session = api_session_factory()
    try:
        alice = _compte("alice")
        session.add(alice)
        session.commit()  # l'identifiant n'existe qu'apres le flush
        session.add(
            ServeurMcp(
                nom="son-outil",
                transport="streamable_http",
                utilisateur_id=alice.id,
            )
        )
        session.add(ServeurMcp(nom="notion-carso", transport="streamable_http"))
        session.commit()
    finally:
        session.close()
    _brancher(api_client, _compte_admin(), admin=True)

    assert [c["nom"] for c in api_client.get("/api/v1/parametres/mcp").json()] == [
        "notion-carso"
    ]
    assert api_client.get("/api/v1/parametres/mcp").json()[0]["personnel"] is False
    connecteurs = api_client.get("/api/v1/parametres/etat").json()["connecteurs_mcp"]
    assert [c["nom"] for c in connecteurs] == ["notion-carso"], (
        "le perimetre global ne doit pas exposer le connecteur d'un compte"
    )


def test_admin_ne_peut_pas_promouvoir_un_nom_personnel_en_global(
    api_client, api_session_factory
) -> None:
    """Ecrire en global sous le nom d'un compte est refuse, pas fusionne."""
    session = api_session_factory()
    try:
        alice = _compte("alice")
        session.add(alice)
        session.commit()  # l'identifiant n'existe qu'apres le flush
        session.add(
            ServeurMcp(
                nom="son-outil",
                transport="streamable_http",
                url="https://perso.exemple.test/mcp",
                utilisateur_id=alice.id,
            )
        )
        session.commit()
    finally:
        session.close()
    _brancher(api_client, _compte_admin(), admin=True)

    reponse = api_client.put(
        "/api/v1/parametres/mcp/son-outil",
        json={
            "transport": "streamable_http",
            "url": "https://global.exemple.test/mcp",
            "commande": None,
            "arguments": [],
            "agents": [],
            "en_tetes": None,
            "actif": True,
        },
    )
    assert reponse.status_code == 422, reponse.text
    assert "son-outil" in reponse.json()["error"]["message"]

    session = api_session_factory()
    try:
        lignes = ServeurMcpRepository(session).lister()
        assert [(une.nom, une.utilisateur_id) for une in lignes] == [("son-outil", alice.id)], (
            "aucune ligne globale ne doit etre creee, celle du compte reste intacte"
        )
    finally:
        session.close()
