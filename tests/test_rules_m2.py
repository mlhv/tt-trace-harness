"""hotspot_span, n_plus_one_sql, sequential_fanout, deep_chain."""

import pytest

from tests.factory import TRIPS_LEFT, ref, span, stub_trace, trips_left_trace
from tt_harness.rules import RULES, run_rules

PRESERVE = "{POST}/api/v1/preserveservice/preserve"


def _by_rule(run, rule):
    return [f for f in run.findings if f.rule == rule]


def _db(seg, span_id, parent, service, start, end, statement):
    db = span(seg, span_id, parent, service, "Mysql/JDBI/PreparedStatement/executeQuery", start, end, kind="Exit", layer="Database", peer="tsdb-mysql-leader:3306")
    db["tags"] = [{"key": "db.type", "value": "sql"}, {"key": "db.instance", "value": "ts"}, {"key": "db.statement", "value": statement}]
    return db


def _hot_trace(trace_id, hot_self=80, root_ms=100):
    """Root calls one service whose entry span burns `hot_self` ms itself."""
    return {
        "traceId": trace_id,
        "spans": [
            span("T", 0, -1, "ts-travel-service", TRIPS_LEFT, 0, root_ms),
            span("T", 1, 0, "ts-travel-service", "/api/v1/routeservice/routes/{id}", 5, 5 + hot_self + 4, kind="Exit"),
            span("R", 0, -1, "ts-route-service", "{GET}/api/v1/routeservice/routes/{routeId}", 6, 6 + hot_self, refs=ref("T", 1)),
        ],
    }


def _balanced_trace(trace_id):
    """Two 35 ms callees under a 100 ms root: no span reaches 40 % self time."""
    spans = [span("T", 0, -1, "ts-travel-service", TRIPS_LEFT, 0, 100)]
    for index, start in enumerate((10, 55)):
        spans.append(span("T", index + 1, 0, "ts-travel-service", f"/x{index}", start, start + 39, kind="Exit"))
        spans.append(span(f"X{index}", 0, -1, f"svc-{index}", f"{{GET}}/x{index}", start + 2, start + 37, refs=ref("T", index + 1)))
    return {"traceId": trace_id, "spans": spans}


def _sql_trace(trace_id, repeats, statement="select s.* from route_stations s where s.route_id=?"):
    spans = [span("R", 0, -1, "ts-route-service", "{POST}/api/v1/routeservice/routes/byIds", 0, 100)]
    spans += [_db("R", i + 1, 0, "ts-route-service", 10 + 5 * i, 13 + 5 * i, statement) for i in range(repeats)]
    return {"traceId": trace_id, "spans": spans}


def _fanout_trace(trace_id, overlap=False, callees=("ts-contacts-service", "ts-travel-service", "ts-security-service")):
    spans = [span("P", 0, -1, "ts-preserve-service", PRESERVE, 0, 200)]
    for index, service in enumerate(callees):
        start = 10 if overlap else 10 + 50 * index
        spans.append(span("P", index + 1, 0, "ts-preserve-service", f"/api/v1/{service}/x", start, start + 40, kind="Exit"))
        spans.append(span(f"C{index}", 0, -1, service, f"{{GET}}/api/v1/{service}/x", start + 1, start + 39, refs=ref("P", index + 1)))
    return {"traceId": trace_id, "spans": spans}


def _chain_trace(trace_id, services):
    """A straight call chain root → s1 → s2 → …, each hop one exit + one entry."""
    spans = [span("S0", 0, -1, services[0], "{POST}/api/v1/a/start", 0, 1000)]
    for index, service in enumerate(services[1:], start=1):
        parent_seg = f"S{index - 1}"
        spans.append(span(parent_seg, 1, 0, services[index - 1], f"/api/v1/{service}", index * 10, 1000 - index * 10, kind="Exit"))
        spans.append(span(f"S{index}", 0, -1, service, f"{{GET}}/api/v1/{service}", index * 10 + 1, 999 - index * 10, refs=ref(parent_seg, 1)))
    return {"traceId": trace_id, "spans": spans}


def test_registry_has_seven_rules_in_spec_order():
    assert list(RULES) == [
        "n_plus_one_remote",
        "cyclic_calls",
        "broken_propagation",
        "hotspot_span",
        "n_plus_one_sql",
        "sequential_fanout",
        "deep_chain",
    ]


# hotspot_span ----------------------------------------------------------------------------------


