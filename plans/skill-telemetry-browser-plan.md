# Skill Telemetry Browser

**Status:** APPROVED (2026-08-07)
**Depends on:** Skill Mesh provider telemetry work that emits real usage and a stable dispatch/run key

## 1. What This Feature Does

Adds a skill-first list/detail view over Mesh Lens telemetry and makes the existing report easy to
open, while preserving missingness, cohort, and correlation caveats.

Proposal: [Utility Projects UAT proposal](../../docs/utility-project-surfaces-proposal.html)

## 2. Existing Context

`store.py` owns normalized events, `analyze.py` aggregates cohorts, `correlate.py` owns joins,
`render.py` emits HTML/JSON, and `cli.py` dispatches inventory/ingest/report/compare. Current real
telemetry is sparse, token/cost fields may be placeholders, and outcomes lack a provable join key.

## 3. Scope

**In:** skill summaries/details, recent events, model mix, missingness, provenance, report auto-open,
and post-producer re-audit. **Out:** routing changes, fabricated outcomes, causal claims, or
timestamp-window joins.

## 4. Impact Analysis

| File | Change Type | Reason | Verified |
|---|---|---|---|
| `src/mesh_lens/analyze.py` | extend | skill summary/detail aggregates | current aggregation owner |
| `src/mesh_lens/models.py` | extend | browser DTOs | current schema owner |
| `src/mesh_lens/render.py` | extend | skill-focused HTML/JSON | current renderer |
| `src/mesh_lens/cli.py` | extend | skills/browse/open flags | current dispatcher |
| `src/mesh_lens/inventory.py` | re-audit | producer capability changes | current availability owner |
| `tests/` | extend | sparse/full/missing/provenance cases | mirrors modules |

## 5. New Components

- `mesh-lens skills list|show --json`: skill, models, invocation count, freshness, verdict mix,
  measured/unavailable metrics, outcome coverage, and source refs.
- `mesh-lens browse --open`: ingest, render the skill browser, and open the generated HTML.
- `src/mesh_lens/open_browser.py`: new tested opener seam; Windows uses PowerShell
  `Start-Process -FilePath`, reports asynchronous spawn errors through a bounded handshake, and
  never silently converts opener failure into success.
- Producer-contract re-audit that changes availability only from producing-code evidence.

## 6. Design Decisions

The skill is the first navigation layer because it matches operator intent. A report with zero
joined outcomes remains useful only if it says so prominently. Observatory invokes the package via
`uv run --project mesh-lens`, not a global console script. Browser opening reuses a tested
platform-specific helper and reports launch failure rather than silently swallowing it.

## 7. Build Steps

### Step 7: Skill list/detail aggregates
- **Problem:** Add versioned JSON summaries and details grouped by skill with model mix, recent
  events, metric availability, outcome coverage, and provenance.
- **Type:** code
- **Issue:** #
- **Flags:** --reviewers deep --isolation worktree
- **Files:** `src/mesh_lens/models.py`, `src/mesh_lens/analyze.py`, `src/mesh_lens/cli.py`,
  `tests/test_analyze.py`, `tests/test_cli.py`
- **Produces:** models, aggregate functions, CLI, tests
- **Done when:** placeholder metrics never aggregate and every displayed number cites source IDs
- **Depends on:** 5

### Step 8: Browser report and reliable open
- **Problem:** Render list/detail HTML and add `browse --open`; make ingest+render one explicit
  command while preserving the existing report/compare verbs.
- **Type:** code
- **Issue:** #
- **Flags:** --reviewers code --isolation worktree
- **Files:** `src/mesh_lens/render.py`, `src/mesh_lens/open_browser.py`,
  `src/mesh_lens/cli.py`, `tests/test_render.py`, `tests/test_open_browser.py`, `tests/test_cli.py`
- **Produces:** browser HTML, opener seam, CLI tests
- **Done when:** browser navigation works on sparse fixtures and opener failure is visible
- **Depends on:** 7

### Step 9: Wait for and re-audit producer telemetry
- **Problem:** Do not claim token/cost/outcome completeness until Skill Mesh emits real usage and a
  stable dispatch/run key; then re-run inventory against producing code and add only provable joins.
- **Type:** wait
- **Issue:** #
- **Produces:** no code while blocked
- **Done when:** `skill-mesh/documentation/provider-expansion-plan.md` (or its explicit successor)
  records a shipped telemetry schema containing measured `tokens_in`/`tokens_out` and a stable
  `dispatch_id`/run key, and producer tests assert both fields in emitted JSONL
- **Depends on:** 8

### Step 10: Integrate and observe real telemetry
- **Problem:** Update inventory/adapters if Step 9 evidence permits and add a bounded,
  versioned observatory artifact export plus fixtures. dev-observatory owns registry/UI integration.
- **Type:** code
- **Issue:** #
- **Flags:** --reviewers deep --isolation worktree
- **Files:** `src/mesh_lens/inventory.py`, `src/mesh_lens/adapters/skill_mesh.py`,
  `src/mesh_lens/observatory_export.py`, `src/mesh_lens/cli.py`,
  `tests/test_inventory.py`, `tests/test_adapter_skill_mesh.py`,
  `tests/test_observatory_export.py`, `docs/observatory-contract.md`
- **Produces:** adapter updates, observatory contract, real report
- **Done when:** one real skill detail shows honest measured/unavailable fields and no ambiguous join
- **Depends on:** 9

## 8. Risks and Open Questions

| Item | Risk | Mitigation |
|---|---|---|
| Sparse stream | pretty empty page overstates value | dominant insufficient-evidence state |
| Producer schema changes | silent cohort mixing | exact schema inference and re-audit |
| Single-writer store | concurrent ingest race | one browse owner; document/lock before wider automation |

## 9. Testing Strategy

Cover empty, stub-only, mixed-schema, measured-token, joined and unjoined outcome fixtures. The final
gate is one real producer -> ingest -> skill detail -> HTML cycle.

## Appendix: Decision Inventory

| ID | P/D | Choice | Status |
|---|---|---|---|
| P2 | P | Add skill-first telemetry list/detail navigation | accepted |
| D2 | D | Emit bounded JSON artifacts; dev-observatory owns shared HTML and registry wiring | accepted |
| D6 | D | Wait for real producer usage and stable join keys before claiming outcomes | accepted |
