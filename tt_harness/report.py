"""Self-contained HTML rendering for trace diagnostics (no scripts, no CDN)."""

from __future__ import annotations

from html import escape
from typing import Any

from tt_harness.findings import Finding, endpoint_path, normalize_endpoint
from tt_harness.rules.base import Trace, build_traces, group_by_root
from tt_harness.spantree import SpanTree, duration_ms, span_ref

CSS = """
:root{--bg:#ffffff;--fg:#1f2328;--muted:#656d76;--line:#d0d7de;--panel:#f6f8fa;--hl:#fff1c2;--hl-line:#d4a72c;--badge:#ddf4ff;--badge-fg:#0550ae;--err:#cf222e}
@media (prefers-color-scheme: dark){:root{--bg:#0d1117;--fg:#e6edf3;--muted:#8d96a0;--line:#30363d;--panel:#161b22;--hl:#3b2e00;--hl-line:#bb8009;--badge:#0c2d6b;--badge-fg:#a5d6ff;--err:#ff7b72}}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif}
main{max-width:1200px;margin:0 auto;padding:24px 16px}
h1{font-size:22px;margin:0 0 12px}h2{font-size:17px;margin:28px 0 8px}h3{font-size:15px;margin:0 0 4px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
dl.meta{display:grid;grid-template-columns:max-content 1fr;gap:2px 16px;margin:0}dl.meta dt{color:var(--muted)}dl.meta dd{margin:0}
.table-wrap{overflow-x:auto}table{border-collapse:collapse;width:100%}th,td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}
th{background:var(--panel);font-weight:600}.num{text-align:right;font-variant-numeric:tabular-nums}.ids{font-family:ui-monospace,Menlo,monospace;font-size:12px}
.badge{display:inline-block;background:var(--badge);color:var(--badge-fg);border-radius:10px;padding:0 8px;font-size:12px;margin-left:4px}
.sub{color:var(--muted);font-size:12px}.empty{color:var(--muted)}.errors{border:1px solid var(--err);color:var(--err);padding:8px 12px;border-radius:6px}
section.root{border:1px solid var(--line);border-radius:8px;padding:12px;margin:12px 0;background:var(--panel)}
.tree{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12.5px;overflow-x:auto}
.tree details>summary{cursor:pointer;list-style:none}.tree details>summary::before{content:"▸ ";color:var(--muted)}.tree details[open]>summary::before{content:"▾ "}
.kids{margin-left:18px;border-left:1px dashed var(--line);padding-left:8px}.leaf{padding-left:14px}
.row{white-space:nowrap}.row.hl{background:var(--hl);outline:1px solid var(--hl-line);border-radius:3px}
.svc{font-weight:600}.kind{color:var(--muted)}.dur{color:var(--muted);margin-left:6px}.err{color:var(--err);margin-left:6px}
details.repeat>summary{color:var(--muted)}details.repeat.flagged>summary{color:var(--hl-line);font-weight:600}
figure.chart{margin:12px 0;overflow-x:auto}figure.chart svg{max-width:100%;height:auto}
"""


def render_run_report(
    meta: dict[str, Any],
    raw_traces: list[dict],
    findings: list[Finding],
    *,
    errors: dict[str, str] | None = None,
    skipped_spans: int = 0,
) -> str:
    traces, _ = build_traces(raw_traces)
    by_id = {trace.trace_id: trace for trace in traces}
    groups = group_by_root(traces)
    order = sorted(groups, key=lambda root: (-len(groups[root]), root))
    anchors = {root: f"root-{index}" for index, root in enumerate(order)}

    parts = ["<h1>Trace diagnostics</h1>", _meta(meta, len(traces), skipped_spans), _errors(errors or {})]
    parts.append(_findings_table(findings, anchors))
    parts.append("<h2>Span trees by root endpoint</h2>")
    if not traces:
        parts.append("<p class='empty'>0 traces collected — no span trees to show.</p>")
    for root in order:
        trace = _display_trace(root, groups[root], findings, by_id)
        marks = _marks(trace.trace_id, findings)
        parts.append(
            f"<section class='root' id='{anchors[root]}'><h3>{escape(root)}</h3>"
            f"<p class='sub'>{len(groups[root])} traces · showing {escape(trace.trace_id)} ({trace.duration_ms:.1f} ms)</p>"
            f"{_render_tree(trace.tree, marks)}</section>"
        )
    return _page(f"Trace diagnostics — {meta.get('Run', '')}", "".join(parts))


