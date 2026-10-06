"""Antipattern rule registry. Each rule: (traces, params) -> list[Finding]."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from tt_harness.findings import Finding
from tt_harness.rules.base import Trace, build_traces
from tt_harness.rules.broken_propagation import broken_propagation
from tt_harness.rules.cyclic_calls import cyclic_calls
from tt_harness.rules.deep_chain import deep_chain
from tt_harness.rules.hotspot_span import hotspot_span
from tt_harness.rules.n_plus_one_remote import n_plus_one_remote
from tt_harness.rules.n_plus_one_sql import n_plus_one_sql
from tt_harness.rules.sequential_fanout import sequential_fanout

RuleFn = Callable[[list[Trace], dict[str, Any]], list[Finding]]

RULES: dict[str, RuleFn] = {
    "n_plus_one_remote": n_plus_one_remote,
    "cyclic_calls": cyclic_calls,
    "broken_propagation": broken_propagation,
    "hotspot_span": hotspot_span,
    "n_plus_one_sql": n_plus_one_sql,
    "sequential_fanout": sequential_fanout,
    "deep_chain": deep_chain,
}


@dataclass
class RuleRun:
    traces: list[Trace]
    findings: list[Finding]
    errors: dict[str, str]
    skipped_spans: int


def run_rules(raw_traces: list[dict], rule_params: dict[str, dict] | None = None) -> RuleRun:
    params = rule_params or {}
    unknown = sorted(set(params) - set(RULES))
    if unknown:
        raise ValueError(f"unknown rules: {', '.join(unknown)}")
    traces, skipped = build_traces(raw_traces)
    findings: list[Finding] = []
    errors: dict[str, str] = {}
    for name, rule in RULES.items():
        config = params.get(name) or {}
        if not config.get("enabled", True):
            continue
        try:
            findings.extend(rule(traces, config))
        except Exception as exc:  # one broken rule must not hide the others
            errors[name] = f"{type(exc).__name__}: {exc}"
    findings.sort(key=lambda f: (f.share_median is None, -(f.share_median or 0.0), -f.prevalence, f.id))
    return RuleRun(traces=traces, findings=findings, errors=errors, skipped_spans=skipped)
