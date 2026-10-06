"""Synthetic SkyWalking spans shaped like OAP queryTrace output."""

from __future__ import annotations

TRIPS_LEFT = "{POST}/api/v1/travelservice/trips/left"
SEAT_PATH = "/api/v1/seatservice/seats/left_tickets"


def span(seg, span_id, parent, service, endpoint, start, end, *, kind="Entry", layer="Http", refs=None, peer="", is_error=False):
    return {
        "traceId": "t",
        "segmentId": seg,
        "spanId": span_id,
        "parentSpanId": parent,
        "serviceCode": service,
        "serviceInstanceName": f"{service}-0",
        "startTime": start,
        "endTime": end,
        "endpointName": endpoint,
        "type": kind,
        "peer": peer,
        "component": "SpringMVC" if kind == "Entry" else "SpringRestTemplate",
        "layer": layer,
        "isError": is_error,
        "refs": refs or [],
        "tags": [],
        "logs": [],
    }


def ref(seg, span_id, kind="CROSS_PROCESS"):
    return [{"parentSegmentId": seg, "parentSpanId": span_id, "traceId": "t", "type": kind}]


def trips_left_trace(trace_id, seat_calls, *, gap=10, call_ms=8, parallel=False, root_ms=None):
    """ts-travel-service root issuing `seat_calls` exits to ts-seat-service.

    Sequential calls start at 5, 15, 25, ...; the root ends 5 ms after the
    last call slot unless `root_ms` is given.
    """
    root_end = root_ms if root_ms is not None else 5 + seat_calls * gap + 5
    spans = [span("T", 0, -1, "ts-travel-service", TRIPS_LEFT, 0, root_end)]
    for i in range(seat_calls):
        start = 5 if parallel else 5 + i * gap
        end = start + call_ms
        spans.append(span("T", i + 1, 0, "ts-travel-service", SEAT_PATH, start, end, kind="Exit", peer="ts-seat-service:18898"))
        spans.append(span(f"S{i}", 0, -1, "ts-seat-service", "{POST}" + SEAT_PATH, start + 1, end - 1, refs=ref("T", i + 1)))
    return {"traceId": trace_id, "spans": spans}


def stub_trace(trace_id, path="/api/v1/travelservice/trips/left", service="ts-gateway-service", ms=1):
    return {"traceId": trace_id, "spans": [span("G", 0, -1, service, path, 0, ms)]}
