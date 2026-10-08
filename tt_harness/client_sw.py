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
                            page_num: int = 1, page_size: int = 20, step: str = "MINUTE") -> list[dict]:
        condition = {
            "queryDuration": {"start": start, "end": end, "step": step},
            "traceState": trace_state,
            "queryOrder": "BY_START_TIME",
            "paging": {"pageNum": page_num, "pageSize": page_size},
        }
        data = post_graphql(self.endpoint, BASIC_TRACES_QUERY, {"condition": condition}, self.timeout)
        return data["queryBasicTraces"]["traces"]

    def query_trace(self, trace_id: str) -> list[dict]:
        data = post_graphql(self.endpoint, FULL_TRACE_QUERY, {"traceId": trace_id}, self.timeout)
        return data["queryTrace"]["spans"]
