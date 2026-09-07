"""T1a bounded scene ownership and read-only render projection."""
from copy import deepcopy
from math import hypot
from pathlib import Path
from uuid import uuid4

from 高天荒野舰艇数据契约 import ContractError, canonical_sha256
from 高天荒野舰艇定向推进控制桥 import DirectionalPropulsionControlInput
from .tactical_scenario import SCENARIO_ID, build_two_ship_scenario

TACTICAL_CAPABILITIES = ("tactical.create", "tactical.inspect", "tactical.close")
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
    """Frozen opening-boundary contract for T3a; no input endpoint is exposed yet."""
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
                          name="蓝方测试舰" if binding.side_id == "side.blue" else "红方测试舰",
                          derived_snapshot_sha256=snapshot.source_sha256, decks=decks, modules=modules))
    return dict(interface=STATIC_INTERFACE, scenario_id=SCENARIO_ID, resources=deepcopy(scenario.manifest), ships=ships)


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

    def snapshot(self, known_static_sha256=None):
        scene = self.scenario.scene
        return dict(interface=RENDER_INTERFACE, backend_instance_id=self.instance_id, scene_id=self.scene_id,
                    authority_interface=scene.to_dict()["interface"], paused=True, fixed_step=scene.fixed_step_index,
                    fixed_step_s=scene.fixed_step_s, time_s=scene.tactical_time_s, static_sha256=self.static_sha256,
                    static=None if known_static_sha256 == self.static_sha256 else deepcopy(self.static),
                    ships=render_dynamic(self.scenario), events=[])

    def dispatch(self, request):
        method, params = request["method"], request["params"]
        if request.get("session_id") is not None or request.get("expected_revision") is not None:
            fail("invalid_scope", "$.session_id", "战术使用独立场景身份，不使用编辑会话")
        if method == "tactical.create":
            exact(params, {"scenario_id"})
            if params["scenario_id"] != SCENARIO_ID:
                fail("scenario_unknown", "$.params.scenario_id", "当前仅提供两舰技术样例")
            if self.scenario is not None:
                fail("scene_active", "$.scene_id", "已有测试场景，请先释放")
            candidate = build_two_ship_scenario(self.root)
            static = render_static(candidate)
            digest = canonical_sha256(static)
            self.scenario, self.static, self.static_sha256 = candidate, static, digest
            self.scene_id = "scene." + uuid4().hex
            return self.snapshot()
        if method not in TACTICAL_CAPABILITIES:
            fail("method_not_supported", "$.method", "尚未启用该战术操作")
        exact(params, {"scene_id", "known_static_sha256"} if method == "tactical.inspect" else {"scene_id"})
        discovering = method == "tactical.inspect" and params["scene_id"] is None
        if self.scenario is None or (not discovering and params["scene_id"] != self.scene_id):
            fail("scene_missing", "$.params.scene_id", "场景已释放、后台已重启或场景身份不匹配")
        if method == "tactical.close":
            closed = self.scene_id
            self.scenario = self.scene_id = self.static = self.static_sha256 = None
            return dict(closed=True, scene_id=closed)
        known = params["known_static_sha256"]
        if known is not None and (not isinstance(known, str) or len(known) != 64 or any(c not in "0123456789abcdef" for c in known)):
            fail("static_hash", "$.params.known_static_sha256", "静态资源指纹格式错误")
        return self.snapshot(known)
