"""Static JSON + HTML renderers for the mesh-lens aggregate report (plan sec. 7 Step 4).

Both renderers are DETERMINISTIC -- the same :class:`~mesh_lens.analyze.Report`
produces byte-identical output every run (stable key ordering, sorted JSON keys, no
timestamps or environment state). A golden test pins this (plan sec. 6 "byte-identical").

The JSON report carries integer ``schema_version = 1`` at the top level and, for
every displayed number, the source record refs that produced it (a reader can walk
from any aggregate to its contributing ``<source-relpath>@<line>`` IDs -- plan sec. 7
done-when). The HTML report is a self-contained static page for human reading; it
labels a placeholder/absent metric "unavailable" (never a fabricated 0) and shows
sample size + missingness per cohort. Only stdlib ``json`` + ``html`` are used (no
templating dependency, plan sec. 10).
"""

from __future__ import annotations

import html
import json
from pathlib import Path

from mesh_lens.analyze import Cohort, MetricAggregate, Report
from mesh_lens.compare import Comparison, MetricComparison


def render_json(report: Report) -> str:
    """Serialize the report to deterministic, sorted JSON (schema_version=1 at top)."""
    return json.dumps(report.to_json(), ensure_ascii=True, indent=2, sort_keys=True) + "\n"


# --------------------------------------------------------------------------- #
# HTML -- a self-contained static page. Deterministic; escapes every value.
# --------------------------------------------------------------------------- #

_STYLE = """
:root { color-scheme: light dark; }
body { font-family: system-ui, sans-serif; margin: 2rem; line-height: 1.45; }
h1 { margin-bottom: 0.2rem; }
.sub { color: #666; margin-top: 0; }
table { border-collapse: collapse; margin: 0.5rem 0 1.5rem; width: 100%; }
th, td { border: 1px solid #bbb; padding: 0.3rem 0.5rem; text-align: left;
         vertical-align: top; font-size: 0.9rem; }
th { background: rgba(127,127,127,0.15); }
.cohort { margin-bottom: 2rem; }
.key { font-weight: 600; }
.measured { color: #157347; }
.unavailable { color: #b02a37; }
.refs { font-family: ui-monospace, monospace; font-size: 0.8rem; color: #555;
        word-break: break-all; }
.banner { border: 1px solid #bbb; padding: 0.6rem 0.9rem; margin: 1rem 0;
          background: rgba(127,127,127,0.08); }
.tag { display: inline-block; padding: 0 0.4rem; border-radius: 0.3rem;
       font-size: 0.8rem; background: rgba(127,127,127,0.2); }
"""


def _esc(value: object) -> str:
    return html.escape(str(value))


def _refs_cell(refs: tuple[str, ...]) -> str:
    if not refs:
        return '<span class="refs">(none)</span>'
    return '<span class="refs">' + _esc(", ".join(refs)) + "</span>"


def _metric_row(label: str, agg: MetricAggregate) -> str:
    cls = "measured" if agg.status == "measured" else "unavailable"
    return (
        "<tr>"
        f"<th>{_esc(label)}</th>"
        f'<td class="{cls}">{_esc(agg.summary())}</td>'
        f"<td>{_refs_cell(agg.measured_refs)}</td>"
        "</tr>"
    )


def _verdict_rows(cohort: Cohort) -> str:
    rows: list[str] = []
    for verdict, refs in cohort.verdicts.entries:
        rows.append(
            "<tr>"
            f"<th>verdict: {_esc(verdict)}</th>"
            f"<td>{len(refs)}</td>"
            f"<td>{_refs_cell(refs)}</td>"
            "</tr>"
        )
    return "".join(rows)


def _cohort_section(cohort: Cohort) -> str:
    key = cohort.key
    comparable = "comparable" if key.is_comparable else "incomparable"
    heading = (
        f"skill={_esc(key.skill)} &middot; model={_esc(key.model)} &middot; "
        f"schema={_esc(key.producer_schema)} v{_esc(key.schema_version)}"
    )
    parts = [
        '<div class="cohort">',
        f'<p class="key">{heading} '
        f'<span class="tag">{_esc(comparable)}</span> '
        f'<span class="tag">N={cohort.count}</span></p>',
        "<table>",
        "<tr><th>metric</th><th>value</th><th>source record ids</th></tr>",
        f"<tr><th>count</th><td>{cohort.count}</td><td>{_refs_cell(cohort.record_refs)}</td></tr>",
        _metric_row("latency_ms", cohort.latency),
        _metric_row("tokens_in", cohort.tokens_in),
        _metric_row("tokens_out", cohort.tokens_out),
        _metric_row("cost_usd", cohort.cost_usd),
        _verdict_rows(cohort),
        "</table>",
        "</div>",
    ]
    return "".join(parts)


