"""T0b.2d4.3：时间提交与物理聚合之前的无场景硬故障开边界。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from 高天荒野舰艇数据契约 import (
    ContractError,
    RESOURCE_ID_PATTERN,
    SHA256_PATTERN,
    canonical_sha256,
)
from 高天荒野舰艇实际推进聚合器 import ActualPropulsionContext
from 高天荒野舰艇推进硬故障边界 import (
    PropulsionHardFaultBoundaryResult,
    apply_propulsion_hard_fault_boundary,
)
from 高天荒野舰艇推进硬故障运行时投影 import (
    RuntimePropulsionHardFactProjection,
    project_runtime_propulsion_hard_facts,
)
from 高天荒野舰艇推进状态合同 import (
    ENGINE_RUNTIME_STATE_INTERFACE_ID,
    EngineRuntimeState,
    TacticalPropulsionState,
)
from 高天荒野舰艇推进通道合同 import DIRECTIONAL_STATE_INTERFACE_ID
from 高天荒野舰艇运行时参数编译器 import RuntimeShipParameters


GOVERNED_HARD_FAULT_COMMAND_INTERFACE_ID = (
    "gaotian.governed-propulsion-hard-fault-command/v1alpha1"
)
EMERGENCY_CUT_RESULT_INTERFACE_ID = (
    "gaotian.propulsion-emergency-cut-result/v1alpha1"
)
GOVERNED_HARD_FAULT_OPENING_INTERFACE_ID = (
    "gaotian.governed-propulsion-hard-fault-opening/v1alpha1"
)
EMERGENCY_CUT_POLICY_ID = "gaotian.propulsion-emergency-cut/immediate-zero/v1"
GOVERNED_HARD_FAULT_OPENING_POLICY_ID = (
    "gaotian.governed-propulsion/hard-fault-before-time-and-delivery/v1"
)
EMERGENCY_CUT_CAUSES = (
    "operator_requested",
    "safety_system_requested",
)
EMERGENCY_CUT_ACTIONS = (
    "cut",
    "already_zero",
    "tripped_preserved",
)


def _require(condition: bool, code: str, path: str, detail: str) -> None:
    if not condition:
        raise ContractError(f"governed_hard_fault.{code}", path, detail)


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


def _resource_id(value: Any, path: str) -> str:
    _require(
        isinstance(value, str) and bool(RESOURCE_ID_PATTERN.fullmatch(value)),
        "resource_id",
        path,
        str(value),
    )
    return value


@dataclass(frozen=True)
class GovernedPropulsionHardFaultCommand:
    reset_actuator_instance_ids: tuple[str, ...] = ()
    emergency_cut_cause: str | None = None
    interface_id: str = GOVERNED_HARD_FAULT_COMMAND_INTERFACE_ID

    def __post_init__(self) -> None:
        if self.interface_id != GOVERNED_HARD_FAULT_COMMAND_INTERFACE_ID:
            raise ValueError("硬故障命令 interface 非法")
        if not isinstance(self.reset_actuator_instance_ids, tuple):
            raise ValueError("reset_actuator_instance_ids 必须是不可变序列")
        if any(
            not isinstance(item, str) or not RESOURCE_ID_PATTERN.fullmatch(item)
            for item in self.reset_actuator_instance_ids
        ):
            raise ValueError("复位执行器 id 非法")
        if (
            self.reset_actuator_instance_ids
            != tuple(sorted(self.reset_actuator_instance_ids))
            or len(set(self.reset_actuator_instance_ids))
            != len(self.reset_actuator_instance_ids)
        ):
            raise ValueError("复位执行器必须稳定排序且不得重复")
        if self.emergency_cut_cause is not None and (
            self.emergency_cut_cause not in EMERGENCY_CUT_CAUSES
            or self.reset_actuator_instance_ids
        ):
            raise ValueError("紧急断推原因非法，且不得与复位同边界提交")

    @classmethod
    def parse(
        cls, value: Any, path: str = "$"
    ) -> "GovernedPropulsionHardFaultCommand":
        obj = _exact_object(
            value,
            {
                "emergency_cut_cause",
                "interface",
                "reset_actuator_instance_ids",
            },
            path,
        )
        _require(
            obj["interface"] == GOVERNED_HARD_FAULT_COMMAND_INTERFACE_ID,
            "command_interface",
            f"{path}.interface",
            str(obj["interface"]),
        )
        resets = obj["reset_actuator_instance_ids"]
        _require(
            isinstance(resets, list),
            "reset_ids",
            f"{path}.reset_actuator_instance_ids",
            "必须是数组",
        )
        cause = obj["emergency_cut_cause"]
        _require(
            cause is None or isinstance(cause, str),
            "emergency_cut_cause",
            f"{path}.emergency_cut_cause",
            "必须是字符串或 null",
        )
        try:
            return cls(
                tuple(
                    _resource_id(
                        item,
                        f"{path}.reset_actuator_instance_ids[{index}]",
                    )
                    for index, item in enumerate(resets)
                ),
                cause,
            )
        except ContractError:
            raise
        except ValueError as error:
            raise ContractError(
                "governed_hard_fault.command_invariant", path, str(error)
            ) from error

    def to_dict(self) -> dict[str, Any]:
        return {
            "emergency_cut_cause": self.emergency_cut_cause,
            "interface": self.interface_id,
            "reset_actuator_instance_ids": list(
                self.reset_actuator_instance_ids
            ),
        }


def _current_engine(value: Any, path: str) -> EngineRuntimeState:
    _require(
        isinstance(value, EngineRuntimeState)
        and value.interface_id == ENGINE_RUNTIME_STATE_INTERFACE_ID,
        "engine_state",
        path,
        "必须提供当前 v2 发动机状态",
    )
    EngineRuntimeState.parse(value.to_dict(), path)
    return value


def _cut_state(state: EngineRuntimeState) -> EngineRuntimeState:
    if state.phase in {"off", "ready", "tripped"}:
        return state
    if state.phase == "starting":
        return replace(
            state,
            phase="off",
            target_output_percent=0,
            actual_output_percent=0,
            ready_at_fixed_step=None,
            next_transition_step=None,
            response_started_at_fixed_step=None,
            response_start_output_percent=None,
        )
    return replace(
        state,
        phase="ready",
        target_output_percent=0,
        actual_output_percent=0,
        next_transition_step=None,
        response_started_at_fixed_step=None,
        response_start_output_percent=None,
    )


@dataclass(frozen=True)
class PropulsionEmergencyCutResult:
    action: str
    cause: str
    fixed_step_index: int
    source_state_sha256: str
    state: EngineRuntimeState
    interface_id: str = EMERGENCY_CUT_RESULT_INTERFACE_ID
    policy_id: str = EMERGENCY_CUT_POLICY_ID

    def __post_init__(self) -> None:
        if self.interface_id != EMERGENCY_CUT_RESULT_INTERFACE_ID:
            raise ValueError("紧急断推结果 interface 非法")
        if self.policy_id != EMERGENCY_CUT_POLICY_ID:
            raise ValueError("紧急断推 policy 非法")
        if self.action not in EMERGENCY_CUT_ACTIONS:
            raise ValueError("紧急断推动作非法")
        if self.cause not in EMERGENCY_CUT_CAUSES:
            raise ValueError("紧急断推原因非法")
        if type(self.fixed_step_index) is not int or self.fixed_step_index < 0:
            raise ValueError("fixed_step_index 必须是非负整数")
        if not isinstance(self.source_state_sha256, str) or not SHA256_PATTERN.fullmatch(
            self.source_state_sha256
        ):
            raise ValueError("source_state_sha256 非法")
        _current_engine(self.state, "$.state")
        if self.state.actual_output_percent or self.state.target_output_percent:
            raise ValueError("紧急断推结果必须立即归零")
        if any(
            value is not None
            for value in (
                self.state.next_transition_step,
                self.state.response_started_at_fixed_step,
                self.state.response_start_output_percent,
            )
        ):
            raise ValueError("紧急断推结果不得保留输出排程")
        if self.action == "tripped_preserved" and self.state.phase != "tripped":
            raise ValueError("tripped_preserved 必须保留跳闸")
        if self.action == "already_zero" and self.state.phase not in {"off", "ready"}:
            raise ValueError("already_zero 只适用于 off/ready")
        if self.action == "cut" and self.state.phase not in {"off", "ready"}:
            raise ValueError("cut 必须落到 off/ready")

    @classmethod
    def parse(
        cls, value: Any, path: str = "$"
    ) -> "PropulsionEmergencyCutResult":
        obj = _exact_object(
            value,
            {
                "action",
                "cause",
                "fixed_step_index",
                "interface",
                "policy",
                "source_state_sha256",
                "state",
            },
            path,
        )
        _require(
            obj["interface"] == EMERGENCY_CUT_RESULT_INTERFACE_ID,
            "cut_interface",
            f"{path}.interface",
            str(obj["interface"]),
        )
        _require(
            obj["policy"] == EMERGENCY_CUT_POLICY_ID,
            "cut_policy",
            f"{path}.policy",
            str(obj["policy"]),
        )
        _require(
            obj["action"] in EMERGENCY_CUT_ACTIONS,
            "cut_action",
            f"{path}.action",
            str(obj["action"]),
        )
        _require(
            obj["cause"] in EMERGENCY_CUT_CAUSES,
            "cut_cause",
            f"{path}.cause",
            str(obj["cause"]),
        )
        try:
            return cls(
                obj["action"],
                obj["cause"],
                _step(obj["fixed_step_index"], f"{path}.fixed_step_index"),
                _sha256(
                    obj["source_state_sha256"],
                    f"{path}.source_state_sha256",
                ),
                EngineRuntimeState.parse(obj["state"], f"{path}.state"),
            )
        except ContractError:
            raise
        except ValueError as error:
            raise ContractError(
                "governed_hard_fault.cut_invariant", path, str(error)
            ) from error

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "cause": self.cause,
            "fixed_step_index": self.fixed_step_index,
            "interface": self.interface_id,
            "policy": self.policy_id,
            "source_state_sha256": self.source_state_sha256,
            "state": self.state.to_dict(),
        }


def apply_emergency_propulsion_cut(
    state: EngineRuntimeState,
    *,
    fixed_step_index: int,
    cause: str,
) -> PropulsionEmergencyCutResult:
    state = _current_engine(state, "$.source_state")
    step = _step(fixed_step_index, "$.fixed_step_index")
    _require(
        cause in EMERGENCY_CUT_CAUSES,
        "cut_cause",
        "$.cause",
        str(cause),
    )
    resulting = _cut_state(state)
    action = (
        "tripped_preserved"
        if state.phase == "tripped"
        else "already_zero"
        if resulting == state
        else "cut"
    )
    return PropulsionEmergencyCutResult(
        action,
        cause,
        step,
        canonical_sha256(state),
        resulting,
    )


def validate_emergency_propulsion_cut_result(
    result: PropulsionEmergencyCutResult,
    source_state: EngineRuntimeState,
) -> None:
    _require(
        isinstance(result, PropulsionEmergencyCutResult),
        "cut_result_type",
        "$.result",
        "必须提供严格紧急断推结果",
    )
    expected = apply_emergency_propulsion_cut(
        source_state,
        fixed_step_index=result.fixed_step_index,
        cause=result.cause,
    )
    _require(
        result == expected,
        "cut_result_replay",
        "$.result",
        "紧急断推结果未通过精确重放",
    )


@dataclass(frozen=True)
class GovernedPropulsionHardFaultOpening:
    fixed_step_index: int
    source_state_sha256: str
    resulting_state_sha256: str
    command: GovernedPropulsionHardFaultCommand
    projection: RuntimePropulsionHardFactProjection
    hard_fault_results: tuple[PropulsionHardFaultBoundaryResult, ...]
    emergency_cut_results: tuple[PropulsionEmergencyCutResult, ...]
    state: TacticalPropulsionState
    interface_id: str = GOVERNED_HARD_FAULT_OPENING_INTERFACE_ID
    policy_id: str = GOVERNED_HARD_FAULT_OPENING_POLICY_ID

    def __post_init__(self) -> None:
        if self.interface_id != GOVERNED_HARD_FAULT_OPENING_INTERFACE_ID:
            raise ValueError("硬故障开边界 interface 非法")
        if self.policy_id != GOVERNED_HARD_FAULT_OPENING_POLICY_ID:
            raise ValueError("硬故障开边界 policy 非法")
        if type(self.fixed_step_index) is not int or self.fixed_step_index < 0:
            raise ValueError("fixed_step_index 必须是非负整数")
        for value in (self.source_state_sha256, self.resulting_state_sha256):
            if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value):
                raise ValueError("开边界状态指纹非法")
        if not isinstance(self.command, GovernedPropulsionHardFaultCommand):
            raise ValueError("硬故障命令非法")
        if not isinstance(self.projection, RuntimePropulsionHardFactProjection):
            raise ValueError("运行时事实投影非法")
        if self.projection.propulsion_state_sha256 != self.source_state_sha256:
            raise ValueError("开边界源状态必须与运行时事实投影精确绑定")
        if not isinstance(self.hard_fault_results, tuple) or any(
            not isinstance(item, PropulsionHardFaultBoundaryResult)
            for item in self.hard_fault_results
        ):
            raise ValueError("硬故障结果必须是严格不可变序列")
        if not isinstance(self.emergency_cut_results, tuple) or any(
            not isinstance(item, PropulsionEmergencyCutResult)
            for item in self.emergency_cut_results
        ):
            raise ValueError("紧急断推结果必须是严格不可变序列")
        if not isinstance(self.state, TacticalPropulsionState):
            raise ValueError("结果推进状态非法")
        TacticalPropulsionState.parse(self.state.to_dict(), "$.state")
        if self.state.interface_id != DIRECTIONAL_STATE_INTERFACE_ID:
            raise ValueError("结果必须保留当前定向推进状态")
        ids = tuple(
            item.snapshot.actuator_instance_id
            for item in self.hard_fault_results
        )
        projection_ids = tuple(
            item.actuator_instance_id for item in self.projection.snapshots
        )
        state_ids = tuple(item.actuator_instance_id for item in self.state.engines)
        if (
            not ids
            or ids != projection_ids
            or ids != state_ids
            or any(
                hard.snapshot != snapshot
                for hard, snapshot in zip(
                    self.hard_fault_results, self.projection.snapshots
                )
            )
        ):
            raise ValueError("投影、硬故障结果与结果状态必须精确覆盖同一执行器集")
        if self.projection.fixed_step_index != self.fixed_step_index or any(
            item.snapshot.fixed_step_index != self.fixed_step_index
            for item in self.hard_fault_results
        ):
            raise ValueError("开边界投影与硬故障结果必须属于同一步")
        reset_ids = set(self.command.reset_actuator_instance_ids)
        if not reset_ids.issubset(ids):
            raise ValueError("复位命令只能引用开边界覆盖的执行器")
        if any(
            (item.action == "reset") != (item.snapshot.actuator_instance_id in reset_ids)
            for item in self.hard_fault_results
        ):
            raise ValueError("逐执行器复位结果必须精确对应命令")
        if self.command.emergency_cut_cause is None:
            if self.emergency_cut_results:
                raise ValueError("无紧急断推命令时不得产生断推结果")
            expected_engines = tuple(
                item.state for item in self.hard_fault_results
            )
        else:
            cut_ids = tuple(
                item.state.actuator_instance_id
                for item in self.emergency_cut_results
            )
            if cut_ids != ids or any(
                item.cause != self.command.emergency_cut_cause
                or item.fixed_step_index != self.fixed_step_index
                or item.source_state_sha256
                != canonical_sha256(hard.state)
                or item
                != apply_emergency_propulsion_cut(
                    hard.state,
                    fixed_step_index=self.fixed_step_index,
                    cause=self.command.emergency_cut_cause,
                )
                for item, hard in zip(
                    self.emergency_cut_results, self.hard_fault_results
                )
            ):
                raise ValueError("紧急断推必须在硬故障之后逐执行器稳定提交")
            expected_engines = tuple(
                item.state for item in self.emergency_cut_results
            )
        if self.state.engines != expected_engines:
            raise ValueError("结果发动机状态未精确来自开边界链")
        if canonical_sha256(self.state) != self.resulting_state_sha256:
            raise ValueError("结果推进状态指纹不匹配")

    @classmethod
    def parse(
        cls, value: Any, path: str = "$"
    ) -> "GovernedPropulsionHardFaultOpening":
        obj = _exact_object(
            value,
            {
                "command",
                "emergency_cut_results",
                "fixed_step_index",
                "hard_fault_results",
                "interface",
                "policy",
                "projection",
                "resulting_state_sha256",
                "source_state_sha256",
                "state",
            },
            path,
        )
        _require(
            obj["interface"] == GOVERNED_HARD_FAULT_OPENING_INTERFACE_ID,
            "opening_interface",
            f"{path}.interface",
            str(obj["interface"]),
        )
        _require(
            obj["policy"] == GOVERNED_HARD_FAULT_OPENING_POLICY_ID,
            "opening_policy",
            f"{path}.policy",
            str(obj["policy"]),
        )
        for key in ("hard_fault_results", "emergency_cut_results"):
            _require(
                isinstance(obj[key], list),
                "opening_results",
                f"{path}.{key}",
                "必须是数组",
            )
        try:
            return cls(
                _step(obj["fixed_step_index"], f"{path}.fixed_step_index"),
                _sha256(
                    obj["source_state_sha256"],
                    f"{path}.source_state_sha256",
                ),
                _sha256(
                    obj["resulting_state_sha256"],
                    f"{path}.resulting_state_sha256",
                ),
                GovernedPropulsionHardFaultCommand.parse(
                    obj["command"], f"{path}.command"
                ),
                RuntimePropulsionHardFactProjection.parse(
                    obj["projection"], f"{path}.projection"
                ),
                tuple(
                    PropulsionHardFaultBoundaryResult.parse(
                        item, f"{path}.hard_fault_results[{index}]"
                    )
                    for index, item in enumerate(obj["hard_fault_results"])
                ),
                tuple(
                    PropulsionEmergencyCutResult.parse(
                        item, f"{path}.emergency_cut_results[{index}]"
                    )
                    for index, item in enumerate(
                        obj["emergency_cut_results"]
                    )
                ),
                TacticalPropulsionState.parse(obj["state"], f"{path}.state"),
            )
        except ContractError:
            raise
        except ValueError as error:
            raise ContractError(
                "governed_hard_fault.opening_invariant", path, str(error)
            ) from error

    @property
    def propulsion_events(self) -> tuple[Any, ...]:
        return tuple(
            event
            for result in self.hard_fault_results
            for event in result.events
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command.to_dict(),
            "emergency_cut_results": [
                item.to_dict() for item in self.emergency_cut_results
            ],
            "fixed_step_index": self.fixed_step_index,
            "hard_fault_results": [
                item.to_dict() for item in self.hard_fault_results
            ],
            "interface": self.interface_id,
            "policy": self.policy_id,
            "projection": self.projection.to_dict(),
            "resulting_state_sha256": self.resulting_state_sha256,
            "source_state_sha256": self.source_state_sha256,
            "state": self.state.to_dict(),
        }


def commit_governed_propulsion_hard_fault_opening(
    context: ActualPropulsionContext,
    runtime: RuntimeShipParameters,
    state: TacticalPropulsionState,
    command: GovernedPropulsionHardFaultCommand,
    *,
    fixed_step_index: int,
) -> GovernedPropulsionHardFaultOpening:
    """原子提交事实、跳闸/复位及独立断推；不得推进时间或计算载荷。"""

    step = _step(fixed_step_index, "$.fixed_step_index")
    _require(
        isinstance(command, GovernedPropulsionHardFaultCommand),
        "command_type",
        "$.command",
        "必须提供严格硬故障命令",
    )
    GovernedPropulsionHardFaultCommand.parse(command.to_dict(), "$.command")
    projection = project_runtime_propulsion_hard_facts(
        context, runtime, state, step
    )
    engine_by_id = {
        item.actuator_instance_id: item for item in state.engines
    }
    projection_ids = tuple(
        item.actuator_instance_id for item in projection.snapshots
    )
    unknown_resets = set(command.reset_actuator_instance_ids) - set(
        projection_ids
    )
    _require(
        not unknown_resets,
        "reset_identity",
        "$.command.reset_actuator_instance_ids",
        f"复位命令引用未知执行器：{sorted(unknown_resets)}",
    )
    reset_ids = set(command.reset_actuator_instance_ids)
    hard_results = tuple(
        apply_propulsion_hard_fault_boundary(
            engine_by_id[snapshot.actuator_instance_id],
            snapshot,
            reset_requested=snapshot.actuator_instance_id in reset_ids,
        )
        for snapshot in projection.snapshots
    )
    cut_results = (
        tuple(
            apply_emergency_propulsion_cut(
                item.state,
                fixed_step_index=step,
                cause=command.emergency_cut_cause,
            )
            for item in hard_results
        )
        if command.emergency_cut_cause is not None
        else ()
    )
    resulting_engines = (
        tuple(item.state for item in cut_results)
        if cut_results
        else tuple(item.state for item in hard_results)
    )
    resulting_state = replace(state, engines=resulting_engines)
    return GovernedPropulsionHardFaultOpening(
        step,
        canonical_sha256(state),
        canonical_sha256(resulting_state),
        command,
        projection,
        hard_results,
        cut_results,
        resulting_state,
    )


def validate_governed_propulsion_hard_fault_opening(
    result: GovernedPropulsionHardFaultOpening,
    context: ActualPropulsionContext,
    runtime: RuntimeShipParameters,
    source_state: TacticalPropulsionState,
) -> None:
    _require(
        isinstance(result, GovernedPropulsionHardFaultOpening),
        "opening_type",
        "$.result",
        "必须提供严格硬故障开边界",
    )
    _require(
        result.source_state_sha256 == canonical_sha256(source_state),
        "opening_source",
        "$.result.source_state_sha256",
        "开边界未绑定当前精确源状态",
    )
    expected = commit_governed_propulsion_hard_fault_opening(
        context,
        runtime,
        source_state,
        result.command,
        fixed_step_index=result.fixed_step_index,
    )
    _require(
        result == expected,
        "opening_replay",
        "$.result",
        "硬故障开边界未通过精确重放",
    )
