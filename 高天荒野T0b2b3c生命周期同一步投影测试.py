"""T0b.2b3c：生命周期同一步语义投影复用与变化边界回归。"""

from __future__ import annotations

from dataclasses import replace
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
from benchmarks.t0.scenario import advance_scenario_step, build_scenario
from 高天荒野舰艇数据契约 import load_material_registry
from 高天荒野舰艇运行时参数编译器 import compile_runtime_ship_parameters
from 高天荒野舰艇阶段F三舰集成测试 import build_chain
from 高天荒野舰艇阶段I统一战术场景时间线测试 import prepared_ship
from 高天荒野舰艇阶段I战术生命周期测试 import replace_module
from 高天荒野舰艇武器时间与射击队列 import load_weapon_timing_profile_catalog
from 高天荒野舰艇战术弹丸世界 import load_projectile_profile_catalog
from 高天荒野舰艇战术机动求解器 import Vec2
import 高天荒野舰艇统一战术场景 as tactical_scene
from 高天荒野舰艇统一战术场景 import (
    TacticalSceneLaunchDirective,
    TacticalShipLifecycleState,
    advance_tactical_scene_step,
    derive_tactical_ship_lifecycle,
    initialize_tactical_scene,
    project_tactical_ship_lifecycle,
)


ROOT = Path(__file__).resolve().parent
PLAN_PATH = ROOT / "contracts" / "web_bridge" / "t0-benchmark-plan.v1.json"
DIAGNOSTIC_PLAN_PATH = (
    ROOT / "contracts" / "web_bridge" / "t0-hot-path-diagnostic.v1.json"
)
GOLDEN_PATH = ROOT / "contracts" / "web_bridge" / "t0-authority-step-golden.v1.json"
STRUCTURE_CATALOG = ROOT / "舰艇数据" / "材料" / "结构材质.v1.json"
ARMOR_CATALOG = ROOT / "舰艇数据" / "材料" / "基础装甲材质.v1.json"
TIMING_CATALOG_PATH = (
    ROOT / "舰艇数据" / "标定" / "阶段I武器时间技术替身配置.v1.json"
)
PROJECTILE_CATALOG_PATH = (
    ROOT / "舰艇数据" / "标定" / "阶段I弹丸与损伤技术替身配置.v1.json"
)
REPORT_PATH = ROOT / "舰艇数据" / "报告" / "阶段T0b2b3c生命周期同一步投影接口.v1.json"


def _replace_instance_module(
    instance,
    instance_id: str,
    *,
    durability: float | None = None,
    operating_mode: str | None = None,
):
    return replace(
        instance,
        module_states=tuple(
            replace(
                item,
                current_durability_points=(
                    item.current_durability_points
                    if durability is None or item.instance_id != instance_id
                    else durability
                ),
                operating_mode=(
                    item.operating_mode
                    if operating_mode is None or item.instance_id != instance_id
                    else operating_mode
                ),
            )
            for item in instance.module_states
        ),
    )


def _projection_tuple(item) -> tuple[object, ...]:
    return (
        item.physical_status,
        item.command_status,
        item.failure_causes,
        item.exit_reason,
        item.exit_tactical_time_s,
    )


