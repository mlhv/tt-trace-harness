#!/usr/bin/env python3
"""Manual smoke test: runs the real 'preserve' workflow once against the live
cluster and asserts every correlate=True step correlates successfully.

Not run in CI — depends on a live cluster and creates a real order + payment
row in tsdb-mysql (see spec's "Error handling" section).

Prerequisites (run in separate terminals, left running):
    kubectl port-forward svc/skywalking-oap 12800:12800 -n default
    kubectl port-forward svc/ts-gateway-service 18888:18888 -n default

Usage:
    .venv/bin/python scripts/smoke_test_preserve.py
"""
import sys

sys.path.insert(0, __file__.rsplit("/scripts/", 1)[0])

from tt_harness.client_gateway import GatewayClient
from tt_harness.client_sw import SkyWalkingClient
from tt_harness.correlator import TraceCorrelator
from tt_harness.runner import WorkflowRunner
from tt_harness.workflows.preserve import PRESERVE_WORKFLOW


def main() -> int:
    gateway = GatewayClient()
    sw = SkyWalkingClient()
    correlator = TraceCorrelator(sw)
    runner = WorkflowRunner(gateway, correlator)

    print("Running preserve workflow once against the live cluster...", file=sys.stderr)
    result = runner.run_once(PRESERVE_WORKFLOW, run_id="smoke-test")

    if not result.success:
        print(f"FAIL: run did not complete: {result.failure_reason}", file=sys.stderr)
        return 1

    print(f"Run completed. Inputs: {result.inputs}", file=sys.stderr)
    ok = True
    for step in result.steps:
        if not step.correlate:
            print(f"  {step.step_name}: skipped (not a correlation point)", file=sys.stderr)
            continue
        marker = "OK" if step.correlation_status == "matched" else "FAIL"
        print(f"  {step.step_name}: {marker} (status={step.correlation_status}, trace_id={step.trace_id})",
              file=sys.stderr)
        if step.correlation_status != "matched":
            ok = False

    if ok:
        print("SMOKE TEST PASSED: every correlation-point step matched a trace.", file=sys.stderr)
        return 0
    print("SMOKE TEST FAILED: at least one step did not correlate.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
