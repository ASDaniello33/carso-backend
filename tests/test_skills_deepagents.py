"""Accès des agents à leurs skills Deep Agents (cause du « je n'ai pas accès »).

``SkillsMiddleware`` lit ses sources via ``backend.ls`` + ``backend.download_files``
et n'accepte que des chemins **POSIX relatifs au backend**. Le harnais passait
les chemins hôtes des dossiers (``C:\\...\\skills\\docx-officiels``) : aucune
route ne correspondait, chaque lecture tombait sur le ``StateBackend`` vide et
le modèle répondait qu'il n'avait aucun accès aux skills documents.

Ces tests montent la vue ``BackendSkills`` comme le fait ``_backend_fichiers``
et vérifient le contrat réellement consommé par Deep Agents.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from deepagents.backends import CompositeBackend, StateBackend
from deepagents.middleware.skills import _list_skills_with_errors

from app.agents.backend_skills import BackendSkills
from app.agents.registre_skills import SOURCE_SKILLS


def _ecrire_skill(racine: Path, nom: str, description: str = "Guide de test") -> None:
    dossier = racine / nom
    dossier.mkdir(parents=True, exist_ok=True)
    (dossier / "SKILL.md").write_text(
        f"---\nname: {nom}\ndescription: {description}\n---\n\n# {nom}\n\nCorps.\n",
        encoding="utf-8",
    )


@pytest.fixture
def racine(tmp_path: Path) -> Path:
    skills = tmp_path / "skills"
    _ecrire_skill(skills, "docx-officiels", "Lire et cloner un DOCX officiel")
    _ecrire_skill(skills, "pdf-officiels", "Lire un PDF par plage de pages")
    _ecrire_skill(skills, "scripts-python", "Scripts autorisés")
    return skills


def _noms_charges(backend: Any) -> list[str]:
    skills, erreur = _list_skills_with_errors(backend, SOURCE_SKILLS)
    assert erreur is None, erreur
    return sorted(skill["name"] for skill in skills)


def test_les_skills_declares_sont_charges(racine: Path) -> None:
    backend = BackendSkills(racine, ("docx-officiels", "pdf-officiels"))

    assert _noms_charges(backend) == ["docx-officiels", "pdf-officiels"]

    # Le prompt renvoie ce chemin au modèle : il doit être lisible.
    lecture = backend.read("/skills/docx-officiels/SKILL.md")
    assert lecture.error is None
    assert "docx-officiels" in (lecture.file_data or {}).get("content", "")


def test_les_skills_non_declares_sont_refuses(racine: Path) -> None:
    """Le contrat de l'agent est une borne : les autres skills restent clos."""
    backend = BackendSkills(racine, ("docx-officiels",))

    assert "scripts-python" not in _noms_charges(backend)
    refus = backend.ls("/skills/scripts-python")
    assert refus.error is not None
    assert "scripts-python" in refus.error

    telechargement = backend.download_files(["/skills/scripts-python/SKILL.md"])
    assert telechargement[0].content is None
    assert telechargement[0].error == "file_not_found"


def test_les_skills_sont_en_lecture_seule(racine: Path) -> None:
    backend = BackendSkills(racine, ("docx-officiels",))

    assert backend.write("/skills/docx-officiels/SKILL.md", "écrasé").error
    assert backend.edit("/skills/docx-officiels/SKILL.md", "Corps", "Autre").error
    assert (racine / "docx-officiels" / "SKILL.md").read_text(
        encoding="utf-8"
    ).endswith("Corps.\n")


def test_le_backend_composite_expose_les_skills(racine: Path) -> None:
    """La route ``/skills/`` ne double pas le préfixe des chemins rendus."""
    composite = CompositeBackend(
        default=StateBackend(),
        routes={SOURCE_SKILLS: BackendSkills(racine, ("docx-officiels",))},
    )

    assert _noms_charges(composite) == ["docx-officiels"]
    listing = composite.ls("/skills/")
    assert listing.error is None
    assert [entree["path"] for entree in listing.entries or []] == [
        "/skills/docx-officiels"
    ]
    assert composite.download_files(["/skills/docx-officiels/SKILL.md"])[0].error is None


def test_le_harnais_route_les_skills_declares(tmp_path: Path, racine: Path, monkeypatch) -> None:
    """``_backend_fichiers`` monte la route des skills pour un agent donné."""
    from app.agents import harness
    from app.core.config import Settings

    monkeypatch.setattr(
        "app.agents.registre_skills.racine_skills", lambda settings=None: racine
    )
    reglages = Settings(storage_root=str(tmp_path / "storage"))

    avec = harness._backend_fichiers(reglages, skills=("docx-officiels",))
    sans = harness._backend_fichiers(reglages, skills=())

    assert _noms_charges(avec) == ["docx-officiels"]
    assert SOURCE_SKILLS not in sans.routes


def test_le_prompt_annonce_le_chemin_virtuel_des_skills() -> None:
    """Le bloc de prompt doit donner le chemin réellement lisible au modèle."""
    from app.agents.generateur_offre import DEFINITION
    from app.agents.registre_skills import bloc_skills

    bloc = bloc_skills(DEFINITION)

    assert f"`{SOURCE_SKILLS}<nom>/SKILL.md" in bloc
    assert "lecture seule" in bloc


def test_le_graphe_expose_les_skills_au_modele(tmp_path: Path) -> None:
    """Bout en bout : le prompt réellement reçu par le modèle liste les skills.

    C'est la plainte d'origine — le modèle répondait « je n'ai pas accès aux
    skills » alors que le prompt les annonçait. Ici le graphe est monté par le
    harnais (route `/skills/` + `SkillsMiddleware`) et le modèle scripté relit
    le message système qu'il a reçu.
    """
    from app.agents import GENERATEUR_DEFINITION
    from app.agents.harness import AgentRuntimeConfig, create_carso_deep_agent
    from app.core.config import Settings
    from tests.agent_fixtures import ScriptedChatModel, final_message

    modele = ScriptedChatModel(responses=[final_message("prêt")])
    agent = create_carso_deep_agent(
        definition=GENERATEUR_DEFINITION,
        tools=[],
        runtime=AgentRuntimeConfig(model=modele),
        settings=Settings(storage_root=str(tmp_path / "storage")),
    )
    agent.invoke(
        {"messages": [("user", "bonjour")]},
        config={"configurable": {"thread_id": "skills-check"}},
    )

    prompt = str(modele.received[0][0].content)
    assert "docx-officiels" in prompt
    assert f"{SOURCE_SKILLS}generation-document/SKILL.md" in prompt
    # Un skill non déclaré par cet agent ne doit pas apparaître.
    assert "xlsx-officiels" not in prompt
