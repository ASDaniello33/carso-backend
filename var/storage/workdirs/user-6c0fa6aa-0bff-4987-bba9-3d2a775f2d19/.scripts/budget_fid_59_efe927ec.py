import classeur

# =============================================================================
# RAPPORT BUDGÉTAIRE COMPLET — MISSION-AMI-FID-59
# Formation IA appliquée aux usages professionnels du FID
# 31 octobre – 5 novembre 2026 | Antananarivo | Devise : Ariary (MGA)
# =============================================================================

# ---------------------------------------------------------------------------
# FEUILLE 1 : COUVERTURE
# ---------------------------------------------------------------------------
couverture = [
    ["", "", "", "", "", "", ""],
    ["", "", "", "", "", "", ""],
    ["", "", "RAPPORT BUDGÉTAIRE", "", "", "", ""],
    ["", "", "MISSION-AMI-FID-59", "", "", "", ""],
    ["", "", "", "", "", "", ""],
    ["Poste", "", "Valeur", "", "", "", ""],
    ["Référence", "", "MISSION-AMI-FID-59", "", "", "", ""],
    ["Titre", "", "Formation IA appliquée aux usages professionnels du FID", "", "", "", ""],
    ["Organisation", "", "Fonds d'Intervention pour le Développement (FID)", "", "", "", ""],
    ["Période", "", "31 octobre – 5 novembre 2026 (6 jours)", "", "", "", ""],
    ["Lieu", "", "Antananarivo – FID Direction Générale", "", "", "", ""],
    ["Date du rapport", "", "7 novembre 2026", "", "", "", ""],
    ["Devise", "", "Ariary (MGA)", "", "", "", ""],
    ["Statut mission", "", "Clôturée", "", "", "", ""],
]
classeur.ecrire_classeur(
    "rapport_budgetaire_MISSION-AMI-FID-59.xlsx",
    "Couverture",
    couverture,
    titre="Rapport Budgétaire — MISSION-AMI-FID-59"
)

# ---------------------------------------------------------------------------
# FEUILLE 2 : SYNTHÈSE BUDGÉTAIRE
# ---------------------------------------------------------------------------
synthese = [
    ["SYNTHÈSE BUDGÉTAIRE — MISSION-AMI-FID-59", "", "", "", "", "", ""],
    ["Ce rapport présente le détail complet des dépenses engagées pour la mission de formation IA appliquée aux usages professionnels du FID.", "", "", "", "", "", ""],
    ["", "", "", "", "", "", ""],
    ["Poste de dépense", "", "Montant (Ar)", "", "% du total", "", ""],
    ["Honoraires intervenants", "", "12 000 000", "", "48,0%", "", ""],
    ["Transport & Déplacements", "", "2 500 000", "", "10,0%", "", ""],
    ["Hébergement & Restauration", "", "3 600 000", "", "14,4%", "", ""],
    ["Logistique & Matériel", "", "2 000 000", "", "8,0%", "", ""],
    ["Fournitures & Supports pédagogiques", "", "1 500 000", "", "6,0%", "", ""],
    ["Communication & Coordination", "", "500 000", "", "2,0%", "", ""],
    ["Frais administratifs & Divers", "", "1 000 000", "", "4,0%", "", ""],
    ["Marge / Imprévus (5%)", "", "1 250 000", "", "5,0%", "", ""],
    ["TOTAL GÉNÉRAL", "", "24 350 000", "", "100,0%", "", ""],
    ["", "", "", "", "", "", ""],
    ["INDICATEURS CLÉS", "", "", "", "", "", ""],
    ["Coût total de la mission", "", "24 350 000 Ar", "", "", "", ""],
    ["Coût par jour de mission", "", "4 058 333 Ar", "", "", "", ""],
    ["Coût par intervenant/jour", "", "1 014 583 Ar", "", "", "", ""],
    ["Nombre d'intervenants", "", "4", "", "", "", ""],
    ["Durée de la mission", "", "6 jours", "", "", "", ""],
    ["Lieu", "", "Antananarivo – FID DG", "", "", "", ""],
]
classeur.ecrire_classeur(
    "rapport_budgetaire_MISSION-AMI-FID-59.xlsx",
    "Synthèse Budgétaire",
    synthese,
    titre="Synthèse Budgétaire"
)

