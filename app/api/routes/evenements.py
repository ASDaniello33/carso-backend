"""Route — révision des données métier (rafraîchissement de l'interface).

L'agent crée et modifie des données (offre, lot, appel, mission, document) alors
que l'utilisateur regarde une liste dans son navigateur. Sans signal, cette liste
reste périmée jusqu'à un rechargement manuel.

Cette route expose une **empreinte** des données métier :

```text
GET /api/v1/evenements/revision -> {"revision": "9f2c…"}
```

L'empreinte change dès qu'une ligne d'une table métier est **créée, modifiée ou
supprimée** (nombre de lignes + dernière modification). Le frontend la compare
périodiquement et recharge les ressources réellement affichées.

Pourquoi une empreinte et pas un flux d'événements : aucun état à maintenir, pas
de reconnexion, plusieurs processus serveur donnent la même valeur, et la
détection ne dépend pas du fait que l'agent ait pensé — ou non — à prévenir.

Lecture seule : la route ne touche à aucune donnée.
"""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, literal, select, union_all
from sqlalchemy.orm import Session
from sqlalchemy.sql.schema import Table

from app.api.deps import DbSession, get_current_user
from app.domain.base import Base

router = APIRouter(
    prefix="/evenements",
    tags=["evenements"],
    dependencies=[Depends(get_current_user)],
)


class RevisionRead(BaseModel):
    """Empreinte des données métier et instant de la mesure."""

    revision: str
    mesure_at: datetime
    nb_tables: int


@router.get("/revision", response_model=RevisionRead)
def lire_revision(session: DbSession) -> RevisionRead:
    """Empreinte des tables métier horodatées (aucune donnée métier exposée).

    Returns:
        ``revision`` : empreinte stable tant que les données ne changent pas ;
        ``mesure_at`` : instant de la mesure (fuseau UTC) ;
        ``nb_tables`` : nombre de tables observées (diagnostic).
    """
    tables = _tables_suivies()
    mesure = _mesure(session, tables)
    empreinte = sha256(mesure.encode("utf-8")).hexdigest()[:16]
    return RevisionRead(
        revision=empreinte,
        mesure_at=datetime.now(UTC),
        nb_tables=len(tables),
    )


def _tables_suivies() -> list[Table]:
    """Tables du domaine CARSO horodatées (``created_at`` + ``updated_at``).

    Les tables du runtime agentique (points de contrôle LangGraph) ne sont pas
    dans le registre du domaine : leurs écritures techniques ne déclenchent donc
    jamais un rafraîchissement d'interface.
    """
    tables: list[Table] = []
    for mapper in Base.registry.mappers:
        table = mapper.local_table
        if not isinstance(table, Table):
            continue
        if "updated_at" not in table.c or "created_at" not in table.c:
            continue
        tables.append(table)
    return sorted(tables, key=lambda table: table.name)


def _mesure(session: Session, tables: list[Table]) -> str:
    """Chaîne de mesure : ``table:lignes:derniere_modification`` par table.

    Une seule requête (``UNION ALL``) pour toutes les tables : le pouls reste
    bon marché même si l'interface l'interroge régulièrement.
    """
    if not tables:
        return "aucune_table"
    requete = union_all(
        *[
            select(
                literal(table.name).label("table_name"),
                func.count().label("nb_lignes"),
                func.max(table.c.updated_at).label("derniere"),
            ).select_from(table)
            for table in tables
        ]
    )
    lignes = session.execute(requete.order_by("table_name")).all()
    morceaux = [
        f"{nom}:{nb}:{derniere.isoformat() if derniere else '-'}"
        for nom, nb, derniere in lignes
    ]
    return "|".join(morceaux)
