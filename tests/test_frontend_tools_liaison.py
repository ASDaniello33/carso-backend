"""Liaison des outils frontend au modèle — hors ligne, aucun réseau.

L'inspecteur CopilotKit voit les hooks client. Deep Agents, lui, ne lie que
les tools du contrat. Ces tests figent :

- l'allowlist (un nom hors catalogue n'est jamais stubé) ;
- le stub inoffensif (aucune mutation) ;
- le bloc prompt (liste concrète, agent inconnu = bloc vide) ;
- la fusion AG-UI (schémas filtrés, hors contrat métier).
"""

from __future__ import annotations

from types import SimpleNamespace

from app.agents.catalogue import AGENT_DEFINITIONS
from app.agents.frontend_tools_catalogue import (
    AGENT_GENERATEUR,
    OUTILS_FRONTEND,
    PayloadQuestionnaire,
    bloc_outils_frontend,
    extraire_outils_etat,
    filtrer_outils_agui,
    noms_pour_agent,
    outils_pour_agent,
    politique_interrupt_frontend,
)
from app.agents.frontend_tools_middleware import (
    SENTINELLE_FRONTEND,
    fusionner_outils_modele,
    stub_frontend,
    stub_frontend_langchain,
    stubs_pour_agent,
)
from app.agents.generateur_offre import DEFINITION as GENERATEUR
from app.agents.harness import allowed_tools_only, build_system_prompt
from app.core.errors import ValidationError
from app.tools.base import TypedTool, to_langchain_tool


def test_catalogue_ne_couvre_que_les_agents_ui() -> None:
    """Aucun outil d'affichage n'est déclarable pour un agent hors catalogue."""
    ids = {agent for outil in OUTILS_FRONTEND for agent in outil.agents}
    assert ids == {definition.agent_id for definition in AGENT_DEFINITIONS}
    assert noms_pour_agent("agent_inconnu") == frozenset()
    assert bloc_outils_frontend("agent_inconnu") == ""


def test_generateur_a_ses_outils_daffichage() -> None:
    noms = noms_pour_agent(AGENT_GENERATEUR)
    assert "naviguer_vers" in noms
    assert "afficher_lots_appel" in noms
    assert "proposer_creation_mission" in noms
    assert "proposer_telechargement_document" in noms
    assert "poser_questionnaire" in noms
    assert "afficher_proposition_affectation" not in noms
    assert "afficher_tableau_import_beneficiaires" not in noms


def test_filtrer_refuse_un_nom_hors_catalogue() -> None:
    payload = [
        {"name": "naviguer_vers", "description": "ok"},
        {"name": "supprimer_tout", "description": "hors allowlist"},
        {"name": "afficher_lots_appel"},
        {"pas_un_outil": True},
        "ignore",
    ]
    retenus = filtrer_outils_agui(payload, AGENT_GENERATEUR)
    assert [item["name"] for item in retenus] == ["naviguer_vers", "afficher_lots_appel"]


def test_filtrer_refuse_un_outil_dun_autre_agent() -> None:
    payload = [{"name": "afficher_proposition_affectation"}]
    assert filtrer_outils_agui(payload, AGENT_GENERATEUR) == []


def test_stub_nexecute_aucun_service() -> None:
    tool = stub_frontend("naviguer_vers", "ouvre une page")
    assert isinstance(tool, TypedTool)
    assert tool.name == "naviguer_vers"
    resultat = tool.invoke({"url": "/missions/abc", "note": "x"})
    assert resultat["ok"] is True
    assert resultat["rendu"] == "client"
    assert resultat["url"] == "/missions/abc"


def test_stub_langchain_emet_la_sentinelle_protocole() -> None:
    """Régression (incrément 37, dashboard Gen-UI invisible) : le core
    CopilotKit n'exécute le handler d'un hook client que si le message tool
    porte exactement ``"Forwarded to client"`` (``isForwardedToClientPlaceholder``).
    Le stub renvoyait ``{ok: true, rendu: "client", ...}`` : le core voyait un
    résultat définitif, sautait le handler — la carte ``generer_dashboard``
    s'affichait dans le chat mais le dashboard n'arrivait jamais dans la page.
    """
    assert SENTINELLE_FRONTEND == "Forwarded to client"

    # TypedTool.invoke laisse passer la chaîne brute (aucun emballage JSON).
    tool = stub_frontend_langchain("naviguer_vers", "ouvre une page")
    assert tool.invoke({"url": "/rapports"}) == SENTINELLE_FRONTEND

    # Projection LangChain : la sentinelle traverse StructuredTool intacte.
    langchain_tool = to_langchain_tool(tool)
    assert langchain_tool.invoke({"url": "/rapports"}) == SENTINELLE_FRONTEND


