"""Extraction documentaire — point d'import unique.

``from app.documents.extraction import ExtractedContent, extract_text``.
"""

from app.documents.extraction.base import (
    ExtractedContent,
    TextExtractor,
    extract_text,
    require_library,
    supported_extensions,
)

__all__ = [
    "ExtractedContent",
    "TextExtractor",
    "extract_text",
    "require_library",
    "supported_extensions",
]
