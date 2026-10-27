"""Statuts des CV et des documents d'appel (règle CARSO 26/10).

Un CV et un document d'appel ne portent pas de **cycle de décision** : ils
naissent ``importe`` au dépôt humain, puis deviennent ``archived`` ou
``supprime`` selon leur cycle de vie — jamais ``draft``.

Cette migration reprend les fiches existantes : les ``draft`` de type ``cv``
ou rattachés à un appel deviennent ``importe``. Elle ne touche **pas** les
fiches ``proposed``/``approved`` : un dépôt d'agent reste une proposition en
attente de décision humaine (règle 6 [C]), et une décision prononcée reste
tracée — jamais effacée silencieusement.

Piège rencontré sur la base de dev : ``doc_metadata`` peut contenir le
littéral jsonb ``null`` (pas un ``NULL`` SQL) — ``COALESCE`` le laisse passer
et l'opérateur ``||`` fabrique alors un **tableau** ``[null, {…}]`` au lieu
d'un objet, ce qui casse la sérialisation API. Le marquage traite donc ce
cas explicitement, et répare les fiches déjà abîmées par une exécution
antérieure de cette même migration.

Revision ID: 0028
Revises: 0027
Create Date: 2026-10-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

#: Marqueur de reprise — le retour arrière ne touche que ce qui a été
#: converti ici, jamais un ``importe`` né du nouveau dépôt.
_MARQUEUR = '{"_origine_reprise_0028": "true"}'

#: Réparation d'une exécution antérieure : ``doc_metadata`` était le littéral
#: jsonb ``null``, la concaténature en avait fait un tableau.
_REPARATION = sa.text(
    "UPDATE documents SET doc_metadata = CAST(:marqueur AS jsonb) "
    "WHERE jsonb_typeof(doc_metadata) = 'array' "
    "AND doc_metadata @> CAST(:recherche AS jsonb)"
)

#: Marquage des fiches « draft » de type cv ou rattachées à un appel. Le
#: littéral jsonb ``null`` est normalisé en objet vide : la trace de reprise
#: reste un objet, jamais un tableau.
_MARQUAGE = sa.text(
    "UPDATE documents "
    "SET doc_metadata = ("
    "  CASE WHEN doc_metadata IS NULL OR doc_metadata = 'null'::jsonb"
    "       THEN '{}'::jsonb ELSE doc_metadata END"
    ") || CAST(:marqueur AS jsonb) "
    "WHERE statut = 'draft' "
    "AND (type_document = 'cv' OR appel_a_proposition_id IS NOT NULL)"
)

#: Conversion elle-même : draft → importe pour ces mêmes fiches.
_REPRISE = sa.text(
    "UPDATE documents SET statut = 'importe' "
    "WHERE statut = 'draft' "
    "AND (type_document = 'cv' OR appel_a_proposition_id IS NOT NULL)"
)

#: Retour arrière : les fiches converties repassent à ``draft``, et la trace
#: de reprise quitte leurs métadonnées.
_RETOUR_STATUT = sa.text(
    "UPDATE documents SET statut = 'draft' "
    "WHERE statut = 'importe' "
    "AND (type_document = 'cv' OR appel_a_proposition_id IS NOT NULL) "
    "AND doc_metadata ? '_origine_reprise_0028'"
)
_RETOUR_MARQUEUR = sa.text(
    "UPDATE documents "
    "SET doc_metadata = doc_metadata - '_origine_reprise_0028' "
    "WHERE doc_metadata ? '_origine_reprise_0028'"
)


def upgrade() -> None:
    """``draft`` → ``importe`` pour les CV et documents d'appel existants."""
    op.execute(_REPARATION.bindparams(marqueur=_MARQUEUR, recherche=_MARQUEUR))
    op.execute(_MARQUAGE.bindparams(marqueur=_MARQUEUR))
    op.execute(_REPRISE)


def downgrade() -> None:
    """Retour aux statuts d'origine pour les seules fiches converties."""
    op.execute(_RETOUR_STATUT)
    op.execute(_RETOUR_MARQUEUR)
