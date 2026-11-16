"""Sonde : la passerelle appliquée honore-t-elle la fenêtre qu'elle annonce ?

Envoie des entrées de plus en plus volumineuses (jusqu'au seuil de résumé) au
modèle **réellement appliqué** par la configuration runtime, et regarde :

- si le fournisseur accepte (200) ;
- s'il refuse (400/413 et message) ;
- s'il tronque (marqueur de FIN introuvable, ou prompt_tokens < attendu).

Chaque appel porte un marqueur au DÉBUT et à la FIN du remplissage : si le
modèle sait dire la fin, la fin est arrivée. Aucune donnée métier, aucun secret
journalisé.

Usage :

    python scripts/sonde_fenetre_reelle.py [paliers en caractères…]

Sans argument, la série par défaut encadre le seuil de résumé (75 % de la
fenêtre). Des paliers explicites permettent de viser le plafond annoncé :

    python scripts/sonde_fenetre_reelle.py 3900000 4400000
"""

from __future__ import annotations

import json
import sys
import time

import httpx

sys.path.insert(0, ".")

from app.application.services.agent_runtime_service import AgentRuntimeService
from app.core.chiffrement import dechiffrer
from app.infrastructure.database import SessionLocal

DEBUT, FIN = "ZQLM4471", "VTPN8893"

GABARITS = [
    "Le paragraphe {i} decrit une exigence fonctionnelle du systeme et ne sert ici "
    "qu'a occuper la fenetre de contexte du modele.",
    "Ligne {i} : le rapport d'analyse mentionne des indicateurs, des zones "
    "d'intervention et des echeances contractuelles variees.",
    "Note {i} du dossier technique : les livrables attendus comprennent des documents "
    "normalises et des tableaux de suivi detailles.",
]

#: Paliers, en caractères. 4 car./jeton ≈ 1 000 000 de jetons à 4 000 000 car.
#: Le seuil de résumé configuré (75 % de 1 000 000) est à 750 000 jetons.
PALIERS = [100_000, 500_000, 1_000_000, 2_000_000, 3_200_000]


def remplissage(cible: int) -> str:
    morceaux: list[str] = []
    taille = 0
    i = 0
    while taille < cible:
        t = GABARITS[i % len(GABARITS)].format(i=i) + " "
        morceaux.append(t)
        taille += len(t)
        i += 1
    return "".join(morceaux)[:cible]


def appeler(client: httpx.Client, base: str, cle: str, modele: str, cible: int) -> None:
    corps = (
        f"MARQUEUR_DEBUT {DEBUT}\n"
        + remplissage(cible)
        + f"\nMARQUEUR_FIN {FIN}\n\n"
        + f"Reponds uniquement: {DEBUT}-{FIN}"
    )
    t0 = time.perf_counter()
    try:
        rep = client.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {cle}", "Content-Type": "application/json"},
            json={
                "model": modele,
                "messages": [{"role": "user", "content": corps}],
                "max_tokens": 30,
                "temperature": 0,
            },
        )
    except Exception as erreur:  # noqa: BLE001 - sonde
        print(f"{cible:>10,} car. -> EXCEPTION {type(erreur).__name__}: {str(erreur)[:120]}")
        sys.stdout.flush()
        return
    duree = time.perf_counter() - t0

    ligne = f"{cible:>10,} car. -> HTTP {rep.status_code} en {duree:.1f} s"
    if rep.status_code == 200:
        j = rep.json()
        u = j.get("usage", {})
        pt = u.get("prompt_tokens")
        texte = (j["choices"][0]["message"].get("content") or "").strip()
        fin_vue = FIN in texte
        if pt:
            ligne += f" | prompt_tokens={pt:,} | car/jeton={cible / pt:.2f}"
        ligne += f" | finish={j['choices'][0].get('finish_reason')} | fin_du_prompt_vue={fin_vue}"
        print(ligne)
        print(f"{'':>13}reponse: {texte[:80]!r}")
    else:
        try:
            err = rep.json().get("error", {})
            detail = f"{err.get('type', '')} {err.get('message', '')[:200]}"
        except Exception:  # noqa: BLE001
            detail = rep.text[:200]
        print(ligne)
        print(f"{'':>13}erreur: {detail.replace(chr(10), ' ')}")
    sys.stdout.flush()


def main() -> None:
    with SessionLocal() as session:
        actif = AgentRuntimeService(session).obtenir_active()
        base = (actif.base_url or "").rstrip("/")
        cle = dechiffrer(actif.api_key_chiffree) or ""
        modele = actif.model

    print(f"fournisseur appliqué : {base}")
    print(f"modele appliqué      : {modele}")
    with httpx.Client(timeout=900.0, verify=False) as client:
        r = client.get(f"{base}/models", headers={"Authorization": f"Bearer {cle}"})
        for m in r.json().get("data", []):
            print(f"  {m['id']:30} context_length={m.get('context_length')}")
        print()
        paliers = [int(a) for a in sys.argv[1:]] or PALIERS
        for cible in paliers:
            appeler(client, base, cle, modele, cible)


if __name__ == "__main__":
    main()
