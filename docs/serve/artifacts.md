# Artifacts

Stargraph's artifact store is a content-addressed (BLAKE3) store for run
outputs that don't fit cleanly into the graph state shape: large
documents, generated reports, attached files. Artifacts are written via
`WriteArtifactNode` and read via `/v1/runs/{id}/artifacts` + `/v1/artifacts/{ref}`.

The `ArtifactRef` Pydantic model (in `stargraph.artifacts.base`) carries the
content hash, MIME type, size, and provenance bundle; it appears in the
OpenAPI spec under `components/schemas/ArtifactRef`.

## Topics

- TODO: BLAKE3 content-addressing scheme + dedup semantics.
- TODO: storage backend Protocol (filesystem, S3-compatible).
- TODO: artifact lifecycle (write-once, GC'd by retention policy).
- TODO: `/v1/runs/{id}/artifacts` listing + filtering.
- TODO: replay isolation (artifact reads are deterministic).
