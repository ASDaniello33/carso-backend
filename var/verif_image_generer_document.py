"""Vérification réelle : un PNG déposé est **inséré** dans le PDF généré.

Le script passe par les vraies couches (PostgreSQL de dev, stockage local du
backend, tool `generer_document`), puis relit le PDF produit et compte ses
images avec pypdf. Il ne remplace pas les tests : il prouve le correctif sur
l'environnement qui fait foi pour l'utilisateur.

Usage : ``.venv/Scripts/python.exe var/verif_image_generer_document.py``
"""

from __future__ import annotations

import base64
import io
import json
import sys
from pathlib import Path
from uuid import UUID, uuid4

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from sqlalchemy import select  # noqa: E402

from app.application.dto import DocumentUploadInput  # noqa: E402
from app.application.perimetre import Perimetre  # noqa: E402
from app.application.services import DocumentService  # noqa: E402
from app.domain.identity import Utilisateur  # noqa: E402
from app.infrastructure.database import SessionLocal  # noqa: E402
from app.tools.documents import build_document_tools  # noqa: E402
from app.tools.permissions import AgentIdentity, PermissionPolicy  # noqa: E402

IDENTITE = AgentIdentity(agent_id="agent_generateur_offre")
POLICY = PermissionPolicy(
    allowed_capabilities=frozenset({"document_read", "document_propose"})
)
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
    "z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def main() -> int:
    session = SessionLocal()
    try:
        compte = session.scalars(
            select(Utilisateur).where(Utilisateur.email == "admin@carso.mg")
        ).first()
        if compte is None:
            print(json.dumps({"ok": False, "erreur": "compte admin introuvable"}))
            return 1

        perimetre = Perimetre(
            role=compte.role,
            utilisateur_id=compte.id,
            equipe_id=compte.equipe_id,
            est_complet=True,
        )
        service = DocumentService(
            session, actor_id=IDENTITE.agent_id, perimetre=perimetre
        )

        visuel = service.enregistrer_document(
            DocumentUploadInput(
                type_document="non_classe",
                nom="visuel_verification.png",
                stream=io.BytesIO(PNG),
                created_by="verif_image",
            )
        )

        spec = {
            "metadata": {
                "titre": "Vérification image insérée",
                "reference": "VERIF-IMAGE-1",
                "type_document": "autre",
            },
            "blocs": [
                {"type": "titre", "texte": "Vérification image insérée"},
                {
                    "type": "paragraphe",
                    "texte": "Ce document contient le PNG réel déposé dans le "
                    "dossier du compte, pas un schéma de remplacement.",
                },
                {
                    "type": "image",
                    "document_id": str(visuel.id),
                    "largeur_mm": 40,
                    "legende": "Visuel déposé",
                },
            ],
        }

        outils = {
            outil.name: outil
            for outil in build_document_tools(
                identite=IDENTITE,
                policy=POLICY,
                service_factory=lambda: service,
            )
        }
        sortie = outils["generer_document"].invoke(
            {
                "format": "pdf",
                "nom_fichier": "verif_image_inseree",
                "type_document": "autre",
                "propose_par": IDENTITE.agent_id,
                "document_spec": spec,
            }
        )

        identifiant = sortie.get("document_id") or sortie.get("documents", {}).get(
            "principal"
        )
        if not identifiant:
            print(json.dumps({"ok": False, "sortie": sortie}, ensure_ascii=False, indent=2))
            return 2
        document = service.obtenir(UUID(str(identifiant)))
        chemin = service.storage.root / document.storage_path
        octets = chemin.read_bytes()

        from pypdf import PdfReader

        lecteur = PdfReader(io.BytesIO(octets))
        images = sum(len(page.images) for page in lecteur.pages)
        avertissements = sortie.get("warnings") or []
        session.commit()

        resultat = {
            "ok": images > 0 and not any("fournisseur" in a for a in avertissements),
            "document_id": identifiant,
            "fichier": document.storage_path,
            "octets": len(octets),
            "pages": len(lecteur.pages),
            "images_pdf": images,
            "warnings": avertissements,
        }
        print(json.dumps(resultat, ensure_ascii=False, indent=2))
        return 0 if resultat["ok"] else 2
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
