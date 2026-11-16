"""Latence : le **même** prompt, sur le couple modèle/fournisseur actuel et l'ancien.

Objet (15/11) : la latence des agents (20 à 45 s pour un « Bonjour ») ne venait
ni du poids des outils ni de la longueur du prompt (tous deux réduits
entre-temps). Reste à mesurer ce que coûte le couple modèle/fournisseur lui-même.

Quatre mesures, un seul prompt (« Bonjour »), même température, même streaming :

* **A — couple d'avant** : ``qwen-3.8-27b`` @ ``https://logfare.ai/v1`` (ligne du
  catalogue ``modeles_ia``, clé chiffrée du catalogue). C'est le couple sous
  lequel le serveur a répondu pendant la recette du matin ;
* **B — couple actuel** : ``deepseek-v4.1-flash:free`` @
  ``https://tokenharbor.ai/v1`` (configuration du ``.env``) ;
* **C — même couple qu'A, entrée longue** : 44 000 caractères de contexte neutre
  devant le même message, pour mesurer ce que coûte réellement une grosse entrée ;
* **D — bout en bout** : le même message à travers l'API AG-UI du serveur en
  cours (agent complet, catalogue d'outils lié, flux SSE).

Les clés ne sont jamais affichées ni journalisées.

Usage ::

    ./.venv/Scripts/python.exe -m scripts.mesure_latence_couples
"""

from __future__ import annotations

import json
import statistics
import time
import urllib.request
import uuid
from dataclasses import dataclass, field

from langchain_openai import ChatOpenAI
from sqlalchemy import select

from app.core.chiffrement import dechiffrer
from app.core.config import get_settings
from app.domain.catalogue_ia import FournisseurIA, ModeleIA
from app.infrastructure.database import SessionLocal

PROMPT = "Bonjour"
MESURES = 5
MESURES_LONG = 3
MESURES_SERVEUR = 3

#: Contexte neutre pour la condition C : même longueur que le prompt réellement
#: assemblé (43 717 caractères mesurés le 15/11), sans instruction à suivre.
CONTEXTE_LONG = ("Le système CARSO gère appels, offres, missions, sessions. " * 900)[:43_717]


@dataclass(frozen=True, slots=True)
class Couple:
    libelle: str
    model: str
    base_url: str
    api_key: str = field(repr=False)


@dataclass(slots=True)
class Tour:
    ttft: float | None = None
    total: float | None = None
    caracteres: int = 0
    erreur: str | None = None


def _couple_catalogue(base_url: str, nom_modele: str) -> Couple | None:
    """Couple du catalogue (clé Fernet déchiffrée) ou ``None`` s'il est absent."""
    with SessionLocal() as session:
        for fournisseur in session.scalars(
            select(FournisseurIA).where(FournisseurIA.base_url == base_url)
        ):
            ligne = session.scalars(
                select(ModeleIA).where(
                    ModeleIA.fournisseur_id == fournisseur.id,
                    ModeleIA.nom_modele == nom_modele,
                )
            ).first()
            if ligne is None:
                continue
            cle = dechiffrer(fournisseur.api_key_chiffree)
            if cle:
                return Couple(
                    libelle="A — couple d'avant", model=nom_modele, base_url=base_url, api_key=cle
                )
    return None


def _couple_du_env() -> Couple | None:
    reglages = get_settings()
    cle = reglages.agent_api_key.get_secret_value() if reglages.agent_api_key else None
    if not (reglages.agent_base_url and reglages.agent_model and cle):
        return None
    return Couple(
        libelle="B — couple actuel",
        model=reglages.agent_model,
        base_url=reglages.agent_base_url,
        api_key=cle,
    )


def _mesurer(couple: Couple, prompt: str, mesures: int) -> list[Tour]:
    tours: list[Tour] = []
    for _ in range(mesures):
        modele = ChatOpenAI(
            model=couple.model,
            base_url=couple.base_url,
            api_key=couple.api_key,
            temperature=0.0,
            max_retries=0,
            timeout=180,
            streaming=True,
        )
        tour = Tour()
        debut = time.monotonic()
        try:
            for morceau in modele.stream(prompt):
                if tour.ttft is None:
                    tour.ttft = time.monotonic() - debut
                tour.caracteres += len(str(getattr(morceau, "content", "") or ""))
            tour.total = time.monotonic() - debut
        except Exception as erreur:  # noqa: BLE001 - toute panne est une mesure
            tour.total = time.monotonic() - debut
            tour.erreur = f"{type(erreur).__name__} : {str(erreur)[:120]}"
        tours.append(tour)
    return tours


