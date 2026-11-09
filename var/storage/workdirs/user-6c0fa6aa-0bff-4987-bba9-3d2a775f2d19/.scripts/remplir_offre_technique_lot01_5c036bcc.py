from pathlib import Path
from docx import Document
from docx.shared import Pt, RGBColor
from docx.enum.text import WD_BREAK
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

# ---------- 1. Localiser la copie du modele client dans le dossier de l'offre ----------
cwd = Path(".").resolve()
anchor_dir = cwd.parent
docx_files = [p for p in anchor_dir.rglob("*.docx") if ".tmp-run" not in str(p)]
print("DOCX trouves dans le dossier de l'offre :")
for p in docx_files:
    print("   ", p.name)

src = None
for p in docx_files:
    if "9431021d" in p.name:
        src = p
        break
if src is None:
    for p in docx_files:
        if p.name == "modele-OFFRE-TECH-L01.docx":
            src = p
            break
if src is None:
    for p in docx_files:
        if "Model_offre_technique_FID" in p.name:
            src = p
            break
if src is None and docx_files:
    src = docx_files[0]
if src is None:
    raise SystemExit("ECHEC : aucun fichier DOCX trouve dans le dossier de l'offre")
print("FICHIER SOURCE RETENU :", src.name)

doc = Document(str(src))

# ---------- 2. Couverture : remplacer les textes du modele (styles conserves) ----------
def set_par_text(p, text):
    if p.runs:
        p.runs[0].text = text
        for r in p.runs[1:]:
            r.text = ""
    else:
        p.add_run(text)

replacements = {
    "OFFRE TECNIQUE": "OFFRE TECHNIQUE",
    "LOL ICI": "Lot 01 : Formation sur l'intelligence artificielle appliquée aux usages professionnels, à l'analyse de données, aux agents IA et à l'aide à la décision",
    "Lieu + date": "Toliara, le 8 novembre 2026",
    "Entreprise (ex : CARSO)": "CARSO (Centre d'Affaires de la Région Sud-Ouest)",
}
nb_rempl = 0
for p in doc.paragraphs:
    t = p.text.strip()
    if t in replacements:
        set_par_text(p, replacements[t])
        nb_rempl += 1
print("Remplacements couverture :", nb_rempl)

# ---------- 3. reperes : Sommaire et ancre de fin ----------
sommaire = None
anchor = None
for p in doc.paragraphs:
    t = p.text.strip()
    if t.lower() == "sommaire" and sommaire is None:
        sommaire = p
    if t == "Contenus commence ici":
        anchor = p
print("Sommaire trouve :", sommaire is not None, "| Ancre trouvee :", anchor is not None)
if anchor is None:
    raise SystemExit("ECHEC : ancre 'Contenus commence ici' introuvable")

# ---------- 4. supprimer les paragraphes vides entre Sommaire et l'ancre ----------
if sommaire is not None:
    started = False
    to_del = []
    for p in doc.paragraphs:
        if p is sommaire:
            started = True
            continue
        if p is anchor:
            break
        if started and not p.text.strip():
            pPr = p._p.find(qn("w:pPr"))
            if pPr is not None and pPr.find(qn("w:sectPr")) is not None:
                continue
            to_del.append(p)
    for p in to_del:
        p._p.getparent().remove(p._p)
    print("Paragraphes vides supprimes :", len(to_del))

# ---------- 5. styles disponibles ----------
h1_style = None
h2_style = None
list_style = None
grid_style = None
for st in doc.styles:
    try:
        nm = (st.name or "").lower()
    except Exception:
        continue
    if nm in ("heading 1", "titre 1") and h1_style is None:
        h1_style = st
    if nm in ("heading 2", "titre 2") and h2_style is None:
        h2_style = st
    if nm in ("list bullet", "puces de liste") and list_style is None:
        list_style = st
    if nm in ("table grid", "grille du tableau") and grid_style is None:
        grid_style = st
if h1_style is None and sommaire is not None:
    h1_style = sommaire.style
print("Styles : h1 =", h1_style.name if h1_style is not None else None,
      "| h2 =", h2_style.name if h2_style is not None else None,
      "| liste =", list_style.name if list_style is not None else None,
      "| tableau =", grid_style.name if grid_style is not None else None)

# ---------- 6. helpers d'insertion (tout avant l'ancre) ----------
def heading(text, level):
    st = h1_style if level == 1 else h2_style
    if st is not None:
        p = anchor.insert_paragraph_before("", style=st)
        p.add_run(text)
        return p
    p = anchor.insert_paragraph_before("")
    r = p.add_run(text)
    r.bold = True
    r.font.size = Pt(14 if level == 1 else 12)
    return p

