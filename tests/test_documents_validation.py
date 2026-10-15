"""Validation documentaire — structure, contenu, style (ADR 0005, Phase 10).

Le moteur de validation est déterministe : chaque cas est un ``DocumentSpec``
réel, aucun LLM. On vérifie qu'un livrable complet passe, et que chaque défaut
réel — section absente, chiffre incohérent, débordement, fait inventé — est
signalé avec un code stable et une gravité exploitable par un agent.
"""

from __future__ import annotations

from datetime import date

from app.documents.spec import (
    DocumentSpec,
    Encadre,
    Image,
    Liste,
    Metadonnees,
    Paragraphe,
    Source,
    Tableau,
    Titre,
)
from app.documents.validation_document import (
    Finding,
    RapportValidation,
    estimer_pages,
    valider_document,
)

# Pool de mots réutilisés pour produire du contenu **différent** par section.
_MOTS = (
    "formation",
    "bénéficiaires",
    "méthodologie",
    "séquence",
    "formateurs",
    "logistique",
    "sessions",
    "évaluation",
    "compétences",
    "quartiers",
    "partenaires",
    "calendrier",
    "objectifs",
    "résultats",
    "moyens",
    "ateliers",
    "supports",
    "présence",
    "suivi",
    "indicateurs",
    "contexte",
    "public",
    "terrain",
    "équipe",
    "budget",
    "démarche",
    "livrables",
    "planning",
    "sécurité",
    "qualité",
)


def _texte(nb_mots: int, decalage: int) -> str:
    """Contenu de ``nb_mots`` mots, distinct selon ``decalage``."""
    return " ".join(_MOTS[(index + decalage) % len(_MOTS)] for index in range(nb_mots))


def _spec(blocs: list, *, profil: str = "offre_technique", **metadata) -> DocumentSpec:
    """Spec de test : métadonnées complètes par défaut (pour isoler un défaut)."""
    base = {
        "titre": "Offre technique",
        "reference": "CARSO-2026-042",
        "organisation": "CARSO",
        "date_document": date(2026, 9, 21),
        "type_document": profil,
    }
    base.update(metadata)
    return DocumentSpec(metadata=Metadonnees(**base), profil=profil, blocs=blocs)


def _offre_complete() -> DocumentSpec:
    """Offre technique qui satisfait réellement son profil (témoin positif)."""
    sections = (
        ("Contexte", 0),
        ("Objectifs", 1),
        ("Démarche", 2),
        ("Moyens", 3),
        ("Planning", 4),
        ("Résultats attendus", 5),
        ("Conclusion", 6),
    )
    blocs: list = []
    for index, (titre, decalage) in enumerate(sections):
        blocs.append(Titre(texte=titre))
        blocs.append(Paragraphe(texte=_texte(60, decalage * 7)))
        if index == 4:
            blocs.append(
                Tableau(
                    entete=["Phase", "Durée", "Montant"],
                    lignes=[
                        ["Préparation", "2 semaines", "4 000,00"],
                        ["Exécution", "6 semaines", "9 500,00"],
                        ["Total", "8 semaines", "13 500,00"],
                    ],
                    total_ligne=2,
                    largeurs_mm=[70.0, 40.0, 62.0],
                )
            )
    return _spec(blocs)


def _codes(rapport: RapportValidation, gravite: str | None = None) -> set[str]:
    return {finding.code for finding in rapport.findings if gravite in (None, finding.gravite)}


# --- Cas témoin ---------------------------------------------------------------


def test_offre_complete_valide() -> None:
    rapport = valider_document(_offre_complete())
    assert rapport.ok is True
    assert rapport.par_portee == {"structure": "passed", "contenu": "passed", "style": "passed"}
    assert rapport.findings == []
    assert rapport.resume["profil"] == "offre_technique"
    assert rapport.resume["nb_mots"] >= 350


# --- Structure ----------------------------------------------------------------


