"""Test réel — proposition vidéo (serveur démarré, PostgreSQL, YouTube réel).

Script de preuve de l'incrément 57 (spécification corrigée du 08/10, puis du
09/10 pour le vrai téléchargement via le relais signé).

Ce n'est pas un test unitaire : tout passe par HTTP sur le serveur réel, contre
**YouTube réellement**, avec un compte interne. Il vérifie ce que l'interface va
réellement consommer après la correction de spécification du 08/10 :

- le client **consulte** une vidéo et la source propose ses résolutions — sans
  hauteur demandée, sans second appel ;
- chaque résolution propose les **fichiers réels** que la source publie, à une
  adresse que le navigateur du client ira chercher lui-même ;
- ces adresses servent réellement des octets (sinon le lien est une promesse
  creuse) ;
- **aucun octet n'entre dans CARSO** : ni document, ni fichier de stockage ;
- une page sans vidéo et une adresse interne sont refusées avec un motif.

Aucune simulation réseau : yt-dlp travaille pour de vrai, sur une vidéo de 19 s.

Résolutions, fichiers réellement servis, et **reprise automatique** : une adresse
volontairement expirée (le CDN la refuse pour de vrai) est réparée par le relais,
qui réanalyse la page et reprend la même piste.

Exécution : ./.venv/Scripts/python.exe _test_reel_lecture_video.py

``CARSO_BASE_URL`` permet de viser une autre instance (ex. un serveur lancé sur un
port libre pour vérifier un changement sans toucher à celui qui tourne déjà).
"""

import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from app.core.config import get_settings  # noqa: E402
from app.documents.relais_video import CibleRelais, signer_jeton  # noqa: E402

BASE = os.environ.get("CARSO_BASE_URL", "http://127.0.0.1:8000/api/v1").rstrip("/")
#: « Me at the zoo » — 19 s, la vidéo la plus courte qui existe sur YouTube.
VIDEO = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
#: Page sans vidéo : l'extracteur n'existe pas, le refus doit être explicite.
SANS_VIDEO = "https://example.com/"

echecs: list[str] = []


def verifier(condition: bool, message: str) -> None:
    if condition:
        print(f"  OK    {message}")
    else:
        print(f"  ECHEC {message}")
        echecs.append(message)


def connecter(email: str, mot_de_passe: str) -> httpx.Client:
    reponse = httpx.post(
        f"{BASE}/auth/login", json={"email": email, "mot_de_passe": mot_de_passe}, timeout=30
    )
    reponse.raise_for_status()
    return httpx.Client(
        base_url=BASE,
        headers={"Authorization": f"Bearer {reponse.json()['access_token']}"},
        timeout=300,
    )


admin = connecter("admin@carso.mg", "CarsoAdmin2026!")
racine = Path(get_settings().storage_root)


def _fichiers_stockage() -> set[str]:
    if not racine.exists():
        return set()
    return {str(chemin) for chemin in racine.rglob("*") if chemin.is_file()}


def _adresse(resolution: dict[str, Any]) -> str | None:
    """Adresse de fichier d'une résolution : le flux complet, sinon la piste vidéo."""
    for cle in ("combine", "video"):
        piste = resolution.get(cle)
        if piste and piste.get("url"):
            return str(piste["url"])
    return None


print("\n1. Résolutions proposées par la source, sans hauteur demandée")
avant_documents = len(admin.get("/documents/all").json())
avant_fichiers = _fichiers_stockage()

reponse = admin.get("/documents/media/flux", params={"url": VIDEO})
verifier(reponse.status_code == 200, f"lecture servie 200 (reçu {reponse.status_code})")
corps: dict[str, Any] = reponse.json() if reponse.status_code == 200 else {}

