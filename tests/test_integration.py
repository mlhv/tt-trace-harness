"""Seam tests: a REAL WorkflowRunner driving a REAL TraceCorrelator.

Every other suite fakes one side of this boundary -- test_runner.py uses a
FakeCorrelator and test_correlator.py uses a FakeStep -- so nothing checked
that a real StepResult actually satisfies what TraceCorrelator.correlate()
reads off it (.start, .end, .endpoint), nor that a correlation outcome lands
back on the real StepResult. Only the SkyWalkingClient is faked here.
"""
from tt_harness.analyzer import DependencyAnalyzer
from tt_harness.correlator import TraceCorrelator
from tt_harness.runner import WorkflowRunner
from tt_harness.workflows.base import WorkflowDefinition, WorkflowStep

# Real span shapes, trimmed from ~/skywalking-exports/traces_2026-08-17.jsonl.
SEG_PRESERVE = "0a14a37113834ed397506608f5dc3987.524.17869773164440004"
SEG_SECURITY = "352dab19b3a64925827a7d1a9ebeb659.462.17869773165050010"
TRACE_ID = "0a14a37113834ed397506608f5dc3987.524.17869773164440005"

TRACE_SPANS = [
    {"traceId": TRACE_ID, "segmentId": SEG_PRESERVE, "spanId": 0, "parentSpanId": -1,
     "serviceCode": "ts-preserve-service", "endpointName": "POST:/api/v1/preserveservice/preserve",
     "startTime": 1786977316444, "endTime": 1786977318061, "isError": False, "refs": []},
    {"traceId": TRACE_ID, "segmentId": SEG_PRESERVE, "spanId": 1, "parentSpanId": 0,
     "serviceCode": "ts-preserve-service", "endpointName": "/api/v1/securityservice/securityConfigs/4d2a46c7",
     "startTime": 1786977316498, "endTime": 1786977316580, "isError": False, "refs": []},
    # Segment root of a DIFFERENT service, linked only through refs.
    {"traceId": TRACE_ID, "segmentId": SEG_SECURITY, "spanId": 0, "parentSpanId": -1,
     "serviceCode": "ts-security-service",
     "endpointName": "GET:/api/v1/securityservice/securityConfigs/{accountId}",
     "startTime": 1786977316505, "endTime": 1786977316580, "isError": False,
     "refs": [{"traceId": TRACE_ID, "parentSegmentId": SEG_PRESERVE,
               "parentSpanId": 1, "type": "CROSS_PROCESS"}]},
]


class FakeSkyWalkingClient:
    def __init__(self, traces, trace_by_id=None, raise_on_basic=None):
        self.traces = traces
        self.trace_by_id = trace_by_id or {}
        self.raise_on_basic = raise_on_basic
        self.basic_calls = 0

    def query_basic_traces(self, start, end, trace_state="ALL", page_num=1, page_size=20):
        self.basic_calls += 1
        if self.raise_on_basic is not None:
            raise self.raise_on_basic
        return self.traces

    def query_trace(self, trace_id):
        return self.trace_by_id.get(trace_id, [])


# The runner stamps step start/end from now_fn, and the correlator filters
# candidate traces on those same values, so the fake clock and the fake trace's
# `start` (epoch ms) have to agree. 1786977316.5s == 1786977316500ms.
STEP_START = 1786977316.5


def _clock():
    """First call (step start) then second (step end), one second apart."""
    t = [STEP_START - 1.0]

    def now():
        t[0] += 1.0
        return t[0]
    return now


def _correlator_clock():
    """Fake clock for the correlator's retry deadline; sleep advances it."""
    t = [0.0]
    return (lambda: t[0]), (lambda seconds: t.__setitem__(0, t[0] + seconds))


def _definition():
    def build_steps(gateway, inputs):
        return [WorkflowStep("preserve", "/api/v1/preserveservice/preserve",
                             lambda ctx: {"order_id": "o1"}, correlate=True)]
    return WorkflowDefinition(name="preserve-ish",
                              randomize_inputs=lambda gw: {"from": "nanjing"},
                              build_steps=build_steps)


def test_real_runner_and_correlator_produce_a_matched_step_result():
    basic = [{"endpointNames": ["POST:/api/v1/preserveservice/preserve"],
              "start": int(STEP_START * 1000), "traceIds": [TRACE_ID]}]
    sw = FakeSkyWalkingClient(basic, trace_by_id={TRACE_ID: TRACE_SPANS})
    sw_now, sw_sleep = _correlator_clock()
    correlator = TraceCorrelator(sw, now_fn=sw_now, sleep_fn=sw_sleep)
    runner = WorkflowRunner(gateway=None, correlator=correlator, now_fn=_clock())

    result = runner.run_once(_definition(), run_id="run-1")

    assert result.success is True
    step = result.steps[0]
    # A real StepResult satisfied everything the real correlator reads off it.
    assert step.correlation_status == "matched"
    assert step.trace_id == TRACE_ID
    assert step.spans == TRACE_SPANS
    assert step.correlation_error is None

    # ...and the real spans flow on into the real analyzer.
    service_edges, _ = DependencyAnalyzer().analyze([result])
    assert set(service_edges) == {("ts-preserve-service", "ts-security-service")}


def test_real_correlator_no_candidates_records_failed_on_the_real_step_result():
    sw = FakeSkyWalkingClient([])  # nothing ever matches
    sw_now, sw_sleep = _correlator_clock()
    correlator = TraceCorrelator(sw, max_wait_seconds=4.0, retry_interval_seconds=2.0,
                                 now_fn=sw_now, sleep_fn=sw_sleep)
    runner = WorkflowRunner(gateway=None, correlator=correlator, now_fn=_clock())

    result = runner.run_once(_definition(), run_id="run-2")

    # The step itself succeeded; only correlation failed.
    assert result.success is True
    assert result.steps[0].success is True
    assert result.steps[0].correlation_status == "failed"
    assert result.steps[0].trace_id is None
    assert result.steps[0].spans == []

    service_edges, endpoint_edges = DependencyAnalyzer().analyze([result])
    assert service_edges == {} and endpoint_edges == {}


def test_skywalking_client_exception_degrades_to_failed_instead_of_killing_the_batch():
    """C2: an HTTPError/URLError/RuntimeError out of the client must not escape."""
    sw = FakeSkyWalkingClient([], raise_on_basic=RuntimeError("port-forward dropped"))
    sw_now, sw_sleep = _correlator_clock()
    correlator = TraceCorrelator(sw, now_fn=sw_now, sleep_fn=sw_sleep)
    runner = WorkflowRunner(gateway=None, correlator=correlator, now_fn=_clock())

    results = runner.run_many(_definition(), count=3, run_id_fn=lambda i: f"run-{i}")

    assert len(results) == 3  # the whole batch survives
    for r in results:
        assert r.success is True
        assert r.steps[0].correlation_status == "failed"
        assert "RuntimeError" in r.steps[0].correlation_error
        assert "port-forward dropped" in r.steps[0].correlation_error