def test_section_absente_signalee() -> None:
    spec = _spec([Titre(texte="Contexte"), Paragraphe(texte=_texte(80, 0))])
    rapport = valider_document(spec, portees=("structure",))
    assert rapport.ok is False
    absentes = [f for f in rapport.findings if f.code == "S001"]
    assert len(absentes) == 6  # 7 sections attendues, 1 seule présente
    assert all(f.gravite == "erreur" for f in absentes)
    assert all(f.emplacement["profil"] == "offre_technique" for f in absentes)


def test_section_trop_pauvre_signalee() -> None:
    blocs = [Titre(texte="Contexte"), Paragraphe(texte="Trop court.")]
    rapport = valider_document(_spec(blocs), portees=("structure",))
    pauvres = [f for f in rapport.findings if f.code == "S002"]
    assert len(pauvres) == 1
    assert pauvres[0].emplacement == {"section": "Contexte", "nb_mots": 2}


def test_variante_de_titre_reconnue() -> None:
    """« 3. Méthodologie » satisfait la section « Démarche » (document réel)."""
    blocs = [Titre(texte="3. Méthodologie"), Paragraphe(texte=_texte(40, 3))]
    rapport = valider_document(_spec(blocs), portees=("structure",))
    absentes = {f.emplacement["section"] for f in rapport.findings if f.code == "S001"}
    assert "Démarche" not in absentes
    assert "Contexte" in absentes


def test_tableau_sans_entete_signale() -> None:
    blocs = [Tableau(lignes=[["A", "1", "2"], ["B", "3", "4"]])]
    rapport = valider_document(_spec(blocs), portees=("structure",))
    assert "S005" in _codes(rapport)
    assert all(f.gravite == "avertissement" for f in rapport.findings if f.code == "S005")


def test_image_sans_reference_refusee() -> None:
    """Une image sans document ni chemin est une erreur détectée avant rendu."""
    blocs = [Image()]
    rapport = valider_document(_spec(blocs), portees=("structure",))
    assert "S007" in _codes(rapport, "erreur")


def test_document_long_sans_titre_signale() -> None:
    blocs = [Paragraphe(texte=_texte(200, 0))]
    rapport = valider_document(_spec(blocs), portees=("structure",))
    assert "S004" in _codes(rapport)


def test_placeholders_signales() -> None:
    blocs = [
        Titre(texte="Contexte"),
        Paragraphe(texte="Le budget est (à compléter) et la date (à confirmer)."),
    ]
    rapport = valider_document(_spec(blocs), portees=("structure",))
    constats = [f for f in rapport.findings if f.code == "S010"]
    assert len(constats) == 1
    assert constats[0].emplacement["blocs"] == [1]


def test_liste_vide_signalee() -> None:
    rapport = valider_document(_spec([Liste(items=["  ", ""])]), portees=("structure",))
    assert "S009" in _codes(rapport, "avertissement")
    constat = next(f for f in rapport.findings if f.code == "S009")
    assert constat.emplacement == {"bloc": 0}


# --- Contenu ------------------------------------------------------------------


def test_contenu_insuffisant_refuse() -> None:
    """Un livrable court et vide n'est pas une proposition : c'est un échec."""
    rapport = valider_document(_spec([Paragraphe(texte="Bonjour.")]), portees=("contenu",))
    assert rapport.ok is False
    assert "C001" in _codes(rapport, "erreur")
    constat = next(f for f in rapport.findings if f.code == "C001")
    assert constat.emplacement["minimum"] == 350


def test_total_budgetaire_incoherent_refuse() -> None:
    blocs = [
        Tableau(
            entete=["Poste", "Montant"],
            lignes=[["A", "1 000,00"], ["B", "2 000,00"], ["Total", "9 999,00"]],
            total_ligne=2,
        )
    ]
    rapport = valider_document(_spec(blocs), portees=("contenu",))
    constat = next(f for f in rapport.findings if f.code == "C003")
    assert constat.gravite == "erreur"
    assert constat.emplacement == {"bloc": 0, "colonne": 1}
    assert "3 000.00" in constat.message and "9 999.00" in constat.message


def test_total_budgetaire_coherent_accepte() -> None:
    """Un total égal à la somme (formats français) ne produit aucun constat."""
    blocs = [
        Tableau(
            entete=["Poste", "Montant"],
            lignes=[["A", "1 250,50"], ["B", "749,50 €"], ["Total", "2 000,00"]],
            total_ligne=2,
        )
    ]
    rapport = valider_document(_spec(blocs), portees=("contenu",))
    assert "C003" not in _codes(rapport)


