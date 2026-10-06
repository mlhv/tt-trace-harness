"""N+1 remote calls: one caller segment calls the same callee endpoint k times."""

from __future__ import annotations

import statistics
from typing import Any

from tt_harness.findings import Finding, normalize_endpoint, pick_evidence
from tt_harness.rules.base import Trace, is_remote_exit, multi_span_groups, remote_callee, share_of_trace
from tt_harness.spantree import SpanTree, span_ref

RULE = "n_plus_one_remote"
CallKey = tuple[str, str, str, str]


def n_plus_one_remote(traces: list[Trace], params: dict[str, Any]) -> list[Finding]:
    min_repeats = int(params.get("min_repeats", 3))
    min_prevalence = float(params.get("min_prevalence", 0.5))
    findings: list[Finding] = []
    for root, group in multi_span_groups(traces).items():
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
                hits.setdefault(key, []).append((trace.trace_id, best_k, share_of_trace(trace, best_exits), refs))
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
        if not is_remote_exit(span):
            continue
        caller = tree.caller_segment_entry(span)
        callee_service, callee_endpoint = remote_callee(tree, span)
        key = (str(caller.get("serviceCode") or ""), normalize_endpoint(caller.get("endpointName")), callee_service, callee_endpoint)
        segment_id = str(caller.get("segmentId") or "")
        buckets.setdefault(key, {}).setdefault(segment_id, []).append(span)
    return buckets