def test_hotspot_reports_span_with_dominant_self_time():
    run = run_rules([_hot_trace("h1", hot_self=80), _hot_trace("h2", hot_self=60)])
    [finding] = _by_rule(run, "hotspot_span")
    assert finding.root == "POST:/api/v1/travelservice/trips/left"
    assert (finding.caller_service, finding.caller_segment_entry) == ("ts-route-service", "GET:/api/v1/routeservice/routes/{routeId}")
    assert finding.share_median == pytest.approx(0.7)  # median of 0.8 and 0.6 self time / 100 ms root
    assert finding.prevalence == 1.0 and finding.trace_count == 2
    assert finding.highlights["h1"] == ["R:0"]


def test_hotspot_below_threshold_or_prevalence_is_not_reported():
    assert _by_rule(run_rules([_balanced_trace("b1")]), "hotspot_span") == []
    raw = [_hot_trace("h1")] + [_balanced_trace(f"b{i}") for i in range(3)]
    assert _by_rule(run_rules(raw), "hotspot_span") == []


def test_hotspot_ignores_single_span_stubs():
    raw = [_hot_trace("h1"), stub_trace("s1", path="/api/v1/travelservice/trips/left"), stub_trace("s2", path="POST:/api/v1/travelservice/trips/left")]
    [finding] = _by_rule(run_rules(raw), "hotspot_span")
    assert finding.trace_count == 1


# n_plus_one_sql --------------------------------------------------------------------------------


def test_repeated_statement_in_one_segment_is_sql_n_plus_one():
    run = run_rules([_sql_trace("q1", 4), _sql_trace("q2", 6)])
    [finding] = _by_rule(run, "n_plus_one_sql")
    assert (finding.caller_service, finding.caller_segment_entry) == ("ts-route-service", "POST:/api/v1/routeservice/routes/byIds")
    assert finding.callee_service == "tsdb-mysql-leader"
    assert finding.callee_endpoint == "SQL on route_stations"
    assert finding.detail == "select s.* from route_stations s where s.route_id=?"
    assert finding.k_median == 5.0
    assert finding.share_median == pytest.approx(0.15)  # median of 4×3 ms and 6×3 ms over 100 ms
    assert len(finding.highlights["q1"]) == 4


def test_sql_below_repeats_or_empty_statement_is_ignored():
    assert _by_rule(run_rules([_sql_trace("q1", 2)]), "n_plus_one_sql") == []
    assert _by_rule(run_rules([_sql_trace("q1", 5, statement="")]), "n_plus_one_sql") == []


# sequential_fanout -----------------------------------------------------------------------------


def test_sequential_calls_to_three_distinct_services_are_a_fanout():
    run = run_rules([_fanout_trace("f1"), _fanout_trace("f2")])
    [finding] = _by_rule(run, "sequential_fanout")
    assert (finding.caller_service, finding.caller_segment_entry) == ("ts-preserve-service", "POST:/api/v1/preserveservice/preserve")
    assert finding.detail == "ts-contacts-service → ts-travel-service → ts-security-service"
    assert finding.k_median == 3.0
    assert finding.share_median == pytest.approx(0.6)  # 3 × 40 ms of a 200 ms root
    assert len(finding.highlights["f1"]) == 6  # each exit and its remote entry


def test_overlapping_or_too_few_callees_is_not_a_fanout():
    assert _by_rule(run_rules([_fanout_trace("f1", overlap=True)]), "sequential_fanout") == []
    assert _by_rule(run_rules([_fanout_trace("f1", callees=("a", "b"))]), "sequential_fanout") == []


def test_repeated_calls_to_one_callee_are_not_a_fanout():
    assert _by_rule(run_rules([trips_left_trace("t", 6)]), "sequential_fanout") == []


# deep_chain ------------------------------------------------------------------------------------


def test_chain_longer_than_max_hops_is_reported():
    services = [f"svc-{i}" for i in range(7)]  # 6 hops
    run = run_rules([_chain_trace("d1", services), _chain_trace("d2", services)])
    [finding] = _by_rule(run, "deep_chain")
    assert finding.root == "POST:/api/v1/a/start"
    assert finding.k_median == 6.0
    assert finding.detail == " → ".join(services)
    assert (finding.caller_service, finding.callee_service) == ("svc-0", "svc-6")
    assert "S6:0" in finding.highlights["d1"]


def test_chain_at_max_hops_is_not_reported():
    assert _by_rule(run_rules([_chain_trace("d1", [f"svc-{i}" for i in range(6)])]), "deep_chain") == []


def test_rule_params_override_defaults():
    services = [f"svc-{i}" for i in range(4)]
    run = run_rules([_chain_trace("d1", services)], {"deep_chain": {"max_hops": 2}})
    assert [f.k_median for f in _by_rule(run, "deep_chain")] == [3.0]
