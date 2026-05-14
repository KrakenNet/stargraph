# SPDX-License-Identifier: Apache-2.0
"""cve_remediation demo bootstrap.

One-shot provisioning step. Idempotent — safe to re-run.

Phases:
  1. Wait for docker-compose services healthy (postgres, pgvector, redis,
     mock-servicenow, llm-shim).
  2. Provision schemas:
       postgres   audit_chain, retros, epss_kev_snapshot
       pgvector   doctrine_embeddings, retro_buffer (with vector column)
       redis      reflexion namespace ping
       ryugraph   skip (embedded driver; demo uses local file-backed instance)
  3. Generate Nautilus broker signing key (Ed25519) at the path declared
     by NAUTILUS_SIGNING_KEY_PATH (gitignored).
  4. Sign the 5 cve_rem.* Bosun packs.
  5. Seed doctrine corpora rows (small fixture set covering MITRE ATT&CK
     T1190, CISA KEV CVE-2026-12345, NIST 800-53 SC-7).
  6. Print a summary table and exit 0.

Usage::

    cp demos/cve_remediation/.env.example demos/cve_remediation/.env
    docker compose -f demos/cve_remediation/docker-compose.yml up -d
    uv run --no-project python -m demos.cve_remediation.bootstrap
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# .env loader (no external dep — keeps bootstrap stand-alone)
# ---------------------------------------------------------------------------


def _load_env(env_path: Path) -> None:
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())


_DEMO_DIR = Path(__file__).resolve().parent
_load_env(_DEMO_DIR / ".env")
_load_env(_DEMO_DIR / ".env.example")  # fallback for unset keys


# ---------------------------------------------------------------------------
# Health probes
# ---------------------------------------------------------------------------


def _wait_tcp(host: str, port: int, timeout_seconds: int = 60) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        with contextlib.suppress(OSError):
            with socket.create_connection((host, port), timeout=1):
                return
        time.sleep(1)
    raise RuntimeError(f"timeout waiting for {host}:{port}")


def _wait_http_health(url: str, timeout_seconds: int = 60) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        with contextlib.suppress(Exception):
            with urllib.request.urlopen(url, timeout=2) as resp:  # noqa: S310 — local trusted URL
                if resp.status == 200:
                    return
        time.sleep(1)
    raise RuntimeError(f"timeout waiting for {url}")


def _wait_services() -> None:
    print("[1/6] Waiting for docker-compose services…")
    _wait_tcp("localhost", int(os.environ["POSTGRES_PORT"]))
    print("      postgres OK")
    _wait_tcp("localhost", int(os.environ["PGVECTOR_PORT"]))
    print("      pgvector OK")
    _wait_tcp("localhost", int(os.environ["REDIS_PORT"]))
    print("      redis    OK")
    _wait_http_health(
        os.environ["SERVICENOW_BASE_URL"].rstrip("/") + "/health",
    )
    print("      mock-servicenow OK")
    _wait_http_health(
        os.environ["LLM_BASE_URL"].rstrip("/").rsplit("/v1", 1)[0] + "/health",
    )
    print("      llm-shim OK")


# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------


def _provision_postgres() -> None:
    print("[2/6] Provisioning postgres schemas…")
    import psycopg

    with psycopg.connect(os.environ["POSTGRES_DSN"]) as conn, conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_chain (
                seq          BIGSERIAL PRIMARY KEY,
                run_id       TEXT NOT NULL,
                step         INT NOT NULL,
                event_kind   TEXT NOT NULL,
                payload_json JSONB NOT NULL,
                emitted_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                prev_sha256  TEXT,
                row_sha256   TEXT
            );
            CREATE INDEX IF NOT EXISTS audit_chain_run_idx ON audit_chain(run_id);

            CREATE TABLE IF NOT EXISTS retros (
                retro_id              TEXT PRIMARY KEY,
                cve_id                TEXT NOT NULL,
                outcome               TEXT NOT NULL,
                payload_json          JSONB NOT NULL,
                docx_artifact_ref     TEXT,
                created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS epss_kev_snapshot (
                cve_id        TEXT PRIMARY KEY,
                epss_score_bp INT NOT NULL,
                kev_listed    BOOLEAN NOT NULL,
                snapshot_date DATE NOT NULL,
                refreshed_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS doctrine_allowlist (
                manifest_hash  TEXT PRIMARY KEY,
                active         BOOLEAN NOT NULL,
                signed_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )


def _provision_pgvector() -> None:
    print("[3/6] Provisioning pgvector schemas…")
    import psycopg

    dim = int(os.environ["PGVECTOR_DIM"])
    with psycopg.connect(os.environ["PGVECTOR_DSN"]) as conn, conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS doctrine_embeddings (
                id             TEXT PRIMARY KEY,
                source         TEXT NOT NULL,
                corpus_pin     TEXT NOT NULL,
                text           TEXT NOT NULL,
                embedding      vector({dim}) NOT NULL
            );
            CREATE TABLE IF NOT EXISTS retro_buffer (
                retro_id       TEXT PRIMARY KEY,
                cwe_class      TEXT NOT NULL,
                outcome        TEXT NOT NULL,
                summary_text   TEXT NOT NULL,
                embedding      vector({dim}) NOT NULL,
                created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )


def _provision_redis() -> None:
    print("[4/6] Pinging redis…")
    import redis

    client = redis.Redis.from_url(os.environ["REDIS_URL"])
    client.ping()
    # Reflexion namespace marker — establishes that we own the prefix.
    client.set("cve_rem:reflexion:bootstrap", time.strftime("%Y-%m-%dT%H:%M:%SZ"))


# ---------------------------------------------------------------------------
# Crypto + packs
# ---------------------------------------------------------------------------


def _gen_nautilus_key() -> None:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        NoEncryption,
        PrivateFormat,
        PublicFormat,
    )

    key_path = Path(os.environ["NAUTILUS_SIGNING_KEY_PATH"]).expanduser()
    if key_path.exists():
        return
    key_path.parent.mkdir(parents=True, exist_ok=True)
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key()
    key_path.write_bytes(
        priv.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    )
    pub_path = key_path.with_suffix(".pub.pem")
    pub_path.write_bytes(pub.public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo))
    print(f"      generated Nautilus broker key  {key_path.name}")


def _sign_packs() -> None:
    print("[5/6] Signing cve_rem.* Bosun packs…")
    subprocess.run(
        [sys.executable, "-m", "demos.cve_remediation.sign_packs"],
        check=True,
    )


# ---------------------------------------------------------------------------
# Doctrine seed
# ---------------------------------------------------------------------------


_DOCTRINE_FIXTURES = [
    {
        "id": "mitre.attack.T1190",
        "source": "mitre.org",
        "corpus_pin": "weekly-2026-05",
        "text": "Exploitation of Public-Facing Application: adversaries exploit weaknesses in internet-facing systems to gain initial access.",
    },
    {
        "id": "cisa.kev.CVE-2026-12345",
        "source": "cisa.gov",
        "corpus_pin": "weekly-2026-05",
        "text": "CVE-2026-12345 added to Known Exploited Vulnerabilities catalog; nginx 1.20 XSS in error template.",
    },
    {
        "id": "nist.800-53.SC-7",
        "source": "nvd.nist.gov",
        "corpus_pin": "weekly-2026-05",
        "text": "Boundary Protection: monitor and control communications at the external boundary of the system.",
    },
]


def _seed_doctrine() -> None:
    print("[6/6] Seeding doctrine corpora…")
    import psycopg

    dim = int(os.environ["PGVECTOR_DIM"])
    # Deterministic embeddings: hash text → repeating dim-length vec in [-1, 1].
    # Real embedder lands in Phase E follow-up. Matches FR-4 (no float
    # precision needed; values stable across runs).
    import hashlib

    def _stub_embed(text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        # 32 bytes -> map to dim floats in [-1, 1] by tiling
        seed = [(b - 128) / 128.0 for b in digest]
        return [seed[i % len(seed)] for i in range(dim)]

    with psycopg.connect(os.environ["PGVECTOR_DSN"]) as conn, conn.cursor() as cur:
        for fix in _DOCTRINE_FIXTURES:
            cur.execute(
                """
                INSERT INTO doctrine_embeddings (id, source, corpus_pin, text, embedding)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    text = EXCLUDED.text,
                    embedding = EXCLUDED.embedding
                """,
                (
                    fix["id"],
                    fix["source"],
                    fix["corpus_pin"],
                    fix["text"],
                    json.dumps(_stub_embed(fix["text"])),
                ),
            )
    print(f"      seeded {len(_DOCTRINE_FIXTURES)} doctrine rows")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    print(f"cve_remediation bootstrap  ({_DEMO_DIR})")
    _wait_services()
    _provision_postgres()
    _provision_pgvector()
    _provision_redis()
    _gen_nautilus_key()
    _sign_packs()
    _seed_doctrine()
    print("Bootstrap complete. Next: run `python -m demos.cve_remediation.serve_demo`.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
