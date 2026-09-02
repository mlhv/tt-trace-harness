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
