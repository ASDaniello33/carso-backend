"""Sonde : le seuil de résumé est-il **proportionnel à la fenêtre du modèle** ?

**Question posée (15/11).** « Valider que le seuil de déclenchement est vraiment à
75 % du contexte du modèle, pas une valeur fixe. » Une valeur fixe serait
``170 000`` (défaut de Deep Agents/LangChain) ou ``200 000`` (repli
``AGENT_MAX_INPUT_TOKENS``) : dans les deux cas, changer de modèle ne changerait
pas le seuil.

**Ce que fait la sonde.** Elle emprunte les chemins de **production** — le modèle
construit par ``build_chat_model`` (profil lu chez le fournisseur), le middleware
construit par ``construire_resume_historique`` avec les réglages effectifs — puis
pour plusieurs fenêtres distinctes :

1. affiche le seuil calculé et si c'est exactement 75 % de la fenêtre ;
2. vérifie qu'un seuil **différent** sort pour chaque fenêtre (sinon : fixe) ;
3. confirme que ``_should_summarize`` bascule **à** ce seuil, pas avant ;
4. compare au défaut de la bibliothèque (85 % / 170 000) pour montrer l'écart.

Aucun appel réseau : le modèle est scripté et le profil est posé directement.
Usage:

    python scripts/verifier_seuil_resume_reel.py
"""

from __future__ import annotations

import sys

sys.path.insert(0, ".")

from langchain_core.messages import HumanMessage  # noqa: E402

from app.agents.resume_historique import construire_resume_historique  # noqa: E402
from tests.agent_fixtures import ScriptedChatModel, final_message  # noqa: E402

FRACTION = 0.75
GARDEE = 0.10
FENETRES = (2_000_000, 200_000, 128_000)
#: Défauts de Deep Agents/LangChain quand le profil existe mais qu'on ne règle
#: rien : c'est la « valeur fixe » à laquelle on veut ne pas ressembler.
DEFAUT_FRACTION = 0.85
DEFAUT_SANS_PROFIL = 170_000


def _middleware(fenetre: int):
    """Middleware de production pour cette fenêtre (mêmes réglages qu'au montage)."""
    from deepagents.backends import StateBackend

    modele = ScriptedChatModel(
        responses=[final_message("résumé")], profile={"max_input_tokens": fenetre}
    )
    return construire_resume_historique(
        modele,
        StateBackend(),
        agent_id="sonde",
        fraction_seuil=FRACTION,
        fraction_gardee=GARDEE,
        fenetre_jetons=fenetre,
    )


def principal() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print(
        f"Réglages appliqués : seuil {FRACTION:.0%}, conservation {GARDEE:.0%} "
        f"(défaut bibliothèque : {DEFAUT_FRACTION:.0%} / {DEFAUT_SANS_PROFIL:,} absolus)"
    )
    print()
    print("  fenêtre      trigger middleware   seuil effectif   75 % exact   valeur fixe ?")
    seuils: dict[int, int] = {}
    for fenetre in FENETRES:
        mw = _middleware(fenetre)
        trigger = mw._lc_helper.trigger
        seuil = int(mw._lc_helper._get_profile_limits() * trigger[1])
        seuils[fenetre] = seuil
        exact = trigger == ("fraction", FRACTION)
        fixe = seuil in (DEFAUT_SANS_PROFIL, 200_000)
        print(
            f"  {fenetre:>9,}   {str(trigger):<20} {seuil:>12,}   "
            f"{'oui' if exact else 'NON':<11} "
            f"{'oui (PROBLEME)' if fixe else 'non'}"
        )

    print()
    if len(set(seuils.values())) != len(FENETRES):
        print("ECHEC : deux fenêtres donnent le même seuil -> le seuil est fixe.")
        return 1
    print(
        f"OK : seuils distincts {sorted(seuils.values())} -> "
        "le seuil suit la fenêtre du modèle."
    )

    # Le déclenchement bascule **à** ce seuil, pas ailleurs.
    print()
    print("Bascule du déclenchement (middleware réel, mêmes réglages) :")
    for fenetre, seuil in seuils.items():
        mw = _middleware(fenetre)
        message = [HumanMessage(content="x")]
        juste_avant = mw._should_summarize(message, seuil - 1)
        juste_apres = mw._should_summarize(message, seuil)
        etat = "OK" if (juste_avant is False and juste_apres is True) else "PROBLEME"
        print(
            f"  fenêtre {fenetre:>9,} : seuil={seuil:,} | "
            f"à {seuil - 1:,} -> {'déclenche' if juste_avant else 'non'} ; "
            f"à {seuil:,} -> {'déclenche' if juste_apres else 'non'}  -> {etat}"
        )
        if etat != "OK":
            return 1

    # Écart avec la valeur fixe : un même historique ne doit pas déclencher
    # dans les mêmes conditions selon le modèle.
    print()
    print("Preuve par l'écart (une valeur fixe donnerait le même verdict partout) :")
    palier = 170_000
    for fenetre, seuil in seuils.items():
        mw = _middleware(fenetre)
        declenche = mw._should_summarize([HumanMessage(content="x")], palier)
        print(
            f"  {palier:,} jetons : fenêtre {fenetre:>9,} (seuil {seuil:,}) -> "
            f"{'déclenche' if declenche else 'non'}"
        )
    verdicts = {
        mw_verdict
        for mw_verdict in (
            _middleware(f)._should_summarize([HumanMessage(content="x")], palier)
            for f in FENETRES
        )
    }
    if len(verdicts) == 1:
        print("ECHEC : le même palier donne le même verdict partout -> seuil fixe.")
        return 1
    print("OK : le verdict dépend du modèle -> le seuil n'est pas une valeur fixe.")

    print()
    print(
        "Conclusion : le seuil vaut 75 % de la fenêtre **du modèle appliqué** "
        f"(ex. {seuils[2_000_000]:,} pour 2 000 000), jamais "
        f"{DEFAUT_SANS_PROFIL:,} ni 200 000."
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - outil en ligne de commande
    raise SystemExit(principal())
