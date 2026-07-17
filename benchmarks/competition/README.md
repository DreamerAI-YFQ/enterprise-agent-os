# Competition Evidence Infrastructure

This directory contains all competition evaluation infrastructure: datasets, runners, metrics, configs, and reports.

## Demo credentials

The seed creates local-only demo accounts for the competition runners:

All three accounts belong to the demo tenant slug `acme-corp`.

| Account | Default demo password |
|---|---|
| `admin@acme.com` | `EaosDemo-Admin-2026!` |
| `manager@acme.com` | `EaosDemo-Manager-2026!` |
| `employee@acme.com` | `EaosDemo-Employee-2026!` |

These credentials are intentionally documented and therefore are **not
production secrets**. Every deployed environment must override the matching
`EAOS_*_PASSWORD` values, reset the seeded accounts, and rotate credentials
before accepting traffic.

## Quick Start

```bash
# 1. Preflight — verify environment is ready
uv run python benchmarks/competition/preflight.py

# 2. Reset deterministic demo data when a clean baseline is required
uv run python scripts/competition/reset_demo_data.py

# 3. Run one frozen evaluation profile with a unique run ID
uv run python benchmarks/competition/runners/run_eval.py \
  --suite order \
  --order-profile core-v1 \
  --run-id order-core-v1-YYYYMMDD

# 4. Verify the exported evidence package
uv run python scripts/competition/verify_evidence.py \
  --run-id <run_id> \
  --require-source-clean \
  --require-results \
  --require-traces \
  --require-usage
```

## Directory Structure

```
benchmarks/competition/
├── README.md                # This file
├── preflight.py             # Environment preflight checker
├── claim_matrix.yaml        # Capability claims with thresholds and evidence links
├── configs/
│   ├── eval_v1.yaml         # Evaluation configuration (frozen after Pilot)
│   ├── baselines_v1.yaml    # Baseline and ablation definitions
│   └── model_prices_v1.yaml # LLM/Embedding pricing for cost calculation
├── datasets/                # Frozen datasets with SHA-256
├── schemas/                 # JSON Schemas for Gold, Case Result, Manifest
├── runners/                 # Evaluation runners
├── metrics/                 # Metric implementations
└── reports/                 # Generated reports (per run_id)

tests/competition/           # Competition E2E acceptance tests
scripts/competition/         # Demo reset and acceptance scripts
artifacts/competition-evidence/<run_id>/  # Generated evidence (gitignored)
```

## Claim Matrix

See `claim_matrix.yaml` for the full list of capability claims. Each claim must close the loop:

```
Claim → Source Anchor → Reproducible Entry → Raw Artifacts → Metrics → Boundary
```

### Current Claims

| ID | Statement | Current | Evidence status |
|----|-----------|---------|-----------------|
| C-KNOW-01 | Knowledge contribution→review→ingest→reuse | L4 | 3/3 frozen E2E demo runs passed |
| C-RAG-01 | Permission-first retrieval, grounded answers, citations | L4 | Core-48 retrieval + Core-16 answer evidence frozen |
| C-WORK-01 | NL order→approval→write→audit | L4 | 27/27 frozen core cases passed |
| C-TRUST-01 | Unforgeable approval, server-verified resume | L4 | 60/60 safety cases passed; risk grading remains coarse |
| C-TRUST-02 | Idempotent write, verified compensating rollback | L4 | Core cases and 3/3 demo repeats passed |
| C-HISTORY-01 | History used by model, PG checkpoint persistence | L3 | Implemented and regression-tested; no dedicated frozen benchmark |
| C-SKILL-01 | Skill selection/execution with fail-closed guardrails | L3 | Implemented and regression-tested; no dedicated frozen benchmark |

L4 denotes a closed evidence chain within the boundary stated in
`claim_matrix.yaml`; it is not a claim of unlimited scope or production-scale
validation. Diagnostic shortfalls and sample-size limits remain part of the
claim rather than being hidden from the competition narrative.

## Evidence Rules

1. **Real full-chain only**: Real LLM, real Embedding, real PostgreSQL. Mock only for unit tests.
2. **No cherry-picking**: case_ids fixed before formal run; no post-hoc selection.
3. **Database is truth**: Final business state verified by DB snapshot, not Agent response or UI toast.
4. **SHA-256 verified**: All evidence files hashed; verifier checks integrity.
5. **No secrets**: All configs redacted; secret scanner runs before packaging.

## Retrieval-only RAG evaluation

Use the retrieval-only runner to measure the real vector + deterministic
keyword + RRF path without query rewriting, LLM reranking, answer generation,
or Agent routing:

```bash
uv run python benchmarks/competition/runners/run_retrieval.py
```

The runner selects all 150 cases by default, but the current dataset is **not a
formal frozen set while `frozen_date: pending` remains in the YAML**. Cases
without positive document judgments are retained in the raw output but excluded
from Hit/Recall/Precision/nDCG/MRR. An empty result is a strict failure only for
a positive-gold case; the 13 `empty` cases have a separate empty-retrieval
accuracy. The 17 `refusal` cases and citation quality require answer generation
and are intentionally not scored by this retrieval-only runner. Exceptions and
returned-chunk permission violations are always strict failures. Each run writes
`retrieval_results.jsonl`, `retrieval_metrics.json`, and a `manifest.json`
binding the result to the dataset hash, source state, corpus fingerprint,
embedding configuration, query-vector fingerprints, and artifact hashes.

