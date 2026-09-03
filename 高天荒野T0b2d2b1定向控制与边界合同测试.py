"""d2b.1 合同准备：不接新力学，保留旧黄金与各版本读写。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys

from 高天荒野T0b2d2a推进资源与控制桥测试 import (
    d2a_profile_resources, migrated_cases, test_existing_authority_isolation,
)
from benchmarks.t0.scenario import controls_for_step
from benchmarks.t0.metadata import file_sha256
from 高天荒野舰艇数据契约 import ContractError, canonical_sha256, ResourceReference
from 高天荒野舰艇推进通道合同 import (
    TRANSLATION_CHANNELS, YAW_CHANNELS, DIRECTIONAL_CHANNELS,
    DIRECTIONAL_STATE_INTERFACE_ID, DIRECTIONAL_EVENT_INTERFACE_ID,
    DIRECTIONAL_SCENE_INTERFACE_ID, ChannelPropulsionCommand, DirectionalPropulsionGovernorState,
)
from 高天荒野舰艇定向推进控制桥 import (
    DirectionalPropulsionControlInput, DirectionalPropulsionActuatorBinding,
    directional_control, migrate_known_t0_control_to_directional, migrate_known_d2a_control_to_directional,
    automatic_linear_brake_control, validate_directional_control_transition, bind_directional_outfit_propulsion,
    validate_directional_binding, migrate_idle_d1_propulsion_state, IDLE_STATE_MIGRATION_ID,
    migrate_d2a_binding_to_directional, BINDING_MIGRATION_ID,
)
from 高天荒野舰艇推进资源与控制桥 import (
    DirectionPropulsionCommand, TacticalPropulsionControlInput, TacticalScenePropulsionEvent,
    automatic_brake_control, migrate_known_t0_continuous_control, _nearest_stage_percent, _nearest_telegraph_notch,
    bind_compiled_outfit_propulsion,
)
from 高天荒野舰艇推进状态合同 import (
    TacticalPropulsionState, PropulsionStateEvent, EngineRuntimeState, migrate_engine_runtime_state_from_module_mode,
)
from 高天荒野舰艇推进时间内核 import PropulsionTimeCommand, PropulsionTimeBoundaryResult
from 高天荒野舰艇场景推进结果 import (
    migrate_known_d1_scene_to_directional, migrate_channel_free_engine_event, ENGINE_EVENT_MIGRATION_ID,
    BoundaryScenePropulsionEvent, propulsion_time_interval, build_interval_propulsion_step_resolution,
    INTERVAL_STEP_RESULT_INTERFACE_ID,
    IntervalPropulsionStepResolution,
)
from 高天荒野舰艇统一战术场景 import TacticalSceneState, TacticalSceneStepResolution, advance_tactical_scene_step

ROOT = Path(__file__).resolve().parent
REPORT_PATH = ROOT / "舰艇数据/报告/阶段T0b2d2b1定向控制与边界合同接口.v1.json"
SCHEMA_FILES = (
    "高天荒野舰艇推进离散控制契约.v2alpha1.schema.json",
    "高天荒野舰艇推进执行器绑定契约.v2alpha1.schema.json",
    "高天荒野舰艇定向推进调节器契约.v2alpha1.schema.json",
    "高天荒野舰艇推进状态契约.v3alpha1.schema.json",
    "高天荒野舰艇统一战术场景状态契约.v4alpha1.schema.json",
    "高天荒野舰艇推进事件契约.v2alpha1.schema.json",
    "高天荒野舰艇场景推进事件契约.v2alpha1.schema.json",
    "高天荒野舰艇场景单步推进结果契约.v3alpha1.schema.json",
)


def rejected(action, code=None):
    try:
        action()
    except (ContractError, ValueError) as error:
        if code is not None:
            assert isinstance(error, ContractError) and error.code == code, str(error)
    else:
        raise AssertionError("非法输入没有被拒绝")


def check_controls():
    count = 0
    for _, bundle, _ in migrated_cases():
        for legacy in controls_for_step(bundle, bundle.initial_scene).values():
            old = migrate_known_t0_continuous_control(legacy)
            runs = [migrate_known_t0_control_to_directional(legacy) for _ in range(3)]
            assert len({canonical_sha256(x) for x in runs}) == 1
            control = runs[0]
            assert control == migrate_known_d2a_control_to_directional(old)
            assert DirectionalPropulsionControlInput.parse(control.to_dict()) == control
            assert TacticalPropulsionControlInput.parse(old.to_dict()) == old
            assert [x.requested_percent for x in control.channel_commands[:4]] == [2, 0, 0, 0]
            assert [x.requested_percent for x in control.channel_commands[4:]] == ([5, 0] if legacy.wheel > 0 else [0, 5])
            count += 1
    assert count == 224
    valid = directional_control()
    for bad in (False, True, 0.0, 5.0, 3, -5, 105, float("nan"), float("inf"), "5", None):
        rejected(lambda: DirectionPropulsionCommand("forward", "stop", bad))
        rejected(lambda: ChannelPropulsionCommand(YAW_CHANNELS[0], None, bad))
        rejected(lambda: PropulsionTimeCommand.maneuver_thruster(bad))
        value = valid.to_dict()
        value["channel_commands"][4]["target_output_percent"] = bad
        rejected(lambda: DirectionalPropulsionControlInput.parse(value))
    for bad in (True, float("nan"), float("inf"), -float("inf"), "1"):
        rejected(lambda: _nearest_stage_percent(bad))
        rejected(lambda: _nearest_telegraph_notch(bad))
    for change in (lambda x: x.update(interface="unknown"), lambda x: x.update(extra=0),
        lambda x: x.pop("automatic_brake"), lambda x: x.update(automatic_brake=1),
        lambda x: x.update(channel_commands=None), lambda x: x.update(source_migration_id="unknown"),
        lambda x: x["channel_commands"].reverse(), lambda x: x["channel_commands"].pop(),
        lambda x: x["channel_commands"].__setitem__(0, deepcopy(x["channel_commands"][1])),
        lambda x: x["channel_commands"][0].update(command_channel="left")):
        value = valid.to_dict()
        change(value)
        rejected(lambda: DirectionalPropulsionControlInput.parse(value))
    rejected(lambda: directional_control([ChannelPropulsionCommand.stop(TRANSLATION_CHANNELS[0])] * 2))
    for a, b in ((TRANSLATION_CHANNELS[0], TRANSLATION_CHANNELS[1]), (TRANSLATION_CHANNELS[2], TRANSLATION_CHANNELS[3])):
        rejected(lambda: directional_control([ChannelPropulsionCommand(a, "quarter", None), ChannelPropulsionCommand(b, "quarter", None)]))
    rejected(lambda: directional_control([ChannelPropulsionCommand(x, None, 5) for x in YAW_CHANNELS]))
    forward = directional_control([ChannelPropulsionCommand(TRANSLATION_CHANNELS[0], "full", None)])
    rejected(lambda: directional_control([ChannelPropulsionCommand(TRANSLATION_CHANNELS[0], "full", None)], automatic_brake=True))
    reverse = directional_control([ChannelPropulsionCommand(TRANSLATION_CHANNELS[1], "quarter", None)])
    actual = dict.fromkeys(DIRECTIONAL_CHANNELS, 0)
    validate_directional_control_transition(valid, forward, actual)
    rejected(lambda: validate_directional_control_transition(forward, reverse, actual), "propulsion_control.direction_switch_unwired")
    actual[TRANSLATION_CHANNELS[0]] = 5
    rejected(lambda: validate_directional_control_transition(valid, reverse, actual), "propulsion_control.direction_switch_unwired")
    rejected(lambda: migrate_known_d2a_control_to_directional(automatic_brake_control(lateral_velocity_body_mps=3, longitudinal_velocity_body_mps=0)))
    return count


def check_brakes_and_bindings():
    count = 0
    channels = dict.fromkeys(DIRECTIONAL_CHANNELS, 0)
    for scene_id, bundle, _ in migrated_cases():
        for ship_id, profile in bundle.ship_fixture_by_id.items():
            catalog, _, outfit = d2a_profile_resources(profile)
            bindings = bind_directional_outfit_propulsion(scene_id, ship_id, outfit, catalog)
            for binding in bindings:
                assert DirectionalPropulsionActuatorBinding.parse(binding.to_dict()) == binding
                for channel in binding.command_channels:
                    channels[channel] += 1
                count += 1
    assert count == 1224
    assert list(channels.values()) == [328, 0, 0, 0, 448, 448]
    catalog, _, outfit = d2a_profile_resources("minimum_legal")
    binding = bind_directional_outfit_propulsion("scene", "ship", outfit, catalog)[0]
    old_binding = bind_compiled_outfit_propulsion("scene", "ship", outfit, catalog)[0]
    assert migrate_d2a_binding_to_directional(BINDING_MIGRATION_ID, old_binding, outfit, catalog) == binding
    rejected(lambda: migrate_d2a_binding_to_directional("unknown", old_binding, outfit, catalog))
    rejected(lambda: migrate_d2a_binding_to_directional(BINDING_MIGRATION_ID, replace(old_binding, module_catalog_sha256="0" * 64), outfit, catalog))
    validate_directional_binding(binding, outfit, catalog)
    rejected(lambda: validate_directional_binding(replace(binding, module_catalog_sha256="0" * 64), outfit, catalog))
    rejected(lambda: validate_directional_binding(replace(binding, prototype=ResourceReference("gtw.unknown", 3)), outfit, catalog))
    for change in (lambda x: x.update(interface="unknown"), lambda x: x.update(command_channels=["left"]),
        lambda x: x.update(response_steps=True), lambda x: x.update(startup_steps=0.0),
        lambda x: x.update(module_catalog_sha256="bad"), lambda x: x.update(command_channels=x["command_channels"] * 2),
        lambda x: x.update(command_channels=[YAW_CHANNELS[0]] if x["actuator_category"] == "main_engine" else [TRANSLATION_CHANNELS[0]])):
        value = binding.to_dict()
        change(value)
        rejected(lambda: DirectionalPropulsionActuatorBinding.parse(value))
    for lateral, longitudinal in ((3, 0), (-3, 0), (0, 8), (0, -8), (3, 8), (0, 0)):
        result = automatic_linear_brake_control(lateral_velocity_body_mps=lateral, longitudinal_velocity_body_mps=longitudinal,
            available_translation_channels=TRANSLATION_CHANNELS)
        requested = {x.command_channel for x in result.control.channel_commands if x.requested_percent}
        expected = set()
        if lateral: expected.add(TRANSLATION_CHANNELS[2 if lateral > 0 else 3])
        if longitudinal: expected.add(TRANSLATION_CHANNELS[1 if longitudinal > 0 else 0])
        assert requested == expected and not result.unavailable_channels
        assert all(x.requested_percent == 0 for x in result.control.channel_commands[4:])
    missing = automatic_linear_brake_control(lateral_velocity_body_mps=3, longitudinal_velocity_body_mps=8,
        available_translation_channels=[TRANSLATION_CHANNELS[0]])
    assert missing.unavailable_channels == (TRANSLATION_CHANNELS[1], TRANSLATION_CHANNELS[2])
    assert not any(x.requested_percent for x in missing.control.channel_commands)
    rejected(lambda: automatic_linear_brake_control(lateral_velocity_body_mps=3, longitudinal_velocity_body_mps=0, available_translation_channels=YAW_CHANNELS))
    for value in (True, float("nan"), float("inf"), -float("inf"), "3"):
        rejected(lambda: automatic_linear_brake_control(lateral_velocity_body_mps=value, longitudinal_velocity_body_mps=0, available_translation_channels=TRANSLATION_CHANNELS))
    return channels


def check_state_migrations():
    hashes = {}
    for scene_id, bundle, d1 in migrated_cases():
        runs = [migrate_known_d1_scene_to_directional(scene_id, d1) for _ in range(3)]
        assert len({canonical_sha256(x) for x in runs}) == 1
        state = runs[0]
        hashes[scene_id] = canonical_sha256(state)
        assert state.to_dict()["interface"] == DIRECTIONAL_SCENE_INTERFACE_ID
        assert TacticalSceneState.parse(state.to_dict()) == state
        for old, new in zip(d1.ships, state.ships):
            assert old.propulsion_state.engines == new.propulsion_state.engines
            assert old.combat_state == new.combat_state and old.motion_state == new.motion_state
            assert new.derived_snapshot_sha256 == old.derived_snapshot_sha256  # 本切片不做 d2b.2。
            assert new.propulsion_state.interface_id == DIRECTIONAL_STATE_INTERFACE_ID
            assert len(new.propulsion_state.governors) == 6
        rejected(lambda: advance_tactical_scene_step(state, bundle.bindings, bundle.timing_catalog, bundle.projectile_catalog, bundle.material_registry), "tactical_scene.propulsion_unwired")
        rejected(lambda: migrate_known_d1_scene_to_directional(scene_id, state))
    scene_id, _, d1 = migrated_cases()[0]
    rejected(lambda: migrate_known_d1_scene_to_directional("unknown", d1))
    rejected(lambda: migrate_known_d1_scene_to_directional(scene_id, replace(d1, propulsion_safety_profile_sha256="0" * 64)))
    propulsion = d1.ships[0].propulsion_state
    altered = replace(propulsion, governors=(replace(propulsion.governors[0], safety_revision=1),) + propulsion.governors[1:])
    rejected(lambda: migrate_idle_d1_propulsion_state(IDLE_STATE_MIGRATION_ID, altered))
    new = migrate_idle_d1_propulsion_state(IDLE_STATE_MIGRATION_ID, propulsion)
    for change in (lambda x: x.update(interface=propulsion.interface_id), lambda x: x["governors"].pop(),
        lambda x: x["governors"][0].update(interface="unknown"),
        lambda x: x["governors"][0]["command"].update(command_channel="left"),
        lambda x: x["governors"][0].update(safety_ceiling_percent=False)):
        value = new.to_dict()
        change(value)
        rejected(lambda: TacticalPropulsionState.parse(value, "$"))
    return hashes


def check_time_intervals():
    catalog, _, outfit = d2a_profile_resources("minimum_legal")
    main = next(x for x in outfit.instances if x.actuator is not None and x.actuator.category == "main_engine")
    capability = main.prototype.capability
    replay = []
    for _ in range(3):
        state = migrate_engine_runtime_state_from_module_mode(main.id, "main_engine", "off", 0)
        rows = []
        for n in range(281):
            notch = "full" if n < 125 else "half" if n < 150 else "stop" if n < 220 else "quarter"
            opening, closing = propulsion_time_interval(state, capability, n, PropulsionTimeCommand.main_engine(notch))
            assert all(x.fixed_step_index == n for x in opening.events)
            assert all(x.fixed_step_index == n + 1 for x in closing.events)
            if n > 0:
                assert not any(x.kind in ("engine_start_completed", "engine_output_stage_changed", "engine_stopped") for x in opening.events)
            rows.append({"source": n, "integration_percent": opening.state.actual_output_percent,
                "opening": opening.to_dict(), "closing": closing.to_dict()})
            state = closing.state
        assert all(x["integration_percent"] == 0 for x in rows[:60])
        assert rows[59]["closing"]["state"]["phase"] == "running"
        assert rows[119]["integration_percent"] == 95
        assert rows[119]["closing"]["state"]["actual_output_percent"] == 100
        assert rows[120]["integration_percent"] == 100
        replay.append(canonical_sha256(rows))
    assert len(set(replay)) == 1
    return replay[0]


def check_result_contract():
    scene_id, bundle, d1 = next(x for x in migrated_cases() if x[0] == "functional_6.motion_only")
    source = migrate_known_d1_scene_to_directional(scene_id, d1)
    # 只借用旧一步的合法非推进时钟；此处不接线新力学，也不产生新运动黄金。
    legacy_step = advance_tactical_scene_step(bundle.initial_scene, bundle.bindings,
        bundle.timing_catalog, bundle.projectile_catalog, bundle.material_registry,
        controls=controls_for_step(bundle, bundle.initial_scene))
    ship = source.ships[0]
    engine = next(x for x in ship.propulsion_state.engines if x.actuator_category == "main_engine")
    off = migrate_engine_runtime_state_from_module_mode(engine.actuator_instance_id, "main_engine", "off", 0)
    catalog, _, outfit = d2a_profile_resources(bundle.ship_fixture_by_id[ship.ship_id])
    capability = next(x.prototype.capability for x in outfit.instances if x.id == engine.actuator_instance_id)
    # 合成 1 步启动能力只验证首尾合同；不改变冻结目录中的 60 步能力。
    capability = type(capability).parse({**capability.to_dict(), "startup_time_s": 1 / 60}, "$.capability", propulsion_capability_version=3)
    opening, closing = propulsion_time_interval(off, capability, 0, PropulsionTimeCommand.main_engine("full"))
    def replace_engine(propulsion, replacement):
        return replace(propulsion, engines=tuple(replacement if x.actuator_instance_id == replacement.actuator_instance_id else x for x in propulsion.engines))
    source = replace(source, ships=tuple(replace(x, propulsion_state=replace_engine(x.propulsion_state, off)) if x.ship_id == ship.ship_id else x for x in source.ships))
    by_id = {x.ship_id: x.propulsion_state for x in source.ships}
    by_id[ship.ship_id] = replace_engine(by_id[ship.ship_id], closing.state)
    result = replace(legacy_step.resulting_scene,
        propulsion_safety_profile=source.propulsion_safety_profile, propulsion_safety_profile_sha256=source.propulsion_safety_profile_sha256,
        ships=tuple(replace(x, propulsion_state=by_id[x.ship_id]) for x in legacy_step.resulting_scene.ships))
    base = replace(legacy_step, source_scene_sha256=canonical_sha256(source), resulting_scene=result)
    events = tuple(BoundaryScenePropulsionEvent(ship.ship_id, phase, migrate_channel_free_engine_event(ENGINE_EVENT_MIGRATION_ID, e))
        for phase, boundary in (("opening", opening), ("closing", closing)) for e in boundary.events)
    assert {x.boundary_phase for x in events} == {"opening", "closing"}
    envelope = build_interval_propulsion_step_resolution(source, base, reversed(events))
    serialized = envelope.to_dict()
    assert serialized["interface"] == INTERVAL_STEP_RESULT_INTERFACE_ID
    assert serialized["source_fixed_step_index"] == 0 and serialized["resulting_fixed_step_index"] == 1
    assert serialized["weapon_events"] == base.to_dict()["weapon_events"]
    assert IntervalPropulsionStepResolution.parse(serialized, source, base).to_dict() == serialized
    assert all(BoundaryScenePropulsionEvent.parse(x.to_dict()) == x for x in events)
    rejected(lambda: build_interval_propulsion_step_resolution(source, base, events + events))
    rejected(lambda: build_interval_propulsion_step_resolution(source, base, events[1:]))
    rejected(lambda: build_interval_propulsion_step_resolution(source, base, (replace(events[0], boundary_phase="closing"),) + events[1:]))
    rejected(lambda: build_interval_propulsion_step_resolution(source, base, (replace(events[0], ship_id="unknown.ship"),) + events[1:]))
    other_ship = next(x.ship_id for x in source.ships if x.ship_id != ship.ship_id)
    rejected(lambda: build_interval_propulsion_step_resolution(source, base, (replace(events[0], ship_id=other_ship),) + events[1:]))
    rejected(lambda: build_interval_propulsion_step_resolution(source, replace(base, source_scene_sha256="0" * 64), events))
    rejected(lambda: build_interval_propulsion_step_resolution(source, replace(base, resulting_scene=source), events))
    safety_event = PropulsionStateEvent(0, engine.actuator_instance_id, TRANSLATION_CHANNELS[0],
        "engine_safety_limit_engaged", None, None, 100, 25, ("structure_limit",), DIRECTIONAL_EVENT_INTERFACE_ID)
    rejected(lambda: build_interval_propulsion_step_resolution(source, base,
        events + (BoundaryScenePropulsionEvent(ship.ship_id, "opening", safety_event),)))
    changed_ship = result.ships[0]
    changed_governors = (replace(changed_ship.propulsion_state.governors[0], safety_revision=1),) + changed_ship.propulsion_state.governors[1:]
    changed_result = replace(result, ships=(replace(changed_ship, propulsion_state=replace(changed_ship.propulsion_state,
        governors=changed_governors)),) + result.ships[1:])
    rejected(lambda: build_interval_propulsion_step_resolution(source, replace(base, resulting_scene=changed_result), events))
    rejected(lambda: TacticalScenePropulsionEvent(ship.ship_id, events[0].event))
    rejected(lambda: replace(opening, events=(events[0].event,)))
    rejected(lambda: replace(opening, state=replace(opening.state, interface_id="gaotian.engine-runtime-state/v1alpha1")))
    for change in (lambda x: x.update(interface="unknown"), lambda x: x.update(boundary_phase="future"),
        lambda x: x["event"].update(command_channel="left"), lambda x: x.update(extra=1),
        lambda x: x["event"].update(fixed_step_index=False)):
        value = events[0].to_dict()
        change(value)
        rejected(lambda: BoundaryScenePropulsionEvent.parse(value))
    for change in (lambda x: x.update(interface="unknown"), lambda x: x.update(source_fixed_step_index=False),
        lambda x: x.update(resulting_fixed_step_index=1.0), lambda x: x.pop("source_fixed_step_index"),
        lambda x: x.update(policy="unknown"), lambda x: x.update(resulting_scene_sha256="0" * 64),
        lambda x: x["propulsion_events"].reverse(), lambda x: x.update(extra=1)):
        value = deepcopy(serialized)
        change(value)
        rejected(lambda: IntervalPropulsionStepResolution.parse(value, source, base))
    assert set(serialized) <= set(json.loads((ROOT / "舰艇数据/模式" / SCHEMA_FILES[-1]).read_text(encoding="utf-8"))["properties"])
    return {"opening_events": len(opening.events), "closing_events": len(closing.events), "result_hash": canonical_sha256(serialized)}


def check_cold_imports():
    for code in (
        "import sys; import 高天荒野舰艇推进资源与控制桥; import 高天荒野舰艇定向推进控制桥; assert '高天荒野舰艇统一战术场景' not in sys.modules; import 高天荒野舰艇统一战术场景; import 高天荒野舰艇场景推进结果",
        "import 高天荒野舰艇统一战术场景; import 高天荒野舰艇场景推进结果; import 高天荒野舰艇定向推进控制桥; import 高天荒野舰艇推进资源与控制桥",
    ):
        subprocess.run([sys.executable, "-X", "utf8", "-c", code], cwd=ROOT, check=True)


def check_schema_declarations():
    """检查发布 id、形状及所有本地引用；领域不变量由上面的严格解析反例验证。"""
    schemas = {name: json.loads((ROOT / "舰艇数据/模式" / name).read_text(encoding="utf-8")) for name in SCHEMA_FILES}
    by_id = {schema["$id"]: schema for schema in schemas.values()}
    expected = {
        "gaotian.tactical-propulsion-control/v2alpha1", "gaotian.propulsion-actuator-binding/v2alpha1",
        "gaotian.propulsion-governor-state/v2alpha1", DIRECTIONAL_STATE_INTERFACE_ID,
        DIRECTIONAL_SCENE_INTERFACE_ID, DIRECTIONAL_EVENT_INTERFACE_ID,
        "gaotian.tactical-scene-propulsion-event/v2alpha1", INTERVAL_STEP_RESULT_INTERFACE_ID,
    }
    assert set(by_id) == expected
    def walk(value, root):
        if isinstance(value, dict):
            if "$ref" in value:
                base, _, fragment = value["$ref"].partition("#")
                target = by_id[base] if base else root
                for part in fragment.split("/")[1:]:
                    target = target[part.replace("~1", "/").replace("~0", "~")]
            for child in value.values(): walk(child, root)
        elif isinstance(value, list):
            for child in value: walk(child, root)
    for schema in schemas.values():
        assert schema["properties"]["interface"]["const"] == schema["$id"]
        assert set(schema["required"]) <= set(schema["properties"])
        walk(schema, schema)
    command_schema = schemas[SCHEMA_FILES[0]]["properties"]["channel_commands"]
    assert tuple(x["allOf"][1]["properties"]["command_channel"]["const"] for x in command_schema["prefixItems"]) == DIRECTIONAL_CHANNELS
    return len(schemas)


def collect_evidence():
    check_cold_imports()
    return {"controls_migrated": check_controls(), "binding_channels": check_brakes_and_bindings(),
        "scene_hashes": check_state_migrations(), "time_interval_replay_sha256": check_time_intervals(),
        "interval_result": check_result_contract(), "isolation": test_existing_authority_isolation(),
        "new_mechanics_steps_advanced": 0, "cold_import_orders": 2, "interval_replay_steps": 281, "replays": 3,
        "schema_declarations_and_local_refs": check_schema_declarations()}


def main():
    evidence = collect_evidence()
    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    assert report["evidence"] == evidence
    assert report["status"] == "PASS"
    assert report["next_slice"] == "T0b.2d2b.2_complete_resource_lineage"
    for relative_path, expected in report["implementation_hashes"].items():
        assert expected == file_sha256(ROOT / relative_path), relative_path
    print(json.dumps({"status": "PASS", "evidence": evidence}, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
