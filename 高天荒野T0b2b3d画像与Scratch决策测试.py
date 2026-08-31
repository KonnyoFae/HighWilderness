"""T0b.2b3d：P0 累计画像、场景级 scratch 取消与下一切片门禁。"""

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


ROOT = Path(__file__).resolve().parent
PLAN_PATH = ROOT / "contracts" / "web_bridge" / "t0-benchmark-plan.v1.json"
DIAGNOSTIC_PLAN_PATH = (
    ROOT / "contracts" / "web_bridge" / "t0-hot-path-diagnostic.v1.json"
)
GOLDEN_PATH = ROOT / "contracts" / "web_bridge" / "t0-authority-step-golden.v1.json"
REPORT_PATH = ROOT / "舰艇数据" / "报告" / "阶段T0b2b3d画像与Scratch决策接口.v1.json"


PROFILE_KEYS = (
    "advance_missile_guidance_step",
    "advance_projectile_world",
    "advance_weapon_timeline",
    "bind_tactical_ship_model",
    "canonical_sha256",
    "derive_tactical_ship_lifecycle",
    "integrate_tactical_step",
    "load_metrics",
    "projectile_geometry_hit",
    "runtime_cache_resolve",
    "spawn_projectiles_from_weapon_events",
)


def selected_calls(observed: dict[str, object]) -> dict[str, int]:
    probes = observed["profiled"]["function_probes"]
    return {key: probes[key]["total_calls"] for key in PROFILE_KEYS}


def main() -> None:
    plan = load_benchmark_plan(PLAN_PATH)
    diagnostic_plan = load_hot_path_diagnostic_plan(DIAGNOSTIC_PLAN_PATH)
    expected_golden = load_authority_step_golden(GOLDEN_PATH)
    assert verify_authority_step_golden(ROOT, plan, GOLDEN_PATH) == expected_golden

    observed_by_id = {}
    for case_id in ("target_20.motion_only", "target_20.guided_projectiles"):
        case = next(item for item in diagnostic_plan.cases if item.id == case_id)
        observed = profile_hot_path_case(ROOT, plan, diagnostic_plan, case)
        assert observed["authority_equivalent"] is True
        observed_by_id[case_id] = observed

    motion_calls = selected_calls(observed_by_id["target_20.motion_only"])
    assert motion_calls == {
        "advance_missile_guidance_step": 0,
        "advance_projectile_world": 1,
        "advance_weapon_timeline": 0,
        "bind_tactical_ship_model": 20,
        "canonical_sha256": 22,
        "derive_tactical_ship_lifecycle": 20,
        "integrate_tactical_step": 20,
        "load_metrics": 20,
        "projectile_geometry_hit": 0,
        "runtime_cache_resolve": 60,
        "spawn_projectiles_from_weapon_events": 0,
    }
    guided_calls = selected_calls(
        observed_by_id["target_20.guided_projectiles"]
    )
    assert guided_calls == {
        "advance_missile_guidance_step": 296,
        "advance_projectile_world": 1,
        "advance_weapon_timeline": 14,
        "bind_tactical_ship_model": 20,
        "canonical_sha256": 51,
        "derive_tactical_ship_lifecycle": 20,
        "integrate_tactical_step": 20,
        "load_metrics": 20,
        "projectile_geometry_hit": 2696,
        "runtime_cache_resolve": 102,
        "spawn_projectiles_from_weapon_events": 1,
    }

    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    assert report["interface"] == "gaotian.stage-t0b2b3d-profile-and-scratch-decision/v1"
    assert report["status"] == "PASS"
    assert report["authority_golden"] == "12_of_12_PASS"
    assert report["official_performance_runs_executed"] == 0
    assert report["t0_performance_measured"] is False
    assert report["scratch_decision"] == {
        "projectile_step_local_scratch": "deferred_to_T0b.2e1",
        "scene_step_local_scratch": "cancelled_no_clear_measurable_benefit",
    }
    assert report["current_profile_calls"] == {
        "target_20.guided_projectiles": guided_calls,
        "target_20.motion_only": motion_calls,
    }
    assert report["next_slice"] == "T0b.2c1_pure_propulsion_safety_governor"
    for relative_path in (
        "高天荒野T0b2b3d画像与Scratch决策测试.py",
        "高天荒野T0b2推进响应与权威性能优化规划.md",
        "高天荒野Web客户端编辑器与战术验证实施计划.md",
    ):
        assert report["implementation_hashes"][relative_path] == file_sha256(
            ROOT / relative_path
        )

    print(
        json.dumps(
            {
                "authority_golden": "12_of_12_PASS",
                "interface": "gaotian.stage-t0b2b3d-profile-and-scratch-decision-test/v1",
                "next_slice": report["next_slice"],
                "scene_scratch": "cancelled",
                "status": "PASS",
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
