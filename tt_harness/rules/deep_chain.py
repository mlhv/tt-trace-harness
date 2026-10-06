"""Deep chain: the longest root-to-leaf service path has more than `max_hops` hops."""

from __future__ import annotations

import statistics
from collections import Counter
from typing import Any

from tt_harness.findings import Finding, pick_evidence
from tt_harness.rules.base import Trace, multi_span_groups
from tt_harness.spantree import SpanTree, span_ref

RULE = "deep_chain"


def deep_chain(traces: list[Trace], params: dict[str, Any]) -> list[Finding]:
    max_hops = int(params.get("max_hops", 5))
    min_prevalence = float(params.get("min_prevalence", 0.5))
    findings: list[Finding] = []
    for root, group in multi_span_groups(traces).items():
        rows = []
        for trace in group:
            services, refs = _deepest_path(trace.tree)
            if len(services) - 1 > max_hops:
                rows.append((trace.trace_id, len(services) - 1, services, refs))
        if not rows or len(rows) / len(group) < min_prevalence:
            continue
        path, _ = Counter(tuple(row[2]) for row in rows).most_common(1)[0]
        evidence = pick_evidence([(trace_id, float(hops)) for trace_id, hops, _, _ in rows])
        refs = {row[0]: row[3] for row in rows}
        findings.append(
            Finding(
                rule=RULE,
                root=root,
                caller_service=path[0],
                caller_segment_entry="",
                callee_service=path[-1],
                callee_endpoint=None,
                detail=" → ".join(path),
                k_median=float(statistics.median(hops for _, hops, _, _ in rows)),
                share_median=None,
                prevalence=len(rows) / len(group),
                trace_count=len(group),
                evidence=evidence,
                highlights={trace_id: refs[trace_id] for trace_id in evidence},
            )
        )
    return findings


def _deepest_path(tree: SpanTree) -> tuple[list[str], list[str]]:
    """Service path (consecutive repeats collapsed) of the deepest root-to-leaf walk, plus the span refs where each service is entered."""
    best: tuple[list[str], list[str]] = ([], [])
    stack = [(root, [], []) for root in tree.roots()]
    while stack:
        span, services, refs = stack.pop()
        service = str(span.get("serviceCode") or "")
        if not services or services[-1] != service:
            services, refs = services + [service], refs + [span_ref(span)]
        children = tree.children(span)
        if not children and len(services) > len(best[0]):
            best = (services, refs)
        stack.extend((child, services, refs) for child in children)
    return best
