import json
import os

from tt_harness.cli import build_arg_parser, run_command
from tt_harness.workflows.base import RunResult, StepResult


def test_arg_parser_defaults():
    parser = build_arg_parser()
    args = parser.parse_args(["run", "preserve"])
    assert args.workflow == "preserve"
    assert args.count == 1
    assert args.gateway_endpoint == "http://localhost:18888"
    assert args.sw_endpoint == "http://localhost:12800/graphql"


def test_arg_parser_rejects_unknown_workflow():
    parser = build_arg_parser()
    try:
        parser.parse_args(["run", "not-a-workflow"])
        assert False, "expected SystemExit"
    except SystemExit:
        pass


class FakeRunner:
    def __init__(self, results):
        self._results = results
        self.run_many_calls = []

    def run_many(self, definition, count, run_id_fn=None, on_result=None):
        self.run_many_calls.append((definition.name, count))
        for i, result in enumerate(self._results):
            if on_result is not None:
                on_result(i, result)
        return self._results


def test_run_command_writes_all_four_output_files(tmp_path):
    parser = build_arg_parser()
    args = parser.parse_args(["run", "preserve", "--count", "2", "--out-dir", str(tmp_path)])

    step = StepResult("login", "/api/v1/users/login", 0.0, 0.1, True, None, {}, True,
                       correlation_status="matched", trace_id="t1",
                       spans=[{"segmentId": "seg-1", "spanId": 0, "parentSpanId": -1,
                               "serviceCode": "ts-gateway-service", "endpointName": "/e",
                               "startTime": 0, "endTime": 100, "isError": False, "refs": []}])
    results = [
        RunResult("r1", "preserve", {}, True, [step], None),
        RunResult("r2", "preserve", {}, False, [], "login failed"),
    ]
    runner = FakeRunner(results)

    exit_code = run_command(args, runner=runner)

    assert exit_code == 0
    assert runner.run_many_calls == [("preserve", 2)]
    assert (tmp_path / "runs.jsonl").exists()
    assert (tmp_path / "runs_summary.csv").exists()
    assert (tmp_path / "dependencies_service.csv").exists()
    assert (tmp_path / "dependencies_endpoint.csv").exists()

    lines = (tmp_path / "runs.jsonl").read_text().strip().split("\n")
    assert len(lines) == 2
