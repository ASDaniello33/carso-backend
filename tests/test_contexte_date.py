"""La date du jour dans le contexte de **tous** les agents.

Sans elle, « cette semaine » se déduit du document lu et une donnée de la
semaine dernière passe pour actuelle. Le bloc de contexte est le seul endroit
injecté à tous les agents (`ContexteUtilisateurMiddleware`) : c'est là que la
date doit figurer, une fois.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.agents.contexte import UserContext


def test_bloc_contexte_porte_la_date_en_clair_et_en_iso():
    contexte = UserContext(role="administrateur", nom_complet="Aina")

    bloc = contexte.bloc_prompt(maintenant=datetime(2026, 10, 30, 9, 15, tzinfo=UTC))

    assert "Date du jour" in bloc
    # 09:15 UTC = 12:15 à Toliara, le même jour.
    assert "vendredi 30 octobre 2026" in bloc
    assert "2026-10-30" in bloc
    assert "12:15" in bloc


def test_bloc_contexte_rappelle_que_la_date_prime_sur_les_documents():
    contexte = UserContext(role="formateur", est_interne=False)

    # 22:30 UTC le 30 = 01:30 le 31 à Toliara : le jour bascule avec le fuseau.
    bloc = contexte.bloc_prompt(maintenant=datetime(2026, 10, 30, 22, 30, tzinfo=UTC))

    assert "se calculent à partir de cette date" in bloc
    assert "ancienne, dis-le" in bloc
    assert "samedi 31 octobre 2026" in bloc
