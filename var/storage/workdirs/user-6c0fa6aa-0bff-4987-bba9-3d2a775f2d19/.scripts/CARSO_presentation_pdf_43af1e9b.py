from fpdf import FPDF
import math

class CARSO_PDF(FPDF):
    def __init__(self):
        super().__init__('P', 'mm', 'Landscape')
        self.set_auto_page_break(auto=False, margin=0)
        
    def add_slide(self):
        self.add_page()
    
    def header_bar(self, title, number=""):
        # Barre sombre en haut
        self.set_fill_color(10, 22, 40)
        self.rect(0, 0, 297, 35, 'F')
        
        if number:
            self.set_fill_color(13, 124, 111)
            self.set_font('Helvetica', 'B', 48)
            self.set_text_color(13, 124, 111)
            self.set_xy(15, 5)
            self.cell(30, 25, number, align='L')
        
        self.set_text_color(255, 255, 255)
        self.set_font('Helvetica', 'B', 32)
        self.set_xy(50 if number else 15, 8)
        self.cell(230, 20, title, align='L')
        
        # Ligne accent
        self.set_draw_color(13, 124, 111)
        self.set_line_width(1.5)
        self.line(50 if number else 15, 30, 280 if number else 270, 30)
        self.set_line_width(0.5)
    
    def card(self, x, y, w, h, title, desc, icon="", accent=True):
        # Fond blanc
        self.set_fill_color(248, 249, 250)
        self.set_draw_color(200, 210, 220)
        self.set_line_width(0.3)
        # Rectangle arrondi simulé
        self.rect(x, y, w, h, 'DF')
        
        if accent:
            self.set_fill_color(13, 124, 111)
            self.rect(x, y, 3, h, 'F')
        
        if icon:
            self.set_font('Helvetica', '', 20)
            self.set_text_color(50, 50, 50)
            self.set_xy(x + 5, y + 3)
            self.cell(w - 15, 8, icon)
        
        self.set_font('Helvetica', 'B', 13)
        self.set_text_color(13, 124, 111)
        self.set_xy(x + 5, y + (11 if icon else 3))
        self.cell(w - 10, 7, title)
        
        self.set_font('Helvetica', '', 10)
        self.set_text_color(80, 80, 80)
        self.set_xy(x + 5, y + (20 if icon else 11))
        self.multi_cell(w - 10, 4.5, desc)
    
    def value_card(self, x, y, w, h, icon, title, desc):
        self.card(x, y, w, h, title, desc, icon=icon)
    
    def dark_box(self, x, y, w, h, title, lines, title_color=(78, 205, 196)):
        self.set_fill_color(10, 22, 40)
        self.rect(x, y, w, h, 'F')
        
        self.set_font('Helvetica', 'B', 18)
        self.set_text_color(*title_color)
        self.set_xy(x + 8, y + 8)
        self.cell(w - 16, 8, title)
        
        self.set_font('Helvetica', '', 11)
        self.set_text_color(200, 220, 220)
        for i, line in enumerate(lines):
            self.set_xy(x + 8, y + 20 + i * 8)
            self.cell(w - 16, 7, line)

# ===== COULEURS =====
TEAL = (13, 124, 111)
LIGHT_TEAL = (78, 205, 196)
DARK_BLUE = (10, 22, 40)
MEDIUM_BLUE = (26, 58, 92)
WHITE = (255, 255, 255)
LIGHT_GRAY = (248, 249, 250)
DARK_TEXT = (10, 22, 40)
BODY_TEXT = (50, 50, 50)
SUBTITLE = (85, 85, 85)

pdf = CARSO_PDF()

# ==================== SLIDE 1 : COUVERTURE ====================
pdf.add_slide()
# Fond dégradé simulé
pdf.set_fill_color(*DARK_BLUE)
pdf.rect(0, 0, 297, 210, 'F')
# Overlay droit
pdf.set_fill_color(*TEAL)
pdf.rect(150, 0, 147, 210, 'F')
pdf.set_fill_color(13, 124, 111)
pdf.rect(150, 0, 147, 210, 'F')

