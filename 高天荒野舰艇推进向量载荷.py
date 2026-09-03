"""d3.2：精确资源/runtime 的只读整舰向量采样；不推进运动或场景。"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import fsum
from typing import Mapping

from 高天荒野舰艇数据契约 import ContractError, canonical_sha256
from 高天荒野舰艇实际推进合同 import ActualActuationRequest, finite_number
from 高天荒野舰艇实际推进聚合器 import ActualPropulsionContext, aggregate_actual_propulsion
from 高天荒野舰艇推进状态合同 import EngineRuntimeState, migrate_engine_runtime_state_from_module_mode
from 高天荒野舰艇推进安全判定器 import PropulsionHardAvailability
from 高天荒野舰艇推进时间内核 import PropulsionTimeCommand
from 高天荒野舰艇受控推进时间边界 import preview_governed_propulsion_time_boundary
from 高天荒野舰艇推进通道合同 import OPPOSING_CHANNEL_PAIRS
from 高天荒野舰艇定向推进控制桥 import DirectionalPropulsionControlInput
from 高天荒野舰艇整舰推进安全判定 import (
    PropulsionOutputVector, WholeShipPropulsionLoadSample, WholeShipActuatorBoundary, _require,
)
import 高天荒野舰艇战术机动求解器 as dynamics


@dataclass(frozen=True)
class _ActuatorLoadBasis:
    actuator_instance_id: str
    command_channel: str
    force_body_n: tuple[float, float]
    torque_n_m: float
    fuel_units_per_s: float

    def to_dict(self):
        return {"actuator_instance_id": self.actuator_instance_id, "command_channel": self.command_channel,
            "force_body_n": list(self.force_body_n), "torque_n_m": self.torque_n_m, "fuel_units_per_s": self.fuel_units_per_s}


@dataclass(frozen=True)
class WholeShipVectorLoadSampler:
    context: ActualPropulsionContext
    model: dynamics.TacticalShipModel
    motion: dynamics.TacticalMotionState
    source_sha256: str = field(init=False)
    _basis: tuple[_ActuatorLoadBasis, ...] = field(init=False, repr=False)
    _drag_world_n: dynamics.Vec2 = field(init=False, repr=False)

    def __post_init__(self) -> None:
        _require(isinstance(self.context, ActualPropulsionContext) and isinstance(self.model, dynamics.TacticalShipModel)
            and isinstance(self.motion, dynamics.TacticalMotionState), "load_context", "必须提供精确资源、模型和运动状态")
        self.context.__post_init__()
        runtime, motion = self.model.runtime, self.motion
        _require(type(motion.fixed_step_index) is int and motion.fixed_step_index >= 0, "load_step", "采样边界必须为非负整数")
        for key in ("heading_rad", "yaw_rate_radps", "hull_integrity_fraction", "fuel_units"):
            finite_number(getattr(motion, key), f"$.motion.{key}")
        for vector in (motion.position_world_m, motion.velocity_world_mps):
            finite_number(vector.x, "$.motion.vector.x")
            finite_number(vector.y, "$.motion.vector.y")
        _require(motion.fuel_units >= 0 and 0 <= motion.hull_integrity_fraction <= 1, "motion_range", "油量或船壳状态越界")
        _require(motion.fuel_units == runtime.instance_snapshot.operational_state.fuel_units
            and motion.hull_integrity_fraction == runtime.current_hull_integrity_fraction
            and motion.height_layer == runtime.height_layer, "stale_load_runtime", "燃料、船壳或层级变化后须重建 runtime")
        _require(self.model == dynamics.build_tactical_ship_model(runtime, self.context.snapshot,
            environment=self.model.environment, tuning=self.model.tuning), "load_model", "模型几何/指纹与精确资源不符")
        for key in ("current_mass_kg", "current_inertia_kg_m2", "safe_longitudinal_mps2", "safe_lateral_mps2"):
            _require(finite_number(getattr(runtime, key), f"$.runtime.{key}", 0) > 0, "load_runtime", "质量/惯量/载荷标定须为正")
        dt = finite_number(self.model.tuning.fixed_step_s, "$.dt", 0)
        _require(abs(dt - 1 / 60) < 1e-12, "load_fixed_step", "受控时间核只支持 60 Hz")
        _require(finite_number(self.model.tuning.gravity_mps2, "$.gravity", 0) > 0, "load_gravity", "重力标定须为正")
        finite_number(self.model.tuning.turn_scale, "$.turn_scale", 0)

        # 零值校验探针只验证既有聚合器的身份、runtime 和配平 use 合同。
        # 它不是引擎就绪判定，既不读取也不覆盖真实时间状态；候选物理只读取下方系数。
        zero_probe = tuple(migrate_engine_runtime_state_from_module_mode(b.actuator_instance_id,
            b.actuator_category, "active", motion.fixed_step_index) for b in self.context.bindings)
        aggregate_actual_propulsion(self.context, runtime, zero_probe, motion.fixed_step_index)
        uses = {u.instance_id: u for g in (*runtime.actuator_aggregation.main_directions,
            *runtime.actuator_aggregation.turning_directions) for u in g.uses}
        rows = []
        for b in self.context.bindings:
            use = uses.get(b.actuator_instance_id)
            rows.append(_ActuatorLoadBasis(b.actuator_instance_id, b.command_channels[0],
                tuple(use.force_body_n) if use else (0.0, 0.0), use.torque_about_cic_n_m if use else 0.0,
                use.fuel_units_per_s if use else 0.0))
        rows = tuple(sorted(rows, key=lambda row: row.actuator_instance_id))
        drag = dynamics.calculate_tactical_drag(self.model, motion).force_world_n
        finite_number(drag.x, "$.drag.x")
        finite_number(drag.y, "$.drag.y")
        object.__setattr__(self, "_basis", rows)
        object.__setattr__(self, "_drag_world_n", drag)
        object.__setattr__(self, "source_sha256", canonical_sha256({
            "policy": "gaotian.propulsion-load/runtime-basis-actual-vector/v1",
            "scene_id": self.context.scene_id, "ship_id": self.context.ship_id,
            "catalog_sha256": canonical_sha256(self.context.catalog), "model": self.model.to_dict(),
            "motion": motion.to_dict(), "structure_points": [p.to_list() for p in self.model.structure_points_body_m],
            "aerodynamic_cache_sha256": runtime.aerodynamic_cache_sha256,
            "basis": [r.to_dict() for r in rows], "drag_world_n": drag.to_list()}))

    def request_for(self, vector: PropulsionOutputVector) -> tuple[ActualActuationRequest, float]:
        _require(isinstance(vector, PropulsionOutputVector), "load_vector", "采样需要严格完整向量")
        _require(tuple(key for key, _ in vector.outputs) == tuple(row.actuator_instance_id for row in self._basis),
            "load_vector_ids", "候选必须保留本舰全部执行器身份")
        active = {row.command_channel for row, (_, p) in zip(self._basis, vector.outputs) if p}
        _require(not any(a in active and b in active for a, b in OPPOSING_CHANNEL_PAIRS),
            "direction_interlock_unwired", "对向交付仍待 d4")
        scaled = [(row, p / 100) for row, (_, p) in zip(self._basis, vector.outputs)]
        request = ActualActuationRequest(self.model.derived_snapshot_sha256, self.model.runtime_parameters_sha256,
            self.motion.fixed_step_index,
            tuple(fsum(row.force_body_n[axis] * p for row, p in scaled) for axis in (0, 1)),
            fsum(row.torque_n_m * p for row, p in scaled), fsum(row.fuel_units_per_s * p for row, p in scaled))
        fuel = finite_number(request.fuel_units_per_s * self.model.tuning.fixed_step_s, "$.requested_fuel", 0)
        fraction = min(1.0, self.motion.fuel_units / fuel) if fuel > 0 else 1.0
        return request, fraction

    def __call__(self, vector: PropulsionOutputVector) -> WholeShipPropulsionLoadSample:
        request, fraction = self.request_for(vector)
        actuation = dynamics.AllocatedActuation(dynamics.Vec2(*request.force_body_n), dynamics.Vec2(),
            request.torque_n_m, 0.0, 0.0, request.fuel_units_per_s)
        try:
            metrics = dynamics._load_metrics(self.model, self.motion, actuation, self._drag_world_n,
                fraction, self.model.tuning.fixed_step_s)
        except (OverflowError, ZeroDivisionError) as error:
            raise ContractError("vector_safety.load_range", "$", "候选产生非有限载荷") from error
        return WholeShipPropulsionLoadSample(self.source_sha256, vector, metrics.structure_ratio, metrics.crew_g)


def prepare_whole_ship_actuator_boundaries(context: ActualPropulsionContext,
    engines: tuple[EngineRuntimeState, ...], controls: DirectionalPropulsionControlInput,
    hard_availability: Mapping[str, PropulsionHardAvailability], fixed_step_index: int,
) -> tuple[WholeShipActuatorBoundary, ...]:
    """使用精确绑定生成时间预览，不为缺少的硬可用性猜默认值。"""
    _require(isinstance(context, ActualPropulsionContext) and isinstance(controls, DirectionalPropulsionControlInput),
        "prepare_context", "资源或控制类型不符")
    _require(isinstance(engines, tuple) and all(isinstance(e, EngineRuntimeState) for e in engines),
        "prepare_engines", "须提供完整引擎状态")
    by_id = {e.actuator_instance_id: e for e in engines}
    expected = {b.actuator_instance_id for b in context.bindings}
    _require(len(by_id) == len(engines) and set(by_id) == expected and isinstance(hard_availability, Mapping)
        and set(hard_availability) == expected, "prepare_ids", "引擎与硬上限必须精确覆盖本舰执行器")
    commands = {c.command_channel: c for c in controls.channel_commands}
    result = []
    for binding in sorted(context.bindings, key=lambda b: b.actuator_instance_id):
        engine = by_id[binding.actuator_instance_id]
        _require(len(binding.command_channels) == 1 and engine.actuator_category == binding.actuator_category,
            "prepare_binding", "绑定类别或用途不唯一")
        channel = commands[binding.command_channels[0]]
        capability = context.catalog.module(binding.prototype).capability
        preview = preview_governed_propulsion_time_boundary(engine, capability, fixed_step_index,
            PropulsionTimeCommand(channel.commanded_notch, channel.target_output_percent))
        result.append(WholeShipActuatorBoundary(channel.command_channel, capability, preview,
            hard_availability[binding.actuator_instance_id]))
    return tuple(result)
