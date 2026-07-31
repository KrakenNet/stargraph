# SPDX-License-Identifier: Apache-2.0
"""SKILL.md loader -- Claude-compatible markdown skills (P2).

A skill is a directory containing ``SKILL.md``: YAML frontmatter between
``---`` fences, markdown body after. The **minimum valid skill is the
Claude Code format** -- ``name`` + ``description`` frontmatter and a
body -- and any stock Claude Code skill loads unmodified: unknown
frontmatter keys (``argument-hint``, ``allowed-tools``, ...) are
preserved-but-ignored, never errors. The body becomes the skill's
``system_prompt``.

Optional Stargraph superset keys:

``version`` (default ``0.1.0``) · ``kind`` (``agent``/``workflow``/
``utility``, default ``agent``) · ``namespace`` (default ``local``) ·
``tools: [ns.name@ver, ...]`` · ``requires: [capability, ...]`` ·
``subgraph: <ir path>`` (relative to the SKILL.md) ·
``state_schema: {field: <type> | {type: <type>, default: ...}}``
(types ``str``/``int``/``float``/``bool``/``list``/``dict``) ·
``examples: [{inputs: {...}, expected_output: {...}}]``.

Discovery (:func:`discover_skill_files`) walks, in order,
``$STARGRAPH_SKILLS_DIR``, ``./skills/``, ``~/.stargraph/skills/`` for
``*/SKILL.md`` -- the Claude Code layout. Loading does NOT go through
the plugin entry-point pipeline (that is for external pip dists; see
``plugin/loader.py``); :func:`seed_markdown_skills` registers compiled
:class:`~stargraph.ir.SkillSpec` records straight onto the
``ToolRegistry``, keeping the loader's duplicate-id check
(:class:`~stargraph.errors.PluginLoadError` on conflict).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import yaml
from pydantic import BaseModel, create_model

from stargraph.errors import PluginLoadError, ValidationError
from stargraph.ir._models import SkillSpec
from stargraph.skills.base import Example, Skill, SkillKind
from stargraph.skills.react import ReactSkill

if TYPE_CHECKING:
    from stargraph.registry.tools import ToolRegistry

__all__ = [
    "CompiledSkill",
    "compile_skill_md",
    "discover_skill_files",
    "seed_markdown_skills",
]

DEFAULT_VERSION = "0.1.0"
DEFAULT_KIND = "agent"
DEFAULT_NAMESPACE = "local"

#: Claude Code frontmatter keys we accept silently (compat superset).
_KNOWN_FOREIGN_KEYS = frozenset(
    {"argument-hint", "allowed-tools", "disable-model-invocation", "model", "user-invocable"}
)
_STARGRAPH_KEYS = frozenset(
    {
        "name",
        "description",
        "version",
        "kind",
        "namespace",
        "tools",
        "requires",
        "subgraph",
        "state_schema",
        "examples",
    }
)

_TYPE_MAP: dict[str, type] = {
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "list": list,
    "dict": dict,
}


@dataclass(frozen=True)
class CompiledSkill:
    """One compiled SKILL.md: the registry record + the runtime object."""

    path: Path
    spec: SkillSpec
    skill: Skill
    warnings: tuple[str, ...] = field(default=())


def _fail(path: Path, message: str, *, hint: str = "") -> ValidationError:
    return ValidationError(
        "SKILL.md validation failed",
        path=str(path),
        expected="Claude-compatible SKILL.md (name + description frontmatter)",
        actual=message,
        hint=hint or "see docs/how-to/write-markdown-skill.md",
    )


def _split_frontmatter(text: str, path: Path) -> tuple[dict[str, Any], str]:
    """Split ``---``-fenced YAML frontmatter from the markdown body.

    ``maxsplit=1`` so a ``---`` horizontal rule inside the body survives
    intact; only the first closing fence terminates the frontmatter.
    """
    if not text.startswith("---"):
        raise _fail(path, "missing '---' frontmatter fence on line 1")
    parts = text.split("\n---", 1)
    if len(parts) < 2:
        raise _fail(path, "unterminated frontmatter (no closing '---')")
    raw_meta = parts[0].removeprefix("---")
    # Drop the remainder of the closing-fence line itself.
    rest = parts[1]
    body = rest.split("\n", 1)[1] if "\n" in rest else ""
    try:
        meta = yaml.safe_load(raw_meta)
    except yaml.YAMLError as exc:
        raise _fail(path, f"frontmatter is not valid YAML: {exc}") from exc
    if not isinstance(meta, dict):
        raise _fail(path, f"frontmatter must be a YAML mapping, got {type(meta).__name__}")
    return cast("dict[str, Any]", meta), body.lstrip("\n")


def _compile_state_schema(raw: Any, path: Path, class_name: str) -> type[BaseModel]:
    """Field-map ``{field: type | {type, default}}`` -> a pydantic model."""
    if not isinstance(raw, dict):
        raise _fail(path, f"state_schema must be a mapping, got {type(raw).__name__}")
    raw_map = cast("dict[str, Any]", raw)
    fields: dict[str, Any] = {}
    for name, decl in raw_map.items():
        if isinstance(decl, str):
            type_name, default = decl, None
        elif isinstance(decl, dict):
            decl_map = cast("dict[str, Any]", decl)
            type_name = str(decl_map.get("type", ""))
            default = decl_map.get("default")
        else:
            raise _fail(path, f"state_schema.{name} must be a type name or mapping")
        py_type = _TYPE_MAP.get(type_name)
        if py_type is None:
            raise _fail(
                path,
                f"state_schema.{name}: unknown type {type_name!r}",
                hint=f"one of {sorted(_TYPE_MAP)}",
            )
        fields[name] = (py_type, default)
    return create_model(class_name, **fields)


def _str_list(meta: dict[str, Any], key: str, path: Path) -> list[str]:
    raw: Any = meta.get(key) or []
    if not isinstance(raw, list):
        raise _fail(path, f"{key} must be a list, got {type(raw).__name__}")
    return [str(item) for item in cast("list[Any]", raw)]


def compile_skill_md(path: Path) -> CompiledSkill:
    """Compile one SKILL.md into a registry :class:`SkillSpec` + runtime :class:`Skill`.

    Raises :class:`~stargraph.errors.ValidationError` (structured: the
    offending file in ``path`` context) on anything malformed. Unknown
    frontmatter keys warn, never fail -- stock Claude Code skills load
    unmodified.
    """
    meta, body = _split_frontmatter(path.read_text(encoding="utf-8"), path)

    name = str(meta.get("name", "")).strip()
    description = str(meta.get("description", "")).strip()
    if not name:
        raise _fail(path, "frontmatter is missing the required 'name' key")
    if not description:
        raise _fail(path, "frontmatter is missing the required 'description' key")

    kind = str(meta.get("kind", DEFAULT_KIND))
    if kind not in ("agent", "workflow", "utility"):
        raise _fail(path, f"kind must be agent/workflow/utility, got {kind!r}")

    warnings = tuple(
        f"ignored unknown frontmatter key {key!r}"
        for key in meta
        if key not in _STARGRAPH_KEYS and key not in _KNOWN_FOREIGN_KEYS
    )

    subgraph = meta.get("subgraph")
    if subgraph is not None:
        resolved = (path.parent / str(subgraph)).resolve()
        if not resolved.is_file():
            raise _fail(path, f"subgraph {str(subgraph)!r} does not exist at {resolved}")
        subgraph = str(resolved)

    tools = _str_list(meta, "tools", path)
    requires = _str_list(meta, "requires", path)
    version = str(meta.get("version", DEFAULT_VERSION))
    namespace = str(meta.get("namespace", DEFAULT_NAMESPACE))
    system_prompt = body.strip() or None

    raw_examples: Any = meta.get("examples") or []
    if not isinstance(raw_examples, list):
        raise _fail(path, "examples must be a list of {inputs, expected_output} mappings")
    examples: list[Example] = []
    for raw_example in cast("list[Any]", raw_examples):
        if not isinstance(raw_example, dict):
            raise _fail(path, "examples entries must be mappings")
        example_map = cast("dict[str, Any]", raw_example)
        examples.append(
            Example(
                inputs=cast("dict[str, Any]", example_map.get("inputs") or {}),
                expected_output=example_map.get("expected_output"),
            )
        )

    class_name = "MdSkillState_" + "".join(c if c.isalnum() else "_" for c in name)
    if "state_schema" in meta:
        state_schema = _compile_state_schema(meta["state_schema"], path, class_name)
    else:
        state_schema = None  # pick the runtime default below

    common: dict[str, Any] = {
        "name": name,
        "version": version,
        "description": description,
        "tools": tools,
        "requires": requires,
        "subgraph": subgraph,
        "system_prompt": system_prompt,
        "examples": examples,
    }
    if subgraph is None and kind == "agent":
        # No subgraph declared -> the default ReAct loop over the listed
        # tools (an empty list is fine: pure-prompt skill).
        skill: Skill = ReactSkill(
            kind=SkillKind.agent,
            **({"state_schema": state_schema} if state_schema is not None else {}),
            **common,
        )
    else:
        skill = Skill(
            kind=SkillKind(kind),
            state_schema=state_schema or create_model(class_name),
            **common,
        )

    spec = SkillSpec(
        name=name,
        namespace=namespace,
        version=version,
        description=description,
        kind=cast("Any", kind),
        tools=tools,
        examples=[{"inputs": ex.inputs, "expected_output": ex.expected_output} for ex in examples],
        subgraph=subgraph,
        system_prompt=system_prompt,
    )
    return CompiledSkill(path=path, spec=spec, skill=skill, warnings=warnings)


def discover_skill_files(extra_dir: str | Path | None = None) -> list[Path]:
    """``*/SKILL.md`` files under the discovery roots, in precedence order.

    Roots: ``extra_dir`` (or ``$STARGRAPH_SKILLS_DIR``), then ``./skills``,
    then ``~/.stargraph/skills``. Missing roots are skipped silently;
    files sort by path within each root for determinism.
    """
    roots: list[Path] = []
    env_dir = str(extra_dir) if extra_dir is not None else os.environ.get("STARGRAPH_SKILLS_DIR")
    if env_dir:
        roots.append(Path(env_dir))
    roots.append(Path.cwd() / "skills")
    roots.append(Path.home() / ".stargraph" / "skills")

    out: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        out.extend(sorted(root.glob("*/SKILL.md")))
    return out


def seed_markdown_skills(
    registry: ToolRegistry,
    *,
    extra_dir: str | Path | None = None,
    strict: bool = False,
) -> list[CompiledSkill]:
    """Discover, compile, and register every markdown skill on ``registry``.

    Two skills resolving to the same ``namespace/name`` fail loudly
    (:class:`PluginLoadError`) -- the same duplicate-id rule the plugin
    loader enforces. A file that fails to compile is skipped with a
    warning unless ``strict`` (one broken drop-in must not take down
    ``stargraph serve``); the CLI compile command uses strict mode.
    """
    from stargraph.logging import get_logger

    logger = get_logger("stargraph.skills.markdown")
    seen: dict[str, Path] = {}
    compiled: list[CompiledSkill] = []
    for md_path in discover_skill_files(extra_dir=extra_dir):
        try:
            item = compile_skill_md(md_path)
        except ValidationError:
            if strict:
                raise
            logger.warning("skill_md_skipped_invalid", path=str(md_path))
            continue
        skill_id = f"{item.spec.namespace}/{item.spec.name}"
        prior = seen.get(skill_id)
        if prior is not None:
            raise PluginLoadError(
                f"markdown skill {skill_id!r} at {md_path} conflicts with {prior}",
                skill=skill_id,
            )
        seen[skill_id] = md_path
        registry.register_skill(item.spec)
        compiled.append(item)
    return compiled
