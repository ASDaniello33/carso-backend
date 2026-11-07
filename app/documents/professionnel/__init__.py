"""Rendu professionnel d'un document depuis du HTML (outil ``generer_document_professionnel``).

Ce paquet est la version CARSO du module autonome ``generation-document``—l'agent
n'écrit que le **corps** du document, en HTML sémantique avec les classes du thème
(``cover``, ``toc``, ``lead``, ``table.kpi``, ``table.data``, ``callout``…), et le
moteur se charge du design.

- **PDF**—rendu par un vrai navigateur Chromium (Microsoft Edge, Google Chrome ou
  le Chromium de Playwright)—couverture pleine page, en-tête, pied « 2 / 5 »,
  sommaire dont les numéros de page sont **calculés**, polices intégrées.
- **DOCX**—rendu natif (``python-docx``)—vrais styles Word, couverture colorée,
  tableaux et KPI colorés, encadrés, sommaire Word, numéros de page.
- **PPTX**—deck natif 16:9 (``python-pptx``, format ``pptx``)—vraies formes,
  tableaux, images et notes présentateur, thèmes CARSO appliqués ; le corps
  HTML suit le contrat ``presentation.py`` (``<section class="slide">``).

Ce que CARSO change par rapport au module d'origine :

- **aucun chemin disque ne sort d'ici**—le rendu se fait dans un dossier
  temporaire interne, effacé aussitôt ; l'appelant reçoit des **octets** et les
  dépose via ``DocumentService`` (AGENTS.md §2.5 et §7) ;
- **aucune exception avalée**—un échec lève ``GenerationError`` avec sa cause
  (navigateur absent, document vide) au lieu de retourner un dictionnaire
  ``succes: false`` que l'agent pourrait ignorer ;
- l'aperçu PNG du module n'est pas repris—CARSO affiche le rendu réel par
  ``render_document`` + ``afficher_apercu_document``, ce qui évite d'exposer un
  chemin de fichier à l'agent.

**Exports paresseux (INC-123).** Le rendu PDF importe ``playwright`` au niveau
module : quand ce paquet ré-exportait tout à l'import, demander un simple
graphique (``professionnel.graphiques``) chargeait Playwright — inutile pour un
DOCX ou un XLSX, et fatal sur un déploiement sans l'extra ``documents_pro``. Les
noms publics sont donc résolus à l'accès (PEP 562) : importer
``app.documents.professionnel.graphiques`` ne charge plus que ce qu'il faut.

Voir ``docs/adr/0011-outil-generation-document-professionnel.md``.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "THEMES",
    "TYPES_GRAPHIQUES",
    "RenduProfessionnel",
    "Theme",
    "get_theme",
    "lire_presentation",
    "render_chart",
    "rendre_professionnel",
    "verifier",
    "verifier_presentation",
]

#: Nom public → (module, attribut). Résolu au premier accès, puis mis en cache.
_EXPORTS: dict[str, tuple[str, str]] = {
    "THEMES": (".themes", "THEMES"),
    "Theme": (".themes", "Theme"),
    "get_theme": (".themes", "get_theme"),
    "TYPES_GRAPHIQUES": (".graphiques", "TYPES_GRAPHIQUES"),
    "render_chart": (".graphiques", "render_chart"),
    "lire_presentation": (".presentation", "lire_presentation"),
    "verifier_presentation": (".presentation", "verifier_presentation"),
    "RenduProfessionnel": (".rendu", "RenduProfessionnel"),
    "rendre_professionnel": (".rendu", "rendre_professionnel"),
    "verifier": (".verification", "verifier"),
}


def __getattr__(nom: str) -> Any:
    """Résout un export public à la demande (jamais un import de Playwright gratuit)."""
    cible = _EXPORTS.get(nom)
    if cible is None:
        raise AttributeError(f"module {__name__!r} n'a pas d'attribut {nom!r}")
    from importlib import import_module

    module = import_module(cible[0], __name__)
    valeur = getattr(module, cible[1])
    globals()[nom] = valeur
    return valeur


def __dir__() -> list[str]:
    """Liste les exports réels (``dir()`` d'un module paresseux doit rester exact)."""
    return sorted(__all__)
