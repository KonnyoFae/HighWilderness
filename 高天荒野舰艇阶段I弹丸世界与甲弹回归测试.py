"""阶段 I5：开火事件、弹丸实体、真实船壳边与模块损伤回归。"""

from __future__ import annotations

from dataclasses import replace
import json
from math import isclose
from pathlib import Path
from typing import Callable

from 高天荒野舰艇出航配置编译器 import compile_sortie_configuration
from 高天荒野舰艇数据契约 import (
    ContractError,
    HullBlueprintInput,
    OutfitPlanInput,
    canonical_json,
    canonical_sha256,
    load_hull_coating_catalog,
    load_material_registry,
)
from 高天荒野舰艇无界面船壳编译器 import compile_hull
from 高天荒野舰艇无界面舾装编译器 import (
    build_derived_ship_snapshot,
    compile_outfit,
)
from 高天荒野舰艇运行时参数编译器 import initialize_ship_instance_snapshot
from 高天荒野舰艇阶段F三舰集成测试 import build_chain
from 高天荒野舰艇阶段I弹药与武器动作测试 import fire_request
from 高天荒野舰艇阶段I武器时间与射击队列测试 import live_ship
from 高天荒野舰艇武器时间与射击队列 import (
    advance_weapon_timeline,
    enqueue_continuous_fire,
    initialize_weapon_timeline,
    load_weapon_timing_profile_catalog,
)
from 高天荒野舰艇战术弹丸世界 import (
    PROJECTILE_DAMAGE_POLICY_ID,
    PROJECTILE_HIT_POLICY_ID,
    PROJECTILE_INTEGRATION_POLICY_ID,
    PROJECTILE_WORLD_INTERFACE_ID,
    ProjectileWorldState,
    ProjectileSpawnRequest,
    ShipCombatState,
    ShipPose2D,
    TacticalProjectileTarget,
    advance_projectile_world,
    initialize_projectile_world,
    initialize_ship_combat_state,
    load_projectile_profile_catalog,
    spawn_projectile_from_weapon_event,
)


ROOT = Path(__file__).resolve().parent
STRUCTURE_CATALOG = ROOT / "舰艇数据" / "材料" / "结构材质.v1.json"
ARMOR_CATALOG = ROOT / "舰艇数据" / "材料" / "基础装甲材质.v1.json"
COATING_CATALOG = ROOT / "舰艇数据" / "涂料" / "船体涂料.v1.json"
TIMING_CATALOG = ROOT / "舰艇数据" / "标定" / "阶段I武器时间技术替身配置.v1.json"
PROJECTILE_CATALOG = ROOT / "舰艇数据" / "标定" / "阶段I弹丸与损伤技术替身配置.v1.json"
PROJECTILE_SCHEMA = ROOT / "舰艇数据" / "模式" / "高天荒野舰艇弹丸性能数据契约.v1alpha1.schema.json"
REPORT_PATH = ROOT / "舰艇数据" / "报告" / "阶段I弹丸世界与甲弹接口.v1.json"


def require_contract_error(code: str, action: Callable[[], object]) -> None:
    try:
        action()
    except ContractError as error:
        if error.code != code:
            raise AssertionError(f"预期 {code}，实际为 {error.code}: {error}") from error
    else:
        raise AssertionError(f"预期抛出 {code}")


def armored_target(
    chain,
    thickness_m: float,
    material_id: str = "gtw.material.base_armor.armor_steel",
):
    registry = load_material_registry((STRUCTURE_CATALOG, ARMOR_CATALOG))
    hull_source = chain.hull.normalized_blueprint.to_dict()
    suffix = str(round(thickness_m * 1000.0)).replace(".", "_")
    hull_source["id"] = f"gtw.hull.fixture.stage_i5.armor_{suffix}mm"
    hull_source["name"] = f"阶段I5·{thickness_m * 1000.0:g}毫米基础装甲目标"
    # 只加固本回归实际使用的舰尾边与一对轴对称舷边，
    # 避免把“局部甲弹测试”误变成一条四周重甲而无法起飞的内容舰。
    base_edges = hull_source["decks"][0]["regions"][0]["edge_armor"]
    for edge_index in (1, 3, 6):
        base_edges[edge_index]["thickness_m"] = thickness_m
        base_edges[edge_index]["material"] = {"id": material_id, "version": 1}
    hull = compile_hull(HullBlueprintInput.parse(hull_source), registry)

    outfit_source = chain.outfit.normalized_plan.to_dict()
    outfit_source["id"] = f"gtw.outfit.fixture.stage_i5.armor_{suffix}mm"
    outfit_source["name"] = f"阶段I5·{thickness_m * 1000.0:g}毫米装甲目标舀装"
    outfit_source["hull_blueprint"] = {
        "id": hull.normalized_blueprint.id,
        "version": hull.normalized_blueprint.version,
    }
    outfit = compile_outfit(
        OutfitPlanInput.parse(outfit_source),
        hull,
        chain.module_catalog,
        load_hull_coating_catalog(COATING_CATALOG),
    )
    snapshot = build_derived_ship_snapshot(hull, outfit)
    configuration = replace(
        chain.sortie.configuration,
        id=f"gtw.sortie.fixture.stage_i5.armor_{suffix}mm",
        name=f"阶段I5·{thickness_m * 1000.0:g}毫米装甲目标出航",
        outfit_plan=outfit.normalized_plan and replace(
            chain.sortie.configuration.outfit_plan,
            id=outfit.normalized_plan.id,
            version=outfit.normalized_plan.version,
        ),
    )
    sortie = compile_sortie_configuration(snapshot, configuration)
    instance = initialize_ship_instance_snapshot(snapshot, sortie)
    return registry, snapshot, instance


