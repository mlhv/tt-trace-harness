import sys
import time

from tt_harness.workflows.base import RunResult, StepResult, WorkflowDefinition


class WorkflowRunner:
    def __init__(self, gateway, correlator=None, now_fn=time.time):
        self.gateway = gateway
        self.correlator = correlator
        self.now_fn = now_fn

    def run_once(self, definition: WorkflowDefinition, run_id: str) -> RunResult:
        # randomize_inputs makes real HTTP calls (e.g. list_routes), so a
        # transient network failure here must fail only this run, not the batch.
        try:
            inputs = definition.randomize_inputs(self.gateway)
            steps = definition.build_steps(self.gateway, inputs)
        except Exception as e:
            reason = f"setup failed: {e}"
            print(f"run {run_id}: {reason}", file=sys.stderr)
            return RunResult(run_id, definition.name, {}, False, [], reason)

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
            # A step whose actual HTTP call varies at runtime (e.g. preserve
            # vs. preserveOther, chosen by which trip got randomly picked)
            # can report the endpoint it really hit via this reserved output
            # key, so correlation matches against the real trace instead of
            # the step's nominal endpoint.
            endpoint = outputs.pop("_endpoint", step.endpoint)
            context.update(outputs)
            result = StepResult(step.name, endpoint, start, end, True, None, outputs, step.correlate)
            if self.correlator is not None and result.correlate:
                self._correlate(result)
            step_results.append(result)

        return RunResult(run_id, definition.name, inputs, True, step_results, None)

    def _correlate(self, result: StepResult) -> None:
        # A correlation failure -- including any exception from the SkyWalking
        # client (HTTPError/URLError from a dropped port-forward, RuntimeError
        # from a GraphQL error, JSONDecodeError, ...) -- degrades this step to
        # correlation_status="failed". It must never abort the run or the batch:
        # the step itself already succeeded, and the remaining runs' data would
        # otherwise be lost.
        try:
            cr = self.correlator.correlate(result)
        except Exception as e:
            result.correlation_status = "failed"
            result.correlation_error = f"{type(e).__name__}: {e}"
            print(f"correlation failed for step {result.step_name}: "
                  f"{result.correlation_error}", file=sys.stderr)
            return
        result.correlation_status = cr.status
        result.trace_id = cr.trace_id
        result.spans = cr.spans

    def run_many(self, definition: WorkflowDefinition, count: int, run_id_fn=None,
                 on_result=None) -> list[RunResult]:
        """Run the workflow `count` times.

        `on_result(index, result)` is invoked as each run completes, so a caller
        can persist partial results incrementally: a hard crash or a Ctrl-C
        part-way through a long batch then still leaves the finished runs on
        disk. run_once never raises, so the loop itself always completes.
        """
        if run_id_fn is None:
            import uuid
            run_id_fn = lambda i: str(uuid.uuid4())
        results: list[RunResult] = []
        for i in range(count):
            result = self.run_once(definition, run_id_fn(i))
            results.append(result)
            if on_result is not None:
                on_result(i, result)
        return results
