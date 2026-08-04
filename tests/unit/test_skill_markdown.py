# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the SKILL.md loader (``stargraph.skills.markdown``).

Locks the P2 contract: the minimum valid skill is the **Claude Code
format** (name + description frontmatter + markdown body) and stock
Claude Code skills compile unmodified -- the three vendored fixtures
under ``tests/fixtures/claude-skills/`` are byte-for-byte copies of
real Claude Code marketplace skills and must keep compiling.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stargraph.errors import PluginLoadError, ValidationError
from stargraph.registry.tools import ToolRegistry
from stargraph.skills.base import Skill
from stargraph.skills.markdown import (
    compile_skill_md,
    discover_skill_files,
    seed_markdown_skills,
)
from stargraph.skills.react import ReactSkill

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "claude-skills"

MINIMAL = """---
name: hello-skill
description: Say hello.
---
Greet the user warmly.
"""


def _write_skill(root: Path, dirname: str, text: str) -> Path:
    skill_dir = root / dirname
    skill_dir.mkdir(parents=True)
    md = skill_dir / "SKILL.md"
    md.write_text(text, encoding="utf-8")
    return md


@pytest.fixture(autouse=True)
def _isolate_discovery_roots(  # pyright: ignore[reportUnusedFunction]
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep discovery off the developer's real ./skills and ~/.stargraph."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "isolated-home"))
    monkeypatch.delenv("STARGRAPH_SKILLS_DIR", raising=False)


# ---------------------------------------------------------------- compile


def test_minimal_claude_format_compiles_with_defaults(tmp_path: Path) -> None:
    md = _write_skill(tmp_path, "hello-skill", MINIMAL)
    compiled = compile_skill_md(md)
    spec = compiled.spec
    assert spec.name == "hello-skill"
    assert spec.description == "Say hello."
    assert spec.namespace == "local"
    assert spec.version == "0.1.0"
    assert spec.kind == "agent"
    assert spec.system_prompt == "Greet the user warmly."
    assert spec.tools == []
    assert spec.subgraph is None
    # No subgraph + agent kind -> default ReAct loop.
    assert isinstance(compiled.skill, ReactSkill)
    assert compiled.warnings == ()


def test_claude_foreign_keys_are_silently_accepted(tmp_path: Path) -> None:
    md = _write_skill(
        tmp_path,
        "s",
        "---\nname: s\ndescription: d\nargument-hint: '[--fix]'\n"
        "allowed-tools: Bash(npx *)\nmodel: sonnet\nuser-invocable: true\n"
        "disable-model-invocation: false\n---\nbody\n",
    )
    compiled = compile_skill_md(md)
    assert compiled.warnings == ()


def test_unknown_frontmatter_key_warns_not_fails(tmp_path: Path) -> None:
    md = _write_skill(tmp_path, "s", "---\nname: s\ndescription: d\nfrobnicate: 1\n---\nbody\n")
    compiled = compile_skill_md(md)
    assert compiled.warnings == ("ignored unknown frontmatter key 'frobnicate'",)


def test_horizontal_rule_in_body_survives(tmp_path: Path) -> None:
    md = _write_skill(tmp_path, "s", "---\nname: s\ndescription: d\n---\nabove\n\n---\n\nbelow\n")
    compiled = compile_skill_md(md)
    assert compiled.spec.system_prompt == "above\n\n---\n\nbelow"


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("name: s\ndescription: d\n", "missing '---' frontmatter fence"),
        ("---\nname: s\ndescription: d\nbody", "unterminated frontmatter"),
        ("---\n- just\n- a-list\n---\nbody\n", "must be a YAML mapping"),
        ("---\ndescription: d\n---\nbody\n", "missing the required 'name'"),
        ("---\nname: s\n---\nbody\n", "missing the required 'description'"),
        ("---\nname: s\ndescription: d\nkind: robot\n---\nbody\n", "kind must be"),
        ("---\nname: s\ndescription: d\ntools: 7\n---\nbody\n", "tools must be a list"),
    ],
)
def test_malformed_skill_md_raises_validation_error(
    tmp_path: Path, text: str, fragment: str
) -> None:
    md = _write_skill(tmp_path, "s", text)
    with pytest.raises(ValidationError) as excinfo:
        compile_skill_md(md)
    assert fragment in excinfo.value.context["actual"]
    assert excinfo.value.context["path"] == str(md)


def test_state_schema_field_map_compiles(tmp_path: Path) -> None:
    md = _write_skill(
        tmp_path,
        "s",
        "---\nname: s\ndescription: d\nkind: workflow\n"
        "state_schema:\n  query: str\n  attempts: {type: int, default: 0}\n"
        "---\nbody\n",
    )
    compiled = compile_skill_md(md)
    model = compiled.skill.state_schema
    assert model(query="q").model_dump() == {"query": "q", "attempts": 0}
    assert model.model_fields["query"].annotation is str
    assert compiled.skill.declared_output_keys == frozenset({"query", "attempts"})


def test_state_schema_unknown_type_fails(tmp_path: Path) -> None:
    md = _write_skill(
        tmp_path,
        "s",
        "---\nname: s\ndescription: d\nstate_schema:\n  q: banana\n---\nbody\n",
    )
    with pytest.raises(ValidationError) as excinfo:
        compile_skill_md(md)
    assert "unknown type 'banana'" in excinfo.value.context["actual"]


