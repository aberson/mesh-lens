"""mesh-lens CLI.

Steps 1-2 expose ``inventory`` (the availability audit) and ``ingest`` (normalize
the Skill Mesh dispatch stream into the local store). ``report`` and ``compare``
land in later steps (plan sec. 7) and are declared here as explicit "not yet
built" stubs so the surface is honest rather than silently missing.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from mesh_lens.inventory import (
    DEFAULT_TELEMETRY_RELPATH,
    Availability,
    audit_telemetry_stream,
    build_inventory,
)
from mesh_lens.store import Store

_NOT_YET_BUILT = {"report", "compare"}


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

    for name in sorted(_NOT_YET_BUILT):
        sub.add_parser(name, help="(not yet built -- lands in a later plan step)")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "inventory":
        return _print_inventory(args.telemetry)

    if args.command == "ingest":
        store_dir = args.store if args.store is not None else default_store_dir()
        return _run_ingest(args.source, store_dir)

    if args.command in _NOT_YET_BUILT:
        parser.error(
            f"'{args.command}' is not built yet (plan sec. 7); only 'inventory' is available"
        )

    parser.error(f"unknown command: {args.command}")  # pragma: no cover - argparse guards this
    return 2  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
