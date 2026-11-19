"""Réactions : unicité par (utilisateur, cible) — incrément 24, jamais migrée.

Revision ID: 0036
Revises: 0035
Create Date: 2026-11-18

Le modèle ``Reaction`` (``app/domain/social.py``) et le service
(``SocialService.basculer_reaction``) appliquent depuis l'incrément 24 la règle
« **une** réaction par utilisateur et par cible » : même emoji => retrait,
autre emoji => remplacement. La migration 0012, elle, avait laissé la contrainte
``uq_reactions_utilisateur_emoji_cible`` (unicité **par emoji**).

Conséquence constatée le 18/11 par ``alembic check`` : la base — et donc toute
base neuve construite depuis les migrations — n'appliquait qu'une unicité par
emoji. Deux requêtes concurrentes (cœur puis like, cas exact du bug d'origine)
pouvaient donc insérer deux lignes : la logique du service protège le cas
séquentiel, pas la course.

Garde explicite : si des doublons subsistent (une base qui aurait déjà subi la
course), la migration **échoue** avec le compte et la commande de diagnostic
plutôt que de supprimer des lignes en silence (AGENTS.md §6).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0036"
down_revision: str | None = "0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SQL_DOUBLONS = """
    select count(*) from (
        select 1
        from reactions
        group by utilisateur_id, cible_type, cible_id
        having count(*) > 1
    ) as groupes
"""


def upgrade() -> None:
    # En mode hors ligne (``alembic upgrade --sql``), ``get_bind()`` rend ``None`` :
    # on ne peut pas compter les doublons puisqu'il n'y a pas de connexion. La
    # garde s'applique donc uniquement à l'exécution réelle — c'est là qu'elle
    # protège des données.
    if not op.get_context().as_sql:
        doublons = op.get_bind().execute(sa.text(_SQL_DOUBLONS)).scalar() or 0
    else:
        doublons = 0
    if doublons:
        msg = (
            f"Migration 0036 impossible : {doublons} groupe(s) de réactions en "
            "double (même utilisateur, même cible). L'unicité par cible ne peut "
            "pas être posée sans perdre une réaction. Diagnostic :\n"
            "  select utilisateur_id, cible_type, cible_id, count(*)\n"
            "  from reactions group by 1,2,3 having count(*) > 1;\n"
            "Conserver la réaction la plus récente de chaque groupe, puis "
            "relancer `alembic upgrade head`."
        )
        raise RuntimeError(msg)

    op.drop_constraint("uq_reactions_utilisateur_emoji_cible", "reactions", type_="unique")
    op.create_unique_constraint(
        "uq_reactions_utilisateur_cible",
        "reactions",
        ["utilisateur_id", "cible_type", "cible_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_reactions_utilisateur_cible", "reactions", type_="unique")
    op.create_unique_constraint(
        "uq_reactions_utilisateur_emoji_cible",
        "reactions",
        ["utilisateur_id", "emoji", "cible_type", "cible_id"],
    )
