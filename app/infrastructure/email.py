"""Envoi d'email (SMTP)—les secrets vivent exclusivement dans ``.env``.

Utilisé par la réinitialisation de mot de passe (« mot de passe oublié ») et par
l'agent RH (``envoyer_email``). Règles :

- ``SMTP_HOST`` absent—en **développement** l'email part sur la console des
  journaux (équivalent du backend console de Django—le flux reste testable
  sans serveur SMTP) ; en **production** l'envoi refuse explicitement, jamais
  un code jeté silencieusement.
- ``SMTP_UTILISATEUR``/``SMTP_MOT_DE_PASSE``—secrets lus depuis la
  configuration (``.env``), jamais écrits dans le code, les logs ou les
  messages inter-agents (AGENTS.md §9).
- Un message part à **un ou plusieurs** destinataires, en texte simple avec une
  variante HTML facultative, et peut porter des **pièces jointes**. Une pièce
  jointe est un couple (nom, octets, type MIME)—ce module ne lit jamais un
  chemin libre—le contenu lui est remis déjà chargé par l'appelant autorisé.
"""

from __future__ import annotations

import logging
import smtplib
from collections.abc import Sequence
from dataclasses import dataclass
from email.message import EmailMessage

from app.core.config import Settings
from app.core.errors import ValidationError

logger = logging.getLogger(__name__)

_EXPEDITEUR_DEFAUT = "no-reply@carso.local"
_TYPE_DEFAUT = "application/octet-stream"


@dataclass(frozen=True, slots=True)
class PieceJointeEmail:
    """Pièce jointe prête à partir : contenu en mémoire, jamais un chemin.

    Le contenu a été lu par la couche de stockage (chemin logique contrôlé) :
    ce module ne touche pas au disque et ne peut donc pas être détourné vers un
    fichier arbitraire.
    """

    nom: str
    contenu: bytes
    mime_type: str = _TYPE_DEFAUT

    @property
    def taille(self) -> int:
        return len(self.contenu)


class EnvoyeurEmail:
    """Transport email contrôlé, construit depuis ``Settings``."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def envoyer(
        self,
        *,
        destinataires: Sequence[str] | str,
        sujet: str,
        corps: str,
        corps_html: str | None = None,
        pieces_jointes: Sequence[PieceJointeEmail] = (),
    ) -> None:
        """Envoie un message à un ou plusieurs destinataires.

        Les adresses figurent toutes en clair (``To``) : c'est l'envoi collectif
        visible — un envoi en copie cachée relèverait d'une règle métier à
        confirmer, jamais d'un choix silencieux du code.

        Raises:
            ValidationError: échec SMTP, ou production sans ``SMTP_HOST``.
        """
        adresses = self._adresses(destinataires)
        if not self._settings.smtp_host:
            self._envoyer_console(adresses, sujet, corps, corps_html, pieces_jointes)
            return
        message = self._composer(adresses, sujet, corps, corps_html, pieces_jointes)
        try:
            with smtplib.SMTP(
                self._settings.smtp_host, self._settings.smtp_port, timeout=15
            ) as smtp:
                if self._settings.smtp_tls:
                    smtp.starttls()
                utilisateur = self._settings.smtp_utilisateur
                mot_de_passe = self._settings.smtp_mot_de_passe
                if utilisateur and mot_de_passe:
                    smtp.login(
                        utilisateur.get_secret_value(), mot_de_passe.get_secret_value()
                    )
                smtp.send_message(message)
        except (smtplib.SMTPException, OSError) as exc:
            raise ValidationError(f"Envoi d'email impossible : {exc}") from exc

    @staticmethod
    def _adresses(destinataires: Sequence[str] | str) -> list[str]:
        """Normalise la liste d'adresses : jamais une chaîne découpée en lettres."""
        if isinstance(destinataires, str):
            # Une chaîne unique est acceptée, mais jamais itérée telle quelle
            # (« a@b.c » deviendrait dix destinataires d'une lettre chacun).
            bruts: Sequence[str] = [destinataires]
        else:
            bruts = destinataires
        adresses = [adresse.strip() for adresse in bruts if adresse and adresse.strip()]
        if not adresses:
            raise ValidationError("Envoi d'email impossible : aucun destinataire")
        return adresses

    def _composer(
        self,
        adresses: Sequence[str],
        sujet: str,
        corps: str,
        corps_html: str | None,
        pieces_jointes: Sequence[PieceJointeEmail],
    ) -> EmailMessage:
        message = EmailMessage()
        message["From"] = self._settings.smtp_from or _EXPEDITEUR_DEFAUT
        message["To"] = ", ".join(adresses)
        message["Subject"] = sujet
        message.set_content(corps)
        if corps_html and corps_html.strip():
            message.add_alternative(corps_html, subtype="html")
        for piece in pieces_jointes:
            principal, _, secondaire = (piece.mime_type or _TYPE_DEFAUT).partition("/")
            message.add_attachment(
                piece.contenu,
                maintype=principal or "application",
                subtype=secondaire or "octet-stream",
                filename=piece.nom,
            )
        return message

    def _envoyer_console(
        self,
        adresses: Sequence[str],
        sujet: str,
        corps: str,
        corps_html: str | None,
        pieces_jointes: Sequence[PieceJointeEmail],
    ) -> None:
        """Mode console : développement seulement, refus explicite en production."""
        if self._settings.environment == "production":
            raise ValidationError(
                "SMTP_HOST n'est pas configuré : l'envoi d'email est une décision "
                "de déploiement explicite (renseigner SMTP_HOST, SMTP_PORT, "
                "SMTP_FROM... dans .env)"
            )
        annexes = (
            " | pièces jointes : "
            + ", ".join(f"{p.nom} ({p.mime_type}, {p.taille} octets)" for p in pieces_jointes)
            if pieces_jointes
            else ""
        )
        logger.warning(
            "EMAIL (mode console, SMTP_HOST absent) → %s | %s%s\n%s%s",
            ", ".join(adresses),
            sujet,
            annexes,
            corps,
            "\n[variante HTML fournie, non affichée]" if corps_html else "",
        )