def test_projection_matches_authoritative_derivation() -> dict[str, object]:
    chain = build_chain("conventional_crewed")
    healthy_runtime = compile_runtime_ship_parameters(
        chain.snapshot,
        chain.sortie,
        chain.instance,
    )
    healthy = derive_tactical_ship_lifecycle(
        healthy_runtime,
        chain.sortie,
        step_index=0,
    )
    cases = {
        "healthy_unchanged": (healthy_runtime, healthy),
        "cic_control_off": (
            compile_runtime_ship_parameters(
                chain.snapshot,
                chain.sortie,
                _replace_instance_module(
                    chain.instance,
                    "cic",
                    operating_mode="off",
                ),
            ),
            healthy,
        ),
        "cic_destroyed": (
            compile_runtime_ship_parameters(
                chain.snapshot,
                chain.sortie,
                _replace_instance_module(chain.instance, "cic", durability=0.0),
            ),
            healthy,
        ),
        "hull_collapsed": (
            compile_runtime_ship_parameters(
                chain.snapshot,
                chain.sortie,
                replace(chain.instance, current_hull_integrity_fraction=0.0),
            ),
            healthy,
        ),
        "falling_is_sticky": (
            healthy_runtime,
            TacticalShipLifecycleState(
                "falling",
                "uncommanded",
                ("cic_destroyed",),
                4,
            ),
        ),
        "exited_is_sticky": (
            healthy_runtime,
            TacticalShipLifecycleState(
                "exited",
                "uncommanded",
                ("cic_destroyed",),
                5,
                "fell_below_scene",
                1.25,
            ),
        ),
    }
    observed: dict[str, str] = {}
    for case_id, (runtime, previous) in cases.items():
        projection = project_tactical_ship_lifecycle(
            runtime,
            chain.sortie,
            previous=previous,
        )
        derived = derive_tactical_ship_lifecycle(
            runtime,
            chain.sortie,
            step_index=9,
            previous=previous,
        )
        assert _projection_tuple(projection) == _projection_tuple(derived)
        observed[case_id] = (
            f"{projection.physical_status}/{projection.command_status}"
        )
    assert observed == {
        "cic_control_off": "operational/uncommanded",
        "cic_destroyed": "falling/uncommanded",
        "exited_is_sticky": "exited/uncommanded",
        "falling_is_sticky": "falling/uncommanded",
        "healthy_unchanged": "operational/scene_command",
        "hull_collapsed": "falling/uncommanded",
    }
    return {
        "case_count": len(cases),
        "derived_projection_parity": True,
        "falling_and_exit_sticky": True,
    }


def test_unchanged_end_boundary_reuses_projection(plan) -> dict[str, int]:
    bundle = build_scenario(ROOT, plan, "target_20", "motion_only", 1)
    counts = {"materialize": 0, "project": 0}
    original_project = tactical_scene.project_tactical_ship_lifecycle
    original_materialize = tactical_scene._materialize_tactical_ship_lifecycle

    def traced_project(*args, **kwargs):
        counts["project"] += 1
        return original_project(*args, **kwargs)

    def traced_materialize(*args, **kwargs):
        counts["materialize"] += 1
        return original_materialize(*args, **kwargs)

    tactical_scene.project_tactical_ship_lifecycle = traced_project
    tactical_scene._materialize_tactical_ship_lifecycle = traced_materialize
    try:
        resolution = advance_scenario_step(bundle, bundle.initial_scene)
    finally:
        tactical_scene.project_tactical_ship_lifecycle = original_project
        tactical_scene._materialize_tactical_ship_lifecycle = original_materialize

    assert counts == {"materialize": 20, "project": 40}
    assert all(
        item.lifecycle_state.last_transition_step_index == 0
        for item in resolution.resulting_scene.ships
    )
    return {
        "end_boundary_materializations": 0,
        "materializations_total": counts["materialize"],
        "projections_total": counts["project"],
        "ships": len(resolution.resulting_scene.ships),
    }


