"""阶段 I11d：火势传播、弹药殉爆与二次爆炸回归。"""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from typing import Callable

from 高天荒野舰艇数据契约 import (
    ContractError,
    ShipInstanceSnapshotInput,
    canonical_json,
    canonical_sha256,
    load_material_registry,
)
from 高天荒野舰艇阶段F三舰集成测试 import build_chain
from 高天荒野舰艇阶段I武器时间与射击队列测试 import live_ship
from 高天荒野舰艇阶段I统一战术场景时间线测试 import scene_fixture
from 高天荒野舰艇阶段I持续毁伤与损管测试 import (
    fire_projectile_catalog,
    module_durability,
    scene_ship,
)
from 高天荒野舰艇运行时参数编译器 import compile_runtime_ship_parameters
from 高天荒野舰艇武器时间与射击队列 import (
    load_weapon_timing_profile_catalog,
)
from 高天荒野舰艇持续毁伤 import (
    FireIgnitionOutcome,
    advance_continuous_damage,
    continuous_damage_automatic_events,
    load_continuous_damage_profile,
    register_fire_ignition,
)
from 高天荒野舰艇人员伤亡 import (
    CrewCasualtyBreakdown,
    CrewCasualtyOutcome,
)
from 高天荒野舰艇二次毁伤 import (
    FIRE_PROPAGATION_ADJACENCY_M,
    SECONDARY_DAMAGE_INTERFACE_ID,
    SECONDARY_DAMAGE_POLICY_ID,
    AmmunitionCookoffConsumption,
    AmmunitionCookoffOutcome,
    FirePropagationOutcome,
    SecondaryExplosionModuleDamage,
    SecondaryFireIgnitionOutcome,
    apply_secondary_damage_outcomes,
)
from 高天荒野舰艇统一战术场景 import advance_tactical_scene_step


ROOT = Path(__file__).resolve().parent
MAIN_SCHEMA = ROOT / "舰艇数据" / "模式" / "高天荒野舰艇数据契约.v1alpha1.schema.json"
SECONDARY_SCHEMA = ROOT / "舰艇数据" / "模式" / "高天荒野舰艇二次毁伤结果契约.v1alpha1.schema.json"
CONTINUOUS_PROFILE = ROOT / "舰艇数据" / "标定" / "阶段I持续毁伤技术替身配置.v1.json"
TIMING_CATALOG = ROOT / "舰艇数据" / "标定" / "阶段I武器时间技术替身配置.v1.json"
STRUCTURE_CATALOG = ROOT / "舰艇数据" / "材料" / "结构材质.v1.json"
ARMOR_CATALOG = ROOT / "舰艇数据" / "材料" / "基础装甲材质.v1.json"
REPORT_PATH = ROOT / "舰艇数据" / "报告" / "阶段I二次毁伤接口.v1.json"


def require_contract_error(code: str, action: Callable[[], object]) -> None:
    try:
        action()
    except ContractError as error:
        if error.code != code:
            raise AssertionError(f"预期 {code}，实际为 {error.code}: {error}") from error
    else:
        raise AssertionError(f"预期抛出 {code}")


def inventory_units(instance: ShipInstanceSnapshotInput, munition_id: str) -> int:
    assert instance.ammunition_state is not None
    magazine = next(
        item
        for item in instance.ammunition_state.magazines
        if item.instance_id == "ammunition_magazine"
    )
    return next(
        (item.units for item in magazine.inventory if item.munition_id == munition_id),
        0,
    )


def register_source_fire(
    chain,
    instance: ShipInstanceSnapshotInput,
    profile,
    *,
    ship_id: str,
    fire_id: str,
    module_instance_id: str,
):
    return register_fire_ignition(
        chain.snapshot,
        instance,
        profile,
        FireIgnitionOutcome(
            f"projectile.fixture.{fire_id}",
            fire_id,
            ship_id,
            module_instance_id,
            1.0,
            10.0,
        ),
        ship_id=ship_id,
        created_time_s=0.0,
        state_tactical_time_s=0.0,
    ).resulting_instance


def advance_source_fire(chain, sortie, instance, profile, *, ship_id: str, target_time: float):
    runtime = compile_runtime_ship_parameters(
        chain.snapshot,
        sortie,
        instance,
        active_automatic_events=continuous_damage_automatic_events(instance),
    )
    return advance_continuous_damage(
        chain.snapshot,
        instance,
        runtime,
        profile,
        ship_id=ship_id,
        target_tactical_time_s=target_time,
    )


