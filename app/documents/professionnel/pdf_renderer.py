"""Rendu PDF avec Chromium (Microsoft Edge, Google Chrome ou le Chromium de Playwright).

- en-tête (titre) et pied de page « n / total » sur les pages de contenu ;
- couverture rendue à part (pleine page, sans numéro) puis fusionnée avec pypdf ;
- sommaire—1re passe pour trouver la page de chaque titre, 2e passe avec les numéros.

Adaptation CARSO—**toutes les attentes ont une borne de temps** (lancement du
navigateur, navigation, impression). Le moteur d'origine n'en posait aucune—un
Chromium qui ne répond pas figeait l'appel de tool, donc la boucle d'événements
du backend. Métadonnées du PDF signées CARSO.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import datetime
import functools
import html
import io
import itertools
import re
import tempfile
import unicodedata
from pathlib import Path
from typing import Any

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright
from pypdf import PdfReader, PdfWriter

from .html_builder import DocumentPrepare, EntreeSommaire, construire_html, remplir_numeros
from .themes import Theme

CANAUX = ("msedge", "chrome", None)  # None = Chromium installé par « playwright install »
MARGES_CORPS = {"top": "24mm", "bottom": "20mm", "left": "18mm", "right": "18mm"}
MARGES_NULLES = {"top": "0", "bottom": "0", "left": "0", "right": "0"}
POLICE_GABARIT = "'Segoe UI', Arial, sans-serif"

#: Bornes de temps explicites. Le module d'origine n'en posait aucune : un
#: navigateur qui ne répond pas figeait l'appel de tool — et donc la boucle
#: d'événements du backend (voir docs/validation/INCREMENT-54, où un tool de
#: 16 s figeait déjà l'interface). Un dépassement devient ici une erreur claire.
TIMEOUT_LANCEMENT_MS = 20_000
TIMEOUT_NAVIGATION_MS = 30_000
TIMEOUT_RENDU_MS = 60_000


#: Motifs de chemins absolus (Windows et POSIX) à masquer dans les messages
#: d'erreur renvoyés à l'agent : la cause reste lisible (« Edge introuvable »),
#: l'arborescence du serveur ne fuit pas (AGENTS.md §9).
_RE_CHEMIN = re.compile(r"(?:[A-Za-z]:[\\/][^\s'\";)]*|/(?:usr|home|opt|var|tmp|mnt)/[^\s'\";)]*)")


def _sans_chemin(texte: str) -> str:
    """Remplace les chemins absolus par ``<chemin>`` dans un message d'erreur."""
    return _RE_CHEMIN.sub("<chemin>", texte)


def _href_carso() -> str:
    """Consigne d'installation du navigateur (cause réelle, jamais un renvoi à uv).

    Le serveur passe d'abord : un Linux headless n'a ni Edge ni Chrome, et le
    Chromium téléchargé sans ``--with-deps`` démarre mal (bibliothèques
    partagées absentes). Le poste de développement, ensuite : Edge/Chrome y
    sont déjà installés et suffisent.
    """
    return (
        "Dans l'environnement du backend, executer `python -m playwright install "
        "--with-deps chromium` (serveur Linux/conteneur : --with-deps installe "
        "aussi les bibliotheques systeme du navigateur) ; sur un poste de "
        "developpement, Microsoft Edge ou Google Chrome installe suffit."
    )


class NavigateurIndisponible(RuntimeError):
    """Aucun navigateur Chromium utilisable n'a été trouvé."""


def _executer(fonction, *args, **kwargs):
    """L'API sync de Playwright refuse de tourner dans une boucle asyncio.

    Dans ce cas (appel depuis un handler async), le rendu passe par un thread.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return fonction(*args, **kwargs)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(fonction, *args, **kwargs).result()


def _lancer(playwright):
    erreurs = []
    for canal in CANAUX:
        try:
            if canal:
                return playwright.chromium.launch(channel=canal, timeout=TIMEOUT_LANCEMENT_MS)
            return playwright.chromium.launch(timeout=TIMEOUT_LANCEMENT_MS)
        except PlaywrightError as exc:
            motif = _sans_chemin(str(exc).strip().splitlines()[0])
            erreurs.append(f"{canal or 'chromium'}: {motif}")
    raise NavigateurIndisponible(
        "Aucun navigateur Chromium trouve (Edge, Chrome ou Chromium de Playwright). "
        f"{_href_carso()} " + " | ".join(erreurs)
    )


def _tester_navigateur() -> tuple[bool, str]:
    try:
        with sync_playwright() as p:
            navigateur = _lancer(p)
            description = f"{navigateur.browser_type.name} {navigateur.version}"
            navigateur.close()
        return True, description
    except Exception as exc:  # noqa: BLE001 - on veut un diagnostic, pas un crash
        return False, _sans_chemin(str(exc))


@functools.lru_cache(maxsize=1)
def navigateur_disponible() -> tuple[bool, str]:
    """(True, 'chromium 1xx') si un navigateur est utilisable, sinon (False, raison)."""
    return _executer(_tester_navigateur)


def _gabarit_entete(titre: str) -> str:
    return (
        f'<div style="width:100%;font-size:8pt;color:#6B7280;font-family:{POLICE_GABARIT};'
        f'padding:0 18mm;">{html.escape(titre)}</div>'
    )


def _gabarit_pied() -> str:
    return (
        f'<div style="width:100%;font-size:8pt;color:#6B7280;font-family:{POLICE_GABARIT};'
        'padding:0 18mm;text-align:right;"><span class="pageNumber"></span> / '
        '<span class="totalPages"></span></div>'
    )


def normaliser(texte: str) -> str:
    """Minuscules, sans accents ni espaces : robuste aux différences d'extraction de texte PDF."""
    texte = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]", "", texte.lower())


