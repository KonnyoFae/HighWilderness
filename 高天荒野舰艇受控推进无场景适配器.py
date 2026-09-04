"""d3.3 第二阶段：无场景副作用的受控推进开边界、积分与收边界适配。

本模块不导入统一场景。它只组合已经冻结的 d3.1 时间提交、d3.2 整舰安全
判定和实际推进积分，并把结果装入 d3.3 第一阶段的严格记录合同。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from math import isfinite
from typing import Any

from 高天荒野舰艇数据契约 import ContractError, canonical_sha256
from 高天荒野舰艇实际推进合同 import ActualActuationRequest
from 高天荒野舰艇实际推进聚合器 import (
    ActualPropulsionAggregation,
    ActualPropulsionContext,
    aggregate_actual_propulsion,
)
from 高天荒野舰艇推进安全判定器 import PropulsionHardAvailability, PropulsionSafetyProfile
from 高天荒野舰艇推进状态合同 import TacticalPropulsionState
from 高天荒野舰艇推进时间内核 import (
    PropulsionTimeCommand,
    validate_committed_propulsion_time_state,
)
from 高天荒野舰艇推进通道合同 import (
    DIRECTIONAL_CHANNELS,
    DIRECTIONAL_STATE_INTERFACE_ID,
    TRANSLATION_CHANNELS,
    DirectionalPropulsionGovernorState,
)
from 高天荒野舰艇定向推进控制桥 import (
    DirectionalPropulsionControlInput,
    automatic_linear_brake_control,
    directional_control,
    validate_directional_control_transition,
)
from 高天荒野舰艇受控推进时间边界 import (
    commit_governed_propulsion_time_boundary,
    preview_governed_propulsion_time_boundary,
)
from 高天荒野舰艇整舰推进安全判定 import (
    PropulsionOutputVector,
    WholeShipPropulsionLoadSample,
    WholeShipPropulsionSafetyResult,
    evaluate_whole_ship_propulsion_safety,
)
from 高天荒野舰艇推进向量载荷 import (
    WholeShipVectorLoadSampler,
    prepare_whole_ship_actuator_boundaries,
)
from 高天荒野舰艇受控推进场景合同 import (
    GovernedActualTacticalStepDiagnostics,
    GovernedPropulsionClosingRecord,
    GovernedPropulsionOpeningRecord,
    GovernedScenePropulsionSafetyEvent,
    PROPULSION_DELIVERY_STATUSES,
)
from 高天荒野舰艇运行时参数编译器 import RuntimeShipParameters
import 高天荒野舰艇战术机动求解器 as dynamics


ACTIVE_DELIVERY_STATUSES = frozenset(
    {"delivered", "suppressed_falling", "suppressed_uncommanded"}
)


def _require(condition: bool, code: str, path: str, message: str) -> None:
    if not condition:
        raise ContractError(f"governed_adapter.{code}", path, message)


def _step(value: Any, path: str) -> int:
    _require(type(value) is int and value >= 0, "fixed_step", path, "必须是非负整数")
    return value


def _delivery_status(value: Any, path: str = "$.propulsion_delivery_status") -> str:
    _require(isinstance(value, str) and value in PROPULSION_DELIVERY_STATUSES, "delivery_status", path, "推进交付状态非法")
    _require(value in ACTIVE_DELIVERY_STATUSES, "exited_frozen", path, "已退出舰必须冻结，不得进入受控适配器")
    return value


def _validate_context(context: ActualPropulsionContext) -> None:
    _require(isinstance(context, ActualPropulsionContext), "context", "$.context", "必须提供精确推进资源上下文")
    context.__post_init__()


def _binding_maps(context: ActualPropulsionContext) -> tuple[dict[str, Any], dict[str, Any]]:
    bindings = {item.actuator_instance_id: item for item in context.bindings}
    _require(
        len(bindings) == len(context.bindings),
        "binding_ids",
        "$.context.bindings",
        "执行器绑定必须唯一",
    )
    capabilities = {
        key: context.catalog.module(binding.prototype).capability
        for key, binding in bindings.items()
    }
    return bindings, capabilities


def _validate_state(
    context: ActualPropulsionContext,
    state: TacticalPropulsionState,
    control: DirectionalPropulsionControlInput,
    *,
    governor_clock: int | None,
    engine_boundary: int,
) -> None:
    _step(engine_boundary, "$.engine_boundary")
    _validate_context(context)
    _require(isinstance(state, TacticalPropulsionState), "state", "$.propulsion_state", "必须提供严格推进状态")
    TacticalPropulsionState.parse(state.to_dict(), "$.propulsion_state")
    _require(
        state.interface_id == DIRECTIONAL_STATE_INTERFACE_ID,
        "state_interface",
        "$.propulsion_state.interface",
        "只接受定向推进状态",
    )
    _require(
        isinstance(control, DirectionalPropulsionControlInput),
        "control",
        "$.propulsion_control",
        "必须提供严格定向控制",
    )
    DirectionalPropulsionControlInput.parse(control.to_dict(), "$.propulsion_control")
    bindings, capabilities = _binding_maps(context)
    engines = {item.actuator_instance_id: item for item in state.engines}
    _require(
        len(engines) == len(state.engines) and set(engines) == set(bindings),
        "engine_set",
        "$.propulsion_state.engines",
        "引擎状态必须精确覆盖静态执行器",
    )
    _require(
        tuple(item.command_channel for item in state.governors) == DIRECTIONAL_CHANNELS
        and all(type(item) is DirectionalPropulsionGovernorState for item in state.governors),
        "governors",
        "$.propulsion_state.governors",
        "必须按规范顺序保存六通道 governor",
    )
    _require(
        tuple(item.command for item in state.governors) == control.channel_commands,
        "control_chain",
        "$.propulsion_state.governors",
        "持久控制必须与 governor 命令一致",
    )
    _require(
        all(item.last_evaluated_step_index == governor_clock for item in state.governors),
        "governor_clock",
        "$.propulsion_state.governors",
        "六通道 governor 时钟不属于所需权威边界",
    )
    governors = {item.command_channel: item for item in state.governors}
    for actuator_id, engine in engines.items():
        binding = bindings[actuator_id]
        _require(engine.phase != "tripped", "tripped_unwired", f"$.propulsion_state.engines.{actuator_id}", "d4 前拒绝 tripped 状态")
        _require(
            engine.actuator_category == binding.actuator_category and len(binding.command_channels) == 1,
            "engine_binding",
            f"$.propulsion_state.engines.{actuator_id}",
            "执行器类别或物理用途与精确绑定不一致",
        )
        validate_committed_propulsion_time_state(engine, capabilities[actuator_id], engine_boundary)
        governor = governors[binding.command_channels[0]]
        _require(
            engine.commanded_notch == governor.command.commanded_notch
            and engine.target_output_percent
            <= min(governor.command.requested_percent, governor.safety_ceiling_percent),
            "engine_governor_chain",
            f"$.propulsion_state.engines.{actuator_id}",
            "执行器目标不得越过持久命令或软上限",
        )


def _actual_output_by_channel(
    context: ActualPropulsionContext, state: TacticalPropulsionState,
) -> dict[str, int]:
    bindings, _ = _binding_maps(context)
    result = {channel: 0 for channel in DIRECTIONAL_CHANNELS}
    for engine in state.engines:
        channel = bindings[engine.actuator_instance_id].command_channels[0]
        result[channel] = max(result[channel], engine.actual_output_percent)
    return result


def validate_governed_propulsion_state(
    context: ActualPropulsionContext,
    state: TacticalPropulsionState,
    control: DirectionalPropulsionControlInput,
    *,
    governor_clock: int,
    engine_boundary: int,
) -> None:
    """供场景与存档层复用同一份受控状态/资源门禁。"""
    _validate_state(
        context,
        state,
        control,
        governor_clock=_step(governor_clock, "$.governor_clock"),
        engine_boundary=engine_boundary,
    )


def select_governed_propulsion_control(
    context: ActualPropulsionContext,
    state: TacticalPropulsionState,
    previous: DirectionalPropulsionControlInput,
    requested: DirectionalPropulsionControlInput | None,
    *,
    velocity_body: tuple[float, float],
    command_available: bool,
    fixed_step_index: int,
) -> tuple[DirectionalPropulsionControlInput, tuple[str, ...]]:
    """选择本步持久控制；保持自动制动、缺失通道和反向冲突的旧场景语义。"""
    n = _step(fixed_step_index, "$.fixed_step_index")
    _require(type(command_available) is bool, "command_available", "$.command_available", "命令可用性必须为布尔值")
    _require(
        isinstance(velocity_body, tuple)
        and len(velocity_body) == 2
        and all(
            not isinstance(value, bool)
            and isinstance(value, (int, float))
            and isfinite(float(value))
            for value in velocity_body
        ),
        "velocity_body",
        "$.velocity_body",
        "体轴速度必须是两个有限数",
    )
    _validate_state(context, state, previous, governor_clock=n, engine_boundary=n)
    control = previous if requested is None else requested
    _require(
        isinstance(control, DirectionalPropulsionControlInput),
        "control_type",
        "$.propulsion_controls",
        "新路径只接受定向控制",
    )
    DirectionalPropulsionControlInput.parse(control.to_dict(), "$.propulsion_controls")
    available = {
        channel
        for binding in context.bindings
        for channel in binding.command_channels
    }
    unavailable: tuple[str, ...] = ()
    if not command_available:
        control = directional_control()
    elif control.automatic_brake:
        selection = automatic_linear_brake_control(
            lateral_velocity_body_mps=velocity_body[0],
            longitudinal_velocity_body_mps=velocity_body[1],
            available_translation_channels=tuple(
                channel for channel in TRANSLATION_CHANNELS if channel in available
            ),
            overg_requested=control.overg_requested,
        )
        control, unavailable = selection.control, selection.unavailable_channels
    else:
        unavailable = tuple(
            command.command_channel
            for command in control.channel_commands
            if command.requested_percent
            and command.command_channel not in available
        )
    validate_directional_control_transition(
        previous,
        control,
        _actual_output_by_channel(context, state),
    )
    return control, unavailable


def neutral_hard_availability(
    context: ActualPropulsionContext, state: TacticalPropulsionState,
) -> dict[str, PropulsionHardAvailability]:
    """d3.3 的显式中性硬上限；不猜测 d4 故障、复位或互锁状态。"""
    _validate_context(context)
    _require(isinstance(state, TacticalPropulsionState), "state", "$.propulsion_state", "必须提供严格推进状态")
    expected = tuple(item.actuator_instance_id for item in context.bindings)
    actual = tuple(item.actuator_instance_id for item in state.engines)
    _require(actual == expected, "neutral_hard_ids", "$.propulsion_state.engines", "执行器必须按精确绑定稳定排序")
    _require(not any(item.phase == "tripped" for item in state.engines), "tripped_unwired", "$.propulsion_state.engines", "d4 前拒绝 tripped 状态")
    return {actuator_id: PropulsionHardAvailability() for actuator_id in expected}


@dataclass(frozen=True)
class GovernedPropulsionOpeningOutcome:
    state: TacticalPropulsionState
    control: DirectionalPropulsionControlInput
    record: GovernedPropulsionOpeningRecord

    def __post_init__(self) -> None:
        _require(isinstance(self.state, TacticalPropulsionState), "opening_state", "$.state", "必须提供严格开边界状态")
        _require(isinstance(self.control, DirectionalPropulsionControlInput), "opening_control", "$.control", "必须提供严格开边界控制")
        _require(isinstance(self.record, GovernedPropulsionOpeningRecord), "opening_record", "$.record", "必须提供严格开边界记录")
        _require(self.control == self.record.resulting_control, "opening_control", "$.control", "开边界结果控制不匹配")
        _require(
            self.state.engines == tuple(item.state for item in self.record.engine_results)
            and self.state.governors == self.record.resulting_governors,
            "opening_state",
            "$.state",
            "开边界状态必须逐项来自记录",
        )
        _require(
            canonical_sha256(self.state) == self.record.resulting_propulsion_state_sha256,
            "opening_hash",
            "$.record.resulting_propulsion_state_sha256",
            "开边界结果状态指纹不匹配",
        )


def commit_governed_propulsion_opening(
    context: ActualPropulsionContext,
    state: TacticalPropulsionState,
    source_control: DirectionalPropulsionControlInput,
    resulting_control: DirectionalPropulsionControlInput,
    *,
    fixed_step_index: int,
) -> GovernedPropulsionOpeningOutcome:
    """在既有软上限下接令和重排目标；不求值载荷、不推进 governor 或实际输出。"""
    n = _step(fixed_step_index, "$.fixed_step_index")
    _validate_state(context, state, source_control, governor_clock=n, engine_boundary=n)
    _require(
        isinstance(resulting_control, DirectionalPropulsionControlInput),
        "resulting_control",
        "$.resulting_control",
        "必须提供严格结果控制",
    )
    DirectionalPropulsionControlInput.parse(resulting_control.to_dict(), "$.resulting_control")
    validate_directional_control_transition(
        source_control,
        resulting_control,
        _actual_output_by_channel(context, state),
    )
    hard = neutral_hard_availability(context, state)
    bindings, capabilities = _binding_maps(context)
    engines = {item.actuator_instance_id: item for item in state.engines}
    governors = {item.command_channel: item for item in state.governors}
    commands = {item.command_channel: item for item in resulting_control.channel_commands}
    results = []
    for actuator_id in sorted(bindings):
        binding = bindings[actuator_id]
        channel = binding.command_channels[0]
        engine = engines[actuator_id]
        command = commands[channel]
        preview = preview_governed_propulsion_time_boundary(
            engine,
            capabilities[actuator_id],
            n,
            PropulsionTimeCommand(command.commanded_notch, command.target_output_percent),
        )
        _require(
            not preview.has_upstage_candidate,
            "opening_due_upstage",
            f"$.propulsion_state.engines.{actuator_id}",
            "开边界不得提交到期升阶",
        )
        effective_target = min(
            command.requested_percent,
            governors[channel].safety_ceiling_percent,
            hard[actuator_id].ceiling_percent,
        )
        result = commit_governed_propulsion_time_boundary(
            preview,
            capabilities[actuator_id],
            current_state=engine,
            fixed_step_index=n,
            effective_target_percent=effective_target,
            allow_upstage=True,
        )
        _require(
            result.state.actual_output_percent == engine.actual_output_percent,
            "opening_actual_output",
            f"$.propulsion_state.engines.{actuator_id}",
            "开边界只能重排目标，不能改变本区间实际输出",
        )
        results.append(result)
    record = GovernedPropulsionOpeningRecord(
        context.ship_id,
        n,
        canonical_sha256(state),
        "0" * 64,
        source_control,
        resulting_control,
        state.governors,
        resulting_control.channel_commands,
        tuple(results),
    )
    resulting_state = replace(
        state,
        engines=tuple(item.state for item in results),
        governors=record.resulting_governors,
    )
    record = replace(record, resulting_propulsion_state_sha256=canonical_sha256(resulting_state))
    _validate_state(context, resulting_state, resulting_control, governor_clock=n, engine_boundary=n)
    return GovernedPropulsionOpeningOutcome(resulting_state, resulting_control, record)


@dataclass(frozen=True)
class GovernedPropulsionDeliveryLoadSampler:
    physical_sampler: WholeShipVectorLoadSampler
    delivery_status: str
    source_sha256: str = field(init=False)
    _suppressed_sample: WholeShipPropulsionLoadSample | None = field(init=False, repr=False)

    def __post_init__(self) -> None:
        _require(isinstance(self.physical_sampler, WholeShipVectorLoadSampler), "load_sampler", "$.physical_sampler", "必须提供真实整舰载荷采样器")
        status = _delivery_status(self.delivery_status)
        if status == "delivered":
            object.__setattr__(self, "source_sha256", self.physical_sampler.source_sha256)
            object.__setattr__(self, "_suppressed_sample", None)
            return
        source_sha256 = canonical_sha256(
            {
                "policy": "gaotian.propulsion-load/lifecycle-suppressed-zero-delivery/v1",
                "physical_load_context_sha256": self.physical_sampler.source_sha256,
                "propulsion_delivery_status": status,
            }
        )
        zero = PropulsionOutputVector(
            tuple(
                (item.actuator_instance_id, 0)
                for item in self.physical_sampler.context.bindings
            )
        )
        object.__setattr__(self, "source_sha256", source_sha256)
        object.__setattr__(self, "_suppressed_sample", self.physical_sampler(zero))

    def __call__(self, vector: PropulsionOutputVector) -> WholeShipPropulsionLoadSample:
        if self.delivery_status == "delivered":
            return self.physical_sampler(vector)
        self.physical_sampler.request_for(vector)
        assert self._suppressed_sample is not None
        return WholeShipPropulsionLoadSample(
            self.source_sha256,
            vector,
            self._suppressed_sample.structure_ratio,
            self._suppressed_sample.crew_g,
        )


def governed_propulsion_delivery_request(
    request: ActualActuationRequest, propulsion_delivery_status: str,
) -> ActualActuationRequest:
    status = _delivery_status(propulsion_delivery_status)
    _require(isinstance(request, ActualActuationRequest), "request", "$.request", "必须提供实际推进请求")
    if status == "delivered":
        return request
    return replace(request, force_body_n=(0.0, 0.0), torque_n_m=0.0, fuel_units_per_s=0.0)


@dataclass(frozen=True)
class GovernedPropulsionIntervalOutcome:
    aggregation: ActualPropulsionAggregation
    delivered_request: ActualActuationRequest
    resulting_motion: dynamics.TacticalMotionState
    diagnostics: GovernedActualTacticalStepDiagnostics
    propulsion_delivery_status: str

    def __post_init__(self) -> None:
        _delivery_status(self.propulsion_delivery_status)
        _require(isinstance(self.aggregation, ActualPropulsionAggregation), "aggregation", "$.aggregation", "缺少实际推进聚合")
        _require(isinstance(self.delivered_request, ActualActuationRequest), "delivered_request", "$.delivered_request", "缺少最终交付请求")
        _require(isinstance(self.resulting_motion, dynamics.TacticalMotionState), "resulting_motion", "$.resulting_motion", "缺少结果运动状态")
        _require(isinstance(self.diagnostics, GovernedActualTacticalStepDiagnostics), "diagnostics", "$.diagnostics", "缺少受控积分诊断")
        _require(self.diagnostics.diagnostic.request == self.delivered_request, "diagnostic_request", "$.diagnostics", "诊断必须绑定最终交付请求")
        _require(
            self.diagnostics.diagnostic.resulting_fixed_step_index == self.resulting_motion.fixed_step_index,
            "diagnostic_motion",
            "$.resulting_motion",
            "积分诊断与结果运动步号不一致",
        )


def integrate_governed_propulsion_interval(
    context: ActualPropulsionContext,
    runtime: RuntimeShipParameters,
    model: dynamics.TacticalShipModel,
    motion: dynamics.TacticalMotionState,
    propulsion_state: TacticalPropulsionState,
    propulsion_control: DirectionalPropulsionControlInput,
    *,
    propulsion_delivery_status: str,
) -> GovernedPropulsionIntervalOutcome:
    """使用开边界后的已保护实际向量积分一个固定步；生命周期抑制只归零交付。"""
    status = _delivery_status(propulsion_delivery_status)
    _require(isinstance(motion, dynamics.TacticalMotionState), "motion", "$.motion", "必须提供严格运动状态")
    n = _step(motion.fixed_step_index, "$.motion.fixed_step_index")
    _validate_state(context, propulsion_state, propulsion_control, governor_clock=n, engine_boundary=n)
    _require(
        isinstance(runtime, RuntimeShipParameters)
        and isinstance(model, dynamics.TacticalShipModel)
        and model.runtime == runtime,
        "runtime_model",
        "$.model",
        "模型必须绑定当前精确 runtime",
    )
    aggregation = aggregate_actual_propulsion(context, runtime, propulsion_state.engines, n)
    delivered_request = governed_propulsion_delivery_request(aggregation.request, status)
    resulting_motion, diagnostic = dynamics.integrate_actual_tactical_step(
        model,
        motion,
        delivered_request,
    )
    wrapped = GovernedActualTacticalStepDiagnostics(
        diagnostic,
        canonical_sha256(propulsion_state),
        canonical_sha256([item.to_dict() for item in propulsion_state.governors]),
    )
    return GovernedPropulsionIntervalOutcome(
        aggregation,
        delivered_request,
        resulting_motion,
        wrapped,
        status,
    )


def _evaluate_safety_boundary(
    context: ActualPropulsionContext,
    state: TacticalPropulsionState,
    control: DirectionalPropulsionControlInput,
    safety_profile: PropulsionSafetyProfile,
    model: dynamics.TacticalShipModel,
    motion: dynamics.TacticalMotionState,
    *,
    fixed_step_index: int,
    propulsion_delivery_status: str,
    crew_safety_lock_enabled: bool,
) -> WholeShipPropulsionSafetyResult:
    status = _delivery_status(propulsion_delivery_status)
    n = _step(fixed_step_index, "$.fixed_step_index")
    _require(motion.fixed_step_index == n, "motion_step", "$.motion.fixed_step_index", "载荷运动状态必须属于当前安全边界")
    _require(
        isinstance(model, dynamics.TacticalShipModel),
        "model",
        "$.model",
        "必须提供最终同步物理模型",
    )
    hard = neutral_hard_availability(context, state)
    physical = WholeShipVectorLoadSampler(context, model, motion)
    sampler = GovernedPropulsionDeliveryLoadSampler(physical, status)
    actuators = prepare_whole_ship_actuator_boundaries(
        context,
        state.engines,
        control,
        hard,
        n,
    )
    return evaluate_whole_ship_propulsion_safety(
        safety_profile,
        state.governors,
        control,
        actuators,
        fixed_step_index=n,
        load_context_sha256=sampler.source_sha256,
        load_evaluator=sampler,
        crew_safety_lock_enabled=crew_safety_lock_enabled,
    )


@dataclass(frozen=True)
class GovernedPropulsionInitializationOutcome:
    state: TacticalPropulsionState
    safety_result: WholeShipPropulsionSafetyResult

    def __post_init__(self) -> None:
        _require(isinstance(self.state, TacticalPropulsionState), "initialization_state", "$.state", "必须提供严格受控初态")
        _require(isinstance(self.safety_result, WholeShipPropulsionSafetyResult), "initialization_result", "$.safety_result", "必须提供初态安全结果")
        _require(
            self.state.engines == tuple(item.state for item in self.safety_result.engine_results)
            and self.state.governors == self.safety_result.governors,
            "initialization_state",
            "$.state",
            "受控初态必须来自一次完整安全提交",
        )


def initialize_governed_propulsion_state(
    context: ActualPropulsionContext,
    state: TacticalPropulsionState,
    control: DirectionalPropulsionControlInput,
    safety_profile: PropulsionSafetyProfile,
    model: dynamics.TacticalShipModel,
    motion: dynamics.TacticalMotionState,
    *,
    crew_safety_lock_enabled: bool,
) -> GovernedPropulsionInitializationOutcome:
    """在边界 0 初始化 governor 时钟；仅供指纹锁定的具名场景构建器消费。"""
    _require(motion.fixed_step_index == 0, "initial_motion_step", "$.motion.fixed_step_index", "受控初态只能在边界 0 建立")
    _validate_state(context, state, control, governor_clock=None, engine_boundary=0)
    for governor in state.governors:
        _require(
            governor == DirectionalPropulsionGovernorState.initial(governor.command_channel),
            "initial_governor_history",
            "$.propulsion_state.governors",
            "受控初态只接受未求值、无限幅、零 revision governor",
        )
    result = _evaluate_safety_boundary(
        context,
        state,
        control,
        safety_profile,
        model,
        motion,
        fixed_step_index=0,
        propulsion_delivery_status="delivered",
        crew_safety_lock_enabled=crew_safety_lock_enabled,
    )
    initialized = replace(
        state,
        engines=tuple(item.state for item in result.engine_results),
        governors=result.governors,
    )
    _validate_state(context, initialized, control, governor_clock=0, engine_boundary=0)
    return GovernedPropulsionInitializationOutcome(initialized, result)


@dataclass(frozen=True)
class GovernedPropulsionClosingOutcome:
    state: TacticalPropulsionState
    record: GovernedPropulsionClosingRecord
    safety_events: tuple[GovernedScenePropulsionSafetyEvent, ...]

    def __post_init__(self) -> None:
        _require(isinstance(self.state, TacticalPropulsionState), "closing_state", "$.state", "必须提供严格收边界状态")
        _require(isinstance(self.record, GovernedPropulsionClosingRecord), "closing_record", "$.record", "必须提供严格收边界记录")
        _require(isinstance(self.safety_events, tuple), "safety_events", "$.safety_events", "安全事件必须是不可变序列")
        _require(
            self.state.engines == tuple(item.state for item in self.record.safety_result.engine_results)
            and self.state.governors == self.record.safety_result.governors,
            "closing_state",
            "$.state",
            "收边界状态必须逐项来自安全结果",
        )
        _require(
            canonical_sha256(self.state) == self.record.resulting_propulsion_state_sha256,
            "closing_hash",
            "$.record.resulting_propulsion_state_sha256",
            "收边界结果状态指纹不匹配",
        )
        expected = tuple(
            GovernedScenePropulsionSafetyEvent(self.record.ship_id, "closing", item)
            for item in self.record.safety_result.event_intents
        )
        _require(
            self.safety_events == tuple(sorted(expected, key=lambda item: item.sort_key)),
            "safety_events",
            "$.safety_events",
            "场景安全事件必须精确来自最终通道意图",
        )


def evaluate_governed_propulsion_closing(
    context: ActualPropulsionContext,
    state: TacticalPropulsionState,
    control: DirectionalPropulsionControlInput,
    safety_profile: PropulsionSafetyProfile,
    final_runtime: RuntimeShipParameters,
    final_model: dynamics.TacticalShipModel,
    final_motion: dynamics.TacticalMotionState,
    *,
    fixed_step_index: int,
    propulsion_delivery_status: str,
    crew_safety_lock_enabled: bool,
) -> GovernedPropulsionClosingOutcome:
    """在 n+1 最终 runtime、运动和生命周期口径上执行唯一一次整舰安全提交。"""
    n = _step(fixed_step_index, "$.fixed_step_index")
    _require(n > 0, "closing_step", "$.fixed_step_index", "收边界必须晚于源边界")
    status = _delivery_status(propulsion_delivery_status)
    _validate_state(context, state, control, governor_clock=n - 1, engine_boundary=n - 1)
    _require(
        isinstance(final_runtime, RuntimeShipParameters)
        and isinstance(final_model, dynamics.TacticalShipModel)
        and final_model.runtime == final_runtime,
        "final_runtime_model",
        "$.final_model",
        "收边界模型必须绑定最终同步 runtime",
    )
    safety = _evaluate_safety_boundary(
        context,
        state,
        control,
        safety_profile,
        final_model,
        final_motion,
        fixed_step_index=n,
        propulsion_delivery_status=status,
        crew_safety_lock_enabled=crew_safety_lock_enabled,
    )
    resulting_state = replace(
        state,
        engines=tuple(item.state for item in safety.engine_results),
        governors=safety.governors,
    )
    record = GovernedPropulsionClosingRecord(
        context.ship_id,
        n,
        canonical_sha256(state),
        canonical_sha256(resulting_state),
        final_runtime.source_sha256,
        canonical_sha256(final_motion),
        status,
        crew_safety_lock_enabled,
        safety,
    )
    events = tuple(
        sorted(
            (
                GovernedScenePropulsionSafetyEvent(context.ship_id, "closing", item)
                for item in safety.event_intents
            ),
            key=lambda item: item.sort_key,
        )
    )
    _validate_state(context, resulting_state, control, governor_clock=n, engine_boundary=n)
    return GovernedPropulsionClosingOutcome(resulting_state, record, events)


def validate_governed_propulsion_opening_replay(
    outcome: GovernedPropulsionOpeningOutcome,
    context: ActualPropulsionContext,
    source_state: TacticalPropulsionState,
    source_control: DirectionalPropulsionControlInput,
    resulting_control: DirectionalPropulsionControlInput,
    *,
    fixed_step_index: int,
) -> None:
    _require(isinstance(outcome, GovernedPropulsionOpeningOutcome), "opening_outcome", "$.outcome", "开边界结果类型错误")
    expected = commit_governed_propulsion_opening(
        context,
        source_state,
        source_control,
        resulting_control,
        fixed_step_index=fixed_step_index,
    )
    _require(outcome == expected, "opening_replay", "$.outcome", "开边界记录与精确输入重放不一致")


def validate_governed_propulsion_interval_replay(
    outcome: GovernedPropulsionIntervalOutcome,
    context: ActualPropulsionContext,
    runtime: RuntimeShipParameters,
    model: dynamics.TacticalShipModel,
    motion: dynamics.TacticalMotionState,
    propulsion_state: TacticalPropulsionState,
    propulsion_control: DirectionalPropulsionControlInput,
    *,
    propulsion_delivery_status: str,
) -> None:
    _require(isinstance(outcome, GovernedPropulsionIntervalOutcome), "interval_outcome", "$.outcome", "积分结果类型错误")
    expected = integrate_governed_propulsion_interval(
        context,
        runtime,
        model,
        motion,
        propulsion_state,
        propulsion_control,
        propulsion_delivery_status=propulsion_delivery_status,
    )
    _require(outcome == expected, "interval_replay", "$.outcome", "积分结果与精确输入重放不一致")


def validate_governed_propulsion_closing_replay(
    outcome: GovernedPropulsionClosingOutcome,
    context: ActualPropulsionContext,
    source_state: TacticalPropulsionState,
    control: DirectionalPropulsionControlInput,
    safety_profile: PropulsionSafetyProfile,
    final_runtime: RuntimeShipParameters,
    final_model: dynamics.TacticalShipModel,
    final_motion: dynamics.TacticalMotionState,
    *,
    fixed_step_index: int,
    propulsion_delivery_status: str,
    crew_safety_lock_enabled: bool,
) -> None:
    _require(isinstance(outcome, GovernedPropulsionClosingOutcome), "closing_outcome", "$.outcome", "收边界结果类型错误")
    expected = evaluate_governed_propulsion_closing(
        context,
        source_state,
        control,
        safety_profile,
        final_runtime,
        final_model,
        final_motion,
        fixed_step_index=fixed_step_index,
        propulsion_delivery_status=propulsion_delivery_status,
        crew_safety_lock_enabled=crew_safety_lock_enabled,
    )
    _require(outcome == expected, "closing_replay", "$.outcome", "收边界记录与最终物理上下文重放不一致")
