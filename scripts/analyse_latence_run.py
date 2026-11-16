"""Analyse d'un journal backend : où passe le temps, étape par étape.

**Pourquoi cet outil.** Les lignes `étape model : … duree=X s` disent ce que
coûte chaque appel au modèle, mais un run de 840 s qui totalise 700 s d'étapes
laisse 140 s que personne ne nomme : outils, mise en forme, points de reprise.
Sans ce total, on attribue au modèle des lenteurs qui viennent d'ailleurs — et
l'inverse est vrai aussi.

Usage:
    python scripts/analyse_latence_run.py [--journal var/backend-8000.log]
        [--run <id>] [--thread <id>] [--derniers N] [--detail]
"""

from __future__ import annotations

import argparse
import re
import statistics
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

#: `2026-11-15 14:46:39 | INFO     | app.agents.mesure_modele | étape model : …`
ENTETE = re.compile(r"^(?P<horodatage>\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) \|")
CHAMP = re.compile(r"(\w+)=(.+?)(?=, \w+=|$)")
ETAPE = re.compile(r"étape model(?: lente \([^)]*\))? : (.*)$")
FIN_RUN = re.compile(r"run terminé : (.*)$")


def champs(texte: str) -> dict[str, str]:
    """Champs `cle=valeur` d'une ligne de journal, dans l'ordre d'apparition."""
    return {cle: valeur.strip().rstrip(",") for cle, valeur in CHAMP.findall(texte)}


def secondes(valeur: str) -> float:
    return float(valeur.replace(" s", "").strip())


def caracteres(valeur: str) -> int:
    return int(valeur.replace(" car.", "").replace(",", "").strip())


@dataclass
class Etape:
    horodatage: datetime
    duree: float
    entree: int
    messages: int


@dataclass
class Run:
    identifiant: str
    agent: str = "?"
    modele: str = "?"
    thread: str = "?"
    etapes: list[Etape] = field(default_factory=list)
    duree_declaree: float | None = None
    #: Renseignés à partir du 15/11 (soir) : le temps passé **dans** les outils,
    #: que les étapes model ne comptent pas. ``None`` pour les runs antérieurs.
    nb_outils: int | None = None
    temps_outils: float | None = None

    @property
    def debut(self) -> datetime:
        return self.etapes[0].horodatage

    @property
    def total_modele(self) -> float:
        return sum(e.duree for e in self.etapes)

    def duree(self) -> float:
        if self.duree_declaree is not None:
            return self.duree_declaree
        if not self.etapes:
            return 0.0
        return (self.etapes[-1].horodatage - self.debut).total_seconds() + self.etapes[-1].duree

    def trous(self) -> list[float]:
        """Écarts entre la fin d'une étape et le début de la suivante.

        C'est le temps passé *dans les outils* et entre deux tours : le seul
        poste que les lignes `étape model` ne comptent pas.

        **Attention au repère.** La ligne de journal est écrite quand l'étape se
        termine : ``horodatage`` est donc la **fin** de l'étape. L'écart se
        calcule contre la durée de l'étape **suivante** — soustraire celle de
        l'étape précédente (erreur commise ici) gonfle le total : sur le run du
        15/11 (`01a1df03`, 840 s), la mauvaise formule annonçait 320 s de trous
        pour 47 s réellement hors modèle.

        **Résolution.** L'horodatage du journal est à la seconde : un écart
        inférieur à 1 s n'est pas mesurable et peut ressortir **négatif** (−0,2 s
        par exemple). Ce n'est pas un défaut de la mesure de latence (les durées
        sont en fractions de seconde, précises) : c'est la granularité de
        l'horodatage. Ne lire les trous que comme « de l'ordre de la seconde ».
        """
        return [
            (self.etapes[i + 1].horodatage - self.etapes[i].horodatage).total_seconds()
            - self.etapes[i + 1].duree
            for i in range(len(self.etapes) - 1)
        ]