# Cercles décoratifs
pdf.set_fill_color(13, 124, 111)
pdf.ellipse(200, -40, 280, 80)
pdf.set_fill_color(*MEDIUM_BLUE)
pdf.ellipse(-20, 120, 60, 200)

# Titre
pdf.set_text_color(*WHITE)
pdf.set_font('Helvetica', 'B', 64)
pdf.set_xy(80, 50)
pdf.cell(137, 30, 'CARSO', align='C')

pdf.set_font('Helvetica', '', 24)
pdf.set_text_color(*LIGHT_TEAL)
pdf.set_xy(80, 85)
pdf.cell(137, 15, 'Centre d\'Affaires du Sud-Ouest', align='C')

# Ligne
pdf.set_draw_color(*LIGHT_TEAL)
pdf.set_line_width(2)
pdf.line(110, 105, 187, 105)
pdf.set_line_width(0.5)

pdf.set_font('Helvetica', '', 14)
pdf.set_text_color(200, 220, 220)
pdf.set_xy(30, 115)
pdf.multi_cell(237, 7, 'Groupement d\'Intérêt Économique — Au service du développement entrepreneurial\nde la région Atsimo Andrefana, Madagascar', align='C')

pdf.set_font('Helvetica', '', 10)
pdf.set_text_color(130, 150, 150)
pdf.set_xy(80, 185)
pdf.cell(137, 10, 'PRÉSENTATION INSTITUTIONNELLE — 2026', align='C')

# ==================== SLIDE 2 : SOMMAIRE ====================
pdf.add_slide()
pdf.set_fill_color(*LIGHT_GRAY)
pdf.rect(0, 0, 297, 210, 'F')
pdf.header_bar('Sommaire', '00')

items = [
    ("01", "Qui sommes-nous ?", "Présentation de CARSO : historique, mission et vision du Centre d'Affaires du Sud-Ouest de Madagascar."),
    ("02", "Nos valeurs", "Innovation, Professionnalisme et Satisfaction Client — les piliers de notre action."),
    ("03", "Nos secteurs d'intervention", "Agriculture, élevage, transformation agroalimentaire, aquaculture, énergie verte et plus encore."),
    ("04", "Notre approche", "Accompagnement sur mesure des porteurs de projets pour la création d'emplois et de revenus stables."),
    ("05", "Projets phares", "Focus sur les projets emblématiques : manioc, moringa, ricin, baobab, dattier, sisal."),
    ("06", "Nos partenaires", "Collaboration avec le Projet PIC, Programme MIARY et les acteurs locaux."),
]

for i, (num, title, desc) in enumerate(items):
    col = i % 3
    row = i // 3
    x = 15 + col * 92
    y = 42 + row * 75
    
    pdf.set_fill_color(*WHITE)
    pdf.set_draw_color(200, 210, 220)
    pdf.rect(x, y, 88, 68, 'DF')
    pdf.set_fill_color(*TEAL)
    pdf.rect(x, y, 3, 68, 'F')
    
    pdf.set_font('Helvetica', 'B', 16)
    pdf.set_text_color(*TEAL)
    pdf.set_xy(x + 6, y + 4)
    pdf.cell(15, 8, num)
    
    pdf.set_font('Helvetica', 'B', 12)
    pdf.set_text_color(*DARK_TEXT)
    pdf.set_xy(x + 25, y + 4)
    pdf.cell(58, 8, title)
    
    pdf.set_font('Helvetica', '', 9)
    pdf.set_text_color(*SUBTITLE)
    pdf.set_xy(x + 6, y + 16)
    pdf.multi_cell(76, 4.2, desc)

# ==================== SLIDE 3 : QUI SOMMES-NOUS ====================
pdf.add_slide()
pdf.set_fill_color(*LIGHT_GRAY)
pdf.rect(0, 0, 297, 210, 'F')
pdf.header_bar('Qui sommes-nous ?', '01')

