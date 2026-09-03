"""d2b.2 全量资源链、严格重载、已知来源及旧权威隔离回归。"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from benchmarks.t0.metadata import file_sha256
from 高天荒野T0b2d2a推进资源与控制桥测试 import (
    migrated_cases, test_existing_authority_isolation,
)
from 高天荒野舰艇数据契约 import (
    ContractError, ResourceReference, canonical_sha256,
)
import 高天荒野舰艇推进场景构建器 as builder
from 高天荒野舰艇推进场景构建器 import (
    build_known_directional_scene, load_known_directional_scene_bundle,
    validate_known_directional_scene_bundle,
)
from 高天荒野舰艇运行时参数编译器 import (
    RUNTIME_CACHE_VALIDATION_STRICT, compile_runtime_ship_parameters,
)
from 高天荒野舰艇持续毁伤 import continuous_damage_automatic_events
from 高天荒野舰艇战术机动求解器 import build_tactical_ship_static_model
from 高天荒野舰艇战术弹丸世界 import compile_projectile_target_geometry
from 高天荒野舰艇定向推进控制桥 import validate_directional_binding
from 高天荒野舰艇推进通道合同 import DIRECTIONAL_CHANNELS, DIRECTIONAL_SCENE_INTERFACE_ID
from 高天荒野舰艇统一战术场景 import (
    TacticalSceneState, advance_tactical_scene_step, prepare_tactical_scene_bindings,
)

ROOT = Path(__file__).resolve().parent
REPORT_PATH = ROOT / "舰艇数据/报告/阶段T0b2d2b2完整推进资源链接口.v1.json"
SCHEMA_PATH = ROOT / "舰艇数据/模式/高天荒野舰艇定向场景资源包契约.v1alpha1.schema.json"


def rejected(action, code=None):
    try:
        action()
    except ContractError as error:
        if code is not None:
            assert error.code == code, str(error)
    else:
        raise AssertionError("非法来源或被篡改资源未拒绝")


def assert_same(a, b):
    assert canonical_sha256(a) == canonical_sha256(b)


def check_lineage(scene_id, old, source, result):
    assert result.scene_id == scene_id
    assert result.source_scene_sha256 == canonical_sha256(source)
    assert result.scene.to_dict()["interface"] == DIRECTIONAL_SCENE_INTERFACE_ID
    assert result.scene.fixed_step_index == 0 and result.scene.tactical_time_s == 0
    assert_same(result.scene.projectile_world, source.projectile_world)
    assert result.scene.engagement_state == source.engagement_state
    assert result.scene.propulsion_safety_profile == source.propulsion_safety_profile
    assert result.scene.propulsion_safety_profile_sha256 == source.propulsion_safety_profile_sha256
    assert [s.ship_id for s in result.scene.ships] == [s.ship_id for s in source.ships]
    profiles = {p.profile_key: p for p in result.profiles}
    originals = {b.ship_id: b for b in old.bindings}
    channels = Counter()
    for ship, previous, resources in zip(result.scene.ships, source.ships, result.ships):
        binding = resources.binding
        original = originals[ship.ship_id]
        profile = profiles[resources.profile_key]
        snapshot = binding.snapshot
        plan = snapshot.outfit.normalized_plan
        assert profile.catalog.version == 3 and plan.version == 2
        assert snapshot is profile.snapshot and snapshot.version == 2
        assert snapshot.source_sha256 != original.snapshot.source_sha256
        assert snapshot.outfit.module_catalog_source_sha256 == canonical_sha256(profile.catalog)
        assert snapshot.hull == original.snapshot.hull
        assert snapshot.outfit.design_mass_kg == original.snapshot.outfit.design_mass_kg
        assert snapshot.outfit.design_inertia_kg_m2 == original.snapshot.outfit.design_inertia_kg_m2
        before_plan = original.snapshot.outfit.normalized_plan.to_dict()
        after_plan = plan.to_dict()
        after_plan["version"] = before_plan["version"]
        for before, after in zip(before_plan["modules"], after_plan["modules"]):
            if after["prototype"]["version"] == 3:
                after["prototype"]["version"] = before["prototype"]["version"]
        assert_same(after_plan, before_plan)
        assert binding.sortie.configuration.outfit_plan == ResourceReference(plan.id, 2)
        assert binding.sortie.configuration.version == original.sortie.configuration.version + 1
        before_config = original.sortie.configuration.to_dict()
        after_config = binding.sortie.configuration.to_dict()
        for key in ("version", "outfit_plan"):
            after_config[key] = before_config[key]
        assert_same(after_config, before_config)
        instance = ship.combat_state.instance
        before_instance = previous.combat_state.instance.to_dict()
        after_instance = instance.to_dict()
        for key in ("version", "outfit_plan", "derived_ship_snapshot_sha256",
                    "sortie_configuration", "sortie_configuration_sha256", "design_state"):
            before_instance.pop(key, None)
            after_instance.pop(key, None)
        assert_same(after_instance, before_instance)
        design = instance.design_state
        assert design is not None and design.revision == 1
        assert design.construction_hull_blueprint == snapshot.hull.normalized_blueprint
        assert design.current_outfit_plan == plan
        assert design.current_outfit_plan_sha256 == snapshot.outfit.source_sha256
        assert design.current_derived_ship_snapshot_sha256 == snapshot.source_sha256
        assert instance.derived_ship_snapshot_sha256 == ship.derived_snapshot_sha256 == snapshot.source_sha256
        assert instance.sortie_configuration_sha256 == ship.sortie_configuration_sha256 == binding.sortie.source_sha256
        assert instance.outfit_plan == binding.sortie.configuration.outfit_plan
        assert instance.sortie_configuration == ResourceReference(binding.sortie.configuration.id, 2)
        assert ship.motion_state == previous.motion_state
        assert ship.lifecycle_state == previous.lifecycle_state
        assert ship.combat_state.armor_edges == previous.combat_state.armor_edges
        assert ship.propulsion_state.engines == previous.propulsion_state.engines
        assert all(e.phase == "ready" and e.actual_output_percent == 0 for e in ship.propulsion_state.engines)
        assert {e.actuator_instance_id for e in ship.propulsion_state.engines} == {
            a.actuator_instance_id for a in resources.actuators}
        assert resources.missing_translation_channels == (
            "translation.reverse", "translation.left", "translation.right")
        for actuator in resources.actuators:
            validate_directional_binding(actuator, snapshot.outfit, profile.catalog)
            channels.update(actuator.command_channels)
        assert binding is not original and binding.runtime_cache is not original.runtime_cache
        assert binding.validated_snapshot_sha256 == snapshot.source_sha256
        assert binding.static_tactical_model == build_tactical_ship_static_model(snapshot)
        assert binding.projectile_target_geometry == compile_projectile_target_geometry(snapshot)
        events = continuous_damage_automatic_events(instance)
        cached = binding.runtime_cache.resolve(snapshot, binding.sortie, instance,
            active_automatic_events=events, validation_mode=RUNTIME_CACHE_VALIDATION_STRICT)
        assert cached.cache_hit
        assert_same(cached.runtime, compile_runtime_ship_parameters(
            snapshot, binding.sortie, instance, active_automatic_events=events))
    rejected(lambda: advance_tactical_scene_step(result.scene, result.bindings,
        old.timing_catalog, old.projectile_catalog, old.material_registry),
        "tactical_scene.propulsion_unwired")
    return channels


def payload_mutations():
    # 每个场景都覆盖目录、舾装、派生量、出航、内嵌设计、实例、绑定和执行器。
    return (
        lambda x: x["profiles"][0]["module_catalog"].update(version=2),
        lambda x: x["profiles"][1]["module_catalog"].update(name="tampered"),
        lambda x: x["profiles"][2]["module_catalog"].update(version=True),
        lambda x: x["profiles"][0]["outfit_plan"]["modules"][0]["prototype"].update(version=99),
        lambda x: x["profiles"][0]["compiled_outfit"].update(source_sha256="0" * 64),
        lambda x: x["profiles"][0]["derived_snapshot"]["design"].update(mass_kg=0),
        lambda x: x["ships"][0]["sortie"]["normalized_configuration"]["outfit_plan"].update(version=1),
        lambda x: x["scene"]["ships"][0]["combat_state"]["instance"]["design_state"].update(current_outfit_plan_sha256="0" * 64),
        lambda x: x["scene"]["ships"][0]["combat_state"]["instance"]["design_state"]["current_outfit_plan"].update(name="tampered"),
        lambda x: x["scene"]["ships"][0]["combat_state"]["instance"].update(sortie_configuration_sha256="0" * 64),
        lambda x: x["scene"]["ships"][0].update(derived_snapshot_sha256="0" * 64),
        lambda x: x["ships"][0].update(fleet_id="fleet.unknown"),
        lambda x: x["ships"][0]["cache_lineage"].update(runtime_revision_sha256="0" * 64),
        lambda x: x["ships"][0]["actuator_bindings"][0].update(module_catalog_sha256="0" * 64),
    )


def check_source_and_memory_rejection(scene_id, old, source, result):
    construct = lambda state, bindings: build_known_directional_scene(ROOT, scene_id, state, bindings)
    rejected(lambda: build_known_directional_scene(ROOT, "unknown", source, old.bindings))
    rejected(lambda: build_known_directional_scene(ROOT, [], source, old.bindings))
    rejected(lambda: construct(replace(source, fixed_step_index=1), old.bindings))
    rejected(lambda: construct(result.scene, old.bindings))
    rejected(lambda: construct(replace(source, ships=source.ships[:-1]), old.bindings))
    rejected(lambda: construct(source, old.bindings[:-1]))
    rejected(lambda: construct(source, old.bindings + (old.bindings[0],)))
    first = old.bindings[0]
    for bad in (
        replace(first, side_id="side.unknown"),
        replace(first, active_automatic_events=("ship.weapon_fire_requested",)),
        replace(first, sortie=replace(first.sortie, current_mass_kg=0)),
        replace(first, sortie=replace(first.sortie, configuration=replace(first.sortie.configuration, fuel_units=1))),
        replace(first, snapshot=replace(first.snapshot, outfit=replace(first.snapshot.outfit, design_mass_kg=0))),
    ):
        rejected(lambda: construct(source, (bad,) + old.bindings[1:]))
    module_loader = builder.load_module_prototype_catalog
    with patch.object(builder, "load_module_prototype_catalog", side_effect=lambda path: replace(module_loader(path), name="tampered")):
        rejected(lambda: construct(source, old.bindings))
    outfit_loader = builder.load_outfit_plan
    with patch.object(builder, "load_outfit_plan", side_effect=lambda path: replace(outfit_loader(path), name="tampered")):
        rejected(lambda: construct(source, old.bindings))
    validate = lambda value: validate_known_directional_scene_bundle(
        value, root=ROOT, source_scene=source, source_bindings=old.bindings)
    validate(result)
    for field in ("_static_tactical_model", "_projectile_target_geometry", "_runtime_cache"):
        poisoned = replace(result.ships[0].binding)
        # 复制有效缓存后仅换入同舰旧资源缓存。验证必须拒绝，不能静默洗成新缓存。
        for attr in ("_validated_snapshot_sha256", "_static_tactical_model", "_projectile_target_geometry", "_runtime_cache"):
            object.__setattr__(poisoned, attr, getattr(result.ships[0].binding, attr))
        object.__setattr__(poisoned, field, getattr(first, field))
        rejected(lambda: validate(replace(result, ships=(replace(result.ships[0], binding=poisoned),) + result.ships[1:])))
    # 正式准备入口不沿用输入缓存，即使调用者传入旧缓存也会构造新的严格来源缓存。
    prepared = prepare_tactical_scene_bindings(result.scene, result.bindings)
    assert all(a.runtime_cache is not b.runtime_cache for a, b in zip(prepared, result.bindings))
    # 来源 revision 正确但实际缓存内容被污染也不得通过。
    poisoned = prepared[0]
    for events, runtime in tuple(poisoned.runtime_cache._entries.items()):
        wrong = replace(runtime, _core=replace(runtime.stable_core, current_mass_kg=0))
        poisoned.runtime_cache._entries[events] = wrong
        poisoned.runtime_cache._view_history[events] = [wrong]
    rejected(lambda: validate(replace(result, ships=(replace(result.ships[0], binding=poisoned),) + result.ships[1:])))
    return 18


def check_cold_imports():
    for prefix in ("", "import 高天荒野舰艇统一战术场景; "):
        subprocess.run([sys.executable, "-X", "utf8", "-c", prefix +
            "import sys; import 高天荒野舰艇推进场景构建器; "
            "assert not any(n.startswith('benchmarks') or n.endswith('测试') for n in sys.modules)"],
            cwd=ROOT, check=True)


def check_schema(sample):
    schemas = [json.loads(p.read_text(encoding="utf-8")) for p in SCHEMA_PATH.parent.glob("*.schema.json")]
    by_id = {s["$id"]: s for s in schemas}
    schema = by_id[builder.DIRECTIONAL_RESOURCE_BUNDLE_INTERFACE_ID]
    assert set(schema["properties"]) == set(sample) == set(schema["required"])
    assert schema["properties"]["interface"]["const"] == schema["$id"]

    def walk(value):
        if isinstance(value, dict):
            if "$ref" in value:
                base, _, fragment = value["$ref"].partition("#")
                target = by_id[base] if base else schema
                for part in fragment.split("/")[1:]:
                    target = target[part.replace("~1", "/").replace("~0", "~")]
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(schema)


def collect_evidence():
    check_cold_imports()
    scene_hashes = {}
    bundle_hashes = {}
    channels = Counter({channel: 0 for channel in DIRECTIONAL_CHANNELS})
    ship_count = actuator_count = negatives = 0
    for scene_id, old, source in migrated_cases():
        source_hash = canonical_sha256(source)
        cache_stats = [b.runtime_cache.stats() for b in old.bindings]
        runs = [build_known_directional_scene(ROOT, scene_id, source, old.bindings) for _ in range(3)]
        assert len({canonical_sha256(run) for run in runs}) == 1
        result = runs[0]
        channels.update(check_lineage(scene_id, old, source, result))
        serialized = json.loads(json.dumps(result.to_dict(), ensure_ascii=False))
        restored = load_known_directional_scene_bundle(serialized, root=ROOT,
            source_scene=source, source_bindings=old.bindings)
        assert_same(restored, result)
        assert_same(TacticalSceneState.parse(result.scene.to_dict()), result.scene)
        for mutation in payload_mutations():
            changed = deepcopy(serialized)
            mutation(changed)
            rejected(lambda: load_known_directional_scene_bundle(changed, root=ROOT,
                source_scene=source, source_bindings=old.bindings))
            negatives += 1
        assert canonical_sha256(source) == source_hash
        assert [b.runtime_cache.stats() for b in old.bindings] == cache_stats
        scene_hashes[scene_id] = canonical_sha256(result.scene)
        bundle_hashes[scene_id] = canonical_sha256(result)
        ship_count += len(result.ships)
        actuator_count += sum(len(s.actuators) for s in result.ships)
    scene_id, old, source = migrated_cases()[0]
    result = build_known_directional_scene(ROOT, scene_id, source, old.bindings)
    context_cases = check_source_and_memory_rejection(scene_id, old, source, result)
    for mutation in (
        lambda x: x.update(interface="unknown"), lambda x: x.update(policy="unknown"),
        lambda x: x.update(migration_id="unknown"), lambda x: x.update(execution_status="ready"),
        lambda x: x.update(extra=1), lambda x: x.pop("profiles"),
        lambda x: x["ships"].reverse(), lambda x: x["ships"].pop(),
        lambda x: x["ships"][0].update(extra=1),
        lambda x: x["ships"][0]["actuator_bindings"][0].update(startup_steps=60.0),
        lambda x: x["scene"]["ships"][0]["propulsion_state"]["engines"][0].update(actual_output_percent=5),
        lambda x: x.update(source_scene_sha256="0" * 64),
    ):
        changed = deepcopy(result.to_dict())
        mutation(changed)
        rejected(lambda: load_known_directional_scene_bundle(changed, root=ROOT,
            source_scene=source, source_bindings=old.bindings))
        negatives += 1
    assert ship_count == 224 and actuator_count == 1224
    assert dict(channels) == dict(zip(DIRECTIONAL_CHANNELS, (328, 0, 0, 0, 448, 448)))
    check_schema(result.to_dict())
    return {
        "profiles": 3, "scenes": 12, "ships": ship_count, "actuators": actuator_count,
        "binding_channels": dict(channels), "deterministic_rebuilds": 3,
        "strict_scene_and_bundle_roundtrips": 12, "payload_negative_cases": negatives,
        "source_and_memory_negative_cases": context_cases, "cold_import_orders": 2,
        "schema_boundary_and_local_refs": 1,
        "source_objects_and_caches_unchanged": True,
        "scene_hashes": scene_hashes, "resource_bundle_hashes": bundle_hashes,
        "unwired_advance_rejections": 12, "new_mechanics_steps_advanced": 0,
        "isolation": test_existing_authority_isolation(),
    }


def main():
    evidence = collect_evidence()
    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    assert report["evidence"] == evidence
    assert report["status"] == "PASS"
    assert report["next_slice"] == "T0b.2d2b.3_actual_output_aggregation_and_integration"
    for relative, expected in report["implementation_hashes"].items():
        assert expected == file_sha256(ROOT / relative), relative
    print(json.dumps({"status": "PASS", "evidence": evidence}, ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
