import csv
import json
import os

from tt_harness.export import write_runs_jsonl, write_runs_summary_csv, write_dependency_csv
from tt_harness.workflows.base import RunResult, StepResult
from tt_harness.analyzer import EdgeStats


def _run(run_id, success, failure_reason=None):
    steps = [
        StepResult("login", "/api/v1/users/login", start=1000.0, end=1000.5, success=True,
                   error=None, outputs={"token": "t"}, correlate=True,
                   correlation_status="matched", trace_id="tr1", spans=[{"spanId": 0}]),
    ]
    if not success:
        steps.append(StepResult("preserve", "/api/v1/preserveservice/preserve", start=1001.0, end=1001.2,
                                 success=False, error="boom", outputs={}, correlate=True))
    return RunResult(run_id=run_id, workflow="preserve", inputs={"from": "Su Zhou", "to": "Shang Hai"},
                      success=success, steps=steps, failure_reason=failure_reason)


def test_write_runs_jsonl_writes_one_json_object_per_line(tmp_path):
    path = tmp_path / "runs.jsonl"
    runs = [_run("r1", True), _run("r2", False, "preserve failed: boom")]

    write_runs_jsonl(runs, str(path))

    lines = path.read_text().strip().split("\n")
    assert len(lines) == 2
    rec1 = json.loads(lines[0])
    assert rec1["run_id"] == "r1"
    assert rec1["success"] is True
    assert rec1["steps"][0]["step_name"] == "login"
    assert rec1["steps"][0]["correlation_status"] == "matched"
    rec2 = json.loads(lines[1])
    assert rec2["success"] is False
    assert rec2["failure_reason"] == "preserve failed: boom"


def test_write_runs_summary_csv_has_expected_columns(tmp_path):
    path = tmp_path / "runs_summary.csv"
    runs = [_run("r1", True), _run("r2", False, "preserve failed: boom")]

    write_runs_summary_csv(runs, str(path))

    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["run_id"] == "r1"
    assert rows[0]["workflow"] == "preserve"
    assert float(rows[0]["total_duration_ms"]) == 500.0  # 1000.5 - 1000.0 in ms
    assert json.loads(rows[0]["step_durations"]) == {"login": 500.0}
    assert rows[0]["success"] == "True"
    assert rows[1]["failure_reason"] == "preserve failed: boom"


def test_write_dependency_csv_has_expected_columns_and_rounding(tmp_path):
    path = tmp_path / "deps.csv"
    edges = {
        ("ts-gateway-service", "ts-auth-service"): EdgeStats(
            source="ts-gateway-service", target="ts-auth-service",
            call_count=2, durations_ms=[10.0, 20.0], error_count=1,
        ),
    }

    write_dependency_csv(edges, str(path))

    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["source_service"] == "ts-gateway-service"
    assert rows[0]["target_service"] == "ts-auth-service"
    assert rows[0]["call_count"] == "2"
    assert float(rows[0]["avg_ms"]) == 15.0
    assert rows[0]["error_count"] == "1"
