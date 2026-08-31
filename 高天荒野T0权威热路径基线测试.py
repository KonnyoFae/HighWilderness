"""T0b.1a：短程诊断协议、十二场景权威黄金结果与观测器回归。"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Callable

from benchmarks.t0.contracts import BenchmarkContractError, load_benchmark_plan
from benchmarks.t0.diagnostics import (
    DIAGNOSTIC_PLAN_INTERFACE,
    GOLDEN_INTERFACE,
    load_authority_step_golden,
    load_hot_path_diagnostic_plan,
    profile_hot_path_case,
    verify_authority_step_golden,
)
from benchmarks.t0.metadata import file_sha256


ROOT = Path(__file__).resolve().parent
PLAN_PATH = ROOT / "contracts" / "web_bridge" / "t0-benchmark-plan.v1.json"
DIAGNOSTIC_PLAN_PATH = ROOT / "contracts" / "web_bridge" / "t0-hot-path-diagnostic.v1.json"
GOLDEN_PATH = ROOT / "contracts" / "web_bridge" / "t0-authority-step-golden.v1.json"
REPORT_PATH = ROOT / "舰艇数据" / "报告" / "阶段T0b1a权威热路径基线接口.v1.json"


def require_error(code: str, action: Callable[[], object]) -> None:
    try:
        action()
    except BenchmarkContractError as error:
        if error.code != code:
            raise AssertionError(f"预期 {code}，实际 {error.code}: {error}") from error
    else:
        raise AssertionError(f"预期抛出 {code}")


def test_negative_diagnostic_plans() -> None:
    raw = json.loads(DIAGNOSTIC_PLAN_PATH.read_text(encoding="utf-8"))
    with TemporaryDirectory() as directory:
        base = Path(directory)
        unknown = deepcopy(raw)
        unknown["unexpected"] = True
        unknown_path = base / "unknown.json"
        unknown_path.write_text(json.dumps(unknown), encoding="utf-8")
        require_error(
            "diagnostic.object_keys",
            lambda: load_hot_path_diagnostic_plan(unknown_path),
        )

        mismatched = deepcopy(raw)
        mismatched["profiled_steps"] = mismatched["unprofiled_steps"] + 1
        mismatched_path = base / "mismatched.json"
        mismatched_path.write_text(json.dumps(mismatched), encoding="utf-8")
        require_error(
            "diagnostic.step_count_mismatch",
            lambda: load_hot_path_diagnostic_plan(mismatched_path),
        )

        boolean = deepcopy(raw)
        boolean["unprofiled_steps"] = True
        boolean["profiled_steps"] = True
        boolean_path = base / "boolean.json"
        boolean_path.write_text(json.dumps(boolean), encoding="utf-8")
        require_error(
            "diagnostic.positive_integer",
            lambda: load_hot_path_diagnostic_plan(boolean_path),
        )


def main() -> None:
    benchmark_plan = load_benchmark_plan(PLAN_PATH)
    diagnostic_plan = load_hot_path_diagnostic_plan(DIAGNOSTIC_PLAN_PATH)
    assert len(diagnostic_plan.cases) == 6
    assert diagnostic_plan.fixed_step_budget_ms == 1000.0 / 60.0
    assert diagnostic_plan.profiled_steps == diagnostic_plan.unprofiled_steps == 1
    assert diagnostic_plan.repetition == 1
    assert diagnostic_plan.top_hotspot_count == 25
    assert diagnostic_plan.source_sha256 == file_sha256(DIAGNOSTIC_PLAN_PATH)
    test_negative_diagnostic_plans()

    expected_golden = load_authority_step_golden(GOLDEN_PATH)
    assert expected_golden["interface"] == GOLDEN_INTERFACE
    assert expected_golden["plan_sha256"] == benchmark_plan.source_sha256
    assert len(expected_golden["cases"]) == 12
    assert set(expected_golden["cases"]) == {
        f"{profile.id}.{stage}"
        for profile in benchmark_plan.profiles
        for stage in benchmark_plan.load_stages
    }
    verified_golden = verify_authority_step_golden(
        ROOT, benchmark_plan, GOLDEN_PATH
    )
    assert verified_golden == expected_golden
    assert all(
        item["resource_hash_count"] == 24
        for item in verified_golden["cases"].values()
    )

    functional_motion = next(
        item
        for item in diagnostic_plan.cases
        if item.id == "functional_6.motion_only"
    )
    observed = profile_hot_path_case(
        ROOT,
        benchmark_plan,
        diagnostic_plan,
        functional_motion,
    )
    assert observed["authority_equivalent"] is True
    assert observed["unprofiled"]["authority_state_sha256"] == (
        expected_golden["cases"][functional_motion.id]["authority_state_sha256"]
    )
    assert observed["unprofiled"]["authority_event_sha256"] == (
        expected_golden["cases"][functional_motion.id]["authority_event_sha256"]
    )
    assert observed["profiled"]["authority_state_sha256"] == (
        observed["unprofiled"]["authority_state_sha256"]
    )
    assert observed["profiled"]["authority_event_sha256"] == (
        observed["unprofiled"]["authority_event_sha256"]
    )
    assert observed["unprofiled"]["fixed_step_ms"]["sample_count"] == 1
    assert observed["result_materialization_ms"] >= 0.0
    assert observed["event_digest_ms"] >= 0.0
    assert observed["estimated_observer_overhead_ms"] >= 0.0
    assert len(observed["profiled"]["top_hotspots"]) == 25

    probes = observed["profiled"]["function_probes"]
    expected_probe_calls = {
        "advance_projectile_world": 1,
        "advance_weapon_timeline": 12,
        "bind_tactical_ship_model": 6,
        "build_tactical_ship_model": 0,
        "build_tactical_ship_static_model": 0,
        "compile_projectile_target_geometry": 0,
        "compile_runtime_ship_parameters": 0,
        "derive_tactical_ship_lifecycle": 12,
        "design_snapshot_source_sha256": 60,
        "integrate_tactical_step": 6,
        "refresh_lifecycle_boundary": 2,
        "runtime_cache_resolve": 18,
        "validate_bindings": 1,
        "validate_instance_current_design": 12,
        "validate_internal_state": 2,
    }
    assert {
        key: probes[key]["total_calls"] for key in expected_probe_calls
    } == expected_probe_calls
    assert {item["phase"] for item in probes.values()} == {
        "damage",
        "integrity_validation",
        "lifecycle",
        "motion",
        "projectile",
        "runtime_compile",
        "weapon_timeline",
    }

    implementation_paths = (
        ROOT / "benchmarks" / "t0" / "diagnostics.py",
        ROOT / "benchmarks" / "t0" / "headless.py",
        ROOT / "benchmarks" / "t0" / "__main__.py",
        ROOT / "tools" / "Invoke-HighWildernessT0.ps1",
        DIAGNOSTIC_PLAN_PATH,
        GOLDEN_PATH,
    )
    report = {
        "acceptance": {
            "authority_golden_verification": "12_of_12_PASS",
            "diagnostic_plan_negative_inputs": "3_of_3_PASS",
            "observer_authority_equivalence": "PASS",
            "observer_default_disabled": "PASS",
            "phase_probe_coverage": "7_of_7_PASS",
        },
        "current_probe_total_calls": {
            key: probes[key]["total_calls"] for key in sorted(expected_probe_calls)
        },
        "diagnostic_case_count": len(diagnostic_plan.cases),
        "diagnostic_plan_sha256": diagnostic_plan.source_sha256,
        "fixed_step_budget_ms": diagnostic_plan.fixed_step_budget_ms,
        "golden_case_count": len(verified_golden["cases"]),
        "golden_sha256": file_sha256(GOLDEN_PATH),
        "implementation_hashes": {
            path.relative_to(ROOT).as_posix(): file_sha256(path)
            for path in implementation_paths
        },
        "interface": "gaotian.stage-t0b1a-authoritative-hot-path-baseline-regression/v1",
        "local_timing_artifact_policy": "ignored_not_persisted_in_regression_report",
        "next_slice": "T0b1b_static_fingerprint_and_validation_boundary",
        "observer_default_enabled": False,
        "official_performance_runs_executed": 0,
        "pre_t0b1b_baseline_probe_total_calls": {
            "advance_projectile_world": 1,
            "advance_weapon_timeline": 12,
            "build_tactical_ship_model": 12,
            "compile_runtime_ship_parameters": 24,
            "derive_tactical_ship_lifecycle": 12,
            "design_snapshot_source_sha256": 120,
            "integrate_tactical_step": 6,
            "refresh_lifecycle_boundary": 2,
            "validate_bindings": 1,
            "validate_instance_current_design": 60,
            "validate_internal_state": 2,
        },
        "scope": "t0b1a_diagnostic_protocol_call_counts_and_authority_golden_only",
        "status": "PASS",
        "t0_performance_measured": False,
    }
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    assert diagnostic_plan.source_path == DIAGNOSTIC_PLAN_PATH.resolve()
    assert DIAGNOSTIC_PLAN_INTERFACE == "gaotian.t0-hot-path-diagnostic-plan/v1"
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
