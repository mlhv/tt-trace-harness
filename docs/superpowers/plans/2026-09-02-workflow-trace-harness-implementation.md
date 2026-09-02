# Workflow Trace Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a CLI tool that runs the TrainTicket `preserve` (login→search→book→pay) workflow N times against the live cluster, correlates each run's HTTP calls to SkyWalking traces, and exports a service/endpoint dependency graph with latency stats.

**Architecture:** A small dependency-injected pipeline: `GatewayClient` (REST) and `SkyWalkingClient` (GraphQL) are thin HTTP wrappers; `WorkflowDefinition`/`WorkflowStep` describe a workflow as pure functions over a shared context dict; `WorkflowRunner` executes a definition N times and, per step, hands the result to an injected `TraceCorrelator`; `DependencyAnalyzer` walks the correlated span trees into graph edges; `export.py` writes the four output files. Every component takes its collaborators as constructor/function arguments so each can be unit-tested with fakes, with no live cluster required except for the final smoke test.

**Tech Stack:** Python 3.12 stdlib only (`urllib.request`, `argparse`, `csv`, `json`, `dataclasses`) for the harness itself — this mirrors `~/skywalking-exports/export_traces.py`'s existing convention of no HTTP client dependency. `pytest` is the only dev dependency.

**Spec:** `docs/superpowers/specs/2026-09-02-workflow-trace-harness-design.md`

## Global Constraints

