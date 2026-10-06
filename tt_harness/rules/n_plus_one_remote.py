"""N+1 remote calls: one caller segment calls the same callee endpoint k times."""

from __future__ import annotations

import statistics
from typing import Any

from tt_harness.findings import Finding, interval_union_ms, normalize_endpoint, pick_evidence
from tt_harness.rules.base import Trace, group_by_root
from tt_harness.spantree import SpanTree, span_ref

RULE = "n_plus_one_remote"
REMOTE_LAYERS = {"Http", "RPCFramework"}
CallKey = tuple[str, str, str, str]


def n_plus_one_remote(traces: list[Trace], params: dict[str, Any]) -> list[Finding]:
    min_repeats = int(params.get("min_repeats", 3))
    min_prevalence = float(params.get("min_prevalence", 0.5))
    findings: list[Finding] = []
    for root, group in group_by_root(traces).items():
        # Single-span traces (e.g. gateway stubs) cannot contain exits; exclude them from prevalence.
        group = [trace for trace in group if len(trace.tree.spans) > 1]
        if not group:
            continue
        hits: dict[CallKey, list[tuple[str, int, float, list[str]]]] = {}
        for trace in group:
            for key, exits_by_segment in _exits_by_call_per_segment(trace.tree).items():
                # Find the segment with the most exits
                best_segment_id = None
                best_exits = []
                best_k = 0
                for segment_id, exits in exits_by_segment.items():
                    if len(exits) >= min_repeats and len(exits) > best_k:
                        best_segment_id = segment_id
                        best_exits = exits
                        best_k = len(exits)

                if best_k == 0:
                    continue

                refs = [span_ref(span) for span in best_exits]
                refs += [span_ref(child) for span in best_exits for child in trace.tree.children(span)]
                hits.setdefault(key, []).append((trace.trace_id, best_k, _share(trace, best_exits), refs))
        for (caller_service, caller_entry, callee_service, callee_endpoint), rows in hits.items():
            prevalence = len(rows) / len(group)
            if prevalence < min_prevalence:
                continue
            evidence = pick_evidence([(trace_id, share) for trace_id, _, share, _ in rows])
            refs_by_trace = {trace_id: refs for trace_id, _, _, refs in rows}
            findings.append(
                Finding(
                    rule=RULE,
                    root=root,
                    caller_service=caller_service,
                    caller_segment_entry=caller_entry,
                    callee_service=callee_service,
                    callee_endpoint=callee_endpoint,
                    detail="",
                    k_median=float(statistics.median(k for _, k, _, _ in rows)),
                    share_median=float(statistics.median(share for _, _, share, _ in rows)),
                    prevalence=prevalence,
                    trace_count=len(group),
                    evidence=evidence,
                    highlights={trace_id: refs_by_trace[trace_id] for trace_id in evidence},
                )
            )
    return findings


def _exits_by_call_per_segment(tree: SpanTree) -> dict[CallKey, dict[str, list[dict]]]:
    """Group exits by (4-part key, caller segment id)."""
    buckets: dict[CallKey, dict[str, list[dict]]] = {}
    for span in tree.spans:
        if span.get("type") != "Exit" or span.get("layer") not in REMOTE_LAYERS:
            continue
        caller = tree.caller_segment_entry(span)
        callee_service, callee_endpoint = _callee(tree, span)
        key = (str(caller.get("serviceCode") or ""), normalize_endpoint(caller.get("endpointName")), callee_service, callee_endpoint)
        segment_id = str(caller.get("segmentId") or "")
        buckets.setdefault(key, {}).setdefault(segment_id, []).append(span)
    return buckets


def _callee(tree: SpanTree, exit_span: dict) -> tuple[str, str]:
    endpoint = normalize_endpoint(exit_span.get("endpointName"))
    for child in tree.children(exit_span):
        if child.get("serviceCode"):
            return str(child["serviceCode"]), endpoint
    peer = str(exit_span.get("peer") or "")
    return peer.split(":", 1)[0] or "unknown", endpoint


def _share(trace: Trace, exits: list[dict]) -> float:
    denominator = trace.duration_ms
    if trace.root is not None:
        # An async gateway root can end before its children; use the whole trace extent.
        extent = max(float(span["endTime"]) for span in trace.tree.spans) - float(trace.root["startTime"])
        denominator = max(denominator, extent)
    if denominator <= 0:
        return 0.0
    union = interval_union_ms([(float(span["startTime"]), float(span["endTime"])) for span in exits])
    return min(union / denominator, 1.0)
