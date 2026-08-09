"""mesh-lens CLI.

Steps 1-2 expose ``inventory`` (the availability audit) and ``ingest`` (normalize
the Skill Mesh dispatch stream into the local store). Step 4 adds ``report`` (the
single-view aggregate report). Step 5 adds ``compare`` -- a guarded pairwise
comparison of two pinned cohorts that REFUSES a directional verdict whenever the
evidence cannot support one (undersized, incomparable, or placeholder-only).
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from mesh_lens.analyze import (
    Report,
    SkillDetail,
    SkillSummary,
    analyze_report,
    build_skill_detail,
    build_skill_summaries,
)
from mesh_lens.compare import (
    CohortSelectionError,
    Comparison,
    compare_cohorts,
    parse_selector,
)
from mesh_lens.correlate import CorrelationResult, correlate, dispatch_ref
from mesh_lens.inventory import (
    DEFAULT_TELEMETRY_RELPATH,
    PRODUCER_SCHEMA_ID,
    Availability,
    audit_telemetry_stream,
    build_inventory,
)
from mesh_lens.models import NormalizedInvocation
from mesh_lens.open_browser import open_local_file
from mesh_lens.render import write_comparison, write_report, write_skill_browser
from mesh_lens.store import Store


def _project_root() -> Path:
    """The mesh-lens project root (src/mesh_lens/cli.py -> parents[2])."""
    return Path(__file__).resolve().parents[2]


def default_telemetry_source() -> Path:
    """Well-known Skill Mesh stream: ``<dev-root>/.claude/lib/telemetry/invocations.jsonl``.

    The dev workspace root is the project root's parent in both the real layout
    (``dev/mesh-lens``) and a build worktree (``dev/worktree_*``).
    """
    return _project_root().parent / DEFAULT_TELEMETRY_RELPATH


def default_store_dir() -> Path:
    """Default local normalized-store directory (kept out of git; plan sec. 10)."""
    return _project_root() / ".mesh-lens"


def default_report_dir() -> Path:
    """Default output directory for the rendered report (kept out of git; plan sec. 10)."""
    return default_store_dir() / "report"


def default_browser_dir() -> Path:
    """Default output directory for the static skill list/detail browser."""
    return default_store_dir() / "browser"


def _print_inventory(telemetry_path: Path | None) -> int:
    inv = build_inventory()

    print("== Producer fields (Skill Mesh dispatch record) ==")
    for f in inv.producer_fields:
        signal = "" if f.value_signal.value == "n/a" else f"  [value: {f.value_signal.value}]"
        print(f"  {f.name:<12} {f.availability.value}{signal}")

    print("\n== Correlation-key candidates ==")
    for k in inv.correlation_keys:
        print(f"  {k.name:<24} {k.availability.value}  (join: {k.join_strength.value})")

    print("\n== Cohort dimensions ==")
    for d in inv.cohort_dimensions:
        print(f"  {d.name:<16} {d.availability.value}")

    print("\n== Outcome-artifact classes (join to a dispatch row) ==")
    for a in inv.outcome_artifacts:
        exists = "exists" if a.exists else "absent"
        print(
            f"  [{exists:>6}] {a.availability.value:<9} (join: {a.join_strength.value})  {a.name}"
        )

    strong = [a for a in inv.outcome_artifacts if a.join_strength.value == "strong-key"]
    print(
        f"\n  Strong dispatch-correlatable keys: {len(strong)} of "
        f"{len(inv.outcome_artifacts)} -- all outcome classes stay unjoined (plan sec. 6)."
    )

    if telemetry_path is not None:
        stream = audit_telemetry_stream(telemetry_path)
        print("\n== Live telemetry stream ==")
        print(f"  path:    {stream.path}")
        print(f"  records: {stream.record_count}  malformed: {stream.malformed_count}")
        print(f"  matches pinned contract: {stream.matches_pinned_contract}")
        print(f"  note: {stream.note}")

    unavailable = [f.name for f in inv.producer_fields if f.value_signal.value == "always-zero"]
    absent_dims = [d.name for d in inv.cohort_dimensions if d.availability is Availability.ABSENT]
    print("\n== Honesty summary ==")
    print(f"  fields present but always-zero (no signal today): {', '.join(unavailable)}")
    print(f"  cohort dimensions absent from producer:           {', '.join(absent_dims)}")
    print("  no producer modification is made by this plan (V1 infers producer schema).")
    return 0


def _run_ingest(source: Path | None, store_dir: Path) -> int:
    src = source if source is not None else default_telemetry_source()
    report = Store(store_dir).ingest_source(src)

    present = "present" if report.source_present else "absent (graceful; zero records)"
    print("== mesh-lens ingest ==")
    print(f"  source: {src}  [{present}]")
    print(f"  store:  {store_dir}")
    schema_split = ", ".join(f"{k}={v}" for k, v in sorted(report.by_producer_schema.items()))
    print(
        f"  ingested this run: {report.ingested}" + (f"  ({schema_split})" if schema_split else "")
    )
    print(f"  skipped blank lines: {report.skipped_blank}")
    print(f"  malformed rows (diagnosed, not aborted): {len(report.malformed)}")
    for diag in report.malformed:
        print(f"    - {diag.provenance_ref}: {diag.reason}")
    if report.rebuilt:
        print("  NOTE: overlap mismatch (rotation/truncation) -> derived store rebuilt")
    print(f"  total events in store: {report.total_events}")
    print(
        "  cohorts stay separate: 'unknown' records are reported incomparable, "
        "never merged with 'skillmesh-v1' (plan sec. 6)."
    )
    return 0


def _correlate_events(events: Sequence[NormalizedInvocation]) -> CorrelationResult:
    """Correlate today's dispatch stream without inventing unavailable outcomes."""
    inventory = build_inventory()
    dispatches = [
        dispatch_ref(event, inventory)
        for event in events
        if getattr(event, "producer_schema", None) == PRODUCER_SCHEMA_ID
    ]
    return correlate(dispatches, [], inventory)


