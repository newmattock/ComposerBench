"""Semantic role registry: musical roles -> concrete device/param/FX recipes.

Roles are YAML files under ~/.musicbench/semantics/ (or a provided dir).
Engine slugs resolve through ~/.musicbench/devices/aliases.json so recipes
never hard-code VST3 uid strings.
"""
import json
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, ValidationError, model_validator


class ParamSpec(BaseModel):
    """One parameter to shape.

    ``names`` are candidate parameter names, tried in order (exact matches
    before substring matches, the plugin's own parameters before the
    engine's wrapper ones). ``label`` picks a state by its display text
    ("LP 12 dB", "Hall", "1/8.") and is resolved against the plugin's own
    labels when the recipe is applied; ``value`` is the native number
    (normalised 0..1 for hosted plugins). With both, the label wins and the
    value is the fallback for engines that cannot report labels.
    """
    names: list[str]
    value: float | None = None
    label: str | None = None
    pinned: bool = False
    note: str | None = None

    @model_validator(mode="after")
    def _needs_a_target(self):
        if not self.names:
            raise ValueError("a param needs at least one candidate name")
        if self.value is None and self.label is None:
            raise ValueError("a param needs a value, a label, or both")
        return self

    def key(self) -> tuple[str, ...]:
        return tuple(sorted(n.lower() for n in self.names))

    def overlaps(self, names) -> bool:
        return bool({n.lower() for n in self.names} & {str(n).lower() for n in names})


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
        self.skipped_learned: list[dict] = []

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

        Authored values marked pinned win. A learned document is skipped (and
        recorded in ``self.skipped_learned`` with the reason) when it is
        marked ``"invalid": true``, or when the recipe context it was learned
        in no longer matches: learned values only mean something next to the
        other parameters that were set at the time (a cutoff learned while
        the filter was Off says nothing about a low-pass). Documents without
        a ``context`` are only trusted for recipes with a single parameter.
        Returns updated role slugs.
        """
        learned_dir = Path(learned_dir).expanduser()
        self.skipped_learned: list[dict] = []
        touched = []
        for jf in sorted(Path(learned_dir).glob("*.json")):
            doc = json.loads(jf.read_text())
            slug = doc.get("role")
            recipe = self.roles.get(slug)
            if recipe is None:
                continue
            reason = self._stale_reason(recipe, doc)
            if reason:
                self.skipped_learned.append({"file": jf.name, "role": slug, "reason": reason})
                continue
            for lp in doc.get("params", []):
                target = next((p for p in recipe.params
                               if p.overlaps(lp.get("names", []))), None)
                if target is None or target.pinned:
                    continue
                target.value = float(lp["value"])
            if slug not in touched:
                touched.append(slug)
        return touched

    @staticmethod
    def _stale_reason(recipe: "RoleRecipe", doc: dict) -> str | None:
        if doc.get("invalid"):
            return str(doc.get("invalid_reason") or "marked invalid")
        learned_names = [n for lp in doc.get("params", []) for n in lp.get("names", [])]
        others = [p for p in recipe.params if not p.overlaps(learned_names)]
        context = doc.get("context")
        if context is None:
            return ("learned without recording the other parameters of the recipe"
                    if others else None)
        recorded = {tuple(sorted(n.lower() for n in c.get("names", []))): c for c in context}
        for p in others:
            c = recorded.pop(p.key(), None)
            if c is None:
                return f"recipe now also sets {p.names[0]!r}"
            if c.get("label") != p.label or (
                    c.get("value") is not None and p.value is not None
                    and abs(float(c["value"]) - p.value) > 1e-6):
                return f"recipe changed {p.names[0]!r} since learning"
        if recorded:
            return "recipe no longer sets " + ", ".join(repr(c["names"][0]) for c in recorded.values())
        return None


DEFAULT_DIR = Path.home() / ".musicbench" / "semantics"


def load_default() -> SemanticRegistry:
    return SemanticRegistry.load_from(DEFAULT_DIR)
