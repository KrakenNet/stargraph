# SPDX-License-Identifier: Apache-2.0
"""Manifest + register_* hookimpls for the ``plugin_knowledge`` fixture.

Declares one vector ``StoreSpec`` and one agent ``SkillSpec`` so the
pluggy loader's collect-all hooks (``register_stores`` /
``register_skills``) can be smoke-tested against a realistic shape
(FR-19/22, NFR-9).
"""

from __future__ import annotations

from stargraph.ir import PluginManifest, SkillSpec, StoreSpec
from stargraph.plugin._markers import hookimpl

MANIFEST: PluginManifest = PluginManifest(
    name="plugin_knowledge",
    version="0.1.0",
    api_version="1",
    namespaces=["knowledge.demo"],
    provides=["store", "skill"],
    order=5000,
)


def make_manifest() -> PluginManifest:
    """Return a fresh :class:`PluginManifest` for the plugin_knowledge fixture.

    Manifest factory shape compatible with the stargraph plugin loader's
    ``stargraph_plugin`` entry-point contract; constructs a new instance per
    call so the zero-side-effect invariant is preserved.
    """
    return PluginManifest(
        name="plugin_knowledge",
        version="0.1.0",
        api_version="1",
        namespaces=["knowledge.demo"],
        provides=["store", "skill"],
        order=5000,
    )


@hookimpl
def register_stores() -> list[StoreSpec]:
    """Return the single vector store this fixture contributes."""
    return [
        StoreSpec(
            name="knowledge.demo.vectors",
            provider="lancedb",
            protocol="vector",
            config_schema={},
            capabilities=[],
        ),
    ]


@hookimpl
def register_skills() -> list[SkillSpec]:
    """Return the single agent skill this fixture contributes."""
    return [
        SkillSpec(
            name="rag",
            namespace="knowledge.demo",
            version="0.1.0",
            description="Demo RAG skill for the plugin_knowledge fixture.",
            kind="agent",
        ),
    ]
