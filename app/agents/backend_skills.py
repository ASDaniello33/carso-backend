"""Vue « skills » du backend de fichiers Deep Agents.

``SkillsMiddleware`` attend des sources **POSIX relatives à la racine du
backend** (``"/skills/"``). Le harnais passait les chemins hôtes des dossiers
(``C:\\...\\app\\agents\\skills\\docx-officiels``) : aucun préfixe ne
correspondait dans le ``CompositeBackend``, chaque ``ls``/``download_files``
tombait sur le ``StateBackend`` vide, et l'agent répondait qu'il n'avait aucun
accès aux skills documents — alors que ``bloc_skills`` les listait.

Cette vue expose **uniquement les skills déclarés** au contrat de l'agent,
montés sous ``/skills/<nom>/``. Elle est en lecture seule : un skill est une
recette, pas un espace de travail modifiable. Le moindre chemin inconnu est
refusé explicitement (jamais un accès silencieux au reste de l'arborescence).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from deepagents.backends import FilesystemBackend
from deepagents.backends.protocol import (
    BackendProtocol,
    EditResult,
    FileDownloadResponse,
    FileUploadResponse,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)

from app.agents.registre_skills import SOURCE_SKILLS

_LECTURE_SEULE = (
    "Les skills sont en lecture seule : ils documentent des recettes, "
    "ils ne se modifient pas depuis une conversation."
)


class BackendSkills(BackendProtocol):
    """Backend en lecture seule limité aux skills déclarés d'un agent."""

    def __init__(self, racine: Path | str, noms: Sequence[str]) -> None:
        racine_path = Path(racine)
        self._bases: dict[str, FilesystemBackend] = {
            nom: FilesystemBackend(
                root_dir=str(racine_path / nom), virtual_mode=True
            )
            for nom in noms
        }

    # -- routage ---------------------------------------------------------
    @property
    def noms(self) -> tuple[str, ...]:
        """Skills montés, dans l'ordre de déclaration."""
        return tuple(self._bases)

    def _inconnu(self, chemin: str) -> str:
        declares = ", ".join(self._bases) or "aucun"
        return (
            f"Skill inaccessible : {chemin!r}. Skills déclarés pour cet agent : "
            f"{declares}. Chemins valides : {SOURCE_SKILLS}<nom>/SKILL.md"
        )

    def _decouper(self, chemin: str) -> tuple[str | None, str]:
        """``/skills/docx-officiels/x.md`` → ``("docx-officiels", "/x.md")``.

        Le préfixe ``/skills`` est facultatif : monté comme route d'un
        ``CompositeBackend``, le middleware reçoit le chemin **déjà écrêté**
        (``/docx-officiels/x.md``) ; appelé directement, il porte le préfixe.
        """
        morceaux = [m for m in str(chemin).replace("\\", "/").split("/") if m]
        if morceaux and morceaux[0] == SOURCE_SKILLS.strip("/"):
            morceaux = morceaux[1:]
        if not morceaux:
            return None, "/"
        return morceaux[0], "/" + "/".join(morceaux[1:])

    def _base(self, chemin: str) -> tuple[FilesystemBackend | None, str]:
        nom, reste = self._decouper(chemin)
        return (self._bases.get(nom) if nom else None), reste

    @staticmethod
    def _prefixe(chemin: str) -> bool:
        """Le chemin reçu porte-t-il déjà ``/skills`` (appel direct) ?

        Monté en route de ``CompositeBackend``, le préfixe est écrêté avant
        d'arriver ici : les chemins rendus doivent alors rester relatifs, sinon
        le composite les préfixerait deux fois.
        """
        return str(chemin).replace("\\", "/").strip("/").split("/")[0] == (
            SOURCE_SKILLS.strip("/")
        )

    def _rendre(self, chemin: str, prefixe: bool) -> str:
        return f"{SOURCE_SKILLS}{chemin}" if prefixe else f"/{chemin}"

    # -- lecture ---------------------------------------------------------
    def ls(self, path: str) -> LsResult:
        nom, reste = self._decouper(path)
        prefixe = self._prefixe(path)
        if nom is None:
            return LsResult(
                entries=[
                    {
                        "path": self._rendre(mon_nom, prefixe),
                        "is_dir": True,
                        "size": 0,
                        "modified_at": "",
                    }
                    for mon_nom in self._bases
                ]
            )
        base = self._bases.get(nom)
        if base is None:
            return LsResult(error=self._inconnu(path))
        resultat = base.ls(reste)
        if resultat.error:
            return resultat
        return LsResult(
            entries=[
                {
                    **entree,
                    "path": self._rendre(f"{nom}{entree['path']}", prefixe),
                }
                for entree in resultat.entries or []
            ]
        )

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        base, reste = self._base(file_path)
        if base is None:
            return ReadResult(error=self._inconnu(file_path))
        return base.read(reste, offset=offset, limit=limit)

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        reponses: list[FileDownloadResponse] = []
        for chemin in paths:
            base, reste = self._base(chemin)
            if base is None:
                reponses.append(
                    FileDownloadResponse(
                        path=chemin, content=None, error="file_not_found"
                    )
                )
                continue
            reponse = base.download_files([reste])[0]
            reponses.append(
                FileDownloadResponse(
                    path=chemin, content=reponse.content, error=reponse.error
                )
            )
        return reponses

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        cibles = self._cibles(path)
        if cibles is None:
            return GlobResult(error=self._inconnu(str(path)))
        prefixe = self._prefixe(path or SOURCE_SKILLS)
        trouves: list[dict[str, Any]] = []
        tronque = False
        for nom, base, reste in cibles:
            resultat = base.glob(pattern, reste)
            if resultat.error:
                return GlobResult(error=resultat.error)
            tronque = tronque or bool(resultat.truncated)
            trouves.extend(
                {**entree, "path": self._rendre(f"{nom}{entree['path']}", prefixe)}
                for entree in resultat.matches or []
            )
        return GlobResult(matches=trouves, truncated=tronque)

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,  # noqa: A002 - nom imposé par le protocole
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        cibles = self._cibles(path)
        if cibles is None:
            return GrepResult(error=self._inconnu(str(path)))
        prefixe = self._prefixe(path or SOURCE_SKILLS)
        trouves: list[dict[str, Any]] = []
        tronque = False
        for nom, base, reste in cibles:
            resultat = base.grep(pattern, reste, glob, max_count=max_count)
            if resultat.error:
                return GrepResult(error=resultat.error)
            tronque = tronque or bool(resultat.truncated)
            trouves.extend(
                {**entree, "path": self._rendre(f"{nom}{entree['path']}", prefixe)}
                for entree in resultat.matches or []
            )
        return GrepResult(matches=trouves, truncated=tronque)

    def _cibles(
        self, path: str | None
    ) -> list[tuple[str, FilesystemBackend, str]] | None:
        """Skills à parcourir pour un ``path`` (``None`` = tous), ou refus."""
        nom, reste = self._decouper(path or SOURCE_SKILLS)
        if nom is None:
            return [(mon_nom, base, "/") for mon_nom, base in self._bases.items()]
        base = self._bases.get(nom)
        if base is None:
            return None
        return [(nom, base, reste)]

    # -- écriture : refusée ----------------------------------------------
    def write(self, file_path: str, content: str) -> WriteResult:
        return WriteResult(error=_LECTURE_SEULE)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,  # noqa: FBT001, FBT002 - signature du protocole
    ) -> EditResult:
        return EditResult(error=_LECTURE_SEULE)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return [
            FileUploadResponse(path=chemin, error="permission_denied")
            for chemin, _contenu in files
        ]


__all__ = ["SOURCE_SKILLS", "BackendSkills"]