def test_total_sur_colonne_non_numerique_ignore() -> None:
    """Une colonne de texte n'est jamais comparée : aucune fausse alerte."""
    blocs = [
        Tableau(
            lignes=[["Préparation"], ["Exécution"], ["Total"]],
            total_ligne=2,
        )
    ]
    rapport = valider_document(_spec(blocs), portees=("contenu",))
    assert "C003" not in _codes(rapport)


def test_metadonnees_manquantes_refusees_pour_une_offre() -> None:
    spec = DocumentSpec(metadata=Metadonnees(titre="Offre technique"), blocs=[])
    rapport = valider_document(spec, profil="offre_technique", portees=("contenu",))
    constat = next(f for f in rapport.findings if f.code == "C004")
    assert constat.gravite == "erreur"
    assert constat.emplacement["manquants"] == ["reference", "organisation", "date_document"]


def test_metadonnees_manquantes_simples_avertissements_ailleurs() -> None:
    """Hors livrable transmis (ex. checklist), l'absence reste un avertissement."""
    rapport = valider_document(
        DocumentSpec(metadata=Metadonnees(titre="Checklist"), blocs=[]),
        profil="checklist",
        portees=("contenu",),
    )
    constat = next(f for f in rapport.findings if f.code == "C004")
    assert constat.gravite == "avertissement"


def test_paragraphe_repete_signale() -> None:
    texte = _texte(30, 0)
    blocs = [Paragraphe(texte=texte), Paragraphe(texte=texte)]
    rapport = valider_document(_spec(blocs), portees=("contenu",))
    constat = next(f for f in rapport.findings if f.code == "C005")
    assert constat.emplacement["blocs"] == [0, 1]


def test_source_inexistante_refusee() -> None:
    blocs = [Paragraphe(texte=_texte(20, 0), origine=[3])]
    rapport = valider_document(_spec(blocs), portees=("contenu",))
    constat = next(f for f in rapport.findings if f.code == "C007")
    assert constat.gravite == "erreur"
    assert constat.emplacement == {"bloc": 0, "source": 3}


def test_hypothese_presentee_comme_un_fait_signalee() -> None:
    """Règle CARSO : une hypothèse n'est jamais présentée comme un fait."""
    blocs = [
        Paragraphe(
            texte=_texte(20, 0),
            origine=[0],
            statut="renseigne",
        )
    ]
    spec = _spec(blocs)
    spec = spec.model_copy(
        update={"sources": [Source(genre="hypothese", reference="estimation interne")]}
    )
    rapport = valider_document(spec, portees=("contenu",))
    constat = next(f for f in rapport.findings if f.code == "C008")
    assert constat.gravite == "avertissement"
    assert "hypothese" in constat.message


def test_information_derivee_marquee_a_confirmer_acceptee() -> None:
    """Déclarer la dérivation (statut ≠ renseigné) lève le constat."""
    spec = _spec([Paragraphe(texte=_texte(20, 0), origine=[0], statut="a_confirmer")])
    spec = spec.model_copy(update={"sources": [Source(genre="information_derivee")]})
    rapport = valider_document(spec, portees=("contenu",))
    assert "C008" not in _codes(rapport)
    assert "C006" in _codes(rapport)


# --- Style --------------------------------------------------------------------


def test_niveau_de_titre_saute_signale() -> None:
    blocs = [Titre(texte="A"), Titre(texte="B", niveau=3)]
    rapport = valider_document(_spec(blocs), portees=("style",))
    constat = next(f for f in rapport.findings if f.code == "Y001")
    assert constat.emplacement == {"bloc": 1, "niveau": 3}


def test_largeurs_incoherentes_refusees() -> None:
    blocs = [
        Tableau(
            entete=["A", "B", "C"],
            lignes=[["1", "2", "3"]],
            largeurs_mm=[60.0, 60.0],
        )
    ]
    rapport = valider_document(_spec(blocs), portees=("style",))
    assert "Y002" in _codes(rapport, "erreur")


