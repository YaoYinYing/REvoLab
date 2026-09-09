"""SkillCatalog — the minimal task/context -> skill mapping (TODO.md section 11).

The canonical skill files remain `.agents/skills/<name>/SKILL.md`; this catalog is
a thin pointer, never a content database. It demonstrates loading a small relevant
subset for the Phase-6 slice and resolves only identifiers + the frontmatter
summary, never copying the skill body into ProjectContext.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from revolab.schemas import ContextSelectionCreate

# Repo-root-relative location of the single canonical skill tree.
_SKILLS_ROOT = Path(__file__).resolve().parents[4] / ".agents" / "skills"

# The small Phase-6 relevance subset. Integration skills (revocompute / revodesign
# / openbio) are intentionally absent: they exist only when the provider exists.
_PHASE6_SKILL_IDS = ("project-context", "decision-record", "provenance-lineage")


@dataclass(frozen=True)
class SkillRef:
    id: str
    name: str
    version: str
    description: str
    path: str


def _frontmatter(path: Path) -> dict[str, str]:
    """Minimal YAML-frontmatter reader for the `name`/`version`/`description`
    keys. No YAML dependency: the frontmatter shape is project-controlled."""
    values: dict[str, str] = {}
    lines = path.read_text(encoding="utf-8").splitlines()
    in_frontmatter = False
    for line in lines:
        stripped = line.strip()
        if stripped == "---":
            if not in_frontmatter:
                in_frontmatter = True
            else:
                break
            continue
        if in_frontmatter and ":" in stripped:
            key, _, value = stripped.partition(":")
            values[key.strip()] = value.strip()
    return values


class SkillCatalog:
    """Read-only pointer catalog over the canonical `.agents/skills/` tree."""

    def __init__(self, root: Path | None = None) -> None:
        self._root = root or _SKILLS_ROOT

    def skill_path(self, skill_id: str) -> Path:
        return self._root / skill_id / "SKILL.md"

    def load(self, skill_id: str) -> SkillRef:
        path = self.skill_path(skill_id)
        if not path.is_file():
            # Fail closed: a requested skill that does not exist must not be
            # silently reported as loaded.
            raise FileNotFoundError(f"skill not found: {skill_id}")
        meta = _frontmatter(path)
        return SkillRef(
            id=skill_id,
            name=meta.get("name", skill_id),
            version=meta.get("version", "0.0.0"),
            description=meta.get("description", ""),
            path=str(path),
        )

    def list_phase6(self) -> list[SkillRef]:
        return [self.load(skill_id) for skill_id in _PHASE6_SKILL_IDS]


def select_skills(selection: ContextSelectionCreate | None) -> list[SkillRef]:
    """Deterministic relevance heuristic (no vector index, no RAG): the context
    skill is always loaded; `decision-record` loads when decisions are requested;
    `provenance-lineage` loads when relations/references are requested. This is
    a demonstration slice — not an always-on encyclopedia."""
    selection = selection or ContextSelectionCreate()
    choices = {"project-context"}
    if selection.include_decisions:
        choices.add("decision-record")
    if selection.include_relations or selection.include_references:
        choices.add("provenance-lineage")
    catalog = SkillCatalog()
    return [catalog.load(skill_id) for skill_id in sorted(choices)]
