from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class StepResult:
    step_name: str
    endpoint: str
    start: float
    end: float
    success: bool
    error: str | None
    outputs: dict[str, Any]
    correlate: bool
    correlation_status: str = "not_applicable"  # "matched" | "failed" | "not_applicable"
    trace_id: str | None = None
    spans: list = field(default_factory=list)


@dataclass
class WorkflowStep:
    name: str
    endpoint: str
    run: Callable[[dict], dict]
    correlate: bool = True


@dataclass
class WorkflowDefinition:
    name: str
    randomize_inputs: Callable[[Any], dict]
    build_steps: Callable[[Any, dict], list[WorkflowStep]]


@dataclass
class RunResult:
    run_id: str
    workflow: str
    inputs: dict
    success: bool
    steps: list[StepResult]
    failure_reason: str | None