def analyser(chemin: Path) -> dict[str, Run]:
    """Regroupe les mesures par `run` : chaque ligne porte son identifiant."""
    runs: dict[str, Run] = {}
    with chemin.open(encoding="utf-8", errors="replace") as flux:
        for ligne in flux:
            entete = ENTETE.match(ligne)
            if not entete:
                continue
            horodatage = datetime.strptime(entete.group("horodatage"), "%Y-%m-%d %H:%M:%S")
            suite = ligne[entete.end() :]

            corps = ETAPE.search(suite)
            if corps:
                d = champs(corps.group(1))
                identifiant = d.get("run")
                if not identifiant:
                    continue
                run = runs.setdefault(identifiant, Run(identifiant))
                run.agent = d.get("agent", run.agent)
                run.modele = d.get("model", run.modele)
                run.thread = d.get("thread", run.thread)
                run.etapes.append(
                    Etape(
                        horodatage=horodatage,
                        duree=secondes(d.get("duree", "0")),
                        entree=caracteres(d.get("entree", "0")),
                        messages=int(d.get("messages", "0")),
                    )
                )
                continue

            fin = FIN_RUN.search(suite)
            if not fin:
                continue
            d = champs(fin.group(1))
            identifiant = d.get("run")
            if not identifiant:
                continue
            run = runs.setdefault(identifiant, Run(identifiant))
            run.agent = d.get("agent", run.agent)
            run.modele = d.get("model", run.modele)
            run.thread = d.get("thread", run.thread)
            if "duree" in d:
                run.duree_declaree = secondes(d["duree"])
            if "outils" in d:
                run.nb_outils = int(d["outils"])
            if "temps_outils" in d:
                run.temps_outils = secondes(d["temps_outils"])
    return runs


def rapporter(run: Run, detail: bool) -> None:
    if not run.etapes:
        return
    duree = run.duree()
    total = run.total_modele
    hors = max(0.0, duree - total)
    durees = [e.duree for e in run.etapes]
    trous = run.trous()

    print(f"\n=== run {run.identifiant} ===")
    print(f"agent        : {run.agent}")
    print(f"modèle       : {run.modele}")
    print(f"durée totale : {duree:.1f} s")
    print(f"étapes model : {len(run.etapes)} étapes, {total:.1f} s")
    if run.temps_outils is not None:
        print(f"outils       : {run.nb_outils or 0} appels, {run.temps_outils:.1f} s")
    if duree:
        print(f"hors modèle  : {hors:.1f} s ({100 * hors / duree:.1f} % du run)")
        if run.temps_outils is not None:
            # Les trois postes enfin séparés : modèle, outils, mise en forme.
            reste = max(0.0, hors - run.temps_outils)
            print(
                f"dont outils  : {100 * run.temps_outils / duree:.1f} % du run | "
                f"reste (mise en forme) : {reste:.1f} s ({100 * reste / duree:.1f} %)"
            )
    print(
        f"par étape    : médiane {statistics.median(durees):.1f} s, "
        f"max {max(durees):.1f} s, min {min(durees):.1f} s"
    )
    if trous:
        positifs = [t for t in trous if t > 0]
        print(
            f"trous        : médiane {statistics.median(trous):.1f} s, "
            f"max {max(trous):.1f} s, somme {sum(positifs):.1f} s "
            f"({100 * sum(positifs) / duree:.1f} % du run)"
        )
    print(f"entrée       : {run.etapes[0].entree:,} -> {run.etapes[-1].entree:,} car.")
    print(f"messages     : {run.etapes[0].messages} → {run.etapes[-1].messages}")

    if detail:
        print("\n   #  heure      durée    entrée     msg   écart")
        for i, e in enumerate(run.etapes):
            ecart = ""
            if i:
                precedent = run.etapes[i - 1]
                ecart = (
                    f"{(e.horodatage - precedent.horodatage).total_seconds() - e.duree:+.1f} s"
                )
            print(
                f"{i + 1:4d}  {e.horodatage:%H:%M:%S}  {e.duree:7.2f}  "
                f"{e.entree:10,}  {e.messages:4d}  {ecart:>9}"
            )


def principal(argv: list[str]) -> int:
    # Console Windows en cp1252 : sans cela, un tiret cadratin du journal
    # interrompt l'analyse en plein rapport.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    analyseur = argparse.ArgumentParser(description=__doc__)
    analyseur.add_argument("--journal", default="var/backend-8000.log")
    analyseur.add_argument("--run", default=None)
    analyseur.add_argument("--thread", default=None)
    analyseur.add_argument("--derniers", type=int, default=3)
    analyseur.add_argument("--detail", action="store_true")
    options = analyseur.parse_args(argv)

    chemin = Path(options.journal)
    if not chemin.exists():
        print(f"journal introuvable : {chemin}", file=sys.stderr)
        return 2

    runs = sorted(analyser(chemin).values(), key=lambda r: r.debut)
    if options.run:
        runs = [r for r in runs if r.identifiant == options.run]
    if options.thread:
        runs = [r for r in runs if r.thread == options.thread]
    if not runs:
        print("aucun run correspondant", file=sys.stderr)
        return 1

    print(f"{len(runs)} run(s) dans {chemin}")
    for run in runs[-options.derniers :]:
        rapporter(run, options.detail)
    return 0


if __name__ == "__main__":  # pragma: no cover - outil en ligne de commande
    raise SystemExit(principal(sys.argv[1:]))
