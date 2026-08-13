# SPDX-License-Identifier: Apache-2.0
"""LM endpoint lifecycle: bind a graph run to a local inference server.

Today one provider: :mod:`stargraph.lm.sglang`, which attaches to (or boots
and tears down) an SGLang OpenAI-compatible server for the length of a run.
"""

from __future__ import annotations

from stargraph.lm.sglang import base_url, served_models, sglang_server

__all__ = ["base_url", "served_models", "sglang_server"]