- Python 3.12 stdlib only for runtime code; `pytest` is the sole test dependency (`requirements-dev.txt`).
- No parallel workflow runs in v1 — `WorkflowRunner` executes sequentially (spec: "Runs are *not* parallelized in v1").
- SkyWalking OAP reached at `http://localhost:12800/graphql` by default (via `kubectl port-forward svc/skywalking-oap 12800:12800 -n default`); gateway at `http://localhost:18888` by default (via `kubectl port-forward svc/ts-gateway-service 18888:18888 -n default`). Both overridable via CLI flags.
- Auth header format is `Authorization: Bearer <token>` (confirmed in `ts-common/.../JWTUtil.java:90-93` — `JWTFilter` is wired into every non-auth service's `SecurityConfig`, but `GET /api/v1/routeservice/routes`, `POST /api/v1/travelservice/trips/left`, and `POST /api/v1/travel2service/trips/left` are `permitAll()` and need no token; `contactservice`, `preserveservice`, `orderservice`, `inside_pay_service` do require it).
- Default seed account: `fdse_microservices@163.com` / `DefaultPassword` (from `old-docs/ts-ui-test/src/test/java/TestFlowOne.java:54-55` — the app's own legacy Selenium suite).
- A step failure aborts only that run (recorded with `failure_reason`); a correlation failure records `correlation_status: "failed"` on that step but does not drop the run. Nothing is silently dropped.
- Every successful run creates a real order + payment in `tsdb-mysql` — this is a known, accepted side effect (spec's "Error handling" section), not something to fix in this plan.

---

## Known gaps beyond what the spec called out

While reading the actual Spring controllers/services (not guessing), two gaps surfaced that the spec's "why only 4-5 correlation points" section didn't cover. Both are handled as **uncorrelated plumbing steps** (like the spec's own `ensure_contact` step), matching the pattern the spec already established for the `contactsId` gap:

1. **`contactsId` gap** (spec already flagged this): `preserve` requires an existing `contactsId`. Handled by an `ensure_contact` step.
2. **`orderId` gap** (newly found, not in spec): `PreserveServiceImpl.preserve()` (`ts-preserve-service/src/main/java/preserve/service/PreserveServiceImpl.java:169`) returns `new Response<>(1, "Success.", cor.getMsg())` — the `data` field of the preserve response is `createOrder`'s **message string**, not the created `Order` object or its id. The orderId generated inside `preserve()` (`UUID orderId = UUID.randomUUID()` at line ~110) is never returned to the caller. The only way to recover it is a follow-up `POST /api/v1/orderservice/order/query` call, filtered to `state=0` (NOTPAID) for the account, matched against `trainNumber`/`travelDate` from this run's inputs. Handled by a `find_order` step between `preserve` and `pay`.

Both are marked `correlate=False` on their `WorkflowStep` — they still run and can fail the whole run, but they are not among the "4-5" endpoints the `DependencyAnalyzer` reasons about, and are not polled against SkyWalking.

---

## File Structure

```
tt-trace-harness/
  tt_harness/
    __init__.py
    __main__.py            — `python -m tt_harness` entry point
    client_sw.py            — SkyWalkingClient (Task 1)
    client_gateway.py       — GatewayClient (Task 2)
    workflows/
      __init__.py
      base.py                — WorkflowStep, WorkflowDefinition, StepResult, RunResult (Task 3)
      preserve.py             — PRESERVE_WORKFLOW definition (Task 4)
    runner.py                — WorkflowRunner (Task 5)
    correlator.py             — TraceCorrelator, CorrelationResult (Task 6)
    analyzer.py               — DependencyAnalyzer (Task 7)
    export.py                 — CSV/JSONL writers (Task 8)
    cli.py                     — argparse wiring (Task 9)
  tests/
    __init__.py
    test_client_sw.py
    test_client_gateway.py
    test_workflow_base.py
    test_preserve_workflow.py
    test_runner.py
    test_correlator.py
    test_analyzer.py
    test_export.py
    test_cli.py
  scripts/
    smoke_test_preserve.py    — manual, live-cluster smoke test (Task 10)
  requirements-dev.txt
  README.md
```

---

### Task 1: SkyWalking GraphQL client + project scaffolding

**Files:**
- Create: `tt-trace-harness/requirements-dev.txt`
- Create: `tt-trace-harness/tt_harness/__init__.py`
- Create: `tt-trace-harness/tt_harness/client_sw.py`
- Create: `tt-trace-harness/tests/__init__.py`
- Test: `tt-trace-harness/tests/test_client_sw.py`

**Interfaces:**
- Produces: `tt_harness.client_sw.post_graphql(endpoint: str, query: str, variables: dict, timeout: float = 30.0) -> dict` — raises `RuntimeError` on GraphQL `errors`.
- Produces: `tt_harness.client_sw.SkyWalkingClient(endpoint="http://localhost:12800/graphql", timeout=30.0)` with methods:
  - `query_basic_traces(self, start: str, end: str, trace_state: str = "ALL", page_num: int = 1, page_size: int = 20) -> list[dict]` — each dict has `segmentId, endpointNames, duration, start, isError, traceIds`.
  - `query_trace(self, trace_id: str) -> list[dict]` — list of span dicts (`traceId, segmentId, spanId, parentSpanId, serviceCode, serviceInstanceName, startTime, endTime, endpointName, type, peer, component, layer, isError, refs, tags, logs`).

- [ ] **Step 1: Create scaffolding**

```bash
mkdir -p /home/ml3787/tt-trace-harness/tt_harness
mkdir -p /home/ml3787/tt-trace-harness/tests
touch /home/ml3787/tt-trace-harness/tt_harness/__init__.py
touch /home/ml3787/tt-trace-harness/tests/__init__.py
```

`requirements-dev.txt`:
```
pytest==8.3.3
```

Install it:
```bash
cd /home/ml3787/tt-trace-harness
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
```

- [ ] **Step 2: Write the failing test**

`tests/test_client_sw.py`:
```python
import json
from unittest.mock import patch

from tt_harness.client_sw import SkyWalkingClient, post_graphql


def test_post_graphql_returns_data_on_success():
    fake_response = {"data": {"foo": "bar"}}

    class FakeHTTPResponse:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def read(self):
            return json.dumps(fake_response).encode()

    with patch("tt_harness.client_sw.urllib.request.urlopen", return_value=FakeHTTPResponse()):
        result = post_graphql("http://x/graphql", "query {}", {"a": 1})

    assert result == {"foo": "bar"}


def test_post_graphql_raises_on_graphql_errors():
    fake_response = {"errors": [{"message": "boom"}]}

    class FakeHTTPResponse:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def read(self):
            return json.dumps(fake_response).encode()

    with patch("tt_harness.client_sw.urllib.request.urlopen", return_value=FakeHTTPResponse()):
        try:
            post_graphql("http://x/graphql", "query {}", {})
            assert False, "expected RuntimeError"
        except RuntimeError as e:
            assert "boom" in str(e)


def test_query_basic_traces_passes_condition_and_returns_traces():
    captured = {}

    def fake_post(endpoint, query, variables, timeout=30.0):
        captured["endpoint"] = endpoint
        captured["variables"] = variables
        return {"queryBasicTraces": {"traces": [{"segmentId": "s1", "endpointNames": ["/api/v1/users/login"],
                                                   "duration": 12, "start": 1000, "isError": False,
                                                   "traceIds": ["t1"]}]}}

    with patch("tt_harness.client_sw.post_graphql", side_effect=fake_post):
        client = SkyWalkingClient(endpoint="http://x/graphql")
        traces = client.query_basic_traces(start="2026-09-02 0000", end="2026-09-02 0001")

    assert captured["endpoint"] == "http://x/graphql"
    cond = captured["variables"]["condition"]
    assert cond["queryDuration"] == {"start": "2026-09-02 0000", "end": "2026-09-02 0001", "step": "MINUTE"}
    assert cond["traceState"] == "ALL"
    assert traces[0]["traceIds"] == ["t1"]


def test_query_trace_returns_spans():
    def fake_post(endpoint, query, variables, timeout=30.0):
        assert variables == {"traceId": "t1"}
        return {"queryTrace": {"spans": [{"traceId": "t1", "spanId": 0, "serviceCode": "ts-auth-service"}]}}

    with patch("tt_harness.client_sw.post_graphql", side_effect=fake_post):
        client = SkyWalkingClient()
        spans = client.query_trace("t1")

    assert spans == [{"traceId": "t1", "spanId": 0, "serviceCode": "ts-auth-service"}]
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd /home/ml3787/tt-trace-harness && .venv/bin/python -m pytest tests/test_client_sw.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tt_harness.client_sw'`

- [ ] **Step 4: Write minimal implementation**

`tt_harness/client_sw.py`:
```python
"""GraphQL client for SkyWalking's OAP query API.

Reuses the query shapes proven in ~/skywalking-exports/export_traces.py.
"""
import json
import urllib.request

BASIC_TRACES_QUERY = """
query queryBasicTraces($condition: TraceQueryCondition) {
  queryBasicTraces(condition: $condition) {
    traces { segmentId endpointNames duration start isError traceIds }
  }
}
"""

FULL_TRACE_QUERY = """
query queryTrace($traceId: ID!) {
  queryTrace(traceId: $traceId) {
    spans {
      traceId segmentId spanId parentSpanId
      serviceCode serviceInstanceName
      startTime endTime endpointName
      type peer component layer isError
      refs { traceId parentSegmentId parentSpanId type }
      tags { key value }
      logs { time data { key value } }
    }
  }
}
"""


def post_graphql(endpoint: str, query: str, variables: dict, timeout: float = 30.0) -> dict:
    body = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(
        endpoint, data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read())
    if "errors" in payload:
        raise RuntimeError(str(payload["errors"]))
    return payload["data"]


class SkyWalkingClient:
    def __init__(self, endpoint: str = "http://localhost:12800/graphql", timeout: float = 30.0):
        self.endpoint = endpoint
        self.timeout = timeout

    def query_basic_traces(self, start: str, end: str, trace_state: str = "ALL",
                            page_num: int = 1, page_size: int = 20) -> list[dict]:
        condition = {
            "queryDuration": {"start": start, "end": end, "step": "MINUTE"},
            "traceState": trace_state,
            "queryOrder": "BY_START_TIME",
            "paging": {"pageNum": page_num, "pageSize": page_size},
        }
        data = post_graphql(self.endpoint, BASIC_TRACES_QUERY, {"condition": condition}, self.timeout)
        return data["queryBasicTraces"]["traces"]

    def query_trace(self, trace_id: str) -> list[dict]:
        data = post_graphql(self.endpoint, FULL_TRACE_QUERY, {"traceId": trace_id}, self.timeout)
        return data["queryTrace"]["spans"]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /home/ml3787/tt-trace-harness && .venv/bin/python -m pytest tests/test_client_sw.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Commit**

```bash
cd /home/ml3787/tt-trace-harness
git add requirements-dev.txt tt_harness/__init__.py tt_harness/client_sw.py tests/__init__.py tests/test_client_sw.py
git commit -m "feat: add SkyWalking GraphQL client and project scaffolding"
```

---

### Task 2: Gateway REST client

**Files:**
- Create: `tt-trace-harness/tt_harness/client_gateway.py`
- Test: `tt-trace-harness/tests/test_client_gateway.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `tt_harness.client_gateway.request_json(method, url, *, headers=None, json_body=None, timeout=15.0) -> dict`.
- Produces: `tt_harness.client_gateway.GatewayClient(base_url="http://localhost:18888", timeout=15.0)` with methods:
  - `login(self, username: str, password: str) -> dict` → raw `Response` dict `{status, msg, data: {userId, username, token}}`.
  - `list_routes(self) -> list[dict]` → list of `RouteInfo` dicts (`id, startStation, endStation, stationList, distanceList`). No auth needed.
  - `search_left(self, start_place: str, end_place: str, date: str, token: str, service: str) -> list[dict]` — `service` is `"travel"` or `"travel2"`; posts to `/api/v1/travelservice/trips/left` or `/api/v1/travel2service/trips/left`; returns `resp.get("data") or []` (list of `TripResponse` dicts with nested `tripId: {type, number}`).
  - `list_contacts(self, account_id: str, token: str) -> list[dict]` → GET `/api/v1/contactservice/contacts/account/{account_id}`, returns `resp.get("data") or []`.
  - `create_contact(self, account_id: str, name: str, document_type: int, document_number: str, phone_number: str, token: str) -> dict` → POST `/api/v1/contactservice/contacts`, returns full `Response` dict.
  - `preserve(self, order: dict, token: str) -> dict` → POST `/api/v1/preserveservice/preserve`, returns full `Response` dict.
  - `find_notpaid_order(self, account_id: str, trip_id: str, travel_date: str, token: str) -> dict | None` → POST `/api/v1/orderservice/order/query` with `{"loginId": account_id, "enableStateQuery": True, "state": 0, "enableBoughtDateQuery": False, "enableTravelDateQuery": False}`; filters `resp["data"]` in Python to `trainNumber == trip_id and travelDate == travel_date`; returns the **last** matching order dict, or `None`. (Documented assumption: JPA `findByAccountId` returns rows in insertion order, so "last matching" approximates "most recently created" — acceptable for a research harness given multiple runs can create ambiguous same-day/same-trip orders; see plan's "Known gaps" section.)
  - `pay(self, order_id: str, trip_id: str, user_id: str, price: str, token: str) -> dict` → POST `/api/v1/inside_pay_service/inside_payment` with body `{"orderId": order_id, "tripId": trip_id, "userId": user_id, "price": price}`, returns full `Response` dict.

- [ ] **Step 1: Write the failing test**

`tests/test_client_gateway.py`:
```python
from unittest.mock import patch

from tt_harness.client_gateway import GatewayClient


def _client():
    return GatewayClient(base_url="http://gw")


def test_login_posts_credentials_and_returns_response():
    captured = {}

    def fake_request_json(method, url, *, headers=None, json_body=None, timeout=15.0):
        captured.update(method=method, url=url, headers=headers, json_body=json_body)
        return {"status": 1, "msg": "login success", "data": {"userId": "u1", "username": "a", "token": "tok"}}

    with patch("tt_harness.client_gateway.request_json", side_effect=fake_request_json):
        resp = _client().login("a", "b")

    assert captured["method"] == "POST"
    assert captured["url"] == "http://gw/api/v1/users/login"
    assert captured["json_body"] == {"username": "a", "password": "b", "verificationCode": ""}
    assert resp["data"]["token"] == "tok"


def test_list_routes_requires_no_auth_header():
    captured = {}

    def fake_request_json(method, url, *, headers=None, json_body=None, timeout=15.0):
        captured.update(method=method, url=url, headers=headers)
        return {"status": 1, "msg": "ok", "data": [{"id": "r1", "startStation": "Su Zhou", "endStation": "Shang Hai"}]}

    with patch("tt_harness.client_gateway.request_json", side_effect=fake_request_json):
        routes = _client().list_routes()

    assert captured["method"] == "GET"
    assert captured["url"] == "http://gw/api/v1/routeservice/routes"
    assert captured["headers"] in (None, {})
    assert routes[0]["startStation"] == "Su Zhou"


def test_search_left_hits_travel2_path_for_travel2_service():
    captured = {}

    def fake_request_json(method, url, *, headers=None, json_body=None, timeout=15.0):
        captured.update(url=url, json_body=json_body, headers=headers)
        return {"status": 1, "msg": "ok", "data": [{"tripId": {"type": "Z", "number": "1"}}]}

    with patch("tt_harness.client_gateway.request_json", side_effect=fake_request_json):
        trips = _client().search_left("su zhou", "shang hai", "2026-09-10", "tok", service="travel2")

    assert captured["url"] == "http://gw/api/v1/travel2service/trips/left"
    assert captured["json_body"] == {"startPlace": "su zhou", "endPlace": "shang hai", "departureTime": "2026-09-10"}
    assert captured["headers"] == {"Authorization": "Bearer tok"}
    assert trips[0]["tripId"]["type"] == "Z"


def test_find_notpaid_order_returns_last_match():
    orders = [
        {"id": "o1", "trainNumber": "G1", "travelDate": "2026-09-10"},
        {"id": "o2", "trainNumber": "G1", "travelDate": "2026-09-10"},
        {"id": "o3", "trainNumber": "G2", "travelDate": "2026-09-10"},
    ]

    def fake_request_json(method, url, *, headers=None, json_body=None, timeout=15.0):
        return {"status": 1, "msg": "ok", "data": orders}

    with patch("tt_harness.client_gateway.request_json", side_effect=fake_request_json):
        order = _client().find_notpaid_order("acct", "G1", "2026-09-10", "tok")

    assert order["id"] == "o2"


def test_find_notpaid_order_returns_none_when_no_match():
    def fake_request_json(method, url, *, headers=None, json_body=None, timeout=15.0):
        return {"status": 1, "msg": "ok", "data": []}

    with patch("tt_harness.client_gateway.request_json", side_effect=fake_request_json):
        order = _client().find_notpaid_order("acct", "G1", "2026-09-10", "tok")

    assert order is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_client_gateway.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tt_harness.client_gateway'`

- [ ] **Step 3: Write minimal implementation**

`tt_harness/client_gateway.py`:
```python
"""REST client for TrainTicket's ts-gateway-service.

Endpoint paths and auth header format confirmed by reading the Spring
controllers directly (see docs/superpowers/specs/2026-09-02-workflow-trace-harness-design.md).
"""
import json
import urllib.request
import urllib.error


def request_json(method: str, url: str, *, headers: dict | None = None,
                  json_body: dict | None = None, timeout: float = 15.0) -> dict:
    data = json.dumps(json_body).encode() if json_body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


class GatewayClient:
    def __init__(self, base_url: str = "http://localhost:18888", timeout: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _auth(self, token: str) -> dict:
        return {"Authorization": f"Bearer {token}"}

    def login(self, username: str, password: str) -> dict:
        return request_json(
            "POST", f"{self.base_url}/api/v1/users/login",
            json_body={"username": username, "password": password, "verificationCode": ""},
            timeout=self.timeout,
        )

    def list_routes(self) -> list[dict]:
        resp = request_json("GET", f"{self.base_url}/api/v1/routeservice/routes", timeout=self.timeout)
        return resp.get("data") or []

    def search_left(self, start_place: str, end_place: str, date: str, token: str, service: str) -> list[dict]:
        path = "travelservice" if service == "travel" else "travel2service"
        resp = request_json(
            "POST", f"{self.base_url}/api/v1/{path}/trips/left",
            headers=self._auth(token),
            json_body={"startPlace": start_place, "endPlace": end_place, "departureTime": date},
            timeout=self.timeout,
        )
        return resp.get("data") or []

    def list_contacts(self, account_id: str, token: str) -> list[dict]:
        resp = request_json(
            "GET", f"{self.base_url}/api/v1/contactservice/contacts/account/{account_id}",
            headers=self._auth(token), timeout=self.timeout,
        )
        return resp.get("data") or []

    def create_contact(self, account_id: str, name: str, document_type: int,
                        document_number: str, phone_number: str, token: str) -> dict:
        return request_json(
            "POST", f"{self.base_url}/api/v1/contactservice/contacts",
            headers=self._auth(token),
            json_body={
                "accountId": account_id, "name": name, "documentType": document_type,
                "documentNumber": document_number, "phoneNumber": phone_number,
            },
            timeout=self.timeout,
        )

    def preserve(self, order: dict, token: str) -> dict:
        return request_json(
            "POST", f"{self.base_url}/api/v1/preserveservice/preserve",
            headers=self._auth(token), json_body=order, timeout=self.timeout,
        )

    def find_notpaid_order(self, account_id: str, trip_id: str, travel_date: str, token: str) -> dict | None:
        resp = request_json(
            "POST", f"{self.base_url}/api/v1/orderservice/order/query",
            headers=self._auth(token),
            json_body={
                "loginId": account_id, "enableStateQuery": True, "state": 0,
                "enableBoughtDateQuery": False, "enableTravelDateQuery": False,
            },
            timeout=self.timeout,
        )
        orders = resp.get("data") or []
        matches = [o for o in orders if o.get("trainNumber") == trip_id and o.get("travelDate") == travel_date]
        return matches[-1] if matches else None

    def pay(self, order_id: str, trip_id: str, user_id: str, price: str, token: str) -> dict:
        return request_json(
            "POST", f"{self.base_url}/api/v1/inside_pay_service/inside_payment",
            headers=self._auth(token),
            json_body={"orderId": order_id, "tripId": trip_id, "userId": user_id, "price": price},
            timeout=self.timeout,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_client_gateway.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add tt_harness/client_gateway.py tests/test_client_gateway.py
git commit -m "feat: add gateway REST client for TrainTicket endpoints"
```

---

### Task 3: Workflow engine base types

**Files:**
- Create: `tt-trace-harness/tt_harness/workflows/__init__.py`
- Create: `tt-trace-harness/tt_harness/workflows/base.py`
- Test: `tt-trace-harness/tests/test_workflow_base.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `StepResult` dataclass (mutable): `step_name: str, endpoint: str, start: float, end: float, success: bool, error: str | None, outputs: dict, correlate: bool, correlation_status: str = "not_applicable", trace_id: str | None = None, spans: list = field(default_factory=list)`.
  - `WorkflowStep` dataclass: `name: str, endpoint: str, run: Callable[[dict], dict], correlate: bool = True`.
  - `WorkflowDefinition` dataclass: `name: str, randomize_inputs: Callable[[Any], dict], build_steps: Callable[[Any, dict], list[WorkflowStep]]`.
  - `RunResult` dataclass: `run_id: str, workflow: str, inputs: dict, success: bool, steps: list[StepResult], failure_reason: str | None`.

- [ ] **Step 1: Write the failing test**

`tests/test_workflow_base.py`:
```python
from tt_harness.workflows.base import StepResult, WorkflowStep, WorkflowDefinition, RunResult


def test_step_result_defaults():
    r = StepResult(step_name="login", endpoint="/api/v1/users/login", start=1.0, end=2.0,
                    success=True, error=None, outputs={"token": "t"}, correlate=True)
    assert r.correlation_status == "not_applicable"
    assert r.trace_id is None
    assert r.spans == []


def test_workflow_step_defaults_correlate_true():
    step = WorkflowStep(name="login", endpoint="/api/v1/users/login", run=lambda ctx: {})
    assert step.correlate is True


def test_workflow_definition_and_run_result_hold_fields():
    definition = WorkflowDefinition(
        name="preserve",
        randomize_inputs=lambda client: {"seatType": 2},
        build_steps=lambda client, inputs: [],
    )
    assert definition.name == "preserve"
    assert definition.randomize_inputs(None) == {"seatType": 2}
    assert definition.build_steps(None, {}) == []

    run = RunResult(run_id="r1", workflow="preserve", inputs={}, success=True, steps=[], failure_reason=None)
    assert run.run_id == "r1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_workflow_base.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tt_harness.workflows'`

- [ ] **Step 3: Write minimal implementation**

```bash
mkdir -p /home/ml3787/tt-trace-harness/tt_harness/workflows
touch /home/ml3787/tt-trace-harness/tt_harness/workflows/__init__.py
```

`tt_harness/workflows/base.py`:
```python
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class StepResult:
    step_name: str
    endpoint: str
    start: float
    end: float
    success: bool
    error: str | None
    outputs: dict[str, Any]
    correlate: bool
    correlation_status: str = "not_applicable"  # "matched" | "failed" | "not_applicable"
    trace_id: str | None = None
    spans: list = field(default_factory=list)


@dataclass
class WorkflowStep:
    name: str
    endpoint: str
    run: Callable[[dict], dict]
    correlate: bool = True


@dataclass
class WorkflowDefinition:
    name: str
    randomize_inputs: Callable[[Any], dict]
    build_steps: Callable[[Any, dict], list[WorkflowStep]]


@dataclass
class RunResult:
    run_id: str
    workflow: str
    inputs: dict
    success: bool
    steps: list[StepResult]
    failure_reason: str | None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_workflow_base.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add tt_harness/workflows/__init__.py tt_harness/workflows/base.py tests/test_workflow_base.py
git commit -m "feat: add workflow engine base types"
```

---

### Task 4: Preserve workflow definition

**Files:**
- Create: `tt-trace-harness/tt_harness/workflows/preserve.py`
- Test: `tt-trace-harness/tests/test_preserve_workflow.py`

**Interfaces:**
- Consumes: `WorkflowStep`, `WorkflowDefinition` from `tt_harness.workflows.base` (Task 3). Calls methods on a `GatewayClient`-shaped object (Task 2): `list_routes()`, `login()`, `search_left()`, `list_contacts()`, `create_contact()`, `preserve()`, `find_notpaid_order()`, `pay()`.
- Produces: `tt_harness.workflows.preserve.PRESERVE_WORKFLOW: WorkflowDefinition`, plus module-level `DEFAULT_USERNAME = "fdse_microservices@163.com"`, `DEFAULT_PASSWORD = "DefaultPassword"`, `SEAT_TYPES = [2, 3]`, and the functions `randomize_inputs(gateway) -> dict` and `build_steps(gateway, inputs) -> list[WorkflowStep]` for later tasks/tests to call directly.

- [ ] **Step 1: Write the failing test**

`tests/test_preserve_workflow.py` — uses a hand-rolled fake `GatewayClient` (no mocking library needed, since we're driving the whole call sequence):

```python
import random

from tt_harness.workflows.preserve import randomize_inputs, build_steps, PRESERVE_WORKFLOW


class FakeGateway:
    def __init__(self):
        self.calls = []

    def list_routes(self):
        self.calls.append(("list_routes",))
        return [{"startStation": "Su Zhou", "endStation": "Shang Hai"}]

    def login(self, username, password):
        self.calls.append(("login", username, password))
        return {"status": 1, "msg": "login success", "data": {"userId": "acct1", "username": username, "token": "tok"}}

    def search_left(self, start_place, end_place, date, token, service):
        self.calls.append(("search_left", start_place, end_place, date, token, service))
        if service == "travel":
            return [{"tripId": {"type": "G", "number": "42"}}]
        return []

    def list_contacts(self, account_id, token):
        self.calls.append(("list_contacts", account_id, token))
        return []

    def create_contact(self, account_id, name, document_type, document_number, phone_number, token):
        self.calls.append(("create_contact", account_id, token))
        return {"status": 1, "msg": "created", "data": {"id": "contact1"}}

    def preserve(self, order, token):
        self.calls.append(("preserve", order, token))
        return {"status": 1, "msg": "Success.", "data": "Success."}

    def find_notpaid_order(self, account_id, trip_id, travel_date, token):
        self.calls.append(("find_notpaid_order", account_id, trip_id, travel_date, token))
        return {"id": "order1", "price": "100.0"}

    def pay(self, order_id, trip_id, user_id, price, token):
        self.calls.append(("pay", order_id, trip_id, user_id, price, token))
        return {"status": 1, "msg": "Success", "data": None}


def test_randomize_inputs_picks_route_and_valid_seat_type():
    gw = FakeGateway()
    random.seed(1)
    inputs = randomize_inputs(gw)
    assert inputs["from"] == "Su Zhou"
    assert inputs["to"] == "Shang Hai"
    assert inputs["seatType"] in (2, 3)
    assert inputs["username"] == "fdse_microservices@163.com"


def test_build_steps_has_expected_names_and_correlate_flags():
    gw = FakeGateway()
    inputs = {"from": "Su Zhou", "to": "Shang Hai", "date": "2026-09-10", "seatType": 2,
              "username": "fdse_microservices@163.com", "password": "DefaultPassword"}
    steps = build_steps(gw, inputs)
    names = [s.name for s in steps]
    assert names == ["login", "search_travel", "search_travel2", "ensure_contact", "preserve", "find_order", "pay"]
    correlate_by_name = {s.name: s.correlate for s in steps}
    assert correlate_by_name["login"] is True
    assert correlate_by_name["search_travel"] is True
    assert correlate_by_name["search_travel2"] is True
    assert correlate_by_name["ensure_contact"] is False
    assert correlate_by_name["preserve"] is True
    assert correlate_by_name["find_order"] is False
    assert correlate_by_name["pay"] is True


def test_full_step_sequence_threads_context_end_to_end():
    gw = FakeGateway()
    inputs = {"from": "Su Zhou", "to": "Shang Hai", "date": "2026-09-10", "seatType": 2,
              "username": "fdse_microservices@163.com", "password": "DefaultPassword"}
    steps = build_steps(gw, inputs)

    context = {}
    for step in steps:
        context.update(step.run(context))

    assert context["token"] == "tok"
    assert context["account_id"] == "acct1"
    assert context["contacts_id"] == "contact1"
    assert context["trip_id"] == "G42"
    assert context["order_id"] == "order1"
    assert context["price"] == "100.0"

    called_names = [c[0] for c in gw.calls]
    assert called_names == [
        "login", "search_left", "search_left", "list_contacts", "create_contact",
        "preserve", "find_notpaid_order", "pay",
    ]


def test_preserve_step_raises_when_no_trips_found():
    gw = FakeGateway()
    gw.search_left = lambda *a, **k: []
    inputs = {"from": "Su Zhou", "to": "Shang Hai", "date": "2026-09-10", "seatType": 2,
              "username": "fdse_microservices@163.com", "password": "DefaultPassword"}
    steps = build_steps(gw, inputs)
    context = {}
    for step in steps:
        if step.name == "preserve":
            try:
                step.run(context)
                assert False, "expected RuntimeError"
            except RuntimeError as e:
                assert "no trips" in str(e)
            break
        context.update(step.run(context))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_preserve_workflow.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tt_harness.workflows.preserve'`

- [ ] **Step 3: Write minimal implementation**

`tt_harness/workflows/preserve.py`:
```python
"""The login -> search -> book -> pay workflow ('preserve' flow).

Two gaps beyond what the spec's controllers list covers, both handled as
uncorrelated plumbing steps (see docs/superpowers/specs/.../design.md and
this plan's "Known gaps beyond what the spec called out" section):
  - preserve requires an existing contactsId -> `ensure_contact` step.
  - preserve's response does not carry the created orderId (it returns
    createOrder's message string as `data`, not the Order) -> `find_order`
    step looks it up via order/query filtered to NOTPAID.
"""
import random
from datetime import date, timedelta

from tt_harness.workflows.base import WorkflowDefinition, WorkflowStep

DEFAULT_USERNAME = "fdse_microservices@163.com"
DEFAULT_PASSWORD = "DefaultPassword"
SEAT_TYPES = [2, 3]  # SeatClass.FIRSTCLASS, SeatClass.SECONDCLASS


def randomize_inputs(gateway) -> dict:
    routes = gateway.list_routes()
    route = random.choice(routes)
    departure = date.today() + timedelta(days=random.randint(5, 30))
    return {
        "from": route["startStation"],
        "to": route["endStation"],
        "date": departure.strftime("%Y-%m-%d"),
        "seatType": random.choice(SEAT_TYPES),
        "username": DEFAULT_USERNAME,
        "password": DEFAULT_PASSWORD,
    }


def build_steps(gateway, inputs: dict) -> list[WorkflowStep]:
    def do_login(ctx: dict) -> dict:
        resp = gateway.login(inputs["username"], inputs["password"])
        if resp.get("status") != 1:
            raise RuntimeError(f"login failed: {resp.get('msg')}")
        data = resp["data"]
        return {"token": data["token"], "account_id": data["userId"]}

    def do_search_travel(ctx: dict) -> dict:
        trips = gateway.search_left(inputs["from"], inputs["to"], inputs["date"], ctx["token"], service="travel")
        return {"travel_trips": trips}

    def do_search_travel2(ctx: dict) -> dict:
        trips = gateway.search_left(inputs["from"], inputs["to"], inputs["date"], ctx["token"], service="travel2")
        return {"travel2_trips": trips}

    def do_ensure_contact(ctx: dict) -> dict:
        contacts = gateway.list_contacts(ctx["account_id"], ctx["token"])
        if contacts:
            return {"contacts_id": contacts[0]["id"]}
        created = gateway.create_contact(
            account_id=ctx["account_id"],
            name="tt-harness-" + str(random.randint(1000, 9999)),
            document_type=1,
            document_number=str(random.randint(10 ** 9, 10 ** 10 - 1)),
            phone_number="1" + str(random.randint(10 ** 9, 10 ** 10 - 1)),
            token=ctx["token"],
        )
        return {"contacts_id": created["data"]["id"]}

    def do_preserve(ctx: dict) -> dict:
        all_trips = list(ctx.get("travel_trips") or []) + list(ctx.get("travel2_trips") or [])
        if not all_trips:
            raise RuntimeError("no trips found for randomized route/date")
        trip = random.choice(all_trips)
        trip_id = trip["tripId"]["type"] + trip["tripId"]["number"]
        order = {
            "accountId": ctx["account_id"],
            "contactsId": ctx["contacts_id"],
            "tripId": trip_id,
            "seatType": inputs["seatType"],
            "date": inputs["date"],
            "from": inputs["from"],
            "to": inputs["to"],
            "assurance": 0,
        }
        resp = gateway.preserve(order, ctx["token"])
        if resp.get("status") != 1:
            raise RuntimeError(f"preserve failed: {resp.get('msg')}")
        return {"trip_id": trip_id}

    def do_find_order(ctx: dict) -> dict:
        order = gateway.find_notpaid_order(ctx["account_id"], ctx["trip_id"], inputs["date"], ctx["token"])
        if order is None:
            raise RuntimeError("could not find the just-created NOTPAID order")
        return {"order_id": order["id"], "price": order.get("price", "0")}

    def do_pay(ctx: dict) -> dict:
        resp = gateway.pay(
            order_id=ctx["order_id"], trip_id=ctx["trip_id"],
            user_id=ctx["account_id"], price=ctx["price"], token=ctx["token"],
        )
        if resp.get("status") != 1:
            raise RuntimeError(f"payment failed: {resp.get('msg')}")
        return {"payment_status": resp.get("msg")}

    return [
        WorkflowStep("login", "/api/v1/users/login", do_login, correlate=True),
        WorkflowStep("search_travel", "/api/v1/travelservice/trips/left", do_search_travel, correlate=True),
        WorkflowStep("search_travel2", "/api/v1/travel2service/trips/left", do_search_travel2, correlate=True),
        WorkflowStep("ensure_contact", "/api/v1/contactservice/contacts", do_ensure_contact, correlate=False),
        WorkflowStep("preserve", "/api/v1/preserveservice/preserve", do_preserve, correlate=True),
        WorkflowStep("find_order", "/api/v1/orderservice/order/query", do_find_order, correlate=False),
        WorkflowStep("pay", "/api/v1/inside_pay_service/inside_payment", do_pay, correlate=True),
    ]


PRESERVE_WORKFLOW = WorkflowDefinition(
    name="preserve",
    randomize_inputs=randomize_inputs,
    build_steps=build_steps,
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_preserve_workflow.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add tt_harness/workflows/preserve.py tests/test_preserve_workflow.py
git commit -m "feat: add preserve (login-search-book-pay) workflow definition"
```

---

### Task 5: WorkflowRunner

**Files:**
- Create: `tt-trace-harness/tt_harness/runner.py`
- Test: `tt-trace-harness/tests/test_runner.py`

**Interfaces:**
- Consumes: `WorkflowDefinition`, `WorkflowStep`, `StepResult`, `RunResult` from `tt_harness.workflows.base` (Task 3). Optionally a correlator object shaped like Task 6's `TraceCorrelator` (duck-typed: `.correlate(step_result) -> CorrelationResult` with `.status`, `.trace_id`, `.spans`).
- Produces: `tt_harness.runner.WorkflowRunner(gateway, correlator=None, now_fn=time.time)` with `run_once(self, definition: WorkflowDefinition, run_id: str) -> RunResult` and `run_many(self, definition: WorkflowDefinition, count: int, run_id_fn=None) -> list[RunResult]`.

- [ ] **Step 1: Write the failing test**

`tests/test_runner.py`:
```python
from tt_harness.runner import WorkflowRunner
from tt_harness.workflows.base import WorkflowDefinition, WorkflowStep


class FakeCorrelationResult:
    def __init__(self, status, trace_id, spans):
        self.status = status
        self.trace_id = trace_id
        self.spans = spans


class FakeCorrelator:
    def __init__(self):
        self.calls = []

    def correlate(self, step_result):
        self.calls.append(step_result.step_name)
        return FakeCorrelationResult("matched", "trace-" + step_result.step_name, [{"spanId": 0}])


def _clock():
    t = [0.0]
    def now():
        t[0] += 1.0
        return t[0]
    return now


def test_run_once_success_records_steps_and_calls_correlator_for_correlate_true_steps():
    def build_steps(gateway, inputs):
        return [
            WorkflowStep("a", "/a", lambda ctx: {"x": 1}, correlate=True),
            WorkflowStep("b", "/b", lambda ctx: {"y": ctx["x"] + 1}, correlate=False),
        ]

    definition = WorkflowDefinition(name="wf", randomize_inputs=lambda gw: {}, build_steps=build_steps)
    correlator = FakeCorrelator()
    runner = WorkflowRunner(gateway=None, correlator=correlator, now_fn=_clock())

    result = runner.run_once(definition, run_id="run1")

    assert result.success is True
    assert result.failure_reason is None
    assert [s.step_name for s in result.steps] == ["a", "b"]
    assert result.steps[0].outputs == {"x": 1}
    assert result.steps[1].outputs == {"y": 2}
    assert correlator.calls == ["a"]  # only the correlate=True step
    assert result.steps[0].correlation_status == "matched"
    assert result.steps[0].trace_id == "trace-a"
    assert result.steps[1].correlation_status == "not_applicable"


def test_run_once_step_failure_aborts_run_but_keeps_prior_steps():
    def failing_step(ctx):
        raise ValueError("boom")

    def build_steps(gateway, inputs):
        return [
            WorkflowStep("a", "/a", lambda ctx: {"x": 1}, correlate=False),
            WorkflowStep("b", "/b", failing_step, correlate=False),
            WorkflowStep("c", "/c", lambda ctx: {"never": True}, correlate=False),
        ]

    definition = WorkflowDefinition(name="wf", randomize_inputs=lambda gw: {}, build_steps=build_steps)
    runner = WorkflowRunner(gateway=None, correlator=None, now_fn=_clock())

    result = runner.run_once(definition, run_id="run2")

    assert result.success is False
    assert "boom" in result.failure_reason
    assert [s.step_name for s in result.steps] == ["a", "b"]
    assert result.steps[1].success is False


def test_run_many_generates_distinct_runs():
    def build_steps(gateway, inputs):
        return [WorkflowStep("a", "/a", lambda ctx: {}, correlate=False)]

    definition = WorkflowDefinition(name="wf", randomize_inputs=lambda gw: {}, build_steps=build_steps)
    runner = WorkflowRunner(gateway=None, correlator=None, now_fn=_clock())

    results = runner.run_many(definition, count=3, run_id_fn=lambda i: f"run-{i}")

    assert [r.run_id for r in results] == ["run-0", "run-1", "run-2"]
    assert all(r.success for r in results)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_runner.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tt_harness.runner'`

- [ ] **Step 3: Write minimal implementation**

`tt_harness/runner.py`:
```python
import time

from tt_harness.workflows.base import RunResult, StepResult, WorkflowDefinition


class WorkflowRunner:
    def __init__(self, gateway, correlator=None, now_fn=time.time):
        self.gateway = gateway
        self.correlator = correlator
        self.now_fn = now_fn

    def run_once(self, definition: WorkflowDefinition, run_id: str) -> RunResult:
        inputs = definition.randomize_inputs(self.gateway)
        steps = definition.build_steps(self.gateway, inputs)
        context: dict = {}
        step_results: list[StepResult] = []

        for step in steps:
            start = self.now_fn()
            try:
                outputs = step.run(context)
            except Exception as e:
                end = self.now_fn()
                result = StepResult(step.name, step.endpoint, start, end, False, str(e), {}, step.correlate)
                step_results.append(result)
                return RunResult(run_id, definition.name, inputs, False, step_results, str(e))

            end = self.now_fn()
            context.update(outputs)
            result = StepResult(step.name, step.endpoint, start, end, True, None, outputs, step.correlate)
            if self.correlator is not None and result.correlate:
                self._correlate(result)
            step_results.append(result)

        return RunResult(run_id, definition.name, inputs, True, step_results, None)

    def _correlate(self, result: StepResult) -> None:
        cr = self.correlator.correlate(result)
        result.correlation_status = cr.status
        result.trace_id = cr.trace_id
        result.spans = cr.spans

    def run_many(self, definition: WorkflowDefinition, count: int, run_id_fn=None) -> list[RunResult]:
        if run_id_fn is None:
            import uuid
            run_id_fn = lambda i: str(uuid.uuid4())
        return [self.run_once(definition, run_id_fn(i)) for i in range(count)]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_runner.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add tt_harness/runner.py tests/test_runner.py
git commit -m "feat: add WorkflowRunner for sequential N-run execution"
```

---

### Task 6: TraceCorrelator

**Files:**
- Create: `tt-trace-harness/tt_harness/correlator.py`
- Test: `tt-trace-harness/tests/test_correlator.py`

**Interfaces:**
- Consumes: a `SkyWalkingClient`-shaped object (Task 1: `.query_basic_traces(start, end, trace_state, page_num, page_size) -> list[dict]`, `.query_trace(trace_id) -> list[dict]`) and a `StepResult`-shaped object (Task 3: `.start: float`, `.end: float`, `.endpoint: str`).
- Produces: `tt_harness.correlator.CorrelationResult` dataclass (`status: str, trace_id: str | None, spans: list`) and `tt_harness.correlator.TraceCorrelator(sw_client, pad_seconds=3.0, max_wait_seconds=15.0, retry_interval_seconds=2.0, now_fn=time.time, sleep_fn=time.sleep)` with `correlate(self, step) -> CorrelationResult`.

- [ ] **Step 1: Write the failing test**

`tests/test_correlator.py`:
```python
from dataclasses import dataclass

from tt_harness.correlator import TraceCorrelator


@dataclass
class FakeStep:
    start: float
    end: float
    endpoint: str


class FakeSkyWalkingClient:
    """query_basic_traces returns queued lists of trace-summary dicts, one list per call."""
    def __init__(self, basic_traces_by_call, trace_by_id=None):
        self._queue = list(basic_traces_by_call)
        self.query_basic_traces_calls = 0
        self.trace_by_id = trace_by_id or {}

    def query_basic_traces(self, start, end, trace_state="ALL", page_num=1, page_size=20):
        self.query_basic_traces_calls += 1
        if self._queue:
            return self._queue.pop(0)
        return []

    def query_trace(self, trace_id):
        return self.trace_by_id.get(trace_id, [])


def _clock_and_sleep():
    t = [1000.0]
    def now():
        return t[0]
    def sleep(seconds):
        t[0] += seconds
    return now, sleep


def test_unambiguous_match_returns_matched():
    step = FakeStep(start=1000.0, end=1001.0, endpoint="/api/v1/users/login")
    traces = [{"endpointNames": ["POST:/api/v1/users/login"], "start": 1000500, "traceIds": ["t1"]}]
    sw = FakeSkyWalkingClient([traces], trace_by_id={"t1": [{"spanId": 0}]})
    now, sleep = _clock_and_sleep()

    correlator = TraceCorrelator(sw, now_fn=now, sleep_fn=sleep)
    result = correlator.correlate(step)

    assert result.status == "matched"
    assert result.trace_id == "t1"
    assert result.spans == [{"spanId": 0}]
    assert sw.query_basic_traces_calls == 1


def test_multiple_candidates_picks_closest_start_to_step_start():
    step = FakeStep(start=1000.0, end=1001.0, endpoint="/api/v1/users/login")
    step_start_ms = 1000000
    traces = [
        {"endpointNames": ["POST:/api/v1/users/login"], "start": step_start_ms + 900, "traceIds": ["far"]},
        {"endpointNames": ["POST:/api/v1/users/login"], "start": step_start_ms + 100, "traceIds": ["close"]},
    ]
    sw = FakeSkyWalkingClient([traces], trace_by_id={"close": [{"spanId": 1}]})
    now, sleep = _clock_and_sleep()

    correlator = TraceCorrelator(sw, now_fn=now, sleep_fn=sleep)
    result = correlator.correlate(step)

    assert result.status == "matched"
    assert result.trace_id == "close"


def test_no_candidates_after_max_wait_returns_failed():
    step = FakeStep(start=1000.0, end=1001.0, endpoint="/api/v1/users/login")
    sw = FakeSkyWalkingClient([[], [], []])  # always empty
    now, sleep = _clock_and_sleep()

    correlator = TraceCorrelator(sw, max_wait_seconds=5.0, retry_interval_seconds=2.0, now_fn=now, sleep_fn=sleep)
    result = correlator.correlate(step)

    assert result.status == "failed"
    assert result.trace_id is None
    assert result.spans == []


def test_retry_then_match_succeeds_on_second_attempt():
    step = FakeStep(start=1000.0, end=1001.0, endpoint="/api/v1/users/login")
    step_start_ms = 1000000
    traces = [{"endpointNames": ["POST:/api/v1/users/login"], "start": step_start_ms + 100, "traceIds": ["t2"]}]
    sw = FakeSkyWalkingClient([[], traces], trace_by_id={"t2": [{"spanId": 2}]})
    now, sleep = _clock_and_sleep()

    correlator = TraceCorrelator(sw, max_wait_seconds=15.0, retry_interval_seconds=2.0, now_fn=now, sleep_fn=sleep)
    result = correlator.correlate(step)

    assert result.status == "matched"
    assert result.trace_id == "t2"
    assert sw.query_basic_traces_calls == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_correlator.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tt_harness.correlator'`

- [ ] **Step 3: Write minimal implementation**

`tt_harness/correlator.py`:
```python
import time
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass
class CorrelationResult:
    status: str  # "matched" | "failed"
    trace_id: str | None
    spans: list


class TraceCorrelator:
    def __init__(self, sw_client, pad_seconds: float = 3.0, max_wait_seconds: float = 15.0,
                 retry_interval_seconds: float = 2.0, now_fn=time.time, sleep_fn=time.sleep):
        self.sw_client = sw_client
        self.pad_seconds = pad_seconds
        self.max_wait_seconds = max_wait_seconds
        self.retry_interval_seconds = retry_interval_seconds
        self.now_fn = now_fn
        self.sleep_fn = sleep_fn

    def correlate(self, step) -> CorrelationResult:
        deadline = self.now_fn() + self.max_wait_seconds
        start_ms = int((step.start - self.pad_seconds) * 1000)
        end_ms = int((step.end + self.pad_seconds) * 1000)

        while True:
            candidates = self._find_candidates(step, start_ms, end_ms)
            if candidates:
                best = min(candidates, key=lambda t: abs(t["start"] - (step.start * 1000)))
                trace_id = best["traceIds"][0]
                spans = self.sw_client.query_trace(trace_id)
                return CorrelationResult("matched", trace_id, spans)
            if self.now_fn() >= deadline:
                return CorrelationResult("failed", None, [])
            self.sleep_fn(self.retry_interval_seconds)

    def _find_candidates(self, step, start_ms: int, end_ms: int) -> list[dict]:
        window_start = datetime.fromtimestamp(start_ms / 1000).strftime("%Y-%m-%d %H%M")
        window_end = datetime.fromtimestamp(end_ms / 1000 + 60).strftime("%Y-%m-%d %H%M")
        traces = self.sw_client.query_basic_traces(start=window_start, end=window_end)
        return [
            t for t in traces
            if any(step.endpoint in name for name in t.get("endpointNames", []))
            and start_ms <= t["start"] <= end_ms
        ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_correlator.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add tt_harness/correlator.py tests/test_correlator.py
git commit -m "feat: add TraceCorrelator with padded-window retry matching"
```

---

### Task 7: DependencyAnalyzer

**Files:**
- Create: `tt-trace-harness/tt_harness/analyzer.py`
- Test: `tt-trace-harness/tests/test_analyzer.py`

**Interfaces:**
- Consumes: `RunResult`/`StepResult` shape from Task 3 (specifically `run.steps[i].spans`, a list of span dicts as returned by `SkyWalkingClient.query_trace` — each with `spanId, parentSpanId, serviceCode, endpointName, startTime, endTime, isError`).
- Produces: `tt_harness.analyzer.EdgeStats` dataclass (`source: str, target: str, call_count: int, durations_ms: list[float], error_count: int`) with property methods `avg_ms`, `p50_ms`, `p95_ms`, `max_ms`. Produces `tt_harness.analyzer.DependencyAnalyzer` with `analyze(self, runs: list) -> tuple[dict[tuple[str, str], EdgeStats], dict[tuple[str, str], EdgeStats]]` returning `(service_edges, endpoint_edges)`.

**Design note:** self-time (spec: "computed from span self-time... so a slow parent isn't blamed for a slow child's time") is used for the edge's *own* latency stat — i.e., the edge `parent -> child`'s duration is the child span's self-time (its own `duration` minus the sum of *its own* direct children's durations), not the parent's. This reflects "how long did this specific hop's work take, excluding further downstream hops."

- [ ] **Step 1: Write the failing test**

`tests/test_analyzer.py` — a hand-built 3-span tree: `ts-gateway (root, self-time irrelevant) -> ts-auth-service (child, has its own child) -> ts-user-service (grandchild)`. Root span type is entry with no parent (`parentSpanId == -1`), matching SkyWalking's convention for the trace's first span.

```python
from tt_harness.analyzer import DependencyAnalyzer


def _span(span_id, parent_span_id, service, endpoint, start, end, is_error=False):
    return {
        "spanId": span_id, "parentSpanId": parent_span_id,
        "serviceCode": service, "endpointName": endpoint,
        "startTime": start, "endTime": end, "isError": is_error,
    }


def test_analyze_builds_service_and_endpoint_edges_with_self_time():
    # ts-auth-service span: 100ms wall time, but its child (ts-user-service) takes 40ms,
    # so ts-auth-service's self-time on this hop is 60ms.
    spans = [
        _span(0, -1, "ts-gateway-service", "/api/v1/users/login", 0, 120),
        _span(1, 0, "ts-auth-service", "/api/v1/users/login", 10, 110),
        _span(2, 1, "ts-user-service", "/api/v1/users/findByUsername", 30, 70),
    ]
    run = type("Run", (), {"steps": [
        type("Step", (), {"spans": spans, "correlate": True, "correlation_status": "matched"})()
    ]})()

    analyzer = DependencyAnalyzer()
    service_edges, endpoint_edges = analyzer.analyze([run])

    key = ("ts-gateway-service", "ts-auth-service")
    assert key in service_edges
    assert service_edges[key].call_count == 1
    assert service_edges[key].durations_ms == [60.0]  # 110-10 self-time (no grandchild subtracted here, auth->user is the next edge)

    key2 = ("ts-auth-service", "ts-user-service")
    assert service_edges[key2].call_count == 1
    assert service_edges[key2].durations_ms == [40.0]

    # endpoint-level uses endpointName instead of serviceCode
    ekey = ("/api/v1/users/login", "/api/v1/users/findByUsername")
    assert ekey in endpoint_edges
    assert endpoint_edges[ekey].call_count == 1


def test_analyze_aggregates_across_multiple_runs_and_counts_errors():
    spans_ok = [
        _span(0, -1, "ts-gateway-service", "/e", 0, 100),
        _span(1, 0, "ts-auth-service", "/e", 0, 50, is_error=False),
    ]
    spans_err = [
        _span(0, -1, "ts-gateway-service", "/e", 0, 200),
        _span(1, 0, "ts-auth-service", "/e", 0, 80, is_error=True),
    ]
    run1 = type("Run", (), {"steps": [type("Step", (), {"spans": spans_ok, "correlate": True, "correlation_status": "matched"})()]})()
    run2 = type("Run", (), {"steps": [type("Step", (), {"spans": spans_err, "correlate": True, "correlation_status": "matched"})()]})()

    analyzer = DependencyAnalyzer()
    service_edges, _ = analyzer.analyze([run1, run2])

    key = ("ts-gateway-service", "ts-auth-service")
    edge = service_edges[key]
    assert edge.call_count == 2
    assert edge.error_count == 1
    assert edge.avg_ms == 65.0
    assert edge.max_ms == 80.0


def test_analyze_skips_uncorrelated_and_failed_steps():
    spans = [_span(0, -1, "ts-gateway-service", "/e", 0, 100)]
    ok_step = type("Step", (), {"spans": spans, "correlate": True, "correlation_status": "matched"})()
    skip_step = type("Step", (), {"spans": [], "correlate": False, "correlation_status": "not_applicable"})()
    failed_step = type("Step", (), {"spans": [], "correlate": True, "correlation_status": "failed"})()
    run = type("Run", (), {"steps": [ok_step, skip_step, failed_step]})()

    analyzer = DependencyAnalyzer()
    service_edges, _ = analyzer.analyze([run])

    assert service_edges == {}  # single span, no parent-child pair -> no edges
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_analyzer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tt_harness.analyzer'`

- [ ] **Step 3: Write minimal implementation**

`tt_harness/analyzer.py`:
```python
from dataclasses import dataclass, field


@dataclass
class EdgeStats:
    source: str
    target: str
    call_count: int = 0
    durations_ms: list = field(default_factory=list)
    error_count: int = 0

    @property
    def avg_ms(self) -> float:
        return sum(self.durations_ms) / len(self.durations_ms) if self.durations_ms else 0.0

    @property
    def p50_ms(self) -> float:
        return self._percentile(0.50)

    @property
    def p95_ms(self) -> float:
        return self._percentile(0.95)

    @property
    def max_ms(self) -> float:
        return max(self.durations_ms) if self.durations_ms else 0.0

    def _percentile(self, p: float) -> float:
        if not self.durations_ms:
            return 0.0
        ordered = sorted(self.durations_ms)
        idx = min(int(len(ordered) * p), len(ordered) - 1)
        return ordered[idx]


class DependencyAnalyzer:
    def analyze(self, runs: list) -> tuple[dict, dict]:
        service_edges: dict[tuple[str, str], EdgeStats] = {}
        endpoint_edges: dict[tuple[str, str], EdgeStats] = {}

        for run in runs:
            for step in run.steps:
                if not step.correlate or step.correlation_status != "matched":
                    continue
                self._process_span_tree(step.spans, service_edges, endpoint_edges)

        return service_edges, endpoint_edges

    def _process_span_tree(self, spans: list[dict], service_edges: dict, endpoint_edges: dict) -> None:
        by_id = {s["spanId"]: s for s in spans}
        children_by_parent: dict[int, list[dict]] = {}
        for s in spans:
            children_by_parent.setdefault(s["parentSpanId"], []).append(s)

        for span in spans:
            parent = by_id.get(span["parentSpanId"])
            if parent is None:
                continue  # root span, no incoming edge to record
            self_time = self._self_time_ms(span, children_by_parent.get(span["spanId"], []))
            is_error = bool(span.get("isError"))

            if parent["serviceCode"] != span["serviceCode"]:
                self._record(service_edges, parent["serviceCode"], span["serviceCode"], self_time, is_error)
            self._record(endpoint_edges, parent["endpointName"], span["endpointName"], self_time, is_error)

    def _self_time_ms(self, span: dict, direct_children: list[dict]) -> float:
        own = span["endTime"] - span["startTime"]
        children_total = sum(c["endTime"] - c["startTime"] for c in direct_children)
        return float(max(own - children_total, 0))

    def _record(self, edges: dict, source: str, target: str, duration_ms: float, is_error: bool) -> None:
        key = (source, target)
        if key not in edges:
            edges[key] = EdgeStats(source=source, target=target)
        edge = edges[key]
        edge.call_count += 1
        edge.durations_ms.append(duration_ms)
        if is_error:
            edge.error_count += 1
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_analyzer.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add tt_harness/analyzer.py tests/test_analyzer.py
git commit -m "feat: add DependencyAnalyzer with self-time latency aggregation"
```

---

### Task 8: Exporters

**Files:**
- Create: `tt-trace-harness/tt_harness/export.py`
- Test: `tt-trace-harness/tests/test_export.py`

**Interfaces:**
- Consumes: `RunResult`/`StepResult` shape (Task 3/5) and `EdgeStats` dict shape (Task 7).
- Produces:
  - `write_runs_jsonl(runs: list, path: str) -> None`
  - `write_runs_summary_csv(runs: list, path: str) -> None`
  - `write_dependency_csv(edges: dict, path: str) -> None`

- [ ] **Step 1: Write the failing test**

`tests/test_export.py`:
```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_export.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tt_harness.export'`

- [ ] **Step 3: Write minimal implementation**

`tt_harness/export.py`:
```python
import csv
import dataclasses
import json


def write_runs_jsonl(runs: list, path: str) -> None:
    with open(path, "w") as f:
        for run in runs:
            record = {
                "run_id": run.run_id,
                "workflow": run.workflow,
                "inputs": run.inputs,
                "success": run.success,
                "failure_reason": run.failure_reason,
                "steps": [
                    {
                        "step_name": s.step_name,
                        "endpoint": s.endpoint,
                        "start": s.start,
                        "end": s.end,
                        "success": s.success,
                        "error": s.error,
                        "correlate": s.correlate,
                        "correlation_status": s.correlation_status,
                        "trace_id": s.trace_id,
                        "spans": s.spans,
                    }
                    for s in run.steps
                ],
            }
            f.write(json.dumps(record) + "\n")


def write_runs_summary_csv(runs: list, path: str) -> None:
    fieldnames = ["run_id", "workflow", "total_duration_ms", "step_durations", "success", "failure_reason"]
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for run in runs:
            step_durations = {s.step_name: (s.end - s.start) * 1000 for s in run.steps}
            total_ms = sum(step_durations.values())
            writer.writerow({
                "run_id": run.run_id,
                "workflow": run.workflow,
                "total_duration_ms": total_ms,
                "step_durations": json.dumps(step_durations),
                "success": run.success,
                "failure_reason": run.failure_reason or "",
            })


def write_dependency_csv(edges: dict, path: str) -> None:
    fieldnames = ["source_service", "target_service", "call_count", "avg_ms", "p50_ms", "p95_ms", "max_ms", "error_count"]
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for edge in edges.values():
            writer.writerow({
                "source_service": edge.source,
                "target_service": edge.target,
                "call_count": edge.call_count,
                "avg_ms": round(edge.avg_ms, 2),
                "p50_ms": round(edge.p50_ms, 2),
                "p95_ms": round(edge.p95_ms, 2),
                "max_ms": round(edge.max_ms, 2),
                "error_count": edge.error_count,
            })
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_export.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add tt_harness/export.py tests/test_export.py
git commit -m "feat: add CSV/JSONL exporters for runs and dependency graphs"
```

---

### Task 9: CLI

**Files:**
- Create: `tt-trace-harness/tt_harness/cli.py`
- Create: `tt-trace-harness/tt_harness/__main__.py`
- Test: `tt-trace-harness/tests/test_cli.py`

**Interfaces:**
- Consumes every module from Tasks 1-8: `GatewayClient`, `SkyWalkingClient`, `TraceCorrelator`, `WorkflowRunner`, `PRESERVE_WORKFLOW`, `DependencyAnalyzer`, `export.write_runs_jsonl`/`write_runs_summary_csv`/`write_dependency_csv`.
- Produces: `tt_harness.cli.build_arg_parser() -> argparse.ArgumentParser`, `tt_harness.cli.run_command(args, runner=None, analyzer=None) -> int` (the `runner`/`analyzer` injection points exist purely so the CLI's orchestration logic is unit-testable without a live cluster), `tt_harness.cli.main(argv=None) -> int`.

- [ ] **Step 1: Write the failing test**

`tests/test_cli.py`:
```python
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

    def run_many(self, definition, count, run_id_fn=None):
        self.run_many_calls.append((definition.name, count))
        return self._results


def test_run_command_writes_all_four_output_files(tmp_path):
    parser = build_arg_parser()
    args = parser.parse_args(["run", "preserve", "--count", "2", "--out-dir", str(tmp_path)])

    step = StepResult("login", "/api/v1/users/login", 0.0, 0.1, True, None, {}, True,
                       correlation_status="matched", trace_id="t1",
                       spans=[{"spanId": 0, "parentSpanId": -1, "serviceCode": "ts-gateway-service",
                               "endpointName": "/e", "startTime": 0, "endTime": 100, "isError": False}])
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tt_harness.cli'`

- [ ] **Step 3: Write minimal implementation**

`tt_harness/cli.py`:
```python
import argparse
import os
import sys
import uuid

from tt_harness.analyzer import DependencyAnalyzer
from tt_harness.client_gateway import GatewayClient
from tt_harness.client_sw import SkyWalkingClient
from tt_harness.correlator import TraceCorrelator
from tt_harness.export import write_dependency_csv, write_runs_jsonl, write_runs_summary_csv
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
    results = runner.run_many(definition, args.count, run_id_fn=lambda i: str(uuid.uuid4()))

    for i, result in enumerate(results, start=1):
        status = "OK" if result.success else f"FAILED: {result.failure_reason}"
        print(f"run {i}/{len(results)}: {status}", file=sys.stderr)

    service_edges, endpoint_edges = analyzer.analyze(results)

    os.makedirs(args.out_dir, exist_ok=True)
    write_runs_jsonl(results, os.path.join(args.out_dir, "runs.jsonl"))
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
```

`tt_harness/__main__.py`:
```python
import sys

from tt_harness.cli import main

if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_cli.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the full test suite**

Run: `cd /home/ml3787/tt-trace-harness && .venv/bin/python -m pytest tests/ -v`
Expected: PASS (all tests from Tasks 1-9)

- [ ] **Step 6: Commit**

```bash
git add tt_harness/cli.py tt_harness/__main__.py tests/test_cli.py
git commit -m "feat: add CLI entry point wiring runner, analyzer, and exporters"
```

---

### Task 10: Smoke test script + README

**Files:**
- Create: `tt-trace-harness/scripts/smoke_test_preserve.py`
- Create: `tt-trace-harness/README.md`

**Interfaces:**
- Consumes: `GatewayClient`, `SkyWalkingClient`, `TraceCorrelator`, `WorkflowRunner`, `PRESERVE_WORKFLOW` (all prior tasks). No new interfaces produced — this is the manual integration check the spec's Testing section requires ("run manually, not CI, since it depends on a live cluster and creates real data").

- [ ] **Step 1: Write the smoke test script**

`scripts/smoke_test_preserve.py`:
```python
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
```

- [ ] **Step 2: Make it executable**

```bash
chmod +x /home/ml3787/tt-trace-harness/scripts/smoke_test_preserve.py
```

- [ ] **Step 3: Write the README**

`README.md`:
```markdown
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
```

- [ ] **Step 4: Run the full test suite one more time**

Run: `cd /home/ml3787/tt-trace-harness && .venv/bin/python -m pytest tests/ -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/smoke_test_preserve.py README.md
git commit -m "docs: add manual smoke test script and README"
```

- [ ] **Step 6: (Manual, not part of automated execution) Run the smoke test against the live cluster**

With the two `kubectl port-forward` commands running in separate terminals:

```bash
cd /home/ml3787/tt-trace-harness
.venv/bin/python scripts/smoke_test_preserve.py
```

Expected: `SMOKE TEST PASSED: every correlation-point step matched a trace.`
If it fails on `correlation_status: "failed"` for a step, first suspect OAP
ingestion lag — rerun; if it persists, check the step's `endpoint` value
against the actual `endpointNames` SkyWalking reports for that call (may
need a wider substring than currently used in `correlator.py`'s
`_find_candidates`).

---

## Self-Review Notes

**Spec coverage:**
- CLI running `preserve --count N` with randomized route/date/seat → Tasks 4, 9.
- Per-run correlation of every top-level HTTP call to its trace, full span tree pulled → Tasks 5, 6.
- Service-level and endpoint-level dependency graph with latency stats from a batch → Task 7.
- CSV/JSON export per the spec's exact output schema → Task 8.
- Reuses `export_traces.py`'s proven GraphQL query shapes → Task 1 (constants copied verbatim).
- The 4-5 top-level correlation points (login, search×2, preserve, pay) → Task 4's `correlate=True` steps.
- `contactsId` gap (spec's own "One gap surfaced...") → Task 4's `ensure_contact` step.
- Step failure aborts only that run, recorded with `failure_reason`; correlation failure recorded, nothing dropped → Task 5 (`run_once`), Task 8 (`write_runs_jsonl` includes every step regardless of correlation outcome).
- Side effect (real orders/payments) flagged, not solved → README, docstring in `scripts/smoke_test_preserve.py`.
- Unit tests for `TraceCorrelator` matching logic (unambiguous, multiple candidates, no candidates, retry-then-match) → Task 6, all four scenarios present.
- Unit tests for `DependencyAnalyzer` edge/latency aggregation against a fixture trace → Task 7.
- Manual smoke test against the live cluster, not CI → Task 10.
- Future work items (ActiveSpan enrichment, viewer, config comparison, cross-app) are explicitly out of scope for this plan, matching the spec's deferral.

**Gaps found beyond the spec, and how they're handled:** documented up front in "Known gaps beyond what the spec called out" and threaded through Tasks 2 and 4 (`find_order` step, `find_notpaid_order` client method) rather than surfacing as a surprise mid-plan.

**Type consistency check:** `StepResult`, `WorkflowStep`, `WorkflowDefinition`, `RunResult` (Task 3) are used with identical field names in Tasks 4, 5, 6, 7, 8, 9 — cross-checked `correlate`, `correlation_status`, `trace_id`, `spans`, `outputs` naming stays consistent throughout. `EdgeStats` (Task 7) field names (`source`, `target`, `call_count`, `durations_ms`, `error_count`, plus `avg_ms`/`p50_ms`/`p95_ms`/`max_ms` properties) match what Task 8's `write_dependency_csv` reads. `GatewayClient` method signatures (Task 2) match exactly how Task 4's `build_steps` calls them (argument names and order).
```