def test_subgraph_must_exist_and_resolves_relative(tmp_path: Path) -> None:
    text = "---\nname: s\ndescription: d\nkind: workflow\nsubgraph: graph.yaml\n---\nbody\n"
    md = _write_skill(tmp_path, "s", text)
    with pytest.raises(ValidationError) as excinfo:
        compile_skill_md(md)
    assert "does not exist" in excinfo.value.context["actual"]
    (md.parent / "graph.yaml").write_text("ir_version: '1.1'\n", encoding="utf-8")
    compiled = compile_skill_md(md)
    assert compiled.spec.subgraph == str((md.parent / "graph.yaml").resolve())
    # Explicit subgraph -> plain Skill, not the default ReAct loop.
    assert isinstance(compiled.skill, Skill)
    assert not isinstance(compiled.skill, ReactSkill)


def test_stargraph_superset_keys(tmp_path: Path) -> None:
    md = _write_skill(
        tmp_path,
        "s",
        "---\nname: s\ndescription: d\nversion: 2.0.0\nnamespace: acme\n"
        "tools: [std.web_search@1, std.fetch_page@1]\nrequires: ['tools:std:net']\n"
        "examples:\n  - inputs: {query: hi}\n    expected_output: {answer: hello}\n"
        "---\nbody\n",
    )
    compiled = compile_skill_md(md)
    assert compiled.spec.version == "2.0.0"
    assert compiled.spec.namespace == "acme"
    assert compiled.spec.tools == ["std.web_search@1", "std.fetch_page@1"]
    assert compiled.skill.requires == ["tools:std:net"]
    assert compiled.spec.examples == [
        {"inputs": {"query": "hi"}, "expected_output": {"answer": "hello"}}
    ]
    assert compiled.skill.examples[0].inputs == {"query": "hi"}


# ------------------------------------------------- vendored Claude fixtures


@pytest.mark.parametrize("dirname", ["init-project", "caveman-stats", "ruflo-doctor"])
def test_vendored_claude_code_skill_compiles_unmodified(dirname: str) -> None:
    """User-required verification: real Claude Code skills load UNMODIFIED."""
    compiled = compile_skill_md(FIXTURES / dirname / "SKILL.md")
    spec = compiled.spec
    assert spec.name == dirname
    assert spec.description.strip()
    assert spec.system_prompt
    assert spec.kind == "agent"
    # Claude-only keys (argument-hint, allowed-tools, ...) must not warn.
    assert compiled.warnings == ()
    assert isinstance(compiled.skill, ReactSkill)


# ------------------------------------------------------------- discovery


def test_discovery_order_and_sorting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_root = tmp_path / "env-root"
    cwd = tmp_path / "cwd"
    home = tmp_path / "home"
    _write_skill(env_root, "zeta", MINIMAL)
    _write_skill(env_root, "alpha", MINIMAL)
    _write_skill(cwd / "skills", "beta", MINIMAL)
    _write_skill(home / ".stargraph" / "skills", "gamma", MINIMAL)
    (cwd / "skills" / "not-a-skill").mkdir()  # no SKILL.md -> ignored

    monkeypatch.setenv("STARGRAPH_SKILLS_DIR", str(env_root))
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("HOME", str(home))

    found = discover_skill_files()
    names = [p.parent.name for p in found]
    # env root first (sorted within), then ./skills, then ~/.stargraph/skills.
    assert names == ["alpha", "zeta", "beta", "gamma"]


def test_discovery_extra_dir_overrides_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_root = tmp_path / "env-root"
    extra = tmp_path / "extra"
    _write_skill(env_root, "from-env", MINIMAL)
    _write_skill(extra, "from-extra", MINIMAL)
    monkeypatch.setenv("STARGRAPH_SKILLS_DIR", str(env_root))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "nohome"))

    names = [p.parent.name for p in discover_skill_files(extra_dir=extra)]
    assert names == ["from-extra"]


# --------------------------------------------------------------- seeding


def test_seed_registers_skills_on_tool_registry(tmp_path: Path) -> None:
    _write_skill(tmp_path, "one", MINIMAL.replace("hello-skill", "one"))
    _write_skill(tmp_path, "two", MINIMAL.replace("hello-skill", "two"))
    registry = ToolRegistry()
    compiled = seed_markdown_skills(registry, extra_dir=tmp_path)
    assert [c.spec.name for c in compiled] == ["one", "two"]
    assert sorted(s.name for s in registry.list_skills()) == ["one", "two"]


def test_seed_duplicate_id_fails_loudly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root_a = tmp_path / "a"
    _write_skill(root_a, "dup", MINIMAL.replace("hello-skill", "dup"))
    # Same namespace/name resolved from a second discovery root (./skills).
    _write_skill(tmp_path / "skills", "dup", MINIMAL.replace("hello-skill", "dup"))
    monkeypatch.setenv("STARGRAPH_SKILLS_DIR", str(root_a))
    with pytest.raises(PluginLoadError, match="local/dup"):
        seed_markdown_skills(ToolRegistry())


def test_seed_skips_invalid_unless_strict(tmp_path: Path) -> None:
    _write_skill(tmp_path, "good", MINIMAL)
    _write_skill(tmp_path, "broken", "---\ndescription: no name\n---\nbody\n")
    registry = ToolRegistry()
    compiled = seed_markdown_skills(registry, extra_dir=tmp_path)
    assert [c.spec.name for c in compiled] == ["hello-skill"]

    with pytest.raises(ValidationError):
        seed_markdown_skills(ToolRegistry(), extra_dir=tmp_path, strict=True)