def _build_report(store_dir: Path) -> Report:
    """Aggregate the store's events into a :class:`Report` (shared by report + compare).

    Outcomes are UNJOINED on real data (Step-3 inventory): the store's dispatches are
    correlated against an empty outcome set so the report states outcomes are unjoined
    and attaches NONE to a dispatch (never a fabricated outcome/retry).
    """
    events = Store(store_dir).read_events()
    return analyze_report(events, _correlate_events(events))


def _run_report(store_dir: Path, out_dir: Path, fmt: str) -> int:
    report = _build_report(store_dir)
    written = write_report(report, out_dir, fmt)

    print("== mesh-lens report ==")
    print(f"  store: {store_dir}")
    print(
        f"  events: {report.total_events} total  "
        f"({report.comparable_event_count} comparable skillmesh-v1, "
        f"{report.incomparable_event_count} incomparable unknown)"
    )
    print(
        f"  cohorts: {len(report.comparable_cohorts)} comparable, "
        f"{len(report.incomparable_cohorts)} incomparable (unknown never merged)"
    )
    print(
        "  tokens_in/tokens_out/cost_usd: unavailable for skillmesh-v1 "
        "(placeholder; never a fabricated 0). latency_ms: real measured aggregate."
    )
    print(
        f"  outcomes: {'UNJOINED' if report.outcomes.all_unjoined else 'partially joined'} "
        "-- not attached to any dispatch; no outcome/retry rate computed."
    )
    for path in written:
        print(f"  wrote: {path}")
    return 0


def _print_metric_summary(summary: SkillSummary) -> None:
    """Print model/schema strata with the refs behind every count and aggregate."""
    for group in summary.model_mix:
        model = group.model if group.model is not None else "(unavailable)"
        print(
            f"    model={model} schema={group.producer_schema} v{group.schema_version} "
            f"N={group.invocation_count}  [refs: {', '.join(group.record_refs)}]"
        )
        for metric_name, aggregate in (
            ("latency_ms", group.latency),
            ("tokens_in", group.tokens_in),
            ("tokens_out", group.tokens_out),
            ("cost_usd", group.cost_usd),
        ):
            measured_refs = ", ".join(aggregate.measured_refs) or "none"
            print(
                f"      {metric_name}: {aggregate.summary()} "
                f"[measured refs: {measured_refs}]"
            )


