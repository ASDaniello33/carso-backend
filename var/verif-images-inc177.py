"""Vérification réelle (INC-177) : image par document_id puis par chemin.

Génère de vrais documents dans la base et le stockage du serveur, puis rend les
pages PDF et compte les pixels magenta. Un simple « média présent dans le DOCX »
ne prouverait pas que l'aperçu PDF affiche l'image.

À lancer depuis `backend/` : `PYTHONPATH=. .venv/Scripts/python.exe var/verif-images-inc177.py`
"""

from __future__ import annotations

import io
import json
from uuid import uuid4

from PIL import Image

from app.application.dto import DocumentUploadInput
from app.application.perimetre import Perimetre
from app.application.services import DocumentService
from app.domain.identity import Utilisateur
from app.infrastructure.database import SessionLocal
from app.tools.documents import build_document_tools
from app.tools.permissions import AgentIdentity, PermissionPolicy

ACTEUR = "verification-inc177"


def image_magenta() -> bytes:
    """PNG 400x200 entièrement magenta : impossible à confondre avec du texte."""
    tampon = io.BytesIO()
    Image.new("RGB", (400, 200), (255, 0, 255)).save(tampon, format="PNG")
    return tampon.getvalue()


def pixels_magenta(png: bytes) -> int:
    with Image.open(io.BytesIO(png)).convert("RGB") as rendu:
        return sum(
            1
            for r, g, b in rendu.getdata()
            if r > 200 and b > 200 and g < 80
        )


def main() -> None:
    session = SessionLocal()
    # Le propriétaire d'un document est un compte réel (FK documents.proprietaire_id).
    administrateur = (
        session.query(Utilisateur)
        .filter(Utilisateur.role == "administrateur")
        .order_by(Utilisateur.created_at)
        .first()
    )
    assert administrateur is not None, "aucun compte administrateur en base"
    perimetre = Perimetre(
        role="administrateur", utilisateur_id=administrateur.id, est_complet=True
    )
    service = DocumentService(session, actor_id=ACTEUR, perimetre=perimetre)
    try:
        image = service.enregistrer_document(
            DocumentUploadInput(
                type_document="autre",
                nom="preuve-magenta.png",
                stream=io.BytesIO(image_magenta()),
                created_by=ACTEUR,
            )
        )
        session.commit()
        outils = {
            outil.name: outil
            for outil in build_document_tools(
                identite=AgentIdentity(agent_id="agent_assistant_formateur"),
                policy=PermissionPolicy(
                    allowed_capabilities=frozenset(
                        {"document_read", "document_propose"}
                    )
                ),
                service_factory=lambda: DocumentService(
                    session, actor_id=ACTEUR, perimetre=perimetre
                ),
            )
        }

        corps = (
            "<h1>Preuve image</h1><p class='lead'>Image insérée depuis le stockage.</p>"
            "<figure data-image='preuve'></figure>"
        )
        resultats: dict[str, object] = {
            "image_document_id": str(image.id),
            "image_chemin": image.storage_path,
        }

        cas = [
            (
                "professionnel_document_id",
                "generer_document_professionnel",
                {
                    "action": "generer",
                    "contenu_html": corps,
                    "titre": "Preuve par document_id",
                    "format": "docx",
                    "type_document": "rapport",
                    "nom_fichier": "preuve_document_id",
                    "images": [{"id": "preuve", "document_id": str(image.id)}],
                },
            ),
            (
                "professionnel_chemin",
                "generer_document_professionnel",
                {
                    "action": "generer",
                    "contenu_html": corps,
                    "titre": "Preuve par chemin",
                    "format": "docx",
                    "type_document": "rapport",
                    "nom_fichier": "preuve_chemin",
                    "images": [{"id": "preuve", "chemin": image.storage_path}],
                },
            ),
            (
                "libre_pdf_document_id",
                "generer_document",
                {
                    "html": corps,
                    "titre": "Preuve libre PDF",
                    "nom_fichier": "preuve_libre_pdf",
                    "format": "pdf",
                    "type_document": "rapport",
                    "images": [{"id": "preuve", "document_id": str(image.id)}],
                },
            ),
        ]

        for nom, outil, payload in cas:
            sortie = outils[outil].invoke(payload)
            if not sortie.get("ok", True):
                resultats[nom] = {"erreur": sortie.get("error")}
                continue
            documents = sortie.get("documents") or {}
            docx = documents.get("principal") or sortie.get("document_id")
            pdf = documents.get("pdf") or (
                docx if str(docx) == str(sortie.get("document_id")) and
                str(sortie.get("nom", "")).endswith(".pdf") else None
            )
            resultats[nom] = {
                "document_id": docx,
                "pdf_id": pdf,
                "format": sortie.get("format"),
                "avertissements": sortie.get("avertissements"),
            }
            if pdf:
                service.apercu_document(pdf, pages=[1], dpi=80)
                _, chemin = service.chemin_page_apercu(pdf, 1)
                resultats[nom]["pixels_magenta_page1"] = pixels_magenta(
                    chemin.read_bytes()
                )
            if docx and str(docx) != str(pdf):
                courant = service.obtenir(docx)
                octets = service.storage.read(courant.storage_path)
                resultats[nom]["media_docx"] = b"word/media/" in octets
            # Les pages ne servent à l'API que si les documents sont écrits.
            session.commit()

        # Contrôle **par l'API redémarrée** : c'est l'aperçu réellement servi
        # au navigateur, pas le fichier lu par le script.
        import httpx

        base = "http://127.0.0.1:8000/api/v1"
        client = httpx.Client(base_url=base, timeout=30)
        connexion = client.post(
            "/auth/login",
            json={"email": "admin@carso.mg", "mot_de_passe": "CarsoAdmin2026!"},
        )
        resultats["api_login"] = connexion.status_code
        if connexion.status_code == 200:
            entetes = {
                "Authorization": "Bearer " + connexion.json()["access_token"]
            }
            for nom, infos in resultats.items():
                if not isinstance(infos, dict) or not infos.get("pdf_id"):
                    continue
                reponse = client.get(
                    f"/documents/{infos['pdf_id']}/apercu/1", headers=entetes
                )
                infos["api_apercu_http"] = reponse.status_code
                if reponse.status_code == 200:
                    infos["api_pixels_magenta"] = pixels_magenta(reponse.content)

        print(json.dumps(resultats, indent=2, ensure_ascii=False, default=str))
    finally:
        session.close()


if __name__ == "__main__":
    main()
