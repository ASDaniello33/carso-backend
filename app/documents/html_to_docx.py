"""Conversion HTML(+CSS) → DOCX pour les tools documentaires CARSO.

Le module fournit la même capacité que le script autonome ``html_to_docx``
(fourni par le client, dépendances ``beautifulsoup4`` + ``python-docx`` +
``Pillow``) : une page HTML avec ses styles devient un document Word fidèle
(couleurs, polices, titres, images locales/distantes/data-URI, listes,
tableaux, liens, encadrés). C'est le moteur du tool
``generer_document_html`` : l'agent maîtrise le HTML, un type **standard**,
et produit des livrables personnalisables sans composer un ``DocumentSpec``
strict — la chaîne ``DocumentSpec``/``profiles`` reste disponible et
inchangée pour les livrables à structure imposée.

Intégration CARSO (différences assumées avec le script d'origine) :

- le code reste ici, commenté en français, et importe ``docx``/``bs4`` de
  l'environnement (aucun pip à l'exécution) ;
- ``Pillow`` et ``requests`` restent facultatifs : sans eux, les images
  distantes sont remplacées par une mention explicite (jamais un échec du
  document entier) ;
- aucune écriture disque : la conversion débouche sur des **octets** en
  mémoire (le dépôt passe par ``DocumentService``, jamais par un chemin).
"""

from __future__ import annotations

import base64
import os
import re
from io import BytesIO
from urllib.parse import urlparse

from bs4 import BeautifulSoup, NavigableString, Tag
from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Emu, Pt, RGBColor

try:
    from PIL import Image
    _HAS_PIL = True
except ImportError:  # pragma: no cover
    _HAS_PIL = False

try:
    import requests
    _HAS_REQUESTS = True
except ImportError:  # pragma: no cover
    _HAS_REQUESTS = False


__all__ = [
    "ConversionHtmlError",
    "html_string_to_docx_bytes",
    "html_to_docx_bytes",
    "HtmlToDocxConverter",
]


class ConversionHtmlError(Exception):
    """Échec explicite de conversion (HTML vide, illisible...)."""


# =========================================================================
# 1. Constantes : couleurs nommées, styles par défaut des balises, unités
# =========================================================================

# Sous-ensemble large des couleurs nommées CSS3.
NAMED_COLORS = {
    "black": "000000", "white": "ffffff", "red": "ff0000", "green": "008000",
    "blue": "0000ff", "yellow": "ffff00", "orange": "ffa500", "purple": "800080",
    "gray": "808080", "grey": "808080", "silver": "c0c0c0", "maroon": "800000",
    "olive": "808000", "lime": "00ff00", "aqua": "00ffff", "teal": "008080",
    "navy": "000080", "fuchsia": "ff00ff", "pink": "ffc0cb", "brown": "a52a2a",
    "gold": "ffd700", "indigo": "4b0082", "violet": "ee82ee", "coral": "ff7f50",
    "salmon": "fa8072", "khaki": "f0e68c", "crimson": "dc143c", "chocolate": "d2691e",
    "darkred": "8b0000", "darkblue": "00008b", "darkgreen": "006400",
    "darkgray": "a9a9a9", "darkgrey": "a9a9a9", "lightgray": "d3d3d3",
    "lightgrey": "d3d3d3", "lightblue": "add8e6", "lightgreen": "90ee90",
    "lightyellow": "ffffe0", "lightpink": "ffb6c1", "steelblue": "4682b4",
    "skyblue": "87ceeb", "tomato": "ff6347", "orangered": "ff4500",
    "seagreen": "2e8b57", "slategray": "708090", "slategrey": "708090",
    "midnightblue": "191970", "dodgerblue": "1e90ff", "royalblue": "4169e1",
    "forestgreen": "228b22", "firebrick": "b22222", "beige": "f5f5dc",
    "ivory": "fffff0", "lavender": "e6e6fa", "plum": "dda0dd", "orchid": "da70d6",
    "turquoise": "40e0d0", "tan": "d2b48c", "peru": "cd853f", "sienna": "a0522d",
    "transparent": None, "none": None,
    "darkorange": "ff8c00", "cornflowerblue": "6495ed", "mediumblue": "0000cd",
    "darkviolet": "9400d3", "deeppink": "ff1493", "hotpink": "ff69b4",
    "darkslategray": "2f4f4f", "cadetblue": "5f9ea0", "chartreuse": "7fff00",
    "cyan": "00ffff", "magenta": "ff00ff", "darkcyan": "008b8b",
    "darkkhaki": "bdb76b", "darkmagenta": "8b008b", "darkolivegreen": "556b2f",
    "darksalmon": "e9967a", "darkseagreen": "8fbc8f", "darkslateblue": "483d8b",
    "darkturquoise": "00ced1", "dimgray": "696969", "dimgrey": "696969",
    "gainsboro": "dcdcdc", "honeydew": "f0fff0", "indianred": "cd5c5c",
    "lawngreen": "7cfc00", "lightcoral": "f08080", "lightcyan": "e0ffff",
    "lightsalmon": "ffa07a", "lightseagreen": "20b2aa", "lightskyblue": "87cefa",
    "lightslategray": "778899", "lightsteelblue": "b0c4de", "limegreen": "32cd32",
    "mediumaquamarine": "66cdaa", "mediumorchid": "ba55d3", "mediumpurple": "9370db",
    "mediumseagreen": "3cb371", "mediumslateblue": "7b68ee",
    "mediumspringgreen": "00fa9a", "mediumturquoise": "48d1cc",
    "mediumvioletred": "c71585", "mistyrose": "ffe4e1", "moccasin": "ffe4b5",
    "navajowhite": "ffdead", "peachpuff": "ffdab9", "powderblue": "b0e0e6",
    "rosybrown": "bc8f8f", "saddlebrown": "8b4513", "sandybrown": "f4a460",
    "springgreen": "00ff7f", "wheat": "f5deb3", "whitesmoke": "f5f5f5",
    "yellowgreen": "9acd32", "aliceblue": "f0f8ff", "antiquewhite": "faebd7",
    "azure": "f0ffff", "bisque": "ffe4c4", "blanchedalmond": "ffebcd",
    "blueviolet": "8a2be2", "burlywood": "deb887",
}

