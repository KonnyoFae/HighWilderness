"""T0b.1d：到期事件索引、边界批量生成、目标几何与增量实体计数。"""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from typing import Callable

from 高天荒野舰艇数据契约 import ContractError
from 高天荒野舰艇运行时参数编译器 import RUNTIME_CACHE_VALIDATION_TRUSTED
from 高天荒野舰艇武器时间与射击队列 import advance_weapon_timeline
from 高天荒野舰艇战术弹丸世界 import (
    ProjectileSpawnInput,
    ProjectileSpawnRequest,
    ShipPose2D,
    TacticalProjectileTarget,
    _geometry_hit,
    compile_projectile_target_geometry,
    spawn_projectile_from_weapon_event,
    spawn_projectiles_from_weapon_events,
)
from benchmarks.t0.contracts import load_benchmark_plan
from benchmarks.t0.diagnostics import (
    load_hot_path_diagnostic_plan,
    profile_hot_path_case,
    verify_authority_step_golden,
)
from benchmarks.t0.scenario import (
    SceneEntityCounter,
    advance_scenario_step,
    build_scenario,
    launch_directives_for_step,
    scene_entity_counts,
)


ROOT = Path(__file__).resolve().parent
PLAN_PATH = ROOT / "contracts" / "web_bridge" / "t0-benchmark-plan.v1.json"
DIAGNOSTIC_PLAN_PATH = (
    ROOT / "contracts" / "web_bridge" / "t0-hot-path-diagnostic.v1.json"
)
GOLDEN_PATH = ROOT / "contracts" / "web_bridge" / "t0-authority-step-golden.v1.json"


def require_error(code: str, action: Callable[[], object]) -> None:
    try:
        action()
    except ContractError as error:
        if error.code != code:
            raise AssertionError(f"预期 {code}，实际 {error.code}: {error}") from error
    else:
        raise AssertionError(f"预期抛出 {code}")


def pose_at_zero(motion) -> ShipPose2D:
    return ShipPose2D(
        0.0,
        (motion.position_world_m.x, motion.position_world_m.y),
        motion.heading_rad,
        (motion.velocity_world_mps.x, motion.velocity_world_mps.y),
        motion.yaw_rate_radps,
    )


def test_batch_spawn_equivalence(plan) -> int:
    bundle = build_scenario(ROOT, plan, "functional_6", "guided_projectiles", 1)
    scene = bundle.initial_scene
    binding_by_id = {item.ship_id: item for item in bundle.bindings}
    ship_by_id = {item.ship_id: item for item in scene.ships}
    directive_map = {
        (item.source_ship_id, item.sequence_id, item.tactical_time_s): item
        for item in launch_directives_for_step(bundle, scene)
    }
    inputs = []
    for ship_id in sorted(ship_by_id):
        binding = binding_by_id[ship_id]
        ship = ship_by_id[ship_id]
        resolution = advance_weapon_timeline(
            binding.snapshot,
            binding.sortie,
            ship.combat_state.instance,
            bundle.timing_catalog,
            target_tactical_time_s=0.0,
            runtime_cache=binding.runtime_cache,
            runtime_validation_mode=RUNTIME_CACHE_VALIDATION_TRUSTED,
        )
        for event in resolution.events:
            if event.status != "resolved" or event.action_kind != "fire":
                continue
            directive = directive_map[(ship_id, event.sequence_id, 0.0)]
            inputs.append(
                ProjectileSpawnInput(
                    binding.snapshot,
                    event,
                    pose_at_zero(ship.motion_state),
                    ProjectileSpawnRequest(
                        directive.projectile_id,
                        ship_id,
                        directive.target_ship_id,
                        directive.selected_target_deck_level,
                        directive.launch_direction_local_xy,
                    ),
                )
            )
    assert len(inputs) >= 2

    batch = spawn_projectiles_from_weapon_events(
        scene.projectile_world,
        bundle.projectile_catalog,
        inputs,
        guidance_catalog=bundle.guidance_catalog,
    )
    sequential_world = scene.projectile_world
    sequential_projectiles = []
    for item in inputs:
        spawned = spawn_projectile_from_weapon_event(
            item.snapshot,
            item.event,
            sequential_world,
            bundle.projectile_catalog,
            item.source_pose,
            item.request,
            guidance_catalog=bundle.guidance_catalog,
        )
        sequential_world = spawned.resulting_world
        sequential_projectiles.append(spawned.projectile)
    assert batch.resulting_world == sequential_world
    assert batch.projectiles == tuple(sequential_projectiles)
    assert tuple(item.id for item in batch.resulting_world.projectiles) == tuple(
        sorted(item.id for item in batch.resulting_world.projectiles)
    )

    duplicate = replace(
        inputs[1],
        request=replace(
            inputs[1].request,
            projectile_id=inputs[0].request.projectile_id,
        ),
    )
    require_error(
        "projectile_world.projectile_duplicate",
        lambda: spawn_projectiles_from_weapon_events(
            scene.projectile_world,
            bundle.projectile_catalog,
            (inputs[0], duplicate),
            guidance_catalog=bundle.guidance_catalog,
        ),
    )
    return len(inputs)


