"""Tests for DependencyAnalyzer.

The span fixtures here are excerpts of a REAL SkyWalking trace captured from the
TrainTicket cluster (~/skywalking-exports/traces_2026-08-17.jsonl, trace
0a14a37113834ed397506608f5dc3987.524.17869773164440005 -- 30 segments, 149
spans, 16 services). They are trimmed for readability but every segmentId,
spanId, parentSpanId, refs entry and timestamp is verbatim from that capture.

Why that matters: SkyWalking spanIds are SEGMENT-LOCAL. Every segment (one per
process/service hop) restarts at spanId=0/parentSpanId=-1, so a trace of 30
segments may contain only ~10 distinct spanIds. Cross-service linkage lives
only in each span's `refs` array. In this 7-span excerpt spanId 0 appears in
four different segments and spanId 1 in two -- keying spans by bare spanId
fabricates edges out of those collisions.
"""
from tt_harness.analyzer import DependencyAnalyzer

# Real segment ids from the captured trace.
SEG_PRESERVE = "0a14a37113834ed397506608f5dc3987.524.17869773164440004"
SEG_SECURITY = "352dab19b3a64925827a7d1a9ebeb659.462.17869773165050010"
SEG_ORDER = "8fac52c1813c48a89914993304952854.320.17869773165170036"
SEG_ORDER_OTHER = "2b443e8c421c4d82bea633b0830a3ac6.397.17869773165340024"
TRACE_ID = "0a14a37113834ed397506608f5dc3987.524.17869773164440005"


def _ref(parent_segment_id, parent_span_id):
    return [{
        "traceId": TRACE_ID,
        "parentSegmentId": parent_segment_id,
        "parentSpanId": parent_span_id,
        "type": "CROSS_PROCESS",
    }]


def _span(segment_id, span_id, parent_span_id, service, endpoint, start, end,
          is_error=False, refs=None):
    return {
        "traceId": TRACE_ID, "segmentId": segment_id,
        "spanId": span_id, "parentSpanId": parent_span_id,
        "serviceCode": service, "endpointName": endpoint,
        "startTime": start, "endTime": end, "isError": is_error,
        "refs": refs or [],
    }


# Verbatim excerpt: ts-preserve-service calls ts-security-service, which in turn
# calls ts-order-service and ts-order-other-service.
#
#   PRESERVE/0  Entry POST:/api/v1/preserveservice/preserve   [316444..318061]
#   PRESERVE/1  Exit  ->securityservice                       [316498..316580]
#     SECURITY/0  Entry (refs -> PRESERVE/1)                  [316505..316580]
#       SECURITY/1  Exit ->orderservice                       [316514..316530]
#         ORDER/0     Entry (refs -> SECURITY/1)              [316517..316530]
#       SECURITY/2  Exit ->orderOtherService                  [316531..316544]
#         ORDER_OTHER/0 Entry (refs -> SECURITY/2)            [316534..316543]
REAL_TRACE_EXCERPT = [
    _span(SEG_PRESERVE, 0, -1, "ts-preserve-service",
          "POST:/api/v1/preserveservice/preserve", 1786977316444, 1786977318061),
    _span(SEG_PRESERVE, 1, 0, "ts-preserve-service",
          "/api/v1/securityservice/securityConfigs/4d2a46c7", 1786977316498, 1786977316580),
    _span(SEG_SECURITY, 0, -1, "ts-security-service",
          "GET:/api/v1/securityservice/securityConfigs/{accountId}",
          1786977316505, 1786977316580, refs=_ref(SEG_PRESERVE, 1)),
    _span(SEG_SECURITY, 1, 0, "ts-security-service",
          "/api/v1/orderservice/order/security/4d2a46c7", 1786977316514, 1786977316530),
    _span(SEG_ORDER, 0, -1, "ts-order-service",
          "GET:/api/v1/orderservice/order/security/{checkDate}/{accountId}",
          1786977316517, 1786977316530, refs=_ref(SEG_SECURITY, 1)),
    _span(SEG_SECURITY, 2, 0, "ts-security-service",
          "/api/v1/orderOtherService/orderOther/security/4d2a46c7", 1786977316531, 1786977316544),
    _span(SEG_ORDER_OTHER, 0, -1, "ts-order-other-service",
          "GET:/api/v1/orderOtherService/orderOther/security/{checkDate}/{accountId}",
          1786977316534, 1786977316543, refs=_ref(SEG_SECURITY, 2)),
]


def _run(*steps):
    return type("Run", (), {"steps": list(steps)})()


def _step(spans, correlate=True, correlation_status="matched"):
    return type("Step", (), {
        "spans": spans, "correlate": correlate,
        "correlation_status": correlation_status,
    })()


def test_cross_segment_service_edges_come_from_refs_not_span_ids():
    analyzer = DependencyAnalyzer()
    service_edges, _ = analyzer.analyze([_run(_step(REAL_TRACE_EXCERPT))])

    # Exactly the three real cross-service hops in this excerpt.
    assert set(service_edges) == {
        ("ts-preserve-service", "ts-security-service"),
        ("ts-security-service", "ts-order-service"),
        ("ts-security-service", "ts-order-other-service"),
    }
    assert all(e.call_count == 1 for e in service_edges.values())