# Styles "user-agent" par défaut, appliqués avant la cascade CSS.
DEFAULT_TAG_STYLES = {
    "b": {"font-weight": "bold"},
    "strong": {"font-weight": "bold"},
    "i": {"font-style": "italic"},
    "em": {"font-style": "italic"},
    "u": {"text-decoration": "underline"},
    "s": {"text-decoration": "line-through"},
    "strike": {"text-decoration": "line-through"},
    "del": {"text-decoration": "line-through"},
    "ins": {"text-decoration": "underline"},
    "small": {"font-size": "0.83em"},
    "sup": {"font-size": "0.7em", "vertical-align": "super"},
    "sub": {"font-size": "0.7em", "vertical-align": "sub"},
    "code": {"font-family": "Consolas, 'Courier New', monospace"},
    "kbd": {"font-family": "Consolas, 'Courier New', monospace"},
    "pre": {"font-family": "Consolas, 'Courier New', monospace",
            "white-space": "pre"},
    "a": {"color": "#0563C1", "text-decoration": "underline"},
    "h1": {"font-size": "2em", "font-weight": "bold"},
    "h2": {"font-size": "1.5em", "font-weight": "bold"},
    "h3": {"font-size": "1.17em", "font-weight": "bold"},
    "h4": {"font-size": "1em", "font-weight": "bold"},
    "h5": {"font-size": "0.83em", "font-weight": "bold"},
    "h6": {"font-size": "0.67em", "font-weight": "bold"},
    "blockquote": {"margin-left": "2em", "margin-right": "2em",
                   "color": "#555555", "font-style": "italic",
                   "border-left": "3px solid #cccccc",
                   "padding-left": "0.8em"},
    "mark": {"background-color": "#ffff00"},
}

# Propriétés héritées par défaut d'un parent vers ses enfants (comme en CSS).
INHERITABLE_PROPS = {
    "color", "font-family", "font-size", "font-weight", "font-style",
    "text-decoration", "text-align", "line-height", "white-space",
    "vertical-align",
}

BLOCK_TAGS = {
    "div", "p", "section", "article", "header", "footer", "nav", "main",
    "aside", "blockquote", "figure", "figcaption", "form", "ul", "ol", "li",
    "table", "hr", "pre", "h1", "h2", "h3", "h4", "h5", "h6", "body", "html",
    "address", "fieldset",
}

SKIP_TAGS = {"script", "style", "head", "meta", "link", "title", "noscript",
             "object", "iframe", "svg", "select", "input", "button", "textarea"}

EMU_PER_PT = 12700
DEFAULT_ROOT_FONT_PT = 11.0


# =========================================================================
# 2. Conversion d'unités et de couleurs
# =========================================================================

_LENGTH_RE = re.compile(r"^(-?\d*\.?\d+)\s*(px|pt|em|rem|%|cm|mm|in)?$")


def parse_length(value, font_size_pt=DEFAULT_ROOT_FONT_PT, percent_base_pt=None):
    """Convertit une longueur CSS (px, pt, em, rem, %, cm, mm, in) en points.
    Retourne None si la valeur n'est pas une longueur reconnue (ex: 'auto')."""
    if not value:
        return None
    value = value.strip()
    m = _LENGTH_RE.match(value)
    if not m:
        return None
    num = float(m.group(1))
    unit = m.group(2) or "px"
    if unit == "pt":
        return num
    if unit == "px":
        return num * 0.75
    if unit in ("em", "rem"):
        return num * font_size_pt
    if unit == "cm":
        return num * 28.3465 / 10
    if unit == "mm":
        return num * 2.83465
    if unit == "in":
        return num * 72.0
    if unit == "%":
        if percent_base_pt is not None:
            return percent_base_pt * num / 100.0
        return None
    return None


def parse_color(value):
    """Convertit une couleur CSS (hex, rgb()/rgba(), nommée) en 'RRGGBB' minuscule."""
    if not value:
        return None
    value = value.strip().lower()
    if value.startswith("#"):
        hexv = value[1:]
        if len(hexv) == 3:
            hexv = "".join(c * 2 for c in hexv)
        if re.match(r"^[0-9a-f]{6}$", hexv):
            return hexv
        return None
    m = re.match(r"rgba?\(([^)]+)\)", value)
    if m:
        parts = [p.strip().rstrip("%") for p in m.group(1).split(",")]
        try:
            r, g, b = (int(float(p)) for p in parts[:3])
            return f"{max(0,min(r,255)):02x}{max(0,min(g,255)):02x}{max(0,min(b,255)):02x}"
        except (ValueError, IndexError):
            return None
    m = re.match(r"hsla?\(([^)]+)\)", value)
    if m:
        parts = [p.strip() for p in m.group(1).split(",")]
        try:
            h = float(re.sub(r"[^\d.]", "", parts[0])) / 360.0
            s = float(parts[1].rstrip("%")) / 100.0
            li = float(parts[2].rstrip("%")) / 100.0
            r, g, b = _hsl_to_rgb(h, s, li)
            return f"{r:02x}{g:02x}{b:02x}"
        except (ValueError, IndexError):
            return None
    if value in NAMED_COLORS:
        return NAMED_COLORS[value]
    return None


def _hsl_to_rgb(h, s, li):
    if s == 0:
        r = g = b = li
    else:
        def hue_to_rgb(p, q, t):
            if t < 0:
                t += 1
            if t > 1:
                t -= 1
            if t < 1 / 6:
                return p + (q - p) * 6 * t
            if t < 1 / 2:
                return q
            if t < 2 / 3:
                return p + (q - p) * (2 / 3 - t) * 6
            return p
        q = li * (1 + s) if li < 0.5 else li + s - li * s
        p = 2 * li - q
        r = hue_to_rgb(p, q, h + 1 / 3)
        g = hue_to_rgb(p, q, h)
        b = hue_to_rgb(p, q, h - 1 / 3)
    return round(r * 255), round(g * 255), round(b * 255)


def parse_box_shorthand(value):
    """'10px 20px' -> (top,right,bottom,left) en respectant la règle CSS du
    nombre de valeurs (1, 2, 3 ou 4)."""
    parts = value.split()
    if len(parts) == 1:
        return parts[0], parts[0], parts[0], parts[0]
    if len(parts) == 2:
        return parts[0], parts[1], parts[0], parts[1]
    if len(parts) == 3:
        return parts[0], parts[1], parts[2], parts[1]
    if len(parts) >= 4:
        return parts[0], parts[1], parts[2], parts[3]
    return None, None, None, None


