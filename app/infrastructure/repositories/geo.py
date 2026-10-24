"""Repositories géographiques — cache d'itinéraires et paramètres de voyage.

Le cache est une **table**, pas un dictionnaire : il survit aux redémarrages et
se partage entre instances. La lecture refuse une ligne expirée (le quota
gratuit ne doit pas empêcher l'utilisateur d'avoir un résultat), et l'écriture
réutilise la ligne de la même clé — pas de doublon qui gonflerait la table.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, select

from app.domain.geo import PROFIL_ITINERAIRE, ItineraireCache, OsmVoie, ParametresVoyage
from app.infrastructure.repositories.base import BaseRepository


class ItineraireCacheRepository(BaseRepository[ItineraireCache]):
    model = ItineraireCache

    def get_valide(self, cle: str, maintenant: datetime) -> ItineraireCache | None:
        """Ligne du trajet si elle existe **et** n'a pas expiré, sinon ``None``."""
        stmt = select(ItineraireCache).where(
            ItineraireCache.cle == cle,
            ItineraireCache.profil == PROFIL_ITINERAIRE,
            ItineraireCache.expire_le > maintenant,
        )
        return self.session.scalars(stmt).first()

    def enregistrer(self, ligne: ItineraireCache) -> ItineraireCache:
        """Enregistre (ou remplace) le résultat d'un trajet — un trajet, une ligne."""
        existante = self.session.scalars(
            select(ItineraireCache).where(
                ItineraireCache.cle == ligne.cle,
                ItineraireCache.profil == ligne.profil,
            )
        ).first()
        if existante is not None:
            self.session.delete(existante)
            self.session.flush()
        self.session.add(ligne)
        self.session.flush()
        return ligne

    def purger_expirees(self, maintenant: datetime) -> int:
        """Supprime les lignes expirées (maintenance, pas une lecture)."""
        resultat = self.session.execute(
            delete(ItineraireCache).where(ItineraireCache.expire_le <= maintenant)
        )
        return int(resultat.rowcount or 0)


class OsmVoieRepository(BaseRepository[OsmVoie]):
    """Tags OSM des voies, une ligne par ``osm_id`` (cache à expiration)."""

    model = OsmVoie

    def lire_valides(
        self, osm_ids: list[int], maintenant: datetime
    ) -> dict[int, dict[str, str]]:
        """Tags encore valides pour ces voies — les autres sont à redemander."""
        if not osm_ids:
            return {}
        stmt = select(OsmVoie).where(
            OsmVoie.osm_id.in_(osm_ids), OsmVoie.expire_le > maintenant
        )
        return {
            ligne.osm_id: {str(cle): str(valeur) for cle, valeur in (ligne.tags or {}).items()}
            for ligne in self.session.scalars(stmt)
        }

    def enregistrer_tags(
        self,
        tags_par_voie: dict[int, dict[str, str]],
        *,
        expire_le: datetime,
        maintenant: datetime,
    ) -> None:
        """Met à jour (ou crée) les lignes des voies reçues, en une passe.

        Une voie sans tags n'est pas enregistrée : on la redemandera plutôt que
        de mémoriser un « vide » qui deviendrait une vérité.
        """
        if not tags_par_voie:
            return
        existantes = {
            ligne.osm_id: ligne
            for ligne in self.session.scalars(
                select(OsmVoie).where(OsmVoie.osm_id.in_(list(tags_par_voie)))
            )
        }
        for osm_id, tags in tags_par_voie.items():
            if not tags:
                continue
            ligne = existantes.get(osm_id)
            if ligne is None:
                self.session.add(
                    OsmVoie(osm_id=osm_id, tags=tags, expire_le=expire_le)
                )
            else:
                ligne.tags = tags
                ligne.expire_le = expire_le
        self.session.flush()

    def purger_expirees(self, maintenant: datetime) -> int:
        resultat = self.session.execute(
            delete(OsmVoie).where(OsmVoie.expire_le <= maintenant)
        )
        return int(resultat.rowcount or 0)


class ParametresVoyageRepository(BaseRepository[ParametresVoyage]):
    model = ParametresVoyage

    def lire_unique(self) -> ParametresVoyage:
        """La ligne unique, recréée si absente (base restaurée partiellement).

        La migration 0025 insère la ligne : ce repli n'existe que pour un
        environnement réinitialisé sans les données.
        """
        ligne = self.session.scalars(select(ParametresVoyage)).first()
        if ligne is None:
            ligne = ParametresVoyage()
            self.session.add(ligne)
            self.session.flush()
        return ligne


__all__ = ["ItineraireCacheRepository", "ParametresVoyageRepository"]
