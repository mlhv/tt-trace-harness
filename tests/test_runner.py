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