def _resume(tours: list[Tour]) -> dict[str, object]:
    ttft = [t.ttft for t in tours if t.ttft is not None]
    totaux = [t.total for t in tours if t.total is not None]
    return {
        "reussis": len(ttft),
        "ttft_moyen": round(statistics.fmean(ttft), 2) if ttft else None,
        "ttft_min": round(min(ttft), 2) if ttft else None,
        "ttft_max": round(max(ttft), 2) if ttft else None,
        "total_moyen": round(statistics.fmean(totaux), 2) if totaux else None,
        "caracteres": round(statistics.fmean([t.caracteres for t in tours])) if tours else 0,
        "erreurs": sorted({t.erreur for t in tours if t.erreur}),
    }


def _afficher(libelle: str, couple: Couple, tours: list[Tour]) -> dict[str, object]:
    resume = {
        "condition": libelle,
        "model": couple.model,
        "base_url": couple.base_url,
        **_resume(tours),
    }
    print(f"=== {libelle} — {couple.model} @ {couple.base_url}")
    for index, tour in enumerate(tours, start=1):
        if tour.erreur:
            print(f"  tour {index} : ERREUR après {tour.total:.2f} s — {tour.erreur}")
        else:
            print(
                f"  tour {index} : premier jeton {tour.ttft:.2f} s · "
                f"total {tour.total:.2f} s · {tour.caracteres} caractères"
            )
    print(f"  résumé : {json.dumps(resume, ensure_ascii=False)}\n", flush=True)
    return resume


def _bout_en_bout(base: str = "http://127.0.0.1:8000") -> dict[str, object]:
    """Le même message à travers l'API AG-UI du serveur en cours."""

    def _appel(chemin: str, corps: dict | None, jeton: str | None = None):
        entetes = {"Content-Type": "application/json", "Accept": "text/event-stream"}
        if jeton:
            entetes["Authorization"] = f"Bearer {jeton}"
        donnees = json.dumps(corps).encode() if corps is not None else None
        return urllib.request.urlopen(
            urllib.request.Request(f"{base}{chemin}", data=donnees, headers=entetes),
            timeout=600,
        )

    try:
        with _appel(
            "/api/v1/auth/login", {"email": "admin@carso.mg", "mot_de_passe": "CarsoAdmin2026!"}
        ) as reponse:
            jeton = json.load(reponse)["access_token"]
    except Exception as erreur:  # noqa: BLE001
        return {"erreur": f"{type(erreur).__name__} : {str(erreur)[:160]}"}

    tours: list[dict[str, object]] = []
    for _ in range(MESURES_SERVEUR):
        charge = {
            "threadId": str(uuid.uuid4()),
            "runId": f"latence-{uuid.uuid4().hex[:8]}",
            "messages": [{"id": "m0", "role": "user", "content": PROMPT}],
            "tools": [],
            "context": [],
            "forwardedProps": {},
            "state": {},
            "resume": [],
        }
        debut = time.monotonic()
        premier_etape = premier_texte = None
        evenements = octets = 0
        erreur = None
        try:
            with _appel("/api/v1/agui/agent_generaliste_readonly", charge, jeton) as flux:
                for brut in flux:
                    octets += len(brut)
                    ligne = brut.decode("utf-8", "replace").strip()
                    if not ligne.startswith("data:"):
                        continue
                    contenu = ligne[5:].strip()
                    if not contenu or contenu == "[DONE]":
                        continue
                    evenements += 1
                    evenement = json.loads(contenu)
                    if evenement.get("type") == "STEP_STARTED" and premier_etape is None:
                        premier_etape = time.monotonic() - debut
                    if evenement.get("type") == "TEXT_MESSAGE_CONTENT" and premier_texte is None:
                        premier_texte = time.monotonic() - debut
                    if evenement.get("type") == "RUN_ERROR":
                        erreur = evenement.get("message")
        except Exception as panne:  # noqa: BLE001
            erreur = f"{type(panne).__name__} : {str(panne)[:120]}"
        tours.append(
            {
                "total": round(time.monotonic() - debut, 2),
                "etape_modele": round(premier_etape, 2) if premier_etape else None,
                "premier_texte": round(premier_texte, 2) if premier_texte else None,
                "evenements": evenements,
                "kio": round(octets / 1024, 1),
                "erreur": erreur,
            }
        )
    return {"tours": tours}


def main() -> int:
    resultats: list[dict[str, object]] = []
    avant = _couple_catalogue("https://logfare.ai/v1", "qwen-3.8-27b")
    actuel = _couple_du_env()

    if avant:
        resultats.append(_afficher(avant.libelle, avant, _mesurer(avant, PROMPT, MESURES)))
        resultats.append(
            _afficher(
                "C — même couple, entrée de 43 717 caractères",
                avant,
                _mesurer(avant, CONTEXTE_LONG + "\n\n" + PROMPT, MESURES_LONG),
            )
        )
    if actuel:
        resultats.append(_afficher(actuel.libelle, actuel, _mesurer(actuel, PROMPT, MESURES)))

    print("=== D — bout en bout (API AG-UI du serveur en cours, agent complet)")
    bout_en_bout = _bout_en_bout()
    print(json.dumps(bout_en_bout, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
