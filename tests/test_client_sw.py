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
