## 7. Build Steps

### Step 1: Audit existing telemetry and outcome availability
- **Problem:** Inventory every current producer field and candidate outcome artifact (the five classes pinned in §2), verify paths and semantics from producing code, and classify fields as present, derivable, ambiguous, or absent.
- **Type:** code
- **Issue:** #7
- **Flags:** --reviewers code --isolation worktree
- **Produces:** scaffold, `inventory.py`, `docs/telemetry-inventory.md`, frozen source fixtures
- **Done when:** every proposed metric has a verified source or is explicitly marked unavailable; no producer modification is hidden in this plan
- **Depends on:** none
- **Status:** DONE (2026-07-29)

### Step 2: Define normalized schemas and ingest dispatch telemetry
- **Problem:** Create versioned invocation/outcome/provenance models and ingest the existing Skill Mesh JSONL without fabricating unavailable values.
- **Type:** code
- **Issue:** #8
- **Flags:** --reviewers deep --isolation worktree
- **Produces:** `models.py`, `store.py`, Skill Mesh adapter, `mesh-lens ingest`, provenance record IDs, per-source ingest checkpoints
- **Done when:** real and fixture invocation rows round-trip; stub and unavailable values remain distinguishable; malformed rows diagnose without aborting siblings
- **Depends on:** 1
- **Status:** DONE (2026-07-29)

### Step 3: Add outcome adapters and correlation keys
- **Problem:** Ingest available test, review, retry, and final-disposition artifacts from the candidate classes pinned in §2, building adapters only for classes with a provable join key.
- **Type:** code
- **Issue:** #9
- **Flags:** --reviewers deep --isolation worktree
- **Produces:** outcome adapters, correlation diagnostics
- **Done when:** joined fixtures preserve source provenance, ambiguous joins remain unjoined, and missing outcomes are reported rather than inferred
- **Depends on:** 2

### Step 4: Build aggregate reports
- **Problem:** Report counts, latency, tokens/cost where measured, pass/fail/stub, outcomes, retries, and missingness by comparable cohorts.
- **Type:** code
- **Issue:** #10
- **Flags:** --reviewers code --isolation worktree
- **Produces:** `analyze.py`, `render.py`, `mesh-lens report`, static HTML/JSON
- **Done when:** synthetic aggregates reproduce expected values and every displayed metric resolves to source record IDs
- **Depends on:** 3

### Step 5: Add guarded pairwise comparison
- **Problem:** Compare two pinned cohorts with sample-size thresholds, missing-data disclosure, project/task stratification, and correlation-not-causation language.
- **Type:** code
- **Issue:** #11
- **Flags:** --reviewers deep --isolation worktree
- **Produces:** `mesh-lens compare`, comparison fixtures
- **Done when:** incomparable or undersized cohorts refuse a directional verdict; valid fixtures compute reproducible deltas with caveats
- **Depends on:** 4

### Step 6: Observe one real recurring routing decision
- **Problem:** Run ingestion and reporting over available historical or newly captured Skill Mesh records and evaluate one predeclared routing question without changing routing.
- **Type:** operator
- **Issue:** #12
- **Produces:** operator evidence record only
- **Done when:** the report identifies whether evidence is sufficient, insufficient, or incomparable and cites every contributing source record
- **Depends on:** 5
