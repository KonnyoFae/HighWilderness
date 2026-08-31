"""T0 每次运行的无歧义统计口径。"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil, isfinite
from typing import Iterable

from .contracts import BenchmarkContractError


@dataclass(frozen=True)
class MetricSummary:
    sample_count: int
    mean: float | None
    p95_nearest_rank: float | None
    maximum: float | None

    def to_dict(self) -> dict[str, float | int | None]:
        return {
            "maximum": self.maximum,
            "mean": self.mean,
            "p95_nearest_rank": self.p95_nearest_rank,
            "sample_count": self.sample_count,
        }


def _samples(values: Iterable[float]) -> tuple[float, ...]:
    result: list[float] = []
    for index, value in enumerate(values):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise BenchmarkContractError("metric.sample_type", f"$.samples[{index}]", "必须是有限数")
        converted = float(value)
        if not isfinite(converted):
            raise BenchmarkContractError("metric.sample_finite", f"$.samples[{index}]", "必须是有限数")
        result.append(converted)
    return tuple(result)


def nearest_rank_percentile(values: Iterable[float], percentile: float) -> float | None:
    if isinstance(percentile, bool) or not isinstance(percentile, (int, float)):
        raise BenchmarkContractError("metric.percentile", "$.percentile", "必须位于 (0, 1]")
    percentile_value = float(percentile)
    if not isfinite(percentile_value) or not 0.0 < percentile_value <= 1.0:
        raise BenchmarkContractError("metric.percentile", "$.percentile", "必须位于 (0, 1]")
    samples = sorted(_samples(values))
    if not samples:
        return None
    return samples[ceil(percentile_value * len(samples)) - 1]


def summarize_samples(values: Iterable[float]) -> MetricSummary:
    samples = _samples(values)
    if not samples:
        return MetricSummary(0, None, None, None)
    return MetricSummary(
        len(samples),
        sum(samples) / len(samples),
        nearest_rank_percentile(samples, 0.95),
        max(samples),
    )

