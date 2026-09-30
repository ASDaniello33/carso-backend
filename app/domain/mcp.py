"""Registre des connecteurs MCP (Model Context Protocol) — par agent.

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

Le transport est celui de ``langchain-mcp-adapters`` : ``stdio`` (processus
local lancé par commande) ou ``streamable_http`` (URL distante).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Boolean, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.base import Base, TimestampMixin, UuidPkMixin

#: Transports supportés (vocabulaire de ``langchain-mcp-adapters``).
TRANSPORTS_MCP: frozenset[str] = frozenset({"stdio", "streamable_http", "sse"})


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
        actif: un connecteur inactif reste connu mais n'est jamais chargé.
        modifie_par: administrateur à l'origine du dernier changement.
    """

    __tablename__ = "serveurs_mcp"
    __table_args__ = (
        Index("uq_serveurs_mcp_nom", "nom", unique=True),
        Index("ix_serveurs_mcp_actif", "actif"),
    )

    nom: Mapped[str] = mapped_column(String(100), nullable=False)
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