basse: dict[str, Any] = {}
complete: dict[str, Any] = {}
if corps:
    print(f"    « {corps['titre']} » — {corps['duree_secondes']} s, {corps['extracteur']}")
    hauteurs = [resolution["hauteur"] for resolution in corps["resolutions"]]
    verifier(hauteurs == sorted(hauteurs, reverse=True), f"résolutions triées : {hauteurs}")
    verifier(corps["telechargeable"] is True, "la vidéo est téléchargeable (au moins un fichier)")
    verifier(corps["adresse_temporaire"] is True, "adresses annoncées comme temporaires")

    telechargeables = [
        resolution
        for resolution in corps["resolutions"]
        if resolution["telechargement_possible"] and _adresse(resolution)
    ]
    verifier(
        len(telechargeables) > 0,
        f"{len(telechargeables)} résolution(s) avec une adresse de fichier",
    )
    verifier(
        all(_adresse(resolution).startswith("https://") for resolution in telechargeables),
        "toutes les adresses proposées sont https (et signées pour le relais)",
    )
    verifier(
        not any(
            resolution["video"] and resolution["video"]["protocole"] not in ("https", "http")
            for resolution in corps["resolutions"]
        ),
        "aucun manifeste présenté comme un téléchargement",
    )

    completes = [r for r in telechargeables if r["combine"] and r["combine"]["contient_audio"]]
    if completes:
        complete = completes[0]
        print(
            f"    {complete['hauteur']}p : UN fichier complet (format "
            f"{complete['combine']['format_id']}, {complete['combine']['extension']})"
            " — un clic, une vidéo lisible"
        )
    else:
        print("    aucune résolution en un seul fichier complet (la source n'en publie pas)")

    basses = [
        resolution
        for resolution in reversed(corps["resolutions"])
        if resolution["video"] and resolution["video"].get("url")
    ]
    basse = basses[0] if basses else {}
    if basse:
        print(
            f"    {basse['hauteur']}p : vidéo {basse['video']['format_id']} "
            f"({basse['video']['protocole']}), son "
            f"{basse['audio']['format_id'] if basse['audio'] else 'absent'}"
        )
        verifier(
            basse["muxage_requis"] is True and basse["telechargement_possible"] is True,
            "vidéo et son séparés : les deux fichiers sont téléchargeables, sans muxage ici",
        )

print("\n2. Les adresses rendues au client servent réellement des octets")
cibles: list[tuple[str, str]] = []
if complete:
    cibles.append(("fichier complet", str(complete["combine"]["url"])))
if basse:
    cibles.append(("piste vidéo (sans le son)", str(basse["video"]["url"])))
    if basse["audio"] and basse["audio"].get("url"):
        cibles.append(("son seul", str(basse["audio"]["url"])))

for libelle, adresse in cibles:
    tete = httpx.get(adresse, headers={"Range": "bytes=0-2047"}, timeout=60, follow_redirects=True)
    verifier(
        tete.status_code in (200, 206) and len(tete.content) > 0,
        f"{libelle} : HTTP {tete.status_code}, {len(tete.content)} octets, "
        f"{tete.headers.get('content-type', 'type inconnu')}",
    )

print("\n3. Rien n'est entré dans CARSO")
apres_documents = len(admin.get("/documents/all").json())
apres_fichiers = _fichiers_stockage()
verifier(
    apres_documents == avant_documents,
    f"aucun document créé ({avant_documents} → {apres_documents})",
)
verifier(
    apres_fichiers == avant_fichiers,
    f"aucun fichier déposé dans le stockage ({len(avant_fichiers)} → {len(apres_fichiers)})",
)

print("\n4. Refus d'une page sans vidéo")
refus = admin.get("/documents/media/flux", params={"url": SANS_VIDEO})
verifier(refus.status_code == 422, f"refus 422 (reçu {refus.status_code})")
if refus.status_code == 422:
    message = refus.json()["error"]["message"]
    verifier("prise en charge" in message, f"motif exploitable : {message[:110]}")

print("\n5. Refus d'une adresse interne (SSRF)")
interne = admin.get("/documents/media/flux", params={"url": "http://169.254.169.254/latest/"})
verifier(interne.status_code == 422, f"adresse interne refusée 422 (reçu {interne.status_code})")

print("\n6. Import d'une vidéo : refusé, jamais rapatrié dans CARSO")
#: Un vrai fichier vidéo servi par le web : le serveur doit refuser de le stocker.
FICHIER_VIDEO = "https://www.w3schools.com/html/mov_bbb.mp4"
refus_import = admin.post(
    "/documents/import-media",
    json={"url": FICHIER_VIDEO, "nom": "essai video refusee"},
    timeout=120,
)
verifier(refus_import.status_code == 422, f"refus 422 (reçu {refus_import.status_code})")
if refus_import.status_code == 422:
    motif_import = refus_import.json()["error"]["message"]
    verifier("vidéo" in motif_import, f"motif explicite : {motif_import[:100]}")
verifier(
    len(admin.get("/documents/all").json()) == avant_documents,
    "un import vidéo refusé ne crée aucun document",
)

print("\n7. Le relais sert un vrai téléchargement, sans rien conserver")
jeton_video: tuple[str, str, int] | None = None
for resolution in corps.get("resolutions", []):
    for cle in ("combine", "video"):
        piste = resolution.get(cle) or {}
        if piste.get("jeton"):
            jeton_video = (piste["jeton"], piste.get("extension") or "mp4", resolution["hauteur"])
            break
    if jeton_video:
        break
verifier(jeton_video is not None, "le flux a livré un lien de téléchargement signé")

