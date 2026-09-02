import argparse
import json
import os
import sys
import uuid

from tt_harness.analyzer import DependencyAnalyzer
from tt_harness.client_gateway import GatewayClient
from tt_harness.client_sw import SkyWalkingClient
from tt_harness.correlator import TraceCorrelator
from tt_harness.export import run_record, write_dependency_csv, write_runs_summary_csv
from tt_harness.runner import WorkflowRunner
from tt_harness.workflows.preserve import PRESERVE_WORKFLOW

WORKFLOWS = {"preserve": PRESERVE_WORKFLOW}


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tt_harness")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="Run a workflow N times and export the dependency graph")
    run.add_argument("workflow", choices=sorted(WORKFLOWS))
    run.add_argument("--count", type=int, default=1)
    run.add_argument("--out-dir", default="out")
    run.add_argument("--gateway-endpoint", default="http://localhost:18888")
    run.add_argument("--sw-endpoint", default="http://localhost:12800/graphql")

    return parser


def run_command(args, runner=None, analyzer=None) -> int:
    if runner is None:
        gateway = GatewayClient(base_url=args.gateway_endpoint)
        sw = SkyWalkingClient(endpoint=args.sw_endpoint)
        correlator = TraceCorrelator(sw)
        runner = WorkflowRunner(gateway, correlator)
    if analyzer is None:
        analyzer = DependencyAnalyzer()

    definition = WORKFLOWS[args.workflow]
    os.makedirs(args.out_dir, exist_ok=True)

    # runs.jsonl is written incrementally as each run finishes, so an
    # interrupted batch still leaves its completed runs on disk. The derived
    # files below need the whole batch and are written at the end.
    with open(os.path.join(args.out_dir, "runs.jsonl"), "w") as runs_file:
        def on_result(i, result):
            runs_file.write(json.dumps(run_record(result)) + "\n")
            runs_file.flush()
            status = "OK" if result.success else f"FAILED: {result.failure_reason}"
            print(f"run {i + 1}/{args.count}: {status}", file=sys.stderr)

        results = runner.run_many(definition, args.count,
                                  run_id_fn=lambda i: str(uuid.uuid4()),
                                  on_result=on_result)

    service_edges, endpoint_edges = analyzer.analyze(results)

    write_runs_summary_csv(results, os.path.join(args.out_dir, "runs_summary.csv"))
    write_dependency_csv(service_edges, os.path.join(args.out_dir, "dependencies_service.csv"))
    write_dependency_csv(endpoint_edges, os.path.join(args.out_dir, "dependencies_endpoint.csv"))

    return 0


def main(argv=None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if args.command == "run":
        return run_command(args)
    return 1