def calculer_numeros(pdf: bytes, entrees: list[EntreeSommaire]) -> dict[str, int]:
    """Trouve la page (1 = première page de contenu) où apparaît chaque titre du sommaire."""
    if not entrees:
        return {}
    textes = [normaliser(page.extract_text() or "") for page in PdfReader(io.BytesIO(pdf)).pages]
    # Le sommaire lui-même contient tous les titres : on commence après la page
    # où figure la dernière entrée du sommaire.
    derniere = normaliser(entrees[-1].titre)
    debut = next((i for i, t in enumerate(textes) if derniere and derniere in t), -1) + 1
    numeros: dict[str, int] = {}
    curseur = debut
    for entree in entrees:
        cle = normaliser(entree.titre)
        for i in range(curseur, len(textes)):
            if cle and cle in textes[i]:
                numeros[entree.id] = i + 1
                curseur = i
                break
    return numeros


def _rendre(
    doc: DocumentPrepare,
    chemin: Path,
    theme: str | Theme,
    titre: str,
    auteur: str,
    orientation: str,
    css_supplementaire: str,
    base_dir: Path | None,
) -> int:
    paysage = orientation == "paysage"
    options_html: dict[str, Any] = dict(
        theme=theme,
        titre=titre,
        auteur=auteur,
        orientation=orientation,
        css_supplementaire=css_supplementaire,
        base_dir=base_dir,
    )
    corps_pdf: bytes | None = None
    couverture_pdf: bytes | None = None

    with tempfile.TemporaryDirectory(prefix="carso_gendoc_") as dossier_tmp:
        compteur = itertools.count()
        with sync_playwright() as p:
            navigateur = _lancer(p)
            try:
                page = navigateur.new_page()
                # Bornes posées sur la PAGE : ``Browser`` ne les porte pas (API
                # Playwright), la page les hérite pour ses propres appels.
                page.set_default_timeout(TIMEOUT_RENDU_MS)
                page.set_default_navigation_timeout(TIMEOUT_NAVIGATION_MS)

                def imprimer(contenu_html: str, marges: dict, entete: bool) -> bytes:
                    fichier = Path(dossier_tmp) / f"passe{next(compteur)}.html"
                    fichier.write_text(contenu_html, encoding="utf-8")
                    page.goto(
                        fichier.as_uri(),
                        wait_until="load",
                        timeout=TIMEOUT_NAVIGATION_MS,
                    )
                    page.evaluate("document.fonts.ready.then(() => true)")
                    # ``page.pdf()`` n'accepte pas de ``timeout`` explicite dans
                    # cette version de Playwright : la borne vient du timeout par
                    # défaut posé sur la page (``TIMEOUT_RENDU_MS``).
                    options: dict[str, Any] = dict(
                        format="A4",
                        landscape=paysage,
                        print_background=True,
                        margin=marges,
                    )
                    if entete:
                        options.update(
                            display_header_footer=True,
                            header_template=_gabarit_entete(titre),
                            footer_template=_gabarit_pied(),
                        )
                    return page.pdf(**options)

                if doc.corps.strip():
                    corps_pdf = imprimer(
                        construire_html(doc.corps, **options_html), MARGES_CORPS, True
                    )
                    if doc.sommaire:
                        numeros = calculer_numeros(corps_pdf, doc.sommaire)
                        corps2 = remplir_numeros(doc.corps, numeros)
                        corps_pdf = imprimer(
                            construire_html(corps2, **options_html), MARGES_CORPS, True
                        )
                if doc.couverture:
                    couverture_pdf = imprimer(
                        construire_html(doc.couverture, couverture=True, **options_html),
                        MARGES_NULLES,
                        False,
                    )
            finally:
                navigateur.close()

    writer = PdfWriter()
    if couverture_pdf:
        writer.add_page(PdfReader(io.BytesIO(couverture_pdf)).pages[0])
    if corps_pdf:
        for page_pdf in PdfReader(io.BytesIO(corps_pdf)).pages:
            writer.add_page(page_pdf)
    if len(writer.pages) == 0:
        raise ValueError("Document vide : aucun contenu à rendre.")
    date = datetime.datetime.now().strftime("D:%Y%m%d%H%M%S")
    writer.add_metadata({
        "/Title": titre, "/Author": auteur or "", "/Creator": "CARSO",
        "/Producer": "CARSO", "/CreationDate": date, "/ModDate": date,
    })
    chemin = Path(chemin)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    with open(chemin, "wb") as f:
        writer.write(f)
    return len(writer.pages)


def rendre_pdf(
    doc: DocumentPrepare,
    chemin: str | Path,
    *,
    theme: str | Theme = "corporate",
    titre: str = "",
    auteur: str = "",
    orientation: str = "portrait",
    css_supplementaire: str = "",
    base_dir: str | Path | None = None,
) -> int:
    """Génère le PDF et retourne le nombre de pages."""
    return _executer(
        _rendre, doc, Path(chemin), theme, titre, auteur, orientation, css_supplementaire,
        Path(base_dir) if base_dir else None,
    )