cards_data = [
    ("Identité", "Le CARSO (Centre d'Affaires de la Région Sud-Ouest) est un Groupement d'Intérêt Économique (GIE) constitué juridiquement pour accompagner le développement entrepreneurial de la zone du Sud-Ouest de Madagascar."),
    ("Mission", "Développer des produits et services d'appui en faveur de sa clientèle dans des perspectives de création réelle d'emplois et de génération de revenus stables, répondant aux préoccupations principales de la région Atsimo Andrefana."),
    ("Zone d'intervention", "Région Atsimo Andrefana (Sud-Ouest) — Madagascar. Le CARSO couvre les districts de Toliara, Bezaha, Tanandava, Miary, Ankililoaka et les communes rurales environnantes."),
]

for i, (title, desc) in enumerate(cards_data):
    y = 42 + i * 42
    pdf.set_fill_color(*WHITE)
    pdf.set_draw_color(200, 210, 220)
    pdf.rect(15, y, 170, 38, 'DF')
    pdf.set_fill_color(*TEAL)
    pdf.rect(15, y, 3, 38, 'F')
    
    pdf.set_font('Helvetica', 'B', 13)
    pdf.set_text_color(*TEAL)
    pdf.set_xy(22, y + 4)
    pdf.cell(158, 7, title)
    
    pdf.set_font('Helvetica', '', 10)
    pdf.set_text_color(*BODY_TEXT)
    pdf.set_xy(22, y + 13)
    pdf.multi_cell(160, 4.5, desc)

# Colonne droite - Vision
pdf.set_fill_color(*DARK_BLUE)
pdf.rect(195, 42, 90, 155, 'F')
pdf.set_font('Helvetica', 'B', 18)
pdf.set_text_color(*LIGHT_TEAL)
pdf.set_xy(200, 50)
pdf.cell(80, 10, 'Notre Vision')

pdf.set_font('Helvetica', 'B', 12)
pdf.set_text_color(*WHITE)
pdf.set_xy(200, 65)
pdf.cell(80, 8, "Être l'acteur incontournable du")
pdf.set_xy(200, 73)
pdf.cell(80, 8, "développement de l'entrepreneuriat régional.")

pdf.set_font('Helvetica', '', 10)
pdf.set_text_color(200, 220, 220)
pdf.set_xy(200, 85)
pdf.multi_cell(80, 5, "Stimuler l'économie locale à travers un accompagnement structuré des initiatives privées, en favorisant la valorisation des ressources naturelles de la région.")

pdf.set_font('Helvetica', 'B', 14)
pdf.set_text_color(*LIGHT_TEAL)
pdf.set_xy(200, 130)
pdf.cell(80, 8, 'Carso mikarakara soa !')
pdf.set_font('Helvetica', '', 9)
pdf.set_text_color(130, 150, 150)
pdf.set_xy(200, 138)
pdf.cell(80, 6, 'Carso takes care so!')

# ==================== SLIDE 4 : NOS VALEURS ====================
pdf.add_slide()
pdf.set_fill_color(*LIGHT_GRAY)
pdf.rect(0, 0, 297, 210, 'F')
pdf.header_bar('Nos Valeurs', '02')

valeurs = [
    ("Innovation", "Nous encourageons la créativité et l'adoption de solutions nouvelles pour valoriser les ressources locales et répondre aux défis du développement régional.", "💡"),
    ("Professionnalisme", "Nos équipes mettent en œuvre des méthodes rigoureuses et des standards de qualité pour accompagner efficacement chaque porteur de projet.", "🏆"),
    ("Satisfaction Client", "La réussite de nos bénéficiaires est notre priorité. Nous nous engageons à fournir un accompagnement sur mesure et un suivi personnalisé.", "🤝"),
]

for i, (title, desc, icon) in enumerate(valeurs):
    x = 15 + i * 92
    pdf.set_fill_color(*WHITE)
    pdf.set_draw_color(200, 210, 220)
    pdf.rect(x, 42, 88, 145, 'DF')
    pdf.set_fill_color(*TEAL)
    pdf.rect(x, 42, 88, 4, 'F')
    
    pdf.set_font('Helvetica', '', 40)
    pdf.set_text_color(*BODY_TEXT)
    pdf.set_xy(x + 30, 50)
    pdf.cell(30, 20, icon, align='C')
    
    pdf.set_font('Helvetica', 'B', 18)
    pdf.set_text_color(*DARK_TEXT)
    pdf.set_xy(x + 5, 75)
    pdf.cell(78, 10, title, align='C')
    
    pdf.set_font('Helvetica', '', 10)
    pdf.set_text_color(*SUBTITLE)
    pdf.set_xy(x + 5, 88)
    pdf.multi_cell(78, 5, desc, align='C')

