# Business-Tag Enrichment for the Preserve Workflow — Design Spec

**Date:** 2026-09-21
**Status:** Approved design, pre-implementation
**Repo:** `train-ticket` (this spec's code changes land there, not in
`tt-trace-harness`) — `tt-trace-harness` is a consumer of the resulting data,
unchanged by this spec.

## Background

The original harness spec
([2026-09-02-workflow-trace-harness-design.md](2026-09-02-workflow-trace-harness-design.md))
explicitly deferred "Adding `ActiveSpan.tag(...)` business-data enrichment to
TrainTicket's Java services (the professor's second suggestion)" as future
work, sequenced after the harness itself proved out. It has: `tt_harness` now
runs the login→search→book→pay ("preserve") workflow against the live
cluster, correlates each step to a SkyWalking trace, and exports
service/endpoint dependency graphs from the span data.

That span data is metadata-only. Confirmed empirically against
`~/skywalking-exports/traces_2026-08-17.jsonl` (540 real traces) and a fresh
`out/runs.jsonl` batch: HTTP spans (`SpringMVC` entries, `SpringRestTemplate`
exits — both gateway-facing and internal service-to-service calls) carry
only `url`, `http.method`, `http.status_code`. DB spans carry `db.statement`
as parameterized SQL text (`?` placeholders, no bound values). No request or
response JSON body is captured anywhere, for any hop, automatically.

We considered and rejected capturing full JSON request/response bodies (see
"Alternatives considered" below). This spec instead adds **selective business
tags** to specific spans, per the professor's suggestion, using SkyWalking's
`ActiveSpan.tag()` API
([application toolkit tracer](https://skywalking.apache.org/docs/skywalking-java/latest/en/setup/service-agent/java-agent/application-toolkit-tracer/)) —
keeping the existing automatic instrumentation and enriching it, rather than
replacing or duplicating it.

**Explicitly out of scope for this spec** (deferred, see Future Work):
- Tagging the security-check's failure *reason* (only pass/fail is in scope
  here — the reason string requires reading further into
  `ts-security-service`'s response, which this pass doesn't need).
- Any service other than `ts-preserve-service`.
- The `search_travel`/`search_travel2` workflow steps (`trips/left`), which
  have their own large fan-out (47 spans / 9 services observed for one
  `trips/left` trace, including a seat-service↔order-service loop hit 6
  times) and would need their own investigation.
- The `assurance`/`food`/`consign` branches in `PreserveServiceImpl.preserve()`
  — the harness's `do_preserve` never sends non-zero/non-empty values for
  these, so the branches never execute. Instrumenting dead code paths adds
  no signal.
- Any change to `tt-trace-harness` itself. `client_sw.py`'s
  `FULL_TRACE_QUERY` already requests `tags { key value }` generically, so
  new tag keys appear in `spans[].tags` in `runs.jsonl` with zero client
  changes.

## Goal

Enrich the spans SkyWalking already captures for the `preserve` workflow
with the business data that explains *why* a trace looks the way it does —
which gate rejected a request, what price/seat was resolved, what order was
created — so trace shape and behavior (which spans appear, how long they
take, whether they error) can be correlated with business context instead of
analyzed as opaque service-name/duration data.

## Investigation: why single-service scope is sufficient

`ts-preserve-service` (`PreserveServiceImpl.preserve()`) is a pure
orchestrator: for every downstream call it makes, it deserializes the
response into a local Java object before deciding what to do next. That
means the request *and* response for nearly every hop of interest are
already in scope as local variables in one file — tagging doesn't require
touching the other 15 services this workflow's trace fans out to.

`ts-preserve-service`'s `pom.xml` does not currently depend on
`apm-toolkit-trace` (only `ts-travel-service` does, pinned at `8.6.0`, used
there for an unrelated `@TraceCrossThread` annotation). Adding the same
dependency/version to `ts-preserve-service` is a prerequisite.

## Tag points

All in `ts-preserve-service/src/main/java/preserve/service/PreserveServiceImpl.java`:

| Call site | Data already in scope | Tags to add | Why |
|---|---|---|---|
| `preserve()` entry | `oti.getTripId()`, `oti.getSeatType()`, `oti.getAccountId()` | `workflow=preserve`, `tripId`, `seatTypeRequested` | Anchors every other tag on this trace to the business request that produced it |
| `checkSecurity()` call (L52-57, L345-357) | `result.getStatus()` | `security.status=pass\|fail` | Real gate — explains traces that terminate after ~1 span (scalper check rejected the request) |
| `getTripAllDetailInformation()` call (L78-98) | `tripResponse.getConfortClass()`, `tripResponse.getEconomyClass()` | `seat.confortAvailable`, `seat.economyAvailable`, `seat.checkResult=pass\|not_enough` | Second gate — explains "Seat Not Enough" failures, correlates capacity with rejection |
| `basic/travel` call (L127-139) | `resultForTravel.getPrices()` | `price.confortClass`, `price.economyClass` | Real price data flowing through the trace |
| `dipatchSeat()` call (L150-166, L266-286) | `ticket.getSeatNo()` | `seat.allocatedClass`, `seat.allocatedNumber` | Concrete allocation outcome — worth watching given the contention already observed in `search_travel`'s seat-service loop |
| `createOrder()` call (L170-175, L391-404) | `cor.getData().getId()`, `order.getPrice()`, `cor.getStatus()` | `order.id`, `order.price`, `order.status` | The workflow's actual business outcome; `order.id` also lets analysis cross-reference the harness's own `find_order` step and the real DB row the harness's README already flags as a side effect |

## Open implementation questions — resolved 2026-09-28

- **Which span the tags land on:** confirmed empirically against a real
  cluster trace (`tt_harness`'s `client_sw.py` querying the live OAP after
  deploying the built image). All business tags land on the **entry span**
  — `ts-preserve-service`'s `POST:/api/v1/preserveservice/preserve` span —
  not on the individual downstream RestTemplate Exit spans. Each Exit span
  (to `ts-security-service`, `ts-contacts-service`, `ts-travel-service`,
  `ts-basic-service`, `ts-seat-service`, `ts-order-service`,
  `ts-user-service`) carries only the generic `url`/`http.method`/
  `http.status_code` tags SkyWalking's RestTemplate plugin adds — meaning
  the plugin closes each Exit span before `preserve()`'s own next line of
  code runs, so `ActiveSpan.tag()` always resolves to the still-open parent
  Entry span at every one of this plan's insertion points. Example (real
  values from a passing smoke-test run, `seatType=3`):
  ```
  POST:/api/v1/preserveservice/preserve tags:
    workflow=preserve, tripId=G1236, seatTypeRequested=3,
    security.status=pass, seat.confortAvailable=1073741823,
    seat.economyAvailable=1073741823, seat.checkResult=pass,
    price.confortClass=50.0, price.economyClass=35.0,
    seat.allocatedClass=SecondClassSeat, seat.allocatedNumber=1444993438,
    order.status=success, order.id=<uuid>, order.price=35.0
  ```
  This is the more useful outcome anyway: all 14 tags are queryable off one
  span per workflow run, rather than split across seven.
