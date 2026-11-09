"""Mesure la conformité réelle des offres FID au modèle de l'organisation.

Question posée : le document produit respecte-t-il le modèle du client, et est-il
conforme aux termes de référence ? Ce script lit les documents réellement déposés
pour l'offre technique FID, les compare au DOCX du modèle au registre, et rend un
tableau par document.

Aucune écriture : lecture seule des documents et du modèle.
"""

from __future__ import annotations

import json
import re
import sys
import unicodedata
import urllib.request
import zipfile
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree as ET

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.application.services import DocumentService  # noqa: E402
from app.infrastructure.database import SessionLocal  # noqa: E402

BASE = "http://127.0.0.1:8000"
TOK = (BACKEND / "var/eval_tmp/tok.txt").read_text().strip()
OFFRE = "f12fb5d5-0a4b-4eee-a918-1d4f31810e26"
MODELE_LOCAL = (
    BACKEND
    / "var/storage/carso/organisations/13382ac8-d3ef-4cb1-a1ed-9d6c5da306d0"
    / "Model_offre_technique_FID.docx"
)
PARTIE_CORPS = "word/document.xml"
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def api(chemin: str):
    requete = urllib.request.Request(BASE + chemin, headers={"Authorization": f"Bearer {TOK}"})
    return json.load(urllib.request.urlopen(requete))


def parties(brut: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(BytesIO(brut)) as z:
        return {n: z.read(n) for n in z.namelist() if not n.endswith("/")}


def textes(p: dict[str, bytes]) -> list[str]:
    if PARTIE_CORPS not in p:
        return []
    racine = ET.fromstring(p[PARTIE_CORPS])
    out = []
    for paragraphe in racine.iter(f"{W}p"):
        ligne = "".join((t.text or "") for t in paragraphe.iter(f"{W}t")).strip()
        if ligne:
            out.append(ligne)
    return out


def norm(t: str) -> str:
    sans = "".join(c for c in unicodedata.normalize("NFKD", t) if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", sans).casefold()


def main() -> int:
    session = SessionLocal()
    depot = DocumentService(session)
    pm = parties(MODELE_LOCAL.read_bytes())
    attendues = [n for n in pm if n != PARTIE_CORPS]

    docs = api(f"/api/v1/documents/?offre_id={OFFRE}")
    lignes = []
    for doc in docs:
        if not doc["nom"].lower().endswith(".docx"):
            continue
        try:
            _nom, chemin = depot.telecharger(doc["id"])  # type: ignore[arg-type]
            pl = parties(Path(chemin).read_bytes())
        except Exception as exc:  # noqa: BLE001
            lignes.append((doc["nom"], "illisible", 0, 0, str(exc)[:40]))
            continue
        identiques = sum(1 for n in attendues if pl.get(n) == pm[n])
        txt = norm(" ".join(textes(pl)))
        mots = len(txt.split())
        faits = {
            "24 participants": r"\b24\b" in txt,
            "30 participants": r"\b30\b" in txt,
            "4 jours": bool(re.search(r"(quatre|4)\s*(\(0?4\)\s*)?jours?", txt)),
            "3 jours": bool(re.search(r"(trois|3)\s*(\(0?3\)\s*)?jours?", txt)),
            "7 jours": bool(re.search(r"(sept|7)\s*(\(0?7\)\s*)?jours?", txt)),
            "2 groupes": bool(re.search(r"vague|groupe", txt)),
            "reference AMI": bool(re.search(r"59\D{0,6}ami|ami\D{0,6}59", txt)),
        }
        lignes.append(
            (
                doc["nom"],
                f"{identiques}/{len(attendues)}",
                mots,
                sum(faits.values()),
                ",".join(k for k, v in faits.items() if not v) or "-",
            )
        )

    print(f"{'document':52} {'parties':>9} {'mots':>6} {'faits':>5}  manquants")
    for nom, parties_ok, mots, faits, manquants in sorted(lignes):
        print(f"{nom[:52]:52} {parties_ok:>9} {mots:>6} {faits:>5}  {manquants}")
    session.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
