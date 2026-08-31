"""T0b.2b1：现有连续安全搜索的请求比例快速路径与权威等价回归。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from benchmarks.t0.contracts import load_benchmark_plan
from benchmarks.t0.diagnostics import (
    load_authority_step_golden,
    load_hot_path_diagnostic_plan,
    profile_hot_path_case,
    verify_authority_step_golden,
)
from benchmarks.t0.metadata import file_sha256
import 高天荒野舰艇战术机动求解器 as solver


ROOT = Path(__file__).resolve().parent
PLAN_PATH = ROOT / "contracts" / "web_bridge" / "t0-benchmark-plan.v1.json"
DIAGNOSTIC_PLAN_PATH = ROOT / "contracts" / "web_bridge" / "t0-hot-path-diagnostic.v1.json"
GOLDEN_PATH = ROOT / "contracts" / "web_bridge" / "t0-authority-step-golden.v1.json"
REPORT_PATH = ROOT / "舰艇数据" / "报告" / "阶段T0b2b1安全搜索快速路径接口.v1.json"
PRIOR_SHORT_REPORT_PATH = ROOT / "舰艇数据" / "报告" / "阶段T0b1f短程诊断决策门接口.v1.json"


def exercise_search(
    allowed_at,
    violation_score=lambda scale: scale,
) -> tuple[float, float, tuple[float, ...]]:
    calls: list[float] = []
    original_load_metrics = solver._load_metrics
    original_command_allowed = solver._command_allowed
    original_violation_score = solver._violation_score

    def fake_load_metrics(_model, _state, _actuation, _drag, scale, _dt):
        calls.append(scale)
        return scale

    solver._load_metrics = fake_load_metrics
    solver._command_allowed = lambda _model, scale, _controls: allowed_at(scale)
    solver._violation_score = (
        lambda _model, scale, _controls: violation_score(scale)
    )
    try:
        scale, metrics = solver._choose_command_scale(
            object(),
            object(),
            object(),
            object(),
            solver.Vec2(),
            1.0 / 60.0,
        )
    finally:
        solver._load_metrics = original_load_metrics
        solver._command_allowed = original_command_allowed
        solver._violation_score = original_violation_score
    return scale, metrics, tuple(calls)


def test_fast_path_and_fallback() -> dict[str, int]:
    safe_scale, safe_metrics, safe_calls = exercise_search(lambda _scale: True)
    assert safe_scale == safe_metrics == 1.0
    assert safe_calls == (1.0,)

    limited_scale, limited_metrics, limited_calls = exercise_search(
        lambda scale: scale <= 0.5
    )
    assert limited_scale == limited_metrics == 0.5
    assert len(limited_calls) == 115
    assert limited_calls.count(1.0) == 1
    assert limited_calls[1:65] == tuple(index / 64.0 for index in range(64))

    fallback_scale, fallback_metrics, fallback_calls = exercise_search(
        lambda _scale: False,
        lambda scale: abs(scale - 0.25),
    )
    assert fallback_scale == fallback_metrics == 0.25
    assert len(fallback_calls) == 65
    assert fallback_calls.count(1.0) == 1

    return {
        "all_safe": len(safe_calls),
        "no_safe_sample": len(fallback_calls),
        "partially_safe": len(limited_calls),
    }


def test_existing_overg_and_crew_lock_semantics() -> None:
    metrics = solver.LoadMetrics(2.0, 13.0, solver.Vec2(), 0.0)
    overg = solver.TacticalControlInput(overg=True)
    unmanned_model = SimpleNamespace(
        runtime=SimpleNamespace(crew_safety_lock_enabled=False)
    )
    crewed_model = SimpleNamespace(
        runtime=SimpleNamespace(crew_safety_lock_enabled=True)
    )
    assert solver._command_allowed(unmanned_model, metrics, overg)
    assert not solver._command_allowed(crewed_model, metrics, overg)


def main() -> None:
    unit_call_counts = test_fast_path_and_fallback()
    test_existing_overg_and_crew_lock_semantics()

    plan = load_benchmark_plan(PLAN_PATH)
    expected_golden = load_authority_step_golden(GOLDEN_PATH)
    assert verify_authority_step_golden(ROOT, plan, GOLDEN_PATH) == expected_golden

    diagnostic_plan = load_hot_path_diagnostic_plan(DIAGNOSTIC_PLAN_PATH)
    target_motion = next(
        item for item in diagnostic_plan.cases if item.id == "target_20.motion_only"
    )
    observed = profile_hot_path_case(ROOT, plan, diagnostic_plan, target_motion)
    assert observed["authority_equivalent"] is True
    assert observed["unprofiled"]["authority_state_sha256"] == (
        expected_golden["cases"][target_motion.id]["authority_state_sha256"]
    )
    assert observed["unprofiled"]["authority_event_sha256"] == (
        expected_golden["cases"][target_motion.id]["authority_event_sha256"]
    )
    load_metric_calls = observed["profiled"]["function_probes"]["load_metrics"][
        "total_calls"
    ]
    assert load_metric_calls <= 20

    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    assert report["interface"] == "gaotian.stage-t0b2b1-safety-search-fast-path/v1"
    assert report["status"] == "PASS"
    assert report["official_performance_runs_executed"] == 0
    assert report["t0_performance_measured"] is False
    assert report["load_metrics_calls_per_profiled_step"][target_motion.id] == 20
    prior_short_report = json.loads(
        PRIOR_SHORT_REPORT_PATH.read_text(encoding="utf-8")
    )
    assert report["short_authority_hashes"] == prior_short_report[
        "authority_hashes"
    ]
    assert report["implementation_hashes"][
        "高天荒野舰艇战术机动求解器.py"
    ] == file_sha256(ROOT / "高天荒野舰艇战术机动求解器.py")
    assert report["implementation_hashes"][
        "benchmarks/t0/diagnostics.py"
    ] == file_sha256(ROOT / "benchmarks" / "t0" / "diagnostics.py")
    assert report["implementation_hashes"][
        "contracts/web_bridge/t0-authority-step-golden.v1.json"
    ] == file_sha256(GOLDEN_PATH)

    print(
        json.dumps(
            {
                "authority_golden": "12_of_12_PASS",
                "interface": "gaotian.stage-t0b2b1-safety-search-fast-path-test/v1",
                "status": "PASS",
                "target_20_motion_load_metrics_per_step": load_metric_calls,
                "unit_call_counts": unit_call_counts,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
