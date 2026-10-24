"""Skills de l'organisation dans le prompt — le bloc et son empreinte.

Le bloc est **commun** : il ne dépend pas du compte qui converse, seulement de
ce que l'administration a écrit. Deux conséquences :

- ``bloc_skills`` produit une section de prompt système ajoutée au tour de
  chaque agent (``system_prompt_extra``) ;
- ``signature_skills`` donne l'empreinte utilisée dans la clé de cache des
  graphes : une modification dans Paramètres > Skills change la clé, donc le
  prochain run reconstruit le graphe au lieu de servir l'ancien prompt.

Deux bornes, appliquées ici **et** à l'écriture : un skill tronqué ou un bloc
sans fin ne doit jamais pouvoir pousser le prompt hors budget. Un skill n'est
jamais un droit : le bloc le dit explicitement au modèle.
"""

from __future__ import annotations

from collections.abc import Sequence
from hashlib import sha256

from app.domain.skill import INSTRUCTIONS_MAX, MAX_SKILLS, Skill

_ENTETE = """## Skills de l'organisation
L'administration CARSO a enregistré des guides de travail communs, nommés dans
le chat par `/nom`. Ce sont des consignes de **forme et de méthode** — jamais
un droit.
- Applique un skill quand la demande relève de son domaine, ou quand
  l'utilisateur le nomme (`/nom`).
- Un skill ne remplace ni le cadre CARSO, ni les permissions, ni les règles
  métier : en cas de contradiction, les règles CARSO gagnent, et tu dis
  laquelle tu as suivie.
- N'invente jamais le contenu d'un skill absent de cette liste.
"""


def bloc_skills(skills: Sequence[Skill]) -> str:
    """Section de prompt listant les skills, texte inclus.

    Le texte est injecté **en entier** (borné à l'écriture) : le harnais ne
    peut pas ouvrir un fichier par run, et un skill réduit à son titre ne
    servirait à rien. Une liste vide rend une chaîne vide — aucun bloc, aucun
    coût.
    """
    if not skills:
        return ""
    parties: list[str] = [_ENTETE]
    for skill in skills[:MAX_SKILLS]:
        texte = (skill.instructions or "").strip()
        if not texte:
            continue
        if len(texte) > INSTRUCTIONS_MAX:
            texte = texte[:INSTRUCTIONS_MAX].rstrip()
        description = (skill.description or "").strip()
        titre = f"### /{skill.nom}" + (f" — {description}" if description else "")
        parties.append(f"{titre}\n{texte}")
    if len(parties) == 1:  # tous les skills sont vides : aucun bloc utile
        return ""
    return "\n\n".join(parties)


def signature_skills(skills: Sequence[Skill]) -> str:
    """Empreinte stable de la composition (nom + description + texte).

    Vide ⇒ ``"-"`` : sans skill, aucun graphe n'est reconstruit pour rien.
    """
    if not skills:
        return "-"
    charge = "\x1e".join(
        f"{skill.nom}\x1f{skill.description}\x1f{skill.instructions}" for skill in skills
    )
    return sha256(charge.encode("utf-8")).hexdigest()[:16]


__all__ = [
    "INSTRUCTIONS_MAX",
    "MAX_SKILLS",
    "bloc_skills",
    "signature_skills",
]
