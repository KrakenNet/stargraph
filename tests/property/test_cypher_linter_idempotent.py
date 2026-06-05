# SPDX-License-Identifier: Apache-2.0
"""Property tests for the Cypher portable-subset linter (FR-12, NFR-4).

The Linter has two halves: an *allow* side (anything not matching a banned
pattern passes) and a *ban* side (any banned token fails). Both halves are
exercised here with Hypothesis strategies so we cover a much wider corpus
than handwritten unit cases.

* The allow-list strategy generates strings from a small set of templates
  that mirror the documented portable subset (MATCH/RETURN, MERGE,
  WHERE+filter, ORDER BY/LIMIT). :meth:`Linter.check` must never raise.
* The ban-list strategy splices a banned token into otherwise-valid
  scaffolding. :meth:`Linter.check` must always raise
  :class:`UnportableCypherError`.

Hypothesis budget is small (``max_examples=50``) to keep the test suite
snappy; a Phase-5 fuzz task can dial this up if needed.
"""

from __future__ import annotations

import pytest
from graphglot.lexer import (  # pyright: ignore[reportMissingTypeStubs]
    Lexer as _GGLexer,
)
from hypothesis import given, settings
from hypothesis import strategies as st

from harbor.errors import UnportableCypherError
from harbor.stores.cypher import Linter

# ---------------------------------------------------------------------------
# Allow-list strategy: portable Cypher templates.
# ---------------------------------------------------------------------------

# Identifier shape that avoids the banned `:$(` dynamic-label pattern and
# the `{.field` map-projection pattern. Filtered against graphglot's own
# keyword table (case-insensitive) so the strategy never emits a reserved
# word as a variable, label, or property name -- e.g. `MATCH (a:Of)` fails
# graphglot's parse with "Expected label primary or label negation".
_GG_KEYWORDS = frozenset(kw.lower() for kw in _GGLexer.KEYWORDS if kw.isalpha())
_IDENT = st.from_regex(r"\A[a-z][a-z0-9_]{0,7}\Z", fullmatch=True).filter(
    lambda s: s not in _GG_KEYWORDS
)
_LABEL = st.from_regex(r"\A[A-Z][A-Za-z0-9]{0,9}\Z", fullmatch=True).filter(
    lambda s: s.lower() not in _GG_KEYWORDS
)
_INT = st.integers(min_value=0, max_value=999)


@st.composite
def _match_return(draw: st.DrawFn) -> str:
    var = draw(_IDENT)
    label = draw(_LABEL)
    prop = draw(_IDENT)
    return f"MATCH ({var}:{label}) RETURN {var}.{prop}"


@st.composite
def _merge(draw: st.DrawFn) -> str:
    var = draw(_IDENT)
    label = draw(_LABEL)
    prop = draw(_IDENT)
    val = draw(_INT)
    return f"MERGE ({var}:{label} {{{prop}: {val}}}) RETURN {var}"


@st.composite
def _where_filter(draw: st.DrawFn) -> str:
    var = draw(_IDENT)
    label = draw(_LABEL)
    prop = draw(_IDENT)
    val = draw(_INT)
    return f"MATCH ({var}:{label}) WHERE {var}.{prop} > {val} RETURN {var}.{prop}"


@st.composite
def _order_limit(draw: st.DrawFn) -> str:
    var = draw(_IDENT)
    label = draw(_LABEL)
    prop = draw(_IDENT)
    limit = draw(st.integers(min_value=1, max_value=100))
    return f"MATCH ({var}:{label}) RETURN {var}.{prop} ORDER BY {var}.{prop} LIMIT {limit}"


@st.composite
def _bounded_varlen(draw: st.DrawFn) -> str:
    """Bounded variable-length path -- explicitly allowed by the linter."""
    a = draw(_IDENT)
    b = draw(_IDENT)
    rel = draw(_LABEL)
    lo = draw(st.integers(min_value=1, max_value=5))
    hi = draw(st.integers(min_value=6, max_value=10))
    return f"MATCH ({a})-[:{rel}*{lo}..{hi}]->({b}) RETURN {a}, {b}"


_ALLOWED = st.one_of(
    _match_return(),
    _merge(),
    _where_filter(),
    _order_limit(),
    _bounded_varlen(),
)


# ---------------------------------------------------------------------------
# Ban-list strategy: scaffolding + banned token.
# ---------------------------------------------------------------------------

# Tokens that the linter rejects on sight (each maps to a rule in
# ``harbor.stores.cypher._BAN_PATTERNS``).
_BANNED_TOKENS: tuple[str, ...] = (
    "apoc.coll.sum(xs)",
    "gds.graph.project('g', 'N', 'R')",
    "LOAD CSV FROM 'file:///x.csv' AS row",
    "LOAD FROM 'file:///x.parquet'",
    "SHOW FUNCTIONS",
    "SHOW INDEXES",
    "SHOW CONSTRAINTS",
    "YIELD *",
    "shortestPath((a)-[*]->(b))",
)


@st.composite
def _scaffolded_ban(draw: st.DrawFn) -> str:
    """Wrap a banned token in otherwise-valid Cypher scaffolding.

    The banned token may appear as a CALL clause, a WITH expression, or a
    standalone statement -- in every shape the linter must reject.
    """
    token = draw(st.sampled_from(_BANNED_TOKENS))
    var = draw(_IDENT)
    label = draw(_LABEL)
    shape = draw(st.sampled_from(("call", "with", "standalone")))
    if shape == "call":
        return f"MATCH ({var}:{label}) CALL {token} RETURN {var}"
    if shape == "with":
        return f"MATCH ({var}:{label}) WITH {token} AS x RETURN x"
    return token


# ---------------------------------------------------------------------------
# Properties.
# ---------------------------------------------------------------------------


@pytest.mark.knowledge
@pytest.mark.property
@settings(max_examples=50, deadline=None)
@given(query=_ALLOWED)
def test_allowed_corpus_never_raises(query: str) -> None:
    """Every string from the allow-list strategy passes :meth:`Linter.check`."""
    Linter().check(query)


@pytest.mark.knowledge
@pytest.mark.property
@settings(max_examples=50, deadline=None)
@given(query=_scaffolded_ban())
def test_ban_list_always_raises(query: str) -> None:
    """Every string containing a banned token raises :class:`UnportableCypherError`."""
    with pytest.raises(UnportableCypherError):
        Linter().check(query)
