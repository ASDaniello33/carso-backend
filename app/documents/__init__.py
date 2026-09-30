"""Couche documentaire (Phase 4, instruction/11) — stockage, validation, extraction.

Responsabilité : tout ce qui touche au **fichier** lui-même.

- ``paths`` : sécurité des chemins (sanitisation, chemin logique, confinement) ;
- ``validation`` : allowlist d'extensions + contrôle du contenu réel ;
- ``storage`` : écriture atomique, checksum, refus d'écrasement ;
- ``extraction`` : lecture du texte (PDF, DOCX, XLSX, texte).

Les cas d'usage documentaires (enregistrer, remplacer, approuver, archiver,
extraire) vivent dans la couche application
(``app.application.services.document_service``) : cette couche-ci n'applique
aucune règle métier et n'ouvre aucune transaction.
"""

from app.documents.paths import (
    SCOPES,
    build_logical_path,
    directory_of,
    extension_of,
    resolve_within_root,
    sanitize_filename,
    versioned_filename,
)
from app.documents.storage import (
    DocumentStorage,
    LocalDocumentStorage,
    StoredFile,
    UploadTooLargeError,
)
from app.documents.validation import (
    ALLOWED_EXTENSIONS,
    FILE_TYPES,
    FileTypeRule,
    inspect_file,
)

__all__ = [
    "ALLOWED_EXTENSIONS",
    "FILE_TYPES",
    "SCOPES",
    "DocumentStorage",
    "FileTypeRule",
    "LocalDocumentStorage",
    "StoredFile",
    "UploadTooLargeError",
    "build_logical_path",
    "directory_of",
    "extension_of",
    "inspect_file",
    "resolve_within_root",
    "sanitize_filename",
    "versioned_filename",
]
