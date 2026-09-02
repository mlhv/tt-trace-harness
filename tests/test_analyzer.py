from tt_harness.analyzer import DependencyAnalyzer


def _span(span_id, parent_span_id, service, endpoint, start, end, is_error=False):
    return {
        "spanId": span_id, "parentSpanId": parent_span_id,
        "serviceCode": service, "endpointName": endpoint,
        "startTime": start, "endTime": end, "isError": is_error,
    }


def test_analyze_builds_service_and_endpoint_edges_with_self_time():
    # ts-auth-service span: 100ms wall time, but its child (ts-user-service) takes 40ms,
    # so ts-auth-service's self-time on this hop is 60ms.
    spans = [
        _span(0, -1, "ts-gateway-service", "/api/v1/users/login", 0, 120),
        _span(1, 0, "ts-auth-service", "/api/v1/users/login", 10, 110),
        _span(2, 1, "ts-user-service", "/api/v1/users/findByUsername", 30, 70),
    ]
    run = type("Run", (), {"steps": [
        type("Step", (), {"spans": spans, "correlate": True, "correlation_status": "matched"})()
    ]})()

    analyzer = DependencyAnalyzer()
    service_edges, endpoint_edges = analyzer.analyze([run])

    key = ("ts-gateway-service", "ts-auth-service")
    assert key in service_edges
    assert service_edges[key].call_count == 1
    assert service_edges[key].durations_ms == [60.0]  # 110-10 self-time (no grandchild subtracted here, auth->user is the next edge)

    key2 = ("ts-auth-service", "ts-user-service")
    assert service_edges[key2].call_count == 1
    assert service_edges[key2].durations_ms == [40.0]

    # endpoint-level uses endpointName instead of serviceCode
    ekey = ("/api/v1/users/login", "/api/v1/users/findByUsername")
    assert ekey in endpoint_edges
    assert endpoint_edges[ekey].call_count == 1


def test_analyze_aggregates_across_multiple_runs_and_counts_errors():
    spans_ok = [
        _span(0, -1, "ts-gateway-service", "/e", 0, 100),
        _span(1, 0, "ts-auth-service", "/e", 0, 50, is_error=False),
    ]
    spans_err = [
        _span(0, -1, "ts-gateway-service", "/e", 0, 200),
        _span(1, 0, "ts-auth-service", "/e", 0, 80, is_error=True),
    ]
    run1 = type("Run", (), {"steps": [type("Step", (), {"spans": spans_ok, "correlate": True, "correlation_status": "matched"})()]})()
    run2 = type("Run", (), {"steps": [type("Step", (), {"spans": spans_err, "correlate": True, "correlation_status": "matched"})()]})()

    analyzer = DependencyAnalyzer()
    service_edges, _ = analyzer.analyze([run1, run2])

    key = ("ts-gateway-service", "ts-auth-service")
    edge = service_edges[key]
    assert edge.call_count == 2
    assert edge.error_count == 1
    assert edge.avg_ms == 65.0
    assert edge.max_ms == 80.0


def test_analyze_skips_uncorrelated_and_failed_steps():
    spans = [_span(0, -1, "ts-gateway-service", "/e", 0, 100)]
    ok_step = type("Step", (), {"spans": spans, "correlate": True, "correlation_status": "matched"})()
    skip_step = type("Step", (), {"spans": [], "correlate": False, "correlation_status": "not_applicable"})()
    failed_step = type("Step", (), {"spans": [], "correlate": True, "correlation_status": "failed"})()
    run = type("Run", (), {"steps": [ok_step, skip_step, failed_step]})()

    analyzer = DependencyAnalyzer()
    service_edges, _ = analyzer.analyze([run])

    assert service_edges == {}  # single span, no parent-child pair -> no edges
