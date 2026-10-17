"""Réparation jetable (une seule écriture) : relit les comptes orphelins.

Pour chaque compte actif (collaborateur/formateur) sans fiche vivier
(``equipe_id IS NULL``) dont l'email correspond à une fiche **active** existante,
applique exactement la logique du correctif (``EquipeService._relier_compte_orphelin``)
puis commit. Les droits restent calculés depuis les affectations approuvées —
ce geste ne crée aucun droit, il restaure le lien compte ↔ source de vérité.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application.perimetre import pour_utilisateur
from app.application.services.equipe_service import EquipeService
from app.core.config import get_settings
from app.domain.enums import RoleUtilisateur, StatutEquipe
from app.domain.execution import Equipe
from app.domain.identity import Utilisateur

from sqlalchemy import create_engine

engine = create_engine(str(get_settings().database_url))
with Session(engine) as session:
    orphelins = list(
        session.scalars(
            select(Utilisateur).where(
                Utilisateur.equipe_id.is_(None),
                Utilisateur.role.in_(
                    (RoleUtilisateur.COLLABORATEUR.value, RoleUtilisateur.FORMATEUR.value)
                ),
            )
        )
    )
    print(f"Comptes orphelins (rôle gérable) : {len(orphelins)}")
    service = EquipeService(session)
    reparies = 0
    for compte in orphelins:
        fiche = session.scalar(
            select(Equipe).where(
                Equipe.email == compte.email,
                Equipe.statut == StatutEquipe.ACTIF.value,
            )
        )
        if fiche is None:
            print(f"  {compte.email} : aucune fiche active de même email — rien à faire")
            continue
        relit = service._relier_compte_orphelin(fiche)
        if relit is not None:
            reparies += 1
            print(f"  {compte.email} : relié à la fiche {fiche.id}")
    if reparies:
        session.commit()
        print(f"\n{reparies} compte(s) relié(s) et commit.")
    else:
        print("\nRien à réparer (aucun commit).")

    # Vérification directe : périmètre des comptes touchés.
    for compte in orphelins:
        session.refresh(compte)
        perimetre = pour_utilisateur(session, compte)
        print(
            f"  {compte.email} : missions_autorisées={len(perimetre.missions_autorisees)} "
            f"(est_complet={perimetre.est_complet})"
        )