def _outcome_section(report: Report) -> str:
    outcomes = report.outcomes
    rows = "".join(
        "<tr>"
        f"<td>{_esc(cls)}</td><td>{_esc(status)}</td>"
        f"<td>{outcomes_count}</td><td>{joined}</td>"
        "</tr>"
        for cls, status, outcomes_count, joined in outcomes.per_class
    )
    table = (
        "<table><tr><th>outcome class</th><th>join status</th>"
        "<th>outcome records</th><th>joined</th></tr>"
        f"{rows}</table>"
        if outcomes.per_class
        else ""
    )
    return f'<h2>Outcomes &amp; retries</h2><div class="banner">{_esc(outcomes.note)}</div>{table}'


def render_html(report: Report) -> str:
    """Render a self-contained, deterministic static HTML report."""
    absent = ", ".join(report.absent_dimensions) or "(none)"
    placeholder = ", ".join(report.placeholder_fields) or "(none)"

    comparable_sections = (
        "".join(_cohort_section(c) for c in report.comparable_cohorts)
        or "<p>(no comparable skillmesh-v1 records)</p>"
    )
    incomparable_sections = (
        "".join(_cohort_section(c) for c in report.incomparable_cohorts)
        or "<p>(no incomparable unknown-schema records)</p>"
    )

    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        "<title>mesh-lens aggregate report</title>"
        f"<style>{_STYLE}</style></head><body>"
        "<h1>mesh-lens aggregate report</h1>"
        '<p class="sub">Single-view aggregation by comparable cohort. '
        "Every displayed number resolves to its source record ids. "
        'Placeholder/absent metrics show <span class="unavailable">unavailable</span>, '
        "never a fabricated 0.</p>"
        '<div class="banner">'
        f"<strong>Events:</strong> {report.total_events} total &middot; "
        f"{report.comparable_event_count} comparable (skillmesh-v1) &middot; "
        f"{report.incomparable_event_count} incomparable (unknown)<br>"
        f"<strong>Never measured (placeholder) fields:</strong> {_esc(placeholder)} "
        "&mdash; reported unavailable, never summed or averaged.<br>"
        f"<strong>Absent cohort dimensions:</strong> {_esc(absent)} "
        "&mdash; cohorts cannot stratify by these (not in the producer contract)."
        "</div>"
        "<h2>Comparable cohorts (skillmesh-v1)</h2>"
        f"{comparable_sections}"
        "<h2>Incomparable cohorts (unknown schema &mdash; never merged)</h2>"
        f"{incomparable_sections}"
        f"{_outcome_section(report)}"
        "</body></html>\n"
    )


# --------------------------------------------------------------------------- #
# Comparison renderers (plan sec. 7 Step 5) -- deterministic; refusal is loud.
# --------------------------------------------------------------------------- #


def render_comparison_json(comparison: Comparison) -> str:
    """Serialize a guarded comparison to deterministic, sorted JSON (schema_version top)."""
    return json.dumps(comparison.to_json(), ensure_ascii=True, indent=2, sort_keys=True) + "\n"


def _cohort_facts_row(
    label: str, key_skill: str | None, key_model: str | None, schema: str, version: int, count: int
) -> str:
    return (
        "<tr>"
        f"<th>cohort {_esc(label)}</th>"
        f"<td>skill={_esc(key_skill)} &middot; model={_esc(key_model)} &middot; "
        f"schema={_esc(schema)} v{_esc(version)}</td>"
        f"<td>N={count}</td>"
        "</tr>"
    )


def _metric_comparison_row(mc: MetricComparison) -> str:
    if mc.comparable and mc.direction is not None:
        cls = "measured"
        verdict = _esc(mc.direction)
    else:
        cls = "unavailable"
        verdict = "REFUSED &mdash; " + _esc("; ".join(mc.refusal_reasons))
    decision_tag = ' <span class="tag">decision</span>' if mc.is_decision_metric else ""
    a_cell = (
        f"A: {mc.a_status} ({mc.a_measured_count}/{mc.a_total} measured, mean={_esc(mc.a_mean)})"
    )
    b_cell = (
        f"B: {mc.b_status} ({mc.b_measured_count}/{mc.b_total} measured, mean={_esc(mc.b_mean)})"
    )
    return (
        "<tr>"
        f"<th>{_esc(mc.metric)}{decision_tag}</th>"
        f"<td>{a_cell}<br>{b_cell}</td>"
        f'<td class="{cls}">{verdict}</td>'
        "</tr>"
    )