def test_changed_end_boundary_is_not_skipped() -> dict[str, object]:
    registry = load_material_registry((STRUCTURE_CATALOG, ARMOR_CATALOG))
    timing_catalog = load_weapon_timing_profile_catalog(TIMING_CATALOG_PATH)
    projectile_catalog = load_projectile_profile_catalog(PROJECTILE_CATALOG_PATH)
    chain = build_chain("conventional_crewed")
    shooter, shooter_combat, shooter_motion = prepared_ship(
        chain,
        timing_catalog,
        "ship.fixture.b3c.shooter",
        queue_fire=True,
    )
    target, target_combat, target_motion = prepared_ship(
        chain,
        timing_catalog,
        "ship.fixture.b3c.target",
        queue_fire=False,
    )
    target_combat = replace_module(target_combat, {"cic"}, durability=1.0)
    shooter_motion = replace(shooter_motion, position_world_m=Vec2(-200.0, 10.0))
    target_motion = replace(target_motion, position_world_m=Vec2(0.0, 0.0))
    battle = initialize_tactical_scene(
        (shooter, target),
        projectile_catalog,
        timing_catalog,
        initial_motion_states={
            shooter.ship_id: shooter_motion,
            target.ship_id: target_motion,
        },
        initial_combat_states={
            shooter.ship_id: shooter_combat,
            target.ship_id: target_combat,
        },
    )
    launch = TacticalSceneLaunchDirective(
        shooter.ship_id,
        f"sequence.{shooter.ship_id}.single",
        0.0,
        "projectile.fixture.b3c.cic_hit",
        target.ship_id,
        0,
        (1.0, 0.0),
    )
    step = advance_tactical_scene_step(
        battle,
        (shooter, target),
        timing_catalog,
        projectile_catalog,
        registry,
        launch_directives=(launch,),
    )
    for _ in range(30):
        falling_event = next(
            (
                item
                for item in step.lifecycle_events
                if item.ship_id == target.ship_id
                and item.resulting_state.physical_status == "falling"
            ),
            None,
        )
        if step.impact_events:
            break
        step = advance_tactical_scene_step(
            step.resulting_scene,
            (shooter, target),
            timing_catalog,
            projectile_catalog,
            registry,
        )
    else:
        raise AssertionError("预期弹丸在限定步数内命中目标")

    assert falling_event is not None
    impact = step.impact_events[0]
    assert "cic" in impact.damaged_module_instance_ids
    assert impact.tactical_time_s <= falling_event.tactical_time_s
    assert (
        falling_event.resulting_state.last_transition_step_index
        == step.resulting_scene.fixed_step_index
    )
    assert falling_event.resulting_state.failure_causes == ("cic_destroyed",)
    return {
        "cic_damage_detected_at_end_boundary": True,
        "impact_and_lifecycle_event_same_resolution_step": True,
        "resulting_physical_status": falling_event.resulting_state.physical_status,
    }


def main() -> None:
    plan = load_benchmark_plan(PLAN_PATH)
    projection_evidence = test_projection_matches_authoritative_derivation()
    reuse_evidence = test_unchanged_end_boundary_reuses_projection(plan)
    changed_boundary_evidence = test_changed_end_boundary_is_not_skipped()

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
    assert probes["derive_tactical_ship_lifecycle"]["total_calls"] == 20
    assert probes["advance_weapon_timeline"]["total_calls"] == 0
    assert probes["canonical_sha256"]["total_calls"] <= 22
    assert probes["runtime_cache_resolve"]["total_calls"] <= 60

    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    assert report["interface"] == "gaotian.stage-t0b2b3c-intra-step-lifecycle-projection/v1"
    assert report["status"] == "PASS"
    assert report["official_performance_runs_executed"] == 0
    assert report["t0_performance_measured"] is False
    assert report["projection_evidence"] == projection_evidence
    assert report["reuse_evidence"] == reuse_evidence
    assert report["changed_boundary_evidence"] == changed_boundary_evidence
    assert report["target_20_motion_profile"] == {
        "advance_weapon_timeline_calls": 0,
        "canonical_sha256_calls": probes["canonical_sha256"]["total_calls"],
        "derive_tactical_ship_lifecycle_calls": 20,
        "runtime_cache_resolve_calls": probes["runtime_cache_resolve"]["total_calls"],
    }
    for relative_path in (
        "高天荒野T0b2b3c生命周期同一步投影测试.py",
        "高天荒野T0b2推进响应与权威性能优化规划.md",
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
                "derive_tactical_ship_lifecycle_calls": probes[
                    "derive_tactical_ship_lifecycle"
                ]["total_calls"],
                "interface": "gaotian.stage-t0b2b3c-intra-step-lifecycle-projection-test/v1",
                "status": "PASS",
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
