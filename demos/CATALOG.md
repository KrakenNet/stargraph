# Demo Catalog

A backlog of demos to build across Railyard's primitive types. Each subfolder
has its own focused catalog (`agents/CATALOG.md`, `tools/CATALOG.md`, etc.);
this file is the master index.

- **Generic** entries are broadly reusable, off-the-shelf primitives — the
  kind every Railyard deployment will want available.
- **Creative** entries are distinctive showcase pieces designed to highlight
  what Railyard's primitives (governors, traces, memory, KGs) make possible
  that other platforms don't.

Each item is intentionally one line: name + hook. They become design docs
when promoted into their own folder.

---

## Tools — see [`tools/CATALOG.md`](tools/CATALOG.md)

### Generic

- `http-fetch` — typed HTTP client with retry/backoff
- `shell-exec` — sandboxed shell command runner
- `sql-query` — read-only Postgres/SQLite query
- `vector-search` — pgvector similarity search
- `embed-text` — single-text embedding helper
- `web-scrape` — readability-mode page → markdown
- `pdf-extract` — PDF → text + tables
- `ocr` — image → text
- `csv-read` / `csv-write`
- `json-jq` — jq-style query over JSON
- `regex-match`
- `git-ops` — clone/diff/blame/log
- `slack-post` / `discord-post` / `email-send`
- `dns-lookup`, `whois`, `tls-cert-info`
- `markdown-html` (both directions)
- `token-count` (model-aware)
- `code-format` (prettier/black/gofmt)
- `lint-run` / `test-run`
- `aws-cli`, `gcloud`, `kubectl` thin wrappers

---

## Agents — see [`agents/CATALOG.md`](agents/CATALOG.md)

### Generic

- `researcher` — query → cited brief
- `summarizer` — long doc → tiered bullets
- `classifier` — label-set → label
- `extractor` — text → typed fields
- `translator`
- `code-reviewer`
- `sql-writer`
- `email-drafter`
- `meeting-notes` — transcript → action items
- `triage` — inbound → category + priority
- `support-agent` — RAG over KB
- `faq-answerer`
- `pr-describer`
- `changelog-writer`
- `test-case-writer`
- `bug-reproducer`
- `onboarding-guide`
- `form-filler`
- `api-explorer`
- `runbook-runner`

### Creative

- `devils-advocate` — always argues against the current plan, never agrees
- `socratic-tutor` — only asks questions, never answers
- `steel-manner` — rebuilds the opposing view as strongly as it can before refuting
- `panel-of-five` — one prompt → five archetypal critiques (PM, SRE, Sec, IC, exec)
- `kintsugi` — finds load-bearing legacy code and writes appreciation/care notes for it
- `naive-newcomer` — asks "why?" until first principles
- `time-bomb-scout` — finds tech debt with expiry dates (deprecation calendars)
- `pattern-archaeologist` — excavates dead idioms from commit history
- `constraint-surfacer` — turns implicit team assumptions into explicit specs
- `dialect-translator` — rewrites text in different team idioms (eng↔sales↔legal)

---

## Workflows — see [`flows/CATALOG.md`](flows/CATALOG.md)

Existing flow demos: `code-graph/`.

### Generic

- `support-triage` — inbound ticket → category + owner + draft reply
- `doc-ingest-rag` — file drop → chunked + embedded + indexed
- `lead-enrichment` — name/email → enriched profile + score
- `invoice-extract-approve` — PDF → fields → approval → ERP
- `outreach-sequencer`
- `pr-review`
- `incident-response`
- `daily-digest`
- `inventory-reconcile`
- `kb-sync` — code/docs ↔ KB articles
- `data-quality-sweep`
- `stale-record-cleanup`
- `backup-verify`
- `license-expiry-watch`
- `api-contract-diff-alert`
- `employee-onboarding`
- `customer-churn-outreach`
- `expense-policy-check`
- `meeting-prep` — calendar + CRM + email recap
- `weekly-roll-up`

### Creative

- `counterfactual-replay` — re-runs past decisions on alt paths and grades each
- `trial-and-retro` — every decision auto-spawns a 7-day post-mortem with outcome
- `pre-mortem-first` — workflow spends N% of budget hunting failure modes before any action
- `devils-pair` — runs primary + opposing strategy in parallel, picks winner on evidence
- `forecast-then-score` — workflow predicts its own outcome up front, logs delta after
- `auto-hypothesis` — scans logs for surprises, proposes & queues experiments
- `inverse-onboarding` — produces a "what would I forget if I left tomorrow" doc
- `knowledge-half-life-sweep` — surfaces KB articles whose source code drifted
- `decision-journal-loop` — every multi-step plan gets an immutable rationale + outcome row
- `anti-cargo-cult` — periodically re-justifies any rule older than X or removes it

---

## Machine Learning — see [`machine-learning/CATALOG.md`](machine-learning/CATALOG.md)

### Generic

- `sentiment` — pos/neg/neutral
- `intent-classifier`
- `ner` — named-entity recognition
- `topic-model`
- `embedding-encoder`
- `toxicity-classifier`
- `timeseries-forecast`
- `anomaly-detector`
- `image-classifier`
- `object-detector`
- `ocr-model`
- `language-id`
- `spam-filter`
- `churn-predictor`
- `doc-classifier`
- `outlier-detector`
- `risk-scorer`
- `recommender`
- `clustering`
- `summarization-extractive`

### Creative (platform-aware)

- `trace-shape-anomaly` — learns "normal" span trees, flags weird ones
- `prompt-drift-classifier` — detects when an agent silently veers off-policy
- `cost-spike-forecaster` — predicts $$ blowups N minutes ahead
- `hallucination-scorer` — per-claim grounding confidence
- `tool-choice-predictor` — recommends which tool the agent _should_ have called
- `workflow-eta-predictor` — time-to-finish from intermediate state
- `operator-fatigue` — HITL reviewer quality decline detector
- `governor-rule-miner` — induces CLIPS rules from past escalation patterns
- `question-difficulty-router` — easy → small model, hard → big model
- `memory-utility-scorer` — predicts which memories are worth keeping past decay