# =========================================================================
# 3. Mini moteur CSS (parsing + sélecteurs + spécificité)
# =========================================================================

def _split_top_level(text, sep):
    """Scinde `text` sur `sep` en ignorant ce qui est entre parenthèses."""
    parts, current, depth = [], "", 0
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch == sep and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += ch
    if current.strip():
        parts.append(current)
    return parts


def parse_declarations(text):
    decls = {}
    for part in _split_top_level(text, ";"):
        if ":" not in part:
            continue
        prop, _, val = part.partition(":")
        prop = prop.strip().lower()
        val = val.strip()
        if prop and val:
            decls[prop] = val
    return decls


_SIMPLE_SEL_RE = re.compile(r"^(\*|[a-zA-Z][\w-]*)?((?:[.#][\w-]+)*)$")


def _compile_simple_selector(token):
    m = _SIMPLE_SEL_RE.match(token.strip())
    if not m:
        return None
    tag = m.group(1)
    if tag == "*":
        tag = None
    rest = m.group(2) or ""
    classes = re.findall(r"\.([\w-]+)", rest)
    ids = re.findall(r"#([\w-]+)", rest)
    return {"tag": tag, "classes": classes, "ids": ids}


def _element_matches_simple(el, simple):
    if simple["tag"] and el.name != simple["tag"]:
        return False
    if simple["ids"]:
        if el.get("id") not in simple["ids"]:
            return False
    if simple["classes"]:
        el_classes = el.get("class") or []
        if not all(c in el_classes for c in simple["classes"]):
            return False
    return True


def _element_matches_selector(el, selector_text):
    cleaned = re.sub(r"[>+~]", " ", selector_text)
    tokens = [t for t in cleaned.split() if t]
    if not tokens:
        return False
    simples = [_compile_simple_selector(t) for t in tokens]
    if any(s is None for s in simples):
        return False
    if not _element_matches_simple(el, simples[-1]):
        return False
    ancestor = el.parent
    for simple in reversed(simples[:-1]):
        found = False
        node = ancestor
        while node is not None and isinstance(node, Tag):
            if _element_matches_simple(node, simple):
                found = True
                ancestor = node.parent
                break
            node = node.parent
        if not found:
            return False
    return True


def _specificity(selector_text):
    ids = selector_text.count("#")
    classes = selector_text.count(".")
    tags = len(re.findall(r"(?<![.\w#-])[a-zA-Z][\w-]*", selector_text))
    return (ids, classes, tags)


class Stylesheet:
    """Feuille de style CSS simplifiée : parsing par expression régulière,
    sans support des règles imbriquées (les blocs @media/@font-face/@keyframes
    sont ignorés en tant que conteneur mais leur contenu interne, s'il
    ressemble à une règle valide, est appliqué sans condition)."""

    _RULE_RE = re.compile(r"([^{}]+)\{([^{}]*)\}", re.S)

    def __init__(self, css_text):
        self.rules = []  # liste de (selector, specificity, declarations, order)
        css_text = re.sub(r"/\*.*?\*/", "", css_text or "", flags=re.S)
        order = 0
        for m in self._RULE_RE.finditer(css_text):
            selector_group = m.group(1).strip()
            if not selector_group or selector_group.startswith("@"):
                continue
            declarations = parse_declarations(m.group(2))
            if not declarations:
                continue
            for sel in _split_top_level(selector_group, ","):
                sel = sel.strip()
                if not sel:
                    continue
                self.rules.append((sel, _specificity(sel), declarations, order))
                order += 1

    def match(self, el):
        """Retourne les déclarations qui s'appliquent à `el`, triées par
        spécificité croissante (les dernières écrasent les premières)."""
        matched = [(spec, order, decls) for sel, spec, decls, order in self.rules
                   if _element_matches_selector(el, sel)]
        matched.sort(key=lambda x: (x[0], x[1]))
        merged = {}
        for _, _, decls in matched:
            merged.update(decls)
        return merged


# =========================================================================
# 4. Calcul du style résolu d'un élément (cascade + héritage + unités)
# =========================================================================

def default_root_style():
    return {
        "color": None,
        "background-color": None,
        "font-family": None,
        "font-size-pt": DEFAULT_ROOT_FONT_PT,
        "font-weight": "normal",
        "font-style": "normal",
        "text-decoration": "none",
        "text-align": None,
        "line-height": None,
        "white-space": "normal",
        "vertical-align": None,
        "margin": (0.0, 0.0, 0.0, 0.0),
        "padding": (0.0, 0.0, 0.0, 0.0),
        "border": {},
        "width-pt": None,
        "height-pt": None,
        "display": "block",
    }


def _box_values(raw, shorthand_key, side_prefix, font_size_pt, percent_base_pt=None):
    top = right = bottom = left = None
    if shorthand_key in raw:
        t, r, b, gauche = parse_box_shorthand(raw[shorthand_key])
        top, right, bottom, left = t, r, b, gauche
    sides = {"top": top, "right": right, "bottom": bottom, "left": left}
    for side in sides:
        key = f"{side_prefix}-{side}"
        if key in raw:
            sides[side] = raw[key]
    result = []
    for side in ("top", "right", "bottom", "left"):
        val = parse_length(sides[side], font_size_pt, percent_base_pt) if sides[side] else None
        result.append(val if val is not None else 0.0)
    return tuple(result)


