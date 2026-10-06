"""Sequential fan-out: one segment calls >= 3 distinct services one after another (no overlap)."""

from __future__ import annotations

import statistics
from collections import Counter
from typing import Any

from tt_harness.findings import Finding, normalize_endpoint, pick_evidence
from tt_harness.rules.base import Trace, is_remote_exit, multi_span_groups, remote_callee, share_of_trace
from tt_harness.spantree import span_ref

RULE = "sequential_fanout"


def sequential_fanout(traces: list[Trace], params: dict[str, Any]) -> list[Finding]:
    min_callees = int(params.get("min_callees", 3))
    min_prevalence = float(params.get("min_prevalence", 0.5))
    findings: list[Finding] = []
    for root, group in multi_span_groups(traces).items():
        hits: dict[tuple[str, str], list[tuple[str, int, float, list[str], str]]] = {}
        for trace in group:
            for key, exits in _fanouts(trace, min_callees).items():
                refs = [span_ref(span) for span in exits] + [span_ref(child) for span in exits for child in trace.tree.children(span)]
                order = " → ".join(remote_callee(trace.tree, span)[0] for span in exits)
                hits.setdefault(key, []).append((trace.trace_id, len(exits), share_of_trace(trace, exits), refs, order))
        for (caller_service, caller_entry), rows in hits.items():
            prevalence = len(rows) / len(group)
            if prevalence < min_prevalence:
                continue
            evidence = pick_evidence([(trace_id, share) for trace_id, _, share, _, _ in rows])
            refs = {row[0]: row[3] for row in rows}
            order, _ = Counter(row[4] for row in rows).most_common(1)[0]
            findings.append(
                Finding(
                    rule=RULE,
                    root=root,
                    caller_service=caller_service,
                    caller_segment_entry=caller_entry,
                    callee_service=None,
                    callee_endpoint=None,
                    detail=order,
                    k_median=float(statistics.median(row[1] for row in rows)),
                    share_median=float(statistics.median(row[2] for row in rows)),
                    prevalence=prevalence,
                    trace_count=len(group),
                    evidence=evidence,
                    highlights={trace_id: refs[trace_id] for trace_id in evidence},
                )
            )
    return findings


def _fanouts(trace: Trace, min_callees: int) -> dict[tuple[str, str], list[dict]]:
    """Per caller segment: the first call to each distinct callee service, if >= min_callees of them run strictly in sequence."""
    by_segment: dict[str, list[dict]] = {}
    for span in trace.tree.spans:
        if is_remote_exit(span):
            by_segment.setdefault(str(trace.tree.caller_segment_entry(span)["segmentId"]), []).append(span)
    found: dict[tuple[str, str], list[dict]] = {}
    for exits in by_segment.values():
        firsts: dict[str, dict] = {}
        for span in sorted(exits, key=lambda span: (span["startTime"], span["spanId"])):
            firsts.setdefault(remote_callee(trace.tree, span)[0], span)
        calls = list(firsts.values())
        if len(calls) < min_callees:
            continue
        if any(later["startTime"] < earlier["endTime"] for earlier, later in zip(calls, calls[1:])):
            continue
        caller = trace.tree.caller_segment_entry(calls[0])
        key = (str(caller.get("serviceCode") or ""), normalize_endpoint(caller.get("endpointName")))
        if len(calls) > len(found.get(key, [])):
            found[key] = calls
    return found