def _print_skill_list(summaries: Sequence[SkillSummary]) -> None:
    print("== mesh-lens skills ==")
    if not summaries:
        print("  no skill events in the store")
        return
    for summary in summaries:
        print(
            f"  {summary.skill}: N={summary.invocation_count} "
            f"[refs: {', '.join(summary.record_refs)}]"
        )
        _print_metric_summary(summary)


def _print_skill_detail(detail: SkillDetail) -> None:
    summary = detail.summary
    print(f"== mesh-lens skill: {summary.skill} ==")
    print(
        f"  invocations: N={summary.invocation_count} "
        f"[refs: {', '.join(summary.record_refs)}]"
    )
    _print_metric_summary(summary)
    print("  recent events (newest first):")
    for event in detail.recent_events:
        event_json = event.to_json()
        print(
            f"    {event_json['source_ref']}: timestamp={event_json['timestamp']} "
            f"model={event_json['model']} verdict={event_json['verdict']} "
            f"latency={event_json['latency_ms']['raw_value']} "
            f"({event_json['latency_ms']['status']})"
        )
    coverage = detail.outcome_coverage
    if coverage.joined_count is None:
        print(f"  outcome coverage: unavailable -- {coverage.note}")
    else:
        print(f"  outcome coverage: {coverage.joined_count} provable join(s)")
        for outcome_class, dispatch_ref_, outcome_ref in coverage.joins:
            print(f"    {outcome_class}: {dispatch_ref_} -> {outcome_ref}")


def _print_json(payload: object) -> None:
    """Print deterministic versioned JSON for tooling without executing any source data."""
    print(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True))


def _run_skills_list(store_dir: Path, json_output: bool) -> int:
    summaries = build_skill_summaries(Store(store_dir).read_events())
    if json_output:
        _print_json(
            {
                "schema_version": 1,
                "skills": [summary.to_json() for summary in summaries],
            }
        )
    else:
        _print_skill_list(summaries)
    return 0


def _run_skills_show(store_dir: Path, skill: str, json_output: bool) -> int:
    events = Store(store_dir).read_events()
    detail = build_skill_detail(events, skill, _correlate_events(events))
    if detail is None:
        print(f"skill error: no stored events for {skill!r}")
        return 2
    if json_output:
        _print_json(detail.to_json())
    else:
        _print_skill_detail(detail)
    return 0


def _run_browse(
    source: Path | None,
    store_dir: Path,
    out_dir: Path,
    fmt: str,
    open_after_render: bool,
) -> int:
    """Explicitly ingest, render the skill browser, and optionally open its HTML artifact."""
    if open_after_render and fmt == "json":
        print("browse error: --open requires HTML output; use --format html or both")
        return 2

    # Deliberately reuse the ingest command path so checkpointing, malformed-row
    # diagnostics, and schema separation remain identical to an explicit ingest.
    _run_ingest(source, store_dir)
    events = Store(store_dir).read_events()
    correlation = _correlate_events(events)
    details: list[SkillDetail] = []
    for summary in build_skill_summaries(events):
        detail = build_skill_detail(events, summary.skill, correlation)
        if detail is not None:  # summary came from these events; protects the CLI seam.
            details.append(detail)
    written = write_skill_browser(details, out_dir, fmt)

    print("== mesh-lens browse ==")
    print(f"  store: {store_dir}")
    print(f"  skills: {len(details)} (list/detail browser; sparse data stays visible)")
    for path in written:
        print(f"  wrote: {path}")

    if not open_after_render:
        return 0

    result = open_local_file(out_dir / "browser.html")
    if result.opened:
        print(f"  opened: {result.uri}")
        return 0
    print(f"browse error: could not open {result.uri}: {result.message}")
    return 2


