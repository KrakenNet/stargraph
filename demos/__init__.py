# SPDX-License-Identifier: Apache-2.0
"""Stargraph demo namespace package.

Demo packages live under this namespace so ``state_class:`` references in
demo IR YAMLs (e.g. ``demos.sentinel_dark_watch.graph.state:SdwState``)
resolve via standard Python import. The demos themselves are not shipped
in the stargraph wheel — they're test/integration artifacts under the repo
root.
"""
