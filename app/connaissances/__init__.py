"""Base de connaissance documentaire de CARSO (RAG).

Un **outil de recherche**, pas un pipeline imposé : les agents appellent
``search_knowledge_base`` quand une question demande une connaissance (méthode,
référence, expertise CARSO) et reçoivent des passages **sourcés** (ouvrage,
page, section). Aucun document n'est résumé de mémoire : le texte cité vient
du fichier indexé.

Modules :

- ``lecture`` — extraction du texte **avec sa provenance** (page PDF, section
  DOCX, feuille XLSX) ;
- ``decoupage`` — découpage déterministe, chevauchement borné ;
- ``embeddings`` — modèle d'embedding effectif (administration, sinon ``.env``)
  et signature anti-mélange ;
- ``vecteurs`` — index vectoriel Qdrant **local** (aucun serveur à exploiter) ;
- ``indexation`` — déroulé complet d'un ouvrage (lecture → chunks → vecteurs) ;
- ``racine`` — résolution du répertoire géré des fichiers.

L'infrastructure d'indexation en tâche de fond vit hors de ce paquet
(``app.infrastructure.indexation_connaissances``), comme le balayage du cycle
de vie.
"""

from app.connaissances.decoupage import Passage, decouper
from app.connaissances.lecture import Segment, lire_segments

__all__ = ["Passage", "Segment", "decouper", "lire_segments"]