def new_par(text):
    p = anchor.insert_paragraph_before("")
    p.add_run(text)
    return p

def bullet(text):
    if list_style is not None:
        p = anchor.insert_paragraph_before("", style=list_style)
        p.add_run(text)
    else:
        p = anchor.insert_paragraph_before("")
        p.add_run("• " + text)
    return p

def set_borders(t):
    tblPr = t._tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el = OxmlElement("w:" + edge)
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), "4")
        el.set(qn("w:color"), "9CB4D6")
        borders.append(el)
    tblPr.append(borders)

def shade(cell, color):
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:fill"), color)
    tcPr.append(shd)

def add_table(entete, lignes):
    t = doc.add_table(rows=1, cols=len(entete))
    if grid_style is not None:
        t.style = grid_style
    else:
        set_borders(t)
    trPr = t.rows[0]._tr.get_or_add_trPr()
    tblHeader = OxmlElement("w:tblHeader")
    tblHeader.set(qn("w:val"), "true")
    trPr.append(tblHeader)
    for i, h in enumerate(entete):
        c = t.rows[0].cells[i]
        c.text = ""
        r = c.paragraphs[0].add_run(h)
        r.bold = True
        r.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        shade(c, "1F4E79")
    for ligne in lignes:
        cells = t.add_row().cells
        for i, v in enumerate(ligne):
            cells[i].text = ""
            cells[i].paragraphs[0].add_run(str(v))
    anchor._p.addprevious(t._tbl)
    anchor.insert_paragraph_before("")
    return t

# ---------- 7. sommaire ----------
SOMMAIRE = [
    "1. Présentation du soumissionnaire",
    "2. Compréhension des termes de référence",
    "3. Approche méthodologique",
    "4. Programme de formation détaillé",
    "5. Planification et calendrier",
    "6. Équipe de formation",
    "7. Moyens et logistique",
    "8. Livrables",
    "9. Suivi, évaluation des acquis et assurance qualité",
    "10. Analyse des risques et mesures d'atténuation",
    "11. Conclusion et engagement",
    "Annexes",
]
for line in SOMMAIRE:
    new_par(line)
pb = anchor.insert_paragraph_before("")
pb.add_run().add_break(WD_BREAK.PAGE)

