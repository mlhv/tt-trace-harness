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
