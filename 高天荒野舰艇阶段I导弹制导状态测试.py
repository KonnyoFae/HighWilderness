"""阶段 I10 首切片：导引头配置、持久状态及弹丸生成绑定回归。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from typing import Callable

from 高天荒野舰艇数据契约 import (
    ContractError,
    canonical_json,
    canonical_sha256,
    load_material_registry,
)
from 高天荒野舰艇阶段F三舰集成测试 import build_chain
from 高天荒野舰艇阶段I弹丸世界与甲弹回归测试 import fire_event
from 高天荒野舰艇阶段I统一战术场景时间线测试 import scene_fixture
from 高天荒野舰艇武器时间与射击队列 import load_weapon_timing_profile_catalog
from 高天荒野舰艇导弹制导 import (
    MISSILE_GUIDANCE_INITIALIZATION_POLICY_ID,
    MISSILE_GUIDANCE_SCHEMA_ID,
    MISSILE_GUIDANCE_STATE_INTERFACE_ID,
    SEEKER_KINDS,
    MissileGuidanceProfile,
    MissileGuidanceProfileCatalog,
    MissileGuidanceRuntimeInput,
    MissileGuidanceState,
    initialize_missile_guidance_state,
    load_missile_guidance_profile_catalog,
    validate_missile_guidance_state,
)
from 高天荒野舰艇战术弹丸世界 import (
    ProjectileSpawnRequest,
    ProjectileWorldState,
    ShipPose2D,
    initialize_projectile_world,
    load_projectile_profile_catalog,
    spawn_projectile_from_weapon_event,
)
from 高天荒野舰艇统一战术场景 import advance_tactical_scene_step


ROOT = Path(__file__).resolve().parent
GUIDANCE_SCHEMA = ROOT / "舰艇数据" / "模式" / "高天荒野舰艇导弹制导数据契约.v1alpha1.schema.json"
GUIDANCE_CATALOG = ROOT / "舰艇数据" / "标定" / "阶段I导弹制导技术替身配置.v1.json"
PROJECTILE_CATALOG = ROOT / "舰艇数据" / "标定" / "阶段I弹丸与损伤技术替身配置.v1.json"
TIMING_CATALOG = ROOT / "舰艇数据" / "标定" / "阶段I武器时间技术替身配置.v1.json"
REPORT_PATH = ROOT / "舰艇数据" / "报告" / "阶段I导弹制导状态接口.v1.json"
STRUCTURE_CATALOG = ROOT / "舰艇数据" / "材料" / "结构材质.v1.json"
ARMOR_CATALOG = ROOT / "舰艇数据" / "材料" / "基础装甲材质.v1.json"


def require_contract_error(code: str, action: Callable[[], object]) -> None:
    try:
        action()
    except ContractError as error:
        if error.code != code:
            raise AssertionError(f"预期 {code}，实际为 {error.code}: {error}") from error
    else:
        raise AssertionError(f"预期抛出 {code}")


def profile_variant(
    source: dict[str, object],
    *,
    munition_id: str,
    seeker_kind: str,
    launch_support: str,
    allowed_height_layers: list[str],
    target_loss_behavior: str,
    target_memory_s: float,
    activation_distance_m: float | None,
) -> dict[str, object]:
    result = deepcopy(source)
    result.update(
        {
            "activation_distance_m": activation_distance_m,
            "allowed_height_layers": allowed_height_layers,
            "launch_support": launch_support,
            "munition_id": munition_id,
            "name": f"阶段I10·{seeker_kind} 合同夹具",
            "seeker_kind": seeker_kind,
            "target_loss_behavior": target_loss_behavior,
            "target_memory_s": target_memory_s,
        }
    )
    return result


def main() -> None:
    schema = json.loads(GUIDANCE_SCHEMA.read_text(encoding="utf-8"))
    assert schema["$id"] == MISSILE_GUIDANCE_SCHEMA_ID
    fixture_source = json.loads(GUIDANCE_CATALOG.read_text(encoding="utf-8"))
    fixture_catalog = load_missile_guidance_profile_catalog(GUIDANCE_CATALOG)
    assert fixture_catalog.fixture_level == "contract_fixture"
    active_source = fixture_source["profiles"][0]

    passive_source = profile_variant(
        active_source,
        munition_id="gtw.munition.fixture.missile.passive_radar",
        seeker_kind="passive_radar",
        launch_support="continuous_illumination",
        allowed_height_layers=["upper", "cloud", "rain"],
        target_loss_behavior="self_destruct",
        target_memory_s=0.0,
        activation_distance_m=None,
    )
    anti_radiation_source = profile_variant(
        active_source,
        munition_id="gtw.munition.fixture.missile.anti_radiation",
        seeker_kind="anti_radiation",
        launch_support="optional_fire_control",
        allowed_height_layers=["upper", "cloud", "rain"],
        target_loss_behavior="last_known_position_then_self_destruct",
        target_memory_s=4.0,
        activation_distance_m=2500.0,
    )
    electro_optical_source = profile_variant(
        active_source,
        munition_id="gtw.munition.fixture.missile.electro_optical",
        seeker_kind="electro_optical",
        launch_support="optional_fire_control",
        allowed_height_layers=["upper"],
        target_loss_behavior="last_known_position_then_self_destruct",
        target_memory_s=1.0,
        activation_distance_m=1500.0,
    )
    all_kinds_source = deepcopy(fixture_source)
    all_kinds_source["profiles"] = [
        active_source,
        passive_source,
        anti_radiation_source,
        electro_optical_source,
    ]
    all_kinds = MissileGuidanceProfileCatalog.parse(all_kinds_source)
    assert {item.seeker_kind for item in all_kinds.profiles} == SEEKER_KINDS
    assert all_kinds.profile("gtw.munition.fixture.missile.electro_optical").allowed_height_layers == ("upper",)

    bad = deepcopy(passive_source)
    bad["launch_support"] = "optional_fire_control"
    require_contract_error(
        "missile_guidance.passive_support",
        lambda: MissileGuidanceProfile.parse(bad, "$.profile"),
    )
    bad = deepcopy(active_source)
    bad["launch_support"] = "continuous_illumination"
    require_contract_error(
        "missile_guidance.independent_support",
        lambda: MissileGuidanceProfile.parse(bad, "$.profile"),
    )
    bad = deepcopy(electro_optical_source)
    bad["allowed_height_layers"] = ["upper", "cloud"]
    require_contract_error(
        "missile_guidance.electro_optical_layer",
        lambda: MissileGuidanceProfile.parse(bad, "$.profile"),
    )
    bad = deepcopy(anti_radiation_source)
    bad["target_loss_behavior"] = "self_destruct"
    bad["target_memory_s"] = 0.0
    require_contract_error(
        "missile_guidance.anti_radiation_loss",
        lambda: MissileGuidanceProfile.parse(bad, "$.profile"),
    )
    bad = deepcopy(active_source)
    bad["target_loss_behavior"] = "self_destruct"
    bad["target_memory_s"] = 1.0
    require_contract_error(
        "missile_guidance.unused_memory",
        lambda: MissileGuidanceProfile.parse(bad, "$.profile"),
    )
    duplicate = deepcopy(fixture_source)
    duplicate["profiles"] = [active_source, deepcopy(active_source)]
    require_contract_error(
        "missile_guidance.duplicate_munition",
        lambda: MissileGuidanceProfileCatalog.parse(duplicate),
    )

    guidance_state = initialize_missile_guidance_state(
        fixture_catalog,
        projectile_id="projectile.fixture.stage_i10.guided",
        munition_id="gtw.munition.fixture.76mm.standard",
        source_ship_id="ship.fixture.shooter",
        intended_target_ship_id="ship.fixture.target",
        launch_time_s=0.0,
    )
    assert guidance_state is not None
    assert guidance_state.phase == "inertial"
    assert guidance_state.profile_catalog == fixture_catalog.reference
    validate_missile_guidance_state(guidance_state, fixture_catalog)
    serialized_state = canonical_json(guidance_state)
    restored_state = MissileGuidanceState.parse(json.loads(serialized_state))
    assert canonical_json(restored_state) == serialized_state
    require_contract_error(
        "missile_guidance.profile_catalog_mismatch",
        lambda: validate_missile_guidance_state(
            guidance_state,
            replace(fixture_catalog, version=2),
        ),
    )
    invalid_phase = guidance_state.to_dict()
    invalid_phase["phase"] = "tracking"
    require_contract_error(
        "missile_guidance.phase_state",
        lambda: MissileGuidanceState.parse(invalid_phase),
    )

    projectile_catalog = load_projectile_profile_catalog(PROJECTILE_CATALOG)
    timing_catalog = load_weapon_timing_profile_catalog(TIMING_CATALOG)
    chain = build_chain("conventional_crewed")
    event = fire_event(chain, timing_catalog)
    guided_spawn = spawn_projectile_from_weapon_event(
        chain.snapshot,
        event,
        initialize_projectile_world(projectile_catalog),
        projectile_catalog,
        ShipPose2D(0.0, (0.0, 0.0), 0.0, (0.0, 0.0), 0.0),
        ProjectileSpawnRequest(
            "projectile.fixture.stage_i10.guided",
            "ship.fixture.shooter",
            "ship.fixture.target",
            0,
            (0.0, 1.0),
        ),
        guidance_catalog=fixture_catalog,
    )
    assert guided_spawn.projectile.guidance_state == guidance_state
    serialized_world = canonical_json(guided_spawn.resulting_world)
    restored_world = ProjectileWorldState.parse(json.loads(serialized_world))
    assert canonical_json(restored_world) == serialized_world

    unguided_spawn = spawn_projectile_from_weapon_event(
        chain.snapshot,
        event,
        initialize_projectile_world(projectile_catalog),
        projectile_catalog,
        ShipPose2D(0.0, (0.0, 0.0), 0.0, (0.0, 0.0), 0.0),
        ProjectileSpawnRequest(
            "projectile.fixture.stage_i10.legacy_unguided",
            "ship.fixture.shooter",
            "ship.fixture.target",
            0,
            (0.0, 1.0),
        ),
    )
    assert unguided_spawn.projectile.guidance_state is None
    assert "guidance_state" not in unguided_spawn.projectile.to_dict()

    registry = load_material_registry((STRUCTURE_CATALOG, ARMOR_CATALOG))
    bindings, scene, directive = scene_fixture(chain, timing_catalog, projectile_catalog)
    scene_step = advance_tactical_scene_step(
        scene,
        bindings,
        timing_catalog,
        projectile_catalog,
        registry,
        guidance_catalog=fixture_catalog,
        guidance_inputs=(
            MissileGuidanceRuntimeInput(
                directive.projectile_id,
                True,
                True,
                True,
            ),
        ),
        launch_directives=(directive,),
    )
    assert len(scene_step.spawned_projectiles) == 1
    assert scene_step.spawned_projectiles[0].guidance_state is not None
    assert scene_step.resulting_scene.projectile_world.projectiles[0].guidance_state is not None

    mismatched_world = json.loads(serialized_world)
    mismatched_world["projectiles"][0]["guidance_state"]["projectile_id"] = "projectile.fixture.other"
    require_contract_error(
        "projectile_world.guidance_binding_mismatch",
        lambda: ProjectileWorldState.parse(mismatched_world),
    )

    report = {
        "catalog": {
            "reference": fixture_catalog.reference.to_dict(),
            "sha256": fixture_catalog.source_sha256,
        },
        "guided_projectile_state": guidance_state.to_dict(),
        "interface": MISSILE_GUIDANCE_STATE_INTERFACE_ID,
        "legacy_unguided_shape_preserved": "guidance_state" not in unguided_spawn.projectile.to_dict(),
        "policy": MISSILE_GUIDANCE_INITIALIZATION_POLICY_ID,
        "profile_schema": MISSILE_GUIDANCE_SCHEMA_ID,
        "scene_spawn_guidance_persisted": scene_step.resulting_scene.projectile_world.projectiles[0].guidance_state is not None,
        "source_world_sha256": canonical_sha256(initialize_projectile_world(projectile_catalog)),
        "status": "PASS",
        "supported_seeker_kinds": sorted(SEEKER_KINDS),
        "tested_error_codes": [
            "missile_guidance.anti_radiation_loss",
            "missile_guidance.duplicate_munition",
            "missile_guidance.electro_optical_layer",
            "missile_guidance.independent_support",
            "missile_guidance.passive_support",
            "missile_guidance.phase_state",
            "missile_guidance.profile_catalog_mismatch",
            "missile_guidance.unused_memory",
            "projectile_world.guidance_binding_mismatch",
        ],
    }
    REPORT_PATH.write_text(canonical_json(report), encoding="utf-8")
    assert json.loads(REPORT_PATH.read_text(encoding="utf-8"))["status"] == "PASS"


if __name__ == "__main__":
    main()