def test_no_edges_are_fabricated_from_span_id_collisions():
    """Regression guard for keying spans by bare spanId.

    spanId 0 lives in all four segments of this excerpt and spanId 1 in two;
    a flat {spanId: span} index invents edges between unrelated services.
    """
    analyzer = DependencyAnalyzer()
    service_edges, _ = analyzer.analyze([_run(_step(REAL_TRACE_EXCERPT))])

    # ts-order-service never calls ts-security-service (the arrow runs the other
    # way), and ts-order-other-service calls nobody -- both would appear if
    # spanIds were treated as trace-global.
    assert ("ts-order-service", "ts-security-service") not in service_edges
    assert not any(src == "ts-order-other-service" for src, _ in service_edges)
    # No self-edges: same-segment parent/child never crosses a service boundary.
    assert not any(src == dst for src, dst in service_edges)


def test_self_time_subtracts_children_across_and_within_segments():
    analyzer = DependencyAnalyzer()
    service_edges, endpoint_edges = analyzer.analyze([_run(_step(REAL_TRACE_EXCERPT))])

    # SECURITY/0 wall time is 316580-316505 = 75ms, but its two direct
    # same-segment children take 16ms and 13ms, so self-time is 46ms.
    assert service_edges[("ts-preserve-service", "ts-security-service")].durations_ms == [46.0]
    # ORDER/0 is a leaf: full 13ms.
    assert service_edges[("ts-security-service", "ts-order-service")].durations_ms == [13.0]
    # ORDER_OTHER/0 is a leaf: full 9ms.
    assert service_edges[("ts-security-service", "ts-order-other-service")].durations_ms == [9.0]

    # SECURITY/1 (Exit, 16ms wall) has ORDER/0 as a CROSS-SEGMENT child via
    # refs; subtracting it leaves 3ms. This is the case the old flat-spanId
    # walk could not see at all.
    ekey = ("GET:/api/v1/securityservice/securityConfigs/{accountId}",
            "/api/v1/orderservice/order/security/4d2a46c7")
    assert endpoint_edges[ekey].durations_ms == [3.0]

    # Same-segment parent/child records an endpoint edge but no service edge.
    pkey = ("POST:/api/v1/preserveservice/preserve",
            "/api/v1/securityservice/securityConfigs/4d2a46c7")
    assert pkey in endpoint_edges
    # PRESERVE/1 is 82ms wall minus its cross-segment child SECURITY/0 (75ms).
    assert endpoint_edges[pkey].durations_ms == [7.0]

    # One endpoint edge per non-root span (6 of the 7 spans have a parent).
    assert sum(e.call_count for e in endpoint_edges.values()) == 6


def test_trace_root_produces_no_incoming_edge():
    analyzer = DependencyAnalyzer()
    _, endpoint_edges = analyzer.analyze([_run(_step(REAL_TRACE_EXCERPT))])

    # PRESERVE/0 has parentSpanId == -1 and no refs: it is the trace root.
    assert not any(dst == "POST:/api/v1/preserveservice/preserve"
                   for _, dst in endpoint_edges)


def test_analyze_aggregates_across_multiple_runs_and_counts_errors():
    ok = [
        _span(SEG_PRESERVE, 0, -1, "ts-preserve-service", "/preserve", 0, 100),
        _span(SEG_PRESERVE, 1, 0, "ts-preserve-service", "/e", 0, 60),
        _span(SEG_ORDER, 0, -1, "ts-order-service", "/e", 0, 50,
              refs=_ref(SEG_PRESERVE, 1)),
    ]
    err = [
        _span(SEG_PRESERVE, 0, -1, "ts-preserve-service", "/preserve", 0, 200),
        _span(SEG_PRESERVE, 1, 0, "ts-preserve-service", "/e", 0, 90),
        _span(SEG_ORDER, 0, -1, "ts-order-service", "/e", 0, 80, is_error=True,
              refs=_ref(SEG_PRESERVE, 1)),
    ]

    analyzer = DependencyAnalyzer()
    service_edges, _ = analyzer.analyze([_run(_step(ok)), _run(_step(err))])

    edge = service_edges[("ts-preserve-service", "ts-order-service")]
    assert edge.call_count == 2
    assert edge.error_count == 1
    assert edge.avg_ms == 65.0
    assert edge.max_ms == 80.0


def test_analyze_skips_uncorrelated_and_failed_steps():
    # Each step below carries a real cross-service parent/child pair, so the
    # skip guard is what keeps them out of the graph -- removing it would make
    # service_edges non-empty and fail this test.
    def _pair(service):
        return [
            _span(SEG_PRESERVE, 0, -1, "ts-preserve-service", "/preserve", 0, 100),
            _span(SEG_PRESERVE, 1, 0, "ts-preserve-service", "/e", 0, 60),
            _span(SEG_ORDER, 0, -1, service, "/e", 0, 50, refs=_ref(SEG_PRESERVE, 1)),
        ]

    ok_step = _step(_pair("ts-order-service"))
    skip_step = _step(_pair("ts-food-service"), correlate=False,
                      correlation_status="not_applicable")
    failed_step = _step(_pair("ts-seat-service"), correlate=True,
                        correlation_status="failed")

    analyzer = DependencyAnalyzer()
    service_edges, _ = analyzer.analyze([_run(ok_step, skip_step, failed_step)])

    # Only the matched step contributes.
    assert set(service_edges) == {("ts-preserve-service", "ts-order-service")}