# ==================== SLIDE 5 : SECTEURS ====================
pdf.add_slide()
pdf.set_fill_color(*LIGHT_GRAY)
pdf.rect(0, 0, 297, 210, 'F')
pdf.header_bar('Nos Secteurs d\'Intervention', '03')

secteurs = [
    ("Élevage & Aviculture", "Production de fois gras, magret, cuisse et carcasse de canard. Élevage de vache laitière race PRN. Élevage caprin pour saucisse chipolata 100% chèvre.", "🦆"),
    ("Transformation Agroalimentaire", "Farine de manioc haute qualité, transformation de banane, stevia, tomate, patate douce en chips, maïs et mils. Production de nouilles.", "🌿"),
    ("Aquaculture", "Aquaculture intégrée poisson-canard à Mampanarivo. Élevage de tilapia monosex male. Création de fermes piscicoles à Bezaha.", "🐟"),
    ("Filières Spécifiques", "Huile de ricin (kinagna), Moringa du Sud, fibre de bananier, romba (huile essentielle), baobab (huile, poudre, savon), sisal.", "🌳"),
    ("Agriculture & Arboriculture", "Plantation de palmiers dattiers et transformation de dattes. Verger à Antanilehibe. Extension du site d'exploitation des mils, pois du cap et lentilles.", "🌴"),
    ("Produits Dérivés", "Savon barre à partir d'huile essentielle, shampoing solide, charcuterie, farine de poisson, fourrage, vinaigre de datte.", "🧴"),
]

for i, (title, desc, icon) in enumerate(secteurs):
    col = i % 3
    row = i // 3
    x = 15 + col * 92
    y = 42 + row * 75
    
    pdf.set_fill_color(*WHITE)
    pdf.set_draw_color(200, 210, 220)
    pdf.rect(x, y, 88, 68, 'DF')
    pdf.set_fill_color(*TEAL)
    pdf.rect(x, y, 3, 68, 'F')
    
    pdf.set_font('Helvetica', '', 20)
    pdf.set_text_color(*BODY_TEXT)
    pdf.set_xy(x + 5, y + 3)
    pdf.cell(15, 10, icon)
    
    pdf.set_font('Helvetica', 'B', 12)
    pdf.set_text_color(*DARK_TEXT)
    pdf.set_xy(x + 22, y + 3)
    pdf.cell(62, 8, title)
    
    pdf.set_font('Helvetica', '', 9)
    pdf.set_text_color(*SUBTITLE)
    pdf.set_xy(x + 5, y + 15)
    pdf.multi_cell(78, 4.2, desc)

# ==================== SLIDE 6 : PROJETS PHARES ====================
pdf.add_slide()
pdf.set_fill_color(*LIGHT_GRAY)
pdf.rect(0, 0, 297, 210, 'F')
pdf.header_bar('Projets Phares', '04')

projets = [
    ("Unité de Farine de Manioc", "Mise en place d'une unité de fabrication de farine de manioc de haute qualité à Mitsinjo Betanimena, Commune urbaine de Tuléar.", "🏭"),
    ("Moringa du Sud", "Transformation de feuilles de Moringa en produits finis à haute valeur nutritionnelle et commerciale.", "🌱"),
    ("Huile de Ricin — Kinagna", "Création d'un atelier de production d'huile de ricin et unité de production dans la région Atsimo Andrefana.", "🫒"),
    ("Filière Baobab", "Transformation complète : fruits en poudre, huile végétale, savon de toilette et shampoing solide.", "🌳"),
    ("Filière Dattier de Tuléar", "Plantation de palmiers dattiers, transformation en datte en barquette et vinaigre de datte (Ambohitsabo, Toliara I).", "🥥"),
    ("Projet Sisal — Telecom", "Plantation de sisal (Agave sisalana) et production de fibres pour applications industrielles.", "🧶"),
]

