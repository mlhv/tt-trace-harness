# tt-trace-harness

Workflow trace harness for TrainTicket: triggers the login→search→book→pay
("preserve") workflow N times, correlates each run's HTTP calls to
SkyWalking traces, and exports a service/endpoint dependency graph.

See `docs/superpowers/specs/2026-09-02-workflow-trace-harness-design.md`
for the design and `docs/superpowers/plans/2026-09-02-workflow-trace-harness-implementation.md`
for the implementation plan.

## Setup

    python3 -m venv .venv
    .venv/bin/pip install -r requirements-dev.txt

## Prerequisites (live cluster)

In separate terminals, left running for the duration of any `run` or the
smoke test:

    kubectl port-forward svc/skywalking-oap 12800:12800 -n default
    kubectl port-forward svc/ts-gateway-service 18888:18888 -n default

## Run the unit tests

    .venv/bin/python -m pytest tests/ -v

## Run the harness

    .venv/bin/python -m tt_harness run preserve --count 100 --out-dir out/

Outputs land in `out/`: `runs.jsonl`, `runs_summary.csv`,
`dependencies_service.csv`, `dependencies_endpoint.csv`.

## Smoke test (manual, live cluster, creates real data)

    .venv/bin/python scripts/smoke_test_preserve.py

## Known side effect

Every successful run creates a real order + payment row in `tsdb-mysql`.
Acceptable for this research/demo cluster; no auto-cleanup is implemented.
