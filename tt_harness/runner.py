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