def test_stubs_pour_agent_portent_tous_la_sentinelle() -> None:
    """Tout stub passé au modèle émet la sentinelle, pas un résultat métier."""
    from app.agents.frontend_tools_catalogue import OUTILS_FRONTEND

    vus = 0
    for outil in OUTILS_FRONTEND:
        projection = stub_frontend_langchain(outil.name, outil.description)
        # Le handler (post-validation schéma) renvoie la sentinelle brute,
        # quel que soit le schéma de l'outil.
        assert projection.handler({}) == SENTINELLE_FRONTEND
        vus += 1
    assert vus == len(OUTILS_FRONTEND)


def test_stub_direct_reste_un_relais_metier() -> None:
    """``stub_frontend`` (chemin TypedTool direct) relâit toujours le payload :
    la sentinelle ne concerne que la projection LangChain passée au modèle.
    """
    tool = stub_frontend("naviguer_vers", "ouvre une page")
    resultat = tool.invoke({"url": "/rapports"})
    assert resultat["ok"] is True
    assert resultat["rendu"] == "client"
    assert resultat["url"] == "/rapports"


def test_stub_tableau_relaye_les_lignes() -> None:
    """Le chat repeint le tableau depuis ``result`` si les args AG-UI sont vides."""
    from app.agents.frontend_tools_catalogue import PayloadTableau

    tool = stub_frontend("afficher_tableau_donnees", "tableau")
    assert tool.input_schema is PayloadTableau
    resultat = tool.invoke(
        {
            "titre": "Missions",
            "colonnes": [{"cle": "titre", "titre": "Titre"}],
            "lignes": [{"id": "m1", "valeurs": {"titre": "Alpha"}}],
        }
    )
    assert resultat["ok"] is True
    assert resultat["rendu"] == "client"
    assert resultat["titre"] == "Missions"
    assert resultat["colonnes"][0]["cle"] == "titre"
    assert resultat["lignes"][0]["valeurs"]["titre"] == "Alpha"
    assert "ok" in resultat


def test_synthese_refuse_une_information_sans_libelle() -> None:
    """Le libellé est obligatoire — constat 07/10 : la fiche s'affichait sans intitulé.

    Le schéma du stub est celui du hook client : un appel du modèle qui ne
    remplit que la valeur est refusé avec un message qui nomme le champ manquant,
    au lieu de peindre une ligne muette.
    """
    import pytest
    from pydantic import ValidationError

    from app.agents.frontend_tools_catalogue import PayloadSynthese

    complet = PayloadSynthese(
        titre="Appels ouverts",
        champs=[
            {"libelle": "Nombre d'appels", "valeur": 5},
            {"libelle": "Date limite", "valeur": "2026-12-31", "precision": "le plus proche"},
        ],
    )
    assert [champ.libelle for champ in complet.champs] == ["Nombre d'appels", "Date limite"]
    assert complet.champs[0].valeur == 5, "une valeur numérique reste un nombre"

    for invalide in (
        {"valeur": "5"},
        {"libelle": "", "valeur": "5"},
        {"libelle": "   ", "valeur": "5"},
    ):
        with pytest.raises(ValidationError) as erreur:
            PayloadSynthese(titre="Sans libellé", champs=[invalide])
        assert "libelle" in str(erreur.value)


def test_resultats_web_relayent_le_fichier_image_et_le_media() -> None:
    """``image`` et ``media`` traversent le stub : c'est ce qui rend le média visible."""
    from app.agents.frontend_tools_catalogue import PayloadResultatsWeb

    tool = stub_frontend("afficher_resultats_web", "résultats")
    assert tool.input_schema is PayloadResultatsWeb
    resultat = tool.invoke(
        {
            "titre": "Images trouvées",
            "nature": "image",
            "resultats": [
                {
                    "titre": "Rizières",
                    "url": "https://exemple.org/article",
                    "image": "https://exemple.org/photo.jpg",
                    "vignette": "https://exemple.org/thumb.jpg",
                },
                {
                    "titre": "Clip",
                    "url": "https://exemple.org/regarder",
                    "media": "https://exemple.org/clip.mp4",
                },
            ],
        }
    )
    assert resultat["ok"] is True
    assert resultat["resultats"][0]["image"] == "https://exemple.org/photo.jpg"
    assert resultat["resultats"][0]["vignette"] == "https://exemple.org/thumb.jpg"
    assert resultat["resultats"][1]["media"] == "https://exemple.org/clip.mp4"