def render_comparison_html(comparison: Comparison) -> str:
    """Render a self-contained, deterministic static HTML comparison page.

    A refused comparison shows a loud red REFUSED banner and NO winner; a valid delta is
    shown only alongside its correlation-not-causation caveats.
    """
    a = comparison.cohort_a
    b = comparison.cohort_b

    if comparison.refused:
        verdict_banner = (
            '<div class="banner"><strong class="unavailable">DIRECTIONAL VERDICT REFUSED.</strong> '
            "No winner is computed. Reasons:</div>"
            "<ul>" + "".join(f"<li>{_esc(r)}</li>" for r in comparison.refusals) + "</ul>"
        )
    else:
        verdict = comparison.directional_verdict or ""
        verdict_banner = (
            '<div class="banner"><strong class="measured">Directional read '
            f"(decision metric {_esc(comparison.decision_metric)}):</strong> {_esc(verdict)}</div>"
        )

    cohort_table = (
        "<table><tr><th>cohort</th><th>stratum</th><th>sample</th></tr>"
        + _cohort_facts_row(
            "A", a.key.skill, a.key.model, a.key.producer_schema, a.key.schema_version, a.count
        )
        + _cohort_facts_row(
            "B", b.key.skill, b.key.model, b.key.producer_schema, b.key.schema_version, b.count
        )
        + "</table>"
    )

    metric_rows = "".join(_metric_comparison_row(m) for m in comparison.metrics)
    caveat_items = "".join(f"<li>{_esc(c)}</li>" for c in comparison.caveats)

    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        "<title>mesh-lens guarded comparison</title>"
        f"<style>{_STYLE}</style></head><body>"
        "<h1>mesh-lens guarded pairwise comparison</h1>"
        '<p class="sub">Two pinned cohorts, compared under sample-size, incomparable-schema, '
        "confound, and placeholder guards. A comparison the evidence cannot support REFUSES a "
        "directional verdict rather than fabricating a winner.</p>"
        f'<div class="banner"><strong>Pre-declared decision metric:</strong> '
        f"{_esc(comparison.decision_metric)} &middot; "
        f"<strong>min directional N:</strong> {comparison.min_n} &middot; "
        f"<strong>contrast:</strong> {_esc(comparison.contrast_dimension or '(none)')}</div>"
        f"{verdict_banner}"
        "<h2>Cohorts</h2>"
        f"{cohort_table}"
        "<h2>Per-metric comparison (disclosure)</h2>"
        "<table><tr><th>metric</th><th>samples &amp; means</th><th>verdict</th></tr>"
        f"{metric_rows}</table>"
        "<h2>Caveats</h2>"
        f"<ul>{caveat_items}</ul>"
        "</body></html>\n"
    )


# --------------------------------------------------------------------------- #
# File writers used by the CLI.
# --------------------------------------------------------------------------- #


def write_comparison(comparison: Comparison, out_dir: Path, fmt: str = "both") -> list[Path]:
    """Write the comparison to ``out_dir`` as ``comparison.json`` and/or ``comparison.html``.

    Same deterministic, UTF-8, newline-terminated contract as :func:`write_report`.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    if fmt in ("json", "both"):
        json_path = out_dir / "comparison.json"
        json_path.write_text(render_comparison_json(comparison), encoding="utf-8")
        written.append(json_path)
    if fmt in ("html", "both"):
        html_path = out_dir / "comparison.html"
        html_path.write_text(render_comparison_html(comparison), encoding="utf-8")
        written.append(html_path)
    return written


def write_report(report: Report, out_dir: Path, fmt: str = "both") -> list[Path]:
    """Write the report to ``out_dir`` as ``report.json`` and/or ``report.html``.

    ``fmt`` is ``"json"``, ``"html"``, or ``"both"``. Returns the written paths in a
    stable order. Files are UTF-8, newline-terminated, and byte-identical run to run.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    if fmt in ("json", "both"):
        json_path = out_dir / "report.json"
        json_path.write_text(render_json(report), encoding="utf-8")
        written.append(json_path)
    if fmt in ("html", "both"):
        html_path = out_dir / "report.html"
        html_path.write_text(render_html(report), encoding="utf-8")
        written.append(html_path)
    return written