# ---------------------------------------------------------------------------
# FEUILLE 3 : HONORAIRES
# ---------------------------------------------------------------------------
honoraires = [
    ["DÉTAIL DES HONORAIRES DES INTERVENANTS", "", "", "", "", "", ""],
    ["Les honoraires couvrent la préparation, l'animation et le suivi de la formation IA appliquée.", "", "", "", "", "", ""],
    ["", "", "", "", "", "", ""],
    ["N°", "Intervenant", "Rôle dans la mission", "Profil", "Jours", "Taux journalier (Ar)", "Total (Ar)"],
    ["1", "Sara Amrani", "Formateur principal", "Formateur senior", "6", "1 500 000", "data-formule='=F5*E5'", "data-format='entier'"],
    ["2", "Jaque Jean", "Formateur", "Formateur", "6", "500 000", "data-formule='=F6*E6'", "data-format='entier'"],
    ["", "", "", "", "", "SOUS-TOTAL HONORAIRES", "data-formule='=SUM(F5:F6)'", "data-format='entier'", "data-gras='1'", "data-fond='doux'"],
]
classeur.ecrire_classeur(
    "rapport_budgetaire_MISSION-AMI-FID-59.xlsx",
    "Honoraires",
    honoraires,
    titre="Honoraires des Intervenants"
)

# ---------------------------------------------------------------------------
# FEUILLE 4 : TRANSPORT
# ---------------------------------------------------------------------------
transport = [
    ["TRANSPORT & DÉPLACEMENTS", "", "", "", "", "", ""],
    ["Poste transport couvrant les déplacements liés à la mission sur Antananarivo.", "", "", "", "", "", ""],
    ["", "", "", "", "", "", ""],
    ["N°", "Désignation", "Unité", "Qté", "Coût unitaire (Ar)", "Total (Ar)", ""],
    ["1", "Location véhicule avec chauffeur (Antananarivo)", "Jour", "6", "200 000", "data-formule='=D5*E5'", "data-format='entier'"],
    ["2", "Carburant", "Forfait", "1", "500 000", "data-formule='=D6*E6'", "data-format='entier'"],
    ["3", "Transport local (taxi, courses)", "Forfait", "1", "300 000", "data-formule='=D7*E7'", "data-format='entier'"],
    ["4", "Indemnités de déplacement intervenants", "Personne", "4", "250 000", "data-formule='=D8*E8'", "data-format='entier'"],
    ["", "", "", "", "SOUS-TOTAL TRANSPORT", "data-formule='=SUM(F5:F8)'", "data-format='entier'", "data-gras='1'", "data-fond='doux'"],
]
classeur.ecrire_classeur(
    "rapport_budgetaire_MISSION-AMI-FID-59.xlsx",
    "Transport",
    transport,
    titre="Transport & Déplacements"
)

# ---------------------------------------------------------------------------
# FEUILLE 5 : HÉBERGEMENT & RESTAURATION
# ---------------------------------------------------------------------------
hebergement = [
    ["HÉBERGEMENT & RESTAURATION", "", "", "", "", "", ""],
    ["Coûts d'hébergement et de restauration pour les 4 intervenants sur 6 jours (31/10 – 05/11).", "", "", "", "", "", ""],
    ["", "", "", "", "", "", ""],
    ["N°", "Désignation", "Unité", "Qté", "Coût unitaire (Ar)", "Total (Ar)", ""],
    ["1", "Hébergement (hôtel 3-4 étoiles Antananarivo)", "Nuitée", "24", "150 000", "data-formule='=D5*E5'", "data-format='entier'"],
    ["", "", "", "", "SOUS-TOTAL HÉBERGEMENT", "data-formule='=SUM(F5:F5)'", "data-format='entier'", "data-gras='1'", "data-fond='doux'"],
    ["", "", "", "", "", "", ""],
    ["DÉTAIL RESTAURATION (si non inclus dans hébergement)", "", "", "", "", "", ""],
    ["N°", "Désignation", "Unité", "Qté", "Coût unitaire (Ar)", "Total (Ar)", ""],
    ["1", "Repas (petit-déjeuner + déjeuner + dîner)", "Personne/jour", "24", "75 000", "data-formule='=D11*E11'", "data-format='entier'"],
    ["2", "Cafés et rafraîchissements (sessions)", "Forfait", "1", "400 000", "data-formule='=D12*E12'", "data-format='entier'"],
    ["", "", "", "", "SOUS-TOTAL RESTAURATION", "data-formule='=SUM(F11:F12)'", "data-format='entier'", "data-gras='1'", "data-fond='doux'"],
]
classeur.ecrire_classeur(
    "rapport_budgetaire_MISSION-AMI-FID-59.xlsx",
    "Hebergement_Restauration",
    hebergement,
    titre="Hébergement & Restauration"
)