def render_comparison(title: str, rows: list[dict[str, Any]], chart_svg: str = "") -> str:
    levels = sorted({level for row in rows for level in row["shares"]}, key=float)
    parts = [f"<h1>{escape(title)}</h1>"]
    if chart_svg:
        parts.append(f"<figure class='chart'>{chart_svg}</figure>")
    parts.append("<h2>Findings across runs</h2>")
    if not rows:
        parts.append("<p class='empty'>No findings in the selected runs.</p>")
        return _page(title, "".join(parts))
    head = "".join(f"<th class='num'>{escape(level)} users</th>" for level in levels)
    body = []
    for row in rows:
        target = escape(row["callee"] or "")
        detail = f"<div class='sub'>{escape(row['detail'])}</div>" if row.get("detail") else ""
        shares = "".join(f"<td class='num'>{_fmt(row['shares'].get(level))}</td>" for level in levels)
        body.append(
            f"<tr><td><span class='badge'>{escape(row['rule'])}</span></td><td>{escape(row['root'])}</td>"
            f"<td>{escape(row['caller'])} → {target}{detail}</td>{shares}"
            f"<td class='num'>{_fmt(row['load_slope'], 4)}</td><td class='num'>{row['runs']}</td></tr>"
        )
    parts.append(
        "<div class='table-wrap'><table><thead><tr><th>Rule</th><th>Root</th><th>Caller → callee</th>"
        f"{head}<th class='num'>load slope</th><th class='num'>runs</th></tr></thead><tbody>{''.join(body)}</tbody></table></div>"
    )
    return _page(title, "".join(parts))


def _page(title: str, body: str) -> str:
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{escape(title)}</title><style>{CSS}</style></head><body><main>{body}</main></body></html>"
    )


def _fmt(value: Any, digits: int = 2) -> str:
    if value is None:
        return "–"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return escape(str(value))


def _meta(meta: dict[str, Any], trace_count: int, skipped_spans: int) -> str:
    items = {**meta, "Traces": f"{trace_count} traces", "Skipped spans": skipped_spans}
    rows = "".join(f"<dt>{escape(str(key))}</dt><dd>{_fmt(value)}</dd>" for key, value in items.items())
    return f"<dl class='meta'>{rows}</dl>"


def _errors(errors: dict[str, str]) -> str:
    if not errors:
        return ""
    items = "".join(f"<li><b>{escape(name)}</b>: {escape(message)}</li>" for name, message in errors.items())
    return f"<div class='errors'><b>Rule errors</b><ul>{items}</ul></div>"


def _anchor_for(root: str, anchors: dict[str, str]) -> str:
    if root in anchors:
        return anchors[root]
    return next((anchor for name, anchor in anchors.items() if endpoint_path(name) == root), "")


def _findings_table(findings: list[Finding], anchors: dict[str, str]) -> str:
    if not findings:
        return "<h2>Findings</h2><p class='empty'>No antipatterns found.</p>"
    rows = []
    for finding in findings:
        callee = ""
        if finding.callee_service:
            callee = " → " + escape(finding.callee_service)
            if finding.callee_endpoint:
                callee += " " + escape(finding.callee_endpoint)
        detail = f"<div class='sub'>{escape(finding.detail)}</div>" if finding.detail else ""
        rows.append(
            f"<tr id='finding-{escape(finding.id)}'><td><span class='badge'>{escape(finding.rule)}</span></td>"
            f"<td><a href='#{_anchor_for(finding.root, anchors)}'>{escape(finding.root)}</a></td>"
            f"<td>{escape(finding.caller_service)}{callee}{detail}</td>"
            f"<td class='num'>{_fmt(finding.k_median, 1)}</td><td class='num'>{_fmt(finding.share_median)}</td>"
            f"<td class='num'>{finding.prevalence:.2f}</td><td class='num'>{finding.trace_count}</td>"
            f"<td class='ids'>{', '.join(escape(trace_id) for trace_id in finding.evidence)}</td></tr>"
        )
    return (
        f"<h2>Findings ({len(findings)})</h2><div class='table-wrap'><table><thead><tr><th>Rule</th><th>Root</th>"
        "<th>Caller → callee</th><th class='num'>k</th><th class='num'>share</th><th class='num'>prevalence</th>"
        f"<th class='num'>traces</th><th>evidence</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>"
    )


