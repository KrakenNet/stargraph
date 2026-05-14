# SPDX-License-Identifier: Apache-2.0
"""Demo-local Harbor tools for the cve_remediation pipeline.

These tools are *demo-specific*: they exist only because the
cve_remediation graph needs them. They live here -- not in
``src/harbor/tools/`` -- because the wider Harbor framework should not
ship CVE-shaped helpers; only general-purpose tools belong upstream
(e.g. ``harbor.tools.servicenow.create_change_request``).

The demo wires them onto the same ``@tool`` decorator the framework
publishes, so the registration path is identical to a third-party
plugin shipping its own tool entry-points. Importing this package as a
side effect registers the tools with the in-process tool registry.

Tools registered here:

* :func:`fetch_advisory` -- NVD JSON 2.0 lookup for a CVE id (Phase-1
  intake).
"""

from __future__ import annotations

from demos.cve_remediation.tools.fetch_advisory import fetch_advisory

__all__ = ["fetch_advisory"]