def _border_side(raw, side, font_size_pt):
    """Analyse `border`, `border-top`, `border-top-width/style/color`, etc.
    pour un côté donné. Retourne un dict {width_pt, color, style} ou None."""
    width_pt, color, bstyle = None, None, "solid"
    shorthand = raw.get(f"border-{side}") or raw.get("border")
    if shorthand:
        tokens = shorthand.split()
        for tok in tokens:
            length = parse_length(tok, font_size_pt)
            if length is not None:
                width_pt = length
            elif tok.lower() in ("solid", "dashed", "dotted", "double"):
                bstyle = tok.lower()
            else:
                col = parse_color(tok)
                if col:
                    color = col
    w = raw.get(f"border-{side}-width")
    if w:
        length = parse_length(w, font_size_pt)
        if length is not None:
            width_pt = length
    c = raw.get(f"border-{side}-color")
    if c:
        col = parse_color(c)
        if col:
            color = col
    s = raw.get(f"border-{side}-style")
    if s and s.lower() != "none":
        bstyle = s.lower()
    style_cote = raw.get(f"border-{side}-style", "").lower()
    style_global = raw.get("border-style", "").lower()
    if style_cote == "none" or style_global == "none":
        return None
    if width_pt is None and shorthand is None and w is None:
        return None
    if width_pt == 0:
        return None
    return {"width_pt": width_pt or 1.0, "color": color or "000000", "style": bstyle}


def compute_style(el, parent_style, stylesheet):
    """Calcule le style CSS résolu (dict "propriétés brutes" + valeurs déjà
    converties pour font-size) d'un élément, à partir du style hérité du
    parent, des styles par défaut de la balise, de la feuille de style et
    du style inline."""
    raw = {}
    # 1. héritage des propriétés héritables du parent (valeurs déjà résolues)
    inherited_font_size_pt = parent_style["font-size-pt"]

    # 2. styles par défaut de la balise (user-agent)
    tag_defaults = DEFAULT_TAG_STYLES.get(el.name, {})
    raw.update(tag_defaults)

    # 3. règles de la feuille de style CSS
    raw.update(stylesheet.match(el))

    # 4. style inline (priorité maximale)
    inline = el.get("style") if isinstance(el, Tag) else None
    if inline:
        raw.update(parse_declarations(inline))

    style = {}

    # -- taille de police (doit être résolue avant les autres unités "em") --
    fs_raw = raw.get("font-size")
    if fs_raw:
        fs_pt = parse_length(fs_raw, inherited_font_size_pt, inherited_font_size_pt)
        style["font-size-pt"] = fs_pt if fs_pt else inherited_font_size_pt
    else:
        style["font-size-pt"] = inherited_font_size_pt
    font_size_pt = style["font-size-pt"]

    # -- couleur --
    style["color"] = parse_color(raw["color"]) if "color" in raw else parent_style.get("color")

    # -- police --
    style["font-family"] = raw.get("font-family", parent_style.get("font-family"))

    # -- gras / italique / décoration --
    fw = raw.get("font-weight", parent_style.get("font-weight", "normal"))
    style["font-weight"] = fw
    if isinstance(fw, str):
        style["bold"] = (
            fw.lower() in ("bold", "bolder")
            or (fw.isdigit() and int(fw) >= 600)
        )
    else:
        style["bold"] = bool(fw)
    fstyle = raw.get("font-style", parent_style.get("font-style", "normal"))
    style["font-style"] = fstyle
    style["italic"] = fstyle in ("italic", "oblique")
    decoration = raw.get("text-decoration", parent_style.get("text-decoration", "none"))
    style["text-decoration"] = decoration
    style["underline"] = "underline" in decoration
    style["strike"] = "line-through" in decoration

    # -- alignement / interligne / espaces --
    style["text-align"] = raw.get("text-align", parent_style.get("text-align"))
    style["line-height"] = raw.get("line-height", parent_style.get("line-height"))
    style["white-space"] = raw.get("white-space", parent_style.get("white-space", "normal"))
    style["vertical-align"] = raw.get("vertical-align", parent_style.get("vertical-align"))

    # -- fond --
    bg = raw.get("background-color") or raw.get("background")
    style["background-color"] = None
    if bg:
        # dans le raccourci "background", on cherche un jeton couleur
        for token in bg.split():
            col = parse_color(token)
            if col:
                style["background-color"] = col
                break
        else:
            col = parse_color(bg)
            style["background-color"] = col

    # -- marges / paddings (non hérités) --
    style["margin"] = _box_values(raw, "margin", "margin", font_size_pt)
    style["padding"] = _box_values(raw, "padding", "padding", font_size_pt)

    # -- bordures --
    borders = {}
    for side in ("top", "right", "bottom", "left"):
        b = _border_side(raw, side, font_size_pt)
        if b:
            borders[side] = b
    style["border"] = borders

    # -- dimensions --
    style["width-pt"] = parse_length(raw["width"], font_size_pt) if "width" in raw else None
    style["height-pt"] = parse_length(raw["height"], font_size_pt) if "height" in raw else None

    # -- display --
    style["display"] = raw.get("display", "inline" if el.name not in BLOCK_TAGS else "block")

    return style


# =========================================================================
# 5. Aides bas niveau python-docx (shading, bordures de paragraphe, page bg)
# =========================================================================

def _set_shading(pr_element, fill_hex):
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill_hex)
    pr_element.append(shd)


def set_paragraph_shading(paragraph, fill_hex):
    pPr = paragraph._p.get_or_add_pPr()
    _set_shading(pPr, fill_hex)


def set_run_shading(run, fill_hex):
    rPr = run._r.get_or_add_rPr()
    _set_shading(rPr, fill_hex)


def set_paragraph_borders(paragraph, borders):
    """`borders`: dict {'top'/'right'/'bottom'/'left': {'width_pt','color','style'}}"""
    if not borders:
        return
    pPr = paragraph._p.get_or_add_pPr()
    pBdr = OxmlElement("w:pBdr")
    val_map = {"solid": "single", "dashed": "dashed", "dotted": "dotted", "double": "double"}
    for side, spec in borders.items():
        el = OxmlElement(f"w:{side}")
        el.set(qn("w:val"), val_map.get(spec["style"], "single"))
        el.set(qn("w:sz"), str(max(2, int(spec["width_pt"] * 8))))  # 1/8 pt
        el.set(qn("w:space"), "4")
        el.set(qn("w:color"), spec["color"])
        pBdr.append(el)
    pPr.append(pBdr)


