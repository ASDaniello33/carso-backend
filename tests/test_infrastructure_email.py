"""Transport email (`app/infrastructure/email.py`) : le message réellement composé.

Ce que ces tests verrouillent, ce sont les **en-têtes et la structure MIME** d'un
envoi collectif avec pièces jointes — la partie que l'espion des tests d'agent ne
voit pas. Aucun réseau : le SMTP est remplacé par un espion, le mode console
(développement sans ``SMTP_HOST``) est exercé tel quel, et la production sans
SMTP doit refuser explicitement.
"""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from typing import Any

import pytest

from app.core.config import Settings
from app.core.errors import ValidationError
from app.infrastructure.email import EnvoyeurEmail, PieceJointeEmail


def _reglages(**champs: Any) -> Settings:
    """Réglages de test : aucun `.env` lu, développement par défaut."""
    return Settings(_env_file=None, **champs)  # type: ignore[call-arg] - isolement des .env locaux


class _SMTPEspion:
    """SMTP factice : aucun réseau, les messages composés sont conservés."""

    messages: list[EmailMessage] = []
    connexions: list[tuple[str, int]] = []
    authentifications: list[tuple[str, str]] = []

    def __init__(self, hote: str, port: int, timeout: float | None = None) -> None:
        del timeout
        type(self).connexions.append((hote, port))
        self._demarre_tls = False

    def __enter__(self) -> _SMTPEspion:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def starttls(self) -> None:
        self._demarre_tls = True

    def login(self, utilisateur: str, mot_de_passe: str) -> None:
        type(self).authentifications.append((utilisateur, mot_de_passe))

    def send_message(self, message: EmailMessage) -> None:
        type(self).messages.append(message)


@pytest.fixture
def smtp(monkeypatch: pytest.MonkeyPatch) -> type[_SMTPEspion]:
    """Remplace `smtplib.SMTP` : le test observe le message, sans socket."""
    _SMTPEspion.messages = []
    _SMTPEspion.connexions = []
    _SMTPEspion.authentifications = []
    monkeypatch.setattr(smtplib, "SMTP", _SMTPEspion)
    return _SMTPEspion


def test_message_collectif_avec_html_et_pieces_jointes(smtp: type[_SMTPEspion]) -> None:
    """Un message, plusieurs destinataires, variante HTML et deux pièces."""
    envoyeur = EnvoyeurEmail(
        _reglages(smtp_host="smtp.carso.test", smtp_from="no-reply@carso.test")
    )
    envoyeur.envoyer(
        destinataires=["a@carso.test", "b@carso.test"],
        sujet="Réunion de préparation",
        corps="Bonjour, la réunion est fixée à lundi.",
        corps_html="<p>Bonjour, la réunion est fixée à <strong>lundi</strong>.</p>",
        pieces_jointes=[
            PieceJointeEmail("offre.pdf", b"%PDF-1.4", "application/pdf"),
            PieceJointeEmail("logo.png", b"\x89PNG", "image/png"),
        ],
    )
    assert smtp.connexions == [("smtp.carso.test", 587)]
    assert len(smtp.messages) == 1
    message = smtp.messages[0]

    assert message["To"] == "a@carso.test, b@carso.test"
    assert message["From"] == "no-reply@carso.test"
    assert message["Subject"] == "Réunion de préparation"
    types = [partie.get_content_type() for partie in message.walk()]
    assert "multipart/mixed" in types
    assert "multipart/alternative" in types
    assert "text/plain" in types
    assert "text/html" in types
    assert "application/pdf" in types
    assert "image/png" in types
    noms = [partie.get_filename() for partie in message.walk() if partie.get_filename()]
    assert noms == ["offre.pdf", "logo.png"]
    # Le corps texte reste la version de repli, jamais perdu.
    corps_textes = [
        partie.get_content()
        for partie in message.walk()
        if partie.get_content_type() == "text/plain" and not partie.get_filename()
    ]
    assert any("la réunion est fixée à lundi." in texte for texte in corps_textes)


def test_message_simple_sans_piece_jointe_ni_html(smtp: type[_SMTPEspion]) -> None:
    """Le cas courant reste un message simple : aucune partie inutile."""
    envoyeur = EnvoyeurEmail(_reglages(smtp_host="smtp.carso.test"))
    envoyeur.envoyer(destinataires=["seul@carso.test"], sujet="Bonjour", corps="Texte brut")
    assert len(smtp.messages) == 1
    message = smtp.messages[0]
    assert message["To"] == "seul@carso.test"
    assert message.get_content_type() == "text/plain"


def test_authentification_smtp_avec_secrets_de_configuration(smtp: type[_SMTPEspion]) -> None:
    """Utilisateur et mot de passe viennent des réglages, jamais d'un littéral."""
    envoyeur = EnvoyeurEmail(
        _reglages(
            smtp_host="smtp.carso.test",
            smtp_utilisateur="compte",
            smtp_mot_de_passe="secret-smtp",
        )
    )
    envoyeur.envoyer(destinataires=["a@carso.test"], sujet="Bonjour", corps="Texte")
    assert smtp.authentifications == [("compte", "secret-smtp")]


def test_adresses_normalisees_et_chaine_unique_acceptee() -> None:
    """Une chaîne seule est acceptée — jamais itérée caractère par caractère."""
    assert EnvoyeurEmail._adresses("  seul@carso.test ") == ["seul@carso.test"]
    assert EnvoyeurEmail._adresses(["a@x.test", "", "  "]) == ["a@x.test"]
    with pytest.raises(ValidationError):
        EnvoyeurEmail._adresses([])


def test_mode_console_en_developpement_journalise(caplog: pytest.LogCaptureFixture) -> None:
    """Sans SMTP en développement : le message part sur les journaux, avec ses annexes."""
    envoyeur = EnvoyeurEmail(_reglages(environment="development", smtp_host=None))
    with caplog.at_level(logging.WARNING, logger="app.infrastructure.email"):
        envoyeur.envoyer(
            destinataires=["a@carso.test", "b@carso.test"],
            sujet="Convocation",
            corps="Bonjour à tous.",
            corps_html="<p>Bonjour à tous.</p>",
            pieces_jointes=[PieceJointeEmail("cv.pdf", b"x" * 12, "application/pdf")],
        )
    journal = caplog.text
    assert "a@carso.test, b@carso.test" in journal
    assert "cv.pdf" in journal
    assert "12 octets" in journal
    assert "variante HTML fournie" in journal


def test_production_sans_smtp_refuse() -> None:
    """En production, l'absence de SMTP est un refus explicite — jamais un silence."""
    envoyeur = EnvoyeurEmail(_reglages(environment="production", smtp_host=None))
    with pytest.raises(ValidationError) as erreur:
        envoyeur.envoyer(destinataires=["a@carso.test"], sujet="Bonjour", corps="Texte")
    assert "SMTP_HOST" in str(erreur.value)
