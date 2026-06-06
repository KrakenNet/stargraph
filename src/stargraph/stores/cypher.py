# SPDX-License-Identifier: Apache-2.0
"""Cypher portable-subset linter (FR-12, design §3.2).

AST-based implementation backed by ``graphglot``'s ``neo4j`` dialect.
The dialect already rejects most non-portable surface (LOAD CSV,
shortestPath, SHOW commands, map projections) at parse time; this
module wraps those parse errors as :class:`UnportableCypherError` and
walks the AST to catch the remaining cases that parse but are still
out of subset:

* Banned procedure namespaces (``apoc.*``, ``gds.*``, ``db.*``).
* Variable-length unbounded paths (``-[*]-``).
* ``YIELD *`` (empty yield-item list).

Whitespace / case-folding bypasses that fooled the prior regex linter
cannot survive AST parsing — the procedure-call name is normalized to
its identifier form by graphglot regardless of how it was tokenized.

The linter also exposes :meth:`Linter.requires_write`, an AST-level
mutation check used by FR-20 capability gating to decide whether a
query mutates graph state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import graphglot.ast as gg_ast  # pyright: ignore[reportMissingTypeStubs]
import graphglot.ast.cypher as gg_cypher  # pyright: ignore[reportMissingTypeStubs]
from graphglot.dialect import Dialect  # pyright: ignore[reportMissingTypeStubs]

from stargraph.errors import UnportableCypherError

if TYPE_CHECKING:
    from collections.abc import Iterable


__all__ = ["Linter"]


# Procedure-name prefixes the portable subset rejects. Matched
# case-insensitively against the dotted form of ``NamedProcedureCall``
# (e.g. ``apoc.path.expand`` → prefix-match against ``apoc``).
_BANNED_PROC_PREFIXES: tuple[str, ...] = (
    "apoc",
    "gds",
    "db",
)


# Cached dialect handle. graphglot's :meth:`Dialect.get_or_raise` is
# cheap but module-level caching keeps the hot path branch-free.
_NEO4J: Dialect = Dialect.get_or_raise("neo4j")


def _proc_full_name(call: gg_ast.NamedProcedureCall) -> str:
    """Reconstruct the dotted procedure name from a ``NamedProcedureCall``.

    Walks the ``catalog_object_parent_reference`` chain (if present) and
    appends the leaf ``procedure_name`` identifier, joined by dots. The
    result is the canonical form (e.g. ``apoc.path.expand``) regardless
    of how the source query tokenized the call.
    """
    pr = call.procedure_reference
    parts: list[str] = []
    parent_ref = getattr(pr, "catalog_object_parent_reference", None)
    if parent_ref is not None:
        parent_chain = getattr(parent_ref, "catalog_object_parent_reference", [])
        parts.extend(ident.name for ident in parent_chain)
    parts.append(pr.procedure_name.name)  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType, reportAttributeAccessIssue]
    return ".".join(parts)


def _iter_parsed_programs(cypher: str) -> Iterable[gg_ast.GqlProgram]:
    """Parse ``cypher`` via graphglot's neo4j dialect; raise ``UnportableCypherError``
    on parse failure.

    graphglot raises ``ParseError`` for surface that is outside its
    Neo4j-2025+ accept set (LOAD CSV, shortestPath, SHOW INDEXES,
    map projection, etc.) — exactly the intent of the portable-subset
    ban list. The translation here keeps the public exception contract
    stable: every linter rejection raises :class:`UnportableCypherError`.
    """
    try:
        programs = _NEO4J.parse(cypher)
    except Exception as exc:  # graphglot.dialect raises ParseError
        message = str(exc)
        raise UnportableCypherError(
            f"Cypher rejected at parse: {message}",
            cypher=cypher,
            violation="parse-error",
            rule="parse-error",
            match=message,
        ) from exc
    return programs  # pyright: ignore[reportReturnType]


class Linter:
    """Cypher portable-subset linter (FR-12).

    Stateless — instances exist only so callers can pass a linter as a
    dependency. The neo4j dialect handle is module-cached.
    """

    def check(self, cypher: str) -> None:
        """Reject queries that fall outside the portable subset.

        Order:

        1. Parse via graphglot's neo4j dialect; parse errors translate
           to :class:`UnportableCypherError` with rule ``parse-error``.
        2. Walk for banned procedure namespaces (``apoc``/``gds``/``db``).
        3. Walk for variable-length unbounded paths.
        4. Walk for ``YIELD *``.
        5. Walk for path comprehension (``CypherPatternComprehension``).
        6. Walk for ``CALL { ... }`` subqueries.
        """
        programs = _iter_parsed_programs(cypher)
        for program in programs:
            self._check_banned_procs(cypher, program)
            self._check_unbounded_varlen(cypher, program)
            self._check_yield_star(cypher, program)
            self._check_path_comprehension(cypher, program)
            self._check_call_subquery(cypher, program)

    def requires_write(self, cypher: str) -> bool:
        """Return True if ``cypher`` contains a graph-state mutation (FR-20).

        Walks the AST for ``CreateClause``, ``MergeClause``,
        ``SetStatement``, ``DeleteStatement``, and ``RemoveStatement``.
        Unlike the prior regex implementation this doesn't false-positive
        on identifiers / string-literals containing the keyword.

        Raises :class:`UnportableCypherError` on parse failure — the
        capability gate must not silently allow malformed queries through.
        """
        programs = _iter_parsed_programs(cypher)
        write_kinds = (
            gg_ast.CreateClause,
            gg_ast.MergeClause,
            gg_ast.SetStatement,
            gg_ast.DeleteStatement,
            gg_ast.RemoveStatement,
        )
        for program in programs:
            for kind in write_kinds:
                if next(iter(program.find_all(kind)), None) is not None:
                    return True
        return False

    # ------------------------------------------------------------------ #
    # Internal walkers                                                   #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _check_banned_procs(cypher: str, program: gg_ast.GqlProgram) -> None:
        # CALL <ns>.<proc>() form — dotted procedure call.
        for call in program.find_all(gg_ast.NamedProcedureCall):
            full_name = _proc_full_name(call)
            head = full_name.split(".", 1)[0].lower()
            if head in _BANNED_PROC_PREFIXES:
                raise UnportableCypherError(
                    f"Cypher rejected by linter rule 'banned-procedure': {full_name!r}",
                    cypher=cypher,
                    violation="banned-procedure",
                    rule="banned-procedure",
                    match=full_name,
                )
        # `<ns>.<proc>(args)` used as a function inside an expression
        # (e.g. ``WITH apoc.coll.sum(xs) AS x``). graphglot parks
        # unrecognized dotted function calls on an ``Anonymous`` node
        # whose ``name`` is the full dotted form. Same prefix ban.
        for anon in program.find_all(gg_ast.Anonymous):
            anon_name = getattr(anon, "name", "") or ""
            if not anon_name:
                continue
            head = anon_name.split(".", 1)[0].lower()
            if head in _BANNED_PROC_PREFIXES:
                raise UnportableCypherError(
                    f"Cypher rejected by linter rule 'banned-procedure': {anon_name!r}",
                    cypher=cypher,
                    violation="banned-procedure",
                    rule="banned-procedure",
                    match=anon_name,
                )

    @staticmethod
    def _check_unbounded_varlen(cypher: str, program: gg_ast.GqlProgram) -> None:
        for quant in program.find_all(gg_ast.GeneralQuantifier):
            # ``upper_bound is None`` = unbounded (e.g. ``-[*]-`` or
            # ``-[*1..]-``). Lower bound alone isn't enough — we need a
            # ceiling for query cost predictability.
            if quant.upper_bound is None:
                raise UnportableCypherError(
                    "Cypher rejected by linter rule 'varlen-unbounded': "
                    "variable-length path missing upper bound",
                    cypher=cypher,
                    violation="varlen-unbounded",
                    rule="varlen-unbounded",
                    match="unbounded variable-length quantifier",
                )

    @staticmethod
    def _check_yield_star(cypher: str, program: gg_ast.GqlProgram) -> None:
        # ``YIELD *`` produces a ``YieldClause`` with an empty
        # ``list_yield_item``. ``YIELD <name>`` produces a populated list.
        # No ``YIELD`` clause at all yields no ``YieldClause`` node.
        for yc in program.find_all(gg_ast.YieldClause):
            if not yc.yield_item_list.list_yield_item:
                raise UnportableCypherError(
                    "Cypher rejected by linter rule 'yield-star': "
                    "explicit YIELD * is not in the portable subset",
                    cypher=cypher,
                    violation="yield-star",
                    rule="yield-star",
                    match="YIELD *",
                )

    @staticmethod
    def _check_path_comprehension(cypher: str, program: gg_ast.GqlProgram) -> None:
        # ``[(n)-[:R]->(m) | m.id]`` form. Distinct from a regular list
        # comprehension over an iterable; this one walks a graph pattern
        # inline. RyuGraph does not implement it.
        if next(iter(program.find_all(gg_cypher.CypherPatternComprehension)), None) is not None:
            raise UnportableCypherError(
                "Cypher rejected by linter rule 'path-comprehension': "
                "graph-pattern list comprehension is not in the portable subset",
                cypher=cypher,
                violation="path-comprehension",
                rule="path-comprehension",
                match="path comprehension",
            )

    @staticmethod
    def _check_call_subquery(cypher: str, program: gg_ast.GqlProgram) -> None:
        # ``CALL { ... }`` subqueries are NOT in the portable subset
        # (RyuGraph does not implement subqueries). Distinguish from
        # regular procedure calls (``CALL apoc.foo()``) by the absence of
        # a ``NamedProcedureCall`` inside the body — a subquery body
        # contains MATCH / RETURN / etc. directly rather than a procedure
        # invocation.
        for call_stmt in program.find_all(gg_ast.CallQueryStatement):
            inner_named = next(iter(call_stmt.find_all(gg_ast.NamedProcedureCall)), None)
            if inner_named is None:
                raise UnportableCypherError(
                    "Cypher rejected by linter rule 'call-subquery': "
                    "CALL { ... } subqueries are not in the portable subset",
                    cypher=cypher,
                    violation="call-subquery",
                    rule="call-subquery",
                    match="CALL { ... }",
                )