def test_tableau_debordant_refuse() -> None:
    """Largeur imprimable A4 = 172 mm : 240 mm débordent."""
    blocs = [
        Tableau(
            entete=["A", "B"],
            lignes=[["1", "2"]],
            largeurs_mm=[180.0, 60.0],
        )
    ]
    rapport = valider_document(_spec(blocs), portees=("style",))
    constat = next(f for f in rapport.findings if f.code == "Y003")
    assert constat.emplacement["largeur_mm"] == 240.0


def test_image_debordante_refusee() -> None:
    rapport = valider_document(
        _spec([Image(chemin="logo.png", largeur_mm=200.0)]), portees=("style",)
    )
    assert "Y005" in _codes(rapport, "erreur")


def test_pagination_sans_numerotation_signalee() -> None:
    from app.documents.styles import DocumentStyle, StyleEntetePied

    spec = _spec([Paragraphe(texte=_texte(700, 0))])
    spec = spec.model_copy(
        update={"style": DocumentStyle(nom="sans_numero", pied=StyleEntetePied())}
    )
    rapport = valider_document(spec, portees=("style",))
    assert "Y007" in _codes(rapport)


# --- Profils, portées, estimation ---------------------------------------------


def test_portees_selectives() -> None:
    rapport = valider_document(_offre_complete(), portees=("style",))
    assert set(rapport.par_portee) == {"style"}
    assert all(f.portee == "style" for f in rapport.findings)


def test_profil_explicite_inconnu_retombe_sur_le_profil_probable() -> None:
    """Un nom de profil inconnu n'est jamais silencieux : le rapport le trace."""
    rapport = valider_document(_offre_complete(), profil="profil_inexistant")
    assert rapport.resume["profil"] == "offre_technique"
    assert rapport.resume["profil_demande"] == "profil_inexistant"


def test_profil_du_document_utilise_quand_aucun_profil_n_est_fourni() -> None:
    rapport = valider_document(_spec([Paragraphe(texte=_texte(200, 0))], profil="checklist"))
    assert rapport.resume["profil"] == "checklist"


def test_sans_profil_seuls_les_controles_generiques_s_appliquent() -> None:
    spec = DocumentSpec(
        metadata=Metadonnees(titre="Note interne"),
        blocs=[Paragraphe(texte=_texte(200, 0))],
    )
    rapport = valider_document(spec)
    assert rapport.resume["profil"] is None
    assert not [f for f in rapport.findings if f.code.startswith("S001")]


def test_estimation_pages_croit_avec_le_contenu() -> None:
    court = _spec([Paragraphe(texte=_texte(20, 0))])
    long = _spec([Paragraphe(texte=_texte(1200, 0))])
    assert estimer_pages(court) < estimer_pages(long)


def test_encadre_et_liste_comptent_dans_la_completude() -> None:
    """Listes et encadrés sont du contenu réel, pas des blancs."""
    blocs = [
        Liste(items=[_texte(30, 0), _texte(30, 1)]),
        Encadre(texte=_texte(30, 2)),
    ]
    spec = _spec(blocs, profil="offre_technique")
    assert spec.nb_mots() >= 80


def test_rapport_serialisable_et_reparable() -> None:
    """Le rapport est exploitable par un agent : JSON stable, messages lisibles."""
    rapport = valider_document(_spec([Paragraphe(texte="Court.")]))
    charge = rapport.model_dump_json()
    assert "S001" in charge
    assert any(message.startswith("[C001]") for message in rapport.messages("erreur"))
    assert isinstance(rapport.findings[0], Finding)
    assert rapport.resume["nb_erreurs"] >= 1


def test_convention_de_codes_par_portee() -> None:
    """Convention figée : S=structure, C=contenu, Y=style (codes stables pour l'agent)."""
    prefixes = {"structure": "S", "contenu": "C", "style": "Y"}
    rapport = valider_document(_spec([Paragraphe(texte="Trop court.")]), profil="courrier")
    assert rapport.findings
    for finding in rapport.findings:
        assert finding.code.startswith(prefixes[finding.portee])
        assert finding.message and finding.portee in prefixes
