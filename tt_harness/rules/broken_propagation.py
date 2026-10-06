"""Broken propagation: an entry service answers with a lone stub span while the
real work for the same endpoint shows up as separate, independently rooted traces
(trace context was not propagated downstream)."""

from __future__ import annotations

from collections import Counter
from typing import Any

from tt_harness.findings import Finding, endpoint_path
from tt_harness.rules.base import Trace
from tt_harness.spantree import span_ref

RULE = "broken_propagation"


def broken_propagation(traces: list[Trace], params: dict[str, Any]) -> list[Finding]:
    stub_ms = float(params.get("stub_ms", 5))
    min_fraction = float(params.get("min_fraction", 0.1))
    by_path: dict[str, list[Trace]] = {}
    for trace in traces:
        if trace.root is not None:
            by_path.setdefault(endpoint_path(trace.root.get("endpointName")), []).append(trace)

    findings: list[Finding] = []
    for path, group in by_path.items():
        stubs = [t for t in group if len(t.tree.spans) == 1 and t.root.get("type") == "Entry" and t.duration_ms < stub_ms]
        full = [t for t in group if len(t.tree.spans) > 1]
        if not stubs or not full:
            continue
        fraction = len(stubs) / (len(stubs) + len(full))
        if fraction < min_fraction:
            continue
        evidence = stubs[:3]
        findings.append(
            Finding(
                rule=RULE,
                root=path,
                caller_service=_most_common(t.root.get("serviceCode") for t in stubs),
                caller_segment_entry=path,
                callee_service=_most_common(t.root.get("serviceCode") for t in full),
                callee_endpoint=None,
                detail="",
                k_median=None,
                share_median=None,
                prevalence=fraction,
                trace_count=len(stubs) + len(full),
                evidence=[t.trace_id for t in evidence],
                highlights={t.trace_id: [span_ref(t.root)] for t in evidence},
            )
        )
    return findings


def _most_common(values) -> str:
    return Counter(str(value or "") for value in values).most_common(1)[0][0]
