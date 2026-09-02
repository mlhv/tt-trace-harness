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
