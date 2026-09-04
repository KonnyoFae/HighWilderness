"""T0b.2d4.1 纯推进硬故障事实、跳闸锁存与显式复位边界。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from 高天荒野舰艇数据契约 import (
    ContractError,
    RESOURCE_ID_PATTERN,
    SHA256_PATTERN,
    canonical_sha256,
)
from 高天荒野舰艇推进安全判定器 import (
    HARD_LIMIT_REASON_ORDER,
    PropulsionHardAvailability,
)
from 高天荒野舰艇推进状态合同 import (
    ENGINE_RUNTIME_STATE_INTERFACE_ID,
    EngineRuntimeState,
    PropulsionStateEvent,
)


HARD_FAULT_SNAPSHOT_INTERFACE_ID = (
    "gaotian.propulsion-hard-fault-snapshot/v1alpha1"
)
HARD_FAULT_BOUNDARY_RESULT_INTERFACE_ID = (
    "gaotian.propulsion-hard-fault-boundary-result/v1alpha1"
)
HARD_FAULT_POLICY_ID = (
    "gaotian.propulsion-hard-fault/trip-latched-explicit-reset/v1"
)
HARD_FAULT_ACTIONS = ("available", "trip", "latched", "reset")
EXTERNAL_HARD_FAULT_REASONS = (
    "fuel_unavailable",
    "power_unavailable",
    "crew_unavailable",
    "actuator_destroyed",
    "host_destroyed",
)


def _require(condition: bool, code: str, path: str, detail: str) -> None:
    if not condition:
        raise ContractError(code, path, detail)


def _exact_object(value: Any, keys: set[str], path: str) -> dict[str, Any]:
    _require(
        isinstance(value, dict) and set(value) == keys,
        "object.keys",
        path,
        f"必须恰含 {sorted(keys)}",
    )
    return value


def _resource_id(value: Any, path: str) -> str:
    _require(
        isinstance(value, str) and bool(RESOURCE_ID_PATTERN.fullmatch(value)),
        "resource.id_invalid",
        path,
        str(value),
    )
    return value


def _boolean(value: Any, path: str) -> bool:
    _require(type(value) is bool, "type.boolean", path, "必须是布尔值")
    return value


def _fixed_step(value: Any, path: str) -> int:
    _require(
        type(value) is int and value >= 0,
        "type.integer",
        path,
        "必须是非负整数",
    )
    return value


def _current_state(value: Any, path: str = "$.source_state") -> EngineRuntimeState:
    _require(
        isinstance(value, EngineRuntimeState)
        and value.interface_id == ENGINE_RUNTIME_STATE_INTERFACE_ID,
        "hard_fault.engine_state",
        path,
        "d4 只接受当前 v2 发动机运行状态",
    )
    return value


@dataclass(frozen=True)
class PropulsionHardFaultSnapshot:
    fixed_step_index: int
    actuator_instance_id: str
    fuel_available: bool
    power_available: bool
    crew_available: bool
    actuator_destroyed: bool
    host_destroyed: bool
    overg_requested: bool = False
    interface_id: str = HARD_FAULT_SNAPSHOT_INTERFACE_ID

    def __post_init__(self) -> None:
        if self.interface_id != HARD_FAULT_SNAPSHOT_INTERFACE_ID:
            raise ValueError("硬故障事实 interface 非法")
        if type(self.fixed_step_index) is not int or self.fixed_step_index < 0:
            raise ValueError("fixed_step_index 必须是非负整数")
        if not isinstance(self.actuator_instance_id, str) or not RESOURCE_ID_PATTERN.fullmatch(
            self.actuator_instance_id
        ):
            raise ValueError("actuator_instance_id 非法")
        for value in (
            self.fuel_available,
            self.power_available,
            self.crew_available,
            self.actuator_destroyed,
            self.host_destroyed,
            self.overg_requested,
        ):
            if type(value) is not bool:
                raise ValueError("硬故障事实必须是布尔值")

    @classmethod
    def parse(
        cls, value: Any, path: str = "$"
    ) -> "PropulsionHardFaultSnapshot":
        obj = _exact_object(
            value,
            {
                "actuator_destroyed",
                "actuator_instance_id",
                "crew_available",
                "fixed_step_index",
                "fuel_available",
                "host_destroyed",
                "interface",
                "overg_requested",
                "power_available",
            },
            path,
        )
        _require(
            obj["interface"] == HARD_FAULT_SNAPSHOT_INTERFACE_ID,
            "hard_fault.snapshot_interface",
            f"{path}.interface",
            str(obj["interface"]),
        )
        try:
            return cls(
                _fixed_step(obj["fixed_step_index"], f"{path}.fixed_step_index"),
                _resource_id(
                    obj["actuator_instance_id"],
                    f"{path}.actuator_instance_id",
                ),
                _boolean(obj["fuel_available"], f"{path}.fuel_available"),
                _boolean(obj["power_available"], f"{path}.power_available"),
                _boolean(obj["crew_available"], f"{path}.crew_available"),
                _boolean(obj["actuator_destroyed"], f"{path}.actuator_destroyed"),
                _boolean(obj["host_destroyed"], f"{path}.host_destroyed"),
                _boolean(obj["overg_requested"], f"{path}.overg_requested"),
            )
        except ValueError as error:
            raise ContractError(
                "hard_fault.snapshot_invariant", path, str(error)
            ) from error

    def to_dict(self) -> dict[str, Any]:
        return {
            "actuator_destroyed": self.actuator_destroyed,
            "actuator_instance_id": self.actuator_instance_id,
            "crew_available": self.crew_available,
            "fixed_step_index": self.fixed_step_index,
            "fuel_available": self.fuel_available,
            "host_destroyed": self.host_destroyed,
            "interface": self.interface_id,
            "overg_requested": self.overg_requested,
            "power_available": self.power_available,
        }


def external_hard_fault_reasons(
    snapshot: PropulsionHardFaultSnapshot,
) -> tuple[str, ...]:
    _require(
        isinstance(snapshot, PropulsionHardFaultSnapshot),
        "hard_fault.snapshot_type",
        "$.snapshot",
        "必须提供严格硬故障事实快照",
    )
    present = {
        "fuel_unavailable": not snapshot.fuel_available,
        "power_unavailable": not snapshot.power_available,
        "crew_unavailable": not snapshot.crew_available,
        "actuator_destroyed": snapshot.actuator_destroyed,
        "host_destroyed": snapshot.host_destroyed,
    }
    return tuple(reason for reason in EXTERNAL_HARD_FAULT_REASONS if present[reason])


def evaluate_propulsion_hard_availability(
    snapshot: PropulsionHardFaultSnapshot,
    state: EngineRuntimeState,
) -> PropulsionHardAvailability:
    state = _current_state(state)
    reasons = external_hard_fault_reasons(snapshot)
    _require(
        snapshot.actuator_instance_id == state.actuator_instance_id,
        "hard_fault.actuator_mismatch",
        "$.snapshot.actuator_instance_id",
        state.actuator_instance_id,
    )
    if state.phase == "tripped":
        reasons = tuple(
            reason
            for reason in HARD_LIMIT_REASON_ORDER
            if reason in set(reasons) | {"engine_tripped"}
        )
    return PropulsionHardAvailability(0 if reasons else 100, reasons)


def _trip_state(state: EngineRuntimeState) -> EngineRuntimeState:
    return replace(
        state,
        phase="tripped",
        target_output_percent=0,
        actual_output_percent=0,
        ready_at_fixed_step=None,
        next_transition_step=None,
        response_started_at_fixed_step=None,
        response_start_output_percent=None,
    )


def _reset_state(state: EngineRuntimeState) -> EngineRuntimeState:
    return replace(
        state,
        phase="off",
        commanded_notch="stop" if state.actuator_category == "main_engine" else None,
        target_output_percent=0,
        actual_output_percent=0,
        ready_at_fixed_step=None,
        next_transition_step=None,
        response_started_at_fixed_step=None,
        response_start_output_percent=None,
    )


def _event(
    state: EngineRuntimeState,
    snapshot: PropulsionHardFaultSnapshot,
    *,
    kind: str,
    resulting_phase: str,
    reasons: tuple[str, ...] = (),
) -> PropulsionStateEvent:
    stage_changed = state.actual_output_percent != 0
    return PropulsionStateEvent(
        snapshot.fixed_step_index,
        state.actuator_instance_id,
        None,
        kind,
        state.phase,
        resulting_phase,
        state.actual_output_percent if stage_changed else None,
        0 if stage_changed else None,
        reasons,
    )


@dataclass(frozen=True)
class PropulsionHardFaultBoundaryResult:
    action: str
    source_state_sha256: str
    snapshot: PropulsionHardFaultSnapshot
    hard_availability: PropulsionHardAvailability
    state: EngineRuntimeState
    events: tuple[PropulsionStateEvent, ...]
    policy_id: str = HARD_FAULT_POLICY_ID
    interface_id: str = HARD_FAULT_BOUNDARY_RESULT_INTERFACE_ID

    def __post_init__(self) -> None:
        if self.interface_id != HARD_FAULT_BOUNDARY_RESULT_INTERFACE_ID:
            raise ValueError("硬故障边界结果 interface 非法")
        if self.policy_id != HARD_FAULT_POLICY_ID:
            raise ValueError("硬故障边界 policy 非法")
        if self.action not in HARD_FAULT_ACTIONS:
            raise ValueError("硬故障边界 action 非法")
        if not isinstance(self.source_state_sha256, str) or not SHA256_PATTERN.fullmatch(
            self.source_state_sha256
        ):
            raise ValueError("source_state_sha256 非法")
        if not isinstance(self.snapshot, PropulsionHardFaultSnapshot):
            raise ValueError("snapshot 必须是严格硬故障事实")
        if not isinstance(self.hard_availability, PropulsionHardAvailability):
            raise ValueError("hard_availability 非法")
        _current_state(self.state, "$.state")
        if self.state.actuator_instance_id != self.snapshot.actuator_instance_id:
            raise ValueError("结果执行器与事实快照不一致")
        if not isinstance(self.events, tuple) or any(
            not isinstance(event, PropulsionStateEvent) for event in self.events
        ):
            raise ValueError("events 必须是严格不可变事件序列")
        if any(
            event.fixed_step_index != self.snapshot.fixed_step_index
            or event.actuator_instance_id != self.snapshot.actuator_instance_id
            for event in self.events
        ):
            raise ValueError("事件必须绑定同一执行器和固定步")
        if self.events != tuple(sorted(self.events, key=lambda event: event.sort_key)):
            raise ValueError("事件必须按稳定键排序")

        blocking = external_hard_fault_reasons(self.snapshot)
        expected_reasons = (
            tuple(
                reason
                for reason in HARD_LIMIT_REASON_ORDER
                if reason in set(blocking) | {"engine_tripped"}
            )
            if self.action in {"trip", "latched"}
            else ()
        )
        expected_ceiling = 0 if expected_reasons else 100
        if self.hard_availability != PropulsionHardAvailability(
            expected_ceiling, expected_reasons
        ):
            raise ValueError("结果硬可用性与动作或事实不一致")

        if self.action == "available":
            if blocking or self.state.phase == "tripped" or self.events:
                raise ValueError("available 必须无硬故障、未锁存且无事件")
        elif self.action == "trip":
            if not blocking or self.state.phase != "tripped" or len(self.events) != 1:
                raise ValueError("trip 必须由外部硬故障触发并进入 tripped")
            event = self.events[0]
            if (
                event.kind != "engine_tripped"
                or event.previous_phase == "tripped"
                or event.resulting_phase != "tripped"
                or event.resulting_stage_percent not in {None, 0}
                or event.reasons != blocking
            ):
                raise ValueError("trip 必须生成唯一且原因精确的跳闸事件")
        elif self.action == "latched":
            if self.state.phase != "tripped" or self.events:
                raise ValueError("latched 必须保持 tripped 且不得重复生成事件")
        elif self.action == "reset":
            if blocking or self.state.phase != "off" or len(self.events) != 1:
                raise ValueError("reset 只可在外部事实恢复后回到 off")
            event = self.events[0]
            if (
                event.kind != "engine_reset"
                or event.previous_phase != "tripped"
                or event.resulting_phase != "off"
                or event.previous_stage_percent is not None
                or event.resulting_stage_percent is not None
                or event.reasons
                or (
                    self.state.actuator_category == "main_engine"
                    and self.state.commanded_notch != "stop"
                )
            ):
                raise ValueError("reset 必须生成唯一无原因复位事件")

    @classmethod
    def parse(
        cls, value: Any, path: str = "$"
    ) -> "PropulsionHardFaultBoundaryResult":
        obj = _exact_object(
            value,
            {
                "action",
                "events",
                "hard_availability",
                "interface",
                "policy",
                "snapshot",
                "source_state_sha256",
                "state",
            },
            path,
        )
        _require(
            obj["interface"] == HARD_FAULT_BOUNDARY_RESULT_INTERFACE_ID,
            "hard_fault.result_interface",
            f"{path}.interface",
            str(obj["interface"]),
        )
        _require(
            obj["policy"] == HARD_FAULT_POLICY_ID,
            "hard_fault.result_policy",
            f"{path}.policy",
            str(obj["policy"]),
        )
        _require(
            obj["action"] in HARD_FAULT_ACTIONS,
            "hard_fault.result_action",
            f"{path}.action",
            str(obj["action"]),
        )
        _require(
            isinstance(obj["source_state_sha256"], str)
            and bool(SHA256_PATTERN.fullmatch(obj["source_state_sha256"])),
            "hash.sha256",
            f"{path}.source_state_sha256",
            str(obj["source_state_sha256"]),
        )
        availability_obj = _exact_object(
            obj["hard_availability"],
            {"ceiling_percent", "reasons"},
            f"{path}.hard_availability",
        )
        _require(
            type(availability_obj["ceiling_percent"]) is int,
            "hard_fault.ceiling_type",
            f"{path}.hard_availability.ceiling_percent",
            "必须是整数离散阶段",
        )
        _require(
            isinstance(availability_obj["reasons"], list)
            and all(isinstance(reason, str) for reason in availability_obj["reasons"]),
            "type.string_array",
            f"{path}.hard_availability.reasons",
            "必须是字符串数组",
        )
        _require(
            isinstance(obj["events"], list),
            "type.array",
            f"{path}.events",
            "必须是数组",
        )
        try:
            availability = PropulsionHardAvailability(
                availability_obj["ceiling_percent"],
                tuple(availability_obj["reasons"]),
            )
            return cls(
                obj["action"],
                obj["source_state_sha256"],
                PropulsionHardFaultSnapshot.parse(
                    obj["snapshot"], f"{path}.snapshot"
                ),
                availability,
                EngineRuntimeState.parse(obj["state"], f"{path}.state"),
                tuple(
                    PropulsionStateEvent.parse(event, f"{path}.events[{index}]")
                    for index, event in enumerate(obj["events"])
                ),
            )
        except ValueError as error:
            raise ContractError(
                "hard_fault.result_invariant", path, str(error)
            ) from error

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "events": [event.to_dict() for event in self.events],
            "hard_availability": self.hard_availability.to_dict(),
            "interface": self.interface_id,
            "policy": self.policy_id,
            "snapshot": self.snapshot.to_dict(),
            "source_state_sha256": self.source_state_sha256,
            "state": self.state.to_dict(),
        }


def apply_propulsion_hard_fault_boundary(
    state: EngineRuntimeState,
    snapshot: PropulsionHardFaultSnapshot,
    *,
    reset_requested: bool = False,
) -> PropulsionHardFaultBoundaryResult:
    state = _current_state(state)
    _require(
        isinstance(snapshot, PropulsionHardFaultSnapshot),
        "hard_fault.snapshot_type",
        "$.snapshot",
        "必须提供严格硬故障事实快照",
    )
    _require(
        type(reset_requested) is bool,
        "type.boolean",
        "$.reset_requested",
        "必须是布尔值",
    )
    _require(
        snapshot.actuator_instance_id == state.actuator_instance_id,
        "hard_fault.actuator_mismatch",
        "$.snapshot.actuator_instance_id",
        state.actuator_instance_id,
    )
    blocking = external_hard_fault_reasons(snapshot)
    source_hash = canonical_sha256(state)

    if reset_requested:
        _require(
            state.phase == "tripped",
            "hard_fault.reset_not_tripped",
            "$.source_state.phase",
            "只有 tripped 执行器可显式复位",
        )
        _require(
            not blocking,
            "hard_fault.reset_blocked",
            "$.snapshot",
            f"外部硬故障仍存在：{blocking}",
        )
        resulting = _reset_state(state)
        event = _event(
            state,
            snapshot,
            kind="engine_reset",
            resulting_phase="off",
        )
        return PropulsionHardFaultBoundaryResult(
            "reset",
            source_hash,
            snapshot,
            PropulsionHardAvailability(),
            resulting,
            (event,),
        )

    if state.phase == "tripped":
        return PropulsionHardFaultBoundaryResult(
            "latched",
            source_hash,
            snapshot,
            evaluate_propulsion_hard_availability(snapshot, state),
            state,
            (),
        )
    if not blocking:
        return PropulsionHardFaultBoundaryResult(
            "available",
            source_hash,
            snapshot,
            PropulsionHardAvailability(),
            state,
            (),
        )

    resulting = _trip_state(state)
    event = _event(
        state,
        snapshot,
        kind="engine_tripped",
        resulting_phase="tripped",
        reasons=blocking,
    )
    return PropulsionHardFaultBoundaryResult(
        "trip",
        source_hash,
        snapshot,
        evaluate_propulsion_hard_availability(snapshot, resulting),
        resulting,
        (event,),
    )


def validate_propulsion_hard_fault_boundary_result(
    result: PropulsionHardFaultBoundaryResult,
    source_state: EngineRuntimeState,
) -> None:
    _require(
        isinstance(result, PropulsionHardFaultBoundaryResult),
        "hard_fault.result_type",
        "$.result",
        "必须提供严格硬故障边界结果",
    )
    source_state = _current_state(source_state)
    _require(
        result.source_state_sha256 == canonical_sha256(source_state),
        "hard_fault.source_state_hash",
        "$.result.source_state_sha256",
        "结果未绑定当前精确源状态",
    )
    expected = apply_propulsion_hard_fault_boundary(
        source_state,
        result.snapshot,
        reset_requested=result.action == "reset",
    )
    _require(
        expected == result,
        "hard_fault.result_replay",
        "$.result",
        "结果未通过精确边界重放",
    )