# ---------- 8. corps de l'offre (source : TDR DG_25079) ----------
BODY = [
    ("h1", "1. Présentation du soumissionnaire"),
    ("p", "Le CARSO (Centre d'Affaires de la Région Sud-Ouest) est un Groupement d'Intérêt Économique (GIE) installé au sein de la Chambre de Commerce et de l'Industrie de Toliara (CCI Tsimenatsy, Toliara 1, Madagascar). Issu du programme MIARY, mis en œuvre en collaboration avec le Projet PIC (Pôles Intégrés de Croissance), le CARSO œuvre pour le développement de l'entrepreneuriat dans la région Atsimo-Andrefana."),
    ("p", "Ses services couvrent l'accompagnement des opérateurs économiques, le renforcement de compétences et l'appui stratégique aux entreprises, en vue de créer des emplois durables. Le CARSO conduit régulièrement des actions de formation et d'assistance technique au profit d'organisations publiques et privées."),
    ("p", "Coordonnées : site web carso.mg ; courriel carso@carso.mg ; téléphone +261 34 61 496 36."),
    ("p", "Langues de travail : français et malagasy, conformément aux termes de référence."),
    ("p", "Références de missions similaires et attestations : (à compléter, à joindre en annexe)."),

    ("h1", "2. Compréhension des termes de référence"),
    ("p", "Le Fonds d'Intervention pour le Développement (FID), organisme public financé par la Banque mondiale, lance l'AMI N°59/AMI/FID/FSR/SQC_CAB_FORM_IA en vue de recruter un prestataire chargé de dispenser une formation sur l'intelligence artificielle appliquée aux usages professionnels, à l'analyse de données, aux agents IA et à l'aide à la décision, au profit de ses équipes centrales."),
    ("p", "La formation se déroule en présentiel sur 7 jours ouvrables, répartis en deux vagues distinctes : 4 jours pour la première, 3 jours pour la seconde. Le lieu de formation sera communiqué ultérieurement par le FID (les termes de référence mentionnent Ampefy en pied de page)."),
    ("table", ["Vague", "Public cible", "Effectif", "Durée", "Niveau visé"], [
        ["Vague 1", "Équipes SI et PSE du FID", "Environ 24 participants (9 Suivi-Évaluation, 15 Systèmes d'Information)", "4 jours", "Application pratique : automatisation, analyse de données, agents IA"],
        ["Vague 2", "Directeurs et chefs de service", "Environ 30 participants", "3 jours", "Initiation, sans prérequis technique (pas d'API, pas de scripts)"],
    ]),
    ("p", "Attentes principales des termes de référence : une démarche pratique et participative, adaptée à chaque vague ; des démonstrations, des exercices et des ateliers ; l'utilisation exclusive de données fictives, anonymisées ou validées par le FID ; une évaluation des acquis à l'issue de chaque vague ; des supports et animations en français et en malagasy."),

    ("h1", "3. Approche méthodologique"),
    ("p", "L'approche proposée est résolument pratique : chaque notion est d'abord démontrée par le formateur, puis immédiatement mise en pratique par les participants sur des cas tirés de leurs propres métiers, à partir de données fictives, anonymisées ou validées par le FID. La théorie reste au service de la pratique et ne dépasse jamais le niveau nécessaire à l'usage professionnel des outils."),
    ("h2", "Principes directeurs"),
    ("li", "Au moins 70 % du temps consacré à la pratique : démonstrations, exercices individuels, ateliers en sous-groupes."),
    ("li", "Supports et échanges en français et en malagasy, pour garantir la compréhension de tous les participants."),
    ("li", "Progression adaptée à chaque vague : filières SI et PSE pour la vague 1, cas managériaux et aide à la décision pour la vague 2."),
    ("li", "Confidentialité : aucune donnée réelle identifiante n'est utilisée sans validation préalable du FID."),
    ("li", "Évaluation des acquis à l'issue de chaque vague, conformément aux termes de référence."),
    ("h2", "Phases de mise en œuvre"),
    ("table", ["Phase", "Contenu", "Durée", "Livrable associé"], [
        ["Phase 1 : Préparation et cadrage", "Note méthodologique, validation du plan de travail avec le FID, préparation des supports et des environnements pratiques", "(à confirmer avec le FID)", "Note méthodologique"],
        ["Phase 2 : Vague 1 (SI et PSE)", "4 jours de formation en présentiel, filières SI et PSE", "4 jours ouvrables", "Supports, exercices, résultats d'évaluation"],
        ["Phase 3 : Vague 2 (Dirigeants)", "3 jours de formation en présentiel, initiation et aide à la décision", "3 jours ouvrables", "Supports, exercices, résultats d'évaluation"],
        ["Phase 4 : Consolidation", "Analyse des évaluations, rapport final, restitution au FID", "(à confirmer avec le FID)", "Rapport final"],
    ]),

    ("h1", "4. Programme de formation détaillé"),
    ("h2", "Vague 1 : équipes SI et PSE (4 jours)"),
    ("table", ["Jour", "Thème", "Contenus principaux"], [
        ["J1", "Fondamentaux de l'IA", "Panorama de l'IA et de l'IA générative ; cas d'usage professionnels ; risques, limites et biais ; bonnes pratiques d'utilisation responsable"],
        ["J2", "IA et analyse de données", "Préparation et exploration de données ; analyse assistée par IA ; production de synthèses et de visualisations ; exercices sur données fictives ou anonymisées"],
        ["J3", "Agents IA et automatisation", "Notion d'agent IA ; automatisation de tâches récurrentes ; chaînes de traitement ; initiation aux agents IA intégrés au SI ; sécurité"],
        ["J4", "Ateliers spécialisés", "Filière SI : automatisation des tâches, flux de données, scripts, agents IA intégrés au SI, sécurité. Filière PSE : planification, suivi des indicateurs, analyse des écarts, rapports, tableaux de bord, notes d'aide à la décision"],
    ]),
    ("h2", "Vague 2 : directeurs et chefs de service (3 jours)"),
    ("table", ["Jour", "Thème", "Contenus principaux"], [
        ["J1", "Initiation à l'IA générative", "Découverte des outils ; rédaction de prompts efficaces ; premiers usages au quotidien ; aucun prérequis technique"],
        ["J2", "Usages professionnels et managériaux", "IA au service du management : rédaction, synthèse de documents, préparation de réunions, communication, veille"],
        ["J3", "Aide à la décision", "Ateliers sur cas professionnels ; analyse et synthèse pour la décision ; confidentialité, sécurité et validation humaine des productions de l'IA"],
    ]),
    ("p", "Le programme détaillé séance par séance (horaires, exercices, supports) sera précisé dans la note méthodologique (livrable 1) et validé avec le FID avant le démarrage de la formation."),

    ("h1", "5. Planification et calendrier"),
    ("p", "La mission couvre 7 jours ouvrables de formation en présentiel. Les dates exactes des deux vagues seront convenues avec le FID ; un intervalle entre les deux vagues permettra d'intégrer les enseignements de la première dans l'animation de la seconde."),
    ("table", ["Étape", "Activité", "Durée", "Échéance"], [
        ["1", "Note méthodologique et cadrage", "(à confirmer avec le FID)", "(à convenir avec le FID)"],
        ["2", "Vague 1 : équipes SI et PSE", "4 jours ouvrables", "(à convenir avec le FID)"],
        ["3", "Vague 2 : directeurs et chefs de service", "3 jours ouvrables", "(à convenir avec le FID)"],
        ["4", "Évaluation des acquis, rapport final", "(à confirmer avec le FID)", "(à convenir avec le FID)"],
    ]),

    ("h1", "6. Équipe de formation"),
    ("p", "L'équipe ci-dessous est pressentie pour la mission. Les participations restent à confirmer et les CV détaillés, établis au regard des qualifications exigées par les termes de référence, seront joints en annexe."),
    ("table", ["Fonction", "Personne pressentie", "Rôle dans la mission", "Statut"], [
        ["Formation, vague 1 (filière SI)", "Sara Amrani", "Conception et animation des modules SI : automatisation, flux de données, agents IA", "Pressentie, à confirmer"],
        ["Formation, vague 1 (filière PSE)", "Fatou Ndiaye", "Animation des modules suivi-évaluation : indicateurs, tableaux de bord, aide à la décision", "Pressentie, à confirmer"],
        ["Formation, vague 2", "Jaque Jean", "Animation de l'initiation à l'IA générative et des ateliers managériaux", "Pressenti, à confirmer"],
        ["Appui méthodologique", "Voahangy Rakotomalala", "Appui aux modules de gestion, contrôle interne et aide à la décision", "Pressentie, à confirmer"],
        ["Appui logistique", "Karim Benali", "Préparation matérielle et coordination sur le lieu de formation", "Pressenti, à confirmer"],
        ["Appui logistique", "Paulette Nirina", "Logistique, accueil et suivi des participants", "Pressentie, à confirmer"],
    ]),
    ("p", "Qualifications détaillées, expériences et références de chaque membre : (à compléter, voir CV en annexe)."),

    ("h1", "7. Moyens et logistique"),
    ("p", "Conformément aux termes de référence, le FID prend en charge les déplacements des formateurs depuis Antananarivo ainsi que la logistique générale de la formation. Le CARSO prend en charge les indemnités d'hébergement, de restauration et de séjour de son équipe."),
    ("table", ["Élément", "Prise en charge", "Précisions"], [
        ["Déplacements des formateurs depuis Antananarivo", "FID", "Selon les termes de référence"],
        ["Logistique générale (salle, pauses, etc.)", "FID", "Lieu de formation communiqué ultérieurement (les TDR mentionnent Ampefy)"],
        ["Indemnités d'hébergement, de restauration et de séjour de l'équipe", "CARSO", "Selon les termes de référence"],
        ["Salle équipée : vidéoprojecteur, connexion Internet", "À préciser avec le FID", "Dispositif détaillé dans la note méthodologique"],
        ["Ordinateurs des participants", "À préciser avec le FID", "(à confirmer avec le FID)"],
        ["Accès aux plateformes et outils d'IA", "CARSO", "Comptes et accès préparés et testés avant la formation ; alternatives prévues en cas de connectivité limitée"],
        ["Supports électroniques bilingues (français, malagasy)", "CARSO", "Remis aux participants de chaque vague"],
    ]),

    ("h1", "8. Livrables"),
    ("table", ["N°", "Livrable", "Description", "Échéance"], [
        ["1", "Note méthodologique", "Approche pédagogique, programme détaillé par vague, modalités d'évaluation des acquis", "Avant le démarrage de la formation"],
        ["2", "Supports électroniques", "Supports de formation par vague, en français et en malagasy", "À chaque vague"],
        ["3", "Exercices et modèles", "Exercices pratiques, modèles, fiches d'automatisation, modèles de prompts, études de cas", "À chaque vague"],
        ["4", "Note de bonnes pratiques", "Utilisation responsable, sécurisée et éthique de l'IA, à l'attention des équipes du FID", "Avec les supports de formation"],
        ["5", "Résultats d'évaluation des acquis", "Par vague, avec analyse des résultats et recommandations", "Après chaque vague"],
        ["6", "Rapport final", "Synthèse de l'ensemble de la mission, résultats, difficultés et recommandations", "À la fin de la mission"],
    ]),

    ("h1", "9. Suivi, évaluation des acquis et assurance qualité"),
    ("p", "Un dispositif de suivi simple et vérifiable est mis en place pour chaque vague : évaluation de positionnement en début de vague, feuille de présence quotidienne, évaluation des acquis en fin de vague (exigée par les termes de référence) et questionnaire de satisfaction."),
    ("li", "Taux de présence par jour et par vague."),
    ("li", "Taux de réussite à l'évaluation des acquis."),
    ("li", "Taux de satisfaction des participants."),
    ("li", "Nombre d'exercices et d'ateliers réalisés par participant."),
    ("p", "Un point de coordination est organisé avec le point focal du FID avant chaque vague et à l'issue de chaque vague. Les ajustements de rythme, de contenus ou de composition des groupes sont actés lors de ces points. Un rapport est remis à l'issue de chaque vague, puis consolidé dans le rapport final."),

    ("h1", "10. Analyse des risques et mesures d'atténuation"),
    ("table", ["Risque identifié", "Impact potentiel", "Mesure d'atténuation"], [
        ["Indisponibilité partielle des participants aux dates retenues", "Décalage du calendrier", "Planification anticipée des dates avec le FID ; listes de participants confirmées avant chaque vague"],
        ["Connectivité Internet limitée sur le lieu de formation", "Exercices en ligne perturbés", "Supports et exercices téléchargeables en amont ; alternatives hors ligne ; outils ne nécessitant pas de connexion"],
        ["Hétérogénéité des niveaux au sein d'une vague", "Rythme inadapté à une partie des participants", "Évaluation de positionnement en début de vague ; sous-groupes par niveau ; tutorat entre pairs"],
        ["Accès restreint à certains outils d'IA depuis le site", "Exercices bloqués", "Comptes et accès préparés et testés avant la formation ; outils de substitution identifiés"],
        ["Confidentialité des données du FID", "Risque de fuite de données", "Utilisation exclusive de données fictives, anonymisées ou validées par le FID ; rappel des règles de confidentialité en ouverture de chaque vague"],
    ]),

    ("h1", "11. Conclusion et engagement"),
    ("p", "Le CARSO confirme sa compréhension des termes de référence de l'AMI N°59/AMI/FID/FSR/SQC_CAB_FORM_IA, Lot 01, et son engagement à dérouler les 7 jours ouvrables de formation en deux vagues, selon l'approche pratique et participative décrite dans la présente offre."),
    ("p", "L'équipe pressentie, les moyens mobilisés et le dispositif de suivi décrits dans ce document visent la montée en compétences effective des équipes SI, PSE et de direction du FID, dans le respect des délais et des exigences de confidentialité."),
    ("p", "Le CARSO reste à la disposition du FID pour tout complément d'information et pour la validation du plan de travail détaillé."),

    ("h1", "Annexes"),
    ("li", "CV détaillés des formateurs pressentis : (à joindre)."),
    ("li", "Références de missions similaires et attestations : (à joindre)."),
    ("li", "Documents administratifs du soumissionnaire : (à joindre)."),
]

for item in BODY:
    kind = item[0]
    if kind == "h1":
        heading(item[1], 1)
    elif kind == "h2":
        heading(item[1], 2)
    elif kind == "p":
        new_par(item[1])
    elif kind == "li":
        bullet(item[1])
    elif kind == "table":
        add_table(item[1], item[2])

# ---------- 9. retirer l'ancre ----------
pPr = anchor._p.find(qn("w:pPr"))
has_sect = pPr is not None and pPr.find(qn("w:sectPr")) is not None
if has_sect:
    set_par_text(anchor, "")
else:
    anchor._p.getparent().remove(anchor._p)

# ---------- 10. sauvegarde ----------
out = Path("Offre_technique_CARSO_FID_AMI59_Lot01.docx")
doc.save(str(out))
print("SAUVEGARDE :", out.name, "|", out.stat().st_size, "octets")
print("Contenu final : paragraphes =", len(doc.paragraphs), "| tableaux =", len(doc.tables))
print("TERMINE")