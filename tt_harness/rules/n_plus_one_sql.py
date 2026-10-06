"""N+1 SQL: one caller segment runs the same statement k times."""

from __future__ import annotations

import re
import statistics
from typing import Any

from tt_harness.findings import Finding, normalize_endpoint, pick_evidence
from tt_harness.rules.base import Trace, multi_span_groups, share_of_trace
from tt_harness.spantree import span_ref

RULE = "n_plus_one_sql"
_TABLE = re.compile(r"\b(?:from|into|update|join)\s+[`\"]?([\w.]+)", re.IGNORECASE)
SqlKey = tuple[str, str, str, str]


def n_plus_one_sql(traces: list[Trace], params: dict[str, Any]) -> list[Finding]:
    min_repeats = int(params.get("min_repeats", 3))
    min_prevalence = float(params.get("min_prevalence", 0.5))
    findings: list[Finding] = []
    for root, group in multi_span_groups(traces).items():
        hits: dict[SqlKey, list[tuple[str, int, float, list[str]]]] = {}
        for trace in group:
            for key, by_segment in _statements_by_segment(trace).items():
                spans = max(by_segment.values(), key=len)
                if len(spans) >= min_repeats:
                    hits.setdefault(key, []).append((trace.trace_id, len(spans), share_of_trace(trace, spans), [span_ref(span) for span in spans]))
        for (caller_service, caller_entry, database, statement), rows in hits.items():
            prevalence = len(rows) / len(group)
            if prevalence < min_prevalence:
                continue
            evidence = pick_evidence([(trace_id, share) for trace_id, _, share, _ in rows])
            refs = {trace_id: refs for trace_id, _, _, refs in rows}
            table = _TABLE.search(statement)
            findings.append(
                Finding(
                    rule=RULE,
                    root=root,
                    caller_service=caller_service,
                    caller_segment_entry=caller_entry,
                    callee_service=database,
                    callee_endpoint=f"SQL on {table.group(1)}" if table else "SQL",
                    detail=statement,
                    k_median=float(statistics.median(k for _, k, _, _ in rows)),
                    share_median=float(statistics.median(share for _, _, share, _ in rows)),
                    prevalence=prevalence,
                    trace_count=len(group),
                    evidence=evidence,
                    highlights={trace_id: refs[trace_id] for trace_id in evidence},
                )
            )
    return findings


def _statements_by_segment(trace: Trace) -> dict[SqlKey, dict[str, list[dict]]]:
    buckets: dict[SqlKey, dict[str, list[dict]]] = {}
    for span in trace.tree.spans:
        if span.get("type") != "Exit" or span.get("layer") != "Database":
            continue
        statement = " ".join(str(_tag(span, "db.statement") or "").split())
        if not statement:
            continue
        caller = trace.tree.caller_segment_entry(span)
        database = str(span.get("peer") or "").split(":", 1)[0] or str(_tag(span, "db.instance") or "db")
        key = (str(caller.get("serviceCode") or ""), normalize_endpoint(caller.get("endpointName")), database, statement)
        buckets.setdefault(key, {}).setdefault(str(caller.get("segmentId") or ""), []).append(span)
    return buckets


def _tag(span: dict, key: str) -> Any:
    return next((tag.get("value") for tag in span.get("tags") or [] if isinstance(tag, dict) and tag.get("key") == key), None)
