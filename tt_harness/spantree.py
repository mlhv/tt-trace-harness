"""Span-tree helpers for SkyWalking traces.

spanId/parentSpanId are segment-local: every segment (one per process/service
hop, or per thread) restarts at spanId=0 with parentSpanId=-1. Cross-segment
linkage lives ONLY in `refs`.

- parentSpanId != -1  -> parent is (same segment, parentSpanId)
- parentSpanId == -1 and refs -> parent is
  (refs[0].parentSegmentId, refs[0].parentSpanId), a DIFFERENT segment
- parentSpanId == -1 and no refs -> the trace's true root, no parent

A ref may point at a segment that is not in the exported trace (partial
trace); such a span has no resolvable parent and is reported as a root.

A service that copies its incoming propagation header onto its own outgoing
calls makes every downstream entry name the FIRST exit of the chain as its
parent (SEALAB train-ticket forwards the incoming HttpHeaders). SpanTree
re-attaches such an entry to the exit that called its service: when the ref'd
exit's peer host is not the entry's service, the innermost exit whose peer host
is that service and whose time window holds the entry's start becomes the
parent. Without such an exit the ref is kept.
"""

from __future__ import annotations

SpanKey = tuple[str, int]


def span_key(span: dict) -> SpanKey:
    return (span["segmentId"], span["spanId"])


def parent_key(span: dict) -> SpanKey | None:
    if span["parentSpanId"] != -1:
        return (span["segmentId"], span["parentSpanId"])
    refs = span.get("refs") or []
    if refs:
        return (refs[0]["parentSegmentId"], refs[0]["parentSpanId"])
    return None


def service_name(span: dict) -> str:
    """serviceCode without the agent's `|namespace|` suffix."""
    return str(span.get("serviceCode") or "").split("|", 1)[0]


def peer_host(span: dict) -> str:
    return str(span.get("peer") or "").rsplit(":", 1)[0]


def duration_ms(span: dict) -> float:
    return float(span["endTime"] - span["startTime"])


def span_ref(span: dict) -> str:
    return f"{span['segmentId']}:{span['spanId']}"


def is_cross_thread(span: dict) -> bool:
    refs = span.get("refs") or []
    return bool(refs) and refs[0].get("type") == "CROSS_THREAD"


class SpanTree:
    def __init__(self, spans: list[dict]) -> None:
        self.spans = list(spans)
        self._by_key = {span_key(span): span for span in self.spans}
        self._parent_keys = {span_key(span): self._resolve_parent_key(span) for span in self.spans}
        self._children: dict[SpanKey, list[dict]] = {}
        self._segment_first: dict[str, dict] = {}
        for span in self.spans:
            key = self._parent_keys[span_key(span)]
            if key is not None:
                self._children.setdefault(key, []).append(span)
            current = self._segment_first.get(span["segmentId"])
            if current is None or span["spanId"] < current["spanId"]:
                self._segment_first[span["segmentId"]] = span

    def _resolve_parent_key(self, span: dict) -> SpanKey | None:
        key = parent_key(span)
        if key is None or span["parentSpanId"] != -1 or span.get("type") != "Entry" or is_cross_thread(span):
            return key
        referenced = self._by_key.get(key)
        service = service_name(span)
        if referenced is None or referenced.get("type") != "Exit" or not service or peer_host(referenced) == service:
            return key
        start = span["startTime"]
        callers = [
            candidate
            for candidate in self.spans
            if candidate.get("type") == "Exit"
            and candidate is not referenced
            and peer_host(candidate) == service
            and candidate["startTime"] <= start <= candidate["endTime"]
        ]
        if not callers:
            return key
        return span_key(max(callers, key=lambda candidate: (candidate["startTime"], -candidate["endTime"])))

    def parent(self, span: dict) -> dict | None:
        key = self._parent_keys.get(span_key(span))
        return self._by_key.get(key) if key is not None else None

    def children(self, span: dict) -> list[dict]:
        return list(self._children.get(span_key(span), []))

    def roots(self) -> list[dict]:
        return [span for span in self.spans if self.parent(span) is None]

    def self_time_ms(self, span: dict) -> float:
        children_total = sum(duration_ms(child) for child in self.children(span))
        return max(duration_ms(span) - children_total, 0.0)

    def trace_root(self) -> dict | None:
        roots = self.roots()
        if not roots:
            return None
        true_roots = [span for span in roots if parent_key(span) is None]
        return min(true_roots or roots, key=lambda span: (span["startTime"], span["segmentId"], span["spanId"]))

    def segment_entry(self, span: dict) -> dict:
        return self._segment_first[span["segmentId"]]

    def caller_segment_entry(self, span: dict) -> dict:
        """The entry span of the request segment that issued `span`.

        Work handed to a worker thread runs in its own segment whose first span
        carries a CROSS_THREAD ref; climb those to the spawning segment.
        """
        entry = self.segment_entry(span)
        seen: set[str] = set()
        while is_cross_thread(entry) and entry["segmentId"] not in seen:
            seen.add(entry["segmentId"])
            parent = self.parent(entry)
            if parent is None:
                break
            entry = self.segment_entry(parent)
        return entry