for i, (title, desc, icon) in enumerate(projets):
    col = i % 3
    row = i // 3
    x = 15 + col * 92
    y = 42 + row * 75
    
    pdf.set_fill_color(*WHITE)
    pdf.set_draw_color(200, 210, 220)
    pdf.rect(x, y, 88, 68, 'DF')
    pdf.set_fill_color(*TEAL)
    pdf.rect(x, y, 3, 68, 'F')
    
    pdf.set_font('Helvetica', '', 20)
    pdf.set_text_color(*BODY_TEXT)
    pdf.set_xy(x + 5, y + 3)
    pdf.cell(15, 10, icon)
    
    pdf.set_font('Helvetica', 'B', 12)
    pdf.set_text_color(*DARK_TEXT)
    pdf.set_xy(x + 22, y + 3)
    pdf.cell(62, 8, title)
    
    pdf.set_font('Helvetica', '', 9)
    pdf.set_text_color(*SUBTITLE)
    pdf.set_xy(x + 5, y + 15)
    pdf.multi_cell(78, 4.2, desc)

# ==================== SLIDE 7 : NOTRE APPROCHE ====================
pdf.add_slide()
pdf.set_fill_color(*LIGHT_GRAY)
pdf.rect(0, 0, 297, 210, 'F')
pdf.header_bar('Notre Approche', '05')

approach = [
    ("Accompagnement Global", "Le CARSO accompagne toutes les personnes habitant dans la région Atsimo Andrefana dans la réalisation de leurs projets, de l'idée initiale jusqu'à la mise en œuvre opérationnelle."),
    ("Double Objectif", "Création réelle d'emplois + Génération de revenus stables — chaque projet est évalué selon son impact socio-économique sur la communauté locale."),
]

for i, (title, desc) in enumerate(approach):
    y = 42 + i * 42
    pdf.set_fill_color(*WHITE)
    pdf.set_draw_color(200, 210, 220)
    pdf.rect(15, y, 170, 38, 'DF')
    pdf.set_fill_color(*TEAL)
    pdf.rect(15, y, 3, 38, 'F')
    
    pdf.set_font('Helvetica', 'B', 13)
    pdf.set_text_color(*TEAL)
    pdf.set_xy(22, y + 4)
    pdf.cell(158, 7, title)
    
    pdf.set_font('Helvetica', '', 10)
    pdf.set_text_color(*BODY_TEXT)
    pdf.set_xy(22, y + 13)
    pdf.multi_cell(160, 4.5, desc)

# Méthode
pdf.set_fill_color(*DARK_BLUE)
pdf.rect(195, 42, 90, 155, 'F')
pdf.set_font('Helvetica', 'B', 18)
pdf.set_text_color(*LIGHT_TEAL)
pdf.set_xy(200, 50)
pdf.cell(80, 10, 'Notre Méthode')

methodes = [
    "1. Identification des ressources locales valorisables",
    "2. Accompagnement technique et financier des porteurs de projets",
    "3. Mise en place d'unités de production et de transformation",
    "4. Suivi et évaluation de l'impact socio-économique",
    "5. Élargissement progressif de la gamme de produits et des bénéficiaires",
]

pdf.set_font('Helvetica', '', 10)
pdf.set_text_color(200, 220, 220)
for i, m in enumerate(methodes):
    pdf.set_xy(200, 65 + i * 12)
    pdf.cell(80, 10, m)

# ==================== SLIDE 8 : PARTENAIRES ====================
pdf.add_slide()
pdf.set_fill_color(*LIGHT_GRAY)
pdf.rect(0, 0, 297, 210, 'F')
pdf.header_bar('Nos Partenaires', '06')

partenaires = [
    ("Projet PIC", "Programme MIARY", "🏛️"),
    ("Région Atsimo Andrefana", "Collectivité territoriale", "🌍"),
    ("Porteurs de Projets", "Entrepreneurs locaux", "👥"),
    ("Acteurs Locaux", "Fokontany & CR", "🤝"),
    ("Institutions Financières", "Microfinance & Banques", "📊"),
]

