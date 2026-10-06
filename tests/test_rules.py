import pytest

from tests.factory import SEAT_PATH, TRIPS_LEFT, ref, span, trips_left_trace
from tt_harness import rules
from tt_harness.rules import run_rules
from tt_harness.rules.base import build_traces, group_by_root

ROOT = "POST:/api/v1/travelservice/trips/left"


def _nplus1(run):
    return [f for f in run.findings if f.rule == "n_plus_one_remote"]


def test_build_traces_groups_by_normalized_root_and_skips_malformed():
    broken = trips_left_trace("t2", 1)
    del broken["spans"][1]["endTime"]
    traces, skipped = build_traces([trips_left_trace("t1", 1), broken])
    assert skipped == 1
    assert list(group_by_root(traces)) == [ROOT]
    assert traces[0].duration_ms == 20.0


def test_flags_repeated_exits_with_k_share_and_evidence():
    run = run_rules([trips_left_trace(f"t{i}", 4) for i in range(4)])
    [finding] = _nplus1(run)
    assert finding.root == ROOT
    assert (finding.caller_service, finding.caller_segment_entry) == ("ts-travel-service", ROOT)
    assert (finding.callee_service, finding.callee_endpoint) == ("ts-seat-service", SEAT_PATH)
    assert finding.k_median == 4.0
    assert finding.share_median == pytest.approx(32 / 50)
    assert (finding.prevalence, finding.trace_count) == (1.0, 4)
    assert finding.evidence == ["t1", "t3", "t0"]
    assert set(finding.highlights) == {"t1", "t3", "t0"}
    assert {"T:1", "S0:0"} <= set(finding.highlights["t1"])


def test_below_min_repeats_is_not_flagged():
    assert _nplus1(run_rules([trips_left_trace(f"t{i}", 2) for i in range(3)])) == []


def test_prevalence_threshold():
    raw = [trips_left_trace("hit", 4)] + [trips_left_trace(f"m{i}", 1) for i in range(3)]
    assert _nplus1(run_rules(raw)) == []
    [finding] = _nplus1(run_rules(raw, {"n_plus_one_remote": {"min_prevalence": 0.2}}))
    assert finding.prevalence == 0.25


def test_parallel_exits_share_is_interval_union():
    run = run_rules([trips_left_trace("p", 5, parallel=True, call_ms=30, root_ms=40)])
    [finding] = _nplus1(run)
    assert finding.share_median == pytest.approx(0.75)


def test_cross_thread_exits_attribute_to_parent_segment():
    spans = [
        span("T", 0, -1, "ts-travel-service", TRIPS_LEFT, 0, 100),
        span("W", 0, -1, "ts-travel-service", "Thread/run", 5, 90, kind="Local", layer="Unknown", refs=ref("T", 0, kind="CROSS_THREAD")),
    ]
    for i in range(3):
        spans.append(span("W", i + 1, 0, "ts-travel-service", SEAT_PATH, 10 + i * 20, 25 + i * 20, kind="Exit", peer="ts-seat-service:18898"))
    [finding] = _nplus1(run_rules([{"traceId": "x", "spans": spans}]))
    assert finding.caller_segment_entry == ROOT
    assert finding.callee_service == "ts-seat-service"


