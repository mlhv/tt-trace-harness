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
