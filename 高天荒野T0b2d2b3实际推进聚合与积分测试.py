"""d2b.3：先冻结实际输出、配平顺序、燃料与纯积分边界。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, replace
from functools import lru_cache
import json
from math import isclose, sqrt
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from benchmarks.t0.metadata import file_sha256
from 高天荒野T0b2d2a推进资源与控制桥测试 import migrated_cases, test_existing_authority_isolation
from 高天荒野舰艇数据契约 import ContractError, ModulePrototypeCatalog, canonical_sha256
from 高天荒野舰艇推进场景构建器 import build_known_directional_scene
from 高天荒野舰艇定向推进控制桥 import bind_directional_outfit_propulsion
from 高天荒野舰艇推进状态合同 import migrate_engine_runtime_state_from_module_mode, C2B_ENGINE_RUNTIME_STATE_INTERFACE_ID
from 高天荒野舰艇实际推进合同 import ActualActuationRequest
from 高天荒野舰艇实际推进聚合器 import compile_actual_propulsion_context, aggregate_actual_propulsion
from 高天荒野舰艇运行时参数编译器 import compile_runtime_ship_parameters, initialize_ship_instance_snapshot
from 高天荒野舰艇无界面舾装编译器 import compile_outfit, build_derived_ship_snapshot
from 高天荒野舰艇出航配置编译器 import compile_sortie_configuration
from 高天荒野舰艇数据契约 import load_hull_coating_catalog
from 高天荒野舰艇持续毁伤 import continuous_damage_automatic_events
from 高天荒野舰艇统一战术场景 import advance_tactical_scene_step
import 高天荒野舰艇战术机动求解器 as dynamics
from 高天荒野舰艇战术机动求解器 import (
    Vec2, TacticalControlInput, build_tactical_ship_model, initialize_tactical_motion_state,
    integrate_actual_tactical_step, integrate_tactical_step, calculate_tactical_drag,
    request_layer_transition, structure_ratio, body_to_world, world_to_body, wrap_angle,
)

ROOT = Path(__file__).resolve().parent
REPORT_PATH = ROOT / "舰艇数据/报告/阶段T0b2d2b3实际推进聚合与积分接口.v1.json"
REJECTIONS = 0


def close(a, b):
    assert isclose(a, b, rel_tol=1e-10, abs_tol=1e-9), (a, b)


def rejected(action, code=None):
    global REJECTIONS
    try:
        action()
    except ContractError as error:
        if code:
            assert error.code == code, str(error)
        REJECTIONS += 1
    else:
        raise AssertionError("非法输入未被拒绝")


@lru_cache(maxsize=1)
def fixture_bundle():
    name, old, source = migrated_cases()[0]
    return build_known_directional_scene(ROOT, name, source, old.bindings)


def fixture(profile="conventional_crewed", mutate=None, catalog_mutate=None):
    bundle = fixture_bundle()
    resources = next(s for s in bundle.ships if s.profile_key == profile)
    ship = next(s for s in bundle.scene.ships if s.ship_id == resources.binding.ship_id)
    p = next(p for p in bundle.profiles if p.profile_key == profile)
    binding = resources.binding
    snapshot, catalog, sortie = binding.snapshot, p.catalog, binding.sortie
    instance = ship.combat_state.instance
    bindings = resources.actuators
    if catalog_mutate:
        payload = catalog.to_dict()
        catalog_mutate(payload)
        catalog = ModulePrototypeCatalog.parse(payload)
        snapshot = build_derived_ship_snapshot(snapshot.hull, compile_outfit(
            snapshot.outfit.normalized_plan, snapshot.hull, catalog,
            load_hull_coating_catalog(ROOT / "舰艇数据/涂料/船体涂料.v1.json")))
        sortie = compile_sortie_configuration(snapshot, sortie.configuration)
        instance = initialize_ship_instance_snapshot(snapshot, sortie, embed_design_state=True)
        bindings = bind_directional_outfit_propulsion(bundle.scene_id, ship.ship_id, snapshot.outfit, catalog)
    if mutate:
        instance = mutate(instance)
    runtime = compile_runtime_ship_parameters(snapshot, sortie, instance,
        active_automatic_events=continuous_damage_automatic_events(instance))
    context = compile_actual_propulsion_context(bundle.scene_id, ship.ship_id,
        snapshot, catalog, bindings)
    return context, runtime, build_tactical_ship_model(runtime, snapshot)


def engines(context, values=None, targets=None):
    values, targets = values or {}, targets or {}
    result = []
    for b in context.bindings:
        value = values.get(b.actuator_instance_id, 0)
        target = targets.get(b.actuator_instance_id, value)
        ready = migrate_engine_runtime_state_from_module_mode(
            b.actuator_instance_id, b.actuator_category, "active", 0)
        if value or target:
            ready = replace(ready, phase="running" if target else "stopping",
                commanded_notch=("full" if target else "stop") if b.actuator_category == "main_engine" else None,
                actual_output_percent=value, target_output_percent=target,
                next_transition_step=200 if value != target else None,
                response_started_at_fixed_step=0 if value != target else None,
                response_start_output_percent=value if value != target else None)
        result.append(ready)
    return tuple(result)


def full_channel(context, channel, value):
    return {b.actuator_instance_id: value for b in context.bindings if channel in b.command_channels}


def aggregate(context, runtime, values=None, *, step=0, targets=None):
    return aggregate_actual_propulsion(context, runtime, engines(context, values, targets), step)


def check_stages_and_asymmetry():
    context, runtime, model = fixture()
    forward = runtime.actuator_aggregation.main("forward")
    for stage in (0, 2, 5, 100):
        result = aggregate(context, runtime, full_channel(context, "translation.forward", stage))
        close(result.request.force_body_n[1], forward.net_force_body_n[1] * stage / 100)
        close(result.request.fuel_units_per_s, forward.fuel_units_per_s * stage / 100)
        assert len(result.contributions) == len(context.bindings)
        assert ActualActuationRequest.parse(result.request.to_dict()) == result.request
        # 无分配、无输出后配平、无软限幅；真实运动求解只读取交付力。
        with patch.object(dynamics, "allocate_tactical_actuation", side_effect=AssertionError("continuous allocator called")), \
             patch.object(dynamics, "_choose_command_scale", side_effect=AssertionError("soft limiter called")), \
             patch("高天荒野舰艇无界面舾装编译器.aggregate_actuators", side_effect=AssertionError("post-output rebalance called")):
            again = aggregate(context, runtime, full_channel(context, "translation.forward", stage))
            state, diag = integrate_actual_tactical_step(model, initialize_tactical_motion_state(model), again.request)
        close(state.velocity_world_mps.y, result.request.force_body_n[1] / runtime.current_mass_kg / 60)
        close(diag.fuel_delivery_fraction, 1)
        assert diag.soft_governor_status == "unwired"
    values = {"main_engine_port": 100, "main_engine_starboard": 5}
    result = aggregate(context, runtime, values)
    assert result.request.force_body_n == (0.0, 105000.0)
    close(result.request.torque_n_m, -475000)
    close(result.request.fuel_units_per_s, 1.05)
    targeted = aggregate(context, runtime, values, targets={"main_engine_port": 100, "main_engine_starboard": 100})
    assert targeted.request == result.request  # 指令目标不得再次参与物理缩放。
    rotated, diag = integrate_actual_tactical_step(model, initialize_tactical_motion_state(model), result.request)
    close(diag.active_torque_n_m, -475000)
    close(rotated.yaw_rate_radps, -475000 / runtime.current_inertia_kg_m2 / 60)
    amplified = replace(model, tuning=replace(model.tuning, turn_scale=2.0))
    doubled, _ = integrate_actual_tactical_step(amplified, initialize_tactical_motion_state(amplified), result.request)
    close(doubled.yaw_rate_radps, 2 * rotated.yaw_rate_radps)
    turn = aggregate(context, runtime, {"thruster_port_aft": 100, "thruster_starboard_fore": 5})
    close(turn.request.force_body_n[0], 9500)
    close(turn.request.torque_n_m, 288750)
    close(turn.request.fuel_units_per_s, .105)
    return {"asynchronous_main_force_y_n": 105000, "asynchronous_main_torque_n_m": -475000,
            "asynchronous_yaw_force_x_n": 9500, "asynchronous_yaw_torque_n_m": 288750}


def mutate_modules(instance, changes):
    return replace(instance, module_states=tuple(replace(m, **changes.get(m.instance_id, {})) for m in instance.module_states))


def check_runtime_constraints():
    damaged = lambda i: mutate_modules(i, {"main_engine_port": {"current_durability_points": 50.0}})
    context, runtime, model = fixture(mutate=damaged)
    result = aggregate(context, runtime, {"main_engine_port": 100, "main_engine_starboard": 5})
    uses = runtime.actuator_aggregation.main("forward").uses
    expected = {u.instance_id: u for u in uses}
    rows = {r.actuator_instance_id: r for r in result.contributions}
    port = next(a for a in runtime.actuators if a.instance_id == "main_engine_port")
    assert port.thrust_n < 100000
    close(rows["main_engine_port"].runtime_thrust_n, port.thrust_n)
    close(rows["main_engine_port"].balance_scale, expected["main_engine_port"].output_scale)
    close(result.request.force_body_n[1], expected["main_engine_port"].force_body_n[1] + .05 * expected["main_engine_starboard"].force_body_n[1])
    assert result.request.torque_n_m != 0  # 不能在实际输出之后再强行配平。
    destroyed = lambda i: mutate_modules(i, {"main_engine_port": {"current_durability_points": 0.0}})
    c, r, _ = fixture(mutate=destroyed)
    zero = aggregate(c, r, full_channel(c, "translation.forward", 100))
    assert zero.request.force_body_n == (0.0, 0.0)
    assert len(zero.contributions) == len(c.bindings)
    assert next(x for x in zero.contributions if x.actuator_instance_id == "main_engine_port").runtime_available is False
    # 单侧主机被配平为 0，不删除其余主机；单侧转向喷口失效则保留另一侧残余平移。
    c, r, _ = fixture(mutate=lambda i: mutate_modules(i, {"thruster_starboard_fore": {"current_durability_points": 0.0}}))
    one_sided = aggregate(c, r, full_channel(c, "yaw.counterclockwise", 100))
    assert one_sided.request.force_body_n == (10000.0, 0.0)
    for mutate in (
        lambda i: replace(i, operational_state=replace(i.operational_state, fuel_units=0.0)),
        lambda i: mutate_modules(i, {m.instance_id: {"operating_mode": "off"} for m in i.module_states}),
    ):
        c, r, m = fixture(mutate=mutate)
        result = aggregate(c, r, full_channel(c, "translation.forward", 100))
        assert result.request.force_body_n == (0.0, 0.0) and result.request.fuel_units_per_s == 0
        state, diag = integrate_actual_tactical_step(m, initialize_tactical_motion_state(m), result.request)
        assert diag.fuel_units_consumed == 0
    def manual_catalog(value):
        for module in value["modules"]:
            if module["category"] == "main_engine":
                module["automation"].update(level="manual", automated_functions=[])
    def electric_catalog(value):
        for module in value["modules"]:
            if module["category"] == "main_engine":
                module["power"].update(consumer_category="sensors", active_load_kw=1000000.0)
    no_crew = lambda i: replace(i, operational_state=replace(i.operational_state, crew=()))
    for mutation, catalog_mutation in ((no_crew, manual_catalog), (None, electric_catalog)):
        c, r, m = fixture(mutate=mutation, catalog_mutate=catalog_mutation)
        result = aggregate(c, r, full_channel(c, "translation.forward", 100))
        assert result.request.force_body_n == (0.0, 0.0) and result.request.fuel_units_per_s == 0
        assert all(not x.runtime_available for x in result.contributions if x.command_channel == "translation.forward")
    # 原自动化夹具缺员仍能节流，不能借测试更改其人员政策。
    c, r, _ = fixture(mutate=no_crew)
    assert aggregate(c, r, full_channel(c, "translation.forward", 100)).request.force_body_n[1] == 200000
    return {"runtime_scaled_port_thrust_n": port.thrust_n, "single_yaw_survivor_force_x_n": 10000,
            "manual_no_crew_zero_output": True, "synthetic_no_power_zero_output": True,
            "original_automated_throttle_preserved": True}


def check_fuel_and_formulas():
    c, r, m = fixture("minimum_legal", lambda i: replace(i, operational_state=replace(i.operational_state, fuel_units=1 / 120)))
    result = aggregate(c, r, full_channel(c, "translation.forward", 100))
    state = replace(initialize_tactical_motion_state(m), velocity_world_mps=Vec2(3, 10), heading_rad=.7, yaw_rate_radps=.2)
    new, d = integrate_actual_tactical_step(m, state, result.request)
    close(d.fuel_delivery_fraction, .5)
    close(d.fuel_units_consumed, 1 / 120)
    assert new.fuel_units == 0
    assert d.active_force_body_n == Vec2(0, 50000)
    drag = calculate_tactical_drag(m, state)
    acceleration = (body_to_world(d.active_force_body_n, state.heading_rad) + drag.force_world_n) / r.current_mass_kg
    close(new.velocity_world_mps.x, state.velocity_world_mps.x + acceleration.x / 60)
    close(new.velocity_world_mps.y, state.velocity_world_mps.y + acceleration.y / 60)
    close(new.position_world_m.y, state.position_world_m.y + new.velocity_world_mps.y / 60)
    close(d.crew_g, sqrt(1 + (acceleration.length / m.tuning.gravity_mps2) ** 2))
    # 零输出依然有阻力、角速度、结构载荷；换层仍在边界完成。
    zero = aggregate(c, r).request
    moving, dd = integrate_actual_tactical_step(m, state, zero)
    assert dd.drag_force_world_n.length > 0 and moving.velocity_world_mps.length < state.velocity_world_mps.length
    close(moving.heading_rad, wrap_angle(state.heading_rad + state.yaw_rate_radps / 60))
    env = replace(m.environment, upper_cloud_transition_s=1 / 60)
    mm = replace(m, environment=env)
    transitioning = request_layer_transition(mm, initialize_tactical_motion_state(mm), "cloud")
    end, _ = integrate_actual_tactical_step(mm, transitioning, zero)
    assert end.height_layer == "cloud" and end.layer_transition is None
    # 新入口不宣称 OverG/乘员锁提供软保护，但仍计算旧结构毁伤公式。
    c, r, m = fixture("unmanned_flagship")
    req = aggregate(c, r, full_channel(c, "translation.forward", 100)).request
    start = initialize_tactical_motion_state(m)
    end, d = integrate_actual_tactical_step(m, start, req)
    assert d.structure_ratio > 1 and d.hull_integrity_damage > 0
    close(d.hull_integrity_damage, (1 / 60) / m.tuning.overg_reference_time_s * ((d.structure_ratio - 1) / (m.tuning.overg_reference_ratio - 1)) ** 2)
    close(end.hull_integrity_fraction, max(0, start.hull_integrity_fraction - d.hull_integrity_damage))
    # 同一实际执行量喂给旧积分公式且固定 scale=1，应得到逐字段相同状态。
    actuation = dynamics.AllocatedActuation(Vec2(*req.force_body_n), Vec2(), req.torque_n_m, 0, 0, req.fuel_units_per_s)
    with patch.object(dynamics, "allocate_tactical_actuation", return_value=actuation), \
         patch.object(dynamics, "_choose_command_scale", side_effect=lambda model, state, controls, act, drag, dt: (1.0, dynamics._load_metrics(model, state, act, drag, 1.0, dt))):
        expected, _ = integrate_tactical_step(m, start, TacticalControlInput())
    assert end == expected
    # 有人舰的乘员锁同样不在本入口隐式启用；只是报告真实载荷。
    def high_thrust(value):
        for module in value["modules"]:
            if module["category"] == "main_engine":
                module["capability"]["thrust_n"] = 1_000_000_000.0
    cc, rr, mm = fixture(catalog_mutate=high_thrust)
    assert rr.crew_safety_lock_enabled
    _, crew_diag = integrate_actual_tactical_step(mm, initialize_tactical_motion_state(mm),
        aggregate(cc, rr, full_channel(cc, "translation.forward", 100)).request)
    assert crew_diag.crew_g > 12 and crew_diag.fuel_delivery_fraction == 1
    return {"partial_fuel_delivery_fraction": .5, "unprotected_structure_ratio": d.structure_ratio,
            "unprotected_hull_damage": d.hull_integrity_damage, "unprotected_crewed_g": crew_diag.crew_g}


def check_partial_torque_and_replay():
    traces = []
    for _ in range(3):
        c, r, m = fixture(mutate=lambda i: replace(i, operational_state=replace(i.operational_state, fuel_units=.02)))
        b = next(s.binding for s in fixture_bundle().ships if s.binding.ship_id == c.ship_id)
        state = initialize_tactical_motion_state(m)
        stage_values = {"main_engine_port": 100, "main_engine_starboard": 5,
                        "thruster_port_aft": 100, "thruster_starboard_fore": 5}
        engine_states = engines(c, stage_values)
        engine_hash = canonical_sha256([e.to_dict() for e in engine_states])
        log = []
        for step in range(4):
            result = aggregate_actual_propulsion(c, r, engine_states, step)
            before = canonical_sha256([e.to_dict() for e in engine_states])
            state, diag = integrate_actual_tactical_step(m, state, result.request)
            assert before == engine_hash
            close(diag.active_torque_n_m, result.request.torque_n_m * diag.fuel_delivery_fraction)
            close(diag.active_force_body_n.y, result.request.force_body_n[1] * diag.fuel_delivery_fraction)
            log.append({"state": asdict(state), "diagnostics": diag.to_dict()})
            instance = dynamics.commit_tactical_state_to_instance(m, state)
            r = compile_runtime_ship_parameters(c.snapshot, b.sortie, instance)
            m = build_tactical_ship_model(r, c.snapshot)
        assert 0 < log[1]["diagnostics"]["fuel_delivery_fraction"] < 1
        assert log[1]["state"]["fuel_units"] == 0
        assert log[2]["diagnostics"]["fuel_units_consumed"] == 0
        assert log[2]["diagnostics"]["active_torque_n_m"] == 0
        close(sum(item["diagnostics"]["fuel_units_consumed"] for item in log), .02)
        traces.append(canonical_sha256(log))
    assert len(set(traces)) == 1
    return traces[0]


def check_negatives():
    start_count = REJECTIONS
    c, r, m = fixture()
    states = engines(c)
    rejected(lambda: aggregate_actual_propulsion(c, r, states[:-1], 0))
    rejected(lambda: aggregate_actual_propulsion(c, r, states + (states[0],), 0))
    rejected(lambda: aggregate_actual_propulsion(c, r, states, True))
    rejected(lambda: aggregate_actual_propulsion(c, r, states, -1))
    rejected(lambda: aggregate_actual_propulsion(c, r, (replace(states[0], interface_id=C2B_ENGINE_RUNTIME_STATE_INTERFACE_ID),) + states[1:], 0))
    future = (replace(states[0], ready_at_fixed_step=2),) + states[1:]
    rejected(lambda: aggregate_actual_propulsion(c, r, future, 0))
    stale = engines(c, {states[0].actuator_instance_id: 5}, {states[0].actuator_instance_id: 100})
    rejected(lambda: aggregate_actual_propulsion(c, r, stale, 200))
    groups = r.actuator_aggregation.main_directions
    duplicates = replace(groups[0], uses=groups[0].uses + (groups[0].uses[0],))
    wrong_aggregation = replace(r.actuator_aggregation, main_directions=(duplicates,) + groups[1:])
    wrong_runtime = replace(r, _core=replace(r.stable_core, actuator_aggregation=wrong_aggregation))
    rejected(lambda: aggregate_actual_propulsion(c, wrong_runtime, states, 0), "actual_propulsion.use_identity")
    wrong_use = replace(groups[0].uses[0], force_body_n=(0, 42))
    wrong_group = replace(groups[0], uses=(wrong_use,) + groups[0].uses[1:])
    wrong_runtime = replace(r, _core=replace(r.stable_core,
        actuator_aggregation=replace(r.actuator_aggregation, main_directions=(wrong_group,) + groups[1:])))
    rejected(lambda: aggregate_actual_propulsion(c, wrong_runtime, states, 0), "actual_propulsion.runtime_use_mismatch")
    other = fixture("minimum_legal")[1]
    rejected(lambda: aggregate_actual_propulsion(c, other, states, 0))
    opposing = full_channel(c, "yaw.counterclockwise", 5) | full_channel(c, "yaw.clockwise", 5)
    rejected(lambda: aggregate(c, r, opposing), "actual_propulsion.direction_interlock_unwired")
    pending = full_channel(c, "yaw.counterclockwise", 5)
    rejected(lambda: aggregate(c, r, pending, targets=full_channel(c, "yaw.clockwise", 5)), "actual_propulsion.direction_interlock_unwired")
    b = c.bindings[0]
    rejected(lambda: compile_actual_propulsion_context(c.scene_id, c.ship_id, c.snapshot, c.catalog, (replace(b, ship_id="ship.wrong"),) + c.bindings[1:]))
    rejected(lambda: compile_actual_propulsion_context(c.scene_id, c.ship_id, c.snapshot, c.catalog, c.bindings + (b,)))
    rejected(lambda: compile_actual_propulsion_context(c.scene_id, c.ship_id, c.snapshot, c.catalog, (replace(b, command_channels=("translation.forward", "translation.reverse")),) + c.bindings[1:]))
    request = aggregate(c, r).request
    for value in (float("nan"), float("inf"), True, "2", 10 ** 400):
        obj = request.to_dict()
        obj["fuel_units_per_s"] = value
        rejected(lambda: ActualActuationRequest.parse(obj))
        rejected(lambda: integrate_actual_tactical_step(m, initialize_tactical_motion_state(m), request, dt=value))
    for mutation in (lambda x: x.update(extra=0), lambda x: x.pop("force_body_n"),
                     lambda x: x.update(interface="unknown"), lambda x: x.update(source_fixed_step_index=False),
                     lambda x: x.update(fuel_units_per_s=-1), lambda x: x.update(force_body_n=[0]),
                     lambda x: x.update(force_body_n=[0, float("inf")])):
        obj = request.to_dict()
        mutation(obj)
        rejected(lambda: ActualActuationRequest.parse(obj))
    start = initialize_tactical_motion_state(m)
    rejected(lambda: integrate_actual_tactical_step(m, start, replace(request, source_fixed_step_index=1)))
    rejected(lambda: integrate_actual_tactical_step(m, start, replace(request, runtime_parameters_sha256="0" * 64)))
    rejected(lambda: integrate_actual_tactical_step(m, start, request, dt=1 / 30))
    rejected(lambda: integrate_actual_tactical_step(m, replace(start, fuel_units=start.fuel_units - 1), request))
    rejected(lambda: integrate_actual_tactical_step(m, replace(start, velocity_world_mps=Vec2(float("nan"), 0)), request))
    rejected(lambda: integrate_actual_tactical_step(m, start, replace(request, force_body_n=(0, 1e308))))
    for zero_phase in ("off", "tripped"):
        idle = tuple(replace(e, phase=zero_phase, ready_at_fixed_step=None) for e in states)
        assert aggregate_actual_propulsion(c, r, idle, 0).request.force_body_n == (0, 0)
    return REJECTIONS - start_count


def check_all_scene_inputs():
    ships = actuators = 0
    output_hashes = {}
    for name, old, source in migrated_cases():
        bundle = build_known_directional_scene(ROOT, name, source, old.bindings)
        before = canonical_sha256(bundle.scene)
        profiles = {p.profile_key: p for p in bundle.profiles}
        by_ship = {s.ship_id: s for s in bundle.scene.ships}
        requests = []
        for item in bundle.ships:
            b = item.binding
            ship = by_ship[b.ship_id]
            context = compile_actual_propulsion_context(name, b.ship_id, b.snapshot,
                profiles[item.profile_key].catalog, item.actuators)
            runtime = compile_runtime_ship_parameters(b.snapshot, b.sortie, ship.combat_state.instance,
                active_automatic_events=continuous_damage_automatic_events(ship.combat_state.instance))
            model = build_tactical_ship_model(runtime, b.snapshot)
            values = full_channel(context, "translation.forward", 5) | full_channel(context, "yaw.counterclockwise", 5)
            output = aggregate(context, runtime, values)
            state, diag = integrate_actual_tactical_step(model, ship.motion_state, output.request)
            assert state.fixed_step_index == 1 and diag.soft_governor_status == "unwired"
            requests.append(output.to_dict())
            ships += 1
            actuators += len(output.contributions)
        rejected(lambda: advance_tactical_scene_step(bundle.scene, bundle.bindings,
            old.timing_catalog, old.projectile_catalog, old.material_registry), "tactical_scene.propulsion_unwired")
        assert canonical_sha256(bundle.scene) == before
        output_hashes[name] = canonical_sha256(requests)
    assert ships == 224 and actuators == 1224
    return {"scenes": 12, "ships": ships, "actuators": actuators, "request_hashes": output_hashes}


def collect_evidence():
    for prefix in ("", "import 高天荒野舰艇战术机动求解器; "):
        subprocess.run([sys.executable, "-X", "utf8", "-c", prefix +
            "import sys; import 高天荒野舰艇实际推进聚合器; assert '高天荒野舰艇统一战术场景' not in sys.modules"], cwd=ROOT, check=True)
    evidence = {"asymmetry": check_stages_and_asymmetry(), "runtime": check_runtime_constraints(),
                "integration": check_fuel_and_formulas(), "negative_cases": check_negatives(),
                "four_step_fuel_exhaustion_replay_sha256": check_partial_torque_and_replay(),
                "scene_input_coverage": check_all_scene_inputs()}
    c, r, model = fixture()
    states = engines(c, {"main_engine_port": 100, "main_engine_starboard": 5})
    hashes = [canonical_sha256(aggregate_actual_propulsion(c, r, tuple(reversed(states)), 0)) for _ in range(3)]
    assert len(set(hashes)) == 1
    assert hashes[0] == canonical_sha256(aggregate_actual_propulsion(c, r, states, 0))
    evidence.update({"replays": 3, "asymmetric_output_sha256": hashes[0], "cold_import_orders": 2,
                     "scene_steps_advanced": 0, "isolation": test_existing_authority_isolation()})
    schemas = [json.loads((ROOT / "舰艇数据/模式" / name).read_text(encoding="utf-8")) for name in (
        "高天荒野舰艇实际执行量请求契约.v1alpha1.schema.json",
        "高天荒野舰艇实际推进聚合结果契约.v1alpha1.schema.json",
        "高天荒野舰艇实际推进积分诊断契约.v1alpha1.schema.json")]
    ids = {s["$id"] for s in schemas}
    output = aggregate(c, r)
    _, diagnostics = integrate_actual_tactical_step(model, initialize_tactical_motion_state(model), output.request)
    for schema, payload in zip(schemas, (output.request.to_dict(), output.to_dict(), diagnostics.to_dict())):
        assert set(schema["properties"]) == set(schema["required"]) == set(payload)
        assert schema["properties"]["interface"]["const"] == payload["interface"]
        def walk(node):
            if isinstance(node, dict):
                if "$ref" in node:
                    assert node["$ref"] in ids
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)
        walk(schema)
    evidence["schema_shapes_and_local_refs"] = 3
    return evidence


def main():
    evidence = collect_evidence()
    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    assert report["status"] == "PASS" and report["evidence"] == evidence
    for relative, expected in report["implementation_hashes"].items():
        assert expected == file_sha256(ROOT / relative), relative
    print(json.dumps({"status": "PASS", "evidence": evidence}, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
