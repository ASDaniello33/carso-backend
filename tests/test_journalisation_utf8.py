"""Journalisation : aucune ligne perdue sur un caractère hors page de code.

Constaté en run réel sous Windows : la console en `cp1252` fait lever
`UnicodeEncodeError` au handler dès qu'une ligne contient `→`. Python remplace
alors la ligne par une trace « --- Logging error --- » : le diagnostic disparaît
au moment où il sert.

`flux_journalisation` prépare la sortie (UTF-8, `errors="backslashreplace"`).
"""

from __future__ import annotations

import io
import logging

from app.core.logging import ERREURS_SUR, configure_logging, flux_journalisation


def test_un_flux_cp1252_ne_leve_plus_sur_une_fleche() -> None:
    tampon = io.BytesIO()
    flux = io.TextIOWrapper(tampon, encoding="cp1252", errors="strict")

    sur = flux_journalisation(flux)
    sur.write("agent → outil\n")
    sur.flush()

    assert b"agent" in tampon.getvalue()
    assert ERREURS_SUR in str(getattr(sur, "errors", ""))


def test_un_flux_deja_utf8_et_permissif_est_rendu_tel_quel() -> None:
    flux = io.TextIOWrapper(io.BytesIO(), encoding="utf-8", errors=ERREURS_SUR)
    assert flux_journalisation(flux) is flux


def test_un_objet_sans_encodage_ni_tampon_reste_utilisable() -> None:
    class FluxMuet:
        def write(self, texte: str) -> int:
            return len(texte)

        def flush(self) -> None:  # pragma: no cover - jamais appelé ici
            return None

    flux = FluxMuet()
    assert flux_journalisation(flux) is flux


def test_configure_logging_journalise_un_caractere_non_latin(capsys) -> None:
    configure_logging(logging.INFO)
    try:
        logging.getLogger("test.utf8").info("étape → suivante")
        sortie = capsys.readouterr().out
        assert "étape" in sortie
        assert "suivante" in sortie
    finally:
        # Rend la racine aux autres tests (handler de pytest).
        logging.getLogger().handlers = []
