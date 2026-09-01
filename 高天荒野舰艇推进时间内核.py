"""T0b.2d1 独立推进时间内核。

本模块只在固定步边界推进单个执行器的启动与离散响应状态。它不读取场景、
不计算力/力矩/油耗，也不调用推进安全 governor、硬故障或方向互锁。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from typing import Any

from 高天荒野舰艇数据契约 import ContractError, ModuleCapability
from 高天荒野舰艇推进安全判定器 import (
    TELEGRAPH_NOTCHES,
    THRUST_OUTPUT_STAGES_PERCENT,
    adjacent_output_stage_percent,
    telegraph_notch_percent,
)
from 高天荒野舰艇推进状态合同 import (
    ENGINE_RUNTIME_STATE_INTERFACE_ID,
    PROPULSION_EVENT_KIND_ORDER,
    EngineRuntimeState,
    PropulsionStateEvent,
)


PROPULSION_TIME_BOUNDARY_INTERFACE_ID = (
    "gaotian.propulsion-time-boundary-result/v1alpha1"
)
FIXED_STEP_HZ = 60
_PROPULSION_CAPABILITY_KEYS = {
    "fuel_units_per_s",
    "kind",
    "local_thrust_axis",
    "response_time_s",
    "startup_time_s",
    "thrust_n",
}


def _nonnegative_integer(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ContractError("propulsion_time.fixed_step", path, "必须是非负整数")
    return value


def _decimal_number(value: Any, path: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError("propulsion_time.capability_number", path, "必须是有限数")
    try:
        result = Decimal(str(value))
    except InvalidOperation as error:
        raise ContractError(
            "propulsion_time.capability_number",
            path,
            "必须是有限数",
        ) from error
    if not result.is_finite():
        raise ContractError("propulsion_time.capability_number", path, "必须是有限数")
    return result


def _ceil_decimal(value: Decimal) -> int:
    return int(value.to_integral_value(rounding=ROUND_CEILING))


@dataclass(frozen=True)
class PropulsionTimeCommand:
    """一个执行器在当前固定步边界收到的完整权威命令。"""

    commanded_notch: str | None
    target_output_percent: int | None

    def __post_init__(self) -> None:
        if (self.commanded_notch is None) == (self.target_output_percent is None):
            raise ValueError("车钟档位与姿态推进目标必须且只能填写一个")
        if (
            self.commanded_notch is not None
            and self.commanded_notch not in TELEGRAPH_NOTCHES
        ):
            raise ValueError("commanded_notch 必须是规范车钟档位")
        if self.target_output_percent is not None and (
            isinstance(self.target_output_percent, bool)
            or self.target_output_percent not in THRUST_OUTPUT_STAGES_PERCENT
        ):
            raise ValueError("target_output_percent 必须是规范离散阶段")

    @classmethod
    def main_engine(cls, commanded_notch: str) -> "PropulsionTimeCommand":
        return cls(commanded_notch, None)

    @classmethod
    def maneuver_thruster(
        cls,
        target_output_percent: int,
    ) -> "PropulsionTimeCommand":
        return cls(None, target_output_percent)

    def target_for(self, actuator_category: str) -> tuple[str | None, int]:
        if actuator_category == "main_engine":
            if self.commanded_notch is None:
                raise ContractError(
                    "propulsion_time.command_category",
                    "$.command",
                    "主发动机必须接收车钟档位",
                )
            return self.commanded_notch, telegraph_notch_percent(
                self.commanded_notch
            )
        if actuator_category == "maneuver_thruster":
            if self.target_output_percent is None:
                raise ContractError(
                    "propulsion_time.command_category",
                    "$.command",
                    "姿态推进器必须接收离散目标阶段",
                )
            return None, self.target_output_percent
        raise ContractError(
            "propulsion_time.actuator_category",
            "$.state.actuator_category",
            actuator_category,
        )


@dataclass(frozen=True)
class _ExactTimingCapability:
    actuator_category: str
    startup_quanta: Decimal
    response_quanta: Decimal
    startup_steps: int
    response_steps: int


def _parse_exact_timing_capability(
    capability: ModuleCapability,
    actuator_category: str,
) -> _ExactTimingCapability:
    if not isinstance(capability, ModuleCapability):
        raise ContractError(
            "propulsion_time.capability_type",
            "$.capability",
            "必须传入已解析的 ModuleCapability",
        )
    value = capability.to_dict()
    if set(value) != _PROPULSION_CAPABILITY_KEYS:
        raise ContractError(
            "propulsion_time.capability_version",
            "$.capability",
            "必须是显式携带 startup_time_s 的推进 capability v2",
        )
    if value["kind"] != actuator_category:
        raise ContractError(
            "propulsion_time.capability_category",
            "$.capability.kind",
            "capability 类别必须与执行器状态一致",
        )
    startup_time = _decimal_number(
        value["startup_time_s"],
        "$.capability.startup_time_s",
    )
    response_time = _decimal_number(
        value["response_time_s"],
        "$.capability.response_time_s",
    )
    if response_time <= 0:
        raise ContractError(
            "propulsion_time.response_time",
            "$.capability.response_time_s",
            "响应时间必须大于 0",
        )
    if actuator_category == "main_engine" and startup_time <= 0:
        raise ContractError(
            "propulsion_time.startup_time",
            "$.capability.startup_time_s",
            "主发动机启动时间必须大于 0",
        )
    if actuator_category == "maneuver_thruster" and startup_time != 0:
        raise ContractError(
            "propulsion_time.startup_time",
            "$.capability.startup_time_s",
            "首轮姿态推进器启动时间必须等于 0",
        )

    startup_quanta = startup_time * FIXED_STEP_HZ
    response_quanta = response_time * FIXED_STEP_HZ
    startup_steps = _ceil_decimal(startup_quanta)
    response_steps = _ceil_decimal(response_quanta)

    # 所有相邻阶段在完整上升和下降响应中都必须落在不同边界；否则既无法
    # 保持精确总时长，也无法遵守“同一步最多跨一个阶段”。
    for origin, targets in (
        (0, THRUST_OUTPUT_STAGES_PERCENT[1:]),
        (100, tuple(reversed(THRUST_OUTPUT_STAGES_PERCENT[:-1]))),
    ):
        offsets = tuple(
            _ceil_decimal(
                response_quanta * Decimal(abs(target - origin)) / Decimal(100)
            )
            for target in targets
        )
        if not offsets or offsets[0] <= 0 or any(
            current <= previous for previous, current in zip(offsets, offsets[1:])
        ):
            raise ContractError(
                "propulsion_time.response_unschedulable",
                "$.capability.response_time_s",
                (
                    f"{response_steps} 个响应步无法让全部相邻阶段占用独立边界；"
                    "请提高正式平衡值"
                ),
            )
    if offsets[-1] != response_steps:
        raise ContractError(
            "propulsion_time.response_total",
            "$.capability.response_time_s",
            "0↔100 响应终点与总响应步数不一致",
        )
    return _ExactTimingCapability(
        actuator_category,
        startup_quanta,
        response_quanta,
        startup_steps,
        response_steps,
    )


@dataclass(frozen=True)
class PropulsionTimeBoundaryResult:
    fixed_step_index: int
    state: EngineRuntimeState
    events: tuple[PropulsionStateEvent, ...]

    def __post_init__(self) -> None:
        if (
            isinstance(self.fixed_step_index, bool)
            or not isinstance(self.fixed_step_index, int)
            or self.fixed_step_index < 0
        ):
            raise ValueError("fixed_step_index 非法")
        if tuple(sorted(self.events, key=lambda item: item.sort_key)) != self.events:
            raise ValueError("事件必须按稳定键排序")
        if any(
            item.fixed_step_index != self.fixed_step_index
            or item.actuator_instance_id != self.state.actuator_instance_id
            for item in self.events
        ):
            raise ValueError("事件必须绑定结果边界与同一执行器")
        stage_events = tuple(
            item
            for item in self.events
            if item.kind == "engine_output_stage_changed"
        )
        if len(stage_events) > 1:
            raise ValueError("同一固定步最多提交一个输出阶段")

    @classmethod
    def parse(cls, value: Any, path: str = "$") -> "PropulsionTimeBoundaryResult":
        if not isinstance(value, dict) or set(value) != {
            "events",
            "fixed_step_index",
            "interface",
            "state",
        }:
            raise ContractError(
                "object.keys",
                path,
                "推进时间边界结果字段不完整或包含额外字段",
            )
        if value["interface"] != PROPULSION_TIME_BOUNDARY_INTERFACE_ID:
            raise ContractError(
                "propulsion_time.interface",
                f"{path}.interface",
                str(value["interface"]),
            )
        if not isinstance(value["events"], list):
            raise ContractError("type.array", f"{path}.events", "必须是数组")
        try:
            return cls(
                _nonnegative_integer(
                    value["fixed_step_index"],
                    f"{path}.fixed_step_index",
                ),
                EngineRuntimeState.parse(value["state"], f"{path}.state"),
                tuple(
                    PropulsionStateEvent.parse(item, f"{path}.events[{index}]")
                    for index, item in enumerate(value["events"])
                ),
            )
        except ContractError:
            raise
        except ValueError as error:
            raise ContractError(
                "propulsion_time.result_invariant",
                path,
                str(error),
            ) from error

    def to_dict(self) -> dict[str, Any]:
        return {
            "events": [item.to_dict() for item in self.events],
            "fixed_step_index": self.fixed_step_index,
            "interface": PROPULSION_TIME_BOUNDARY_INTERFACE_ID,
            "state": self.state.to_dict(),
        }


def _event(
    state: EngineRuntimeState,
    fixed_step_index: int,
    kind: str,
    *,
    previous_phase: str | None = None,
    resulting_phase: str | None = None,
    previous_stage: int | None = None,
    resulting_stage: int | None = None,
) -> PropulsionStateEvent:
    return PropulsionStateEvent(
        fixed_step_index,
        state.actuator_instance_id,
        None,
        kind,
        previous_phase,
        resulting_phase,
        previous_stage,
        resulting_stage,
        (),
    )


def _transition_step(
    timing: _ExactTimingCapability,
    schedule_started_at: int,
    schedule_origin: int,
    output_stage: int,
) -> int:
    delta = Decimal(abs(output_stage - schedule_origin)) / Decimal(100)
    return schedule_started_at + _ceil_decimal(timing.response_quanta * delta)


def _scheduled_state(
    state: EngineRuntimeState,
    timing: _ExactTimingCapability,
    fixed_step_index: int,
    target: int,
    commanded_notch: str | None,
) -> EngineRuntimeState:
    actual = state.actual_output_percent
    if target == actual:
        if actual == 0:
            return replace(
                state,
                phase="ready",
                commanded_notch=commanded_notch,
                target_output_percent=0,
                next_transition_step=None,
                response_started_at_fixed_step=None,
                response_start_output_percent=None,
            )
        return replace(
            state,
            phase="running",
            commanded_notch=commanded_notch,
            target_output_percent=target,
            next_transition_step=None,
            response_started_at_fixed_step=None,
            response_start_output_percent=None,
        )
    adjacent = adjacent_output_stage_percent(actual, target)
    return replace(
        state,
        phase="stopping" if target == 0 else "running",
        commanded_notch=commanded_notch,
        target_output_percent=target,
        next_transition_step=_transition_step(
            timing,
            fixed_step_index,
            actual,
            adjacent,
        ),
        response_started_at_fixed_step=fixed_step_index,
        response_start_output_percent=actual,
    )


def _commit_due_transition(
    state: EngineRuntimeState,
    timing: _ExactTimingCapability,
    fixed_step_index: int,
) -> tuple[EngineRuntimeState, list[PropulsionStateEvent]]:
    due = state.next_transition_step
    if due is None or fixed_step_index < due:
        return state, []
    if fixed_step_index > due:
        raise ContractError(
            "propulsion_time.missed_boundary",
            "$.fixed_step_index",
            f"必须先处理固定步边界 {due}",
        )
    if state.phase == "starting":
        adjacent = adjacent_output_stage_percent(
            state.actual_output_percent,
            state.target_output_percent,
        )
        running = replace(
            state,
            phase="running",
            next_transition_step=_transition_step(
                timing,
                fixed_step_index,
                state.actual_output_percent,
                adjacent,
            ),
            response_started_at_fixed_step=fixed_step_index,
            response_start_output_percent=state.actual_output_percent,
        )
        event = _event(
            state,
            fixed_step_index,
            "engine_start_completed",
            previous_phase="starting",
            resulting_phase="running",
        )
        return running, [event]
    if state.phase not in {"running", "stopping"}:
        raise ContractError(
            "propulsion_time.transition_phase",
            "$.state.phase",
            state.phase,
        )
    if (
        state.response_started_at_fixed_step is None
        or state.response_start_output_percent is None
    ):
        raise ContractError(
            "propulsion_time.schedule_missing",
            "$.state",
            "响应中的执行器必须保存排程起点",
        )
    next_stage = adjacent_output_stage_percent(
        state.actual_output_percent,
        state.target_output_percent,
    )
    events = [
        _event(
            state,
            fixed_step_index,
            "engine_output_stage_changed",
            previous_stage=state.actual_output_percent,
            resulting_stage=next_stage,
        )
    ]
    if next_stage == state.target_output_percent:
        if next_stage == 0:
            result = replace(
                state,
                phase="ready",
                actual_output_percent=0,
                next_transition_step=None,
                response_started_at_fixed_step=None,
                response_start_output_percent=None,
            )
            events.append(
                _event(
                    state,
                    fixed_step_index,
                    "engine_stopped",
                    previous_phase="stopping",
                    resulting_phase="ready",
                )
            )
            return result, events
        return (
            replace(
                state,
                actual_output_percent=next_stage,
                next_transition_step=None,
                response_started_at_fixed_step=None,
                response_start_output_percent=None,
            ),
            events,
        )
    next_after = adjacent_output_stage_percent(
        next_stage,
        state.target_output_percent,
    )
    next_due = _transition_step(
        timing,
        state.response_started_at_fixed_step,
        state.response_start_output_percent,
        next_after,
    )
    if next_due <= fixed_step_index:
        raise ContractError(
            "propulsion_time.response_unschedulable",
            "$.capability.response_time_s",
            "相邻阶段落在同一固定步边界",
        )
    return replace(
        state,
        actual_output_percent=next_stage,
        next_transition_step=next_due,
    ), events


def _apply_command(
    state: EngineRuntimeState,
    timing: _ExactTimingCapability,
    fixed_step_index: int,
    commanded_notch: str | None,
    target: int,
) -> tuple[EngineRuntimeState, list[PropulsionStateEvent]]:
    if state.phase == "tripped":
        if target != 0:
            raise ContractError(
                "propulsion_time.tripped_command",
                "$.command",
                "d1 不负责复位 tripped 执行器",
            )
        return replace(state, commanded_notch=commanded_notch), []
    if state.phase == "off":
        if target == 0:
            return replace(state, commanded_notch=commanded_notch), []
        requested = _event(
            state,
            fixed_step_index,
            "engine_start_requested",
            previous_phase="off",
            resulting_phase="starting",
        )
        if timing.startup_steps == 0:
            ready = replace(
                state,
                phase="ready",
                commanded_notch=commanded_notch,
                ready_at_fixed_step=fixed_step_index,
            )
            running = _scheduled_state(
                ready,
                timing,
                fixed_step_index,
                target,
                commanded_notch,
            )
            completed = _event(
                state,
                fixed_step_index,
                "engine_start_completed",
                previous_phase="starting",
                resulting_phase="running",
            )
            return running, [requested, completed]
        ready_at = fixed_step_index + timing.startup_steps
        return (
            replace(
                state,
                phase="starting",
                commanded_notch=commanded_notch,
                target_output_percent=target,
                ready_at_fixed_step=ready_at,
                next_transition_step=ready_at,
            ),
            [requested],
        )
    if state.phase == "starting":
        if target == 0:
            return (
                replace(
                    state,
                    phase="off",
                    commanded_notch=commanded_notch,
                    target_output_percent=0,
                    ready_at_fixed_step=None,
                    next_transition_step=None,
                ),
                [
                    _event(
                        state,
                        fixed_step_index,
                        "engine_stop_requested",
                        previous_phase="starting",
                        resulting_phase="off",
                    )
                ],
            )
        return replace(
            state,
            commanded_notch=commanded_notch,
            target_output_percent=target,
        ), []
    if target == state.target_output_percent:
        return replace(state, commanded_notch=commanded_notch), []

    previous_phase = state.phase
    if target == state.actual_output_percent:
        if target == 0:
            stopped = _scheduled_state(
                state,
                timing,
                fixed_step_index,
                target,
                commanded_notch,
            )
            if previous_phase == "ready":
                return stopped, []
            return stopped, [
                _event(
                    state,
                    fixed_step_index,
                    "engine_stop_requested",
                    previous_phase=previous_phase,
                    resulting_phase="stopping",
                ),
                _event(
                    state,
                    fixed_step_index,
                    "engine_stopped",
                    previous_phase="stopping",
                    resulting_phase="ready",
                ),
            ]
        return _scheduled_state(
            state,
            timing,
            fixed_step_index,
            target,
            commanded_notch,
        ), []

    result = _scheduled_state(
        state,
        timing,
        fixed_step_index,
        target,
        commanded_notch,
    )
    if target == 0 and previous_phase != "stopping":
        return result, [
            _event(
                state,
                fixed_step_index,
                "engine_stop_requested",
                previous_phase=previous_phase,
                resulting_phase="stopping",
            )
        ]
    return result, []


def advance_propulsion_time_boundary(
    state: EngineRuntimeState,
    capability: ModuleCapability,
    fixed_step_index: int,
    command: PropulsionTimeCommand,
) -> PropulsionTimeBoundaryResult:
    """提交一个固定步边界；已到期阶段先提交，再从已提交输出处理新命令。"""

    if not isinstance(state, EngineRuntimeState):
        raise ContractError(
            "propulsion_time.state_type",
            "$.state",
            "必须传入 EngineRuntimeState",
        )
    if state.interface_id != ENGINE_RUNTIME_STATE_INTERFACE_ID:
        raise ContractError(
            "propulsion_time.state_interface",
            "$.state.interface",
            "c2b 状态必须先显式迁移到 d1 interface",
        )
    boundary = _nonnegative_integer(fixed_step_index, "$.fixed_step_index")
    if not isinstance(command, PropulsionTimeCommand):
        raise ContractError(
            "propulsion_time.command_type",
            "$.command",
            "必须传入 PropulsionTimeCommand",
        )
    timing = _parse_exact_timing_capability(capability, state.actuator_category)
    commanded_notch, target = command.target_for(state.actuator_category)
    committed, events = _commit_due_transition(state, timing, boundary)
    resulting, command_events = _apply_command(
        committed,
        timing,
        boundary,
        commanded_notch,
        target,
    )
    events.extend(command_events)
    events.sort(
        key=lambda item: (
            item.fixed_step_index,
            item.actuator_instance_id,
            PROPULSION_EVENT_KIND_ORDER.index(item.kind),
        )
    )
    try:
        return PropulsionTimeBoundaryResult(boundary, resulting, tuple(events))
    except ValueError as error:
        raise ContractError(
            "propulsion_time.result_invariant",
            "$",
            str(error),
        ) from error
