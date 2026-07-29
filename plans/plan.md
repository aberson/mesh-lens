# Seed Plan: mesh-lens

<!-- decisions-applied: 2026-07-26 per dev/docs/plan-reviews/2026-07-25-utility/DECISIONS.md -->

## 1. What This Feature Does

Proposal: `../../docs/utility-project-proposal.html`

Mesh Lens is a local-first utility project for explicit Skill Mesh telemetry analysis. It normalizes
skill/model dispatch records and downstream outcome evidence, then reports comparable cohorts by
skill, model, project, and task type with provenance, missing-data warnings, and sample-size limits.
It measures routing outcomes but never changes routing.

<!-- autofix-applied: 2026-07-25 -->
## 2. Existing Context

- Skill Mesh currently appends one JSON object per dispatch to
  `../../.claude/lib/telemetry/invocations.jsonl` (UTF-8, no BOM, append-only). Producer format
  contract: `../../documentation/multi-model/telemetry-schema.md` — cited as a file-format
  contract, not a build dependency. Full producer record shape (all eight fields):

  `SkillMeshInvocation` shape:

  | field | type | note |
  |---|---|---|
  | `timestamp` | string | UTC ISO-8601, written at record time |
  | `skill` | string | skill directory/name requested from the router |
  | `model` | string | resolved model ID used for the dispatch (e.g. `gpt-5.5`, `claude`, `code-30b`) |
  | `tokens_in` | integer | provider-reported input tokens; 0 when unavailable or in stub mode |
  | `tokens_out` | integer | provider-reported output tokens; 0 when unavailable or in stub mode |
  | `latency_ms` | integer | dispatch wall-clock latency in milliseconds |
  | `cost_usd` | number | provider-reported or adapter-calculated USD cost; 0 when unavailable or in stub mode |
  | `verdict` | string | `pass`, `fail`, or `stub` (`stub` = no billable invocation measured; absent `OPENAI_API_KEY` forces stub + zeroed token/cost fields) |

  The stream carries no native record/run/session identifier — correlation keys must be derived
  or discovered (Step 1 classifies candidates as present, derivable, ambiguous, or absent).
- That schema does not currently connect dispatches to tests, retries, review verdicts, or final
  shipped/parked/blocked disposition.