def set_table_borders(table, borders, default_color="000000", default_width_pt=1.0):
    tbl = table._tbl
    tblPr = tbl.tblPr
    tblBorders = OxmlElement("w:tblBorders")
    val_map = {"solid": "single", "dashed": "dashed", "dotted": "dotted", "double": "double"}
    for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
        spec = borders.get(side if side in borders else None)
        el = OxmlElement(f"w:{side}")
        if side in borders:
            spec = borders[side]
            el.set(qn("w:val"), val_map.get(spec["style"], "single"))
            el.set(qn("w:sz"), str(max(2, int(spec["width_pt"] * 8))))
            el.set(qn("w:color"), spec["color"])
        else:
            el.set(qn("w:val"), "none")
            el.set(qn("w:sz"), "0")
            el.set(qn("w:color"), "auto")
        el.set(qn("w:space"), "0")
        tblBorders.append(el)
    tblPr.append(tblBorders)


def set_table_cell_shading(cell, fill_hex):
    tcPr = cell._tc.get_or_add_tcPr()
    _set_shading(tcPr, fill_hex)


def set_cell_margins(cell, top_pt=0.0, right_pt=0.0, bottom_pt=0.0, left_pt=0.0):
    tcPr = cell._tc.get_or_add_tcPr()
    mar = OxmlElement("w:tcMar")
    cotes = (("top", top_pt), ("left", left_pt), ("bottom", bottom_pt), ("right", right_pt))
    for side, val in cotes:
        node = OxmlElement(f"w:{side}")
        node.set(qn("w:w"), str(int(val * 20)))  # twips
        node.set(qn("w:type"), "dxa")
        mar.append(node)
    tcPr.append(mar)


def set_page_background(document, fill_hex):
    bg = OxmlElement("w:background")
    bg.set(qn("w:color"), fill_hex)
    document.element.insert(0, bg)


def add_spacer_paragraph(container, space_after_pt):
    p = container.add_paragraph()
    p.paragraph_format.space_after = Pt(0)
    p.paragraph_format.space_before = Pt(0)
    run = p.add_run("")
    run.font.size = Pt(max(1, space_after_pt))
    return p


ALIGN_MAP = {
    "left": WD_ALIGN_PARAGRAPH.LEFT,
    "right": WD_ALIGN_PARAGRAPH.RIGHT,
    "center": WD_ALIGN_PARAGRAPH.CENTER,
    "justify": WD_ALIGN_PARAGRAPH.JUSTIFY,
}


def apply_paragraph_box_style(paragraph, style, apply_border=True):
    """Applique alignement, retraits, espacements, fond et bordures
    directes (sans table) à un paragraphe."""
    pf = paragraph.paragraph_format
    align = style.get("text-align")
    if align in ALIGN_MAP:
        paragraph.alignment = ALIGN_MAP[align]
    mt, mr, mb, ml = style.get("margin", (0, 0, 0, 0))
    pt_, pr_, pb_, pl_ = style.get("padding", (0, 0, 0, 0))
    if mt or pt_:
        pf.space_before = Pt(mt + pt_)
    if mb or pb_:
        pf.space_after = Pt(mb + pb_)
    left_indent = ml + pl_
    right_indent = mr + pr_
    if left_indent:
        pf.left_indent = Pt(left_indent)
    if right_indent:
        pf.right_indent = Pt(right_indent)
    lh = style.get("line-height")
    if lh:
        try:
            pf.line_spacing = float(lh)
        except (TypeError, ValueError):
            pass
    if style.get("background-color"):
        set_paragraph_shading(paragraph, style["background-color"])
    if apply_border and style.get("border"):
        set_paragraph_borders(paragraph, style["border"])


def apply_run_style(run, style):
    if style.get("font-family"):
        family = style["font-family"].split(",")[0].strip().strip("'\"")
        if family:
            run.font.name = family
            rPr = run._r.get_or_add_rPr()
            rFonts = rPr.find(qn("w:rFonts"))
            if rFonts is not None:
                rFonts.set(qn("w:eastAsia"), family)
    if style.get("font-size-pt"):
        run.font.size = Pt(style["font-size-pt"])
    run.font.bold = bool(style.get("bold"))
    run.font.italic = bool(style.get("italic"))
    if style.get("underline"):
        run.font.underline = True
    if style.get("strike"):
        run.font.strike = True
    if style.get("color"):
        try:
            run.font.color.rgb = RGBColor.from_string(style["color"])
        except ValueError:
            pass
    if style.get("background-color"):
        set_run_shading(run, style["background-color"])
    va = style.get("vertical-align")
    if va == "super":
        run.font.superscript = True
    elif va == "sub":
        run.font.subscript = True


# =========================================================================
# 6. Images
# =========================================================================

def _load_image_source(src, base_dir):
    """Retourne un objet utilisable par python-docx (chemin ou flux BytesIO),
    ou None si l'image n'a pas pu être chargée."""
    if not src:
        return None
    if src.startswith("data:"):
        try:
            header, b64data = src.split(",", 1)
            return BytesIO(base64.b64decode(b64data))
        except (ValueError, base64.binascii.Error):
            return None
    parsed = urlparse(src)
    if parsed.scheme in ("http", "https"):
        if not _HAS_REQUESTS:
            return None
        try:
            resp = requests.get(src, timeout=10)
            resp.raise_for_status()
            return BytesIO(resp.content)
        except Exception:
            return None
    path = src
    if not os.path.isabs(path) and base_dir:
        path = os.path.join(base_dir, path)
    return path if os.path.exists(path) else None


def _image_pixel_size(source):
    if not _HAS_PIL:
        return None, None
    try:
        if isinstance(source, BytesIO):
            source.seek(0)
        img = Image.open(source)
        size = img.size
        if isinstance(source, BytesIO):
            source.seek(0)
        return size
    except Exception:
        return None, None


def _compute_image_dims_pt(source, style, max_width_pt):
    width_pt = style.get("width-pt")
    height_pt = style.get("height-pt")
    iw, ih = _image_pixel_size(source)
    if width_pt and not height_pt and iw and ih:
        height_pt = width_pt * ih / iw
    elif height_pt and not width_pt and iw and ih:
        width_pt = height_pt * iw / ih
    elif not width_pt and not height_pt and iw and ih:
        width_pt = iw * 0.75
        height_pt = ih * 0.75
    if width_pt and max_width_pt and width_pt > max_width_pt:
        ratio = max_width_pt / width_pt
        width_pt *= ratio
        if height_pt:
            height_pt *= ratio
    return width_pt, height_pt


# =========================================================================
# 7. Convertisseur principal
# =========================================================================

