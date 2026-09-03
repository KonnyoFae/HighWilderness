"""d2b.3：逐执行器 runtime 配平 use × 实际离散阶段，无场景副作用。"""

from __future__ import annotations

from dataclasses import dataclass
from math import fsum, isclose
from typing import Any, Iterable

from 高天荒野舰艇数据契约 import ContractError, ModulePrototypeCatalog, canonical_sha256
from 高天荒野舰艇实际推进合同 import ActualActuationRequest, finite_number, fixed_step_index
from 高天荒野舰艇无界面舾装编译器 import DerivedShipSnapshot, verify_derived_ship_snapshot_fingerprint
from 高天荒野舰艇运行时参数编译器 import RuntimeShipParameters, ACTUATOR_FUNCTION_BY_CATEGORY, EPS
from 高天荒野舰艇定向推进控制桥 import DirectionalPropulsionActuatorBinding, bind_directional_outfit_propulsion
from 高天荒野舰艇推进状态合同 import EngineRuntimeState, ENGINE_RUNTIME_STATE_INTERFACE_ID
from 高天荒野舰艇推进通道合同 import OPPOSING_CHANNEL_PAIRS

ACTUAL_AGGREGATION_INTERFACE_ID = "gaotian.actual-propulsion-aggregation/v1alpha1"
ACTUAL_AGGREGATION_POLICY_ID = "gaotian.propulsion/runtime-use-then-actual-output/v1"


def _equal(actual: Any, expected: Any, path: str) -> None:
    if canonical_sha256(actual) != canonical_sha256(expected):
        raise ContractError("actual_propulsion.source_mismatch", path, "必须使用当前精确来源")


@dataclass(frozen=True)
class ActualPropulsionContext:
    scene_id: str
    ship_id: str
    snapshot: DerivedShipSnapshot
    catalog: ModulePrototypeCatalog
    bindings: tuple[DirectionalPropulsionActuatorBinding, ...]

    def __post_init__(self) -> None:
        verify_derived_ship_snapshot_fingerprint(self.snapshot)
        expected = bind_directional_outfit_propulsion(
            self.scene_id, self.ship_id, self.snapshot.outfit, self.catalog)
        _equal([b.to_dict() for b in self.bindings], [b.to_dict() for b in expected], "$.bindings")
        if any(len(b.command_channels) != 1 for b in self.bindings):
            raise ContractError("actual_propulsion.ambiguous_intent", "$.bindings", "本版本要求每执行器唯一物理意图")


def compile_actual_propulsion_context(
    scene_id: str, ship_id: str, snapshot: DerivedShipSnapshot, catalog: ModulePrototypeCatalog,
    bindings: Iterable[DirectionalPropulsionActuatorBinding],
) -> ActualPropulsionContext:
    """在静态边界验证精确身份；不让资源构建器或场景成为聚合器依赖。"""
    return ActualPropulsionContext(scene_id, ship_id, snapshot, catalog,
        tuple(sorted(bindings, key=lambda b: b.actuator_instance_id)))


@dataclass(frozen=True)
class ActualActuatorContribution:
    actuator_instance_id: str
    command_channel: str
    target_output_percent: int
    actual_output_percent: int
    runtime_available: bool
    runtime_efficiency: float
    runtime_thrust_n: float
    balance_scale: float
    force_body_n: tuple[float, float]
    torque_n_m: float
    fuel_units_per_s: float

    def to_dict(self) -> dict[str, Any]:
        return {"actuator_instance_id": self.actuator_instance_id, "command_channel": self.command_channel,
                "target_output_percent": self.target_output_percent, "actual_output_percent": self.actual_output_percent,
                "runtime_available": self.runtime_available, "runtime_efficiency": self.runtime_efficiency,
                "runtime_thrust_n": self.runtime_thrust_n, "balance_scale": self.balance_scale,
                "force_body_n": list(self.force_body_n), "torque_n_m": self.torque_n_m,
                "fuel_units_per_s": self.fuel_units_per_s}


