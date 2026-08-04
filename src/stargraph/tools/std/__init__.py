# SPDX-License-Identifier: Apache-2.0
"""``stargraph.tools.std`` -- the batteries-included standard tool pack.

Twelve general-purpose tools under the ``std`` namespace, seeded into every
:class:`~stargraph.registry.tools.ToolRegistry` by
:func:`stargraph.tools.builtin.seed_builtin_tools` (registry ids
``std.<name>@1``):

========================  ==============  ==========================================
tool                      side effects    notes
========================  ==============  ==========================================
``std.calculator``        none            AST-safe arithmetic, no ``eval``
``std.file_read``         read            root-jailed (``STARGRAPH_TOOLS_FS_ROOT``)
``std.file_write``        write           root-jailed
``std.file_list``         read            root-jailed
``std.sql_query``         read            sqlite (stdlib) / duckdb (``[tools]``)
``std.http_request``      external        arbitrary-method HTTP via httpx
``std.fetch_page``        read            main-content extraction (``[tools]``)
``std.web_search``        read            DuckDuckGo via ``ddgs`` (``[tools]``)
``std.wikipedia``         read            Wikipedia REST search
``std.arxiv``             read            arXiv Atom API search
``std.python_exec``       external        capability ``tools:std:exec``
``std.shell``             external        capability ``tools:std:shell``
========================  ==============  ==========================================

Design rules:

* Hard deps only at import time -- optional heavy deps (``ddgs``,
  ``readability-lxml``, ``duckdb`` -- the ``stargraph[tools]`` extra) are
  imported lazily inside the tool body and fail with a pip-install hint,
  so every tool always *registers* and unavailability is a loud runtime
  error, not a silent absence.
* Honest ``side_effects`` per the house convention (network read ->
  ``read``, arbitrary mutation -> ``write``/``external``) so replay
  policies (FR-21) default correctly.
* Code/shell execution is capability-gated (``tools:std:exec`` /
  ``tools:std:shell``) -- default-deny blocks both unless the run grants
  the claim. Filesystem tools are jailed under ``STARGRAPH_TOOLS_FS_ROOT``
  (default: the process working directory) instead.
"""

from __future__ import annotations

from stargraph.tools.std.arxiv import arxiv_search
from stargraph.tools.std.calculator import calculator
from stargraph.tools.std.fetch_page import fetch_page
from stargraph.tools.std.fs import file_list, file_read, file_write
from stargraph.tools.std.http_request import http_request
from stargraph.tools.std.python_exec import python_exec
from stargraph.tools.std.shell import shell
from stargraph.tools.std.sql_query import sql_query
from stargraph.tools.std.web_search import web_search
from stargraph.tools.std.wikipedia import wikipedia

__all__ = [
    "arxiv_search",
    "calculator",
    "fetch_page",
    "file_list",
    "file_read",
    "file_write",
    "http_request",
    "python_exec",
    "shell",
    "sql_query",
    "web_search",
    "wikipedia",
]