def _display_trace(root: str, group: list[Trace], findings: list[Finding], by_id: dict[str, Trace]) -> Trace:
    for finding in findings:
        if finding.root in (root, endpoint_path(root)):
            for trace_id in finding.evidence:
                trace = by_id.get(trace_id)
                if trace is not None and trace.root_endpoint == root:
                    return trace
    ordered = sorted(group, key=lambda trace: (trace.duration_ms, trace.trace_id))
    return ordered[(len(ordered) - 1) // 2]


def _marks(trace_id: str, findings: list[Finding]) -> dict[str, list[str]]:
    marks: dict[str, list[str]] = {}
    for finding in findings:
        for ref in finding.highlights.get(trace_id, []):
            rules = marks.setdefault(ref, [])
            if finding.rule not in rules:
                rules.append(finding.rule)
    return marks


def _render_tree(tree: SpanTree, marks: dict[str, list[str]]) -> str:
    open_refs: set[str] = set()
    for span in tree.spans:
        if span_ref(span) not in marks:
            continue
        current: dict | None = span
        while current is not None and span_ref(current) not in open_refs:
            open_refs.add(span_ref(current))
            current = tree.parent(current)
    roots = sorted(tree.roots(), key=_order)
    return "<div class='tree'>" + "".join(_render_span(tree, root, marks, open_refs, 0) for root in roots) + "</div>"


def _order(span: dict) -> tuple:
    return (span["startTime"], span["segmentId"], span["spanId"])


def _render_span(tree: SpanTree, span: dict, marks: dict[str, list[str]], open_refs: set[str], depth: int) -> str:
    label = _label(tree, span, marks)
    children = sorted(tree.children(span), key=_order)
    if not children:
        return f"<div class='leaf'>{label}</div>"
    is_open = depth < 1 or span_ref(span) in open_refs
    body = "".join(_render_children(tree, children, marks, open_refs, depth + 1))
    return f"<details{' open' if is_open else ''}><summary>{label}</summary><div class='kids'>{body}</div></details>"


def _render_children(tree: SpanTree, children: list[dict], marks: dict[str, list[str]], open_refs: set[str], depth: int) -> list[str]:
    """Siblings calling the same (service, endpoint) 3+ times collapse into '×k calls'."""
    groups: dict[tuple[str, str], list[dict]] = {}
    for child in children:
        groups.setdefault(_sibling_key(child), []).append(child)
    out: list[str] = []
    for key, run in groups.items():
        if len(run) < 3:
            out.extend(_render_span(tree, span, marks, open_refs, depth) for span in run)
            continue
        out.append(_render_span(tree, run[0], marks, open_refs, depth))
        rest = "".join(_render_span(tree, span, marks, open_refs, depth) for span in run[1:])
        flagged = " flagged" if any(span_ref(span) in marks for span in run) else ""
        out.append(
            f"<details class='repeat{flagged}'><summary>×{len(run)} calls to {escape(key[1])} — {len(run) - 1} more</summary>"
            f"<div class='kids'>{rest}</div></details>"
        )
    return out


def _sibling_key(span: dict) -> tuple[str, str]:
    return (str(span.get("serviceCode") or ""), normalize_endpoint(span.get("endpointName")))


def _label(tree: SpanTree, span: dict, marks: dict[str, list[str]]) -> str:
    rules = marks.get(span_ref(span), [])
    kind = str(span.get("type") or "")
    badges = "".join(f"<span class='badge'>{escape(rule)}</span>" for rule in rules)
    error = "<span class='err'>error</span>" if span.get("isError") else ""
    return (
        f"<span class='row{' hl' if rules else ''}'><span class='svc'>{escape(str(span.get('serviceCode') or '?'))}</span> "
        f"<span class='ep'>{escape(str(span.get('endpointName') or ''))}</span> <span class='kind'>{escape(kind)}</span>"
        f"<span class='dur'>{duration_ms(span):.1f} ms · self {tree.self_time_ms(span):.1f}</span>{error}{badges}</span>"
    )
