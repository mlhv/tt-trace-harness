from dataclasses import dataclass, field


@dataclass
class EdgeStats:
    source: str
    target: str
    call_count: int = 0
    durations_ms: list = field(default_factory=list)
    error_count: int = 0

    @property
    def avg_ms(self) -> float:
        return sum(self.durations_ms) / len(self.durations_ms) if self.durations_ms else 0.0

    @property
    def p50_ms(self) -> float:
        return self._percentile(0.50)

    @property
    def p95_ms(self) -> float:
        return self._percentile(0.95)

    @property
    def max_ms(self) -> float:
        return max(self.durations_ms) if self.durations_ms else 0.0

    def _percentile(self, p: float) -> float:
        if not self.durations_ms:
            return 0.0
        ordered = sorted(self.durations_ms)
        idx = min(int(len(ordered) * p), len(ordered) - 1)
        return ordered[idx]


class DependencyAnalyzer:
    def analyze(self, runs: list) -> tuple[dict, dict]:
        service_edges: dict[tuple[str, str], EdgeStats] = {}
        endpoint_edges: dict[tuple[str, str], EdgeStats] = {}

        for run in runs:
            for step in run.steps:
                if not step.correlate or step.correlation_status != "matched":
                    continue
                self._process_span_tree(step.spans, service_edges, endpoint_edges)

        return service_edges, endpoint_edges

    def _process_span_tree(self, spans: list[dict], service_edges: dict, endpoint_edges: dict) -> None:
        by_id = {s["spanId"]: s for s in spans}
        children_by_parent: dict[int, list[dict]] = {}
        for s in spans:
            children_by_parent.setdefault(s["parentSpanId"], []).append(s)

        for span in spans:
            parent = by_id.get(span["parentSpanId"])
            if parent is None:
                continue  # root span, no incoming edge to record
            self_time = self._self_time_ms(span, children_by_parent.get(span["spanId"], []))
            is_error = bool(span.get("isError"))

            if parent["serviceCode"] != span["serviceCode"]:
                self._record(service_edges, parent["serviceCode"], span["serviceCode"], self_time, is_error)
            self._record(endpoint_edges, parent["endpointName"], span["endpointName"], self_time, is_error)

    def _self_time_ms(self, span: dict, direct_children: list[dict]) -> float:
        own = span["endTime"] - span["startTime"]
        children_total = sum(c["endTime"] - c["startTime"] for c in direct_children)
        return float(max(own - children_total, 0))

    def _record(self, edges: dict, source: str, target: str, duration_ms: float, is_error: bool) -> None:
        key = (source, target)
        if key not in edges:
            edges[key] = EdgeStats(source=source, target=target)
        edge = edges[key]
        edge.call_count += 1
        edge.durations_ms.append(duration_ms)
        if is_error:
            edge.error_count += 1
