"""Moteur documentaire DOCX (INC-128).

Sous-modules, tous utilisables séparément et testés indépendamment :

- ``paquet`` — lecture/réécriture fidèle du ZIP DOCX ;
- ``schemas`` — contrats Pydantic (blocs, proposition, configuration, rapport) ;
- ``analyse`` — ``DocumentAnalyzer``, extraction déterministe ;
- ``comprehension`` — ``TemplateUnderstandingService``, classification LLM ;
- ``instrumentation`` — ``TemplateInstrumenter``, ancres ``w:sdt carso:*`` ;
- ``rendu`` — ``DocumentRenderer``, rendu déterministe ;
- ``validation`` — ``DocumentValidator``, contrôles du livrable.
"""

from __future__ import annotations
