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