class HtmlToDocxConverter:
    """Moteur de conversion HTML(+CSS) -> DOCX. Utiliser de préférence
    ``html_to_docx_bytes()`` / ``html_string_to_docx_bytes()`` pour l'usage
    simple (octets en mémoire, sans écriture disque)."""

    def __init__(self, base_dir=None, extra_css=""):
        self.base_dir = base_dir
        self.extra_css = extra_css or ""
        self.stylesheet = None
        self.doc = None
        self.content_width_pt = 468.0  # ~ page Letter avec marges 1 pouce

    # -- point d'entrée -----------------------------------------------
    def convert_string(self, html_text, output_path):
        soup = BeautifulSoup(html_text, "html.parser")
        return self._convert_soup(soup, output_path)

    def convert_file(self, html_path, output_path):
        html_path = os.path.abspath(html_path)
        html_dir = os.path.dirname(html_path)
        if self.base_dir is None:
            self.base_dir = html_dir
        with open(html_path, encoding="utf-8") as f:
            html_text = f.read()
        soup = BeautifulSoup(html_text, "html.parser")
        # feuilles de style liées (<link rel="stylesheet" href="...">)
        for link_tag in soup.find_all("link"):
            rel = link_tag.get("rel") or []
            if isinstance(rel, list) and "stylesheet" in [r.lower() for r in rel]:
                href = link_tag.get("href")
                if href and not urlparse(href).scheme:
                    css_path = os.path.join(html_dir, href)
                    if os.path.exists(css_path):
                        with open(css_path, encoding="utf-8") as cf:
                            self.extra_css += "\n" + cf.read()
        return self._convert_soup(soup, output_path)

    def _convert_soup(self, soup, output_path):
        css_text = "\n".join(tag.get_text() for tag in soup.find_all("style"))
        css_text += "\n" + self.extra_css
        self.stylesheet = Stylesheet(css_text)

        self.doc = Document()
        section = self.doc.sections[0]
        self.content_width_pt = (section.page_width - section.left_margin
                                  - section.right_margin) / EMU_PER_PT

        body = soup.find("body") or soup
        root_style = compute_style(body, default_root_style(), self.stylesheet)
        if root_style.get("background-color"):
            set_page_background(self.doc, root_style["background-color"])

        self._render_children(body, root_style, self.doc)
        self.doc.save(output_path)
        return output_path

    # -- rendu récursif --------------------------------------------------
    def _new_paragraph(self, container, style, apply_border=True):
        p = container.add_paragraph()
        apply_paragraph_box_style(p, style, apply_border=apply_border)
        return p

    def _render_children(self, el, style, container, seed_paragraph=None):
        current_p = seed_paragraph
        for child in el.children:
            if isinstance(child, NavigableString):
                preserve = style.get("white-space") == "pre"
                text = str(child) if preserve else re.sub(r"\s+", " ", str(child))
                if not preserve and text.strip() == "" and current_p is None:
                    continue
                if not preserve and text.strip() == "":
                    # espace significatif entre deux inline
                    if current_p is not None:
                        self._add_text(current_p, " ", style, preserve=False)
                    continue
                if current_p is None:
                    current_p = self._new_paragraph(container, style)
                self._add_text(current_p, text, style, preserve=preserve)
                continue

            if not isinstance(child, Tag):
                continue
            name = child.name
            if name in SKIP_TAGS:
                continue
            if name == "br":
                if current_p is None:
                    current_p = self._new_paragraph(container, style)
                current_p.add_run().add_break()
                continue

            child_style = compute_style(child, style, self.stylesheet)
            if child_style.get("display") == "none":
                continue

            if name == "img":
                if child_style.get("display") == "inline":
                    if current_p is None:
                        current_p = self._new_paragraph(container, style)
                    self._add_inline_image(current_p, child, child_style)
                else:
                    current_p = None
                    self._render_image_block(child, child_style, container)
                continue

            if name == "a":
                if current_p is None:
                    current_p = self._new_paragraph(container, style)
                self._render_link(current_p, child, child_style)
                continue

            if name in ("span", "b", "strong", "i", "em", "u", "s", "strike",
                        "del", "ins", "small", "sup", "sub", "code", "kbd",
                        "mark", "label", "abbr", "cite", "q", "time"):
                if current_p is None:
                    current_p = self._new_paragraph(container, style)
                self._render_inline(current_p, child, child_style)
                continue

            # -- éléments de type bloc : on referme le paragraphe courant --
            current_p = None
            self._render_block(child, child_style, container)

        return current_p

    def _add_text(self, paragraph, text, style, preserve=False):
        if preserve and "\n" in text:
            lines = text.split("\n")
            for i, line in enumerate(lines):
                if i > 0:
                    paragraph.add_run().add_break()
                if line:
                    run = paragraph.add_run(line)
                    apply_run_style(run, style)
        else:
            run = paragraph.add_run(text)
            apply_run_style(run, style)

    def _render_inline(self, paragraph, el, style):
        """Rendu d'un élément inline (peut contenir d'autres inline imbriqués)."""
        for child in el.children:
            if isinstance(child, NavigableString):
                preserve = style.get("white-space") == "pre"
                text = str(child) if preserve else re.sub(r"\s+", " ", str(child))
                if text:
                    self._add_text(paragraph, text, style, preserve=preserve)
            elif isinstance(child, Tag):
                if child.name in SKIP_TAGS:
                    continue
                if child.name == "br":
                    paragraph.add_run().add_break()
                    continue
                if child.name == "img":
                    child_style = compute_style(child, style, self.stylesheet)
                    self._add_inline_image(paragraph, child, child_style)
                    continue
                if child.name == "a":
                    child_style = compute_style(child, style, self.stylesheet)
                    self._render_link(paragraph, child, child_style)
                    continue
                child_style = compute_style(child, style, self.stylesheet)
                self._render_inline(paragraph, child, child_style)

    def _render_link(self, paragraph, a_el, style):
        href = a_el.get("href", "") or ""
        text = a_el.get_text()
        if not text:
            # lien contenant uniquement une image
            img = a_el.find("img")
            if img:
                self._add_inline_image(paragraph, img, compute_style(img, style, self.stylesheet))
            return
        try:
            r_id = paragraph.part.relate_to(href, RT.HYPERLINK, is_external=True) if href else None
        except Exception:
            r_id = None
        hyperlink = OxmlElement("w:hyperlink")
        if r_id:
            hyperlink.set(qn("r:id"), r_id)
        run_el = OxmlElement("w:r")
        rPr = OxmlElement("w:rPr")
        color_hex = style.get("color") or "0563C1"
        c = OxmlElement("w:color")
        c.set(qn("w:val"), color_hex)
        rPr.append(c)
        if style.get("underline", True):
            u = OxmlElement("w:u")
            u.set(qn("w:val"), "single")
            rPr.append(u)
        if style.get("bold"):
            rPr.append(OxmlElement("w:b"))
        if style.get("italic"):
            rPr.append(OxmlElement("w:i"))
        if style.get("font-size-pt"):
            sz = OxmlElement("w:sz")
            sz.set(qn("w:val"), str(int(style["font-size-pt"] * 2)))
            rPr.append(sz)
        run_el.append(rPr)
        t = OxmlElement("w:t")
        t.set(qn("xml:space"), "preserve")
        t.text = text
        run_el.append(t)
        hyperlink.append(run_el)
        paragraph._p.append(hyperlink)

    def _add_inline_image(self, paragraph, img_el, style):
        src = img_el.get("src", "")
        source = _load_image_source(src, self.base_dir)
        if source is None:
            run = paragraph.add_run(f"[image indisponible: {os.path.basename(src)}]")
            run.font.italic = True
            return
        width_pt, height_pt = _compute_image_dims_pt(source, style, self.content_width_pt)
        run = paragraph.add_run()
        kwargs = {}
        if width_pt:
            kwargs["width"] = Pt(width_pt)
        if height_pt:
            kwargs["height"] = Pt(height_pt)
        try:
            run.add_picture(source, **kwargs)
        except Exception:
            run = paragraph.add_run(f"[image invalide: {os.path.basename(src)}]")
            run.font.italic = True

    def _render_image_block(self, img_el, style, container):
        p = self._new_paragraph(container, style, apply_border=False)
        align = style.get("text-align")
        p.alignment = ALIGN_MAP.get(align, WD_ALIGN_PARAGRAPH.LEFT)
        self._add_inline_image(p, img_el, style)

    def _render_block(self, el, style, container):
        name = el.name

        if name == "hr":
            p = self._new_paragraph(container, style, apply_border=False)
            bord_bas = {"width_pt": 1.0, "color": "999999", "style": "solid"}
            set_paragraph_borders(p, {"bottom": bord_bas})
            return

        if name in ("ul", "ol"):
            self._render_list(el, style, container, ordered=(name == "ol"), level=0)
            return

        if name == "table":
            self._render_table(el, style, container)
            return

        # boîte avec fond et/ou bordure "pleine" -> rendue comme un tableau
        # à une cellule pour obtenir un vrai encadré autour de tout le contenu
        borders = style.get("border") or {}
        needs_box = bool(style.get("background-color")) or len(borders) >= 3
        if name in ("div", "section", "article", "header", "footer", "nav",
                    "main", "aside", "figure", "form", "fieldset") and needs_box:
            self._render_box(el, style, container)
            return

        # bloc "normal" : simple paragraphe stylé, contenu rendu dedans
        self._render_children(el, style, container)

    def _render_box(self, el, style, container):
        mt, mr, mb, ml = style.get("margin", (0, 0, 0, 0))
        pt_, pr_, pb_, pl_ = style.get("padding", (0, 0, 0, 0))
        if mt:
            add_spacer_paragraph(container, mt)
        width_pt = style.get("width-pt") or self.content_width_pt
        width_pt = min(width_pt, self.content_width_pt)
        table = container.add_table(rows=1, cols=1)
        table.autofit = False
        table.alignment = WD_TABLE_ALIGNMENT.LEFT
        table.columns[0].width = Emu(int(width_pt * EMU_PER_PT))
        cell = table.cell(0, 0)
        cell.width = Emu(int(width_pt * EMU_PER_PT))
        if style.get("background-color"):
            set_table_cell_shading(cell, style["background-color"])
        if style.get("border"):
            set_table_borders(table, style["border"])
        else:
            set_table_borders(table, {})
        set_cell_margins(cell, pt_ or 4, pr_ or 4, pb_ or 4, pl_ or 4)
        cell.paragraphs[0].text = ""
        inner_style = dict(style)
        inner_style["margin"] = (0.0, 0.0, 0.0, 0.0)
        inner_style["padding"] = (0.0, 0.0, 0.0, 0.0)
        inner_style["background-color"] = None
        inner_style["border"] = {}
        self._render_children(el, inner_style, cell, seed_paragraph=cell.paragraphs[0])
        if mb:
            add_spacer_paragraph(container, mb)

    def _render_list(self, el, style, container, ordered, level):
        style_name = "List Number" if ordered else "List Bullet"
        if level > 0:
            style_name += f" {min(level + 1, 3)}"
        for li in el.find_all("li", recursive=False):
            li_style = compute_style(li, style, self.stylesheet)
            p = container.add_paragraph()
            try:
                p.style = self.doc.styles[style_name]
            except KeyError:
                pass
            apply_paragraph_box_style(p, li_style, apply_border=False)
            nested_lists = li.find_all(["ul", "ol"], recursive=False)
            # rendu du contenu direct de <li> (hors listes imbriquées) dans p
            self._render_children_filtered(li, li_style, container, p, exclude=nested_lists)
            for nested in nested_lists:
                nested_style = compute_style(nested, li_style, self.stylesheet)
                self._render_list(nested, nested_style, container,
                                   ordered=(nested.name == "ol"), level=level + 1)

    def _render_children_filtered(self, el, style, container, seed_paragraph, exclude):
        current_p = seed_paragraph
        for child in el.children:
            if child in exclude:
                continue
            if isinstance(child, NavigableString):
                text = re.sub(r"\s+", " ", str(child))
                if text.strip() == "":
                    continue
                if current_p is None:
                    current_p = self._new_paragraph(container, style)
                self._add_text(current_p, text, style)
                continue
            if not isinstance(child, Tag) or child.name in SKIP_TAGS:
                continue
            if child.name == "br":
                if current_p is None:
                    current_p = self._new_paragraph(container, style)
                current_p.add_run().add_break()
                continue
            child_style = compute_style(child, style, self.stylesheet)
            if child.name == "img":
                if current_p is None:
                    current_p = self._new_paragraph(container, style)
                self._add_inline_image(current_p, child, child_style)
            elif child.name == "a":
                if current_p is None:
                    current_p = self._new_paragraph(container, style)
                self._render_link(current_p, child, child_style)
            else:
                if current_p is None:
                    current_p = self._new_paragraph(container, style)
                self._render_inline(current_p, child, child_style)

    def _render_table(self, table_el, style, container):
        rows = table_el.find_all("tr", recursive=True)
        # on exclut les <tr> appartenant à une table imbriquée
        rows = [r for r in rows if r.find_parent("table") is table_el]
        if not rows:
            return
        # --- Placement en grille : colspan/rowspan HTML réels ----------------
        # L'ancien code ignorait les fusions : une ligne de total
        # ``<td colspan="4">`` décalait toutes les cellules suivantes (et la
        # largeur de grille ne correspondait plus au HTML). Chaque cellule est
        # placée à sa colonne de grille puis **fusionnée** (``cell.merge``
        # python-docx) sur sa portée réelle — fidélité au HTML source.
        nb_lignes = len(rows)
        occupées = {}  # (ligne, colonne) -> ligne de la cellule d'origine
        placements = []  # par ligne : (element, colonne, colspan, rowspan)
        ncols = 0
        for i, row_el in enumerate(rows):
            rangée = []
            colonne = 0
            for cell_el in row_el.find_all(["td", "th"], recursive=False):
                while (i, colonne) in occupées:
                    colonne += 1
                try:
                    colspan = max(1, int(cell_el.get("colspan", 1)))
                except (TypeError, ValueError):
                    colspan = 1
                try:
                    rowspan = max(1, int(cell_el.get("rowspan", 1)))
                except (TypeError, ValueError):
                    rowspan = 1
                rowspan = min(rowspan, nb_lignes - i)  # jamais au-delà du tableau
                for ligne_v in range(i, i + rowspan):
                    for col_v in range(colonne, colonne + colspan):
                        occupées[(ligne_v, col_v)] = i
                rangée.append((cell_el, colonne, colspan, rowspan))
                colonne += colspan
            placements.append(rangée)
            ncols = max(ncols, colonne)
        if ncols == 0:
            return
        table = container.add_table(rows=nb_lignes, cols=ncols)
        table.autofit = True
        col_width_pt = self.content_width_pt / ncols
        for col in table.columns:
            col.width = Emu(int(col_width_pt * EMU_PER_PT))
        bord_fin = {"width_pt": 0.75, "color": "999999", "style": "solid"}
        borders = style.get("border") or {
            "top": dict(bord_fin),
            "bottom": dict(bord_fin),
            "left": dict(bord_fin),
            "right": dict(bord_fin),
        }
        table_full_borders = dict(borders)
        bord_interne = {"width_pt": 0.75, "color": "cccccc", "style": "solid"}
        table_full_borders.setdefault("insideH", dict(bord_interne))
        table_full_borders.setdefault("insideV", dict(bord_interne))
        set_table_borders(table, table_full_borders)

        for i, rangée in enumerate(placements):
            for cell_el, colonne, colspan, rowspan in rangée:
                cell = table.cell(i, colonne)
                if colspan > 1 or rowspan > 1:
                    # Fusion réelle DOCX sur la portée HTML (rectangulaire).
                    cell = cell.merge(
                        table.cell(i + rowspan - 1, colonne + colspan - 1)
                    )
                cell_style = compute_style(cell_el, style, self.stylesheet)
                if cell_el.name == "th" and "font-weight" not in (cell_el.get("style") or ""):
                    cell_style["bold"] = True
                if cell_style.get("background-color"):
                    set_table_cell_shading(cell, cell_style["background-color"])
                set_cell_margins(cell, 3, 4, 3, 4)
                cell.paragraphs[0].text = ""
                self._render_children(cell_el, cell_style, cell, seed_paragraph=cell.paragraphs[0])


