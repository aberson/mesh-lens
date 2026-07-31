# Telemetry & outcome availability inventory (Step 1)

This is the honest availability audit mesh-lens Step 1 requires. Every classification
below was verified against a **primary source** — the producing code and the real
telemetry stream — not against the schema doc alone.

**No producer modification is made by this plan.** mesh-lens only *reads*. V1 infers the
producer schema by field-set equality (plan §6); it never edits the Skill Mesh router,
the telemetry writer, or `invocations.jsonl`. A native `schema_version` field on the
producer is a parked, separately-reviewed cross-repo ask (plan §2).

## Sources verified

| Source | Path | What it proved |
|---|---|---|
| Record writer | the Skill Mesh telemetry writer | The exact 8-field record shape; stub zeroing of tokens/cost. |
| Dispatch code | the Skill Mesh router | Every telemetry write hardcodes `TokensIn 0 / TokensOut 0 / CostUsd 0.0`; a session identifier exists in the router runtime but is never written into the JSONL. |
| Real telemetry stream | `.claude/lib/telemetry/invocations.jsonl` | 2 records at audit time, both `verdict=stub`, both with `tokens_in/out=0`, `cost_usd=0`, nonzero `latency_ms`. Frozen byte-identical at `tests/fixtures/invocations.real.jsonl`. |
| Producer format contract | the Skill Mesh telemetry-schema | Field names/types match reality (with the divergence noted below). |

## Reality vs. the pinned contract

The real records' field set matches the pinned 8 fields **exactly**:

```json
{"timestamp":"2026-07-24T15:13:27.1397224Z","skill":"plan-init","model":"gpt-5.6-sol","tokens_in":0,"tokens_out":0,"latency_ms":1730,"cost_usd":0,"verdict":"stub"}
{"timestamp":"2026-07-24T15:13:44.7766510Z","skill":"plan-init","model":"claude","tokens_in":0,"tokens_out":0,"latency_ms":4,"cost_usd":0,"verdict":"stub"}
```

**One divergence from the doc, verified from producing code.** The schema doc says tokens/cost
are zero "when unavailable or in stub mode." Reality is stronger: the current router passes
`0` for tokens **and** cost on *every* telemetry write path (pass, fail, and stub alike). So
tokens/cost carry **no signal at all** from today's producer, independent of verdict. This is
recorded honestly below rather than presented as a merely-stub limitation.

## Producer fields (the 8-field dispatch record)

| Field | Availability | Value signal today | Verified source |
|---|---|---|---|
| `timestamp` | PRESENT | real (per-record UTC) | the writer; both real records |
| `skill` | PRESENT | real | the writer; `plan-init` in both |
| `model` | PRESENT | real | the writer; `gpt-5.6-sol`, `claude` |
| `tokens_in` | PRESENT | **always zero — no signal** | the writer + router hardcodes 0; both real records 0 |
| `tokens_out` | PRESENT | **always zero — no signal** | the writer + router hardcodes 0; both real records 0 |
| `latency_ms` | PRESENT | real (Stopwatch) | the writer; 1730 and 4 in real records |
| `cost_usd` | PRESENT | **always zero — no signal** | the writer + router hardcodes 0; both real records 0 |
| `verdict` | PRESENT | real (`pass`/`fail`/`stub`) | the writer; both real records `stub` |

## Correlation-key candidates

| Candidate | Availability | Join strength | Verified source |
|---|---|---|---|
| run/session/record id | **ABSENT** | none | the writer emits no id; the router's session identifier is never persisted into the JSONL; neither real record carries an id. |
| timestamp window | AMBIGUOUS | timestamp-window-only | only per-record time key; dispatches can share a second and stub records are byte-identical (§6) → cannot uniquely attribute. |
| skill name | AMBIGUOUS | skill-name-only | present but non-unique (both real records = `plan-init`); a cohort key, not a row join. |

**The pinned 8-field contract carries no run/session key, so all outcome joins are
timestamp- or skill-name-based, which are ambiguous and stay unjoined (plan §6).**

