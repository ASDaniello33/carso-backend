"""Registre des connecteurs MCP (Model Context Protocol)—par agent.

Un serveur MCP expose des outils à distance. CARSO en a besoin pour brancher des
sources externes sans écrire un tool par intégration. Deux décisions de
conception :

1. **Par agent, jamais global.** Un connecteur déclare les agents qui peuvent
   l'utiliser (``agents``). Un agent ne reçoit donc jamais les outils d'un
   serveur qu'il n'a pas explicitement au contrat (moindre privilège, AGENTS.md
   §9) ; le harnais ajoute les outils distants **en plus** de l'allow-list
   déclarée, jamais à sa place.
2. **Rien en clair.** Les en-têtes d'authentification (jeton Bearer, clé d'API)
   sont chiffrés au repos (``en_tetes_chiffres``, ``app.core.chiffrement``) et
   l'API ne renvoie que le **nom** des en-têtes, jamais leur valeur.

Le transport est celui de ``langchain-mcp-adapters``—``stdio`` (processus
local lancé par commande) ou ``streamable_http`` (URL distante).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.base import Base, TimestampMixin, UuidPkMixin

#: Seul agent qui charge un connecteur **personnel** (décision 10/10/2026).
#:
#: Un connecteur déclaré par un compte apporte des outils distants, des pannes
#: et des jetons qui n'ont rien à faire dans un agent partagé de CARSO : ses
#: outils chargeraient le graphe de cet agent pour ce compte, ses pannes
#: interrompraient ses conversations. L'agent personnel est donc le **seul**
#: agent qui le charge — c'est aussi ce qui le rend « l'agent par défaut des
#: MCP personnels ». La règle est appliquée à l'écriture (service), à la lecture
#: (repository) et par migration pour les lignes existantes.
AGENT_CONNECTEURS_PERSONNELS = "agent_personnel"

#: Transports supportés (vocabulaire de ``langchain-mcp-adapters``).
TRANSPORTS_MCP: frozenset[str] = frozenset({"stdio", "streamable_http", "sse"})

#: Modes d'authentification d'un connecteur réseau :
#:
#: - ``aucune`` — serveur ouvert ;
#: - ``en_tetes`` — en-têtes statiques chiffrés (jeton pré-généré) ;
#: - ``oauth`` — autorisation déléguée (consentement navigateur, jetons
#:   rafraîchissables, client enregistré dynamiquement ou fourni).
MODES_AUTHENTIFICATION_MCP: frozenset[str] = frozenset({"aucune", "en_tetes", "oauth"})

#: États d'une autorisation OAuth :
#:
#: - ``non_requis`` — le connecteur n'est pas en mode OAuth ;
#: - ``jamais`` — OAuth choisi, aucune autorisation encore obtenue ;
#: - ``en_attente`` — consentement en cours dans le navigateur ;
#: - ``autorise`` — jetons présents (rafraîchis automatiquement) ;
#: - ``erreur`` — la dernière tentative a échoué (motif conservé à part).
STATUTS_OAUTH_MCP: frozenset[str] = frozenset(
    {"non_requis", "jamais", "en_attente", "autorise", "erreur"}
)


class ServeurMcp(Base, UuidPkMixin, TimestampMixin):
    """Un connecteur MCP activable par agent.

    Attributes:
        nom: identifiant lisible et unique (sert aussi de préfixe d'outils).
        transport: ``stdio``, ``streamable_http`` ou ``sse``.
        url: URL du serveur (transports réseau uniquement).
        commande: exécutable à lancer (``stdio`` uniquement).
        arguments: arguments de la commande (``stdio`` uniquement).
        agents: identifiants des agents autorisés à utiliser ce connecteur.
        en_tetes_chiffres: en-têtes HTTP chiffrés (jeton d'authentification).
        mode_authentification: ``aucune`` | ``en_tetes`` | ``oauth``.
        oauth_client_id: client **pré-enregistré** (client public) — utilisé
            quand le serveur refuse l'enregistrement dynamique ; jamais un
            secret (le client OAuth 2.1 des MCP est public).
        oauth_etat_chiffre: métadonnées de client et jetons OAuth chiffrés
            (même clé que les en-têtes). L'API n'en expose jamais le contenu.
        oauth_statut: état lisible de l'autorisation (``jamais``, ``en_attente``,
            ``autorise``, ``erreur``, ``non_requis``).
        oauth_derniere_erreur: dernier motif d'échec de l'autorisation.
        derniere_sonde: dernier résultat **réel** de connexion (JSON public :
            outils chargés, erreur, horodatage) — jamais un secret.
        actif: un connecteur inactif reste connu mais n'est jamais chargé.
        utilisateur_id: proprietaire de la configuration. **NULL = connecteur
            global**, administré par CARSO et lisible par tous ; renseigné, le
            connecteur est **personnel**, n'existe que dans le contexte de ce
            compte et ne se charge que dans ``AGENT_CONNECTEURS_PERSONNELS``.
        modifie_par: administrateur à l'origine du dernier changement.
    """

    __tablename__ = "serveurs_mcp"
    __table_args__ = (
        # Uniquite par proprietaire : deux comptes peuvent nommer leur
        # connecteur "github" sans se genner. Les globaux (utilisateur_id
        # NULL) sont proteges a part — en PostgreSQL, deux NULL ne sont pas
        # considers egaux dans un index unique, d'ou l'index partiel.
        Index("uq_serveurs_mcp_proprietaire_nom", "utilisateur_id", "nom", unique=True),
        Index(
            "uq_serveurs_mcp_nom_global",
            "nom",
            unique=True,
            postgresql_where=text("utilisateur_id IS NULL"),
            sqlite_where=text("utilisateur_id IS NULL"),
        ),
        Index("ix_serveurs_mcp_actif", "actif"),
    )

    nom: Mapped[str] = mapped_column(String(100), nullable=False)
    #: NULL = connecteur global (admin). Renseigné = connecteur personnel,
    #: invisible des autres comptes et sans effet sur le Runtime partage.
    utilisateur_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("utilisateurs.id", ondelete="CASCADE"), nullable=True, index=True
    )
    transport: Mapped[str] = mapped_column(
        String(30), nullable=False, default="streamable_http",
        server_default=text("'streamable_http'"),
    )
    url: Mapped[str | None] = mapped_column(String(1000))
    commande: Mapped[str | None] = mapped_column(String(500))
    # Pas de ``server_default`` JSONB : c'est un dialecte PostgreSQL (le schéma
    # doit aussi se créer sur SQLite pour les tests).
    arguments: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    agents: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    en_tetes_chiffres: Mapped[str | None] = mapped_column(Text)
    #: Mode d'authentification. Défaut ``aucune`` : une ligne existante sans
    #: en-tête reste ouverte, et la migration bascule en ``en_tetes`` celles qui
    #: en portent (le comportement d'avant est conservé, sans le deviner).
    mode_authentification: Mapped[str] = mapped_column(
        String(20), nullable=False, default="aucune", server_default=text("'aucune'")
    )
    #: Client pré-enregistré (public). Renseigné, il **remplace** l'enregistrement
    #: dynamique : c'est le seul recours face à un serveur qui n'accepte que des
    #: clients déjà connus (Meta — voir docs/validation/VERIFICATION-MCP-OAUTH.md).
    oauth_client_id: Mapped[str | None] = mapped_column(String(255))
    #: Métadonnées de client + jetons OAuth, chiffrés au repos.
    oauth_etat_chiffre: Mapped[str | None] = mapped_column(Text)
    oauth_statut: Mapped[str] = mapped_column(
        String(20), nullable=False, default="non_requis", server_default=text("'non_requis'")
    )
    oauth_derniere_erreur: Mapped[str | None] = mapped_column(Text)
    #: Dernière sonde : JSON lisible (outils, erreur, durée, horodatage).
    derniere_sonde: Mapped[str | None] = mapped_column(Text)
    actif: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    modifie_par: Mapped[str | None] = mapped_column(String(64))

    def resume(self, *, noms_en_tetes: list[str] | None = None) -> dict[str, Any]:
        """Vue sûre pour l'API : jamais la valeur d'un en-tête, seulement son nom."""
        return {
            "nom": self.nom,
            "transport": self.transport,
            "url": self.url,
            "commande": self.commande,
            "arguments": list(self.arguments or []),
            "agents": list(self.agents or []),
            "en_tetes": noms_en_tetes or [],
            "actif": bool(self.actif),
        }
