from tests.factory import span, stub_trace, trips_left_trace
from tt_harness.report import render_comparison, render_run_report
from tt_harness.rules import run_rules


def _report(raw, **kwargs):
    run = run_rules(raw)
    return run, render_run_report({"Run": "demo/users-005/r001", "Users": 5}, raw, run.findings, **kwargs)


def test_run_report_shows_findings_tree_and_highlights():
    raw = [trips_left_trace(f"t{i}", 4) for i in range(3)] + [stub_trace("s0"), stub_trace("s1")]
    run, html = _report(raw)
    [nplus1] = [f for f in run.findings if f.rule == "n_plus_one_remote"]
    assert html.startswith("<!doctype html>")
    assert f"id='finding-{nplus1.id}'" in html
    assert "class='row hl'" in html
    assert "×4 calls to /api/v1/seatservice/seats/left_tickets" in html
    assert "broken_propagation" in html
    assert "demo/users-005/r001" in html
    assert html.count("<details") == html.count("</details>")
    assert "<script" not in html and "http://" not in html and "https://" not in html


def test_report_with_zero_traces():
    _, html = _report([])
    assert "0 traces" in html
    assert "No antipatterns found." in html


def test_report_shows_rule_errors():
    _, html = _report([], errors={"boom": "RuntimeError: kaput"})
    assert "RuntimeError: kaput" in html


def test_report_escapes_span_text():
    raw = [{"traceId": "x", "spans": [span("A", 0, -1, "svc<b>", "{GET}/x<script>alert(1)</script>", 0, 10)]}]
    _, html = _report(raw)
    assert "<script>" not in html and "svc<b>" not in html
    assert "&lt;script&gt;" in html


def test_comparison_page_lists_levels_slope_and_chart():
    rows = [
        {
            "id": "abc123abc123",
            "rule": "n_plus_one_remote",
            "root": "POST:/api/v1/travelservice/trips/left",
            "caller": "ts-travel-service",
            "callee": "ts-seat-service /api/v1/seatservice/seats/left_tickets",
            "detail": "",
            "runs": 2,
            "shares": {"5": 0.4, "15": 0.6},
            "load_slope": 0.02,
        }
    ]
    html = render_comparison("Trace diagnostics — demo", rows, "<svg id='chart'></svg>")
    assert "5 users" in html and "15 users" in html
    assert "0.0200" in html
    assert "<svg id='chart'></svg>" in html
    assert "No findings" in render_comparison("t", [], "")


def test_comparison_shows_detail_to_distinguish_rows():
    base = {"rule": "cycle", "root": "r", "caller": "a", "callee": "b", "runs": 1, "shares": {"5": 0.1}, "load_slope": None}
    rows = [{**base, "id": "1", "detail": "a>b>a via /x"}, {**base, "id": "2", "detail": "a>b>c>a via /y"}]
    html = render_comparison("t", rows, "")
    assert "<div class='sub'>a&gt;b&gt;a via /x</div>" in html
    assert "a&gt;b&gt;c&gt;a via /y" in html


def test_comparison_shows_each_rules_own_measure_and_prevalence():
    rows = [
        {
            "id": "1",
            "rule": "broken_propagation",
            "root": "/api/v1/preserveservice/preserve",
            "caller": "ts-gateway-service",
            "callee": "ts-preserve-service",
            "detail": "",
            "runs": 2,
            "shares": {},
            "measure": "stub fraction",
            "values": {"5": 0.45, "15": 0.36},
            "prevalence": {"5": 0.45, "15": 0.36},
            "load_slope": -0.009,
        },
        {
            "id": "2",
            "rule": "n_plus_one_remote",
            "root": "r",
            "caller": "a",
            "callee": "b",
            "detail": "",
            "runs": 1,
            "shares": {"5": 0.77},
            "measure": "latency share",
            "values": {"5": 0.77},
            "prevalence": {"5": 0.51},
            "load_slope": None,
        },
    ]
    html = render_comparison("t", rows, "")
    assert "stub fraction" in html and "0.45" in html and "0.36" in html
    assert "prev 0.51" in html  # prevalence shown under a share
    assert "prev 0.45" not in html  # not repeated when the measure already is the prevalence
    assert ">–<" in html  # missing level stays a dash


def test_rule_measures_cover_every_rule():
    from tt_harness.findings import rule_measure
    from tt_harness.rules import RULES

    assert {rule_measure(rule)[1] for rule in RULES} <= {"share_median", "prevalence", "k_median"}
    assert rule_measure("broken_propagation") == ("stub fraction", "prevalence")
    assert rule_measure("deep_chain") == ("hops", "k_median")
    assert rule_measure("hotspot_span") == ("self-time share", "share_median")


def test_root_header_says_how_many_traces_are_stubs_and_k_is_an_integer():
    raw = [trips_left_trace(f"t{i}", 4) for i in range(3)] + [stub_trace(f"s{i}", path="{POST}/api/v1/travelservice/trips/left") for i in range(2)]
    _, html = _report(raw)
    assert "5 traces (2 single-span stubs)" in html
    assert "<td class='num'>4</td>" in html and "4.0" not in html


def test_findings_table_names_the_hot_span():
    hot = {
        "traceId": "h1",
        "spans": [
            span("T", 0, -1, "ts-travel-service", "{POST}/api/v1/travelservice/trips/left", 0, 100),
            span("T", 1, 0, "ts-travel-service", "/api/v1/routeservice/routes", 5, 95, kind="Exit"),
        ],
    }
    _, html = _report([hot])
    assert "ts-travel-service /api/v1/routeservice/routes" in html
