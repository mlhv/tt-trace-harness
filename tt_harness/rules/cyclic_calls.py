"""Cyclic calls: a service reappears on one root-to-leaf service path."""

from __future__ import annotations

import statistics
from typing import Any

from tt_harness.findings import Finding
from tt_harness.rules.base import Trace, group_by_root
from tt_harness.spantree import SpanTree, span_ref

RULE = "cyclic_calls"


def cyclic_calls(traces: list[Trace], params: dict[str, Any]) -> list[Finding]:
    min_prevalence = float(params.get("min_prevalence", 0.5))
    findings: list[Finding] = []
    for root, group in group_by_root(traces).items():
        hits: dict[tuple[str, ...], list[tuple[str, int, list[str]]]] = {}
        for trace in group:
            for cycle, refs in _cycles(trace.tree).items():
                hits.setdefault(cycle, []).append((trace.trace_id, len(cycle) - 1, refs))
        for cycle, rows in hits.items():
            prevalence = len(rows) / len(group)
            if prevalence < min_prevalence:
                continue
            evidence = [trace_id for trace_id, _, _ in rows[:3]]
            refs_by_trace = {trace_id: refs for trace_id, _, refs in rows}
            findings.append(
                Finding(
                    rule=RULE,
                    root=root,
                    caller_service=cycle[0],
                    caller_segment_entry="",
                    callee_service=cycle[1],
                    callee_endpoint=None,
                    detail=" → ".join(cycle),
                    k_median=float(statistics.median(hops for _, hops, _ in rows)),
                    share_median=None,
                    prevalence=prevalence,
                    trace_count=len(group),
                    evidence=evidence,
                    highlights={trace_id: refs_by_trace[trace_id] for trace_id in evidence},
                )
            )
    return findings


def _cycles(tree: SpanTree) -> dict[tuple[str, ...], list[str]]:
    """Distinct service cycles in one trace, each with the span refs that form it.

    `services` holds (service, index into refs where the path entered it);
    consecutive spans of the same service collapse into one path element.
    """
    found: dict[tuple[str, ...], list[str]] = {}
    stack: list[tuple[dict, tuple[tuple[str, int], ...], tuple[str, ...]]] = [(root, (), ()) for root in tree.roots()]
    while stack:
        span, services, refs = stack.pop()
        service = str(span.get("serviceCode") or "")
        refs = refs + (span_ref(span),)
        if not services or services[-1][0] != service:
            names = [name for name, _ in services]
            if service in names:
                start = names.index(service)
                cycle = tuple(names[start:]) + (service,)
                found.setdefault(cycle, list(refs[services[start][1] :]))
                continue
            services = services + ((service, len(refs) - 1),)
        for child in tree.children(span):
            stack.append((child, services, refs))
    return found