@dataclass(frozen=True)
class ActualPropulsionAggregation:
    scene_id: str
    ship_id: str
    request: ActualActuationRequest
    contributions: tuple[ActualActuatorContribution, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"interface": ACTUAL_AGGREGATION_INTERFACE_ID, "policy": ACTUAL_AGGREGATION_POLICY_ID,
                "scene_id": self.scene_id, "ship_id": self.ship_id, "request": self.request.to_dict(),
                "contributions": [c.to_dict() for c in self.contributions]}


def _close(actual: float, expected: float, path: str) -> None:
    finite_number(actual, path)
    # 既有配平输出保留 10 位小数，output_scale 的舍入也会放大到推力单位。
    if not isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-8):
        raise ContractError("actual_propulsion.runtime_use_mismatch", path, "配平 use 与执行器不符")


def aggregate_actual_propulsion(
    context: ActualPropulsionContext, runtime: RuntimeShipParameters,
    engine_states: Iterable[EngineRuntimeState], source_fixed_step_index: int,
) -> ActualPropulsionAggregation:
    """runtime 已完成效率/可用性及配平；这里只缩放每个 use，一次稳定求和。"""
    step = fixed_step_index(source_fixed_step_index)
    if runtime.derived_snapshot_sha256 != context.snapshot.source_sha256:
        raise ContractError("actual_propulsion.runtime_snapshot", "$.runtime", "运行时来自其他设计")
    if canonical_sha256(runtime) != runtime.source_sha256 or canonical_sha256(runtime.instance_snapshot) != runtime.instance_snapshot_sha256:
        raise ContractError("actual_propulsion.runtime_fingerprint", "$.runtime", "运行时或实例指纹失效")
    modules = {m.instance_id: m for m in runtime.modules}
    if len(modules) != len(runtime.modules) or set(modules) != {m.id for m in context.snapshot.outfit.instances}:
        raise ContractError("actual_propulsion.runtime_modules", "$.runtime.modules", "运行时模块集合必须精确且唯一")
    states = tuple(engine_states)
    by_id = {e.actuator_instance_id: e for e in states}
    binding_by_id = {b.actuator_instance_id: b for b in context.bindings}
    if len(by_id) != len(states) or set(by_id) != set(binding_by_id):
        raise ContractError("actual_propulsion.engine_set", "$.engines", "必须保留全部静态执行器且各出现一次")
    active_channels = set()
    for key, engine in by_id.items():
        EngineRuntimeState.parse(engine.to_dict(), f"$.engines.{key}")
        if engine.interface_id != ENGINE_RUNTIME_STATE_INTERFACE_ID or engine.actuator_category != binding_by_id[key].actuator_category:
            raise ContractError("actual_propulsion.engine_binding", "$.engines", "执行器版本或类别与绑定不一致")
        if (engine.phase in {"ready", "running", "stopping"} and engine.ready_at_fixed_step > step) or (
            engine.next_transition_step is not None and engine.next_transition_step <= step
        ) or (engine.response_started_at_fixed_step is not None and engine.response_started_at_fixed_step > step):
            raise ContractError("actual_propulsion.engine_boundary", "$.engines", "只接受当前已提交边界状态")
        if engine.actual_output_percent or engine.target_output_percent:
            active_channels.add(binding_by_id[key].command_channels[0])
    if any(a in active_channels and b in active_channels for a, b in OPPOSING_CHANNEL_PAIRS):
        raise ContractError("actual_propulsion.direction_interlock_unwired", "$.engines", "对向请求或未停车换向仍待 d4 互锁")
    available = {a.instance_id: a for a in runtime.actuators}
    if len(available) != len(runtime.actuators) or set(available) - set(binding_by_id):
        raise ContractError("actual_propulsion.runtime_actuators", "$.runtime.actuators", "运行时执行器必须唯一且属于当前绑定")
    use_by_id = {}
    groups = [(f"translation.{g.direction}", g.uses) for g in runtime.actuator_aggregation.main_directions]
    groups += [(f"yaw.{g.direction}", g.uses) for g in runtime.actuator_aggregation.turning_directions]
    for channel, uses in groups:
        for use in uses:
            key = use.instance_id
            if key in use_by_id or key not in available or channel not in binding_by_id[key].command_channels:
                raise ContractError("actual_propulsion.use_identity", "$.runtime.uses", "配平 use 不得重复、跨用途或引用不存在的执行器")
            scale = finite_number(use.output_scale, "$.runtime.uses.output_scale", 0)
            if scale > 1:
                raise ContractError("actual_propulsion.use_scale", "$.runtime.uses", "配平比例不得超过 1")
            actuator = available[key]
            _close(use.available_thrust_n, actuator.thrust_n, "$.runtime.uses.available_thrust_n")
            _close(use.used_thrust_n, actuator.thrust_n * scale, "$.runtime.uses.used_thrust_n")
            for force, direction in zip(use.force_body_n, actuator.direction_body):
                _close(force, use.used_thrust_n * direction, "$.runtime.uses.force_body_n")
            _close(use.torque_about_cic_n_m, actuator.torque_about_cic_n_m * scale, "$.runtime.uses.torque")
            _close(use.fuel_units_per_s, actuator.fuel_units_per_s * scale, "$.runtime.uses.fuel")
            use_by_id[key] = use
    if set(use_by_id) != set(available):
        raise ContractError("actual_propulsion.use_set", "$.runtime.uses", "运行时执行器必须逐个有唯一配平 use")
    static = {a.instance_id: a for a in context.snapshot.outfit.actuators}
    contributions = []
    for key, binding in sorted(binding_by_id.items()):
        engine = by_id[key]
        module = modules[key]
        efficiency = finite_number(
            module.function_efficiency(ACTUATOR_FUNCTION_BY_CATEGORY[binding.actuator_category]),
            "$.runtime.efficiency", 0,
        )
        expected_available = runtime.fuel_available and efficiency > EPS
        if (key in available) != expected_available:
            raise ContractError("actual_propulsion.availability", "$.runtime.actuators", "执行器与当前运行时可用性不一致")
        actuator, use = available.get(key), use_by_id.get(key)
        if actuator is not None:
            base = static[key]
            if (actuator.category, actuator.application_point_m, actuator.direction_body, actuator.response_time_s) != (
                base.category, base.application_point_m, base.direction_body, base.response_time_s):
                raise ContractError("actual_propulsion.runtime_geometry", "$.runtime.actuators", "运行时不得改变静态执行器几何")
            _close(actuator.thrust_n, base.thrust_n * efficiency, "$.runtime.actuators.thrust_n")
            _close(actuator.torque_about_cic_n_m, base.torque_about_cic_n_m * efficiency, "$.runtime.actuators.torque")
            _close(actuator.fuel_units_per_s, base.fuel_units_per_s * efficiency, "$.runtime.actuators.fuel")
        fraction = engine.actual_output_percent / 100
        contributions.append(ActualActuatorContribution(
            key, binding.command_channels[0], engine.target_output_percent, engine.actual_output_percent,
            expected_available, efficiency, actuator.thrust_n if actuator else 0.0,
            use.output_scale if use else 0.0,
            tuple(component * fraction for component in use.force_body_n) if use else (0.0, 0.0),
            use.torque_about_cic_n_m * fraction if use else 0.0,
            use.fuel_units_per_s * fraction if use else 0.0,
        ))
    rows = tuple(contributions)
    request = ActualActuationRequest(context.snapshot.source_sha256, runtime.source_sha256, step,
        (fsum(c.force_body_n[0] for c in rows), fsum(c.force_body_n[1] for c in rows)),
        fsum(c.torque_n_m for c in rows), fsum(c.fuel_units_per_s for c in rows))
    return ActualPropulsionAggregation(context.scene_id, context.ship_id, request, rows)