# ---------------------------------------------------------------------------
# FEUILLE 6 : LOGISTIQUE & MATÉRIEL
# ---------------------------------------------------------------------------
logistique = [
    ["LOGISTIQUE & MATÉRIEL", "", "", "", "", "", ""],
    ["Matériel technique et logistique nécessaire au bon déroulement de la formation IA.", "", "", "", "", "", ""],
    ["", "", "", "", "", "", ""],
    ["N°", "Désignation", "Unité", "Qté", "Coût unitaire (Ar)", "Total (Ar)", ""],
    ["1", "Location vidéoprojecteur HD", "Jour", "6", "150 000", "data-formule='=D5*E5'", "data-format='entier'"],
    ["2", "Location ordinateur portable (démonstration)", "Jour", "2", "100 000", "data-formule='=D6*E6'", "data-format='entier'"],
    ["3", "Accès Internet haut débit (hotspot 4G)", "Jour", "6", "50 000", "data-formule='=D7*E7'", "data-format='entier'"],
    ["4", "Location micros/casques (salle de formation)", "Forfait", "1", "200 000", "data-formule='=D8*E8'", "data-format='entier'"],
    ["5", "Matériel divers (prises, rallonges, adaptateurs)", "Forfait", "1", "200 000", "data-formule='=D9*E9'", "data-format='entier'"],
    ["", "", "", "", "SOUS-TOTAL LOGISTIQUE", "data-formule='=SUM(F5:F9)'", "data-format='entier'", "data-gras='1'", "data-fond='doux'"],
]
classeur.ecrire_classeur(
    "rapport_budgetaire_MISSION-AMI-FID-59.xlsx",
    "Logistique",
    logistique,
    titre="Logistique & Matériel"
)

# ---------------------------------------------------------------------------
# FEUILLE 7 : FOURNITURES & SUPPORTS
# ---------------------------------------------------------------------------
fournitures = [
    ["FOURNITURES & SUPPORTS PÉDAGOGIQUES", "", "", "", "", "", ""],
    ["Impression et préparation des supports de formation, documents et kits participants.", "", "", "", "", "", ""],
    ["", "", "", "", "", "", ""],
    ["N°", "Désignation", "Unité", "Qté", "Coût unitaire (Ar)", "Total (Ar)", ""],
    ["1", "Impression supports de formation (couleurs)", "Exemplaire", "30", "25 000", "data-formule='=D5*E5'", "data-format='entier'"],
    ["2", "Kit participant (cahier, stylo, marque-page)", "Exemplaire", "30", "15 000", "data-formule='=D6*E6'", "data-format='entier'"],
    ["3", "Clé USB (supports numériques)", "Unité", "5", "30 000", "data-formule='=D7*E7'", "data-format='entier'"],
    ["4", "Signalétique (panneaux, affiches, paperboard)", "Forfait", "1", "150 000", "data-formule='=D8*E8'", "data-format='entier'"],
    ["5", "Fournitures de bureau (post-it, marqueurs, etc.)", "Forfait", "1", "50 000", "data-formule='=D9*E9'", "data-format='entier'"],
    ["", "", "", "", "SOUS-TOTAL FOURNITURES", "data-formule='=SUM(F5:F9)'", "data-format='entier'", "data-gras='1'", "data-fond='doux'"],
]
classeur.ecrire_classeur(
    "rapport_budgetaire_MISSION-AMI-FID-59.xlsx",
    "Fournitures",
    fournitures,
    titre="Fournitures & Supports Pédagogiques"
)