for i, (name, desc, icon) in enumerate(partenaires):
    x = 15 + i * 56
    pdf.set_fill_color(*WHITE)
    pdf.set_draw_color(200, 210, 220)
    pdf.rect(x, 42, 50, 70, 'DF')
    
    pdf.set_font('Helvetica', '', 28)
    pdf.set_text_color(*BODY_TEXT)
    pdf.set_xy(x + 15, 48)
    pdf.cell(20, 12, icon, align='C')
    
    pdf.set_font('Helvetica', 'B', 10)
    pdf.set_text_color(*DARK_TEXT)
    pdf.set_xy(x + 2, 65)
    pdf.cell(46, 7, name, align='C')
    
    pdf.set_font('Helvetica', '', 8)
    pdf.set_text_color(*SUBTITLE)
    pdf.set_xy(x + 2, 73)
    pdf.cell(46, 6, desc, align='C')

# Citation
pdf.set_fill_color(232, 248, 245)
pdf.set_draw_color(*TEAL)
pdf.set_line_width(1)
pdf.rect(15, 125, 267, 35, 'DF')
pdf.set_fill_color(*TEAL)
pdf.rect(15, 125, 267, 2, 'F')

pdf.set_font('Helvetica', 'I', 12)
pdf.set_text_color(*DARK_TEXT)
pdf.set_xy(30, 135)
pdf.multi_cell(237, 6, '"La première collaboration avec le Projet PIC à travers le Programme MIARY a permis au CARSO de se constituer un capital de démarrage."', align='C')

# ==================== SLIDE 9 : CONTACT ====================
pdf.add_slide()
pdf.set_fill_color(*DARK_BLUE)
pdf.rect(0, 0, 297, 210, 'F')
pdf.set_fill_color(*TEAL)
pdf.rect(150, 0, 147, 210, 'F')

# Cercles
pdf.set_fill_color(13, 124, 111)
pdf.ellipse(-20, -20, 50, 50)
pdf.set_fill_color(*MEDIUM_BLUE)
pdf.ellipse(230, 130, 300, 230)

pdf.set_font('Helvetica', 'B', 42)
pdf.set_text_color(*WHITE)
pdf.set_xy(80, 30)
pdf.cell(137, 20, 'Rejoignez-nous', align='C')

pdf.set_draw_color(*LIGHT_TEAL)
pdf.set_line_width(2)
pdf.line(110, 55, 187, 55)
pdf.set_line_width(0.5)

contacts = [
    ("📍", "Adresse", "Tuléara, Région Atsimo Andrefana\nMadagascar"),
    ("🌐", "Site Web", "www.carso.mg"),
    ("📧", "Email", "contact@carso.mg"),
]

for i, (icon, label, value) in enumerate(contacts):
    x = 30 + i * 80
    pdf.set_font('Helvetica', '', 28)
    pdf.set_text_color(*WHITE)
    pdf.set_xy(x + 20, 65)
    pdf.cell(40, 15, icon, align='C')
    
    pdf.set_font('Helvetica', '', 9)
    pdf.set_text_color(130, 150, 150)
    pdf.set_xy(x + 5, 85)
    pdf.cell(70, 7, label, align='C')
    
    pdf.set_font('Helvetica', '', 11)
    pdf.set_text_color(*WHITE)
    pdf.set_xy(x + 5, 93)
    pdf.multi_cell(70, 5, value, align='C')

pdf.set_font('Helvetica', 'I', 22)
pdf.set_text_color(*LIGHT_TEAL)
pdf.set_xy(60, 140)
pdf.cell(177, 15, 'Carso mikarakara soa !', align='C')

pdf.set_font('Helvetica', '', 10)
pdf.set_text_color(130, 150, 150)
pdf.set_xy(60, 165)
pdf.cell(177, 10, 'CARSO — Centre d\'Affaires du Sud-Ouest — © 2026', align='C')

# Sauvegarde
output_path = "/home/user/.local/share/carsoworkdirs/user-admin/CARSO_Presentation_2026.pdf"
pdf.output(output_path)
print(f"PDF généré avec succès : {output_path}")
print(f"Nombre de diapositives : {pdf.pages_count}")