def test_stubs_generateur_sont_hors_contrat_metier() -> None:
    stubs = stubs_pour_agent(AGENT_GENERATEUR)
    noms = {getattr(s, "name", None) for s in stubs}
    assert "naviguer_vers" in noms
    assert "afficher_lots_appel" in noms
    # HITL : stubé pour que le modèle le voie ; interrupt_on coupe l'exécution.
    assert "poser_questionnaire" in noms
    # allowed_tools_only refuse tout nom hors contrat : les stubs n'y entrent pas.
    try:
        allowed_tools_only(
            GENERATEUR,
            (stub_frontend("naviguer_vers", "x"),),
        )
        raise AssertionError("le stub ne doit pas passer l'allow-list métier")
    except ValidationError as exc:
        assert "naviguer_vers" in str(exc)


def test_interrupt_frontend_coupe_poser_questionnaire() -> None:
    politique = politique_interrupt_frontend(AGENT_GENERATEUR)
    assert "poser_questionnaire" in politique
    assert politique["poser_questionnaire"]["allowed_decisions"] == ["respond"]
    assert politique_interrupt_frontend("agent_inconnu") == {}


def test_stub_questionnaire_porte_le_schema_reel() -> None:
    tool = stub_frontend("poser_questionnaire", "questionne")
    assert tool.input_schema is PayloadQuestionnaire
    resultat = tool.invoke({"questions": [{"id": "q1", "question": "Quoi ?"}]})
    assert resultat["ok"] is True
    assert resultat["rendu"] == "client"


def test_schemas_frontend_couvre_tout_le_catalogue() -> None:
    """Régression (incrément 16, formulaire mission invisible) : un outil du
    catalogue SANS schéma dédié tombait sur le schéma générique (champ
    ``note`` seul) — le modèle appelait l'outil sans ``lot_id``/``titre`` et
    la carte client se rendait vide. Tout outil frontend doit avoir son
    schéma réel dans ``SCHEMAS_FRONTEND``."""
    from app.agents.frontend_tools_catalogue import SCHEMAS_FRONTEND
    from app.agents.frontend_tools_middleware import PayloadFrontend as PayloadGenérique

    for outil in OUTILS_FRONTEND:
        schema = SCHEMAS_FRONTEND.get(outil.name)
        assert schema is not None, f"{outil.name} sans schéma frontend"
        assert schema is not PayloadGenérique, (
            f"{outil.name} tombe sur le schéma générique"
        )


def test_stub_mission_porte_le_schema_et_relaye_les_champs() -> None:
    """Le stub mission valide les vrais champs et les relais au client.

    Sans ``lot_id``, la carte client se rend en ``null`` : la validation
    Pydantic doit l'exiger pour que le modèle l'envoie systématiquement.
    """
    from app.agents.frontend_tools_catalogue import PayloadPropositionMission

    tool = stub_frontend("proposer_creation_mission", "formulaire mission")
    assert tool.input_schema is PayloadPropositionMission
    arguments = {
        "appel_id": "a1",
        "lot_id": "lot-9",
        "lot_reference": "Lot 2 — Formation",
        "titre": "Mission radar",
        "reference": "M-2026-01",
        "offres": [
            {"offre_id": "o1", "type": "offre_technique", "reference": "OT-1"}
        ],
    }
    resultat = tool.invoke(arguments)
    assert resultat["ok"] is True
    assert resultat["rendu"] == "client"
    assert resultat["lot_id"] == "lot-9"
    assert resultat["titre"] == "Mission radar"
    assert resultat["offres"][0]["offre_id"] == "o1"
    # lot_id est obligatoire : une invocation sans champ structurant renvoie
    # une erreur outillée (le modèle doit le renvoyer, pas le déduire).
    sans_lot = tool.invoke({"titre": "sans lot"})
    assert sans_lot["ok"] is False
    assert "lot_id" in (sans_lot.get("error", {}).get("message", "") or str(sans_lot))


def test_prompt_liste_les_outils_daffichage_du_generateur() -> None:
    systeme = build_system_prompt(GENERATEUR)
    assert "Outils d'affichage" in systeme
    assert "`afficher_lots_appel`" in systeme
    assert "`naviguer_vers`" in systeme
    assert "`poser_questionnaire`" in systeme
    assert "tools fournis (métier + affichage" in systeme
    assert "afficher_proposition_affectation" not in systeme


def test_prompt_agent_hors_catalogue_sans_bloc_affichage() -> None:
    """Un agent sans outil d'affichage n'a pas de bloc dans son prompt."""
    from app.agents.base import AgentDefinition
    from app.tools.permissions import PermissionPolicy

    sans_outils = AgentDefinition(
        agent_id="agent_sans_affichage",
        display_name="Agent sans affichage",
        description="test",
        policy=PermissionPolicy(allowed_capabilities=frozenset({"c"})),
        tools=(),
    )
    assert "Outils d'affichage" not in build_system_prompt(sans_outils)


