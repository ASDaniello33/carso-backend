"""Service — paramètres de voyage de l'organisation.

Une seule ligne, une seule source : l'origine par défaut (siège CARSO) et le
coût du carburant. Ces valeurs alimentent le formulaire de calcul, qui peut
les surcharger pour un trajet précis (un autre véhicule consomme plus). La
modification est réservée à l'administration — c'est une décision
d'organisation, pas une préférence de compte.

Les bornes sont volontairement larges mais réelles : une consommation
négative ou un prix négatif rendraient un coût absurdement favorable.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.application.trace import TraceContext
from app.core.errors import ValidationError
from app.domain.enums import ActionAudit, ActorType
from app.domain.geo import PARAMETRES_VOYAGE_ID, ParametresVoyage
from app.infrastructure.repositories.geo import ParametresVoyageRepository

_CONSO_MIN, _CONSO_MAX = 1.0, 60.0  # L/100 km : voiture légère → camion lourd
_PRIX_MIN, _PRIX_MAX = 0.0, 10_000_000.0


@dataclass(frozen=True, slots=True)
class ParametresVoyageMiseAJour:
    """Champs fournis par l'administrateur (un champ absent est conservé)."""

    origine_libelle: str | None = None
    origine_latitude: float | None = None
    origine_longitude: float | None = None
    consommation_l_100km: float | None = None
    prix_litre: float | None = None
    devise: str | None = None


class ParametresVoyageService:
    """Lecture pour tous, écriture par l'administration (garde de la route)."""

    def __init__(self, session: Session, actor_id: str | None = None) -> None:
        self._session = session
        self.parametres = ParametresVoyageRepository(session)
        self._trace = TraceContext(session, actor_id=actor_id)

    def lire(self) -> ParametresVoyage:
        """Réglages courants (la ligne unique existe par la migration)."""
        return self.parametres.lire_unique()

    def origine(self) -> tuple[str | None, float | None, float | None]:
        """Point de départ par défaut ``(libellé, latitude, longitude)``."""
        ligne = self.lire()
        if ligne.origine_latitude is None or ligne.origine_longitude is None:
            return ligne.origine_libelle, None, None
        return (
            ligne.origine_libelle,
            float(ligne.origine_latitude),
            float(ligne.origine_longitude),
        )

    def modifier(self, mise_a_jour: ParametresVoyageMiseAJour) -> ParametresVoyage:
        """Applique les champs fournis, avec bornes et paires complètes.

        Raises:
            ValidationError: coordonnées partielles ou hors plage, consommation
                ou prix hors bornes, libellé ou devise vide.
        """
        from app.application.services.lieu_service import valider_coordonnees

        ligne = self.parametres.lire_unique()
        avant = {
            "origine_libelle": ligne.origine_libelle,
            "consommation_l_100km": float(ligne.consommation_l_100km),
            "prix_litre": float(ligne.prix_litre),
            "devise": ligne.devise,
        }
        modifie = False

        if mise_a_jour.origine_libelle is not None:
            libelle = mise_a_jour.origine_libelle.strip()
            if not libelle:
                raise ValidationError("Le libellé de l'origine ne peut pas être vide")
            ligne.origine_libelle = libelle
            modifie = True

        # Les coordonnées d'origine se règlent ensemble ; ``None`` global retire
        # le départ par défaut (le formulaire demande alors une origine à chaque
        # calcul) — c'est une décision explicite, pas un oubli.
        if mise_a_jour.origine_latitude is not None or mise_a_jour.origine_longitude is not None:
            coordonnees = valider_coordonnees(
                mise_a_jour.origine_latitude, mise_a_jour.origine_longitude
            )
            if coordonnees is None:
                ligne.origine_latitude = None
                ligne.origine_longitude = None
            else:
                ligne.origine_latitude, ligne.origine_longitude = coordonnees
            modifie = True

        if mise_a_jour.consommation_l_100km is not None:
            conso = mise_a_jour.consommation_l_100km
            if not _CONSO_MIN <= conso <= _CONSO_MAX:
                raise ValidationError(
                    f"Consommation invalide : entre {_CONSO_MIN} et {_CONSO_MAX} L/100 km",
                    details={"consommation_l_100km": conso},
                )
            ligne.consommation_l_100km = round(conso, 2)
            modifie = True

        if mise_a_jour.prix_litre is not None:
            prix = mise_a_jour.prix_litre
            if not _PRIX_MIN <= prix <= _PRIX_MAX:
                raise ValidationError(
                    f"Prix du litre invalide : entre {_PRIX_MIN} et {_PRIX_MAX}",
                    details={"prix_litre": prix},
                )
            ligne.prix_litre = round(prix, 2)
            modifie = True

        if mise_a_jour.devise is not None:
            devise = mise_a_jour.devise.strip()
            if not devise or len(devise) > 10:
                raise ValidationError(
                    "Devise invalide : un code ou un symbole court (ex. MGA)",
                    details={"devise": mise_a_jour.devise},
                )
            ligne.devise = devise
            modifie = True

        if not modifie:
            raise ValidationError(
                "Aucune modification fournie pour les paramètres de voyage"
            )
        self._session.flush()
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.PARAMETRES_VOYAGE_MODIFIES.value,
            entity_type="parametres_voyage",
            entity_id=PARAMETRES_VOYAGE_ID,
            before=avant,
            after={
                "origine_libelle": ligne.origine_libelle,
                "consommation_l_100km": float(ligne.consommation_l_100km),
                "prix_litre": float(ligne.prix_litre),
                "devise": ligne.devise,
            },
        )
        return ligne


__all__ = [
    "ParametresVoyageMiseAJour",
    "ParametresVoyageService",
]
