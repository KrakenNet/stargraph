=============================================================================
                   INTERNAL DOCS Q&A WITH CITATION AUDIT
=============================================================================

  Pitch:  Onboarding bot that answers questions over a company's internal
          docs (Confluence / Notion / repo READMEs / runbooks). Every
          answer cites the exact paragraph it came from. CI gate proves
          no claim entered an answer without provenance.

  Audience:  Every team that has tried "AI search over docs" and killed
             it because of hallucinations.

=============================================================================

[Doc ingestion (one-shot or cron)]
             │
             ├──► [PDFExtract / MarkdownLoader]
             ├──► [Chunker (semantic, ~512 tok)]
             ├──► [Embed via MiniLM (sha256-pinned)]
             ├──► [SqliteDocStore: chunk text + metadata]
             ├──► [LanceDBVectorStore: embeddings]
             └──► Each chunk carries ProvenanceBundle
                    (source_url, captured_at, author, doc_id, line_range)
             │
             ▼
[stargraph serve  -- /v1/runs POST]
             │
             ├──► [Auth: API key + capability gate (docs.read)]
             ├──► [Rate limit]
             │
             ▼
[QASkill subgraph]
             │
             ├──► [RetrievalNode]
             │      │
             │      ├──► VectorSearch (top-20)
             │      ├──► DocStore keyword fallback
             │      └──► RRF fusion -> top-5 chunks
             │
             ├──► [DSPyAdapter: answer_with_citations]
             │      Generates answer + per-claim chunk_id citations
             │
             └──► [WriteArtifactNode]
                    Stores answer + citations as
                    BLAKE3 content-addressable artifact
             │
             ▼
[stargraph.audit.JSONLAuditSink]
             Ed25519-signed record:
             question, retrieved_chunks, answer, citations, ruleset_hash
             │
             ▼
[CI gate: lineage_audit.py]
             Walks every claim in answer.citations[]
             Fails if any claim lacks a chunk with full ProvenanceBundle.
             Fails the deploy.

=============================================================================
                              WHY IT LANDS
=============================================================================

- Bar is low: every team has tried this. Most have failed.
- Differentiator: cryptographic citation chain, not "AI search."
- Sellable to: any company that killed a previous "AI search" project.
- Sellable to legal/compliance because every answer is auditable.

=============================================================================
                         STARGRAPH CAPABILITIES EXERCISED
=============================================================================

  Skills:        rag (reference skill)
  Stores:        DocStore (sqlite_doc), VectorStore (lancedb)
  Embeddings:    MiniLM, sha256-pinned safetensors
  Nodes:         RetrievalNode (RRF fusion), DSPy adapter, WriteArtifactNode
  Adapters:      DSPy
  Provenance:    Mandatory ProvenanceBundle per chunk
  Audit:         JSONLAuditSink (Ed25519 signed)
  Artifacts:     BLAKE3 content-addressable answer storage
  Auth:          API key + capability gate
  Tooling:       lineage_audit.py as a CI gate

=============================================================================
                              DEMO FOOTPRINT
=============================================================================

  demos/internal-docs-qa/
    README.md              -- run instructions + screenshots
    stargraph.yaml            -- graph definition, stores, skills
    docs/
      handbook/            -- 30 sample employee handbook pages
      runbooks/            -- 12 sample SRE runbooks
      adrs/                -- 8 architecture decision records
    fixtures/
      golden_qa.jsonl      -- 25 question/expected-citation pairs
    scripts/
      ingest.sh            -- chunk + embed + load
      ask.sh               -- one-shot CLI question
      lineage_audit_ci.sh  -- CI gate wrapper
    Makefile
      make ingest          -- build the corpus
      make ask Q="..."     -- interactive query
      make audit           -- run lineage gate (exits non-zero on miss)
      make demo            -- end-to-end
