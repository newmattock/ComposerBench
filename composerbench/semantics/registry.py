"""Semantic role registry: musical roles -> concrete device/param/FX recipes.

Roles are YAML files under ~/.musicbench/semantics/ (or a provided dir).
Engine slugs resolve through ~/.musicbench/devices/aliases.json so recipes
never hard-code VST3 uid strings.
"""
import json
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, ValidationError


class ParamSpec(BaseModel):
    """One parameter to shape: tried in candidate-name order."""
    names: list[str]
    value: float
    pinned: bool = False


class SpaceSpec(BaseModel):
    """Depth/space treatment attached by apply_role."""
    reverb: str | None = None          # alias slug e.g. dragonfly_hall
    reverb_wet: float | None = None
    delay: str | None = None           # alias slug e.g. chowmatrix
    wet: float = 0.3


class RoleRecipe(BaseModel):
    slug: str
    engine: str                        # alias slug e.g. surge_xt / magda_marimba
    as_instrument: bool = True
    params: list[ParamSpec] = Field(default_factory=list)
    space: SpaceSpec = Field(default_factory=SpaceSpec)
    register_range: tuple[int, int] = Field(default=(36, 84),
                                            alias="register")
    level: float = 0.85
    character: list[str] = Field(default_factory=list)


class SemanticRegistry:
    def __init__(self, roles: dict[str, RoleRecipe], aliases: dict[str, str]):
        self.roles = roles
        self.aliases = aliases

    @classmethod
    def load_from(cls, directory: Path | str) -> "SemanticRegistry":
        """Load from a semantics dir, or a parent containing semantics/ +
        devices/ (mirroring the ~/.musicbench layout)."""
        directory = Path(directory).expanduser()
        if not directory.exists():
            raise FileNotFoundError(directory)
        if directory.name != "semantics" and (directory / "semantics").is_dir():
            directory = directory / "semantics"
        aliases_file = directory.parent / "devices" / "aliases.json"
        if not aliases_file.exists():
            aliases_file = Path.home() / ".musicbench" / "devices" / "aliases.json"
        aliases: dict[str, str] = {}
        if aliases_file.exists():
            aliases = json.loads(aliases_file.read_text())

        roles: dict[str, RoleRecipe] = {}
        for pattern in ("*.yaml", "*.yml"):
            for yml in sorted(directory.glob(pattern)):
                doc = yaml.safe_load(yml.read_text()) or {}
                entries = doc.get("roles", doc)
                if isinstance(entries, dict):
                    entries = [{"slug": k, **v} for k, v in entries.items()]
                for entry in entries:
                    try:
                        recipe = RoleRecipe(**entry)
                    except ValidationError as e:
                        raise ValueError(
                            f"{yml.name}: bad role {entry.get('slug')}: {e}") from e
                    if recipe.engine not in aliases \
                            and not recipe.engine.startswith("magda_") \
                            and recipe.engine != "4osc":
                        raise ValueError(
                            f"{yml.name}: role '{recipe.slug}' engine "
                            f"'{recipe.engine}' has no alias")
                    roles[recipe.slug] = recipe
        return cls(roles, aliases)

    def get(self, slug: str) -> RoleRecipe:
        return self.roles[slug]

    def resolve_engine(self, slug: str) -> str:
        """Alias slug -> concrete plugin id (magda_ ids pass through)."""
        return self.aliases.get(slug, slug)

    def by_character(self, *tags: str) -> list[RoleRecipe]:
        out = []
        for r in self.roles.values():
            if all(t in r.character for t in tags):
                out.append(r)
        return out

    def merge_learned(self, learned_dir: Path | str) -> list[str]:
        """Overlay learned/*.json param overrides onto authored defaults.
        Authored values marked pinned win. Returns updated role slugs."""
        learned_dir = Path(learned_dir).expanduser()
        touched = []
        for jf in sorted(Path(learned_dir).glob("*.json")):
            doc = json.loads(jf.read_text())
            slug = doc.get("role")
            recipe = self.roles.get(slug)
            if recipe is None:
                continue
            for lp in doc.get("params", []):
                target = next((p for p in recipe.params
                               if set(p.names) & set(lp.get("names", []))), None)
                if target is None or target.pinned:
                    continue
                target.value = float(lp["value"])
            if slug not in touched:
                touched.append(slug)
        return touched


DEFAULT_DIR = Path.home() / ".musicbench" / "semantics"


def load_default() -> SemanticRegistry:
    return SemanticRegistry.load_from(DEFAULT_DIR)
