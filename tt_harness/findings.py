"""Finding model and helpers shared by every antipattern rule."""

from __future__ import annotations

import hashlib
import re
import statistics
from dataclasses import asdict, dataclass, field
from typing import Any

_BRACE_METHOD = re.compile(r"^\{([A-Za-z]+)\}(.*)$")
_COLON_METHOD = re.compile(r"^([A-Z]+):(/.*)$")
_ID_SEGMENT = re.compile(
    r"^(?:\d+"
    r"|(?=[0-9a-fA-F]*\d)[0-9a-fA-F]{8,}"
    r"|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    r"|\d{4}-\d{2}-\d{2})$"
)


def split_endpoint(name: str | None) -> tuple[str | None, str]:
    """Split SkyWalking endpoint names: '{POST}/p', 'POST:/p' or a bare '/p'."""
    text = (name or "").strip()
    for pattern in (_BRACE_METHOD, _COLON_METHOD):
        match = pattern.match(text)
        if match:
            return match.group(1).upper(), match.group(2)
    return None, text


def normalize_path(path: str) -> str:
    text = path.split("?", 1)[0]
    if "://" in text:
        rest = text.split("://", 1)[1]
        text = "/" + rest.split("/", 1)[1] if "/" in rest else "/"
    return "/".join("{id}" if _ID_SEGMENT.match(segment) else segment for segment in text.split("/"))


def normalize_endpoint(name: str | None) -> str:
    method, path = split_endpoint(name)
    normalized = normalize_path(path)
    return f"{method}:{normalized}" if method else normalized


def endpoint_path(name: str | None) -> str:
    return normalize_path(split_endpoint(name)[1])


def finding_id(*parts: str | None) -> str:
    return hashlib.sha1("|".join(part or "" for part in parts).encode("utf-8")).hexdigest()[:12]


def interval_union_ms(intervals: list[tuple[float, float]]) -> float:
    total = 0.0
    current: tuple[float, float] | None = None
    for start, end in sorted(intervals):
        if current is None or start > current[1]:
            if current is not None:
                total += current[1] - current[0]
            current = (start, end)
        else:
            current = (current[0], max(current[1], end))
    if current is not None:
        total += current[1] - current[0]
    return float(total)


def pick_evidence(shares: list[tuple[str, float]]) -> list[str]:
    ordered = sorted(shares, key=lambda item: (item[1], item[0]))
    picks = [ordered[(len(ordered) - 1) // 2][0], ordered[-1][0], ordered[0][0]]
    return list(dict.fromkeys(picks))


def load_slope(points: list[tuple[float | None, float | None]]) -> float | None:
    usable = [(float(x), float(y)) for x, y in points if x is not None and y is not None]
    if len({x for x, _ in usable}) < 2:
        return None
    xs, ys = zip(*usable)
    return statistics.linear_regression(xs, ys).slope


@dataclass
class Finding:
    rule: str
    root: str
    caller_service: str
    caller_segment_entry: str
    callee_service: str | None
    callee_endpoint: str | None
    detail: str
    k_median: float | None
    share_median: float | None
    prevalence: float
    trace_count: int
    evidence: list[str]
    highlights: dict[str, list[str]] = field(default_factory=dict)
    id: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = finding_id(
                self.rule,
                self.root,
                self.caller_service,
                self.caller_segment_entry,
                self.callee_service,
                self.callee_endpoint,
                self.detail,
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Finding:
        return cls(**data)
