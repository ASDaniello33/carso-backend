"""Budgets : devise par défaut MGA (ariary) au lieu de MAD.

Le prototype portait `MAD` (dirham marocain) comme devise de travail. CARSO est
à Toliara : tout budget créé sans devise explicite naissait dans une monnaie qui
n'est pas la sienne.

Cette migration ne change **pas** le sens de montants existants :

1. elle aligne le défaut SQL de `budgets.devise` sur `MGA` (les créations
   suivantes, y compris hors ORM, reçoivent l'ariary) ;
2. elle corrige les budgets **encore vides** (`aucune ligne`) passés en `MAD` :
   un total nul ne porte aucune valeur monétaire à préserver ;
3. elle laisse **intact** tout budget `MAD` qui possède des lignes : changer la
   devise d'une somme déjà chiffrée réécrirait un montant métier. Ces lignes
   sont listées par la migration (elle échoue avec un diagnostic explicite) afin
   qu'un humain décide s'il faut les reprendre.

Elle est donc rejouable sans perte : seule une conversion volontaire, jamais une
conversion silencieuse.

Revision ID: 0034
Revises: 0033
Create Date: 2026-11-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0034"
down_revision: str | None = "0033"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_ANCIENNE_DEVISE = "MAD"
_NOUVELLE_DEVISE = "MGA"


def upgrade() -> None:
    op.alter_column(
        "budgets",
        "devise",
        server_default=_NOUVELLE_DEVISE,
        existing_type=sa.String(length=10),
        existing_nullable=False,
    )

    connexion = op.get_bind()

    # Budgets vides : rien de monétaire à conserver, la correction est sûre.
    connexion.execute(
        sa.text(
            "UPDATE budgets SET devise = :nouvelle "
            "WHERE devise = :ancienne "
            "AND NOT EXISTS (SELECT 1 FROM lignes_budget l WHERE l.budget_id = budgets.id)"
        ),
        {"nouvelle": _NOUVELLE_DEVISE, "ancienne": _ANCIENNE_DEVISE},
    )

    # Budgets chiffrés encore dans l'ancienne devise : refus explicite plutôt
    # qu'une conversion muette d'un montant validé.
    restants = connexion.execute(
        sa.text("SELECT count(*) FROM budgets WHERE devise = :ancienne"),
        {"ancienne": _ANCIENNE_DEVISE},
    ).scalar_one()
    if restants:
        raise RuntimeError(
            f"{restants} budget(s) chiffré(s) restent en {_ANCIENNE_DEVISE}. "
            "La devise d'un montant existant ne se convertit pas automatiquement : "
            "reprenez ces budgets (nouvelle version en "
            f"{_NOUVELLE_DEVISE}) puis relancez la migration."
        )


def downgrade() -> None:
    op.alter_column(
        "budgets",
        "devise",
        server_default=_ANCIENNE_DEVISE,
        existing_type=sa.String(length=10),
        existing_nullable=False,
    )
