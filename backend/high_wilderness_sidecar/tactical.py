"""Bounded scene ownership, render projection and atomic T3a paused control steps."""
from copy import deepcopy
from dataclasses import replace
from math import hypot
from pathlib import Path
from uuid import uuid4

from 高天荒野舰艇数据契约 import ContractError, canonical_sha256
from 高天荒野舰艇定向推进控制桥 import DirectionalPropulsionControlInput
from .tactical_scenario import SCENARIO_ID, build_two_ship_scenario
from .tactical_control import DIRECT_SHIP_ID, render_control_state
from 高天荒野舰艇战术舰队指挥 import (
    TacticalShipRoleAssignment, initialize_tactical_fleet_command_state, load_tactical_command_tuning_profile,
)
from 高天荒野舰艇定向直控仲裁 import advance_directional_direct_scene_step
from 高天荒野舰艇定向直控仲裁 import prepare_directional_direct_command
from 高天荒野舰艇实际推进聚合器 import propulsion_step_validation_scope

TACTICAL_CAPABILITIES = ("tactical.create", "tactical.inspect", "tactical.close", "tactical.set_mode", "tactical.step", "tactical.advance", "tactical.pause")
ADVANCE_INTERFACE = "gaotian.tactical-bounded-advance/v1alpha1"
RENDER_INTERFACE = "gaotian.tactical-render-snapshot/v1alpha1"
STATIC_INTERFACE = "gaotian.tactical-render-static/v1alpha1"
INPUT_INTERFACE = "gaotian.tactical-input/v1alpha1"
EVENT_INTERFACE = "gaotian.tactical-visual-event/v1alpha1"


def fail(code, path, message):
    raise ContractError("tactical." + code, path, message)