- Candidate outcome-artifact classes (Step 1 classifies each as present, derivable, ambiguous, or
  absent; Step 3 builds adapters only for classes with a provable join key — "most classes lack a
  dispatch-correlatable key and stay unjoined" is a valid, expected inventory outcome):
  1. `../../.build-step/<role>-report.md` dev/reviewer reports (verified present at the workspace root)
  2. GitHub issue states via `gh issue list --json number,title,state` per project repo
  3. `git log` of the target repo
  4. Plan `**Status:** DONE` / `### Step N:` markers in canonical plans
  5. skill-iterate run-logs
- Parked (non-gating): a follow-up ask for Skill Mesh to emit a native `schema_version` field — a
  separately reviewed cross-repository plan. V1 infers producer schema instead (see §6).
- The approved seed is `../../docs/seeds/seed_mesh_lens.md`.
- Mesh Lens is the explicit analytics owner. Skill Mesh remains the producer; Dev Observatory is at
  most an optional renderer of already-produced summaries.

## 3. Scope

**In:** Python 3.12+ and uv; telemetry inventory; versioned normalized JSONL; adapters for current
dispatch records and available outcome artifacts; ingest/report/compare; static HTML/JSON; provenance;
missingness, cohort, confound, and sample-size warnings.

**Out:** automatic routing changes, bandits, causal claims from correlation, fabricated token/cost
values, external telemetry, daemon collection, and cross-project comparisons without stratification.

## 4. Impact Analysis

| File | Change Type | Reason | Verified |
|---|---|---|---|
| `plans/plan.md` | add | Canonical project plan | New project |
| `../../documentation/multi-model/telemetry-schema.md` | read-only input | Current Skill Mesh producer format contract | Read directly; eight fields confirmed |
| `../../.claude/lib/telemetry/invocations.jsonl` | read-only input | Existing invocation stream | Path confirmed by producer documentation |
| `../../docs/seeds/seed_mesh_lens.md` | read-only input | Approved seed | Read directly |

V1 adapters do not change producer schemas. Any later producer extension requires a separately
reviewed cross-repository plan.

## 5. New Components

- `src/mesh_lens/models.py`: normalized invocation, outcome, cohort, and provenance shapes.
- `src/mesh_lens/inventory.py`: producer/artifact availability audit.
- `src/mesh_lens/adapters/`: Skill Mesh JSONL and downstream result readers.
- `src/mesh_lens/store.py`: append-only normalized local event/result files.
- `src/mesh_lens/analyze.py`: grouping, missingness, and comparison rules.
- `src/mesh_lens/render.py`: static HTML and JSON reports.
- `src/mesh_lens/cli.py`: `ingest`, `report`, and `compare`.

## 6. Design Decisions

**Inventory before schema expansion.** Phase 1 records which desired fields exist, are derivable, or
are absent. Missing fields remain missing instead of receiving success-shaped defaults.

**Producer/analyst separation.** Mesh Lens reads Skill Mesh contracts and owns normalized analytics.
It does not become the router or write back routing decisions.

**Comparison requires cohorts.** Reports stratify by skill, pinned model, project, task type, and
schema version. Incomparable records are not merged into one rate.

**Correlation only.** Reports state sample size, missingness, and confounds. Decision-changing metrics
must be defined before looking at the relevant comparison.

**Record identity and re-ingest idempotency.** A normalized record's ID is its source-provenance ref
`<source-relpath>@<line-number>` (e.g. `invocations.jsonl@17`) plus a stored SHA-256 `content_hash`
of the raw line. A pure content-hash ID is not viable: stub records zero all token/cost fields, so
two dispatches in the same ISO-8601 second are byte-identical. Re-ingest is idempotent via a
per-source byte-offset checkpoint with hash-verify on the overlap window; a mismatch means rotation
or truncation and triggers a rebuild of the derived store.

**Correlation keys come from the inventory.** Only fields Step 1 classifies as present or derivable
qualify as correlation keys. Today's eight-field producer contract carries no run/session key, so
timestamp-window joins are classified ambiguous and stay unjoined.

**Producer schema by inference; store schema_version from day one.** The Skill Mesh adapter assigns
`producer_schema="skillmesh-v1"` only when a record's field set exactly matches the pinned
eight-field contract (§2); any other field set lands in an `unknown` cohort that is never merged —
it is reported incomparable. Independently, every normalized store record and JSON report carries
integer `schema_version: 1`; readers tolerate older versions and refuse newer ones with an explicit
error.

**Independent by contract.** Mesh Lens binds to file formats and well-known paths, never to another
project's internals or build state. The Skill Mesh adapter consumes the pinned eight-field JSONL
format (§2) as a format contract — any substitute producer emitting the same format is analyzed
identically. An absent or empty telemetry stream degrades gracefully: ingest completes with zero
records and the report states that no records were ingested, never an error. Outcome-artifact
adapters bind the same way; an absent artifact class is reported as missing, not fatal.

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

<!-- autofix-applied: 2026-07-25 -->
### Step 2: Define normalized schemas and ingest dispatch telemetry
- **Problem:** Create versioned invocation/outcome/provenance models and ingest the existing Skill Mesh JSONL without fabricating unavailable values. Each normalized record carries its provenance ID (`<source-relpath>@<line-number>`) and SHA-256 `content_hash`; re-ingest stays idempotent via the per-source byte-offset checkpoint (§6).
- **Type:** code
- **Issue:** #8
- **Flags:** --reviewers deep --isolation worktree
- **Produces:** `models.py`, `store.py`, Skill Mesh adapter, `mesh-lens ingest`, provenance record IDs, per-source ingest checkpoints
- **Done when:** real and fixture invocation rows round-trip; stub and unavailable values remain distinguishable; malformed rows diagnose without aborting siblings; records whose field set exactly matches the pinned eight-field contract carry `producer_schema="skillmesh-v1"` and any other field set lands in the `unknown` cohort, never merged
- **Depends on:** 1
- **Status:** DONE (2026-07-29)

<!-- autofix-applied: 2026-07-25 -->
### Step 3: Add outcome adapters and correlation keys
- **Problem:** Ingest available test, review, retry, and final-disposition artifacts from the candidate classes pinned in §2, building adapters only for classes with a provable join key, and correlate them only where stable run/session keys prove the relationship. Only fields Step 1 classified present or derivable qualify as correlation keys (§6); timestamp-window joins are ambiguous and stay unjoined.
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

<!-- autofix-applied: 2026-07-25 -->
### Step 5: Add guarded pairwise comparison
- **Problem:** Compare two pinned cohorts with sample-size thresholds, missing-data disclosure, project/task stratification, and correlation-not-causation language.
- **Type:** code
- **Issue:** #11
- **Flags:** --reviewers deep --isolation worktree
- **Produces:** `mesh-lens compare`, comparison fixtures
- **Done when:** incomparable or undersized cohorts refuse a directional verdict; valid fixtures compute reproducible deltas with caveats
- **Depends on:** 4

### Step 6: Observe one real recurring routing decision
- **Problem:** Run ingestion and reporting over available historical or newly captured Skill Mesh records and evaluate one predeclared routing question without changing routing. With today's stub-only stream the expected result is an insufficient-evidence verdict citing every contributing stub record — that verdict exercises the full ingest, report, and refusal pipeline and completes this step; the step is cheaply re-runnable after skill-mesh M2 lands real telemetry, and that re-run is non-gating.
- **Type:** operator
- **Issue:** #12
- **Produces:** operator evidence record only
- **Done when:** the report identifies whether evidence is sufficient, insufficient, or incomparable and cites every contributing source record; an insufficient-evidence verdict over today's stub-only records satisfies this step (the post-M2 re-run is non-gating)
- **Depends on:** 5

## 8. Risks and Open Questions

| Item | Risk | Mitigation |
|---|---|---|
| Sparse outcomes | Reports imply more evidence than exists | Inventory, missingness, sample thresholds |
| Bad joins | Outcomes attach to wrong dispatch | Stable keys only; ambiguous remains unjoined |
| Goodhart pressure | Routing optimizes dashboard metrics | Reporting-only boundary |
| Misleading comparisons | Project/task mix confounds result | Cohort stratification and refusal states |

## 9. Testing Strategy

Use frozen current-schema telemetry, malformed and mixed-version JSONL, synthetic outcome joins,
aggregate goldens, undersized/incomparable cohort cases, and provenance assertions. Step 2 performs
the real producer-to-store smoke before reports. Step 6 is operator observation and produces no code.

## 10. Build and Run Contract

Bootstrap with Python 3.12+ and `uv sync --extra dev`. Quality gates are `uv run pytest -q`,
`uv run ruff check .`, and `uv run mypy --strict src`. The installed CLI entry point is
`mesh-lens`; source paths are explicit and all normalized data and reports stay local.

## Appendix: Decision Inventory

| ID | P/D | Choice | Status |
|---|---|---|---|
| P7 | P | Build Mesh Lens as explicit Skill Mesh telemetry rather than Observatory logic | accepted |
| D1 | D | Use Python 3.12+, uv, argparse, pytest, Ruff, and mypy strict | accepted |
| D3 | D | Initialize a separate nested GitHub repository before build | accepted |
| D9 | D | Inventory real telemetry before schema expansion or comparison | accepted |
