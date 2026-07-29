"""mesh-lens CLI.

Step 1 exposes only ``inventory`` -- the availability audit. ``ingest``,
``report``, and ``compare`` land in later steps (plan sec. 7) and are declared
here as explicit "not yet built" stubs so the surface is honest rather than
silently missing.
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

_NOT_YET_BUILT = {"ingest", "report", "compare"}


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

    for name in sorted(_NOT_YET_BUILT):
        sub.add_parser(name, help="(not yet built -- lands in a later plan step)")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "inventory":
        return _print_inventory(args.telemetry)

    if args.command in _NOT_YET_BUILT:
        parser.error(
            f"'{args.command}' is not built yet (plan sec. 7); only 'inventory' is available"
        )

    parser.error(f"unknown command: {args.command}")  # pragma: no cover - argparse guards this
    return 2  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
