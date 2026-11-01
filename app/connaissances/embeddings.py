"""Modèle d'embedding de la base de connaissance et signature anti-mélange.

Le modèle est une décision de **déploiement**, surchargeable à chaud depuis la
page Paramètres (priorité à la configuration administrée, repli sur
``EMBEDDING_*`` du ``.env``). Rien n'est deviné :

- les dimensions sont **mesurées** par un appel réel (``sonder``), jamais
  déduites du nom du modèle ;
- une ``signature`` (``provider:modele:dimensions``) accompagne chaque vecteur.
  Interroger la collection avec une autre signature est refusé : comparer des
  vecteurs issus de deux modèles produirait des résultats faux en silence.

Seuls les endpoints **compatibles OpenAI** sont branchés. Un provider inconnu
est refusé explicitement (``EmbeddingIndisponibleError``) plutôt
qu'approximé — ajouter Google GenAI demandera son propre adaptateur.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Protocol

from app.core.config import Settings, get_settings
from app.core.errors import CarsoError

#: Providers branchés. Un nom hors de cet ensemble est refusé avec la liste.
PROVIDERS_EMBEDDING: frozenset[str] = frozenset({"openai", "openai_compatible"})

#: Taille d'un lot d'appels (l'API OpenAI accepte bien davantage ; ce plafond
#: garde un appel borné en mémoire et en temps).
TAILLE_LOT = 64


class EmbeddingIndisponibleError(CarsoError):
    """Le modèle d'embedding n'est pas utilisable (config, paquet, réseau)."""


@dataclass(frozen=True, slots=True)
class ConfigurationEmbeddingEffective:
    """Modèle d'embedding effectif, avec sa provenance (``administration`` ou ``environnement``)."""

    provider: str
    model: str
    base_url: str | None
    dimensions: int
    api_key: str | None
    source: str

    @property
    def signature(self) -> str:
        """Empreinte stable du couple modèle + dimensions (contrôle anti-mélange)."""
        return f"{self.provider}:{self.model}:{self.dimensions}"

    def resume(self) -> dict[str, Any]:
        """Vue API : aucun secret, la seule existence d'une clé est signalée."""
        return {
            "provider": self.provider,
            "model": self.model,
            "base_url": self.base_url,
            "dimensions": self.dimensions,
            "signature": self.signature,
            "source": self.source,
            "cle_disponible": bool(self.api_key),
        }


@dataclass(frozen=True, slots=True)
class SondageEmbedding:
    """Résultat d'un appel réel de mesure du modèle."""

    dimensions: int
    signature: str
    latence_ms: int
    modele: str


class Embedder(Protocol):
    """Contrat minimal : vectoriser des textes, connaître sa signature."""

    @property
    def configuration(self) -> ConfigurationEmbeddingEffective: ...

    def vectoriser(self, textes: list[str]) -> list[list[float]]: ...


class OpenAIEmbedder:
    """Embedder d'un endpoint compatible OpenAI (OpenAI, Azure, Ollama, LM Studio…)."""

    def __init__(self, configuration: ConfigurationEmbeddingEffective) -> None:
        self._configuration = configuration

    @property
    def configuration(self) -> ConfigurationEmbeddingEffective:
        return self._configuration

    def _client(self) -> Any:
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - dépend du venv
            msg = (
                "Embeddings indisponibles : le paquet 'openai' n'est pas installé "
                "(pip install -e '.[connaissances]')"
            )
            raise EmbeddingIndisponibleError(msg) from exc
        cfg = self._configuration
        if not cfg.api_key:
            msg = (
                "Aucune clé d'API d'embedding : la renseigner dans Paramètres → "
                "Base de connaissance (ou EMBEDDING_API_KEY pour le déploiement)"
            )
            raise EmbeddingIndisponibleError(msg)
        return OpenAI(
            api_key=cfg.api_key,
            base_url=cfg.base_url or None,
            timeout=90.0,
            max_retries=1,
        )

    def vectoriser(self, textes: list[str]) -> list[list[float]]:
        """Vectorise des textes, par lots, en conservant l'ordre d'entrée.

        Raises:
            EmbeddingIndisponibleError: clé absente, modèle refusé, panne réseau.
        """
        if not textes:
            return []
        client = self._client()
        vecteurs: list[list[float]] = []
        for debut in range(0, len(textes), TAILLE_LOT):
            lot = textes[debut : debut + TAILLE_LOT]
            try:
                reponse = client.embeddings.create(
                    model=self._configuration.model, input=lot
                )
            except Exception as exc:  # noqa: BLE001 — erreur provider traduite, jamais avalée
                msg = (
                    "Le modèle d'embedding a refusé la requête : "
                    f"{type(exc).__name__}. Vérifier le modèle, la clé et l'URL "
                    "dans Paramètres → Base de connaissance."
                )
                raise EmbeddingIndisponibleError(
                    msg, details={"modele": self._configuration.model}
                ) from exc
            donnees = sorted(reponse.data, key=lambda item: item.index)
            for item in donnees:
                vecteur = list(getattr(item, "embedding", []) or [])
                if not vecteur:
                    raise EmbeddingIndisponibleError(
                        "Le provider d'embedding a renvoyé un vecteur vide",
                        details={"modele": self._configuration.model},
                    )
                vecteurs.append(vecteur)
        return vecteurs