def exact(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        fail("invalid_arguments", "$.params", "战术请求字段不匹配")


def validate_control_input(value, *, scene_id, current_step, last_input_seq, ship_ids):
    """Opening-boundary v1 contract; T3a paused step executes exactly this boundary."""
    exact(value, {"interface", "scene_id", "input_seq", "target_step", "command", "arguments"})
    if value["interface"] != INPUT_INTERFACE or value["command"] != "control":
        fail("input_version", "$.interface", "不支持的战术输入版本或命令")
    if value["scene_id"] != scene_id:
        fail("scene_mismatch", "$.scene_id", "输入不属于当前场景")
    for key in ("input_seq", "target_step"):
        if type(value[key]) is not int or not 0 <= value[key] < 2**53:
            fail("input_integer", "$." + key, "序号和目标步必须为安全整数")
    if value["input_seq"] <= last_input_seq:
        fail("input_duplicate", "$.input_seq", "输入序号已执行或过期")
    if value["target_step"] != current_step:
        fail("input_step", "$.target_step", "输入必须指定当前尚未积分的开边界")
    exact(value["arguments"], {"ship_id", "control"})
    if value["arguments"]["ship_id"] not in ship_ids:
        fail("ship_missing", "$.arguments.ship_id", "未找到输入指定的舰艇")
    return DirectionalPropulsionControlInput.parse(value["arguments"]["control"])


def render_static(scenario):
    ships = []
    for binding in scenario.bindings:
        snapshot = binding.snapshot
        decks = [dict(id=d.id, level=d.level, regions=[dict(id=r.id, vertices_m=[list(p) for p in r.vertices_m])
                                                       for r in d.regions]) for d in snapshot.hull.normalized_blueprint.decks]
        modules = [dict(id=m.id, name=m.prototype.name, category=m.prototype.category,
                        anchor_m=list(m.anchor_m), rotation_deg=m.rotation_deg, deck_level=m.base_deck_level,
                        internal_cells=[list(c) for c in m.internal_cells], top_cells=[list(c) for c in m.top_cells],
                        body_points=[list(c) for c in m.body_spatial_keys], max_durability=m.prototype.durability_points)
                   for m in snapshot.outfit.instances]
        ships.append(dict(id=binding.ship_id, side_id=binding.side_id, fleet_id=binding.fleet_id,
                          name=scenario.manifest.get('ship_names', {}).get(binding.ship_id, "蓝方测试舰" if binding.side_id == "side.blue" else "红方测试舰"),
                          derived_snapshot_sha256=snapshot.source_sha256, decks=decks, modules=modules))
    return dict(interface=STATIC_INTERFACE, scenario_id=scenario.manifest.get('scenario_id', SCENARIO_ID), resources=deepcopy(scenario.manifest), ships=ships)


def render_dynamic(scenario):
    return [dict(id=s.ship_id, position_m=s.motion_state.position_world_m.to_list(),
                 heading_rad=s.motion_state.heading_rad, velocity_mps=s.motion_state.velocity_world_mps.to_list(),
                 speed_mps=hypot(s.motion_state.velocity_world_mps.x, s.motion_state.velocity_world_mps.y),
                 yaw_rate_radps=s.motion_state.yaw_rate_radps, height_layer=s.motion_state.height_layer,
                 hull_integrity=s.combat_state.instance.current_hull_integrity_fraction,
                 physical_status=s.lifecycle_state.physical_status, command_status=s.lifecycle_state.command_status,
                 modules=[dict(id=m.instance_id, durability=m.current_durability_points)
                          for m in s.combat_state.instance.module_states]) for s in scenario.scene.ships]


class TacticalService:
    def __init__(self, instance_id, root=None):
        self.instance_id = instance_id
        self.root = Path(root) if root else Path(__file__).resolve().parents[2]
        self.scenario = None
        self.scene_id = None
        self.static = None
        self.static_sha256 = None
        self.mode = "editor"
        self.command_state = self.command_tuning = self.last_input = self.last_resolution = None
        self.last_input_seq = 0
        self.advance_state = None
        self.advance_input = None

    @property
    def advancing(self):
        return self.advance_state is not None and self.advance_state["status"] == "running"

    def pause(self):
        if self.advancing:
            self.advance_state = dict(self.advance_state, status="stopped")

    def advance_one(self):
        """One fixed step on the same authority worker, independent of UI polling.

        Jobs take priority between steps. A stopped/failed preview preserves exactly
        the successfully published prefix and its receipt, never retries a step.
        """
        if not self.advancing:
            return
        try:
            control = DirectionalPropulsionControlInput.parse(self.advance_input["arguments"]["control"])
            result, candidate = self._integrate(self.advance_input, control)
            progress = dict(self.advance_state, executed_steps=self.advance_state["executed_steps"] + 1)
            if progress["executed_steps"] == progress["step_count"]:
                progress["status"] = "completed"
            self._snapshot(candidate, result.resulting_command_state, self.advance_input["input_seq"],
                self.advance_input, result, self.static_sha256)
            self.scenario, self.command_state = candidate, result.resulting_command_state
            self.last_input_seq, self.last_input, self.last_resolution = self.advance_input["input_seq"], deepcopy(self.advance_input), result
            self.advance_state = progress
        except Exception as error:
            reason = error.message if isinstance(error, ContractError) else "推进失败；已保留完成的步数，请读取状态"
            self.advance_state = dict(self.advance_state, status="failed", error=reason)

    def _integrate(self, value, control):
        with propulsion_step_validation_scope():
            result = advance_directional_direct_scene_step(self.scenario.scene, self.command_state, self.scenario.bindings,
                self.scenario.timing_catalog, self.scenario.projectile_catalog, self.scenario.material_registry, self.command_tuning,
                ship_id=value["arguments"]["ship_id"], control=control, propulsion_context=self.scenario.propulsion_context,
                continuous_damage_profile=self.scenario.continuous_damage_profile)
        return result, replace(self.scenario, scene=result.scene_resolution.resulting_scene)

    def snapshot(self, known_static_sha256=None):
        return self._snapshot(self.scenario, self.command_state, self.last_input_seq,
            self.last_input, self.last_resolution, known_static_sha256)

    def _snapshot(self, scenario, command_state, last_input_seq, last_input, last_resolution, known_static_sha256):
        scene = scenario.scene
        return dict(interface=RENDER_INTERFACE, backend_instance_id=self.instance_id, scene_id=self.scene_id,
                    authority_interface=scene.to_dict()["interface"], paused=not self.advancing, fixed_step=scene.fixed_step_index,
                    fixed_step_s=scene.fixed_step_s, time_s=scene.tactical_time_s, static_sha256=self.static_sha256,
                    static=None if known_static_sha256 == self.static_sha256 else deepcopy(self.static),
                    ships=render_dynamic(scenario), events=[], advance_state=deepcopy(self.advance_state),
                    control_state=render_control_state(scenario, command_state, self.command_tuning,
                        last_input_seq, last_input, last_resolution))

    def dispatch(self, request):
        method, params = request["method"], request["params"]
        if request.get("session_id") is not None or request.get("expected_revision") is not None:
            fail("invalid_scope", "$.session_id", "战术使用独立场景身份，不使用编辑会话")
        if method == "tactical.set_mode":
            exact(params, {"mode"})
            if params["mode"] not in ("editor", "tactical"):
                fail("invalid_mode", "$.params.mode", "仅支持编辑或战术视图")
            # Pause on the authority worker before releasing editor ownership.
            self.pause()
            self.mode = params["mode"]
            return dict(mode=self.mode, paused=True, scene_id=self.scene_id)
        if method == "tactical.create":
            exact(params, {"scenario_id"})
            if params["scenario_id"] != SCENARIO_ID:
                fail("scenario_unknown", "$.params.scenario_id", "当前仅提供两舰技术样例")
            if self.scenario is not None:
                fail("scene_active", "$.scene_id", "已有测试场景，请先释放")
            candidate = build_two_ship_scenario(self.root)
            tuning = load_tactical_command_tuning_profile(self.root / "舰艇数据/标定/阶段I舰队指挥技术替身配置.v1.json")
            command_state = initialize_tactical_fleet_command_state(candidate.scene, tuning=tuning,
                player_side_id="side.blue", assignments=(TacticalShipRoleAssignment(DIRECT_SHIP_ID, "main_flagship"),),
                direct_control_ship_id=DIRECT_SHIP_ID)
            static = render_static(candidate)
            digest = canonical_sha256(static)
            self.scenario, self.static, self.static_sha256 = candidate, static, digest
            self.command_state, self.command_tuning = command_state, tuning
            self.last_input_seq, self.last_input, self.last_resolution = 0, None, None
            self.advance_state = self.advance_input = None
            self.scene_id = "scene." + uuid4().hex
            return self.snapshot()
        if method not in TACTICAL_CAPABILITIES:
            fail("method_not_supported", "$.method", "尚未启用该战术操作")
        exact(params, {"scene_id", "known_static_sha256"} if method == "tactical.inspect" else
            {"scene_id", "input"} if method == "tactical.step" else
            {"scene_id", "input", "step_count"} if method == "tactical.advance" else {"scene_id"})
        discovering = method == "tactical.inspect" and params["scene_id"] is None
        if self.scenario is None or (not discovering and params["scene_id"] != self.scene_id):
            fail("scene_missing", "$.params.scene_id", "场景已释放、后台已重启或场景身份不匹配")
        if method == "tactical.pause":
            self.pause()
            return self.snapshot(self.static_sha256)
        if method in ("tactical.step", "tactical.advance"):
            if self.mode != "tactical":
                fail("mode_required", "$.mode", "请先进入战术视角再执行单步")
            if self.advancing:
                fail("advance_active", "$.scene_id", "正在推进，请先停止再修改操纵")
            value = params["input"]
            control = validate_control_input(value, scene_id=self.scene_id, current_step=self.scenario.scene.fixed_step_index,
                last_input_seq=self.last_input_seq, ship_ids=tuple(s.ship_id for s in self.scenario.scene.ships))
            if method == "tactical.advance":
                count = params["step_count"]
                if type(count) is not int or count not in (60, 300, 600):
                    fail("advance_count", "$.step_count", "仅支持推进 1、5 或 10 秒")
                if value["target_step"] + count >= 2**53:
                    fail("advance_count", "$.step_count", "推进后的步号超过安全整数范围")
                prepare_directional_direct_command(self.scenario.scene, self.command_state, self.command_tuning, value["arguments"]["ship_id"])
                # No step has executed yet; accepting another command is blocked until stopped.
                progress = dict(interface=ADVANCE_INTERFACE, status="running", input_seq=value["input_seq"],
                    input_sha256=canonical_sha256(value), start_step=value["target_step"], step_count=count, executed_steps=0, error=None)
                snapshot = self.snapshot(self.static_sha256)
                snapshot.update(paused=False, advance_state=deepcopy(progress))
                self.advance_state, self.advance_input = progress, deepcopy(value)
                return snapshot
            result, candidate = self._integrate(value, control)
            accepted = deepcopy(value)
            # Build the response before publishing: projection failure cannot consume a step/sequence.
            snapshot = self._snapshot(candidate, result.resulting_command_state, value["input_seq"], accepted, result, self.static_sha256)
            snapshot["advance_state"] = None
            self.scenario, self.command_state = candidate, result.resulting_command_state
            self.last_input_seq, self.last_input, self.last_resolution = value["input_seq"], accepted, result
            self.advance_state = self.advance_input = None
            return snapshot
        if method == "tactical.close":
            closed = self.scene_id
            self.scenario = self.scene_id = self.static = self.static_sha256 = None
            self.command_state = self.command_tuning = self.last_input = self.last_resolution = None
            self.last_input_seq = 0
            self.advance_state = self.advance_input = None
            return dict(closed=True, scene_id=closed)
        known = params["known_static_sha256"]
        if known is not None and (not isinstance(known, str) or len(known) != 64 or any(c not in "0123456789abcdef" for c in known)):
            fail("static_hash", "$.params.known_static_sha256", "静态资源指纹格式错误")
        return self.snapshot(known)
