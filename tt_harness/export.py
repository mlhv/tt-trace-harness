import csv
import dataclasses
import json


def run_record(run) -> dict:
    """The runs.jsonl representation of a single run."""
    return {
        "run_id": run.run_id,
        "workflow": run.workflow,
        "inputs": run.inputs,
        "success": run.success,
        "failure_reason": run.failure_reason,
        "steps": [
            {
                "step_name": s.step_name,
                "endpoint": s.endpoint,
                "start": s.start,
                "end": s.end,
                "success": s.success,
                "error": s.error,
                "correlate": s.correlate,
                "correlation_status": s.correlation_status,
                "correlation_error": s.correlation_error,
                "trace_id": s.trace_id,
                "spans": s.spans,
            }
            for s in run.steps
        ],
    }


def write_runs_jsonl(runs: list, path: str) -> None:
    with open(path, "w") as f:
        for run in runs:
            f.write(json.dumps(run_record(run)) + "\n")


def write_runs_summary_csv(runs: list, path: str) -> None:
    fieldnames = ["run_id", "workflow", "total_duration_ms", "step_durations", "success", "failure_reason"]
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for run in runs:
            step_durations = {s.step_name: (s.end - s.start) * 1000 for s in run.steps}
            total_ms = sum(step_durations.values())
            writer.writerow({
                "run_id": run.run_id,
                "workflow": run.workflow,
                "total_duration_ms": total_ms,
                "step_durations": json.dumps(step_durations),
                "success": run.success,
                "failure_reason": run.failure_reason or "",
            })


def write_dependency_csv(edges: dict, path: str) -> None:
    fieldnames = ["source_service", "target_service", "call_count", "avg_ms", "p50_ms", "p95_ms", "max_ms", "error_count"]
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for edge in edges.values():
            writer.writerow({
                "source_service": edge.source,
                "target_service": edge.target,
                "call_count": edge.call_count,
                "avg_ms": round(edge.avg_ms, 2),
                "p50_ms": round(edge.p50_ms, 2),
                "p95_ms": round(edge.p95_ms, 2),
                "max_ms": round(edge.max_ms, 2),
                "error_count": edge.error_count,
            })
