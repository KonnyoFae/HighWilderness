"""T0b.1f：冻结短程协议、共享元数据、严格结果合同与唯一分支。"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

import benchmarks.t0.decision as decision_module
from benchmarks.t0.contracts import BenchmarkContractError, load_benchmark_plan
from benchmarks.t0.decision import (
    SHORT_DECISION_INTERFACE,
    run_short_diagnostic_decision,
    select_decision_branch,
    validate_short_decision_result,
)
from benchmarks.t0.diagnostics import load_hot_path_diagnostic_plan
from benchmarks.t0.scenario import (
    advance_scenario_step,
    build_scenario,
    guidance_inputs_for_step,
    launch_directives_for_step,
)


ROOT = Path(__file__).resolve().parent
PLAN_PATH = ROOT / "contracts" / "web_bridge" / "t0-benchmark-plan.v1.json"
DIAGNOSTIC_PATH = ROOT / "contracts" / "web_bridge" / "t0-hot-path-diagnostic.v1.json"
SCHEMA_PATH = (
    ROOT
    / "contracts"
    / "web_bridge"
    / "t0b1f-short-diagnostic-decision.v1.schema.json"
)
HEX = "a" * 64


def require_error(code: str, action: Callable[[], object]) -> None:
    try:
        action()
    except BenchmarkContractError as error:
        if error.code != code:
            raise AssertionError(f"预期 {code}，实际 {error.code}: {error}") from error
    else:
        raise AssertionError(f"预期抛出 {code}")


def test_branch_selection() -> None:
    common = {
        "authority_repeatable": True,
        "resources_bounded": True,
        "motion_hotspot_supported": True,
    }
    assert select_decision_branch(
        target_motion_realtime_factors=[1.1] * 3,
        target_guided_realtime_factors=[1.01] * 3,
        **common,
    )["selected_branch"] == "branch_1_resume_t0c"
    assert select_decision_branch(
        target_motion_realtime_factors=[1.1] * 3,
        target_guided_realtime_factors=[0.9] * 3,
        **common,
    )["selected_branch"] == "branch_2_review_projectile_rust_hot_core"
    assert select_decision_branch(
        target_motion_realtime_factors=[0.9] * 3,
        target_guided_realtime_factors=[0.5] * 3,
        **common,
    )["selected_branch"] == "branch_3_author_multirate_authority_rfc"
    assert select_decision_branch(
        target_motion_realtime_factors=[1.1] * 3,
        target_guided_realtime_factors=[1.01] * 3,
        authority_repeatable=True,
        resources_bounded=False,
        motion_hotspot_supported=True,
    )["selected_branch"] == "branch_4_continue_t0b1_boundedness"
    assert select_decision_branch(
        target_motion_realtime_factors=[1.1] * 3,
        target_guided_realtime_factors=[1.01] * 3,
        authority_repeatable=False,
        resources_bounded=True,
        motion_hotspot_supported=True,
    )["status"] == "FAIL"
    require_error(
        "decision.repetition_count",
        lambda: select_decision_branch(
            target_motion_realtime_factors=[1.0],
            target_guided_realtime_factors=[1.0] * 3,
            **common,
        ),
    )


def _metric(value: float, count: int) -> dict[str, float | int]:
    return {
        "maximum": value,
        "mean": value,
        "p95_nearest_rank": value,
        "sample_count": count,
    }


def test_orchestration_contract(plan, diagnostic_plan) -> dict:
    calls = {"environment": 0, "profile": 0, "run": 0}
    command = "python -X utf8 -m benchmarks.t0 decide-short-diagnostic"
    environment = {
        "command": command,
        "commit": HEX,
        "cpu": "test-cpu",
        "dirty": False,
        "dirty_diff_sha256": HEX,
        "node": "test-node",
        "os": "test-os",
        "power_mode": "test-power",
        "python": "test-python",
        "ram_bytes": 16_000_000_000,
        "rust": "test-rust",
        "webview2": "test-webview",
    }
    originals = {
        "baseline": decision_module._historical_call_baselines,
        "build": decision_module.build_scenario,
        "environment": decision_module.collect_environment_metadata,
        "profile": decision_module.profile_hot_path_case,
        "run": decision_module.run_headless_baseline,
    }

    def fake_environment(root, *, command):
        calls["environment"] += 1
        assert command == environment["command"]
        return dict(environment)

    def fake_baseline(root):
        return {
            case.id: {
                "build_tactical_ship_static_model": 10,
                "compile_runtime_ship_parameters": 20,
            }
            for case in diagnostic_plan.cases
        }

    def fake_profile(root, benchmark_plan, supplied_diagnostic_plan, case):
        calls["profile"] += 1
        return {
            "profiled": {
                "function_probes": {
                    "build_tactical_ship_static_model": {"total_calls": 0},
                    "compile_runtime_ship_parameters": {"total_calls": 0},
                    "integrate_tactical_step": {"total_calls": 1},
                },
                "top_hotspots": [
                    {
                        "cumulative_seconds": 0.01,
                        "function": "高天荒野舰艇战术机动求解器.py:1(integrate_tactical_step)",
                        "primitive_calls": 1,
                        "self_seconds": 0.01,
                        "total_calls": 1,
                    }
                ],
            }
        }

    def fake_build(root, benchmark_plan, profile, stage, repetition):
        return SimpleNamespace(profile=profile, stage=stage, repetition=repetition)

    def fake_run(bundle, *, command, warmup_steps, measured_steps, environment_metadata):
        calls["run"] += 1
        assert environment_metadata == environment
        assert warmup_steps == 10 and measured_steps == 60
        case = f"{bundle.profile}.{bundle.stage}"
        digest = (case.encode("utf-8").hex() + "0" * 64)[:64]
        realtime = 0.8 if bundle.stage == "motion_only" else 0.5
        return {
            "authority_event_sha256": digest,
            "authority_state_sha256": digest,
            "execution": {
                "authoritative_advance_wall_s": 1.0,
                "measured_wall_s": 1.25,
                "observer_drain_wall_s": 0.25,
            },
            "initial_scene_sha256": digest,
            "load_coverage": {
                "reasons": ["诊断短跑未使用计划规定的预热和测量步数"],
                "status": "NOT_COVERED",
            },
            "run_result": {
                "metrics": {
                    "fixed_step": _metric(20.0, 60),
                    "observer_drain": _metric(1.0, 60),
                    "real_time_factor": _metric(realtime, 1),
                    "resident_memory": _metric(100_000_000.0, 1),
                },
                "run_spec": {"input_stream_sha256": digest},
            },
        }

    decision_module.collect_environment_metadata = fake_environment
    decision_module._historical_call_baselines = fake_baseline
    decision_module.profile_hot_path_case = fake_profile
    decision_module.build_scenario = fake_build
    decision_module.run_headless_baseline = fake_run
    try:
        result = run_short_diagnostic_decision(
            ROOT, plan, diagnostic_plan, command=command
        )
    finally:
        decision_module._historical_call_baselines = originals["baseline"]
        decision_module.build_scenario = originals["build"]
        decision_module.collect_environment_metadata = originals["environment"]
        decision_module.profile_hot_path_case = originals["profile"]
        decision_module.run_headless_baseline = originals["run"]

    assert calls == {"environment": 1, "profile": 6, "run": 18}
    assert result["interface"] == SHORT_DECISION_INTERFACE
    assert result["decision"]["selected_branch"] == (
        "branch_3_author_multirate_authority_rfc"
    )
    assert result["official_performance_runs_executed"] == 0
    assert result["t0_performance_measured"] is False
    assert all(item["authority_repeatable"] for item in result["cases"])
    validate_short_decision_result(result)
    invalid = deepcopy(result)
    invalid["unexpected"] = True
    require_error(
        "decision.object_keys", lambda: validate_short_decision_result(invalid)
    )
    return result


def test_schema() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert schema["properties"]["interface"]["const"] == SHORT_DECISION_INTERFACE
    assert schema["properties"]["official_performance_runs_executed"]["const"] == 0
    assert schema["properties"]["t0_performance_measured"]["const"] is False
    assert schema["properties"]["protocol"]["properties"]["warmup_steps"]["const"] == 10
    assert schema["properties"]["protocol"]["properties"]["measured_steps"]["const"] == 60
    assert schema["properties"]["protocol"]["properties"]["repetitions"]["const"] == 3


def test_end_boundary_guidance_inputs(plan) -> None:
    bundle = build_scenario(ROOT, plan, "functional_6", "guided_projectiles", 1)
    scene = bundle.initial_scene
    for _ in range(59):
        scene = advance_scenario_step(bundle, scene).resulting_scene
    assert scene.fixed_step_index == 59
    directives = launch_directives_for_step(bundle, scene)
    end_boundary_ids = {
        item.projectile_id
        for item in directives
        if item.tactical_time_s > scene.tactical_time_s
    }
    assert len(end_boundary_ids) == 4
    inputs = guidance_inputs_for_step(bundle, scene, directives)
    assert end_boundary_ids.isdisjoint(item.projectile_id for item in inputs)
    resolution = advance_scenario_step(bundle, scene)
    assert end_boundary_ids == {item.id for item in resolution.spawned_projectiles}
    next_inputs = guidance_inputs_for_step(bundle, resolution.resulting_scene, ())
    assert end_boundary_ids <= {item.projectile_id for item in next_inputs}


def main() -> None:
    plan = load_benchmark_plan(PLAN_PATH)
    diagnostic_plan = load_hot_path_diagnostic_plan(DIAGNOSTIC_PATH)
    test_branch_selection()
    result = test_orchestration_contract(plan, diagnostic_plan)
    test_schema()
    test_end_boundary_guidance_inputs(plan)
    report = {
        "acceptance": {
            "branch_selection": "4_of_4_PASS",
            "environment_metadata_collection": "1_for_18_runs_PASS",
            "end_boundary_guidance_inputs": "PASS",
            "frozen_protocol": "10_warmup_60_measured_3_repetitions_PASS",
            "official_scope_guard": "PASS",
            "result_contract_unknown_key_rejected": "PASS",
            "schema_contract": "PASS",
            "six_case_orchestration": "6_profiles_18_runs_PASS",
        },
        "interface": "gaotian.stage-t0b1f-short-decision-regression/v1",
        "selected_synthetic_branch": result["decision"]["selected_branch"],
        "status": "PASS",
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