After human gold review locks an ISO freeze date, sets
`metadata.gold_review_status: approved`, and supplies an ISO
`metadata.as_of_date` for relative-time queries, use the single formal gate
instead of composing a pilot command manually:

```bash
uv run python benchmarks/competition/runners/run_retrieval.py \
  --run-id retrieval-formal-YYYYMMDD \
  --top-k 5 \
  --formal
```

`--formal` aborts before case execution unless the source tree is clean, the
dataset date and human gold review are locked, relative-time cases have a fixed
`as_of_date`, all cases are selected, and every expected benchmark `KB-*` label
exists exactly once. Real base-seed or E2E distractor documents are allowed to
remain in the corpus when they have no benchmark `KB-*` label; they are counted
and included in the corpus fingerprint. This preserves a realistic retrieval
environment without allowing missing, duplicate, or extra benchmark gold
documents.

The corpus schema does not currently persist the embedding model used to create
stored vectors. The manifest records this limitation and identifies only the
query embedder. Do not describe a run as fully embedding-reproducible until the
corpus model identity is bound by the seed/evidence process.

The runner independently checks the tenant and personal/department ownership of
every returned chunk, and the manifest records corpus scope counts. That alone
is not an empirical permission-filtering rate. The static 60-document
competition seed is enterprise-scope only; a run without the Core-48 permission
fixture cannot substantiate permission filtering or cross-tenant leakage
performance and exercises only the query path and returned-chunk postcondition.

### Preregistered Core-48 profile

The audited 48-case profile uses a result-independent SHA-256 rank to select six
cases from each of the eight source categories. Its source hash, selection seed,
all 102 deterministic exclusions, and seven gold corrections are recorded in
`configs/retrieval_core_v1.yaml` and
`datasets/rag_queries_core_v1_ledger.yaml`. Run it only from a clean source tree:

```bash
uv run python benchmarks/competition/runners/run_retrieval.py \
  --profile core-v1 \
  --run-id retrieval-core-v1-YYYYMMDD \
  --formal
```

Formal Core-48 runs install three run-scoped, embedded, high-similarity canaries:
one foreign tenant, one foreign department, and one foreign personal document.
Every canary repeats all six permission queries exactly. Any returned canary is
a hard failure. The runner removes only the deterministic fixture IDs, verifies
zero residual rows, and writes `permission_fixture_receipt.json`; setup or
cleanup uncertainty fails closed.

## Frozen 16-case RAG answer evaluation

Answer quality is evaluated independently from Core-48 retrieval metrics. The
registered `answer-core-v1` profile points to
`datasets/rag_answers_core_v1.yaml`, which reuses 16 preregistered Core-48 case
IDs without changing the Core-48 dataset. Its composition is four fact, three
list, three summary, three no-answer, and three permission cases.

For a short non-formal pilot, select explicit IDs. Selection is always emitted
in the frozen dataset order, and an unknown or duplicate ID aborts the run:

```bash
python -u benchmarks/competition/runners/run_eval.py \
  --suite rag \
  --rag-profile answer-core-v1 \
  --run-id rag-answer-pilot-YYYYMMDD \
  --case-id RAG-021 \
  --case-id RAG-129 \
  --case-id RAG-136
```

The formal command intentionally has no `--limit` or `--case-id`:

```bash
python -u benchmarks/competition/runners/run_eval.py \
  --suite rag \
  --rag-profile answer-core-v1 \
  --run-id rag-answer-core-v1-YYYYMMDD \
  --formal
```

The answer runner uses the real Agent SSE path with `mode=rag` and the configured
generation model. Content is judged by audited claim-group recall. A citation
passes only when its numbered marker maps to the actual ranked evidence from
that invocation and at least one cited document is in the case gold. Silent
empty output does not count as refusal; no-answer and permission cases require
an explicit abstention or access-denial response.

Selected permission cases install the same three real-embedding scope canaries,
with an additional run-unique response token in each payload. A returned canary
or a run-unique token in the final answer is a leak. Generic refusal wording is
not a leak signal. Fixture setup and exact cleanup are both required evidence.

Each run writes `rag_answer_results.jsonl`, `rag_answer_metrics.json`,
`rag_answer_manifest.json`, and (when permission cases are selected)
`rag_answer_permission_fixture.json`. Formal execution fails closed unless the
tree is clean, profile/dataset SHA-256 and frozen gold are valid, all 16 cases
execute once in order, every Agent invocation succeeds, permission cleanup is
verified, and all preregistered quality thresholds pass. Failed cases remain in
the artifacts and cannot be replaced after the run.

## Completion Levels

| Level | Meaning | Allowed statement |
|-------|---------|-------------------|
| L1 | Source exists | "Designed/implemented module" |
| L2 | Component trustworthy | "Component supports..." |
| L3 | Main chain closed | "System can..." |
| L4 | Evidence closed | "Achieves X on dataset Y with boundary Z" |
