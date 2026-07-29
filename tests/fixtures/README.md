# Frozen source fixtures

Every fixture here is **captured from real workspace data**, not fabricated. No
telemetry rows were invented (measurement-validity discipline). Provenance:

| Fixture | Captured from | Notes |
|---|---|---|
| `invocations.real.jsonl` | `.claude/lib/telemetry/invocations.jsonl` | Byte-identical copy (SHA-256 `25b0f1ff…54d364`, 322 bytes, CRLF). The 2 real records that existed at audit time; both `verdict=stub`, tokens/cost `0`. |
| `invocations.empty.jsonl` | (empty) | Zero-byte stream for the graceful-degradation test. |
| `git_log.sample.tsv` | `git -C mesh-lens log --format='%H\t%ai\t%s'` | Real mesh-lens commits (sha, author-date, subject). Class 3. |
| `gh_issues.sample.json` | `gh issue list --json number,title,state` in mesh-lens | Real issues #1–#12, all OPEN at audit time. Class 2. |
| `skill_iterate_results.sample.tsv` | `.claude/skills/build-phase/evals/results.tsv` (head) | Real skill-iterate run-log schema `commit\tscore\tstatus\tdescription\twall_seconds`. Class 5. |
| `build_step_report.sample.md` | header of a real `.build-step/review-tests.md` | Real reviewer-report shape (lens + step + verdict + finding counts; **no timestamp, no dispatch id**). Class 1. |

These let later steps (ingest, join attempts, reports) test against real-shaped
data. The telemetry fixture is the load-bearing one; the outcome fixtures exist
to prove — in code — that none of the five classes carries a
dispatch-correlatable key (see `docs/telemetry-inventory.md`).