def main() -> None:
    schema = json.loads(MAIN_SCHEMA.read_text(encoding="utf-8"))
    fire_schema = schema["$defs"]["fireIncidentState"]["properties"]
    assert "propagated_from_fire_incident_id" in fire_schema
    assert "source_secondary_explosion_id" in fire_schema
    secondary_schema = json.loads(SECONDARY_SCHEMA.read_text(encoding="utf-8"))
    assert secondary_schema["$id"] == SECONDARY_DAMAGE_INTERFACE_ID

    profile = load_continuous_damage_profile(CONTINUOUS_PROFILE)
    chain = build_chain("conventional_crewed")
    sortie, legacy_instance = live_ship(chain)
    legacy_sha256 = canonical_sha256(legacy_instance)
    ship_id = "ship.fixture.stage_i11d.direct"

    propagation_source = register_source_fire(
        chain,
        legacy_instance,
        profile,
        ship_id=ship_id,
        fire_id="fire.fixture.stage_i11d.lift",
        module_instance_id="lift_tank",
    )
    propagation_advanced = advance_source_fire(
        chain,
        sortie,
        propagation_source,
        profile,
        ship_id=ship_id,
        target_time=1.0,
    )
    source_state = propagation_source.continuous_damage_state
    assert source_state is not None
    generator_before = module_durability(
        propagation_advanced.resulting_instance,
        "generator",
    )
    propagation_outcome = FirePropagationOutcome(
        "propagation.fixture.stage_i11d.primary",
        "fire.fixture.stage_i11d.lift",
        "fire.fixture.stage_i11d.generator",
        1.0,
        ship_id,
        "generator",
        0.5,
        4.0,
    )
    propagation = apply_secondary_damage_outcomes(
        chain.snapshot,
        propagation_advanced.resulting_instance,
        profile,
        ship_id=ship_id,
        target_tactical_time_s=1.0,
        source_fire_incidents=source_state.fire_incidents,
        source_fire_events=propagation_advanced.events,
        fire_propagation_outcomes=(propagation_outcome,),
    )
    repeated_propagation = apply_secondary_damage_outcomes(
        chain.snapshot,
        propagation_advanced.resulting_instance,
        profile,
        ship_id=ship_id,
        target_tactical_time_s=1.0,
        source_fire_incidents=source_state.fire_incidents,
        source_fire_events=propagation_advanced.events,
        fire_propagation_outcomes=(propagation_outcome,),
    )
    assert repeated_propagation == propagation
    assert module_durability(propagation.resulting_instance, "generator") == generator_before
    propagated_state = propagation.resulting_instance.continuous_damage_state
    assert propagated_state is not None
    propagated_fire = next(
        item
        for item in propagated_state.fire_incidents
        if item.id == propagation_outcome.incident_id
    )
    assert propagated_fire.created_time_s == 1.0
    assert (
        propagated_fire.propagated_from_fire_incident_id
        == propagation_outcome.source_fire_incident_id
    )
    restored = ShipInstanceSnapshotInput.parse(
        json.loads(canonical_json(propagation.resulting_instance))
    )
    assert canonical_json(restored) == canonical_json(propagation.resulting_instance)
    require_contract_error(
        "secondary_damage.propagation_not_adjacent",
        lambda: apply_secondary_damage_outcomes(
            chain.snapshot,
            propagation_advanced.resulting_instance,
            profile,
            ship_id=ship_id,
            target_tactical_time_s=1.0,
            source_fire_incidents=source_state.fire_incidents,
            source_fire_events=propagation_advanced.events,
            fire_propagation_outcomes=(
                replace(
                    propagation_outcome,
                    outcome_id="propagation.fixture.stage_i11d.nonadjacent",
                    incident_id="fire.fixture.stage_i11d.nonadjacent",
                    target_module_instance_id="ammunition_magazine",
                ),
            ),
        ),
    )
    require_contract_error(
        "secondary_damage.fire_source_unmatched",
        lambda: apply_secondary_damage_outcomes(
            chain.snapshot,
            propagation_advanced.resulting_instance,
            profile,
            ship_id=ship_id,
            target_tactical_time_s=1.0,
            source_fire_incidents=source_state.fire_incidents,
            source_fire_events=(),
            fire_propagation_outcomes=(propagation_outcome,),
        ),
    )

    cookoff_source = register_source_fire(
        chain,
        legacy_instance,
        profile,
        ship_id=ship_id,
        fire_id="fire.fixture.stage_i11d.magazine",
        module_instance_id="ammunition_magazine",
    )
    cookoff_advanced = advance_source_fire(
        chain,
        sortie,
        cookoff_source,
        profile,
        ship_id=ship_id,
        target_time=1.0,
    )
    cookoff_source_state = cookoff_source.continuous_damage_state
    assert cookoff_source_state is not None
    standard_munition = "gtw.munition.fixture.76mm.standard"
    cookoff_outcome = AmmunitionCookoffOutcome(
        "cookoff.fixture.stage_i11d.primary",
        "explosion.fixture.stage_i11d.primary",
        "fire.fixture.stage_i11d.magazine",
        1.0,
        ship_id,
        "ammunition_magazine",
        (AmmunitionCookoffConsumption(standard_munition, 1),),
        (SecondaryExplosionModuleDamage("generator", 2.0),),
        0.01,
        (
            SecondaryFireIgnitionOutcome(
                "fire.fixture.stage_i11d.secondary",
                "generator",
                0.25,
                2.0,
            ),
        ),
    )
    cookoff = apply_secondary_damage_outcomes(
        chain.snapshot,
        cookoff_advanced.resulting_instance,
        profile,
        ship_id=ship_id,
        target_tactical_time_s=1.0,
        source_fire_incidents=cookoff_source_state.fire_incidents,
        source_fire_events=cookoff_advanced.events,
        ammunition_cookoff_outcomes=(cookoff_outcome,),
    )
    assert inventory_units(cookoff.resulting_instance, standard_munition) == 3
    assert (
        module_durability(cookoff_advanced.resulting_instance, "generator")
        - module_durability(cookoff.resulting_instance, "generator")
        == 2.0
    )
    assert abs(
        cookoff_advanced.resulting_instance.current_hull_integrity_fraction
        - cookoff.resulting_instance.current_hull_integrity_fraction
        - 0.01
    ) < 1.0e-8
    cookoff_state = cookoff.resulting_instance.continuous_damage_state
    assert cookoff_state is not None
    secondary_fire = next(
        item
        for item in cookoff_state.fire_incidents
        if item.id == "fire.fixture.stage_i11d.secondary"
    )
    assert (
        secondary_fire.source_secondary_explosion_id
        == cookoff_outcome.explosion_id
    )
    assert len(cookoff.ammunition_cookoff_events) == 1
    assert cookoff.ammunition_cookoff_events[0].damaged_module_instance_ids == (
        "generator",
    )
    require_contract_error(
        "secondary_damage.insufficient_ammunition",
        lambda: apply_secondary_damage_outcomes(
            chain.snapshot,
            cookoff_advanced.resulting_instance,
            profile,
            ship_id=ship_id,
            target_tactical_time_s=1.0,
            source_fire_incidents=cookoff_source_state.fire_incidents,
            source_fire_events=cookoff_advanced.events,
            ammunition_cookoff_outcomes=(
                replace(
                    cookoff_outcome,
                    outcome_id="cookoff.fixture.stage_i11d.excess",
                    explosion_id="explosion.fixture.stage_i11d.excess",
                    consumed_ammunition=(
                        AmmunitionCookoffConsumption(standard_munition, 5),
                    ),
                ),
            ),
        ),
    )
    require_contract_error(
        "secondary_damage.cookoff_source_mismatch",
        lambda: apply_secondary_damage_outcomes(
            chain.snapshot,
            propagation_advanced.resulting_instance,
            profile,
            ship_id=ship_id,
            target_tactical_time_s=1.0,
            source_fire_incidents=source_state.fire_incidents,
            source_fire_events=propagation_advanced.events,
            ammunition_cookoff_outcomes=(
                replace(
                    cookoff_outcome,
                    outcome_id="cookoff.fixture.stage_i11d.wrong-source-module",
                    explosion_id="explosion.fixture.stage_i11d.wrong-source-module",
                    source_fire_incident_id="fire.fixture.stage_i11d.lift",
                ),
            ),
        ),
    )
    require_contract_error(
        "secondary_damage.secondary_fire_without_damage_target",
        lambda: apply_secondary_damage_outcomes(
            chain.snapshot,
            cookoff_advanced.resulting_instance,
            profile,
            ship_id=ship_id,
            target_tactical_time_s=1.0,
            source_fire_incidents=cookoff_source_state.fire_incidents,
            source_fire_events=cookoff_advanced.events,
            ammunition_cookoff_outcomes=(
                replace(
                    cookoff_outcome,
                    outcome_id="cookoff.fixture.stage_i11d.bad-fire",
                    explosion_id="explosion.fixture.stage_i11d.bad-fire",
                    secondary_fires=(
                        SecondaryFireIgnitionOutcome(
                            "fire.fixture.stage_i11d.bad-fire",
                            "cic",
                            0.25,
                            2.0,
                        ),
                    ),
                ),
            ),
        ),
    )
    assert canonical_sha256(legacy_instance) == legacy_sha256

    timing_catalog = load_weapon_timing_profile_catalog(TIMING_CATALOG)
    projectile_catalog = fire_projectile_catalog()
    registry = load_material_registry((STRUCTURE_CATALOG, ARMOR_CATALOG))
    bindings, initial_scene, launch_directive = scene_fixture(
        chain,
        timing_catalog,
        projectile_catalog,
    )
    target = next(item for item in initial_scene.ships if item.ship_id.endswith("target"))
    scene_propagation_source = register_source_fire(
        chain,
        target.combat_state.instance,
        profile,
        ship_id=target.ship_id,
        fire_id="fire.fixture.stage_i11d.scene-lift",
        module_instance_id="lift_tank",
    )
    propagation_scene = replace(
        initial_scene,
        ships=tuple(
            replace(
                item,
                combat_state=replace(
                    item.combat_state,
                    instance=scene_propagation_source,
                ),
            )
            if item.ship_id == target.ship_id
            else item
            for item in initial_scene.ships
        ),
    )
    scene_propagation_outcome = FirePropagationOutcome(
        "propagation.fixture.stage_i11d.scene",
        "fire.fixture.stage_i11d.scene-lift",
        "fire.fixture.stage_i11d.scene-generator",
        initial_scene.fixed_step_s,
        target.ship_id,
        "generator",
        0.5,
        4.0,
    )
    scene_propagation = advance_tactical_scene_step(
        propagation_scene,
        bindings,
        timing_catalog,
        projectile_catalog,
        registry,
        continuous_damage_profile=profile,
        fire_propagation_outcomes=(scene_propagation_outcome,),
        launch_directives=(launch_directive,),
    )
    assert len(scene_propagation.fire_propagation_events) == 1
    scene_propagated_target = scene_ship(
        scene_propagation.resulting_scene,
        target.ship_id,
    )
    assert any(
        item.id == scene_propagation_outcome.incident_id
        for item in scene_propagated_target.combat_state.instance.continuous_damage_state.fire_incidents
    )

    scene_fire_id = "fire.fixture.stage_i11d.scene-magazine"
    burning_target_instance = register_source_fire(
        chain,
        target.combat_state.instance,
        profile,
        ship_id=target.ship_id,
        fire_id=scene_fire_id,
        module_instance_id="ammunition_magazine",
    )
    burning_target = replace(
        target,
        combat_state=replace(
            target.combat_state,
            instance=burning_target_instance,
        ),
    )
    burning_scene = replace(
        initial_scene,
        ships=tuple(
            burning_target if item.ship_id == target.ship_id else item
            for item in initial_scene.ships
        ),
    )
    no_automatic = advance_tactical_scene_step(
        burning_scene,
        bindings,
        timing_catalog,
        projectile_catalog,
        registry,
        continuous_damage_profile=profile,
        launch_directives=(launch_directive,),
    )
    no_auto_target = scene_ship(no_automatic.resulting_scene, target.ship_id)
    assert inventory_units(no_auto_target.combat_state.instance, standard_munition) == 4
    assert not no_automatic.ammunition_cookoff_events

    end_time = initial_scene.fixed_step_s
    scene_cookoff = AmmunitionCookoffOutcome(
        "cookoff.fixture.stage_i11d.scene",
        "explosion.fixture.stage_i11d.scene",
        scene_fire_id,
        end_time,
        target.ship_id,
        "ammunition_magazine",
        (AmmunitionCookoffConsumption(standard_munition, 1),),
        (SecondaryExplosionModuleDamage("damage_control", 2.0),),
        0.001,
        (
            SecondaryFireIgnitionOutcome(
                "fire.fixture.stage_i11d.scene-secondary",
                "damage_control",
                0.25,
                2.0,
            ),
        ),
    )
    scene_casualty = CrewCasualtyOutcome(
        "casualty.fixture.stage_i11d.secondary",
        "secondary_explosion",
        scene_cookoff.explosion_id,
        end_time,
        target.ship_id,
        "damage_control",
        (CrewCasualtyBreakdown("ordinary", 1, 0),),
    )
    scene_resolution = advance_tactical_scene_step(
        burning_scene,
        bindings,
        timing_catalog,
        projectile_catalog,
        registry,
        continuous_damage_profile=profile,
        ammunition_cookoff_outcomes=(scene_cookoff,),
        crew_casualty_outcomes=(scene_casualty,),
        launch_directives=(launch_directive,),
    )
    repeated_scene = advance_tactical_scene_step(
        burning_scene,
        bindings,
        timing_catalog,
        projectile_catalog,
        registry,
        continuous_damage_profile=profile,
        ammunition_cookoff_outcomes=(scene_cookoff,),
        crew_casualty_outcomes=(scene_casualty,),
        launch_directives=(launch_directive,),
    )
    assert repeated_scene == scene_resolution
    resulting_target = scene_ship(scene_resolution.resulting_scene, target.ship_id)
    assert inventory_units(resulting_target.combat_state.instance, standard_munition) == 3
    assert len(scene_resolution.ammunition_cookoff_events) == 1
    assert len(scene_resolution.crew_casualty_events) == 1
    casualty_state = resulting_target.combat_state.instance.crew_casualty_state
    assert casualty_state is not None
    ordinary = next(
        item for item in casualty_state.crew_statuses if item.crew_type == "ordinary"
    )
    assert (ordinary.fit_for_duty_count, ordinary.wounded_count) == (9, 1)
    scene_secondary_fire = next(
        item
        for item in resulting_target.combat_state.instance.continuous_damage_state.fire_incidents
        if item.id == "fire.fixture.stage_i11d.scene-secondary"
    )
    assert scene_secondary_fire.created_time_s == end_time
    assert "ammunition_cookoff_events" in scene_resolution.to_dict()
    require_contract_error(
        "crew_casualty.source_module_unmatched",
        lambda: advance_tactical_scene_step(
            burning_scene,
            bindings,
            timing_catalog,
            projectile_catalog,
            registry,
            continuous_damage_profile=profile,
            ammunition_cookoff_outcomes=(scene_cookoff,),
            crew_casualty_outcomes=(
                replace(
                    scene_casualty,
                    outcome_id="casualty.fixture.stage_i11d.bad-module",
                    target_module_instance_id="cic",
                ),
            ),
            launch_directives=(launch_directive,),
        ),
    )

    report = {
        "adjacency_m": FIRE_PROPAGATION_ADJACENCY_M,
        "cookoff_events": [
            item.to_dict() for item in cookoff.ammunition_cookoff_events
        ],
        "deterministic_repeat_equal": repeated_propagation == propagation,
        "interface": SECONDARY_DAMAGE_INTERFACE_ID,
        "policy": SECONDARY_DAMAGE_POLICY_ID,
        "propagation_events": [
            item.to_dict() for item in propagation.fire_propagation_events
        ],
        "scene_cookoff_events": [
            item.to_dict() for item in scene_resolution.ammunition_cookoff_events
        ],
        "status": "PASS",
        "tested_error_codes": [
            "crew_casualty.source_module_unmatched",
            "secondary_damage.cookoff_source_mismatch",
            "secondary_damage.fire_source_unmatched",
            "secondary_damage.insufficient_ammunition",
            "secondary_damage.propagation_not_adjacent",
            "secondary_damage.secondary_fire_without_damage_target",
        ],
        "tested_paths": [
            "explicit_adjacent_fire_propagation",
            "no_birth_step_damage",
            "physical_magazine_inventory_consumption",
            "explicit_secondary_module_and_hull_damage",
            "secondary_fire_lineage",
            "secondary_explosion_crew_casualty",
            "no_automatic_propagation_or_cookoff",
            "scene_fire_propagation",
            "scene_save_shape",
        ],
    }
    REPORT_PATH.write_text(canonical_json(report), encoding="utf-8")
    assert json.loads(REPORT_PATH.read_text(encoding="utf-8"))["status"] == "PASS"


if __name__ == "__main__":
    main()