def test_geometry_invalidation_and_reuse(plan) -> None:
    bundle = build_scenario(ROOT, plan, "functional_6", "guided_projectiles", 1)
    binding = bundle.bindings[0]
    ship = next(
        item for item in bundle.initial_scene.ships if item.ship_id == binding.ship_id
    )
    geometry = compile_projectile_target_geometry(binding.snapshot)
    projectile = bundle.initial_scene.projectile_world.projectiles[0]
    target_ship = next(
        item
        for item in bundle.initial_scene.ships
        if item.ship_id == projectile.target_ship_id
    )
    target_binding = next(
        item for item in bundle.bindings if item.ship_id == target_ship.ship_id
    )
    bad_geometry = replace(
        compile_projectile_target_geometry(target_binding.snapshot),
        snapshot_sha256="0" * 64,
    )
    target = TacticalProjectileTarget(
        target_ship.ship_id,
        target_binding.snapshot,
        target_ship.combat_state,
        pose_at_zero(target_ship.motion_state),
        geometry=bad_geometry,
    )
    require_error(
        "projectile_world.target_geometry_mismatch",
        lambda: _geometry_hit(
            projectile,
            projectile.position_xy,
            projectile.velocity_xy,
            0.0,
            0.005,
            target,
        ),
    )
    assert geometry.snapshot_sha256 == binding.snapshot.source_sha256

    first = advance_scenario_step(bundle, bundle.initial_scene)
    assert all(
        item.projectile_target_geometry is not None for item in bundle.bindings
    )
    cached_ids = {
        item.ship_id: id(item.projectile_target_geometry) for item in bundle.bindings
    }
    advance_scenario_step(bundle, first.resulting_scene)
    assert cached_ids == {
        item.ship_id: id(item.projectile_target_geometry) for item in bundle.bindings
    }


def test_incremental_entity_counts(plan) -> int:
    bundle = build_scenario(ROOT, plan, "target_20", "guided_projectiles", 1)
    scene = bundle.initial_scene
    counter = SceneEntityCounter.from_scene(scene)
    assert counter.snapshot() == scene_entity_counts(scene)
    steps = 3
    for _ in range(steps):
        resolution = advance_scenario_step(bundle, scene)
        scene = resolution.resulting_scene
        assert counter.advance(resolution) == scene_entity_counts(scene)
    return steps


def main() -> None:
    plan = load_benchmark_plan(PLAN_PATH)
    diagnostic_plan = load_hot_path_diagnostic_plan(DIAGNOSTIC_PLAN_PATH)
    verified = verify_authority_step_golden(ROOT, plan, GOLDEN_PATH)
    assert len(verified["cases"]) == 12
    batch_size = test_batch_spawn_equivalence(plan)
    test_geometry_invalidation_and_reuse(plan)
    count_steps = test_incremental_entity_counts(plan)

    probes_by_case = {}
    local_timings = {}
    for case in diagnostic_plan.cases:
        observed = profile_hot_path_case(ROOT, plan, diagnostic_plan, case)
        assert observed["authority_equivalent"] is True
        probes = observed["profiled"]["function_probes"]
        assert probes["spawn_projectile_from_weapon_event"]["total_calls"] == 0
        if case.load_stage == "guided_projectiles":
            assert probes["spawn_projectiles_from_weapon_events"]["total_calls"] == 1
            assert probes["projectile_geometry_hit"]["total_calls"] > 0
        else:
            assert probes["spawn_projectiles_from_weapon_events"]["total_calls"] == 0
        assert probes["compile_projectile_target_geometry"]["total_calls"] == 0
        probes_by_case[case.id] = {
            "batch_spawn_calls": probes["spawn_projectiles_from_weapon_events"][
                "total_calls"
            ],
            "geometry_compile_calls": probes[
                "compile_projectile_target_geometry"
            ]["total_calls"],
            "geometry_hit_calls": probes["projectile_geometry_hit"]["total_calls"],
            "single_spawn_calls": probes[
                "spawn_projectile_from_weapon_event"
            ]["total_calls"],
            "timeline_calls": probes["advance_weapon_timeline"]["total_calls"],
        }
        local_timings[case.id] = observed["unprofiled"]["fixed_step_ms"][
            "p95_nearest_rank"
        ]

    result = {
        "acceptance": {
            "authority_golden_verification": "12_of_12_PASS",
            "batch_matches_sequential_spawn": "PASS",
            "batch_duplicate_rejected": "PASS",
            "entity_counter_matches_full_scan": f"{count_steps}_of_{count_steps}_PASS",
            "geometry_revision_invalidation": "PASS",
            "stable_projectile_id_order": "PASS",
            "steady_step_geometry_recompiles": "0_in_6_of_6_PASS",
        },
        "batch_equivalence_projectiles": batch_size,
        "interface": "gaotian.stage-t0b1d-event-projectile-hot-path/v1",
        "local_diagnostic_fixed_step_ms": local_timings,
        "local_timing_artifact_policy": "diagnostic_only_not_official_t0",
        "next_slice": "T0b1e_measurement_boundary_and_production_gate",
        "official_performance_runs_executed": 0,
        "probe_results": probes_by_case,
        "remaining_hotspot_evidence": [
            "projectile_substep_integration_and_geometry_broad_phase",
            "runtime_cache_strict_validation_hashing",
            "tactical_motion_integration",
        ],
        "status": "PASS",
        "t0_performance_measured": False,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
