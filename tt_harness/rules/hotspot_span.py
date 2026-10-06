"""Hotspot span: one span's own (self) time dominates the trace."""

from __future__ import annotations

import statistics
from typing import Any

from tt_harness.findings import Finding, normalize_endpoint, pick_evidence
from tt_harness.rules.base import Trace, multi_span_groups, trace_extent_ms
from tt_harness.spantree import span_ref

RULE = "hotspot_span"


def hotspot_span(traces: list[Trace], params: dict[str, Any]) -> list[Finding]:
    threshold = float(params.get("min_self_share", 0.4))
    min_prevalence = float(params.get("min_prevalence", 0.5))
    findings: list[Finding] = []
    for root, group in multi_span_groups(traces).items():
        hits: dict[tuple[str, str], list[tuple[str, float, str]]] = {}
        for trace in group:
            extent = trace_extent_ms(trace)
            if extent <= 0:
                continue
            hot = max(trace.tree.spans, key=lambda span: (trace.tree.self_time_ms(span), -span["startTime"]))
            share = trace.tree.self_time_ms(hot) / extent
            if share < threshold:
                continue
            key = (str(hot.get("serviceCode") or ""), normalize_endpoint(hot.get("endpointName")))
            hits.setdefault(key, []).append((trace.trace_id, min(share, 1.0), span_ref(hot)))
        for (service, endpoint), rows in hits.items():
            prevalence = len(rows) / len(group)
            if prevalence < min_prevalence:
                continue
            evidence = pick_evidence([(trace_id, share) for trace_id, share, _ in rows])
            refs = {trace_id: [ref] for trace_id, _, ref in rows}
            findings.append(
                Finding(
                    rule=RULE,
                    root=root,
                    caller_service=service,
                    caller_segment_entry=endpoint,
                    callee_service=None,
                    callee_endpoint=None,
                    detail="",
                    k_median=None,
                    share_median=float(statistics.median(share for _, share, _ in rows)),
                    prevalence=prevalence,
                    trace_count=len(group),
                    evidence=evidence,
                    highlights={trace_id: refs[trace_id] for trace_id in evidence},
                )
            )
    return findings