# =========================================================================
# 8. API publique — octets en mémoire (intégration CARSO)
# =========================================================================

def html_string_to_docx_bytes(html_string: str, *, css_string: str | None = None) -> bytes:
    """Convertit une **chaîne** HTML en octets DOCX, entièrement en mémoire.

    Args:
        html_string: page HTML complète (ou fragment) avec ses ``<style>``.
        css_string: CSS additionnel appliqué après celui du HTML.

    Returns:
        Les octets du ``.docx`` produit (à déposer via ``DocumentService``).

    Raises:
        ConversionHtmlError: HTML vide ou sans contenu rendable.
    """
    if not (html_string or "").strip():
        raise ConversionHtmlError("La chaîne HTML fournie est vide.")

    converter = HtmlToDocxConverter(extra_css=css_string or "")
    soup = BeautifulSoup(html_string, "html.parser")
    css_text = "\n".join(tag.get_text() for tag in soup.find_all("style"))
    css_text += "\n" + converter.extra_css
    converter.stylesheet = Stylesheet(css_text)

    from docx import Document as _Document  # import local : clarté du flux

    converter.doc = _Document()
    section = converter.doc.sections[0]
    converter.content_width_pt = (
        section.page_width - section.left_margin - section.right_margin
    ) / EMU_PER_PT

    body = soup.find("body") or soup
    if not body.get_text(strip=True) and not body.find("img"):
        raise ConversionHtmlError(
            "Le HTML fourni ne contient ni texte ni image : rien à convertir."
        )
    root_style = compute_style(body, default_root_style(), converter.stylesheet)
    if root_style.get("background-color"):
        set_page_background(converter.doc, root_style["background-color"])
    converter._render_children(body, root_style, converter.doc)

    tampon = BytesIO()
    converter.doc.save(tampon)
    return tampon.getvalue()


def html_to_docx_bytes(html_bytes: bytes, *, css_string: str | None = None) -> bytes:
    """Convertit des **octets** HTML (ex. contenu d'un ``Document`` CARSO) en
    octets DOCX. Même comportement que ``html_string_to_docx_bytes``."""
    return html_string_to_docx_bytes(
        html_bytes.decode("utf-8", errors="replace"), css_string=css_string
    )
