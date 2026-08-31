"""T0 战术性能风险验证的契约与准备期工具。"""

from .contracts import BenchmarkContractError, BenchmarkPlan, load_benchmark_plan
from .diagnostics import (
    HotPathDiagnosticPlan,
    load_hot_path_diagnostic_plan,
    run_hot_path_diagnostic,
    verify_authority_step_golden,
)
from .fixture_audit import audit_fixture_capacity
from .headless import run_headless_baseline
from .matrix import BenchmarkRunSpec, expand_matrix
from .metrics import MetricSummary, summarize_samples
from .scenario import T0ScenarioBundle, build_scenario

__all__ = [
    "BenchmarkContractError",
    "BenchmarkPlan",
    "BenchmarkRunSpec",
    "HotPathDiagnosticPlan",
    "MetricSummary",
    "T0ScenarioBundle",
    "audit_fixture_capacity",
    "expand_matrix",
    "build_scenario",
    "load_benchmark_plan",
    "load_hot_path_diagnostic_plan",
    "run_headless_baseline",
    "run_hot_path_diagnostic",
    "summarize_samples",
    "verify_authority_step_golden",
]
