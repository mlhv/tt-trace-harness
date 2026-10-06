import pytest

from tt_harness.findings import (
    Finding,
    endpoint_path,
    finding_id,
    interval_union_ms,
    load_slope,
    normalize_endpoint,
    pick_evidence,
)


def test_normalize_endpoint_forms():
    expected = "POST:/api/v1/travelservice/trips/left"
    assert normalize_endpoint("{POST}/api/v1/travelservice/trips/left") == expected
    assert normalize_endpoint("POST:/api/v1/travelservice/trips/left") == expected
    assert normalize_endpoint("/api/v1/travelservice/trips/left") == "/api/v1/travelservice/trips/left"
    assert normalize_endpoint("http://ts-seat-service:18898/api/v1/seatservice/seats/left_tickets?x=1") == "/api/v1/seatservice/seats/left_tickets"
    assert normalize_endpoint(None) == ""


def test_normalize_replaces_id_segments():
    assert normalize_endpoint("{GET}/orders/12345") == "GET:/orders/{id}"
    assert normalize_endpoint("/a/3fa85f64-5717-4562-b3fc-2c963f66afa6/b") == "/a/{id}/b"
    assert normalize_endpoint("/api/v1/securityservice/securityConfigs/4d2a46c7") == "/api/v1/securityservice/securityConfigs/{id}"
    assert normalize_endpoint("/foods/2026-10-07/shanghai/suzhou/D1345") == "/foods/{id}/shanghai/suzhou/D1345"
    assert normalize_endpoint("/api/v1/deadbeef/left") == "/api/v1/deadbeef/left"
    assert endpoint_path("{GET}/orders/7") == "/orders/{id}"


def test_interval_union_merges_overlaps():
    assert interval_union_ms([(0, 10), (5, 15), (20, 25)]) == 20.0
    assert interval_union_ms([]) == 0.0


def test_pick_evidence_returns_median_max_min():
    assert pick_evidence([("a", 0.1), ("b", 0.5), ("c", 0.3)]) == ["c", "b", "a"]
    assert pick_evidence([("only", 0.2)]) == ["only"]


def test_load_slope():
    assert load_slope([(10, 0.2), (20, 0.4)]) == pytest.approx(0.02)
    assert load_slope([(10, 0.2), (10, 0.4)]) is None
    assert load_slope([(None, 0.2), (20, None)]) is None


def test_finding_id_is_stable_and_round_trips():
    finding = Finding(
        rule="n_plus_one_remote",
        root="POST:/r",
        caller_service="a",
        caller_segment_entry="POST:/r",
        callee_service="b",
        callee_endpoint="/x",
        detail="",
        k_median=4.0,
        share_median=0.5,
        prevalence=1.0,
        trace_count=3,
        evidence=["t1"],
        highlights={"t1": ["A:1"]},
    )
    assert finding.id == finding_id("n_plus_one_remote", "POST:/r", "a", "POST:/r", "b", "/x", "")
    assert len(finding.id) == 12
    assert Finding.from_dict(finding.to_dict()) == finding
