"""Tests — référentiel et montants d'un camembert (INC-132).

Une pièce réelle exprime ses parts par rapport au total général (frais imprévus
compris) alors que les parts affichées n'en couvrent qu'une partie : sans
référentiel, le camembert affichait 35,1 % là où le tableau disait 33,5 %.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.documents.figures import spec_graphique, svg_graphique
from app.documents.spec import Graphique

VALEURS = [8_400_000.0, 3_750_000.0, 4_750_000.0, 3_000_000.0, 3_800_000.0, 200_000.0]
LABELS = [
    "Transport",
    "Indemnités",
    "Hébergement",
    "Restauration",
    "Formation",
    "Communication",
]
TOTAL_GENERAL = 25_095_000.0


def _bloc(**options: object) -> Graphique:
    return Graphique(variante="pie", labels=LABELS, valeurs=VALEURS, **options)


def _svg(**options: object) -> str:
    return svg_graphique(spec_graphique(_bloc(**options)))


class TestTotalDeReference:
    """Le dénominateur des pourcentages est réglable."""

    def test_sans_reference_somme_des_parts(self) -> None:
        assert "35.1 %" in _svg()

    def test_reference_generale(self) -> None:
        # 8 400 000 / 25 095 000 = 33,5 % — le pourcentage du tableau du rapport.
        assert "33.5 %" in _svg(total_reference=TOTAL_GENERAL)
        assert "35.1 %" not in _svg(total_reference=TOTAL_GENERAL)

    def test_options_transmises_par_la_spec(self) -> None:
        spec = spec_graphique(_bloc(total_reference=TOTAL_GENERAL, afficher_valeurs=True))
        assert spec["total_reference"] == TOTAL_GENERAL
        assert spec["afficher_valeurs"] is True


class TestMontantsEnLegende:
    """Les montants peuvent accompagner les pourcentages."""

    def test_montants_affiches(self) -> None:
        svg = _svg(afficher_valeurs=True)
        assert "Transport — 8.4M (35.1 %)" in svg
        assert "Communication — 200.0k (0.8 %)" in svg

    def test_montants_absents_par_defaut(self) -> None:
        # La valeur reste dans la bulle d'aide SVG : seule la légende est réglée.
        svg = _svg()
        assert "Transport — 35.1 %" in svg
        assert "Transport — 8.4M" not in svg


class TestRefusExplicites:
    """Les options de camembert sont refusées ailleurs, jamais ignorées."""

    def test_total_reference_sur_barres(self) -> None:
        with pytest.raises(PydanticValidationError, match="total_reference"):
            Graphique(
                variante="bar",
                labels=["a", "b"],
                series=[{"nom": "s", "valeurs": [1, 2]}],
                total_reference=10.0,
            )

    def test_montants_sur_courbes(self) -> None:
        with pytest.raises(PydanticValidationError, match="total_reference"):
            Graphique(
                variante="line",
                labels=["a", "b"],
                series=[{"nom": "s", "valeurs": [1, 2]}],
                afficher_valeurs=True,
            )

    def test_reference_nulle(self) -> None:
        with pytest.raises(PydanticValidationError):
            _bloc(total_reference=0)
