"""T0b.2d2a 可排程资源、离散控制、精确绑定与新步结果合同测试。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from functools import lru_cache
import json
from pathlib import Path
from typing import Callable

from benchmarks.t0.contracts import load_benchmark_plan
from benchmarks.t0.diagnostics import (
    load_authority_step_golden,
    verify_authority_step_golden,
)
from benchmarks.t0.metadata import file_sha256
from benchmarks.t0.scenario import (
    T0ScenarioBundle,
    build_scenario,
    controls_for_step,
)
from 高天荒野舰艇阶段F三舰集成测试 import (
    ARMOR_CATALOG,
    BASE_MODULE_CATALOG,
    COATING_CATALOG,
    COMBAT_MODULE_CATALOG,
    SHIP_PATHS,
    STRUCTURE_CATALOG,
    UNMANNED_MODULE_CATALOG,
)
from 高天荒野舰艇数据契约 import (
    MODULE_CATALOG_V2_SCHEMA_ID,
    MODULE_CATALOG_V3_SCHEMA_ID,
    ContractError,
    ModulePrototypeCatalog,
    OutfitPlanInput,
    canonical_sha256,
    load_hull_blueprint,
    load_hull_coating_catalog,
    load_json,
    load_material_registry,
    load_module_prototype_catalog,
    load_outfit_plan,
)
from 高天荒野舰艇无界面船壳编译器 import compile_hull
from 高天荒野舰艇无界面舾装编译器 import CompiledOutfit, compile_outfit
from 高天荒野舰艇战术机动求解器 import TacticalControlInput, Vec2
from 高天荒野舰艇推进安全判定器 import load_propulsion_safety_profile
from 高天荒野舰艇推进资源与控制桥 import (
    AUTOMATIC_BRAKE_MAIN_ENGINE_NOTCH,
    AUTOMATIC_BRAKE_MANEUVER_TARGET_PERCENT,
    KNOWN_OUTFIT_V1_TO_D2A_MIGRATIONS,
    KNOWN_SCENE_CATALOG_V2_TO_V3_MIGRATIONS,
    KNOWN_T0_CONTINUOUS_CONTROL_MIGRATIONS,
    PROPULSION_ACTUATOR_BINDING_INTERFACE_ID,
    PROPULSION_CONTROL_INTERFACE_ID,
    SCENE_PROFILE_KEYS,
    TACTICAL_PROPULSION_STEP_RESULT_INTERFACE_ID,
    TACTICAL_PROPULSION_STEP_RESULT_POLICY_ID,
    DirectionPropulsionCommand,
    TacticalPropulsionControlInput,
    TacticalPropulsionStepResolutionEnvelope,
    TacticalScenePropulsionEvent,
    automatic_brake_control,
    bind_compiled_outfit_propulsion,
    build_propulsion_step_resolution_envelope,
    compose_known_scene_catalog_v2,
    migrate_known_scene_catalog_v2_to_v3,
    migrate_known_scene_outfit_v1_to_d2a,
    migrate_known_t0_continuous_control,
)
from 高天荒野舰艇推进状态合同 import PropulsionStateEvent
from 高天荒野舰艇统一战术场景 import (
    TACTICAL_PROPULSION_SCENE_INTERFACE_ID,
    TacticalSceneState,
    TacticalSceneStepResolution,
    advance_tactical_scene_step,
    migrate_known_tactical_scene_propulsion_v2_to_d1_v3,
    migrate_known_tactical_scene_v1_to_propulsion_v2,
)


ROOT = Path(__file__).resolve().parent
PLAN_PATH = ROOT / "contracts" / "web_bridge" / "t0-benchmark-plan.v1.json"
GOLDEN_PATH = ROOT / "contracts" / "web_bridge" / "t0-authority-step-golden.v1.json"
PROFILE_PATH = ROOT / "舰艇数据" / "标定" / "T0推进安全技术替身配置.v1.json"
MODULE_V3_SCHEMA_PATH = (
    ROOT / "舰艇数据" / "模式" / "高天荒野舰艇模块目录数据契约.v3.schema.json"
)
CONTROL_SCHEMA_PATH = (
    ROOT / "舰艇数据" / "模式" / "高天荒野舰艇推进离散控制契约.v1alpha1.schema.json"
)
BINDING_SCHEMA_PATH = (
    ROOT / "舰艇数据" / "模式" / "高天荒野舰艇推进执行器绑定契约.v1alpha1.schema.json"
)
SCENE_EVENT_SCHEMA_PATH = (
    ROOT / "舰艇数据" / "模式" / "高天荒野舰艇场景推进事件契约.v1alpha1.schema.json"
)
STEP_RESULT_SCHEMA_PATH = (
    ROOT / "舰艇数据" / "模式" / "高天荒野舰艇场景单步推进结果契约.v2alpha1.schema.json"
)
REPORT_PATH = ROOT / "舰艇数据" / "报告" / "阶段T0b2d2a推进资源与控制桥接口.v1.json"

_COMPONENT_IDS = {
    "minimum_legal": ("gtw.module_catalog.fixture.minimum",),
    "conventional_crewed": (
        "gtw.module_catalog.fixture.minimum",
        "gtw.module_catalog.fixture.combat_system",
    ),
    "unmanned_flagship": (
        "gtw.module_catalog.fixture.minimum",
        "gtw.module_catalog.fixture.combat_system",
        "gtw.module_catalog.fixture.stage_f_unmanned",
    ),
}


def require_contract_error(code: str, action: Callable[[], object]) -> None:
    try:
        action()
    except ContractError as error:
        if error.code != code:
            raise AssertionError(f"预期错误 {code}，实际为 {error.code}: {error}") from error
    else:
        raise AssertionError(f"预期抛出 {code}")


@lru_cache(maxsize=1)
def _source_catalogs() -> dict[str, ModulePrototypeCatalog]:
    catalogs = tuple(
        load_module_prototype_catalog(path)
        for path in (
            BASE_MODULE_CATALOG,
            COMBAT_MODULE_CATALOG,
            UNMANNED_MODULE_CATALOG,
        )
    )
    return {item.id: item for item in catalogs}


@lru_cache(maxsize=3)
def d2a_profile_resources(
    profile_key: str,
) -> tuple[ModulePrototypeCatalog, OutfitPlanInput, CompiledOutfit]:
    sources = _source_catalogs()
    v2 = compose_known_scene_catalog_v2(
        profile_key,
        tuple(sources[item] for item in _COMPONENT_IDS[profile_key]),
    )
    v3 = migrate_known_scene_catalog_v2_to_v3(v2)
    outfit = migrate_known_scene_outfit_v1_to_d2a(
        load_outfit_plan(SHIP_PATHS[profile_key]["outfit"])
    )
    registry = load_material_registry((STRUCTURE_CATALOG, ARMOR_CATALOG))
    hull = compile_hull(
        load_hull_blueprint(SHIP_PATHS[profile_key]["hull"]),
        registry,
    )
    compiled = compile_outfit(
        outfit,
        hull,
        v3,
        load_hull_coating_catalog(COATING_CATALOG),
    )
    return v3, outfit, compiled


@lru_cache(maxsize=1)
def migrated_cases() -> tuple[
    tuple[str, T0ScenarioBundle, TacticalSceneState], ...
]:
    plan = load_benchmark_plan(PLAN_PATH)
    safety_profile = load_propulsion_safety_profile(PROFILE_PATH)
    cases: list[tuple[str, T0ScenarioBundle, TacticalSceneState]] = []
    for profile in plan.profiles:
        for load_stage in plan.load_stages:
            scene_id = f"{profile.id}.{load_stage}"
            bundle = build_scenario(
                ROOT,
                plan,
                profile.id,
                load_stage,
                1,
            )
            c2b = migrate_known_tactical_scene_v1_to_propulsion_v2(
                scene_id,
                bundle.initial_scene,
                bundle.bindings,
                safety_profile,
            )
            d1 = migrate_known_tactical_scene_propulsion_v2_to_d1_v3(
                scene_id,
                c2b,
            )
            cases.append((scene_id, bundle, d1))
    return tuple(sorted(cases, key=lambda item: item[0]))


def test_schedulable_resource_migrations() -> dict[str, object]:
    assert load_json(MODULE_V3_SCHEMA_PATH)["$id"] == MODULE_CATALOG_V3_SCHEMA_ID
    specifications = {
        item.profile_key: item
        for item in KNOWN_SCENE_CATALOG_V2_TO_V3_MIGRATIONS
    }
    outfit_specifications = {
        item.profile_key: item for item in KNOWN_OUTFIT_V1_TO_D2A_MIGRATIONS
    }
    assert tuple(specifications) == SCENE_PROFILE_KEYS
    assert tuple(outfit_specifications) == SCENE_PROFILE_KEYS

    target_catalog_hashes: dict[str, str] = {}
    target_outfit_hashes: dict[str, str] = {}
    propulsion_modules = 0
    source_v2_hashes: dict[str, str] = {}
    sources = _source_catalogs()
    for profile_key in SCENE_PROFILE_KEYS:
        v2_runs = tuple(
            compose_known_scene_catalog_v2(
                profile_key,
                tuple(sources[item] for item in _COMPONENT_IDS[profile_key]),
            )
            for _ in range(3)
        )
        assert len({canonical_sha256(item) for item in v2_runs}) == 1
        v2 = v2_runs[0]
        assert v2.schema == MODULE_CATALOG_V2_SCHEMA_ID
        assert canonical_sha256(v2) == specifications[profile_key].source_sha256
        source_v2_hashes[profile_key] = canonical_sha256(v2)

        v3_runs = tuple(migrate_known_scene_catalog_v2_to_v3(v2) for _ in range(3))
        assert len({canonical_sha256(item) for item in v3_runs}) == 1
        v3 = v3_runs[0]
        assert v3.schema == MODULE_CATALOG_V3_SCHEMA_ID
        assert v3.version == 3
        assert ModulePrototypeCatalog.parse(v3.to_dict()) == v3
        target_catalog_hashes[profile_key] = canonical_sha256(v3)
        for module in v3.modules:
            if module.category not in {"main_engine", "maneuver_thruster"}:
                continue
            propulsion_modules += 1
            assert module.reference.version == 3
            assert module.capability.to_dict()["response_time_s"] == 1.0

        source_outfit = load_outfit_plan(SHIP_PATHS[profile_key]["outfit"])
        outfit_runs = tuple(
            migrate_known_scene_outfit_v1_to_d2a(source_outfit) for _ in range(3)
        )
        assert len({canonical_sha256(item) for item in outfit_runs}) == 1
        migrated_outfit = outfit_runs[0]
        assert migrated_outfit.version == 2
        propulsion_ids = set(
            outfit_specifications[profile_key].propulsion_module_ids
        )
        for source, target in zip(source_outfit.modules, migrated_outfit.modules):
            expected = 3 if source.prototype.id in propulsion_ids else source.prototype.version
            assert target.prototype.id == source.prototype.id
            assert target.prototype.version == expected
            assert target.placement == source.placement
        target_outfit_hashes[profile_key] = canonical_sha256(migrated_outfit)

    tampered_v2 = v2_runs[0].to_dict()
    tampered_v2["name"] += "·篡改"
    require_contract_error(
        "propulsion_bridge.catalog_migration_source_hash",
        lambda: migrate_known_scene_catalog_v2_to_v3(
            ModulePrototypeCatalog.parse(tampered_v2)
        ),
    )
    unknown_v2 = v2_runs[0].to_dict()
    unknown_v2["id"] = "gtw.module_catalog.fixture.unknown"
    require_contract_error(
        "propulsion_bridge.catalog_migration_unknown",
        lambda: migrate_known_scene_catalog_v2_to_v3(
            ModulePrototypeCatalog.parse(unknown_v2)
        ),
    )
    unschedulable_v3 = d2a_profile_resources("minimum_legal")[0].to_dict()
    next(
        item
        for item in unschedulable_v3["modules"]
        if item["category"] == "maneuver_thruster"
    )["capability"]["response_time_s"] = 0.2
    require_contract_error(
        "module.propulsion_response_unschedulable",
        lambda: ModulePrototypeCatalog.parse(unschedulable_v3),
    )
    tampered_outfit = load_outfit_plan(
        SHIP_PATHS["minimum_legal"]["outfit"]
    ).to_dict()
    tampered_outfit["name"] += "·篡改"
    require_contract_error(
        "propulsion_bridge.outfit_migration_source_hash",
        lambda: migrate_known_scene_outfit_v1_to_d2a(
            OutfitPlanInput.parse(tampered_outfit)
        ),
    )
    return {
        "catalog_types_migrated": len(target_catalog_hashes),
        "deterministic_replays_per_resource": 3,
        "outfit_types_migrated": len(target_outfit_hashes),
        "propulsion_modules_in_scene_catalogs": propulsion_modules,
        "source_v2_hashes": source_v2_hashes,
        "strict_negative_cases": 4,
        "target_catalog_hashes": target_catalog_hashes,
        "target_outfit_hashes": target_outfit_hashes,
    }


def test_exact_scene_actuator_bindings() -> dict[str, object]:
    assert load_json(BINDING_SCHEMA_PATH)["$id"] == (
        PROPULSION_ACTUATOR_BINDING_INTERFACE_ID
    )
    all_keys: set[tuple[str, str, str]] = set()
    category_counts = {"main_engine": 0, "maneuver_thruster": 0}
    channel_counts = {channel: 0 for channel in ("forward", "reverse", "left", "right")}
    startup_steps: set[int] = set()
    response_steps: set[int] = set()
    scene_counts: dict[str, int] = {}
    for scene_id, bundle, d1_scene in migrated_cases():
        d1_ship_by_id = {item.ship_id: item for item in d1_scene.ships}
        scene_count = 0
        for ship_id, profile_key in sorted(bundle.ship_fixture_by_id.items()):
            catalog, _outfit, compiled = d2a_profile_resources(profile_key)
            bindings = bind_compiled_outfit_propulsion(
                scene_id,
                ship_id,
                compiled,
                catalog,
            )
            state = d1_ship_by_id[ship_id].propulsion_state
            assert state is not None
            assert tuple(item.actuator_instance_id for item in bindings) == tuple(
                item.actuator_instance_id for item in state.engines
            )
            for binding in bindings:
                key = (
                    binding.scene_id,
                    binding.ship_id,
                    binding.actuator_instance_id,
                )
                assert key not in all_keys
                all_keys.add(key)
                category_counts[binding.actuator_category] += 1
                for channel in binding.command_channels:
                    channel_counts[channel] += 1
                startup_steps.add(binding.startup_steps)
                response_steps.add(binding.response_steps)
                assert binding.to_dict()["interface"] == (
                    PROPULSION_ACTUATOR_BINDING_INTERFACE_ID
                )
            scene_count += len(bindings)
        scene_counts[scene_id] = scene_count

    assert len(all_keys) == 1224
    assert category_counts == {"main_engine": 328, "maneuver_thruster": 896}
    assert channel_counts == {
        "forward": 328,
        "reverse": 0,
        "left": 448,
        "right": 448,
    }
    assert startup_steps == {0, 60}
    assert response_steps == {60}

    catalog, _outfit, compiled = d2a_profile_resources("minimum_legal")
    tampered = catalog.to_dict()
    tampered["name"] += "·篡改"
    require_contract_error(
        "propulsion_bridge.binding_catalog_mismatch",
        lambda: bind_compiled_outfit_propulsion(
            "scene.tampered",
            "ship.tampered",
            compiled,
            ModulePrototypeCatalog.parse(tampered),
        ),
    )
    return {
        "actuator_bindings": len(all_keys),
        "category_counts": category_counts,
        "channel_counts": channel_counts,
        "response_steps": sorted(response_steps),
        "scene_bindings": scene_counts,
        "startup_steps": sorted(startup_steps),
        "tampered_catalog_rejected": True,
    }


def test_discrete_control_contract_and_migration() -> dict[str, object]:
    assert load_json(CONTROL_SCHEMA_PATH)["$id"] == PROPULSION_CONTROL_INTERFACE_ID
    migrated_controls = 0
    source_migrations = {item.migration_id: 0 for item in KNOWN_T0_CONTINUOUS_CONTROL_MIGRATIONS}
    target_hashes: set[str] = set()
    for _scene_id, bundle, _d1_scene in migrated_cases():
        legacy_controls = controls_for_step(bundle, bundle.initial_scene)
        for legacy in legacy_controls.values():
            runs = tuple(migrate_known_t0_continuous_control(legacy) for _ in range(3))
            hashes = {canonical_sha256(item) for item in runs}
            assert len(hashes) == 1
            migrated = runs[0]
            assert TacticalPropulsionControlInput.parse(migrated.to_dict()) == migrated
            by_channel = {
                item.command_channel: item for item in migrated.direction_commands
            }
            assert by_channel["forward"].main_engine_notch == "dead_slow"
            assert by_channel["forward"].maneuver_target_percent == 10
            turning = "left" if legacy.wheel > 0.0 else "right"
            assert by_channel[turning].maneuver_target_percent == 5
            assert migrated.source_migration_id is not None
            source_migrations[migrated.source_migration_id] += 1
            target_hashes.update(hashes)
            migrated_controls += 1
    assert migrated_controls == 224
    assert set(source_migrations.values()) == {112}

    brake = automatic_brake_control(
        lateral_velocity_body_mps=3.0,
        longitudinal_velocity_body_mps=8.0,
    )
    brake_by_channel = {
        item.command_channel: item for item in brake.direction_commands
    }
    assert brake.automatic_brake
    assert brake_by_channel["reverse"].main_engine_notch == (
        AUTOMATIC_BRAKE_MAIN_ENGINE_NOTCH
    )
    assert brake_by_channel["reverse"].maneuver_target_percent == (
        AUTOMATIC_BRAKE_MANEUVER_TARGET_PERCENT
    )
    assert brake_by_channel["left"].maneuver_target_percent == (
        AUTOMATIC_BRAKE_MANEUVER_TARGET_PERCENT
    )
    assert TacticalPropulsionControlInput.parse(brake.to_dict()) == brake

    require_contract_error(
        "propulsion_control.legacy_migration_unknown",
        lambda: migrate_known_t0_continuous_control(
            TacticalControlInput(move_body=Vec2(0.0, 0.1), wheel=0.051)
        ),
    )
    wrong_policy = brake.to_dict()
    wrong_policy["automatic_brake_policy"] = "gaotian.propulsion-control/wrong"
    require_contract_error(
        "propulsion_control.policy",
        lambda: TacticalPropulsionControlInput.parse(wrong_policy),
    )
    reversed_commands = brake.to_dict()
    reversed_commands["direction_commands"] = list(
        reversed(reversed_commands["direction_commands"])
    )
    require_contract_error(
        "propulsion_control.invariant",
        lambda: TacticalPropulsionControlInput.parse(reversed_commands),
    )
    require_contract_error(
        "propulsion_control.brake_velocity",
        lambda: automatic_brake_control(
            lateral_velocity_body_mps=float("nan"),
            longitudinal_velocity_body_mps=0.0,
        ),
    )
    return {
        "automatic_brake_main_engine_notch": AUTOMATIC_BRAKE_MAIN_ENGINE_NOTCH,
        "automatic_brake_maneuver_target_percent": AUTOMATIC_BRAKE_MANEUVER_TARGET_PERCENT,
        "deterministic_replays_per_control": 3,
        "legacy_controls_migrated": migrated_controls,
        "named_source_counts": source_migrations,
        "strict_negative_cases": 4,
        "unique_target_controls": len(target_hashes),
    }


def test_step_result_and_propulsion_event_contract() -> dict[str, object]:
    assert load_json(SCENE_EVENT_SCHEMA_PATH)["$id"] == (
        "gaotian.tactical-scene-propulsion-event/v1alpha1"
    )
    assert load_json(STEP_RESULT_SCHEMA_PATH)["$id"] == (
        TACTICAL_PROPULSION_STEP_RESULT_INTERFACE_ID
    )
    scene_id, bundle, d1_scene = migrated_cases()[0]
    assert d1_scene.to_dict()["interface"] == TACTICAL_PROPULSION_SCENE_INTERFACE_ID
    base = TacticalSceneStepResolution(
        canonical_sha256(d1_scene),
        d1_scene,
        (),
        (),
        (),
        (),
        (),
        (),
        (),
    )
    selected_ships = d1_scene.ships[:2]
    wrapped_events = tuple(
        TacticalScenePropulsionEvent(
            ship.ship_id,
            PropulsionStateEvent(
                d1_scene.fixed_step_index,
                ship.propulsion_state.engines[0].actuator_instance_id,  # type: ignore[union-attr]
                None,
                "engine_start_requested",
                "off",
                "starting",
                None,
                None,
                (),
            ),
        )
        for ship in reversed(selected_ships)
    )
    envelope = build_propulsion_step_resolution_envelope(base, wrapped_events)
    assert tuple(item.sort_key for item in envelope.propulsion_events) == tuple(
        sorted(item.sort_key for item in wrapped_events)
    )
    value = envelope.to_dict()
    assert value["interface"] == TACTICAL_PROPULSION_STEP_RESULT_INTERFACE_ID
    assert value["policy"] == TACTICAL_PROPULSION_STEP_RESULT_POLICY_ID
    assert len(value["propulsion_events"]) == 2
    assert TacticalScenePropulsionEvent.parse(
        value["propulsion_events"][0],
        "$.propulsion_events[0]",
    ) == envelope.propulsion_events[0]

    try:
        TacticalPropulsionStepResolutionEnvelope(base, wrapped_events)
    except ValueError as error:
        assert "稳定排序" in str(error)
    else:
        raise AssertionError("未排序推进事件必须拒绝")
    old_base = replace(base, resulting_scene=bundle.initial_scene)
    require_contract_error(
        "propulsion_bridge.step_result_invariant",
        lambda: build_propulsion_step_resolution_envelope(old_base, ()),
    )
    unknown_ship_event = replace(
        envelope.propulsion_events[0],
        ship_id="ship.unknown",
    )
    require_contract_error(
        "propulsion_bridge.step_result_invariant",
        lambda: build_propulsion_step_resolution_envelope(
            base,
            (unknown_ship_event,),
        ),
    )
    require_contract_error(
        "tactical_scene.propulsion_unwired",
        lambda: advance_tactical_scene_step(
            d1_scene,
            bundle.bindings,
            bundle.timing_catalog,
            bundle.projectile_catalog,
            bundle.material_registry,
        ),
    )
    return {
        "event_sort_key": [
            "fixed_step_index",
            "ship_id",
            "actuator_instance_id",
            "event_kind_order",
        ],
        "new_mechanics_steps_advanced": 0,
        "propulsion_events_roundtrip": 2,
        "strict_negative_cases": 3,
        "unwired_advance_rejected": True,
    }


def test_existing_authority_isolation() -> dict[str, object]:
    expected = load_authority_step_golden(GOLDEN_PATH)
    actual = verify_authority_step_golden(
        ROOT,
        load_benchmark_plan(PLAN_PATH),
        GOLDEN_PATH,
    )
    assert actual == expected
    return {
        "authority_golden": "12_of_12_PASS",
        "legacy_catalog_schema": "gaotian.ship/v1alpha1",
        "new_mechanics_steps_advanced": 0,
        "official_performance_runs_executed": 0,
    }


def main() -> None:
    resource_evidence = test_schedulable_resource_migrations()
    binding_evidence = test_exact_scene_actuator_bindings()
    control_evidence = test_discrete_control_contract_and_migration()
    result_evidence = test_step_result_and_propulsion_event_contract()
    isolation_evidence = test_existing_authority_isolation()

    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    assert report["interface"] == "gaotian.stage-t0b2d2a-propulsion-resource-control-bridge/v1"
    assert report["status"] == "PASS"
    assert report["resource_evidence"] == resource_evidence
    assert report["binding_evidence"] == binding_evidence
    assert report["control_evidence"] == control_evidence
    assert report["result_evidence"] == result_evidence
    assert report["isolation_evidence"] == isolation_evidence
    assert report["authority_golden"] == "12_of_12_PASS"
    assert report["official_performance_runs_executed"] == 0
    assert report["next_slice"] == "T0b.2d2b_propulsion_mechanics_wiring"
    for relative_path in (
        "舰艇数据/模式/高天荒野舰艇模块目录数据契约.v3.schema.json",
        "舰艇数据/模式/高天荒野舰艇推进离散控制契约.v1alpha1.schema.json",
        "舰艇数据/模式/高天荒野舰艇推进执行器绑定契约.v1alpha1.schema.json",
        "舰艇数据/模式/高天荒野舰艇场景推进事件契约.v1alpha1.schema.json",
        "舰艇数据/模式/高天荒野舰艇场景单步推进结果契约.v2alpha1.schema.json",
        "高天荒野T0b2d2a推进资源与控制桥测试.py",
        "高天荒野T0b2推进响应与权威性能优化规划.md",
        "高天荒野Web客户端编辑器与战术验证实施计划.md",
        "高天荒野舰艇数据契约.py",
        "高天荒野舰艇推进时间内核.py",
        "高天荒野舰艇推进资源与控制桥.py",
    ):
        assert report["implementation_hashes"][relative_path] == file_sha256(
            ROOT / relative_path
        )

    print(
        json.dumps(
            {
                "actuator_bindings": binding_evidence["actuator_bindings"],
                "authority_golden": "12_of_12_PASS",
                "interface": "gaotian.stage-t0b2d2a-propulsion-resource-control-bridge-test/v1",
                "status": "PASS",
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
