"""T0b.2b3a：步内精确 ShipStepContext 与重复解析收敛回归。"""

from __future__ import annotations

import json
from pathlib import Path

from benchmarks.t0.contracts import load_benchmark_plan
from benchmarks.t0.diagnostics import (
    load_authority_step_golden,
    load_hot_path_diagnostic_plan,
    profile_hot_path_case,
    verify_authority_step_golden,
)
from benchmarks.t0.metadata import file_sha256
from benchmarks.t0.scenario import (
    advance_scenario_step,
    build_scenario,
    launch_directives_for_step,
)
from 高天荒野舰艇数据契约 import canonical_sha256


ROOT = Path(__file__).resolve().parent
PLAN_PATH = ROOT / "contracts" / "web_bridge" / "t0-benchmark-plan.v1.json"
DIAGNOSTIC_PLAN_PATH = (
    ROOT / "contracts" / "web_bridge" / "t0-hot-path-diagnostic.v1.json"
)
GOLDEN_PATH = ROOT / "contracts" / "web_bridge" / "t0-authority-step-golden.v1.json"
REPORT_PATH = ROOT / "舰艇数据" / "报告" / "阶段T0b2b3a精确边界上下文接口.v1.json"


def test_exact_instance_survives_start_boundary_weapon_mutation(plan) -> dict[str, object]:
    bundle = build_scenario(ROOT, plan, "functional_6", "ordinary_projectiles", 1)
    initial = bundle.initial_scene
    directives = launch_directives_for_step(bundle, initial)
    assert directives
    source_ship_ids = tuple(sorted({item.source_ship_id for item in directives}))
    initial_by_id = {item.ship_id: item for item in initial.ships}

    resolution = advance_scenario_step(bundle, initial)
    resulting_by_id = {
        item.ship_id: item for item in resolution.resulting_scene.ships
    }
    result_runtime_by_id = {
        item.ship_id: item.resulting_runtime for item in resolution.ship_results
    }

    for ship_id, resulting_ship in resulting_by_id.items():
        runtime = result_runtime_by_id[ship_id]
        assert runtime.instance_snapshot == resulting_ship.combat_state.instance
        assert (
            runtime.instance_snapshot_sha256
            == canonical_sha256(resulting_ship.combat_state.instance)
        )
    for ship_id in source_ship_ids:
        before = initial_by_id[ship_id].combat_state.instance
        after = resulting_by_id[ship_id].combat_state.instance
        assert after.ammunition_state != before.ammunition_state
        assert after.weapon_timeline_state != before.weapon_timeline_state

    assert len(resolution.weapon_events) == len(directives)
    assert len(resolution.spawned_projectiles) == len(directives)
    return {
        "final_runtime_matches_exact_instance": True,
        "start_boundary_launch_count": len(directives),
        "weapon_mutation_preserved_after_motion_commit": True,
    }


def main() -> None:
    plan = load_benchmark_plan(PLAN_PATH)
    exact_instance_evidence = test_exact_instance_survives_start_boundary_weapon_mutation(
        plan
    )

    expected_golden = load_authority_step_golden(GOLDEN_PATH)
    assert verify_authority_step_golden(ROOT, plan, GOLDEN_PATH) == expected_golden

    diagnostic_plan = load_hot_path_diagnostic_plan(DIAGNOSTIC_PLAN_PATH)
    target_motion = next(
        item
        for item in diagnostic_plan.cases
        if item.id == "target_20.motion_only"
    )
    observed = profile_hot_path_case(ROOT, plan, diagnostic_plan, target_motion)
    assert observed["authority_equivalent"] is True
    probes = observed["profiled"]["function_probes"]
    assert probes["runtime_cache_resolve"]["total_calls"] <= 60
    assert probes["bind_tactical_ship_model"]["total_calls"] <= 20
    assert probes["compile_runtime_ship_parameters"]["total_calls"] == 0
    assert probes["runtime_view_bind"]["total_calls"] == 0
    assert probes["derive_tactical_ship_lifecycle"]["total_calls"] <= 40
    # b3b 及后续等价切片可以继续减少完整时间线引擎调用。
    assert probes["advance_weapon_timeline"]["total_calls"] <= 40

    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    assert report["interface"] == "gaotian.stage-t0b2b3a-exact-ship-step-context/v1"
    assert report["status"] == "PASS"
    assert report["official_performance_runs_executed"] == 0
    assert report["t0_performance_measured"] is False
    assert report["exact_instance_evidence"] == exact_instance_evidence
    assert report["target_20_motion_profile"] == {
        "bind_tactical_ship_model_calls": probes["bind_tactical_ship_model"][
            "total_calls"
        ],
        "compile_runtime_ship_parameters_calls": probes[
            "compile_runtime_ship_parameters"
        ]["total_calls"],
        "runtime_cache_resolve_calls": probes["runtime_cache_resolve"][
            "total_calls"
        ],
        "runtime_view_bind_calls": probes["runtime_view_bind"]["total_calls"],
    }
    for relative_path in (
        "高天荒野T0b2b3a精确边界上下文测试.py",
        "高天荒野T0b2推进响应与权威性能优化规划.md",
        "高天荒野T0b2运行时稳定核心测试.py",
        "高天荒野T0权威热路径基线测试.py",
        "高天荒野T0运行时与静态模型复用测试.py",
        "高天荒野Web客户端编辑器与战术验证实施计划.md",
        "高天荒野舰艇统一战术场景.py",
    ):
        assert report["implementation_hashes"][relative_path] == file_sha256(
            ROOT / relative_path
        )

    print(
        json.dumps(
            {
                "authority_golden": "12_of_12_PASS",
                "bind_tactical_ship_model_calls": probes[
                    "bind_tactical_ship_model"
                ]["total_calls"],
                "interface": "gaotian.stage-t0b2b3a-exact-ship-step-context-test/v1",
                "runtime_cache_resolve_calls": probes["runtime_cache_resolve"][
                    "total_calls"
                ],
                "status": "PASS",
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
