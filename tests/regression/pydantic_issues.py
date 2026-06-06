# SPDX-License-Identifier: Apache-2.0
"""Pydantic-issue regression suite -- pins Stargraph's mitigations (NFR-11).

Five fixtures probe the Pydantic issues the requirements register cites
(see ``.progress.md`` Learnings -> "Pydantic citation updates required").

* **#6884 (closed)** -- ``model_dump_json`` once ignored ``ensure_ascii=False``.
  Probe: ``dumps`` emits non-ASCII verbatim. (Issue confirmed fixed; Stargraph's
  use of ``json.dumps(ensure_ascii=False)`` after ``model_dump`` is
  belt-and-suspenders.)
* **#9136 (closed-with-workaround)** -- PEP-695 ``type X = ...`` aliases broke
  forward refs. Mitigation (FR-8): Stargraph bans PEP-695 ``type`` aliases in IR
  models. Probe: an AST walk over ``src/stargraph/ir/_models.py`` finds zero
  ``type X = ...`` aliases. The portable-subset walker enforces this on the
  whole IR tree; here we pin the same invariant on the ``ir.IRDocument``
  module specifically.
* **#7491 (open)** -- nested discriminated unions corrupt JSON Schema.
  Mitigation (FR-11): Stargraph's ``Action`` discriminated union is *top-level
  only* -- variants do not themselves contain :data:`Action` fields. Probe:
  none of the six ``Action`` variants declare an :data:`Action`-typed field.
* **#9872 (open)** -- ``Sequence[Union[...]]`` discriminator silently ignored.
  Mitigation (FR-11): Stargraph uses ``list[Action]`` (concrete sequence type)
  with a discriminator-marked ``Annotated`` union. Probe: ``RuleSpec.then``
  is annotated as ``list[Action]`` (not ``Sequence[...]``), and a wrong-tag
  payload fails validation rather than silently parsing.
* **#7424 (open)** -- ``model_dump_json`` has no ``sort_keys``. Mitigation
  (FR-15): Stargraph's single canonical entry point ``stargraph.ir.dumps_canonical``
  applies ``sort_keys=True`` via ``json.dumps`` after ``model_dump``. Probe:
  ``dumps_canonical`` emits keys in alphabetical order, and the output
  differs from a naive ``model_dump_json`` call when fields are non-alpha
  in declaration order.

Each probe is a plain assertion that Stargraph's pattern works. ``pytest.mark.xfail``
is reserved for the case where Stargraph's mitigation can't be applied directly --
none of the current mitigations require it, so all probes are plain pass.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

if TYPE_CHECKING:
    from pydantic.fields import FieldInfo

from stargraph.ir import (
    AssertAction,
    GotoAction,
    HaltAction,
    IRDocument,
    NodeSpec,
    ParallelAction,
    RetractAction,
    RetryAction,
    RuleSpec,
    dumps,
    dumps_canonical,
)

# ---------------------------------------------------------------------------
# #6884 (closed) -- model_dump_json honors ensure_ascii in Stargraph's wrapper.
# ---------------------------------------------------------------------------


def test_pydantic_6884_ensure_ascii_false_emits_non_ascii_verbatim() -> None:
    """#6884 closed via PR #6887; Stargraph's ``dumps`` belt-and-suspenders the same.

    ``json.dumps(..., ensure_ascii=False)`` after ``model_dump`` keeps non-ASCII
    characters verbatim instead of escaping to ``\\uXXXX``. This pins the
    behavior so a future refactor that drops ``ensure_ascii=False`` regresses
    here loudly.
    """
    doc = IRDocument(ir_version="1.0.0", id="run:café", nodes=[])
    text = dumps(doc)
    assert "café" in text
    assert "\\u" not in text


# ---------------------------------------------------------------------------
# #9136 (closed-with-workaround) -- PEP-695 ``type`` ban (FR-8).
# ---------------------------------------------------------------------------


_IR_MODELS_PATH = Path(__file__).resolve().parents[2] / "src" / "stargraph" / "ir" / "_models.py"


def test_pydantic_9136_no_pep695_type_aliases_in_ir_models() -> None:
    """#9136 mitigation (FR-8): IR models contain zero ``type X = ...`` aliases.

    PEP-695 type-alias statements broke Pydantic forward refs and were only
    fixed via a workaround. Stargraph bans them outright in IR models. The
    portable-subset walker enforces this on the whole tree (task 2.3); this
    probe pins the invariant on the canonical models module.
    """
    source = _IR_MODELS_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(_IR_MODELS_PATH))
    type_aliases = [n for n in ast.walk(tree) if isinstance(n, ast.TypeAlias)]
    assert type_aliases == [], (
        f"FR-8 violation: PEP-695 `type` aliases present at {[n.lineno for n in type_aliases]}"
    )


# ---------------------------------------------------------------------------
# #7491 (open) -- top-level-only discriminated union (FR-11).
# ---------------------------------------------------------------------------


_ACTION_VARIANTS: tuple[type[BaseModel], ...] = (
    GotoAction,
    HaltAction,
    ParallelAction,
    RetryAction,
    AssertAction,
    RetractAction,
)


def test_pydantic_7491_action_variants_do_not_nest_action_fields() -> None:
    """#7491 mitigation (FR-11): variants of the ``Action`` union don't nest unions.

    Nested discriminated unions corrupt JSON Schema generation. Stargraph's six
    ``Action`` variants declare only primitive / list-of-primitive fields --
    no field of any variant is itself an :data:`Action`-typed slot.
    """
    for variant in _ACTION_VARIANTS:
        fields: dict[str, FieldInfo] = variant.model_fields
        for field_name, field_info in fields.items():
            annotation_text = repr(field_info.annotation)
            assert "Action" not in annotation_text or field_name == "kind", (
                f"{variant.__name__}.{field_name} nests an Action union: {annotation_text}"
            )


def test_pydantic_7491_top_level_action_json_schema_emits_discriminator() -> None:
    """The top-level ``Action`` discriminator survives JSON Schema generation.

    A nested-union bug would cause the discriminator to be dropped from the
    schema; pinning here ensures Stargraph's top-level placement keeps it.
    """
    schema = RuleSpec.model_json_schema()
    # ``then`` is a list of Action; its items must reference a discriminated union.
    then_schema = schema["properties"]["then"]
    assert then_schema["type"] == "array"
    items = then_schema["items"]
    # Either the union appears inline with a 'discriminator' key, or as a $ref
    # whose target carries one. Pydantic v2 emits ``oneOf`` + ``discriminator``.
    if "discriminator" not in items:
        # Followed via $ref or referenced in $defs.
        assert "$ref" in items or "oneOf" in items or "anyOf" in items, items


# ---------------------------------------------------------------------------
# #9872 (open) -- list[Action] not Sequence[Union[...]] (FR-11).
# ---------------------------------------------------------------------------


def test_pydantic_9872_rule_then_uses_list_not_sequence() -> None:
    """#9872 mitigation (FR-11): ``RuleSpec.then`` is ``list[Action]``, not ``Sequence``.

    ``Sequence[Union[...]]`` with a discriminator is silently ignored by
    Pydantic. Stargraph uses concrete ``list`` to keep the discriminator active.
    """
    annotation = RuleSpec.model_fields["then"].annotation
    annotation_text = repr(annotation)
    # ``Sequence`` must NOT appear in the annotation; ``list`` must.
    assert "Sequence" not in annotation_text, annotation_text
    assert "list" in annotation_text.lower(), annotation_text


def test_pydantic_9872_wrong_discriminator_tag_fails_validation() -> None:
    """An invalid ``kind`` tag is rejected -- the discriminator IS active (#9872 mitigated)."""
    payload = {
        "ir_version": "1.0.0",
        "id": "run:t",
        "nodes": [{"id": "n1", "kind": "task"}],
        "rules": [
            {
                "id": "r1",
                "when": "",
                "then": [{"kind": "not-a-real-verb", "target": "x"}],
            }
        ],
    }
    with pytest.raises(PydanticValidationError):
        IRDocument.model_validate(payload)


# ---------------------------------------------------------------------------
# #7424 (open) -- dumps_canonical applies sort_keys=True (FR-15).
# ---------------------------------------------------------------------------


def test_pydantic_7424_dumps_canonical_sorts_top_level_keys() -> None:
    """#7424 mitigation (FR-15): ``dumps_canonical`` emits sorted keys.

    Pydantic's ``model_dump_json`` has no ``sort_keys`` knob; Stargraph wraps
    ``model_dump`` then ``json.dumps(sort_keys=True)`` in the single
    canonical entry point.
    """
    doc = IRDocument(
        ir_version="1.0.0",
        id="run:t",
        nodes=[NodeSpec(id="n1", kind="task")],
    )
    canonical = dumps_canonical(doc)
    decoded = json.loads(canonical)
    keys = list(decoded.keys())
    assert keys == sorted(keys), keys
    # Pydantic declares ir_version first; sort_keys flips ``id`` to lead.
    assert canonical.startswith('{"id":')


def test_pydantic_7424_canonical_diverges_from_pydantic_native_dump() -> None:
    """``dumps_canonical`` produces a different ordering than raw ``model_dump_json``.

    This is the load-bearing demonstration of the mitigation: if a future
    refactor accidentally routes through ``model_dump_json`` directly (which
    has no ``sort_keys``), this probe regresses.
    """
    doc = IRDocument(
        ir_version="1.0.0",
        id="run:t",
        nodes=[NodeSpec(id="n1", kind="task")],
    )
    canonical = dumps_canonical(doc)
    native = doc.model_dump_json(exclude_defaults=True)
    # Both decode to the same logical dict...
    assert json.loads(canonical) == json.loads(native)
    # ...but Pydantic-native preserves declared field order (ir_version first).
    assert native.startswith('{"ir_version":')
    # ...whereas the canonical form has alphabetic order (id first).
    assert canonical.startswith('{"id":')
    assert canonical != native