- **SkyWalking agent version compatibility:** confirmed. The cluster's Java
  agent is **8.13.0** (seen loading `apm-toolkit-trace-activation-8.13.0.jar`
  in the pod's startup logs), while the app was compiled against
  `apm-toolkit-trace:8.6.0` — the toolkit API is stable and
  forward-compatible across that gap by design (SkyWalking publishes the
  toolkit separately from the agent for exactly this reason). Tags landed
  correctly with zero compatibility issues; no version bump needed.

## Alternatives considered

**Full JSON request/response payload capture at each hop**, rather than
selective tags. Rejected:
- Not a pattern SkyWalking or TrainTicket support anywhere today — a search
  across all 42 TrainTicket services found zero uses of body capture,
  despite `apm-toolkit-trace` already being on the classpath in one service.
- Requires serializing full objects at every call site, versus a handful of
  primitive-valued tag calls.
- Captures the harness's own plaintext login password and contact PII
  (phone number, document number) by default, requiring a redaction pass
  before the data could safely land in OAP storage and `runs.jsonl`.
- Answers "what data moved" but not "why it matters" — selective tags
  target exactly the business decisions and outcomes worth correlating with
  trace shape, which is the actual research goal.

**Enriching entry spans in every downstream service** (as well as, or
instead of, the caller's exit spans), per the professor's note that this is
optionally useful. Deferred: since `ts-preserve-service` already holds both
request and response locally for each hop, entry-span enrichment in the
other 15 services adds coordination and redeploy cost without adding data
this pass doesn't already have another way to get.

## Deployment

Changes are Java source changes to `ts-preserve-service` (pom.xml dependency
+ `ActiveSpan.tag()` calls) requiring a rebuild and redeploy of that
service's image in the cluster. No `tt-trace-harness` changes, no OAP/agent
config changes.

## Future Work

- Security-check failure *reason* (not just pass/fail).
- Extend tagging to `search_travel`/`search_travel2` (`trips/left`), which
  has richer internal fan-out and an observed seat-allocation retry loop
  worth explaining.
- Randomize `assurance`/`food`/`consign` inputs in `tt_harness`'s
  `do_preserve` step to actually exercise those branches, as a prerequisite
  to tagging them.
- Entry-span enrichment in downstream services, if a future analysis needs
  data not available to the caller (e.g. distinguishing "which specific
  validation inside `ts-security-service` failed").
