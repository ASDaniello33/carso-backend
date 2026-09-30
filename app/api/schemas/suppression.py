"""Schémas API — suppression cascadée (impact + exécution).

Deux schémas miroir du service : ``ImpactSuppressionRead`` (ce qui disparaîtrait)
et ``RapportSuppressionRead`` (ce qui a disparu). ``decided_by`` ne vient jamais
du corps de la requête : il est lu du JWT, comme les autres décisions humaines.
"""

from uuid import UUID

from pydantic import BaseModel, Field


class SuppressionRequete(BaseModel):
    """Motif d'une suppression — facultatif à l'API, exigé par la conduite CARSO.

    Le motif part dans l'``AuditEvent`` (``event_metadata.reason``) : c'est la
    seule trace conservée de la destruction.
    """

    reason: str | None = Field(default=None, max_length=2000)


class ImpactSuppressionRead(BaseModel):
    """Dépendances d'une suppression : dénombrement, blocages, fichiers.

    ``dependances`` = ce qui sera **détruit** ; ``conservees`` = ce qui **survit**
    (les fiches documentaires, dont le fichier est retiré et le statut passé à
    « Supprimé »). L'interface annonce les deux séparément.
    """

    type_entite: str
    libelle: str
    identifiant: UUID
    reference: str | None = None
    dependances: dict[str, int] = Field(default_factory=dict)
    conservees: dict[str, int] = Field(default_factory=dict)
    documents_officiels: list[str] = Field(default_factory=list)
    fichiers: int = 0
    bloquant: bool = False
    message: str | None = None


class RapportSuppressionRead(BaseModel):
    """Inventaire de ce qui a été supprimé (comptages, fichiers retirés)."""

    type_entite: str
    libelle: str
    identifiant: UUID
    reference: str | None = None
    dependances: dict[str, int] = Field(default_factory=dict)
    conservees: dict[str, int] = Field(default_factory=dict)
    fichiers_supprimes: int = 0


__all__ = [
    "ImpactSuppressionRead",
    "RapportSuppressionRead",
    "SuppressionRequete",
]
