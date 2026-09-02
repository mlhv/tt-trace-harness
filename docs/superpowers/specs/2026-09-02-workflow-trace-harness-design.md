# Workflow Trace Harness — Design Spec

**Date:** 2026-09-02
**Status:** Approved design, pre-implementation
**Repo:** `tt-trace-harness` (new, standalone from `train-ticket`)

## Background

TrainTicket (the microservices demo app running in the `default` namespace,
observed via SkyWalking) is being used as a research subject for a professor's
lab: mapping the dependency structure between its ~45 microservices, finding
latency bottlenecks, and relating trace behavior to business workflows
(login → search → book → pay).

We already validated that SkyWalking's OAP GraphQL API (`queryBasicTraces`,
`queryTrace`) can pull full span-level trace data programmatically — used
earlier for a one-off export (`~/skywalking-exports/export_traces.py`,
540 traces / 991 spans pulled from a live time window). This spec extends
that into a proper harness: something that *triggers* a workflow on demand
(rather than passively scraping whatever traffic happens to exist) and
correlates the resulting traces automatically.

**Explicitly out of scope for this spec** (deferred, see Future Work):
- Adding `ActiveSpan.tag(...)` business-data enrichment to TrainTicket's
  Java services (the professor's second suggestion). That's a separate,
  code-change-plus-redeploy effort with its own spec, sequenced *after*
  this harness proves out.
- A visual dashboard/viewer. v1 output is CSV/JSON; a viewer is a fast-follow
  once the data shape is proven against real analysis.
- Support for multiple concurrent workflow runs. v1 runs sequentially to
  keep trace correlation unambiguous.
- Generalizing to microservices apps beyond TrainTicket. The design keeps
  the gateway/OAP endpoints and workflow definitions as config/pluggable
  pieces (not hardcoded throughout), but no second target app is built or
  tested against in this phase.

## Goal

A CLI tool that:
1. Executes a named workflow (v1: **login → search → book → pay**, the
   "preserve" flow) against the running TrainTicket cluster, `--count N`
   times, with randomized inputs per run (route, date, seat type — matching
   the variation strategy in the app's own legacy Selenium test suite).
2. For each run, correlates every top-level HTTP call to its resulting
   SkyWalking trace, and pulls the full span tree.
3. From a batch of runs, derives a service-level and endpoint-level
   dependency graph (who calls whom, how often, how slow) and per-run
   summaries — exported as CSV/JSON for offline analysis.

## Architecture

```
tt_harness/
  workflows/
    base.py            — WorkflowStep / WorkflowDefinition types
    preserve.py         — the login→search→book→pay definition
  runner.py             — WorkflowRunner: executes a definition N times
  correlator.py         — TraceCorrelator: HTTP call → SkyWalking trace
  client_gateway.py     — typed REST client for ts-gateway-service
  client_sw.py          — GraphQL client for OAP (extends export_traces.py)
  analyzer.py           — DependencyAnalyzer: traces → graph + latency stats
  export.py             — CSV/JSON writers
  cli.py                — `python -m tt_harness run preserve --count 100`
tests/
  test_correlator.py    — matching logic against canned GraphQL responses
docs/superpowers/specs/ — this file and future specs
```

Reuses the GraphQL query patterns already proven in `export_traces.py`
(`queryBasicTraces` for discovery, `queryTrace` for full span detail),
run against `http://localhost:12800/graphql` via
`kubectl port-forward svc/skywalking-oap 12800:12800 -n default`, same as
before. The gateway is reached the same way, via
`kubectl port-forward svc/ts-gateway-service 18888:18888 -n default`
(confirmed against the live Service).

## Why only 4-5 correlation points are needed per run

The workflow is one user action end-to-end, but only involves these
top-level HTTP calls through the gateway (confirmed by reading the
Spring controllers directly, not guessed):

```
POST /api/v1/users/login                        (ts-auth-service)
POST /api/v1/travelservice/trips/left            (ts-travel-service)   ─┐ search
POST /api/v1/travel2service/trips/left           (ts-travel2-service)  ─┘
POST /api/v1/preserveservice/preserve            (ts-preserve-service)
POST /api/v1/inside_pay_service/inside_payment   (ts-inside-payment-service)
```

`preserve` and `inside_payment` each fan out synchronously to several other
services internally (`ts-basic-service`, `ts-user-service`,
`ts-contacts-service`, `ts-security-service`, `ts-seat-service`,
`ts-order-service`, `ts-payment-service`, …). Because that fan-out happens
inside one HTTP request's execution, SkyWalking captures it as descendant
spans under the *same* trace ID as the top-level call. So the harness only
needs to correlate these 4-5 top-level calls; the full cross-service call
tree comes back automatically inside each `queryTrace` result.

One gap surfaced while reading the source: `preserve` requires an existing
`contactsId`. The workflow definition will include an "ensure a contact
exists" step (create one via `ts-contacts-service` if the account has none)
so runs are self-contained and don't depend on hand-seeded test data.

## Components

- **WorkflowStep** — one HTTP call: name, method/path template, a function
  that builds the request body from prior steps' outputs + randomized
  inputs, and a function that extracts the outputs later steps need
  (e.g., login's step extracts `token`/`userId`; search's step extracts a
  randomly-chosen `tripId`/`from`/`to`/`date` from the results).
- **WorkflowDefinition** — an ordered list of `WorkflowStep`s plus an
  input-randomizer (route/date/seat pool, mirroring the variation the old
  Selenium `TestFlowOne.java` used). New workflows (rebook, cancel, admin
  flows) are new definitions against the same engine — no new plumbing.
- **WorkflowRunner** — executes one definition once, or `--count N` times
  sequentially. Records each step's endpoint and precise wall-clock
  `[start, end]` window. Runs are *not* parallelized in v1, to keep
  correlation-by-time-window unambiguous.
- **TraceCorrelator** — after each step completes, polls
  `queryBasicTraces` for a window padded around the step's
  `[start, end]` (OAP ingestion lags a few seconds; retry with backoff,
  capped at ~15s total wait), filtering by endpoint name. Picks the trace
  whose `start` timestamp falls inside the padded window. If it can't find
  a confident match after retries, the step is recorded as
  `correlation_failed` rather than silently dropped. Once matched, calls
  `queryTrace(traceId)` for the full span tree.
- **DependencyAnalyzer** — given a batch of correlated runs, walks each
  trace's span tree (via `parentSpanId`/`refs`) and emits:
  - service-level edges: parent span's `serviceCode` → child span's
    `serviceCode` (only where they differ — i.e., real cross-service hops)
  - endpoint-level edges: same, keyed by `endpointName` instead of service
  - per-service/per-endpoint latency stats (mean/p50/p95/max), computed
    from span self-time (`duration` minus the sum of direct children's
    durations) so a slow parent isn't blamed for a slow child's time.
- **Exporters** — write the outputs below.

## Output schema

- **`runs.jsonl`** — one record per run, full fidelity:
  ```json
  {"run_id": "...", "workflow": "preserve", "started_at": "...",
   "inputs": {"from": "...", "to": "...", "date": "...", "seatType": 2},
   "success": true,
   "steps": [{"step_name": "login", "endpoint": "...", "trace_id": "...",
              "correlation_status": "matched", "spans": [...]}]}
  ```
- **`dependencies_service.csv`** — `source_service, target_service,
  call_count, avg_ms, p50_ms, p95_ms, max_ms, error_count`
- **`dependencies_endpoint.csv`** — same columns, keyed by endpoint pairs
  instead of service pairs (finer-grained — e.g. distinguishes `preserve`'s
  six internal calls from each other).
- **`runs_summary.csv`** — `run_id, workflow, total_duration_ms,
  step_durations (json), success, failure_reason`

## Error handling

- **Step failure** (login rejected, no trips returned, insufficient
  contacts, payment declined) aborts *that run only*, recorded in
  `runs_summary.csv` with a `failure_reason`. The batch continues to the
  next run rather than aborting entirely.
- **Correlation failure** (no matching trace found after retries) is
  recorded as `correlation_status: "failed"` on that step — the run's
  other, successfully-correlated steps are still exported. Nothing is
  silently dropped from the output.
- **Side effect to flag, not solve here:** every successful run creates a
  real order + payment row in `tsdb-mysql`. Running hundreds of iterations
  will leave that data behind in the app's database. Acceptable for a
  research/demo cluster; not addressed by this spec (e.g. no auto-cleanup).

## Testing

- Unit tests for `TraceCorrelator`'s matching logic against canned/fixture
  `queryBasicTraces` responses (unambiguous match, multiple candidates,
  no candidates, retry-then-match).
- Unit tests for `DependencyAnalyzer`'s edge/latency aggregation against a
  fixture trace (hand-built small span tree with known expected output).
- One smoke test that runs the real `preserve` workflow once against the
  live cluster and asserts every step correlates successfully — this is
  the harness's own integration check, run manually (not CI, since it
  depends on a live cluster and creates real data as noted above).

## Future work (explicitly deferred, not designed here)

1. **ActiveSpan business-data enrichment** — add `ActiveSpan.tag(...)` calls
   in `ts-preserve-service` (and related) for `workflow`, `tripId`,
   `seatType`, etc., per the professor's suggestion. Needs its own spec
   (Java code change + rebuild + redeploy). Once live, this harness's
   `DependencyAnalyzer` picks up the richer tags automatically — no rework
   needed here.
2. **Interactive viewer** — a waterfall/dependency-graph browser UI reading
   `runs.jsonl` directly, once the CSV/JSON shape has been validated
   against real analysis.
3. **Configuration comparison** — running the same workflow batch under
   different conditions (fault injection, load level, code version — exact
   axis still undecided) and diffing the resulting dependency graphs.
   Deferred until axis of comparison is chosen.
4. **Cross-app generalization** — pointing the harness at a different
   SkyWalking-instrumented app. The design avoids hardcoding TrainTicket
   specifics outside `workflows/` and a config file, but this isn't
   exercised or tested against a second app in this phase.
