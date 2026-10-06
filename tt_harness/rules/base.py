"""Trace grouping shared by all rules."""

from __future__ import annotations

from dataclasses import dataclass

from tt_harness.findings import interval_union_ms, normalize_endpoint
from tt_harness.spantree import SpanTree, duration_ms

REMOTE_LAYERS = {"Http", "RPCFramework"}
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
        try:
            tree = SpanTree(valid)
            root = tree.trace_root()
            trace = Trace(
                trace_id=str(item.get("traceId")),
                tree=tree,
                root=root,
                root_endpoint=normalize_endpoint(root.get("endpointName")) if root else "",
                duration_ms=duration_ms(root) if root else 0.0,
            )
        except Exception:
            skipped += len(valid)
            continue
        traces.append(trace)
    return traces, skipped


def group_by_root(traces: list[Trace]) -> dict[str, list[Trace]]:
    groups: dict[str, list[Trace]] = {}
    for trace in traces:
        if trace.root_endpoint:
            groups.setdefault(trace.root_endpoint, []).append(trace)
    return groups


def multi_span_groups(traces: list[Trace]) -> dict[str, list[Trace]]:
    """Root groups without single-span traces (gateway stubs hold no calls, so they cannot show a pattern)."""
    groups = {root: [trace for trace in group if len(trace.tree.spans) > 1] for root, group in group_by_root(traces).items()}
    return {root: group for root, group in groups.items() if group}


def trace_extent_ms(trace: Trace) -> float:
    """Root duration, or root start → last span end when an async root ends before its children."""
    if trace.root is None:
        return trace.duration_ms
    extent = max(float(span["endTime"]) for span in trace.tree.spans) - float(trace.root["startTime"])
    return max(trace.duration_ms, extent)


def share_of_trace(trace: Trace, spans: list[dict]) -> float:
    denominator = trace_extent_ms(trace)
    if denominator <= 0:
        return 0.0
    return min(interval_union_ms([(float(span["startTime"]), float(span["endTime"])) for span in spans]) / denominator, 1.0)


def is_remote_exit(span: dict) -> bool:
    return span.get("type") == "Exit" and span.get("layer") in REMOTE_LAYERS


def remote_callee(tree: SpanTree, exit_span: dict) -> tuple[str, str]:
    """(callee service, normalized endpoint) of a remote exit: the child entry's service, else the peer host."""
    endpoint = normalize_endpoint(exit_span.get("endpointName"))
    for child in tree.children(exit_span):
        if child.get("serviceCode"):
            return str(child["serviceCode"]), endpoint
    peer = str(exit_span.get("peer") or "")
    return peer.split(":", 1)[0] or "unknown", endpoint
