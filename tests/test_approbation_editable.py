"""Approbation humaine **éditable** — le contrat qui permet de corriger un email.

Un email ne se rappelle pas : l'utilisateur doit voir et corriger les
destinataires, l'objet et le corps avant l'exécution. Cela tient à deux choses :

- le tool est déclaré dans ``approval_editable`` (contrat de l'agent) ;
- le harnais l'interrompt avec la décision ``edit`` autorisée — sans elle le
  middleware refuse la reprise, et l'interface ne pourrait qu'approuver/refuser.

Ces tests verrouillent la politique ``interrupt_on`` réellement construite.
"""

from __future__ import annotations

from app.agents import AgentRuntimeConfig
from app.agents.agent_rh import DEFINITION
from app.agents.harness import _politique_approbation


class _OutilNomme:
    """Suffisant pour la politique : seul le nom est lu."""

    def __init__(self, nom: str) -> None:
        self.name = nom


def _politique(noms: tuple[str, ...]) -> dict[str, object]:
    return _politique_approbation(
        DEFINITION,
        [_OutilNomme(nom) for nom in noms],
        AgentRuntimeConfig(interactive=True),
    )


def test_envoyer_email_accepte_la_decision_edit() -> None:
    """Sans `edit`, la carte de correction ne pourrait pas renvoyer le message."""
    politique = _politique(("envoyer_email",))

    assert politique["envoyer_email"] == {"allowed_decisions": ["approve", "edit", "reject"]}


def test_une_ecriture_sans_formulaire_reste_un_booleen() -> None:
    """Les autres approbations gardent le comportement d'origine."""
    politique = _politique(("create_assignment_proposal",))

    assert politique["create_assignment_proposal"] is True


def test_outil_editable_absent_du_contrat_effectif_est_ignore() -> None:
    """Un outil retiré par une surcharge administrée n'interrompt rien."""
    assert "envoyer_email" not in _politique(())


def test_hors_mode_interactif_aucune_interruption() -> None:
    """Un run non interactif (tâche inter-agent) ne s'arrête pas sur un humain."""
    assert (
        _politique_approbation(
            DEFINITION, [_OutilNomme("envoyer_email")], AgentRuntimeConfig(interactive=False)
        )
        == {}
    )


def test_chaque_outil_editable_est_aussi_approuve() -> None:
    """Invariant : `edit` sans `approve` serait une faille de contrat."""
    assert set(DEFINITION.approval_editable) <= set(DEFINITION.approval_required)