if jeton_video:
    jeton, extension, hauteur = jeton_video
    lien = f"{BASE}/documents/media/fichier"
    # Aucun en-tête d'authentification : c'est le cas d'un <a href> du navigateur.
    with httpx.stream(
        "GET",
        lien,
        params={"jeton": jeton, "nom": f"Me at the zoo — {hauteur}p.{extension}"},
        timeout=60,
    ) as telechargement:
        verifier(
            telechargement.status_code == 200,
            f"téléchargement servi 200 (reçu {telechargement.status_code})",
        )
        disposition = telechargement.headers.get("content-disposition", "")
        verifier(
            disposition.startswith("attachment;"),
            f"le navigateur enregistre au lieu de jouer : {disposition[:90]}",
        )
        type_servi = telechargement.headers.get("content-type", "")
        verifier(
            type_servi.startswith(("video/", "audio/")),
            f"type réel transmis : {type_servi}",
        )
        cache = telechargement.headers.get("cache-control", "")
        verifier(
            cache == "private, no-store",
            f"rien ne reste dans un cache : Cache-Control: {cache or 'absent'}",
        )
        premiers = next(telechargement.iter_bytes(65_536), b"")
        verifier(len(premiers) > 0, f"{len(premiers)} octets reçus, puis flux interrompu")

    falsifie = httpx.get(lien, params={"jeton": jeton[:-3] + "aaa"}, timeout=30)
    verifier(
        falsifie.status_code == 422,
        f"jeton falsifié refusé 422 (reçu {falsifie.status_code})",
    )
    sans_jeton = httpx.get(lien, params={"jeton": "jeton-inexistant-000000"}, timeout=30)
    verifier(
        sans_jeton.status_code == 422,
        f"jeton inconnu refusé 422 (reçu {sans_jeton.status_code})",
    )

    apres_relais = len(admin.get("/documents/all").json())
    fichiers_relais = _fichiers_stockage()
    verifier(
        apres_relais == avant_documents and fichiers_relais == avant_fichiers,
        "le relais n'a rien écrit : ni document, ni fichier",
    )

# --- 8. Reprise après un refus **réel** de la source (403/410) --------------------
print("\n8. Une adresse expirée est réparée automatiquement, pas rendue à l'utilisateur")


def _expirer(adresse: str) -> str:
    """Rend l'adresse réellement refusée par le CDN : son expiration est passée.

    C'est exactement ce qui arrive à un lien affiché depuis plus longtemps que la
    durée de vie de l'adresse signée par YouTube — et ce qui obligeait l'utilisateur
    à relancer la lecture avant la reprise automatique.
    """
    analyse = urlsplit(adresse)
    parametres = dict(parse_qsl(analyse.query, keep_blank_values=True))
    parametres["expire"] = "1"
    return urlunsplit(
        (analyse.scheme, analyse.netloc, analyse.path, urlencode(parametres), "")
    )


if basse:
    adresse_morte = _expirer(str(basse["video"]["url"]))
    direct = httpx.get(adresse_morte, headers={"Range": "bytes=0-2047"}, timeout=60)
    verifier(
        direct.status_code in (401, 403, 410),
        f"l'adresse expirée est bien refusée par la source (HTTP {direct.status_code})",
    )

    cible = CibleRelais(
        url=adresse_morte,
        page=VIDEO,
        hauteur=basse["hauteur"],
        piste="video",
    )
    jeton_mort = signer_jeton(cible, settings=get_settings())
    with httpx.stream(
        "GET",
        f"{BASE}/documents/media/fichier",
        params={"jeton": jeton_mort, "nom": "reprise automatique 240p.mp4"},
        timeout=120,
    ) as reprise:  # aucun en-tête d'authentification, comme un clic du navigateur
        verifier(
            reprise.status_code == 200,
            f"le relais reprend le fichier malgré le refus (reçu {reprise.status_code})",
        )
        verifier(
            reprise.headers.get("content-disposition", "").startswith("attachment;"),
            "la reprise reste un vrai téléchargement",
        )
        if reprise.status_code == 200:
            octets = next(reprise.iter_bytes(65_536), b"")
            verifier(len(octets) > 0, f"{len(octets)} octets servis après reprise")

    apres_reprise_documents = len(admin.get("/documents/all").json())
    apres_reprise_fichiers = _fichiers_stockage()
    verifier(
        apres_reprise_documents == avant_documents
        and apres_reprise_fichiers == avant_fichiers,
        "la reprise n'a rien écrit non plus",
    )

print("\n" + ("=" * 60))
if echecs:
    print(f"ECHECS ({len(echecs)}) :")
    for echec in echecs:
        print(f"  - {echec}")
    raise SystemExit(1)
print("TOUT EST VERT — résolutions réelles, adresses exploitables, et rien dans CARSO.")
