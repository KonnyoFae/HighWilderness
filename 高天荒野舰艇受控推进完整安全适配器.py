"""T0b.2d4.5：组合硬故障、方向互锁、时间、交付与软安全的无场景适配器。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from 高天荒野舰艇数据契约 import ContractError, SHA256_PATTERN, canonical_sha256
from 高天荒野舰艇实际推进聚合器 import (
    ActualPropulsionContext,
    aggregate_actual_propulsion,
)
from 高天荒野舰艇受控推进场景合同 import (
    GovernedActualTacticalStepDiagnostics,
)
from 高天荒野舰艇受控推进无场景适配器 import (
    ACTIVE_DELIVERY_STATUSES,
    GovernedPropulsionDeliveryLoadSampler,
    GovernedPropulsionIntervalOutcome,
    governed_propulsion_delivery_request,
)
from 高天荒野舰艇受控推进硬故障适配器 import (
    GovernedPropulsionHardFaultCommand,
    GovernedPropulsionHardFaultOpening,
    commit_governed_propulsion_hard_fault_opening,
)
from 高天荒野舰艇定向推进控制桥 import DirectionalPropulsionControlInput
from 高天荒野舰艇推进向量载荷 import (
    WholeShipVectorLoadSampler,
    prepare_whole_ship_actuator_boundaries,
)
from 高天荒野舰艇推进方向互锁边界 import (
    GovernedPropulsionDirectionInterlockBoundary,
    resolve_governed_propulsion_direction_interlock,
)
from 高天荒野舰艇推进安全判定器 import (
    PropulsionHardAvailability,
    PropulsionSafetyProfile,
)
from 高天荒野舰艇推进硬故障边界 import evaluate_propulsion_hard_availability
from 高天荒野舰艇推进状态合同 import (
    EngineRuntimeState,
    PropulsionStateEvent,
    TacticalPropulsionState,
)
from 高天荒野舰艇推进时间内核 import (
    PropulsionTimeCommand,
    validate_committed_propulsion_time_state,
)
from 高天荒野舰艇受控推进时间边界 import (
    GovernedPropulsionTimeResult,
    commit_governed_propulsion_time_boundary,
    preview_governed_propulsion_time_boundary,
)
from 高天荒野舰艇推进通道合同 import (
    DIRECTIONAL_CHANNELS,
    DIRECTIONAL_STATE_INTERFACE_ID,
    DirectionalPropulsionGovernorState,
)
from 高天荒野舰艇整舰推进安全判定 import (
    WholeShipPropulsionSafetyResult,
    evaluate_whole_ship_propulsion_safety,
)
from 高天荒野舰艇运行时参数编译器 import RuntimeShipParameters
import 高天荒野舰艇战术机动求解器 as dynamics


FULLY_GOVERNED_OPENING_INTERFACE_ID = (
    "gaotian.fully-governed-propulsion-opening/v1alpha1"
)
FULLY_GOVERNED_CLOSING_INTERFACE_ID = (
    "gaotian.fully-governed-propulsion-closing/v1alpha1"
)
FULLY_GOVERNED_POLICY_ID = (
    "gaotian.governed-propulsion/hard-interlock-time-delivery-soft/v1"
)


def _require(condition: bool, code: str, path: str, detail: str) -> None:
    if not condition:
        raise ContractError(f"full_governed.{code}", path, detail)


def _exact_object(value: Any, keys: set[str], path: str) -> dict[str, Any]:
    _require(
        isinstance(value, dict) and set(value) == keys,
        "object_keys",
        path,
        f"必须恰含 {sorted(keys)}",
    )
    return value


def _step(value: Any, path: str) -> int:
    _require(
        type(value) is int and value >= 0,
        "fixed_step",
        path,
        "必须是非负整数",
    )
    return value


def _sha256(value: Any, path: str) -> str:
    _require(
        isinstance(value, str) and bool(SHA256_PATTERN.fullmatch(value)),
        "sha256",
        path,
        str(value),
    )
    return value


def _binding_maps(context: ActualPropulsionContext) -> tuple[dict[str, Any], dict[str, Any]]:
    _require(
        isinstance(context, ActualPropulsionContext),
        "context",
        "$.context",
        "必须提供精确推进上下文",
    )
    context.__post_init__()
    bindings = {item.actuator_instance_id: item for item in context.bindings}
    capabilities = {
        actuator_id: context.catalog.module(binding.prototype).capability
        for actuator_id, binding in bindings.items()
    }
    return bindings, capabilities


def _validate_state(
    context: ActualPropulsionContext,
    state: TacticalPropulsionState,
    control: DirectionalPropulsionControlInput,
    *,
    governor_clock: int,
    engine_boundary: int,
) -> None:
    _require(
        isinstance(state, TacticalPropulsionState)
        and state.interface_id == DIRECTIONAL_STATE_INTERFACE_ID,
        "state",
        "$.propulsion_state",
        "必须提供定向推进状态",
    )
    TacticalPropulsionState.parse(state.to_dict(), "$.propulsion_state")
    _require(
        isinstance(control, DirectionalPropulsionControlInput),
        "control",
        "$.propulsion_control",
        "必须提供严格定向控制",
    )
    DirectionalPropulsionControlInput.parse(
        control.to_dict(), "$.propulsion_control"
    )
    bindings, capabilities = _binding_maps(context)
    engines = {item.actuator_instance_id: item for item in state.engines}
    _require(
        len(engines) == len(state.engines) and set(engines) == set(bindings),
        "engine_set",
        "$.propulsion_state.engines",
        "状态必须精确覆盖静态执行器",
    )
    _require(
        tuple(item.command_channel for item in state.governors)
        == DIRECTIONAL_CHANNELS
        and all(
            type(item) is DirectionalPropulsionGovernorState
            for item in state.governors
        ),
        "governors",
        "$.propulsion_state.governors",
        "必须按规范顺序保存六通道 governor",
    )
    _require(
        tuple(item.command for item in state.governors)
        == control.channel_commands
        and all(
            item.last_evaluated_step_index == governor_clock
            for item in state.governors
        ),
        "governor_chain",
        "$.propulsion_state.governors",
        "原始控制与 governor 命令/时钟不一致",
    )
    governors = {item.command_channel: item for item in state.governors}
    for actuator_id, engine in engines.items():
        binding = bindings[actuator_id]
        _require(
            len(binding.command_channels) == 1
            and engine.actuator_category == binding.actuator_category,
            "engine_binding",
            f"$.propulsion_state.engines.{actuator_id}",
            "执行器类别或唯一物理用途不匹配",
        )
        validate_committed_propulsion_time_state(
            engine, capabilities[actuator_id], engine_boundary
        )
        governor = governors[binding.command_channels[0]]
        _require(
            engine.commanded_notch == governor.command.commanded_notch
            and engine.target_output_percent
            <= min(
                governor.command.requested_percent,
                governor.safety_ceiling_percent,
            ),
            "engine_governor_chain",
            f"$.propulsion_state.engines.{actuator_id}",
            "执行器目标不得越过原始命令或软上限",
        )


def _hard_availability(
    opening: GovernedPropulsionHardFaultOpening,
) -> dict[str, PropulsionHardAvailability]:
    return {
        item.snapshot.actuator_instance_id: evaluate_propulsion_hard_availability(
            item.snapshot, item.state
        )
        for item in opening.hard_fault_results
    }


@dataclass(frozen=True)
class FullyGovernedPropulsionOpening:
    fixed_step_index: int
    source_state_sha256: str
    resulting_state_sha256: str
    source_control: DirectionalPropulsionControlInput
    requested_control: DirectionalPropulsionControlInput
    hard_fault_opening: GovernedPropulsionHardFaultOpening
    direction_interlock: GovernedPropulsionDirectionInterlockBoundary
    time_results: tuple[GovernedPropulsionTimeResult, ...]
    state: TacticalPropulsionState
    interface_id: str = FULLY_GOVERNED_OPENING_INTERFACE_ID
    policy_id: str = FULLY_GOVERNED_POLICY_ID

    def __post_init__(self) -> None:
        if self.interface_id != FULLY_GOVERNED_OPENING_INTERFACE_ID:
            raise ValueError("完整受控开边界 interface 非法")
        if self.policy_id != FULLY_GOVERNED_POLICY_ID:
            raise ValueError("完整受控开边界 policy 非法")
        if type(self.fixed_step_index) is not int or self.fixed_step_index < 0:
            raise ValueError("fixed_step_index 必须是非负整数")
        for value in (self.source_state_sha256, self.resulting_state_sha256):
            if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value):
                raise ValueError("完整受控开边界状态指纹非法")
        if (
            not isinstance(self.source_control, DirectionalPropulsionControlInput)
            or not isinstance(
                self.requested_control, DirectionalPropulsionControlInput
            )
            or not isinstance(
                self.hard_fault_opening, GovernedPropulsionHardFaultOpening
            )
            or not isinstance(
                self.direction_interlock,
                GovernedPropulsionDirectionInterlockBoundary,
            )
            or not isinstance(self.state, TacticalPropulsionState)
        ):
            raise ValueError("完整受控开边界组成类型非法")
        if (
            not isinstance(self.time_results, tuple)
            or any(
                not isinstance(item, GovernedPropulsionTimeResult)
                for item in self.time_results
            )
        ):
            raise ValueError("完整受控开边界必须保存严格时间结果")
        if (
            self.hard_fault_opening.fixed_step_index != self.fixed_step_index
            or self.hard_fault_opening.source_state_sha256
            != self.source_state_sha256
            or self.direction_interlock.fixed_step_index != self.fixed_step_index
            or self.direction_interlock.source_hard_fault_opening_sha256
            != canonical_sha256(self.hard_fault_opening)
            or self.direction_interlock.requested_control
            != self.requested_control
        ):
            raise ValueError("硬故障、互锁与完整开边界来源链不一致")
        if tuple(
            item.command for item in self.hard_fault_opening.state.governors
        ) != self.source_control.channel_commands:
            raise ValueError("完整受控开边界源控制未绑定既有 governor")
        source_engines = tuple(
            item.state for item in self.hard_fault_opening.hard_fault_results
        )
        if (
            tuple(item.preview.source_state for item in self.time_results)
            != (
                tuple(item.state for item in self.hard_fault_opening.emergency_cut_results)
                if self.hard_fault_opening.emergency_cut_results
                else source_engines
            )
            or self.state.engines
            != tuple(item.state for item in self.time_results)
        ):
            raise ValueError("完整受控开边界时间链未精确覆盖硬故障结果")
        expected_governors = tuple(
            replace(old, command=command)
            for old, command in zip(
                self.hard_fault_opening.state.governors,
                self.requested_control.channel_commands,
            )
        )
        if self.state.governors != expected_governors:
            raise ValueError("完整受控开边界不得改写 governor 历史")
        if canonical_sha256(self.state) != self.resulting_state_sha256:
            raise ValueError("完整受控开边界结果状态指纹不匹配")

    @classmethod
    def parse(cls, value: Any, path: str = "$") -> "FullyGovernedPropulsionOpening":
        obj = _exact_object(
            value,
            {
                "direction_interlock",
                "fixed_step_index",
                "hard_fault_opening",
                "interface",
                "policy",
                "requested_control",
                "resulting_state_sha256",
                "source_control",
                "source_state_sha256",
                "state",
                "time_results",
            },
            path,
        )
        _require(
            obj["interface"] == FULLY_GOVERNED_OPENING_INTERFACE_ID,
            "opening_interface",
            f"{path}.interface",
            str(obj["interface"]),
        )
        _require(
            obj["policy"] == FULLY_GOVERNED_POLICY_ID,
            "opening_policy",
            f"{path}.policy",
            str(obj["policy"]),
        )
        _require(
            isinstance(obj["time_results"], list),
            "time_results",
            f"{path}.time_results",
            "必须是数组",
        )
        try:
            return cls(
                _step(obj["fixed_step_index"], f"{path}.fixed_step_index"),
                _sha256(
                    obj["source_state_sha256"], f"{path}.source_state_sha256"
                ),
                _sha256(
                    obj["resulting_state_sha256"],
                    f"{path}.resulting_state_sha256",
                ),
                DirectionalPropulsionControlInput.parse(
                    obj["source_control"], f"{path}.source_control"
                ),
                DirectionalPropulsionControlInput.parse(
                    obj["requested_control"], f"{path}.requested_control"
                ),
                GovernedPropulsionHardFaultOpening.parse(
                    obj["hard_fault_opening"], f"{path}.hard_fault_opening"
                ),
                GovernedPropulsionDirectionInterlockBoundary.parse(
                    obj["direction_interlock"], f"{path}.direction_interlock"
                ),
                tuple(
                    GovernedPropulsionTimeResult.parse(
                        item, f"{path}.time_results[{index}]"
                    )
                    for index, item in enumerate(obj["time_results"])
                ),
                TacticalPropulsionState.parse(obj["state"], f"{path}.state"),
            )
        except ContractError:
            raise
        except (TypeError, ValueError) as error:
            raise ContractError(
                "full_governed.opening_invariant", path, str(error)
            ) from error

    @property
    def hard_fault_events(self) -> tuple[PropulsionStateEvent, ...]:
        return self.hard_fault_opening.propulsion_events

    @property
    def time_events(self) -> tuple[PropulsionStateEvent, ...]:
        return tuple(event for item in self.time_results for event in item.events)

    @property
    def propulsion_events(self) -> tuple[PropulsionStateEvent, ...]:
        return self.hard_fault_events + self.time_events

    def to_dict(self) -> dict[str, Any]:
        return {
            "direction_interlock": self.direction_interlock.to_dict(),
            "fixed_step_index": self.fixed_step_index,
            "hard_fault_opening": self.hard_fault_opening.to_dict(),
            "interface": self.interface_id,
            "policy": self.policy_id,
            "requested_control": self.requested_control.to_dict(),
            "resulting_state_sha256": self.resulting_state_sha256,
            "source_control": self.source_control.to_dict(),
            "source_state_sha256": self.source_state_sha256,
            "state": self.state.to_dict(),
            "time_results": [item.to_dict() for item in self.time_results],
        }


def commit_fully_governed_propulsion_opening(
    context: ActualPropulsionContext,
    runtime: RuntimeShipParameters,
    source_state: TacticalPropulsionState,
    source_control: DirectionalPropulsionControlInput,
    requested_control: DirectionalPropulsionControlInput,
    hard_fault_command: GovernedPropulsionHardFaultCommand,
    *,
    fixed_step_index: int,
) -> FullyGovernedPropulsionOpening:
    """按硬故障→方向互锁→时间目标的唯一顺序提交开边界。"""

    n = _step(fixed_step_index, "$.fixed_step_index")
    _require(
        isinstance(runtime, RuntimeShipParameters),
        "runtime",
        "$.runtime",
        "必须提供精确 runtime",
    )
    _validate_state(
        context,
        source_state,
        source_control,
        governor_clock=n,
        engine_boundary=n,
    )
    _require(
        isinstance(requested_control, DirectionalPropulsionControlInput),
        "requested_control",
        "$.requested_control",
        "必须提供严格原始请求控制",
    )
    _require(
        isinstance(hard_fault_command, GovernedPropulsionHardFaultCommand),
        "hard_fault_command",
        "$.hard_fault_command",
        "必须提供严格硬故障命令",
    )
    hard = commit_governed_propulsion_hard_fault_opening(
        context,
        runtime,
        source_state,
        hard_fault_command,
        fixed_step_index=n,
    )
    interlock = resolve_governed_propulsion_direction_interlock(
        context,
        hard,
        requested_control,
        fixed_step_index=n,
    )
    bindings, capabilities = _binding_maps(context)
    governors = {
        item.command_channel: item for item in source_state.governors
    }
    requested = {
        item.command_channel: item
        for item in requested_control.channel_commands
    }
    effective = {
        item.command_channel: item
        for item in interlock.effective_control.channel_commands
    }
    hard_by_id = _hard_availability(hard)
    hard_action = {
        item.snapshot.actuator_instance_id: item.action
        for item in hard.hard_fault_results
    }
    time_results = []
    for engine in hard.state.engines:
        actuator_id = engine.actuator_instance_id
        channel = bindings[actuator_id].command_channels[0]
        command = requested[channel]
        preview = preview_governed_propulsion_time_boundary(
            engine,
            capabilities[actuator_id],
            n,
            PropulsionTimeCommand(
                command.commanded_notch, command.target_output_percent
            ),
        )
        _require(
            not preview.has_upstage_candidate,
            "opening_due_upstage",
            f"$.source_state.engines.{actuator_id}",
            "开边界不得提前提交到期升阶",
        )
        target = min(
            command.requested_percent,
            effective[channel].requested_percent,
            governors[channel].safety_ceiling_percent,
            hard_by_id[actuator_id].ceiling_percent,
        )
        if hard_action[actuator_id] == "reset":
            target = 0
        time_results.append(
            commit_governed_propulsion_time_boundary(
                preview,
                capabilities[actuator_id],
                current_state=engine,
                fixed_step_index=n,
                effective_target_percent=target,
                allow_upstage=True,
            )
        )
    resulting_governors = tuple(
        replace(old, command=command)
        for old, command in zip(
            source_state.governors, requested_control.channel_commands
        )
    )
    state = replace(
        hard.state,
        engines=tuple(item.state for item in time_results),
        governors=resulting_governors,
    )
    _validate_state(
        context,
        state,
        requested_control,
        governor_clock=n,
        engine_boundary=n,
    )
    return FullyGovernedPropulsionOpening(
        n,
        canonical_sha256(source_state),
        canonical_sha256(state),
        source_control,
        requested_control,
        hard,
        interlock,
        tuple(time_results),
        state,
    )


def validate_fully_governed_propulsion_opening(
    result: FullyGovernedPropulsionOpening,
    context: ActualPropulsionContext,
    runtime: RuntimeShipParameters,
    source_state: TacticalPropulsionState,
    hard_fault_command: GovernedPropulsionHardFaultCommand,
) -> None:
    _require(
        isinstance(result, FullyGovernedPropulsionOpening),
        "opening_type",
        "$.result",
        "必须提供严格完整受控开边界",
    )
    expected = commit_fully_governed_propulsion_opening(
        context,
        runtime,
        source_state,
        result.source_control,
        result.requested_control,
        hard_fault_command,
        fixed_step_index=result.fixed_step_index,
    )
    _require(
        result == expected,
        "opening_replay",
        "$.result",
        "完整受控开边界未通过精确重放",
    )


def integrate_fully_governed_propulsion_interval(
    context: ActualPropulsionContext,
    runtime: RuntimeShipParameters,
    model: dynamics.TacticalShipModel,
    motion: dynamics.TacticalMotionState,
    opening: FullyGovernedPropulsionOpening,
    *,
    propulsion_delivery_status: str,
) -> GovernedPropulsionIntervalOutcome:
    """使用完整开边界状态聚合并积分一个区间；允许 tripped 执行器留在集合中。"""

    _require(
        isinstance(opening, FullyGovernedPropulsionOpening),
        "opening_type",
        "$.opening",
        "必须提供严格完整受控开边界",
    )
    FullyGovernedPropulsionOpening.parse(opening.to_dict(), "$.opening")
    _require(
        isinstance(runtime, RuntimeShipParameters)
        and runtime.source_sha256
        == opening.hard_fault_opening.projection.runtime_parameters_sha256
        and isinstance(model, dynamics.TacticalShipModel)
        and model.runtime == runtime,
        "runtime_model",
        "$.model",
        "区间模型必须绑定开边界使用的精确 runtime",
    )
    _require(
        isinstance(motion, dynamics.TacticalMotionState)
        and motion.fixed_step_index == opening.fixed_step_index,
        "motion_step",
        "$.motion",
        "区间运动状态必须属于开边界步",
    )
    aggregation = aggregate_actual_propulsion(
        context,
        runtime,
        opening.state.engines,
        opening.fixed_step_index,
    )
    delivered = governed_propulsion_delivery_request(
        aggregation.request, propulsion_delivery_status
    )
    resulting_motion, diagnostic = dynamics.integrate_actual_tactical_step(
        model, motion, delivered
    )
    diagnostics = GovernedActualTacticalStepDiagnostics(
        diagnostic,
        canonical_sha256(opening.state),
        canonical_sha256(
            [item.to_dict() for item in opening.state.governors]
        ),
    )
    return GovernedPropulsionIntervalOutcome(
        aggregation,
        delivered,
        resulting_motion,
        diagnostics,
        propulsion_delivery_status,
    )


def validate_fully_governed_propulsion_interval(
    result: GovernedPropulsionIntervalOutcome,
    context: ActualPropulsionContext,
    runtime: RuntimeShipParameters,
    model: dynamics.TacticalShipModel,
    motion: dynamics.TacticalMotionState,
    opening: FullyGovernedPropulsionOpening,
) -> None:
    _require(
        isinstance(result, GovernedPropulsionIntervalOutcome),
        "interval_type",
        "$.result",
        "必须提供严格区间结果",
    )
    expected = integrate_fully_governed_propulsion_interval(
        context,
        runtime,
        model,
        motion,
        opening,
        propulsion_delivery_status=result.propulsion_delivery_status,
    )
    _require(
        result == expected,
        "interval_replay",
        "$.result",
        "完整受控区间未通过精确重放",
    )


def _shadow_state(opening: FullyGovernedPropulsionOpening) -> TacticalPropulsionState:
    bindings = {
        actuator_id: item.command_channel
        for item in opening.direction_interlock.decisions
        for actuator_id in item.actuator_instance_ids
    }
    effective = {
        item.command_channel: item
        for item in opening.direction_interlock.effective_control.channel_commands
    }
    engines = tuple(
        replace(
            engine,
            commanded_notch=effective[bindings[engine.actuator_instance_id]].commanded_notch,
        )
        for engine in opening.state.engines
    )
    governors = tuple(
        replace(governor, command=command)
        for governor, command in zip(
            opening.state.governors,
            opening.direction_interlock.effective_control.channel_commands,
        )
    )
    return replace(opening.state, engines=engines, governors=governors)


def _restore_requested_state(
    opening: FullyGovernedPropulsionOpening,
    safety: WholeShipPropulsionSafetyResult,
) -> TacticalPropulsionState:
    bindings = {
        actuator_id: item.command_channel
        for item in opening.direction_interlock.decisions
        for actuator_id in item.actuator_instance_ids
    }
    requested = {
        item.command_channel: item
        for item in opening.requested_control.channel_commands
    }
    engines = tuple(
        replace(
            item.state,
            commanded_notch=requested[bindings[item.state.actuator_instance_id]].commanded_notch,
        )
        for item in safety.engine_results
    )
    governors = tuple(
        replace(governor, command=command)
        for governor, command in zip(
            safety.governors, opening.requested_control.channel_commands
        )
    )
    return TacticalPropulsionState(
        engines, governors, DIRECTIONAL_STATE_INTERFACE_ID
    )


@dataclass(frozen=True)
class FullyGovernedPropulsionClosing:
    fixed_step_index: int
    source_opening_sha256: str
    resulting_state_sha256: str
    final_runtime_sha256: str
    final_motion_sha256: str
    propulsion_delivery_status: str
    crew_safety_lock_enabled: bool
    opening: FullyGovernedPropulsionOpening
    safety_result: WholeShipPropulsionSafetyResult
    state: TacticalPropulsionState
    interface_id: str = FULLY_GOVERNED_CLOSING_INTERFACE_ID
    policy_id: str = FULLY_GOVERNED_POLICY_ID

    def __post_init__(self) -> None:
        if self.interface_id != FULLY_GOVERNED_CLOSING_INTERFACE_ID:
            raise ValueError("完整受控收边界 interface 非法")
        if self.policy_id != FULLY_GOVERNED_POLICY_ID:
            raise ValueError("完整受控收边界 policy 非法")
        if type(self.fixed_step_index) is not int or self.fixed_step_index < 1:
            raise ValueError("收边界步必须是正整数")
        for value in (
            self.source_opening_sha256,
            self.resulting_state_sha256,
            self.final_runtime_sha256,
            self.final_motion_sha256,
        ):
            if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value):
                raise ValueError("完整受控收边界指纹非法")
        if type(self.crew_safety_lock_enabled) is not bool:
            raise ValueError("乘员安全锁必须是布尔值")
        if self.propulsion_delivery_status not in ACTIVE_DELIVERY_STATUSES:
            raise ValueError("推进交付状态非法或已退出冻结")
        if (
            not isinstance(self.opening, FullyGovernedPropulsionOpening)
            or not isinstance(self.safety_result, WholeShipPropulsionSafetyResult)
            or not isinstance(self.state, TacticalPropulsionState)
        ):
            raise ValueError("完整受控收边界组成类型非法")
        if (
            self.fixed_step_index != self.opening.fixed_step_index + 1
            or self.safety_result.fixed_step_index != self.fixed_step_index
            or self.source_opening_sha256 != canonical_sha256(self.opening)
        ):
            raise ValueError("完整受控收边界步号或来源链不一致")
        if self.state != _restore_requested_state(self.opening, self.safety_result):
            raise ValueError("收边界必须只把原始命令恢复到软安全结果")
        if canonical_sha256(self.state) != self.resulting_state_sha256:
            raise ValueError("完整受控收边界结果状态指纹不匹配")

    @classmethod
    def parse(cls, value: Any, path: str = "$") -> "FullyGovernedPropulsionClosing":
        obj = _exact_object(
            value,
            {
                "crew_safety_lock_enabled",
                "final_motion_sha256",
                "final_runtime_sha256",
                "fixed_step_index",
                "interface",
                "opening",
                "policy",
                "propulsion_delivery_status",
                "resulting_state_sha256",
                "safety_result",
                "source_opening_sha256",
                "state",
            },
            path,
        )
        _require(
            obj["interface"] == FULLY_GOVERNED_CLOSING_INTERFACE_ID,
            "closing_interface",
            f"{path}.interface",
            str(obj["interface"]),
        )
        _require(
            obj["policy"] == FULLY_GOVERNED_POLICY_ID,
            "closing_policy",
            f"{path}.policy",
            str(obj["policy"]),
        )
        _require(
            type(obj["crew_safety_lock_enabled"]) is bool,
            "crew_safety_lock",
            f"{path}.crew_safety_lock_enabled",
            "必须是布尔值",
        )
        try:
            return cls(
                _step(obj["fixed_step_index"], f"{path}.fixed_step_index"),
                _sha256(
                    obj["source_opening_sha256"],
                    f"{path}.source_opening_sha256",
                ),
                _sha256(
                    obj["resulting_state_sha256"],
                    f"{path}.resulting_state_sha256",
                ),
                _sha256(
                    obj["final_runtime_sha256"],
                    f"{path}.final_runtime_sha256",
                ),
                _sha256(
                    obj["final_motion_sha256"],
                    f"{path}.final_motion_sha256",
                ),
                obj["propulsion_delivery_status"],
                obj["crew_safety_lock_enabled"],
                FullyGovernedPropulsionOpening.parse(
                    obj["opening"], f"{path}.opening"
                ),
                WholeShipPropulsionSafetyResult.parse(
                    obj["safety_result"], f"{path}.safety_result"
                ),
                TacticalPropulsionState.parse(obj["state"], f"{path}.state"),
            )
        except ContractError:
            raise
        except (TypeError, ValueError) as error:
            raise ContractError(
                "full_governed.closing_invariant", path, str(error)
            ) from error

    @property
    def time_events(self) -> tuple[PropulsionStateEvent, ...]:
        return tuple(
            event
            for item in self.safety_result.engine_results
            for event in item.events
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "crew_safety_lock_enabled": self.crew_safety_lock_enabled,
            "final_motion_sha256": self.final_motion_sha256,
            "final_runtime_sha256": self.final_runtime_sha256,
            "fixed_step_index": self.fixed_step_index,
            "interface": self.interface_id,
            "opening": self.opening.to_dict(),
            "policy": self.policy_id,
            "propulsion_delivery_status": self.propulsion_delivery_status,
            "resulting_state_sha256": self.resulting_state_sha256,
            "safety_result": self.safety_result.to_dict(),
            "source_opening_sha256": self.source_opening_sha256,
            "state": self.state.to_dict(),
        }


def evaluate_fully_governed_propulsion_closing(
    context: ActualPropulsionContext,
    opening: FullyGovernedPropulsionOpening,
    safety_profile: PropulsionSafetyProfile,
    final_runtime: RuntimeShipParameters,
    final_model: dynamics.TacticalShipModel,
    final_motion: dynamics.TacticalMotionState,
    *,
    fixed_step_index: int,
    propulsion_delivery_status: str,
    crew_safety_lock_enabled: bool,
) -> FullyGovernedPropulsionClosing:
    """以有效控制求值软安全，再仅恢复持久原始命令。"""

    n = _step(fixed_step_index, "$.fixed_step_index")
    _require(
        isinstance(opening, FullyGovernedPropulsionOpening)
        and n == opening.fixed_step_index + 1,
        "closing_opening",
        "$.opening",
        "收边界必须紧接完整开边界",
    )
    FullyGovernedPropulsionOpening.parse(opening.to_dict(), "$.opening")
    _require(
        isinstance(final_runtime, RuntimeShipParameters)
        and isinstance(final_model, dynamics.TacticalShipModel)
        and final_model.runtime == final_runtime,
        "final_runtime_model",
        "$.final_model",
        "最终模型必须绑定最终 runtime",
    )
    _require(
        isinstance(final_motion, dynamics.TacticalMotionState)
        and final_motion.fixed_step_index == n,
        "final_motion",
        "$.final_motion",
        "最终运动状态必须属于收边界步",
    )
    _require(
        isinstance(safety_profile, PropulsionSafetyProfile)
        and type(crew_safety_lock_enabled) is bool,
        "safety_input",
        "$.safety_profile",
        "必须提供严格安全配置与乘员锁",
    )
    shadow = _shadow_state(opening)
    _validate_state(
        context,
        shadow,
        opening.direction_interlock.effective_control,
        governor_clock=opening.fixed_step_index,
        engine_boundary=opening.fixed_step_index,
    )
    physical = WholeShipVectorLoadSampler(context, final_model, final_motion)
    sampler = GovernedPropulsionDeliveryLoadSampler(
        physical, propulsion_delivery_status
    )
    actuators = prepare_whole_ship_actuator_boundaries(
        context,
        shadow.engines,
        opening.direction_interlock.effective_control,
        _hard_availability(opening.hard_fault_opening),
        n,
    )
    safety = evaluate_whole_ship_propulsion_safety(
        safety_profile,
        shadow.governors,
        opening.direction_interlock.effective_control,
        actuators,
        fixed_step_index=n,
        load_context_sha256=sampler.source_sha256,
        load_evaluator=sampler,
        crew_safety_lock_enabled=crew_safety_lock_enabled,
    )
    state = _restore_requested_state(opening, safety)
    _validate_state(
        context,
        state,
        opening.requested_control,
        governor_clock=n,
        engine_boundary=n,
    )
    return FullyGovernedPropulsionClosing(
        n,
        canonical_sha256(opening),
        canonical_sha256(state),
        final_runtime.source_sha256,
        canonical_sha256(final_motion),
        propulsion_delivery_status,
        crew_safety_lock_enabled,
        opening,
        safety,
        state,
    )


def validate_fully_governed_propulsion_closing(
    result: FullyGovernedPropulsionClosing,
    context: ActualPropulsionContext,
    safety_profile: PropulsionSafetyProfile,
    final_runtime: RuntimeShipParameters,
    final_model: dynamics.TacticalShipModel,
    final_motion: dynamics.TacticalMotionState,
) -> None:
    _require(
        isinstance(result, FullyGovernedPropulsionClosing),
        "closing_type",
        "$.result",
        "必须提供严格完整受控收边界",
    )
    expected = evaluate_fully_governed_propulsion_closing(
        context,
        result.opening,
        safety_profile,
        final_runtime,
        final_model,
        final_motion,
        fixed_step_index=result.fixed_step_index,
        propulsion_delivery_status=result.propulsion_delivery_status,
        crew_safety_lock_enabled=result.crew_safety_lock_enabled,
    )
    _require(
        result == expected,
        "closing_replay",
        "$.result",
        "完整受控收边界未通过精确重放",
    )
