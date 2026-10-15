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

Voir ``docs/adr/0011-outil-generation-document-professionnel.md``.
"""

from __future__ import annotations

from .rendu import RenduProfessionnel, rendre_professionnel
from .themes import THEMES, Theme, get_theme
from .verification import verifier

__all__ = [
    "THEMES",
    "RenduProfessionnel",
    "Theme",
    "get_theme",
    "rendre_professionnel",
    "verifier",
]
