import time
from dataclasses import dataclass
from datetime import datetime, timezone


def _utc_minute(epoch_ms: float) -> str:
    """Format epoch milliseconds as OAP's 'yyyy-MM-dd HHmm', in UTC."""
    return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H%M")


@dataclass
class CorrelationResult:
    status: str  # "matched" | "failed"
    trace_id: str | None
    spans: list


class TraceCorrelator:
    def __init__(self, sw_client, pad_seconds: float = 3.0, max_wait_seconds: float = 15.0,
                 retry_interval_seconds: float = 2.0, page_size: int = 100,
                 now_fn=time.time, sleep_fn=time.sleep):
        self.sw_client = sw_client
        self.pad_seconds = pad_seconds
        self.max_wait_seconds = max_wait_seconds
        self.retry_interval_seconds = retry_interval_seconds
        # All filtering is client-side, so the target trace must actually be on
        # the page we fetch. The client default of 20 is easily exhausted by
        # background cluster traffic within the query window, which shows up as
        # a spurious correlation failure that looks like ingestion lag.
        self.page_size = page_size
        self.now_fn = now_fn
        self.sleep_fn = sleep_fn

    def correlate(self, step) -> CorrelationResult:
        deadline = self.now_fn() + self.max_wait_seconds
        start_ms = int((step.start - self.pad_seconds) * 1000)
        end_ms = int((step.end + self.pad_seconds) * 1000)
        best_result = None

        while True:
            candidates = self._find_candidates(step, start_ms, end_ms)
            if candidates:
                # ts-gateway-service (Zuul/Hystrix) does not propagate trace
                # context into its downstream call: every request through it
                # produces TWO disconnected SkyWalking traces sharing the same
                # endpoint name and near-identical start time -- a near-instant
                # (~1ms) stub for the gateway's own proxy hop, and a separate
                # trace, independently rooted by the backend service, holding
                # the real downstream span tree. The stub's start time is
                # mechanically closer to our own measured step.start (it truly
                # is the first thing that happens), so picking "closest start"
                # always grabbed the empty stub. Duration reliably tells them
                # apart -- the stub reports near-zero, the real trace reports
                # actual processing time -- so prefer it, falling back to
                # closest-start only to disambiguate otherwise-equal candidates
                # (e.g. unrelated background traffic to the same endpoint).
                best = min(
                    candidates,
                    key=lambda t: (-int(t.get("duration", 0)), abs(int(t["start"]) - (step.start * 1000))),
                )
                trace_id = best["traceIds"][0]
                spans = self.sw_client.query_trace(trace_id)
                best_result = CorrelationResult("matched", trace_id, spans)
                # The richer trace can take longer than the gateway stub to be
                # ingested and indexed by OAP -- querying right after the step
                # completes sometimes only finds the stub, not because it's
                # the real answer, but because the real one hasn't landed yet.
                # A lone span whose serviceCode is the gateway is that stub's
                # specific tell (not "any single-span trace" -- a step whose
                # real trace is legitimately single-hop must still return
                # immediately), so keep polling only in that exact case.
                is_lone_gateway_stub = (
                    len(spans) == 1 and spans[0].get("serviceCode") == "ts-gateway-service"
                )
                if not is_lone_gateway_stub:
                    return best_result
            if self.now_fn() >= deadline:
                return best_result if best_result is not None else CorrelationResult("failed", None, [])
            self.sleep_fn(self.retry_interval_seconds)

    def _find_candidates(self, step, start_ms: int, end_ms: int) -> list[dict]:
        # OAP's queryDuration strings carry no offset, so they are interpreted in
        # the OAP server's own timezone -- which is UTC unless TZ is set on the
        # container. Build them explicitly in UTC rather than in the harness
        # host's local timezone, so the window does not silently shift with
        # wherever the harness happens to run. (Symptom of a mismatch: every
        # step records correlation_status="failed" with an otherwise healthy
        # cluster -- check the OAP container's TZ.) Note the final filter below
        # is on absolute epoch ms and is unaffected either way.
        window_start = _utc_minute(start_ms)
        window_end = _utc_minute(end_ms + 60_000)
        traces = self.sw_client.query_basic_traces(start=window_start, end=window_end,
                                                   page_size=self.page_size)
        return [
            t for t in traces
            if any(step.endpoint in name for name in t.get("endpointNames", []))
            and start_ms <= int(t["start"]) <= end_ms
        ]