## Cohort dimensions (reports stratify by these — plan §6)

| Dimension | Availability | Verified source |
|---|---|---|
| `skill` | PRESENT | = producer field |
| `model` | PRESENT | = producer field |
| `project` | **ABSENT** | no field; not derivable from the 8 fields |
| `task_type` | **ABSENT** | no field; not derivable from the 8 fields |
| `producer_schema` | DERIVABLE | field-set equality → `skillmesh-v1`, else `unknown` (never merged) |
| `schema_version` | DERIVABLE | assigned by the mesh-lens store (int `1`), not by the producer |

## Outcome-artifact classes (the 5 candidates — plan §2)

All five **exist** on-workspace (verified via filesystem/CLI), but the dispatch row exposes
only `skill` and `timestamp` as possible join columns and carries no id — so **none has a
strong dispatch-correlatable key, and all five stay unjoined.**

| # | Class | Exists | Record key | Join to dispatch | Availability |
|---|---|---|---|---|---|
| 1 | developer/reviewer report files | yes (per-project report dirs) | review-**lens** name + step number; no in-file timestamp/dispatch id (only mtime) | skill-name-only (fuzzy role→skill) | AMBIGUOUS |
| 2 | GitHub issue states | yes (mesh-lens #1–#12 OPEN) | issue number + state; title embeds "Step N", not a skill | none | ABSENT |
| 3 | `git log` of target repo | yes (mesh-lens: 3 commits) | commit sha + author-date + message (message *sometimes* prefixes the producing skill) | timestamp-window-only | AMBIGUOUS |
| 4 | Plan `**Status:** DONE` / `### Step N:` markers | yes (6 `### Step N:` headers; **zero real `**Status:** DONE`** markers — the only DONE string is meta-prose) | step number (+ Issue #) | none | ABSENT |
| 5 | skill-evaluation run-logs | yes (per-skill eval-result TSVs, 32 non-empty) | TSV `commit\tscore\tstatus\tdescription\twall_seconds`; key is a git **commit sha**; skill name implicit in dir path; no timestamp column | skill-name-only (via dir) | AMBIGUOUS |

Split: **3 ambiguous, 2 absent, 0 strong keys.** "Most classes lack a dispatch-correlatable
key and stay unjoined" (plan §2) is the verified, expected outcome — reported, not papered over.

## Proposed metrics → verified source or "unavailable"

| Proposed metric | Status | Source / reason |
|---|---|---|
| dispatch count (by skill/model) | **AVAILABLE** | count of records; `skill`, `model` present |
| latency (avg / p50 / p95) | **AVAILABLE** | `latency_ms` real, even in stub-only stream |
| verdict rate (pass / fail / stub) | **AVAILABLE** | `verdict` real; both real records `stub` |
| input/output tokens per dispatch | **UNAVAILABLE** | `tokens_in/out` present but always 0 in current producer — no signal |
| cost per dispatch (USD) | **UNAVAILABLE** | `cost_usd` present but always 0 in current producer — no signal |
| by-project / by-task_type cohorts | **UNAVAILABLE** | `project` / `task_type` absent from producer; not derivable |
| outcome join (tests / reviews / retries / final disposition) | **UNAVAILABLE (unjoined)** | no run/session key; all 5 outcome classes are skill-name- or timestamp-only → ambiguous, stay unjoined (§6) |
| `producer_schema` cohort tag | **DERIVABLE** | field-set equality vs. the pinned 8 |

## Consequences for later steps

- **Step 2** can normalize the real 8-field stream and compute count / latency / verdict
  metrics honestly; it must keep tokens/cost distinguishable as *present-but-zero*, never
  fill success-shaped defaults.
- **Step 3** builds outcome adapters only for classes with a provable join key. Per this
  inventory that set is **empty** today — the correct, expected result is that outcome
  artifacts are cataloged but stay unjoined until a native run/session key exists.
- Frozen fixtures for all verified classes live under `tests/fixtures/` (see its README for
  provenance); the telemetry fixture is byte-identical to the real stream and was **not**
  fabricated.
