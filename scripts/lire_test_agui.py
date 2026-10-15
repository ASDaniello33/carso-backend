"""Lit le dump SSE de test et reconstruit le texte de l'agent (diagnostic)."""

import io
import json
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

contenu = open("agui_test.out", encoding="utf-8", errors="replace").read()
deltas = re.findall(r'"delta":"((?:[^"\\]|\\.)*)"', contenu)
texte = "".join(json.loads('"' + d + '"') for d in deltas)
print(texte[:600])