def fire_event(chain, timing_catalog):
    sortie, instance = live_ship(chain)
    timed = initialize_weapon_timeline(chain.snapshot, instance, timing_catalog)
    queued = enqueue_continuous_fire(
        chain.snapshot,
        sortie,
        timed,
        timing_catalog,
        replace(fire_request(), id="sequence.fixture.stage_i5", rounds=1),
    ).resulting_instance
    resolution = advance_weapon_timeline(
        chain.snapshot,
        sortie,
        queued,
        timing_catalog,
        target_tactical_time_s=0.0,
    )
    assert len(resolution.events) == 1
    return resolution.events[0]


def module_durability(target, instance_id: str) -> float:
    return next(
        item.current_durability_points
        for item in target.combat_state.instance.module_states
        if item.instance_id == instance_id
    )


def main() -> None:
    schema = json.loads(PROJECTILE_SCHEMA.read_text(encoding="utf-8"))
    assert schema["$id"] == "gaotian.projectile-profile/v1alpha1"
    catalog = load_projectile_profile_catalog(PROJECTILE_CATALOG)
    timing_catalog = load_weapon_timing_profile_catalog(TIMING_CATALOG)
    assert catalog.fixture_level == "contract_fixture"
    assert catalog.profile("gtw.munition.fixture.76mm.standard").ballistic.caliber_mm == 76.0

    chain = build_chain("conventional_crewed")
    event = fire_event(chain, timing_catalog)

    # 100mm装甲目标：从真实炮塔中心发射，命中舰尾边并穿入 deck.0。
    registry_100, snapshot_100, instance_100 = armored_target(chain, 0.1)
    world = initialize_projectile_world(catalog)
    spawned = spawn_projectile_from_weapon_event(
        chain.snapshot,
        event,
        world,
        catalog,
        ShipPose2D(0.0, (0.1, 0.0), 0.0, (0.0, 100.0), 0.0),
        ProjectileSpawnRequest(
            "projectile.fixture.stage_i5.penetration",
            "ship.fixture.shooter",
            "ship.fixture.target_100mm",
            0,
            (0.0, 1.0),
        ),
    )
    projectile = spawned.projectile
    assert projectile.position_xy == (-4.9, -10.0)
    assert projectile.velocity_xy == (0.0, 1005.0)
    serialized_world = canonical_json(spawned.resulting_world)
    restored_world = ProjectileWorldState.parse(json.loads(serialized_world))
    assert canonical_json(restored_world) == serialized_world
    target_100 = TacticalProjectileTarget(
        "ship.fixture.target_100mm",
        snapshot_100,
        initialize_ship_combat_state(snapshot_100, instance_100),
        ShipPose2D(0.0, (0.0, 200.0), 0.0, (0.0, 0.0), 0.0),
    )
    penetration = advance_projectile_world(
        spawned.resulting_world,
        catalog,
        (target_100,),
        registry_100,
        target_tactical_time_s=1.0,
        density_kg_m3=0.55,
        sound_speed_mps=320.0,
        fixed_step_s=0.005,
    )
    assert penetration.resulting_world.projectiles == ()
    assert len(penetration.impact_events) == 1
    penetrating_hit = penetration.impact_events[0]
    assert penetrating_hit.armor_result.outcome.value == "penetrated"
    assert penetrating_hit.deck_id == "deck.0"
    assert penetrating_hit.region_id == "deck.0.region.0"
    assert penetrating_hit.edge_index == 1
    assert penetrating_hit.armor_durability_after < penetrating_hit.armor_durability_before
    assert penetrating_hit.hull_integrity_after < penetrating_hit.hull_integrity_before
    assert "main_engine_port" in penetrating_hit.damaged_module_instance_ids
    penetrated_target = penetration.resulting_targets[0]
    assert module_durability(penetrated_target, "main_engine_port") < 100.0
    serialized_combat = canonical_json(penetrated_target.combat_state)
    restored_combat = ShipCombatState.parse(json.loads(serialized_combat))
    assert canonical_json(restored_combat) == serialized_combat

    # 110mm轻质碳化物复合装甲（等效148.5mm钢）目标：
    # 横向命中左舷 edge 6 上的真实转向机挂载步长。
    registry_110, snapshot_110, instance_110 = armored_target(
        chain,
        0.11,
        "gtw.material.base_armor.lightweight_carbide_composite",
    )
    surface_world = initialize_projectile_world(catalog)
    surface_spawn = spawn_projectile_from_weapon_event(
        chain.snapshot,
        event,
        surface_world,
        catalog,
        ShipPose2D(0.0, (-200.0, -17.5), 0.0, (0.0, 0.0), 0.0),
        ProjectileSpawnRequest(
            "projectile.fixture.stage_i5.surface",
            "ship.fixture.shooter",
            "ship.fixture.target_110mm_carbide",
            0,
            (1.0, 0.0),
        ),
    )
    target_110 = TacticalProjectileTarget(
        "ship.fixture.target_110mm_carbide",
        snapshot_110,
        initialize_ship_combat_state(snapshot_110, instance_110),
        ShipPose2D(0.0, (0.0, 0.0), 0.0, (0.0, 0.0), 0.0),
    )
    stopped = advance_projectile_world(
        surface_spawn.resulting_world,
        catalog,
        (target_110,),
        registry_110,
        target_tactical_time_s=1.0,
        density_kg_m3=0.55,
        sound_speed_mps=320.0,
        fixed_step_s=0.005,
    )
    stopped_hit = stopped.impact_events[0]
    assert stopped_hit.armor_result.outcome.value == "stopped"
    assert stopped_hit.edge_index == 6
    assert isclose(stopped_hit.hull_integrity_after, 1.0)
    assert "thruster_port_aft" in stopped_hit.damaged_module_instance_ids
    assert module_durability(stopped.resulting_targets[0], "thruster_port_aft") == 75.0

    # 远处目标不命中：弹丸按弹种最大寿命自毁。
    expiry_spawn = spawn_projectile_from_weapon_event(
        chain.snapshot,
        event,
        initialize_projectile_world(catalog),
        catalog,
        ShipPose2D(0.0, (0.0, 0.0), 0.0, (0.0, 0.0), 0.0),
        ProjectileSpawnRequest(
            "projectile.fixture.stage_i5.expiry",
            "ship.fixture.shooter",
            "ship.fixture.target_far",
            0,
            (0.0, 1.0),
        ),
    )
    far_target = replace(
        target_100,
        ship_id="ship.fixture.target_far",
        pose=ShipPose2D(0.0, (100000.0, 100000.0), 0.0, (0.0, 0.0), 0.0),
    )
    expired = advance_projectile_world(
        expiry_spawn.resulting_world,
        catalog,
        (far_target,),
        registry_100,
        target_tactical_time_s=31.0,
        density_kg_m3=0.55,
        sound_speed_mps=320.0,
        fixed_step_s=1.0,
    )
    assert expired.resulting_world.projectiles == ()
    assert expired.impact_events == ()
    assert expired.expired_events[0].reason == "maximum_lifetime"

    wrong_catalog = replace(catalog, id="gtw.projectile_profile.fixture.stage_i5.changed")
    require_contract_error(
        "projectile_world.profile_catalog_mismatch",
        lambda: advance_projectile_world(
            spawned.resulting_world,
            wrong_catalog,
            (target_100,),
            registry_100,
            target_tactical_time_s=1.0,
            density_kg_m3=0.55,
            sound_speed_mps=320.0,
        ),
    )
    require_contract_error(
        "projectile_world.time_reversed",
        lambda: advance_projectile_world(
            penetration.resulting_world,
            catalog,
            (penetrated_target,),
            registry_100,
            target_tactical_time_s=0.5,
            density_kg_m3=0.55,
            sound_speed_mps=320.0,
        ),
    )

    report = {
        "catalog": {
            "reference": catalog.reference.to_dict(),
            "source_sha256": catalog.source_sha256,
        },
        "fixture_notice": "弹种与损伤换算仍是契约技术替身，不是正式内容平衡值。",
        "interface": PROJECTILE_WORLD_INTERFACE_ID,
        "policies": {
            "damage": PROJECTILE_DAMAGE_POLICY_ID,
            "hit": PROJECTILE_HIT_POLICY_ID,
            "integration": PROJECTILE_INTEGRATION_POLICY_ID,
        },
        "penetration_case": penetrating_hit.to_dict(),
        "source_fire_event_sha256": canonical_sha256(event.to_dict()),
        "stopped_surface_case": stopped_hit.to_dict(),
        "verified_boundaries": [
            "I4成功单发开火事件才能生成弹丸",
            "发射原点取真实武器模块中心并继承舰体速度",
            "命中甲板层由上层显式注入，本层不发明散布或甲板概率",
            "未击穿不降低有效厚度，但递减局部装甲耐久并可损伤外部模块",
            "击穿后仅检索所选甲板层的真实内部占格并回写模块/船壳状态",
        ],
    }
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print("阶段I5弹丸世界与甲弹回归：PASS")


if __name__ == "__main__":
    main()