def valider_configuration(
    *, provider: str, model: str, base_url: str | None
) -> tuple[str, str, str | None]:
    """Normalise et vérifie une configuration **sans** appel réseau.

    Raises:
        EmbeddingIndisponibleError: provider inconnu, modèle vide, URL absente
            pour un endpoint personnalisé.
    """
    provider_n = (provider or "").strip().lower()
    model_n = (model or "").strip()
    base_url_n = (base_url or "").strip() or None
    if provider_n not in PROVIDERS_EMBEDDING:
        msg = (
            f"Provider d'embedding inconnu : {provider_n!r} "
            f"(acceptés : {', '.join(sorted(PROVIDERS_EMBEDDING))})"
        )
        raise EmbeddingIndisponibleError(msg, details={"provider": provider_n})
    if not model_n:
        raise EmbeddingIndisponibleError(
            "Modèle d'embedding manquant : préciser l'identifiant exact du modèle",
            details={"champ": "model"},
        )
    if provider_n == "openai_compatible" and not base_url_n:
        raise EmbeddingIndisponibleError(
            "Un endpoint compatible OpenAI exige une URL de base "
            "(ex. http://localhost:11434/v1 pour Ollama)",
            details={"champ": "base_url"},
        )
    return provider_n, model_n, base_url_n


def construire_embedder(configuration: ConfigurationEmbeddingEffective) -> Embedder:
    """Fabrique l'embedder d'une configuration validée."""
    if configuration.provider not in PROVIDERS_EMBEDDING:
        raise EmbeddingIndisponibleError(
            f"Provider d'embedding non branché : {configuration.provider!r}",
            details={"acceptes": sorted(PROVIDERS_EMBEDDING)},
        )
    return OpenAIEmbedder(configuration)


def depuis_settings(settings: Settings | None = None) -> ConfigurationEmbeddingEffective:
    """Configuration de repli (``.env``), utilisée si l'administration n'a rien réglé."""
    cfg = settings or get_settings()
    return ConfigurationEmbeddingEffective(
        provider=(cfg.embedding_provider or "").strip().lower(),
        model=(cfg.embedding_model or "").strip(),
        base_url=(cfg.embedding_base_url or "").strip() or None,
        dimensions=int(cfg.embedding_dimensions or 0),
        api_key=(cfg.embedding_api_key.get_secret_value() if cfg.embedding_api_key else None),
        source="environnement",
    )


def sonder(configuration: ConfigurationEmbeddingEffective) -> SondageEmbedding:
    """Exécute un **vrai** appel d'embedding et mesure la dimension obtenue.

    C'est ce qui rend la signature fiable : la dimension enregistrée est celle
    du vecteur réellement produit, pas celle annoncée par une documentation.

    Raises:
        EmbeddingIndisponibleError: configuration inutilisable ou appel refusé.
    """
    embedder = construire_embedder(configuration)
    debut = time.monotonic()
    vecteurs = embedder.vectoriser(["mesure de la dimension du modèle d'embedding CARSO"])
    latence = int((time.monotonic() - debut) * 1000)
    if len(vecteurs) != 1 or not vecteurs[0]:
        raise EmbeddingIndisponibleError(
            "Le provider d'embedding n'a pas produit de vecteur mesurable"
        )
    dimensions = len(vecteurs[0])
    return SondageEmbedding(
        dimensions=dimensions,
        signature=f"{configuration.provider}:{configuration.model}:{dimensions}",
        latence_ms=latence,
        modele=configuration.model,
    )


__all__ = [
    "PROVIDERS_EMBEDDING",
    "TAILLE_LOT",
    "ConfigurationEmbeddingEffective",
    "Embedder",
    "EmbeddingIndisponibleError",
    "OpenAIEmbedder",
    "SondageEmbedding",
    "construire_embedder",
    "depuis_settings",
    "sonder",
    "valider_configuration",
]
