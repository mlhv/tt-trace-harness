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
        self._children: dict[SpanKey, list[dict]] = {}
        self._segment_first: dict[str, dict] = {}
        for span in self.spans:
            key = parent_key(span)
            if key is not None:
                self._children.setdefault(key, []).append(span)
            current = self._segment_first.get(span["segmentId"])
            if current is None or span["spanId"] < current["spanId"]:
                self._segment_first[span["segmentId"]] = span

    def parent(self, span: dict) -> dict | None:
        key = parent_key(span)
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
