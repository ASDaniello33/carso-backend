
import sys, time
sys.path.insert(0, ".")
from app.documents.generation_html import convertir_html_bytes

def html_doc(nb_sections: int, nb_lignes: int) -> str:
    corps = []
    for s in range(nb_sections):
        corps.append(f"<h2>Section {s} — présentation du lot {s}</h2>")
        corps.append("<p>" + ("Texte de présentation détaillé pour la section. " * 12) + "</p>")
        corps.append("<ul>" + "".join(f"<li>Point {i} du lot {s}</li>" for i in range(10)) + "</ul>")
        lignes = "".join(
            f"<tr><td>Poste {i}</td><td>{i * 100} MAD</td><td>Commentaire {i}</td></tr>"
            for i in range(nb_lignes)
        )
        corps.append(
            "<table><thead><tr><th>Poste</th><th>Montant</th><th>Détail</th></tr></thead>"
            f"<tbody>{lignes}</tbody></table>"
        )
    return ("<html><head><style>h2{color:#123;}"
            "table{border-collapse:collapse;}td,th{border:1px solid #999;padding:4px;}</style>"
            "</head><body>" + "".join(corps) + "</body></html>")

for nb_sections, nb_lignes in ((2, 10), (6, 30), (12, 60)):
    source = html_doc(nb_sections, nb_lignes)
    debut = time.perf_counter()
    octets = convertir_html_bytes(source, format="docx")
    duree = time.perf_counter() - debut
    print(f"sections={nb_sections:2d} lignes/table={nb_lignes:3d} html={len(source)/1024:7.1f} Ko "
          f"-> docx={len(octets)/1024:8.1f} Ko en {duree:6.2f} s")