def test_extraire_outils_depuis_etat_agui() -> None:
    assert extraire_outils_etat({"tools": [{"name": "naviguer_vers"}]}) == [
        {"name": "naviguer_vers"}
    ]
    assert extraire_outils_etat({"ag-ui": {"tools": [{"name": "x"}]}}) == [{"name": "x"}]
    assert extraire_outils_etat({}) == []
    assert extraire_outils_etat(None) == []


def test_fusion_ajoute_uniquement_les_schemas_allowlistes() -> None:
    request_tools = [SimpleNamespace(name="search_missions")]
    state = {
        "tools": [
            {"name": "naviguer_vers", "description": "ouvre"},
            {"name": "supprimer_tout"},
            {"name": "search_missions"},
        ]
    }
    fusion = fusionner_outils_modele(request_tools, state, AGENT_GENERATEUR)
    noms = []
    for item in fusion:
        nom = item.get("name") if isinstance(item, dict) else getattr(item, "name", None)
        noms.append(nom)
    assert noms[0] == "search_missions"
    assert "naviguer_vers" in noms
    assert "supprimer_tout" not in noms


def test_catalogue_aligne_sur_les_hooks_client() -> None:
    """Chaque outil du catalogue a un hook ; aucun nom inventé."""
    attendus = {
        "naviguer_vers",
        "afficher_tableau_donnees",
        "page_actuelle",
        "proposer_telechargement_document",
        "afficher_synthese_lecture",
        "afficher_resultats_web",
        "afficher_lots_appel",
        "proposer_creation_mission",
        "afficher_proposition_affectation",
        "afficher_liste_beneficiaires",
        "afficher_tableau_import_beneficiaires",
        "afficher_indicateurs",
        "generer_dashboard",
        "poser_questionnaire",
    }
    assert {outil.name for outil in OUTILS_FRONTEND} == attendus
    for outil in outils_pour_agent("agent_rh"):
        assert outil.name in attendus


def test_la_colonne_declare_sa_forme_sans_l_imposer() -> None:
    """``mono``/``num`` sont des indications de mise en forme, pas des regles.

    Le modele les renseigne quand c'est vrai et s'en abstient sinon : les deux
    doivent rester facultatifs, sinon un tableau ordinaire serait refuse parce
    qu'aucune colonne n'a ete declaree « numerique ».
    """
    import pytest
    from pydantic import ValidationError

    from app.agents.frontend_tools_catalogue import ColonneTableau

    reference = ColonneTableau(cle="ref", titre="Reference", mono=True)
    assert reference.mono is True
    assert reference.num is None

    titre = ColonneTableau(cle="intitule", titre="Intitule")
    assert titre.mono is None
    assert titre.num is None

    quantite = ColonneTableau(cle="montant", titre="Montant", num=True)
    assert quantite.num is True

    # Un modele qui renvoie une chaine est **refuse**, pas converti : une
    # indication de mise en forme acceptee au hasard deformait une colonne en
    # chasse fixe sans que personne l'ait demandee.
    with pytest.raises(ValidationError):
        ColonneTableau(cle="x", mono="oui")  # type: ignore[arg-type]


def test_la_ressource_du_tableau_ouvre_les_actions_de_ligne() -> None:
    """``ressource`` est l'interrupteur des gestes de ligne — et il est borne.

    Sans lui, l'interface affiche un tableau en lecture seule : le modele ne
    gagne donc pas une capacite d'ecriture en habillant un tableau quelconque.
    Une ressource hors vocabulaire est refusee (jamais un geste sur une entite
    que le service de suppression ne connait pas).
    """
    import pytest
    from pydantic import ValidationError

    from app.agents.frontend_tools_catalogue import PayloadTableau

    sans = PayloadTableau(titre="Offres", colonnes=[], lignes=[])
    assert sans.ressource is None

    avec = PayloadTableau(
        titre="Offres de formation",
        ressource="offres",
        colonnes=[{"cle": "reference", "titre": "Reference"}],
        lignes=[{"id": "6f0b1a2c-0000-4000-8000-000000000001", "valeurs": {}}],
    )
    assert avec.ressource == "offres"

    for ressource in ("missions", "sessions", "documents", "appels"):
        assert PayloadTableau(titre="t", ressource=ressource).ressource == ressource

    with pytest.raises(ValidationError):
        PayloadTableau(titre="t", ressource="inconnue")  # type: ignore[arg-type]