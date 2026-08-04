# SPDX-License-Identifier: Apache-2.0
"""``stargraph.tools.s3`` -- S3 object tools (namespace ``s3``).

| tool | capability | side effects |
|---|---|---|
| ``s3.get_object@1`` | ``tools:s3:read`` | read |
| ``s3.list_objects@1`` | ``tools:s3:read`` | read |
| ``s3.put_object@1`` | ``tools:s3:write`` | write (dry-run by default) |

``boto3`` (the ``stargraph[tools-saas]`` extra) is imported lazily
inside the tool bodies, so the tools always register and a missing
dependency fails loudly with a pip hint at call time. Credentials and
region resolve through boto3's default chain (env / shared config /
instance role). The blocking client runs in a worker thread.

Writes follow the SaaS safety boundaries (:mod:`stargraph.tools._saas`):
dry-run unless ``STARGRAPH_S3_LIVE`` is truthy. ``put_object`` needs no
separate dedupe key -- the object key IS the idempotency key (a retried
put of the same content to the same key converges).
"""

from __future__ import annotations

from stargraph.tools.s3.get_object import get_object
from stargraph.tools.s3.list_objects import list_objects
from stargraph.tools.s3.put_object import put_object

__all__ = ["get_object", "list_objects", "put_object"]