def _print_comparison(comparison: Comparison) -> None:
    print("== mesh-lens compare (guarded pairwise) ==")
    a, b = comparison.cohort_a, comparison.cohort_b
    print(
        f"  cohort A: skill={a.key.skill} model={a.key.model} "
        f"schema={a.key.producer_schema} v{a.key.schema_version}  N={a.count}"
    )
    print(
        f"  cohort B: skill={b.key.skill} model={b.key.model} "
        f"schema={b.key.producer_schema} v{b.key.schema_version}  N={b.count}"
    )
    print(
        f"  pre-declared decision metric: {comparison.decision_metric} (min N={comparison.min_n})"
    )
    print(f"  contrast dimension: {comparison.contrast_dimension or '(none)'}")

    if comparison.refused:
        print("  VERDICT: REFUSED -- no directional verdict, no winner computed.")
        for reason in comparison.refusals:
            print(f"    - {reason}")
    else:
        print(f"  VERDICT: {comparison.directional_verdict}")

    print("  per-metric disclosure:")
    for m in comparison.metrics:
        tag = " [decision]" if m.is_decision_metric else ""
        if m.comparable and m.direction is not None:
            print(f"    {m.metric}{tag}: delta A-B={m.delta}  ({m.direction})")
        else:
            print(f"    {m.metric}{tag}: no verdict -- {'; '.join(m.refusal_reasons)}")

    print("  caveats:")
    for caveat in comparison.caveats:
        print(f"    - {caveat}")