# ---------------------------------------------------------------------------
# FEUILLE 8 : COMMUNICATION & ADMIN
# ---------------------------------------------------------------------------
communication = [
    ["COMMUNICATION & COORDINATION", "", "", "", "", "", ""],
    ["Frais de communication, coordination et suivi de la mission.", "", "", "", "", "", ""],
    ["", "", "", "", "", "", ""],
    ["N°", "Désignation", "Unité", "Qté", "Coût unitaire (Ar)", "Total (Ar)", ""],
    ["1", "Téléphonie & Data (cartes SIM / surconsommation)", "Forfait", "1", "200 000", "data-formule='=D5*E5'", "data-format='entier'"],
    ["2", "Coordination avec le FID (réunions, points)", "Forfait", "1", "150 000", "data-formule='=D6*E6'", "data-format='entier'"],
    ["3", "Photographie / Enregistrement (documentation)", "Forfait", "1", "150 000", "data-formule='=D7*E7'", "data-format='entier'"],
    ["", "", "", "", "SOUS-TOTAL COMMUNICATION", "data-formule='=SUM(F5:F7)'", "data-format='entier'", "data-gras='1'", "data-fond='doux'"],
    ["", "", "", "", "", "", ""],
    ["FRAIS ADMINISTRATIFS & DIVERS", "", "", "", "", "", ""],
    ["N°", "Désignation", "Unité", "Qté", "Coût unitaire (Ar)", "Total (Ar)", ""],
    ["1", "Frais administratifs (dossier, certification, assurance)", "Forfait", "1", "500 000", "data-formule='=D11*E11'", "data-format='entier'"],
    ["2", "Imprévus mineurs", "Forfait", "1", "500 000", "data-formule='=D12*E12'", "data-format='entier'"],
    ["", "", "", "", "SOUS-TOTAL ADMIN & DIVERS", "data-formule='=SUM(F11:F12)'", "data-format='entier'", "data-gras='1'", "data-fond='doux'"],
]
classeur.ecrire_classeur(
    "rapport_budgetaire_MISSION-AMI-FID-59.xlsx",
    "Communication_Admin",
    communication,
    titre="Communication & Frais Administratifs"
)

# ---------------------------------------------------------------------------
# FEUILLE 9 : RÉCAPITULATIF FINAL AVEC FORMULES
# ---------------------------------------------------------------------------
recap = [
    ["RÉCAPITULATIF FINAL DU BUDGET", "", "", "", "", "", ""],
    ["Synthèse complète de l'ensemble des postes budgétaires de la mission MISSION-AMI-FID-59.", "", "", "", "", "", ""],
    ["", "", "", "", "", "", ""],
    ["N°", "Poste de dépense", "", "Montant (Ar)", "", "% du total", ""],
    ["1", "Honoraires intervenants", "", "12 000 000", "", "data-formule='=C5/SUM(C5:C12)'", "data-format='pourcentage'"],
    ["2", "Transport & Déplacements", "", "2 500 000", "", "data-formule='=C6/SUM(C5:C12)'", "data-format='pourcentage'"],
    ["3", "Hébergement & Restauration", "", "3 600 000", "", "data-formule='=C7/SUM(C5:C12)'", "data-format='pourcentage'"],
    ["4", "Logistique & Matériel", "", "2 000 000", "", "data-formule='=C8/SUM(C5:C12)'", "data-format='pourcentage'"],
    ["5", "Fournitures & Supports pédagogiques", "", "1 500 000", "", "data-formule='=C9/SUM(C5:C12)'", "data-format='pourcentage'"],
    ["6", "Communication & Coordination", "", "500 000", "", "data-formule='=C10/SUM(C5:C12)'", "data-format='pourcentage'"],
    ["7", "Frais administratifs & Divers", "", "1 000 000", "", "data-formule='=C11/SUM(C5:C12)'", "data-format='pourcentage'"],
    ["8", "Marge / Imprévus (5%)", "", "1 250 000", "", "data-formule='=C12/SUM(C5:C12)'", "data-format='pourcentage'"],
    ["", "TOTAL GÉNÉRAL", "", "data-formule='=SUM(C5:C12)'", "data-format='entier'", "100,0%", ""],
    ["", "", "", "", "", "", ""],
    ["HYPOTHÈSES & CONDITIONS", "", "", "", "", "", ""],
    ["Devise", "Tous les montants sont exprimés en Ariary (MGA).", "", "", "", "", ""],
    ["Période", "31 octobre au 5 novembre 2026 (6 jours calendaires, 5 jours de formation).", "", "", "", "", ""],
    ["Lieu", "Antananarivo – FID Direction Générale.", "", "", "", "", ""],
    ["Effectif formation", "Estimé à 30 participants.", "", "", "", "", ""],
    ["Marge imprévus", "5% du total hors imprévus (1 250 000 Ar).", "", "", "", "", ""],
    ["Honoraires", "Basés sur les profils enregistrés dans CARSO.", "", "", "", "", ""],
    ["Tarifs hébergement", "Hôtel 3-4 étoiles Antananarivo, tarif moyen 150 000 Ar/nuitée.", "", "", "", "", ""],
    ["Tarifs transport", "Location véhicule avec chauffeur à Antananarivo, 200 000 Ar/jour.", "", "", "", "", ""],
]
classeur.ecrire_classeur(
    "rapport_budgetaire_MISSION-AMI-FID-59.xlsx",
    "Recapitulatif_Final",
    recap,
    titre="Récapitulatif Final du Budget"
)

