"""Trace grouping shared by all rules."""

from __future__ import annotations

from dataclasses import dataclass

from tt_harness.findings import normalize_endpoint
from tt_harness.spantree import SpanTree, duration_ms

REQUIRED_SPAN_KEYS = ("segmentId", "spanId", "parentSpanId", "startTime", "endTime")


@dataclass
class Trace:
    trace_id: str
    tree: SpanTree
    root: dict | None
    root_endpoint: str
    duration_ms: float


def build_traces(raw_traces: list[dict]) -> tuple[list[Trace], int]:
    traces: list[Trace] = []
    skipped = 0
    for item in raw_traces:
        spans = item.get("spans") or []
        valid = [span for span in spans if all(span.get(key) is not None for key in REQUIRED_SPAN_KEYS)]
        skipped += len(spans) - len(valid)
        if not valid:
            continue
        tree = SpanTree(valid)
        root = tree.trace_root()
        traces.append(
            Trace(
                trace_id=str(item.get("traceId")),
                tree=tree,
                root=root,
                root_endpoint=normalize_endpoint(root.get("endpointName")) if root else "",
                duration_ms=duration_ms(root) if root else 0.0,
            )
        )
    return traces, skipped


def group_by_root(traces: list[Trace]) -> dict[str, list[Trace]]:
    groups: dict[str, list[Trace]] = {}
    for trace in traces:
        if trace.root_endpoint:
            groups.setdefault(trace.root_endpoint, []).append(trace)
    return groups