def test_repeats_split_across_caller_segments_are_not_pooled():
    """Root issues 3 exits to ts-seat-service, each in its own segment.
    Each seat segment then issues 1 exit to ts-order-service.
    Should find exactly 1 n_plus_one_remote: root -> ts-seat-service (3 repeats).
    Should NOT pool the 3 segment exits as a second finding.
    """
    SEAT_PATH_NORMALIZED = "{POST}" + SEAT_PATH
    ORDER_PATH = "/api/v1/orderservice/x"
    spans = [
        # Root segment: ts-travel-service, issues 3 exits to ts-seat-service
        span("T", 0, -1, "ts-travel-service", TRIPS_LEFT, 0, 100),
        # 3 exits from root to ts-seat-service, each landing in different segment
        span("T", 1, 0, "ts-travel-service", SEAT_PATH, 10, 20, kind="Exit", peer="ts-seat-service:18898"),
        span("T", 2, 0, "ts-travel-service", SEAT_PATH, 30, 40, kind="Exit", peer="ts-seat-service:18898"),
        span("T", 3, 0, "ts-travel-service", SEAT_PATH, 50, 60, kind="Exit", peer="ts-seat-service:18898"),
    ]
    # Seat service entry segments (3 of them)
    for i in range(3):
        seg_id = f"S{i}"
        exit_start = 10 + i * 20
        exit_end = exit_start + 10
        # Entry span for this seat segment
        spans.append(span(seg_id, 0, -1, "ts-seat-service", SEAT_PATH_NORMALIZED, exit_start + 1, exit_end - 1, refs=ref("T", i + 1)))
        # Exit span from this seat segment to ts-order-service (only 1, not multiple)
        spans.append(span(seg_id, 1, 0, "ts-seat-service", ORDER_PATH, exit_start + 2, exit_end - 2, kind="Exit", peer="ts-order-service:9000"))
    # Order service entry segments (3 of them, one for each order call)
    for i in range(3):
        seg_id = f"O{i}"
        exit_start = 10 + i * 20
        exit_end = exit_start + 10
        spans.append(span(seg_id, 0, -1, "ts-order-service", ORDER_PATH, exit_start + 3, exit_end - 3, refs=ref(f"S{i}", 1)))

    run = run_rules([{"traceId": "split", "spans": spans}])
    findings = _nplus1(run)

    # Should find exactly 1 finding: root -> ts-seat-service (3 repeats)
    assert len(findings) == 1
    [finding] = findings
    assert finding.root == ROOT
    assert finding.caller_service == "ts-travel-service"
    assert finding.caller_segment_entry == ROOT
    assert finding.callee_service == "ts-seat-service"
    assert finding.callee_endpoint == SEAT_PATH
    assert finding.k_median == 3.0  # 3 exits from root


def test_rule_error_is_isolated(monkeypatch):
    def boom(traces, params):
        raise RuntimeError("kaput")

    monkeypatch.setitem(rules.RULES, "boom", boom)
    run = run_rules([trips_left_trace(f"t{i}", 4) for i in range(2)])
    assert run.errors == {"boom": "RuntimeError: kaput"}
    assert len(_nplus1(run)) == 1


def test_disabled_and_unknown_rules():
    raw = [trips_left_trace(f"t{i}", 4) for i in range(2)]
    assert _nplus1(run_rules(raw, {"n_plus_one_remote": {"enabled": False}})) == []
    with pytest.raises(ValueError, match="unknown rules: nope"):
        run_rules(raw, {"nope": {}})


def test_no_traces_gives_no_findings():
    run = run_rules([])
    assert (run.traces, run.findings, run.errors, run.skipped_spans) == ([], [], {}, 0)


def test_share_denominator_covers_async_root_shorter_than_children():
    raw = [trips_left_trace(f"t{i}", 4, root_ms=1) for i in range(3)]
    [finding] = _nplus1(run_rules(raw))
    # exits cover 4 x 8 ms; trace spans 0..43 ms (last exit ends at 43)
    assert finding.share_median < 1.0
    assert finding.share_median == pytest.approx(32 / 43)


def test_build_traces_skips_trace_whose_tree_cannot_be_built(monkeypatch):
    from tt_harness.rules import base

    real = base.SpanTree

    def flaky(spans):
        if any(s["traceId"] == "bad" for s in spans):
            raise ValueError("boom")
        return real(spans)

    good, bad = trips_left_trace("good", 1), trips_left_trace("bad", 1)
    for s in bad["spans"]:
        s["traceId"] = "bad"
    monkeypatch.setattr(base, "SpanTree", flaky)
    traces, skipped = build_traces([good, bad])
    assert [t.trace_id for t in traces] == ["good"]
    assert skipped == len(bad["spans"])
