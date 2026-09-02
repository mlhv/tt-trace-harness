#!/usr/bin/env python3
"""Manual smoke test: runs the real 'preserve' workflow once against the live
cluster, asserts every correlate=True step correlates, and then asserts the
DependencyAnalyzer actually derives a non-trivial service graph from the
resulting spans.

That last check matters: correlation succeeding only proves we fetched a trace.
It says nothing about whether we read SkyWalking's segment model correctly, and
an analyzer bug there yields a confidently-wrong dependency graph while every
correlation check still reports OK.

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

from tt_harness.analyzer import DependencyAnalyzer
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

    if not ok:
        print("SMOKE TEST FAILED: at least one step did not correlate.", file=sys.stderr)
        return 1
    print("Every correlation-point step matched a trace.", file=sys.stderr)

    return check_dependency_graph(result)


def check_dependency_graph(result) -> int:
    """Run the correlated spans through the real analyzer and eyeball the graph.

    A correct walk of SkyWalking's segment/refs model must find real
    cross-service edges here: preserve and pay both fan out to several internal
    TrainTicket services. Zero edges means the span tree is being misread.
    """
    total_spans = sum(len(s.spans) for s in result.steps)
    segments = {sp.get("segmentId") for s in result.steps for sp in s.spans}
    print(f"\nAnalyzing {total_spans} spans across {len(segments)} segments...",
          file=sys.stderr)

    service_edges, endpoint_edges = DependencyAnalyzer().analyze([result])

    if not service_edges:
        print("SMOKE TEST FAILED: analyzer found no cross-service edges. The "
              "spans were fetched but the dependency graph is empty -- most "
              "likely the segment/refs walk is broken.", file=sys.stderr)
        return 1

    print(f"Service edges ({len(service_edges)}):", file=sys.stderr)
    for (src, dst), edge in sorted(service_edges.items()):
        print(f"  {src} -> {dst}  calls={edge.call_count} "
              f"avg={edge.avg_ms:.1f}ms max={edge.max_ms:.1f}ms "
              f"errors={edge.error_count}", file=sys.stderr)
    print(f"Endpoint edges: {len(endpoint_edges)}", file=sys.stderr)

    # Sanity signal for a human reader, not a hard assertion: the live
    # cluster's topology varies run to run, so we only flag it as odd.
    non_tt = sorted({s for pair in service_edges for s in pair if not s.startswith("ts-")})
    if non_tt:
        print(f"NOTE: services not named ts-*: {non_tt}", file=sys.stderr)
    print("\nEyeball the edges above: sources and targets should be recognisable "
          "TrainTicket services (ts-preserve-service, ts-order-service, "
          "ts-payment-service, ts-security-service, ...), the arrows should run "
          "in plausible directions, and there should be no self-edges.",
          file=sys.stderr)

    self_edges = [k for k in service_edges if k[0] == k[1]]
    if self_edges:
        print(f"SMOKE TEST FAILED: self-edges present, which the analyzer "
              f"should never emit: {self_edges}", file=sys.stderr)
        return 1

    # A non-empty graph is not by itself proof the segment model is read right:
    # the old flat-spanId walk also produced ~15 edges on a real 30-segment
    # trace, but every one of them fanned out from a single service, because
    # they were artifacts of spanId collisions rather than real refs links. A
    # correct walk of a multi-segment preserve trace always has nested fan-out
    # (preserve -> travel -> basic -> station), so >= 2 distinct sources.
    sources = {src for src, _ in service_edges}
    if len(segments) > 1 and len(sources) < 2:
        print(f"SMOKE TEST FAILED: {len(segments)} segments but every edge "
              f"originates from {sources}. A multi-segment trace should show "
              f"nested fan-out; this is the signature of resolving spans by "
              f"bare spanId instead of (segmentId, spanId) + refs.",
              file=sys.stderr)
        return 1

    print("SMOKE TEST PASSED: every correlation-point step matched a trace and "
          f"the analyzer derived {len(service_edges)} cross-service edges.",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
