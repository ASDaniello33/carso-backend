"""Aides Python d'un script d'agent (bibliothèque standard uniquement).

Elles sont copiées dans ``<espace>/carso_outils/`` à chaque exécution : un
script les importe comme n'importe quel module.

Pourquoi ici : la garde d'audit refuse ``openpyxl`` (nom interdit, et le module
natif de Windows dont il dépend). Écrire un classeur d'une table ne demande
pourtant aucune dépendance — le format OOXML est un ZIP de parties XML.

Contrat identique au reste de la chaîne documentaire : les valeurs sont écrites
telles quelles, aucune donnée n'est inventée ni calculée.
"""

from __future__ import annotations

__all__ = ["classeur_depuis_table", "ecrire_classeur"]
