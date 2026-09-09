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

# Development canonical root; packaged deployments configure `REVOLAB_SKILLS_ROOT`
# because the repo-relative path is NOT part of the installed wheel.
_DEV_SKILLS_ROOT = Path(__file__).resolve().parents[4] / ".agents" / "skills"


def _default_skill_root() -> Path:
    from revolab.config import get_settings

    configured = get_settings().skills_root
    if configured:
        return Path(configured)
    return _DEV_SKILLS_ROOT

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
    """Read-only pointer catalog over the canonical `.agents/skills/` tree.

    The root is configurable (`REVOLAB_SKILLS_ROOT`); in development it defaults
    to the repo tree. A packaged deployment must configure an explicit root: the
    repo-relative path is not part of the installed wheel.
    """

    def __init__(self, root: Path | None = None) -> None:
        self._root = root if root is not None else _default_skill_root()

    def skill_path(self, skill_id: str) -> Path:
        return self._root / skill_id / "SKILL.md"

    def load(self, skill_id: str) -> SkillRef:
        path = self.skill_path(skill_id)
        if not path.is_file():
            # Fail closed: a requested skill that does not exist must not be
            # silently reported as loaded. Name the resolved root so a packaged
            # deployment misconfiguration is diagnosable, not a bare 500.
            raise FileNotFoundError(f"skill not found: {skill_id} (root={self._root})")
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


def select_skills(
    selection: ContextSelectionCreate | None,
    *,
    skill_root: Path | None = None,
) -> list[SkillRef]:
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
    catalog = SkillCatalog(skill_root)
    return [catalog.load(skill_id) for skill_id in sorted(choices)]
