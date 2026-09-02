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


def test_step_can_override_its_correlation_endpoint_via_reserved_output_key():
    """A step whose real HTTP call is only known at run time (e.g. preserve
    vs. preserveOther) reports it via `_endpoint`, and that must be used for
    correlation instead of the step's nominal endpoint -- and must not leak
    into the recorded outputs or the shared context."""
    def build_steps(gateway, inputs):
        return [WorkflowStep("preserve", "/api/v1/preserveservice/preserve",
                              lambda ctx: {"trip_id": "Z1", "_endpoint": "/api/v1/preserveotherservice/preserveOther"},
                              correlate=True)]

    definition = WorkflowDefinition(name="wf", randomize_inputs=lambda gw: {}, build_steps=build_steps)
    correlator = FakeCorrelator()
    runner = WorkflowRunner(gateway=None, correlator=correlator, now_fn=_clock())

    result = runner.run_once(definition, run_id="run1")

    assert result.steps[0].endpoint == "/api/v1/preserveotherservice/preserveOther"
    assert result.steps[0].outputs == {"trip_id": "Z1"}


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


class ExplodingCorrelator:
    """Stands in for a dropped port-forward / GraphQL error / malformed body."""
    def __init__(self, exc):
        self.exc = exc
        self.calls = 0

    def correlate(self, step_result):
        self.calls += 1
        raise self.exc


def test_correlator_exception_marks_step_failed_without_aborting_the_run(capsys):
    def build_steps(gateway, inputs):
        return [
            WorkflowStep("a", "/a", lambda ctx: {"x": 1}, correlate=True),
            WorkflowStep("b", "/b", lambda ctx: {"y": 2}, correlate=True),
        ]

    definition = WorkflowDefinition(name="wf", randomize_inputs=lambda gw: {}, build_steps=build_steps)
    correlator = ExplodingCorrelator(RuntimeError("[{'message': 'oap exploded'}]"))
    runner = WorkflowRunner(gateway=None, correlator=correlator, now_fn=_clock())

    result = runner.run_once(definition, run_id="run-x")

    # The steps themselves succeeded; only correlation degraded.
    assert result.success is True
    assert result.failure_reason is None
    assert [s.step_name for s in result.steps] == ["a", "b"]
    assert all(s.success for s in result.steps)
    assert [s.correlation_status for s in result.steps] == ["failed", "failed"]
    # The reason is recorded, not silently dropped.
    assert "RuntimeError" in result.steps[0].correlation_error
    assert "oap exploded" in result.steps[0].correlation_error
    # A failure on step "a" must not stop us correlating step "b".
    assert correlator.calls == 2
    assert "correlation failed for step a" in capsys.readouterr().err


def test_correlator_exception_does_not_lose_the_rest_of_the_batch():
    def build_steps(gateway, inputs):
        return [WorkflowStep("a", "/a", lambda ctx: {}, correlate=True)]

    definition = WorkflowDefinition(name="wf", randomize_inputs=lambda gw: {}, build_steps=build_steps)
    runner = WorkflowRunner(gateway=None, correlator=ExplodingCorrelator(OSError("port-forward died")),
                            now_fn=_clock())

    results = runner.run_many(definition, count=5, run_id_fn=lambda i: f"run-{i}")

    assert len(results) == 5  # nothing dropped
    assert all(r.success for r in results)
    assert all(r.steps[0].correlation_status == "failed" for r in results)


def test_setup_failure_fails_only_that_run(capsys):
    """randomize_inputs makes real HTTP calls, so it must not escape run_once."""
    attempts = []

    def randomize_inputs(gateway):
        attempts.append(1)
        if len(attempts) == 2:
            raise ConnectionResetError("list_routes: connection reset")
        return {"route": "nanjing->shanghai"}

    def build_steps(gateway, inputs):
        return [WorkflowStep("a", "/a", lambda ctx: {}, correlate=False)]

    definition = WorkflowDefinition(name="wf", randomize_inputs=randomize_inputs, build_steps=build_steps)
    runner = WorkflowRunner(gateway=None, correlator=None, now_fn=_clock())

    results = runner.run_many(definition, count=3, run_id_fn=lambda i: f"run-{i}")

    assert [r.run_id for r in results] == ["run-0", "run-1", "run-2"]
    assert [r.success for r in results] == [True, False, True]
    assert "connection reset" in results[1].failure_reason
    assert results[1].steps == []
    assert "run run-1: setup failed" in capsys.readouterr().err


def test_build_steps_failure_fails_only_that_run():
    def build_steps(gateway, inputs):
        raise ValueError("no trips found")

    definition = WorkflowDefinition(name="wf", randomize_inputs=lambda gw: {}, build_steps=build_steps)
    runner = WorkflowRunner(gateway=None, correlator=None, now_fn=_clock())

    results = runner.run_many(definition, count=2, run_id_fn=lambda i: f"run-{i}")

    assert len(results) == 2
    assert all(not r.success for r in results)
    assert "no trips found" in results[0].failure_reason


def test_run_many_reports_each_result_as_it_completes():
    """The CLI relies on this to persist runs.jsonl incrementally."""
    def build_steps(gateway, inputs):
        return [WorkflowStep("a", "/a", lambda ctx: {}, correlate=False)]

    definition = WorkflowDefinition(name="wf", randomize_inputs=lambda gw: {}, build_steps=build_steps)
    runner = WorkflowRunner(gateway=None, correlator=None, now_fn=_clock())

    seen = []
    results = runner.run_many(definition, count=3, run_id_fn=lambda i: f"run-{i}",
                              on_result=lambda i, r: seen.append((i, r.run_id)))

    assert seen == [(0, "run-0"), (1, "run-1"), (2, "run-2")]
    assert [r.run_id for r in results] == ["run-0", "run-1", "run-2"]