def _run_compare(
    store_dir: Path,
    selector_a: str,
    selector_b: str,
    decision_metric: str,
    out_dir: Path | None,
    fmt: str,
) -> int:
    report = _build_report(store_dir)
    try:
        sel_a = parse_selector(selector_a)
        sel_b = parse_selector(selector_b)
        comparison = compare_cohorts(report, sel_a, sel_b, decision_metric=decision_metric)
    except (ValueError, CohortSelectionError) as exc:
        print(f"compare error: {exc}")
        return 2

    _print_comparison(comparison)

    if out_dir is not None:
        for path in write_comparison(comparison, out_dir, fmt):
            print(f"  wrote: {path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mesh-lens", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    inv = sub.add_parser("inventory", help="print the telemetry/outcome availability audit")
    inv.add_argument(
        "--telemetry",
        type=Path,
        default=None,
        help=(
            "path to a telemetry JSONL stream to audit live "
            f"(default: none; the real stream is {DEFAULT_TELEMETRY_RELPATH})"
        ),
    )

    ing = sub.add_parser(
        "ingest", help="normalize the Skill Mesh dispatch stream into the local store"
    )
    ing.add_argument(
        "--source",
        type=Path,
        default=None,
        help=(
            "explicit telemetry JSONL source (default: the well-known stream "
            f"<dev-root>/{DEFAULT_TELEMETRY_RELPATH})"
        ),
    )
    ing.add_argument(
        "--store",
        type=Path,
        default=None,
        help="local normalized-store directory (default: <project-root>/.mesh-lens)",
    )

    rep = sub.add_parser(
        "report", help="render the single-view aggregate report (static JSON + HTML)"
    )
    rep.add_argument(
        "--store",
        type=Path,
        default=None,
        help="local normalized-store directory to read events from (default: <root>/.mesh-lens)",
    )
    rep.add_argument(
        "--out",
        type=Path,
        default=None,
        help="output directory for report.json/report.html (default: <root>/.mesh-lens/report)",
    )
    rep.add_argument(
        "--format",
        choices=("json", "html", "both"),
        default="both",
        help="which report artifacts to write (default: both)",
    )

    cmp_ = sub.add_parser(
        "compare",
        help="guarded pairwise comparison of two pinned cohorts (refuses unsupportable verdicts)",
    )
    cmp_.add_argument(
        "--store",
        type=Path,
        default=None,
        help="local normalized-store directory to read events from (default: <root>/.mesh-lens)",
    )
    cmp_.add_argument(
        "--a",
        required=True,
        metavar="KEY=VAL,...",
        help="cohort A selector, e.g. 'skill=repo-sync,model=claude' (stratification key)",
    )
    cmp_.add_argument(
        "--b",
        required=True,
        metavar="KEY=VAL,...",
        help="cohort B selector, e.g. 'skill=repo-sync,model=gpt-5.5' (stratification key)",
    )
    cmp_.add_argument(
        "--metric",
        default="latency_ms",
        choices=("latency_ms", "tokens_in", "tokens_out", "cost_usd"),
        help=(
            "pre-declared decision metric, defined before looking (default: latency_ms -- "
            "the only metric measured for skillmesh-v1; token/cost are placeholder)"
        ),
    )
    cmp_.add_argument(
        "--out",
        type=Path,
        default=None,
        help="optional output directory for comparison.json/comparison.html (default: none)",
    )
    cmp_.add_argument(
        "--format",
        choices=("json", "html", "both"),
        default="both",
        help="which comparison artifacts to write when --out is given (default: both)",
    )

    skills = sub.add_parser(
        "skills",
        help="list skill aggregates or show a provenance-backed skill detail",
    )
    skills_sub = skills.add_subparsers(dest="skills_command", required=True)
    skills_list = skills_sub.add_parser("list", help="list per-skill model/schema aggregates")
    skills_list.add_argument(
        "--store",
        type=Path,
        default=None,
        help="local normalized-store directory (default: <root>/.mesh-lens)",
    )
    skills_list.add_argument(
        "--json",
        action="store_true",
        help="write the versioned JSON list to stdout",
    )
    skills_show = skills_sub.add_parser(
        "show",
        help="show bounded recent events and honest outcome coverage for one skill",
    )
    skills_show.add_argument("skill", help="exact skill identifier shown by 'skills list'")
    skills_show.add_argument(
        "--store",
        type=Path,
        default=None,
        help="local normalized-store directory (default: <root>/.mesh-lens)",
    )
    skills_show.add_argument(
        "--json",
        action="store_true",
        help="write the versioned JSON detail to stdout",
    )

    browse = sub.add_parser(
        "browse",
        help="ingest then render the static skill list/detail browser",
    )
    browse.add_argument(
        "--source",
        type=Path,
        default=None,
        help=(
            "telemetry JSONL source to ingest before rendering "
            f"(default: <dev-root>/{DEFAULT_TELEMETRY_RELPATH})"
        ),
    )
    browse.add_argument(
        "--store",
        type=Path,
        default=None,
        help="local normalized-store directory (default: <root>/.mesh-lens)",
    )
    browse.add_argument(
        "--out",
        type=Path,
        default=None,
        help="output directory for browser.json/browser.html (default: <root>/.mesh-lens/browser)",
    )
    browse.add_argument(
        "--format",
        choices=("json", "html", "both"),
        default="both",
        help="which browser artifacts to write (default: both)",
    )
    browse.add_argument(
        "--open",
        action="store_true",
        help="open browser.html through the platform browser after it is rendered",
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "inventory":
        return _print_inventory(args.telemetry)

    if args.command == "ingest":
        store_dir = args.store if args.store is not None else default_store_dir()
        return _run_ingest(args.source, store_dir)

    if args.command == "report":
        store_dir = args.store if args.store is not None else default_store_dir()
        out_dir = args.out if args.out is not None else default_report_dir()
        return _run_report(store_dir, out_dir, args.format)

    if args.command == "compare":
        store_dir = args.store if args.store is not None else default_store_dir()
        return _run_compare(store_dir, args.a, args.b, args.metric, args.out, args.format)

    if args.command == "skills":
        store_dir = args.store if args.store is not None else default_store_dir()
        if args.skills_command == "list":
            return _run_skills_list(store_dir, args.json)
        if args.skills_command == "show":
            return _run_skills_show(store_dir, args.skill, args.json)

    if args.command == "browse":
        store_dir = args.store if args.store is not None else default_store_dir()
        out_dir = args.out if args.out is not None else default_browser_dir()
        return _run_browse(args.source, store_dir, out_dir, args.format, args.open)

    parser.error(f"unknown command: {args.command}")  # pragma: no cover - argparse guards this
    return 2  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
