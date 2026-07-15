# Competition Evidence Infrastructure

This directory contains all competition evaluation infrastructure: datasets, runners, metrics, configs, and reports.

## Quick Start

```bash
# 1. Preflight — verify environment is ready
python benchmarks/competition/preflight.py

# 2. (Future) Reset demo data
python scripts/competition/reset_demo_data.py

# 3. (Future) Run evaluation
python benchmarks/competition/runners/run_eval.py --variant B4_full

# 4. (Future) Verify evidence
python benchmarks/competition/runners/verify_evidence.py --run-id <run_id>
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

| ID | Statement | Current | Target |
|----|-----------|---------|--------|
| C-KNOW-01 | Knowledge contribution→review→ingest→reuse | L2 | L4 |
| C-RAG-01 | RAG with real scores, permission-first, citations | L1-L2 | L4 |
| C-WORK-01 | NL order→approval→write→audit | L1 | L4 |
| C-TRUST-01 | Unforgeable approval, server-verified resume | L1-L2 | L4 |
| C-TRUST-02 | Idempotent write, verified compensating rollback | L1-L2 | L4 |
| C-HISTORY-01 | History used by model, PG checkpoint persistence | L1 | L3/L4 |
| C-SKILL-01 | Real Skill E2E from chat, fail-closed on invalid | L1-L2 | L3/L4 |

## Evidence Rules

1. **Real full-chain only**: Real LLM, real Embedding, real PostgreSQL. Mock only for unit tests.
2. **No cherry-picking**: case_ids fixed before formal run; no post-hoc selection.
3. **Database is truth**: Final business state verified by DB snapshot, not Agent response or UI toast.
4. **SHA-256 verified**: All evidence files hashed; verifier checks integrity.
5. **No secrets**: All configs redacted; secret scanner runs before packaging.

## Completion Levels

| Level | Meaning | Allowed statement |
|-------|---------|-------------------|
| L1 | Source exists | "Designed/implemented module" |
| L2 | Component trustworthy | "Component supports..." |
| L3 | Main chain closed | "System can..." |
| L4 | Evidence closed | "Achieves X on dataset Y with boundary Z" |
