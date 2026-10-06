from tests.factory import TRIPS_LEFT, ref, span, stub_trace, trips_left_trace
from tt_harness.rules import RULES, run_rules


def _by_rule(run, rule):
    return [f for f in run.findings if f.rule == rule]


def _seat_order_seat(trace_id):
    return {
        "traceId": trace_id,
        "spans": [
            span("T", 0, -1, "ts-travel-service", TRIPS_LEFT, 0, 100),
            span("T", 1, 0, "ts-travel-service", "/seat", 5, 90, kind="Exit"),
            span("S", 0, -1, "ts-seat-service", "{POST}/seat", 6, 89, refs=ref("T", 1)),
            span("S", 1, 0, "ts-seat-service", "/order", 10, 80, kind="Exit"),
            span("O", 0, -1, "ts-order-service", "{POST}/order", 11, 79, refs=ref("S", 1)),
            span("O", 1, 0, "ts-order-service", "/seat2", 20, 60, kind="Exit"),
            span("S2", 0, -1, "ts-seat-service", "{POST}/seat2", 21, 59, refs=ref("O", 1)),
        ],
    }


def test_registry_order():
    assert list(RULES) == ["n_plus_one_remote", "cyclic_calls", "broken_propagation"]


def test_cycle_is_reported_with_signature_and_hops():
    run = run_rules([_seat_order_seat("c1"), _seat_order_seat("c2")])
    [finding] = _by_rule(run, "cyclic_calls")
    assert finding.detail == "ts-seat-service → ts-order-service → ts-seat-service"
    assert (finding.caller_service, finding.callee_service) == ("ts-seat-service", "ts-order-service")
    assert finding.k_median == 2.0
    assert finding.share_median is None
    assert finding.evidence == ["c1", "c2"]
    assert {"S:0", "O:0", "S2:0"} <= set(finding.highlights["c1"])


def test_chain_without_revisit_is_not_a_cycle():
    assert _by_rule(run_rules([trips_left_trace("t", 4)]), "cyclic_calls") == []


def test_cycle_below_prevalence_is_not_reported():
    raw = [_seat_order_seat("c1")] + [trips_left_trace(f"t{i}", 1) for i in range(3)]
    assert _by_rule(run_rules(raw), "cyclic_calls") == []


def test_gateway_stubs_next_to_full_traces_are_broken_propagation():
    raw = [stub_trace(f"s{i}") for i in range(4)] + [trips_left_trace(f"t{i}", 1) for i in range(4)]
    [finding] = _by_rule(run_rules(raw), "broken_propagation")
    assert finding.root == "/api/v1/travelservice/trips/left"
    assert (finding.caller_service, finding.callee_service) == ("ts-gateway-service", "ts-travel-service")
    assert finding.prevalence == 0.5
    assert finding.trace_count == 8
    assert finding.evidence == ["s0", "s1", "s2"]
    assert finding.highlights["s0"] == ["G:0"]


def test_no_broken_propagation_without_both_kinds_or_below_threshold():
    assert _by_rule(run_rules([stub_trace(f"s{i}") for i in range(3)]), "broken_propagation") == []
    assert _by_rule(run_rules([trips_left_trace("t", 1)]), "broken_propagation") == []
    slow = [stub_trace("slow", ms=50)] + [trips_left_trace("t", 1)]
    assert _by_rule(run_rules(slow), "broken_propagation") == []
    rare = [stub_trace("s")] + [trips_left_trace(f"t{i}", 1) for i in range(10)]
    assert _by_rule(run_rules(rare), "broken_propagation") == []