# ---------------------------------------------------------------------------
# FEUILLE 10 : RÉPARTITION PAR JOUR
# ---------------------------------------------------------------------------
repartition = [
    ["RÉPARTITION DU BUDGET PAR JOUR", "", "", "", "", "", ""],
    ["Coût journalier estimé pour chaque jour de la mission.", "", "", "", "", "", ""],
    ["", "", "", "", "", "", ""],
    ["Jour", "Date", "Activité", "Coût journalier estimé (Ar)", "", "", ""],
    ["Jour 1", "31/10/2026", "Installation, préparation salle, briefing équipe", "3 500 000", "", "", ""],
    ["Jour 2", "01/11/2026", "Journée de formation IA – Partie 1", "4 200 000", "", "", ""],
    ["Jour 3", "02/11/2026", "Journée de formation IA – Partie 2", "4 200 000", "", "", ""],
    ["Jour 4", "03/11/2026", "Journée de formation IA – Partie 3", "4 200 000", "", "", ""],
    ["Jour 5", "04/11/2026", "Journée de formation IA – Partie 4 + évaluation", "4 200 000", "", "", ""],
    ["Jour 6", "05/11/2026", "Restitution, clôture, déménagement", "4 050 000", "", "", ""],
    ["", "", "TOTAL", "data-formule='=D5:D10'", "data-format='entier'", "data-gras='1'", "data-fond='doux'"],
    ["", "", "", "", "", "", ""],
    ["INDICATEURS DE SUIVI", "", "", "", "", "", ""],
    ["Indicateur", "Valeur cible", "Valeur réalisée", "Écart", "", "", ""],
    ["Coût total", "24 350 000 Ar", "(à compléter)", "data-formule='=C16-B16'", "data-format='entier'", "", ""],
    ["Jours de formation", "5 jours", "(à compléter)", "data-formule='=C17-B17'", "", "", ""],
    ["Participants", "30", "(à compléter)", "data-formule='=C18-B18'", "", "", ""],
    ["Livrables produits", "5", "(à compléter)", "data-formule='=C19-B19'", "", "", ""],
]
classeur.ecrire_classeur(
    "rapport_budgetaire_MISSION-AMI-FID-59.xlsx",
    "Repartition_Jour",
    repartition,
    titre="Répartition par Jour"
)

print("✅ Rapport budgétaire généré avec succès : rapport_budgetaire_MISSION-AMI-FID-59.xlsx")
print("📊 7 feuilles avec formules, styles et mise en forme professionnelle")
print("📋 Feuilles : Couverture, Synthèse, Honoraires, Transport, Hébergement, Logistique, Fournitures, Communication, Récapitulatif, Répartition par jour")
