"""DocumentValidator — ce qui a été vérifié, et ce qui ne l'a pas été (INC-128).

Produire un fichier ne suffit pas. Ce module sépare explicitement trois niveaux :

1. **structure** : archive lisible, chaque partie XML bien formée, relations
   présentes, images toujours là, et — quand le modèle d'origine est fourni —
   parties non ciblées **identiques octet pour octet** ;
2. **contenu** : aucune ancre ``carso:`` résiduelle dans le livrable, aucun
   placeholder ``{{…}}`` laissé par le modèle, tags uniques dans la copie
   instrumentée ;
3. **visuel** : une conversion PDF par LibreOffice **si l'outil existe**. Sinon
   le rapport dit ``visuel = "non_verifie"`` et l'explique. Un XML valide n'a
   jamais été, et ne sera jamais présenté ici comme une preuve de rendu fidèle.

Le validateur ne corrige rien : il constate, et son ``ok`` vaut faux dès qu'une
erreur structurelle ou de contenu est trouvée.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from app.core.errors import ValidationError
from app.documents.moteur import analyse as analyse_docx
from app.documents.moteur import instrumentation as instrumentation_docx
from app.documents.moteur import paquet as paquet_docx
from app.documents.moteur import schemas as S

__all__ = ["DocumentValidator", "valider_livrable"]

_PLACEHOLDER = re.compile(r"\{\{\s*[a-zA-Z0-9_.-]+\s*\}\}")
_EXTENSIONS_XML = (".xml", ".rels")


def _parties_xml(paquet: paquet_docx.Paquet) -> list[str]:
    return [nom for nom in paquet.parties if nom.casefold().endswith(_EXTENSIONS_XML)]


def _liens(paquet: paquet_docx.Paquet) -> dict[str, int]:
    """Nombre de relations par partie ``.rels`` (preuve que les liens tiennent)."""
    from lxml import etree

    ns = "http://schemas.openxmlformats.org/package/2006/relationships"
    comptes: dict[str, int] = {}
    for nom in paquet.parties:
        if not nom.casefold().endswith(".rels"):
            continue
        try:
            racine = etree.fromstring(paquet.parties[nom])
        except etree.XMLSyntaxError:
            comptes[nom] = -1
            continue
        comptes[nom] = len(racine.findall(f"{{{ns}}}Relationship"))
    return comptes


def _texte_total(paquet: paquet_docx.Paquet) -> str:
    morceaux: list[str] = []
    for nom in paquet_docx.zones_texte(paquet):
        morceaux.append(analyse_docx.texte_element(paquet_docx.xml(paquet, nom)))
    return "\n".join(morceaux)


class DocumentValidator:
    """Contrôles indépendants du rendu lui-même."""

    def valider_instrumente(
        self, octets: bytes, configuration: S.ConfigurationModele
    ) -> S.RapportValidation:
        """Vérifie la copie technique : tags présents, uniques, conformes."""
        rapport = S.RapportValidation()
        try:
            paquet = paquet_docx.lire(octets)
        except ValidationError as exc:
            rapport.ok = False
            rapport.erreurs.append(str(exc))
            return rapport

        tags = instrumentation_docx.tags_carso(paquet)
        attendus = set(configuration.tags())
        for champ in configuration.champs:
            if champ.classification is S.Classification.REPEATABLE:
                # Une ligne répétable n'a pas de tag simple : elle porte un tag de
                # ligne et un tag par colonne. Le tag nu serait une ancre absente.
                attendus.discard(f"{S.TAG_PREFIX}{champ.field_key}")
                attendus.add(f"{S.TAG_PREFIX}lignes:{champ.field_key}")
                attendus.update(
                    f"{S.TAG_PREFIX}{champ.field_key}.{colonne}"
                    for colonne in champ.colonnes
                )
            elif champ.classification is S.Classification.CONDITIONAL:
                attendus.discard(f"{S.TAG_PREFIX}{champ.field_key}")
                attendus.add(f"{S.TAG_PREFIX}conditionnel:{champ.field_key}")
            elif champ.classification is S.Classification.STATIC:
                attendus.discard(f"{S.TAG_PREFIX}{champ.field_key}")

        absents = sorted(attendus - set(tags))
        inattendus = sorted(set(tags) - attendus)
        dupliques = sorted({tag for tag in tags if tags.count(tag) > 1})
        if absents:
            rapport.ok = False
            rapport.erreurs.append("Ancres attendues absentes : " + ", ".join(absents))
        if dupliques:
            rapport.ok = False
            rapport.erreurs.append("Ancres dupliquées : " + ", ".join(dupliques))
        if inattendus:
            rapport.avertissements.append(
                "Ancres non prévues par la configuration : " + ", ".join(inattendus)
            )
        rapport.tags_restants = tags
        self._verifier_structure(paquet, rapport)
        return rapport

    def valider_livrable(
        self,
        octets: bytes,
        *,
        modele: bytes | None = None,
        parties_modifiees: list[str] | None = None,
        verifier_visuel: bool = True,
    ) -> S.RapportValidation:
        """Vérifie un livrable : structure, conservation, absence d'ancre.

        Args:
            octets: DOCX produit.
            modele: DOCX d'origine, pour comparer les parties non ciblées.
            parties_modifiees: parties que le rendu a le droit d'avoir changées.
            verifier_visuel: tenter la conversion PDF si LibreOffice existe.
        """
        rapport = S.RapportValidation()
        rapport.parties_modifiees = list(parties_modifiees or [])
        try:
            paquet = paquet_docx.lire(octets)
        except ValidationError as exc:
            rapport.ok = False
            rapport.erreurs.append(str(exc))
            return rapport

        self._verifier_structure(paquet, rapport)

        tags = instrumentation_docx.tags_carso(paquet)
        if tags:
            rapport.ok = False
            rapport.tags_restants = tags
            rapport.erreurs.append(
                f"{len(tags)} ancre(s) technique(s) encore présente(s) dans le "
                "livrable : " + ", ".join(tags[:5])
            )
        restes = sorted(set(_PLACEHOLDER.findall(_texte_total(paquet))))
        if restes:
            rapport.ok = False
            rapport.erreurs.append(
                "Placeholders du modèle non remplacés : " + ", ".join(restes[:10])
            )

        if modele is not None:
            self._verifier_conservation(paquet, modele, rapport)

        if verifier_visuel:
            self._verifier_visuel(octets, rapport)
        return rapport

    def valider_edition(
        self,
        octets: bytes,
        *,
        precedent: bytes,
        parties_modifiees: list[str] | None = None,
        parties_ajoutees: list[str] | None = None,
        verifier_visuel: bool = False,
    ) -> S.RapportValidation:
        """Vérifie une **édition** d'une copie de modèle (INC-129).

        Différence avec :meth:`valider_livrable` : une édition ajoute des
        parties (médias insérés) et n'impose aucun remplacement de placeholder —
        un modèle client peut en contenir sans que ce soit une erreur. Ce qui
        reste interdit : une partie perdue, une partie non visée modifiée et une
        image disparue.

        Args:
            octets: copie éditée.
            precedent: version précédente, pour comparer les parties.
            parties_modifiees: parties que l'édition a le droit de changer.
            parties_ajoutees: parties médias légitimement ajoutées.
            verifier_visuel: tenter la conversion PDF si LibreOffice existe.
        """
        rapport = S.RapportValidation()
        rapport.parties_modifiees = list(parties_modifiees or [])
        try:
            paquet = paquet_docx.lire(octets)
            origine = paquet_docx.lire(precedent)
        except ValidationError as exc:
            rapport.ok = False
            rapport.erreurs.append(str(exc))
            return rapport

        self._verifier_structure(paquet, rapport)

        autorisees = set(rapport.parties_modifiees)
        ajoutees = set(parties_ajoutees or [])
        perdues = sorted(set(origine.parties) - set(paquet.parties))
        if perdues:
            rapport.ok = False
            rapport.erreurs.append("Parties perdues : " + ", ".join(perdues[:10]))
        alterees = sorted(
            nom
            for nom, contenu in origine.parties.items()
            if nom not in autorisees
            and nom in paquet.parties
            and paquet.parties[nom] != contenu
        )
        if alterees:
            rapport.ok = False
            rapport.erreurs.append(
                "Parties non visées modifiées : " + ", ".join(alterees[:10])
            )
        inattendues = sorted(set(paquet.parties) - set(origine.parties) - ajoutees)
        if inattendues:
            rapport.ok = False
            rapport.erreurs.append(
                "Parties ajoutées non déclarées : " + ", ".join(inattendues[:10])
            )
        if paquet.nb_images() < origine.nb_images():
            rapport.ok = False
            rapport.erreurs.append(
                f"Ressources graphiques perdues : {paquet.nb_images()} au lieu de "
                f"{origine.nb_images()}"
            )
        restes = sorted(set(_PLACEHOLDER.findall(_texte_total(paquet))))
        if restes:
            rapport.avertissements.append(
                "Marqueurs {{…}} encore présents dans la copie : "
                + ", ".join(restes[:10])
            )
        rapport.details["parties_conservees"] = len(
            [nom for nom in origine.parties if nom not in autorisees]
        )
        rapport.details["images"] = paquet.nb_images()
        rapport.details["images_ajoutees"] = paquet.nb_images() - origine.nb_images()
        if verifier_visuel:
            self._verifier_visuel(octets, rapport)
        return rapport

    # --- internes -----------------------------------------------------------

    def _verifier_structure(self, paquet: paquet_docx.Paquet, rapport: S.RapportValidation) -> None:
        """Chaque partie XML doit s'analyser ; les relations doivent tenir."""
        from lxml import etree

        for nom in _parties_xml(paquet):
            try:
                etree.fromstring(paquet.parties[nom])
            except etree.XMLSyntaxError as exc:
                rapport.ok = False
                rapport.erreurs.append(f"XML mal formé dans {nom} : {str(exc)[:120]}")
        liens = _liens(paquet)
        casses = sorted(nom for nom, compte in liens.items() if compte < 0)
        if casses:
            rapport.ok = False
            rapport.erreurs.append("Fichier de relations illisible : " + ", ".join(casses))
        rapport.details["parties"] = len(paquet.parties)
        rapport.details["images"] = paquet.nb_images()
        rapport.details["relations"] = sum(compte for compte in liens.values() if compte > 0)

    def _verifier_conservation(
        self, paquet: paquet_docx.Paquet, modele: bytes, rapport: S.RapportValidation
    ) -> None:
        """Parties non ciblées identiques au modèle, octet pour octet."""
        try:
            origine = paquet_docx.lire(modele)
        except ValidationError as exc:
            rapport.avertissements.append(f"Modèle d'origine illisible : {exc}")
            return
        modifiees = set(rapport.parties_modifiees)
        perdues = sorted(set(origine.parties) - set(paquet.parties))
        if perdues:
            rapport.ok = False
            rapport.erreurs.append("Parties perdues : " + ", ".join(perdues[:10]))
        alterees = sorted(
            nom
            for nom, contenu in origine.parties.items()
            if nom not in modifiees
            and nom in paquet.parties
            and paquet.parties[nom] != contenu
        )
        if alterees:
            rapport.ok = False
            rapport.erreurs.append(
                "Parties non ciblées modifiées : " + ", ".join(alterees[:10])
            )
        if paquet.nb_images() != origine.nb_images():
            rapport.ok = False
            rapport.erreurs.append(
                f"Ressources graphiques : {paquet.nb_images()} au lieu de "
                f"{origine.nb_images()}"
            )
        rapport.details["parties_conservees"] = len(set(origine.parties) - modifiees)

    def _verifier_visuel(self, octets: bytes, rapport: S.RapportValidation) -> None:
        """Conversion PDF **si** LibreOffice est disponible ; sinon le dire.

        Une conversion réussie ne prouve pas la fidélité visuelle : elle prouve
        que le fichier s'ouvre et se pagine. Le rapport le formule ainsi.
        """
        binaire = shutil.which("soffice") or shutil.which("libreoffice")
        if binaire is None:
            rapport.visuel = "non_verifie"
            rapport.avertissements.append(
                "LibreOffice (soffice) introuvable : le contrôle visuel n'a PAS été "
                "effectué. Le document est valide structurellement, pas certifié "
                "conforme au rendu du modèle."
            )
            return
        with tempfile.TemporaryDirectory(prefix="carso-moteur-") as dossier:
            source = Path(dossier) / "livrable.docx"
            source.write_bytes(octets)
            try:
                resultat = subprocess.run(  # noqa: S603 - binaire local, args fixes
                    [
                        binaire,
                        "--headless",
                        "--convert-to",
                        "pdf",
                        "--outdir",
                        dossier,
                        str(source),
                    ],
                    capture_output=True,
                    timeout=120,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                rapport.visuel = "echec"
                rapport.avertissements.append(
                    f"Conversion PDF impossible : {type(exc).__name__}. Rendu non vérifié."
                )
                return
            pdf = Path(dossier) / "livrable.pdf"
            if resultat.returncode != 0 or not pdf.exists():
                rapport.visuel = "echec"
                rapport.avertissements.append(
                    "LibreOffice n'a pas produit de PDF : rendu non vérifié "
                    f"(code {resultat.returncode})."
                )
                return
            rapport.visuel = "converti"
            rapport.details["pdf_octets"] = pdf.stat().st_size
            rapport.avertissements.append(
                "PDF de contrôle produit : il confirme que le document s'ouvre et se "
                "paginer ; il ne prouve pas une fidélité au pixel près."
            )


def valider_livrable(
    octets: bytes,
    *,
    modele: bytes | None = None,
    parties_modifiees: list[str] | None = None,
    verifier_visuel: bool = True,
) -> S.RapportValidation:
    """Raccourci : validation d'un livrable produit par le moteur."""
    return DocumentValidator().valider_livrable(
        octets,
        modele=modele,
        parties_modifiees=parties_modifiees,
        verifier_visuel=verifier_visuel,
    )
