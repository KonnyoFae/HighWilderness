"""阶段 I11a：持久火灾、显式点燃与损管队固定步闭环回归。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from typing import Callable

from 高天荒野舰艇数据契约 import (
    ContractError,
    ShipInstanceSnapshotInput,
    canonical_json,
    load_material_registry,
)
from 高天荒野舰艇阶段F三舰集成测试 import build_chain
from 高天荒野舰艇阶段I武器时间与射击队列测试 import live_ship
from 高天荒野舰艇阶段I统一战术场景时间线测试 import scene_fixture
from 高天荒野舰艇运行时参数编译器 import compile_runtime_ship_parameters
from 高天荒野舰艇武器时间与射击队列 import load_weapon_timing_profile_catalog
from 高天荒野舰艇持续毁伤 import (
    CONTINUOUS_DAMAGE_INTERFACE_ID,
    CONTINUOUS_DAMAGE_POLICY_ID,
    CONTINUOUS_DAMAGE_SCHEMA_ID,
    ContinuousDamageProfile,
    DamageControlDirective,
    FireIgnitionOutcome,
    advance_continuous_damage,
    apply_damage_control_directives,
    continuous_damage_automatic_events,
    load_continuous_damage_profile,
    register_fire_ignition,
    validate_instance_continuous_damage,
)
from 高天荒野舰艇实例设计状态 import transition_current_design
from 高天荒野舰艇船坞后勤与战略工时 import quote_ship_repair
from 高天荒野舰艇战术弹丸世界 import (
    ProjectileProfileCatalog,
    load_projectile_profile_catalog,
)
from 高天荒野舰艇统一战术场景 import (
    TacticalSceneState,
    advance_tactical_scene_step,
)


ROOT = Path(__file__).resolve().parent
CONTINUOUS_SCHEMA = ROOT / "舰艇数据" / "模式" / "高天荒野舰艇持续毁伤数据契约.v1alpha1.schema.json"
CONTINUOUS_PROFILE = ROOT / "舰艇数据" / "标定" / "阶段I持续毁伤技术替身配置.v1.json"
PROJECTILE_CATALOG = ROOT / "舰艇数据" / "标定" / "阶段I弹丸与损伤技术替身配置.v1.json"
TIMING_CATALOG = ROOT / "舰艇数据" / "标定" / "阶段I武器时间技术替身配置.v1.json"
STRUCTURE_CATALOG = ROOT / "舰艇数据" / "材料" / "结构材质.v1.json"
ARMOR_CATALOG = ROOT / "舰艇数据" / "材料" / "基础装甲材质.v1.json"
REPORT_PATH = ROOT / "舰艇数据" / "报告" / "阶段I持续毁伤与损管接口.v1.json"


def require_contract_error(code: str, action: Callable[[], object]) -> None:
    try:
        action()
    except ContractError as error:
        if error.code != code:
            raise AssertionError(f"预期 {code}，实际为 {error.code}: {error}") from error
    else:
        raise AssertionError(f"预期抛出 {code}")


def module_durability(instance: ShipInstanceSnapshotInput, instance_id: str) -> float:
    return next(
        item.current_durability_points
        for item in instance.module_states
        if item.instance_id == instance_id
    )


def scene_ship(scene: TacticalSceneState, ship_id: str):
    return next(item for item in scene.ships if item.ship_id == ship_id)


def fire_projectile_catalog() -> ProjectileProfileCatalog:
    source = json.loads(PROJECTILE_CATALOG.read_text(encoding="utf-8"))
    source["id"] = "gtw.projectile_profile.fixture.stage_i11.fire"
    source["name"] = "阶段I11·火灾命中技术替身弹丸配置"
    source["profiles"][0]["penetration"]["aftereffect"] = "fire"
    return ProjectileProfileCatalog.parse(source)


def find_impact_boundary(scene, bindings, timing_catalog, projectile_catalog, registry, profile, directive):
    current = scene
    for step_index in range(40):
        launch = (directive,) if step_index == 0 else ()
        resolution = advance_tactical_scene_step(
            current,
            bindings,
            timing_catalog,
            projectile_catalog,
            registry,
            continuous_damage_profile=profile,
            launch_directives=launch,
        )
        if resolution.impact_events:
            return current, resolution, launch
        current = resolution.resulting_scene
    raise AssertionError("技术替身弹丸未在预期固定步内命中")


def main() -> None:
    schema = json.loads(CONTINUOUS_SCHEMA.read_text(encoding="utf-8"))
    assert schema["$id"] == CONTINUOUS_DAMAGE_SCHEMA_ID
    profile = load_continuous_damage_profile(CONTINUOUS_PROFILE)
    chain = build_chain("conventional_crewed")
    sortie, legacy_instance = live_ship(chain)
    assert "continuous_damage_state" not in legacy_instance.to_dict()

    ignition = FireIgnitionOutcome(
        "projectile.fixture.stage_i11.direct",
        "fire.fixture.stage_i11.engine",
        "ship.fixture.target",
        "main_engine_port",
        1.0,
        10.0,
    )
    registered = register_fire_ignition(
        chain.snapshot,
        legacy_instance,
        profile,
        ignition,
        ship_id="ship.fixture.target",
        created_time_s=0.0,
        state_tactical_time_s=0.0,
    )
    burning_instance = registered.resulting_instance
    state = burning_instance.continuous_damage_state
    assert state is not None and len(state.fire_incidents) == 1
    assert registered.events[0].event_kind == "fire_started"
    validate_instance_continuous_damage(chain.snapshot, burning_instance, profile)
    restored = ShipInstanceSnapshotInput.parse(
        json.loads(canonical_json(burning_instance))
    )
    assert canonical_json(restored) == canonical_json(burning_instance)

    runtime = compile_runtime_ship_parameters(
        chain.snapshot,
        sortie,
        burning_instance,
        active_automatic_events=continuous_damage_automatic_events(
            burning_instance
        ),
    )
    assert runtime.module("damage_control").automatically_activated
    no_control = advance_continuous_damage(
        chain.snapshot,
        burning_instance,
        runtime,
        profile,
        ship_id="ship.fixture.target",
        target_tactical_time_s=1.0,
    )
    repeated = advance_continuous_damage(
        chain.snapshot,
        burning_instance,
        runtime,
        profile,
        ship_id="ship.fixture.target",
        target_tactical_time_s=1.0,
    )
    assert repeated == no_control
    no_control_state = no_control.resulting_instance.continuous_damage_state
    assert no_control_state is not None
    assert abs(no_control_state.fire_incidents[0].intensity_units - 0.95) < 1.0e-8
    assert abs(no_control_state.fire_incidents[0].remaining_fuel_units - 9.5) < 1.0e-8
    assert abs(
        module_durability(burning_instance, "main_engine_port")
        - module_durability(no_control.resulting_instance, "main_engine_port")
        - 4.0
    ) < 1.0e-8
    assert abs(
        burning_instance.current_hull_integrity_fraction
        - no_control.resulting_instance.current_hull_integrity_fraction
        - 0.001
    ) < 1.0e-8

    assignment = DamageControlDirective(
        "ship.fixture.target",
        "damage_control",
        0,
        ignition.incident_id,
    )
    assigned = apply_damage_control_directives(
        chain.snapshot,
        burning_instance,
        profile,
        ship_id="ship.fixture.target",
        tactical_time_s=0.0,
        directives=(assignment,),
    )
    assert assigned.events[0].event_kind == "damage_control_assignment_set"
    assigned_runtime = compile_runtime_ship_parameters(
        chain.snapshot,
        sortie,
        assigned.resulting_instance,
        active_automatic_events=continuous_damage_automatic_events(
            assigned.resulting_instance
        ),
    )
    assert (
        assigned_runtime.module("damage_control").function_efficiency(
            "damage_control.firefighting"
        )
        > 0.0
    )
    extinguished = advance_continuous_damage(
        chain.snapshot,
        assigned.resulting_instance,
        assigned_runtime,
        profile,
        ship_id="ship.fixture.target",
        target_tactical_time_s=1.0,
    )
    extinguished_state = extinguished.resulting_instance.continuous_damage_state
    assert extinguished_state is not None
    assert not extinguished_state.fire_incidents
    assert not extinguished_state.damage_control_assignments
    assert extinguished.events[-1].event_kind == "fire_extinguished"

    off_states = tuple(
        replace(item, operating_mode="off")
        if item.instance_id == "damage_control"
        else item
        for item in assigned.resulting_instance.module_states
    )
    off_instance = replace(assigned.resulting_instance, module_states=off_states)
    off_runtime = compile_runtime_ship_parameters(
        chain.snapshot,
        sortie,
        off_instance,
        active_automatic_events=continuous_damage_automatic_events(off_instance),
    )
    assert (
        off_runtime.module("damage_control").function_efficiency(
            "damage_control.firefighting"
        )
        == 0.0
    )
    off_result = advance_continuous_damage(
        chain.snapshot,
        off_instance,
        off_runtime,
        profile,
        ship_id="ship.fixture.target",
        target_tactical_time_s=1.0,
    )
    assert off_result.resulting_instance.continuous_damage_state is not None
    assert off_result.resulting_instance.continuous_damage_state.fire_incidents

    require_contract_error(
        "continuous_damage.team_index",
        lambda: apply_damage_control_directives(
            chain.snapshot,
            burning_instance,
            profile,
            ship_id="ship.fixture.target",
            tactical_time_s=0.0,
            directives=(
                DamageControlDirective(
                    "ship.fixture.target",
                    "damage_control",
                    1,
                    ignition.incident_id,
                ),
            ),
        ),
    )
    require_contract_error(
        "continuous_damage.state_required",
        lambda: apply_damage_control_directives(
            chain.snapshot,
            legacy_instance,
            profile,
            ship_id="ship.fixture.target",
            tactical_time_s=0.0,
            directives=(assignment,),
        ),
    )
    require_contract_error(
        "continuous_damage.profile_mismatch",
        lambda: validate_instance_continuous_damage(
            chain.snapshot,
            burning_instance,
            replace(profile, version=2),
        ),
    )
    require_contract_error(
        "continuous_damage.time_reversed",
        lambda: advance_continuous_damage(
            chain.snapshot,
            no_control.resulting_instance,
            compile_runtime_ship_parameters(
                chain.snapshot,
                sortie,
                no_control.resulting_instance,
                active_automatic_events=continuous_damage_automatic_events(
                    no_control.resulting_instance
                ),
            ),
            profile,
            ship_id="ship.fixture.target",
            target_tactical_time_s=0.5,
        ),
    )
    require_contract_error(
        "refit.active_fire",
        lambda: transition_current_design(
            chain.snapshot,
            burning_instance,
            chain.snapshot,
        ),
    )
    require_contract_error(
        "logistics.active_fire",
        lambda: quote_ship_repair(
            "quote.fixture.stage_i11.active_fire",
            chain.snapshot,
            burning_instance,
        ),
    )
    bad_profile = deepcopy(json.loads(CONTINUOUS_PROFILE.read_text(encoding="utf-8")))
    bad_profile["hull_integrity_fraction_per_intensity_s"] = 1.1
    require_contract_error(
        "continuous_damage.hull_rate",
        lambda: ContinuousDamageProfile.parse(bad_profile),
    )

    # 真实弹丸命中：fire 后效只有收到显式点燃结果时才登记火灾，出生步不偷跑伤害。
    timing_catalog = load_weapon_timing_profile_catalog(TIMING_CATALOG)
    projectile_catalog = fire_projectile_catalog()
    registry = load_material_registry((STRUCTURE_CATALOG, ARMOR_CATALOG))
    bindings, initial_scene, launch_directive = scene_fixture(
        chain,
        timing_catalog,
        projectile_catalog,
    )
    before_impact, probe, impact_launch = find_impact_boundary(
        initial_scene,
        bindings,
        timing_catalog,
        projectile_catalog,
        registry,
        profile,
        launch_directive,
    )
    impact = probe.impact_events[0]
    assert impact.damaged_module_instance_ids
    impacted_module = impact.damaged_module_instance_ids[0]
    scene_ignition = FireIgnitionOutcome(
        launch_directive.projectile_id,
        "fire.fixture.stage_i11.scene",
        impact.target_ship_id,
        impacted_module,
        1.0,
        10.0,
    )
    ignited = advance_tactical_scene_step(
        before_impact,
        bindings,
        timing_catalog,
        projectile_catalog,
        registry,
        continuous_damage_profile=profile,
        fire_ignition_outcomes=(scene_ignition,),
        launch_directives=impact_launch,
    )
    assert [item.event_kind for item in ignited.continuous_damage_events] == [
        "fire_started"
    ]
    probe_target = scene_ship(probe.resulting_scene, impact.target_ship_id)
    ignited_target = scene_ship(ignited.resulting_scene, impact.target_ship_id)
    assert probe_target.combat_state.instance.continuous_damage_state is None
    assert module_durability(
        probe_target.combat_state.instance, impacted_module
    ) == module_durability(ignited_target.combat_state.instance, impacted_module)
    scene_fire_state = ignited_target.combat_state.instance.continuous_damage_state
    assert scene_fire_state is not None
    assert scene_fire_state.fire_incidents[0].intensity_units == 1.0

    controlled = advance_tactical_scene_step(
        ignited.resulting_scene,
        bindings,
        timing_catalog,
        projectile_catalog,
        registry,
        continuous_damage_profile=profile,
        damage_control_directives=(
            DamageControlDirective(
                impact.target_ship_id,
                "damage_control",
                0,
                scene_ignition.incident_id,
            ),
        ),
    )
    controlled_kinds = {
        item.event_kind for item in controlled.continuous_damage_events
    }
    assert "damage_control_assignment_set" in controlled_kinds
    assert "fire_damage_applied" in controlled_kinds
    assert "fire_suppressed" in controlled_kinds
    controlled_target = scene_ship(controlled.resulting_scene, impact.target_ship_id)
    controlled_state = controlled_target.combat_state.instance.continuous_damage_state
    assert controlled_state is not None
    assert controlled_state.tactical_time_s == controlled.resulting_scene.tactical_time_s
    target_result = next(
        item for item in controlled.ship_results if item.ship_id == impact.target_ship_id
    )
    assert target_result.resulting_runtime.module("damage_control").automatically_activated

    serialized_scene = canonical_json(controlled.resulting_scene)
    restored_scene = TacticalSceneState.parse(json.loads(serialized_scene))
    assert canonical_json(restored_scene) == serialized_scene

    # 原有 internal_blast 后效不能借显式结果旁路创建火灾。
    original_projectile_catalog = load_projectile_profile_catalog(PROJECTILE_CATALOG)
    original_bindings, original_scene, original_directive = scene_fixture(
        chain,
        timing_catalog,
        original_projectile_catalog,
    )
    original_before, original_probe, original_launch = find_impact_boundary(
        original_scene,
        original_bindings,
        timing_catalog,
        original_projectile_catalog,
        registry,
        profile,
        original_directive,
    )
    original_impact = original_probe.impact_events[0]
    assert original_impact.damaged_module_instance_ids
    require_contract_error(
        "continuous_damage.ignition_aftereffect",
        lambda: advance_tactical_scene_step(
            original_before,
            original_bindings,
            timing_catalog,
            original_projectile_catalog,
            registry,
            continuous_damage_profile=profile,
            fire_ignition_outcomes=(
                FireIgnitionOutcome(
                    original_directive.projectile_id,
                    "fire.fixture.stage_i11.invalid_aftereffect",
                    original_impact.target_ship_id,
                    original_impact.damaged_module_instance_ids[0],
                    1.0,
                    10.0,
                ),
            ),
            launch_directives=original_launch,
        ),
    )

    report = {
        "catalog": {
            "reference": profile.reference.to_dict(),
            "sha256": profile.source_sha256,
        },
        "deterministic_repeat_equal": repeated == no_control,
        "direct_fire_events": [item.to_dict() for item in no_control.events],
        "extinguish_events": [item.to_dict() for item in extinguished.events],
        "interface": CONTINUOUS_DAMAGE_INTERFACE_ID,
        "legacy_instance_shape_preserved": "continuous_damage_state"
        not in legacy_instance.to_dict(),
        "policy": CONTINUOUS_DAMAGE_POLICY_ID,
        "scene_events": [
            item.to_dict() for item in controlled.continuous_damage_events
        ],
        "schema": CONTINUOUS_DAMAGE_SCHEMA_ID,
        "status": "PASS",
        "tested_error_codes": [
            "continuous_damage.hull_rate",
            "continuous_damage.ignition_aftereffect",
            "continuous_damage.profile_mismatch",
            "continuous_damage.state_required",
            "continuous_damage.team_index",
            "continuous_damage.time_reversed",
            "logistics.active_fire",
            "refit.active_fire",
        ],
        "tested_paths": [
            "explicit_fire_ignition",
            "persistent_module_and_hull_damage",
            "automatic_damage_control_activation",
            "assignment_and_suppression",
            "disabled_damage_control_zero_suppression",
            "impact_birth_step_no_tick",
            "scene_save_reload",
        ],
    }
    REPORT_PATH.write_text(canonical_json(report), encoding="utf-8")
    assert json.loads(REPORT_PATH.read_text(encoding="utf-8"))["status"] == "PASS"


if __name__ == "__main__":
    main()
