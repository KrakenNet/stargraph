# SPDX-License-Identifier: Apache-2.0
"""``stargraph.tools.servicenow`` -- ServiceNow tools (read + write).

Nautilus's adapter surface is intentionally read-only (see
:mod:`nautilus.adapters.servicenow`; ``execute()`` issues HTTP GET and
no other verbs). Production pilots uncovered two gaps:

* Phase 4 :class:`CreateChangeRequestNode` needs to *create* a CR, not
  just fetch one. Write-side mutation lives here until Nautilus ships a
  gated write surface (``design-docs/05-ecosystem-roadmap.md`` --
  "Weeks 22.5: Write surface").
* CMDB correlation needs an agent-callable read surface (substring +
  relationship walk) so the CorrelateAgent can route through the
  Nautilus broker / rule pack. The three CMDB read tools below are the
  foundation for that flow.

Tools registered here:

* :func:`create_change_request` -- POST ``/api/now/table/change_request``
  (capability ``tools:servicenow:write``).
* :func:`cmdb_query_software` -- GET ``cmdb_ci`` filtered to
  ``cmdb_ci_spkg`` rows by ``nameLIKE``/``vendorLIKE``
  (capability ``tools:servicenow:read``).
* :func:`cmdb_traverse_runs_on` -- GET ``cmdb_rel_ci`` filtered to the
  Runs-on relationship type for a parent Software CI
  (capability ``tools:servicenow:read``).
* :func:`cmdb_resolve_hosts` -- GET ``cmdb_ci`` batched by ``sys_idIN``
  to resolve a list of CI sys_ids to their ``name`` fields
  (capability ``tools:servicenow:read``).

Safety boundaries enforced on every write tool:

1. **Dry-run by default.** Write callables check
   ``STARGRAPH_SERVICENOW_LIVE`` env. Unless set to ``1``/``true``/``yes``/
   ``on``, the tool returns a synthetic envelope and skips the network
   call entirely.
2. **Idempotency.** Every write requires a caller-supplied
   ``correlation_id``; the adapter uses it as the ServiceNow
   ``correlation_id`` field so retries from the same caller dedupe.
3. **No persistent client.** Each call opens / closes its own
   :class:`httpx.AsyncClient`; no shared state, no leaked auth.
4. **Capability gate.** Write tools require
   ``tools:servicenow:write``; read tools require
   ``tools:servicenow:read``. Registry filter denies any graph that
   lacks the capability even when the tool is loaded.
"""

from __future__ import annotations

from stargraph.tools.servicenow.cmdb_query_software import cmdb_query_software
from stargraph.tools.servicenow.cmdb_resolve_hosts import cmdb_resolve_hosts
from stargraph.tools.servicenow.cmdb_traverse_runs_on import cmdb_traverse_runs_on
from stargraph.tools.servicenow.create_change_request import create_change_request
from stargraph.tools.servicenow.patch_cr_state import patch_cr_state
from stargraph.tools.servicenow.patch_work_notes import patch_work_notes
from stargraph.tools.servicenow.poll_approval import poll_approval
from stargraph.tools.servicenow.table_crud import table_create, table_query
from stargraph.tools.servicenow.upload_attachment import upload_attachment

__all__ = [
    "cmdb_query_software",
    "cmdb_resolve_hosts",
    "cmdb_traverse_runs_on",
    "create_change_request",
    "patch_cr_state",
    "patch_work_notes",
    "poll_approval",
    "table_create",
    "table_query",
    "upload_attachment",
]
