"""T0b.2d4.4：硬故障开边界之后、时间提交之前的纯方向互锁。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from 高天荒野舰艇数据契约 import (
    ContractError,
    RESOURCE_ID_PATTERN,
    SHA256_PATTERN,
    canonical_sha256,
)
from 高天荒野舰艇实际推进聚合器 import ActualPropulsionContext
from 高天荒野舰艇受控推进硬故障适配器 import (
    GovernedPropulsionHardFaultOpening,
)
from 高天荒野舰艇定向推进控制桥 import (
    DirectionalPropulsionControlInput,
)
from 高天荒野舰艇推进状态合同 import TacticalPropulsionState
from 高天荒野舰艇推进通道合同 import (
    DIRECTIONAL_CHANNELS,
    DIRECTIONAL_STATE_INTERFACE_ID,
    OPPOSING_CHANNEL_PAIRS,
    ChannelPropulsionCommand,
)


DIRECTION_INTERLOCK_DECISION_INTERFACE_ID = (
    "gaotian.propulsion-direction-interlock-decision/v1alpha1"
)
DIRECTION_INTERLOCK_BOUNDARY_INTERFACE_ID = (
    "gaotian.governed-propulsion-direction-interlock/v1alpha1"
)
DIRECTION_INTERLOCK_POLICY_ID = (
    "gaotian.propulsion-direction-interlock/opposed-zero-before-increase/v1"
)
DIRECTION_INTERLOCK_ACTIONS = (
    "pass_through",
    "blocked_until_opposing_zero",
    "emergency_cut_hold",
)
OPPOSING_CHANNEL = {
    channel: opposite
    for pair in OPPOSING_CHANNEL_PAIRS
    for channel, opposite in (pair, tuple(reversed(pair)))
}


def _require(condition: bool, code: str, path: str, detail: str) -> None:
    if not condition:
        raise ContractError(f"direction_interlock.{code}", path, detail)


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


def _resource_ids(value: Any, path: str) -> tuple[str, ...]:
    _require(isinstance(value, list), "resource_ids", path, "必须是数组")
    result = tuple(value)
    _require(
        all(
            isinstance(item, str) and bool(RESOURCE_ID_PATTERN.fullmatch(item))
            for item in result
        ),
        "resource_ids",
        path,
        "必须只包含规范资源 id",
    )
    return result


@dataclass(frozen=True)
class PropulsionDirectionInterlockDecision:
    command_channel: str
    opposing_channel: str
    actuator_instance_ids: tuple[str, ...]
    blocking_actuator_instance_ids: tuple[str, ...]
    requested_command: ChannelPropulsionCommand
    effective_command: ChannelPropulsionCommand
    action: str
    interface_id: str = DIRECTION_INTERLOCK_DECISION_INTERFACE_ID
    policy_id: str = DIRECTION_INTERLOCK_POLICY_ID

    def __post_init__(self) -> None:
        if self.interface_id != DIRECTION_INTERLOCK_DECISION_INTERFACE_ID:
            raise ValueError("方向互锁决定 interface 非法")
        if self.policy_id != DIRECTION_INTERLOCK_POLICY_ID:
            raise ValueError("方向互锁决定 policy 非法")
        if (
            self.command_channel not in DIRECTIONAL_CHANNELS
            or self.opposing_channel != OPPOSING_CHANNEL.get(self.command_channel)
        ):
            raise ValueError("方向互锁通道或对向通道非法")
        for values in (
            self.actuator_instance_ids,
            self.blocking_actuator_instance_ids,
        ):
            if (
                not isinstance(values, tuple)
                or values != tuple(sorted(values))
                or len(set(values)) != len(values)
                or any(
                    not isinstance(item, str)
                    or not RESOURCE_ID_PATTERN.fullmatch(item)
                    for item in values
                )
            ):
                raise ValueError("方向互锁执行器 id 必须稳定排序且不得重复")
        if (
            not isinstance(self.requested_command, ChannelPropulsionCommand)
            or not isinstance(self.effective_command, ChannelPropulsionCommand)
            or self.requested_command.command_channel != self.command_channel
            or self.effective_command.command_channel != self.command_channel
        ):
            raise ValueError("方向互锁命令必须绑定当前通道")
        if self.action not in DIRECTION_INTERLOCK_ACTIONS:
            raise ValueError("方向互锁动作非法")
        stopped = ChannelPropulsionCommand.stop(self.command_channel)
        if self.action == "pass_through":
            valid = (
                not self.blocking_actuator_instance_ids
                and self.effective_command == self.requested_command
            )
        elif self.action == "blocked_until_opposing_zero":
            valid = (
                self.requested_command.requested_percent > 0
                and bool(self.blocking_actuator_instance_ids)
                and self.effective_command == stopped
            )
        else:
            valid = (
                not self.blocking_actuator_instance_ids
                and self.effective_command == stopped
            )
        if not valid:
            raise ValueError("方向互锁动作、阻塞执行器与有效命令不一致")

    @classmethod
    def parse(
        cls, value: Any, path: str = "$"
    ) -> "PropulsionDirectionInterlockDecision":
        obj = _exact_object(
            value,
            {
                "action",
                "actuator_instance_ids",
                "blocking_actuator_instance_ids",
                "command_channel",
                "effective_command",
                "interface",
                "opposing_channel",
                "policy",
                "requested_command",
            },
            path,
        )
        _require(
            obj["interface"] == DIRECTION_INTERLOCK_DECISION_INTERFACE_ID,
            "decision_interface",
            f"{path}.interface",
            str(obj["interface"]),
        )
        _require(
            obj["policy"] == DIRECTION_INTERLOCK_POLICY_ID,
            "decision_policy",
            f"{path}.policy",
            str(obj["policy"]),
        )
        try:
            return cls(
                obj["command_channel"],
                obj["opposing_channel"],
                _resource_ids(
                    obj["actuator_instance_ids"],
                    f"{path}.actuator_instance_ids",
                ),
                _resource_ids(
                    obj["blocking_actuator_instance_ids"],
                    f"{path}.blocking_actuator_instance_ids",
                ),
                ChannelPropulsionCommand.parse(
                    obj["requested_command"], f"{path}.requested_command"
                ),
                ChannelPropulsionCommand.parse(
                    obj["effective_command"], f"{path}.effective_command"
                ),
                obj["action"],
            )
        except ContractError:
            raise
        except (TypeError, ValueError) as error:
            raise ContractError(
                "direction_interlock.decision_invariant", path, str(error)
            ) from error

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "actuator_instance_ids": list(self.actuator_instance_ids),
            "blocking_actuator_instance_ids": list(
                self.blocking_actuator_instance_ids
            ),
            "command_channel": self.command_channel,
            "effective_command": self.effective_command.to_dict(),
            "interface": self.interface_id,
            "opposing_channel": self.opposing_channel,
            "policy": self.policy_id,
            "requested_command": self.requested_command.to_dict(),
        }


@dataclass(frozen=True)
class GovernedPropulsionDirectionInterlockBoundary:
    fixed_step_index: int
    source_hard_fault_opening_sha256: str
    propulsion_state_sha256: str
    requested_control_sha256: str
    effective_control_sha256: str
    requested_control: DirectionalPropulsionControlInput
    effective_control: DirectionalPropulsionControlInput
    decisions: tuple[PropulsionDirectionInterlockDecision, ...]
    interface_id: str = DIRECTION_INTERLOCK_BOUNDARY_INTERFACE_ID
    policy_id: str = DIRECTION_INTERLOCK_POLICY_ID

    def __post_init__(self) -> None:
        if self.interface_id != DIRECTION_INTERLOCK_BOUNDARY_INTERFACE_ID:
            raise ValueError("方向互锁边界 interface 非法")
        if self.policy_id != DIRECTION_INTERLOCK_POLICY_ID:
            raise ValueError("方向互锁边界 policy 非法")
        if type(self.fixed_step_index) is not int or self.fixed_step_index < 0:
            raise ValueError("fixed_step_index 必须是非负整数")
        for value in (
            self.source_hard_fault_opening_sha256,
            self.propulsion_state_sha256,
            self.requested_control_sha256,
            self.effective_control_sha256,
        ):
            if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value):
                raise ValueError("方向互锁来源指纹非法")
        if (
            not isinstance(self.requested_control, DirectionalPropulsionControlInput)
            or not isinstance(self.effective_control, DirectionalPropulsionControlInput)
        ):
            raise ValueError("方向互锁必须保存严格请求与有效控制")
        DirectionalPropulsionControlInput.parse(
            self.requested_control.to_dict(), "$.requested_control"
        )
        DirectionalPropulsionControlInput.parse(
            self.effective_control.to_dict(), "$.effective_control"
        )
        if (
            canonical_sha256(self.requested_control)
            != self.requested_control_sha256
            or canonical_sha256(self.effective_control)
            != self.effective_control_sha256
        ):
            raise ValueError("方向互锁控制指纹不匹配")
        if (
            not isinstance(self.decisions, tuple)
            or any(
                not isinstance(item, PropulsionDirectionInterlockDecision)
                for item in self.decisions
            )
            or tuple(item.command_channel for item in self.decisions)
            != DIRECTIONAL_CHANNELS
        ):
            raise ValueError("方向互锁决定必须按规范顺序覆盖六通道")
        requested = {
            item.command_channel: item
            for item in self.requested_control.channel_commands
        }
        effective = {
            item.command_channel: item
            for item in self.effective_control.channel_commands
        }
        decision_by_channel = {
            item.command_channel: item for item in self.decisions
        }
        actuator_ids = tuple(
            actuator_id
            for item in self.decisions
            for actuator_id in item.actuator_instance_ids
        )
        if not actuator_ids or len(set(actuator_ids)) != len(actuator_ids):
            raise ValueError("方向互锁决定必须唯一覆盖非空执行器集")
        for item in self.decisions:
            opposite_ids = set(
                decision_by_channel[item.opposing_channel].actuator_instance_ids
            )
            if (
                item.requested_command != requested[item.command_channel]
                or item.effective_command != effective[item.command_channel]
                or not set(item.blocking_actuator_instance_ids).issubset(
                    opposite_ids
                )
            ):
                raise ValueError("方向互锁决定未精确绑定控制或对向执行器")
        emergency_actions = tuple(
            item.action == "emergency_cut_hold" for item in self.decisions
        )
        if any(emergency_actions) and not all(emergency_actions):
            raise ValueError("紧急断推保持必须原子覆盖六通道")

    @classmethod
    def parse(
        cls, value: Any, path: str = "$"
    ) -> "GovernedPropulsionDirectionInterlockBoundary":
        obj = _exact_object(
            value,
            {
                "decisions",
                "effective_control",
                "effective_control_sha256",
                "fixed_step_index",
                "interface",
                "policy",
                "propulsion_state_sha256",
                "requested_control",
                "requested_control_sha256",
                "source_hard_fault_opening_sha256",
            },
            path,
        )
        _require(
            obj["interface"] == DIRECTION_INTERLOCK_BOUNDARY_INTERFACE_ID,
            "boundary_interface",
            f"{path}.interface",
            str(obj["interface"]),
        )
        _require(
            obj["policy"] == DIRECTION_INTERLOCK_POLICY_ID,
            "boundary_policy",
            f"{path}.policy",
            str(obj["policy"]),
        )
        _require(
            isinstance(obj["decisions"], list),
            "decisions",
            f"{path}.decisions",
            "必须是数组",
        )
        try:
            return cls(
                _step(obj["fixed_step_index"], f"{path}.fixed_step_index"),
                _sha256(
                    obj["source_hard_fault_opening_sha256"],
                    f"{path}.source_hard_fault_opening_sha256",
                ),
                _sha256(
                    obj["propulsion_state_sha256"],
                    f"{path}.propulsion_state_sha256",
                ),
                _sha256(
                    obj["requested_control_sha256"],
                    f"{path}.requested_control_sha256",
                ),
                _sha256(
                    obj["effective_control_sha256"],
                    f"{path}.effective_control_sha256",
                ),
                DirectionalPropulsionControlInput.parse(
                    obj["requested_control"], f"{path}.requested_control"
                ),
                DirectionalPropulsionControlInput.parse(
                    obj["effective_control"], f"{path}.effective_control"
                ),
                tuple(
                    PropulsionDirectionInterlockDecision.parse(
                        item, f"{path}.decisions[{index}]"
                    )
                    for index, item in enumerate(obj["decisions"])
                ),
            )
        except ContractError:
            raise
        except (TypeError, ValueError) as error:
            raise ContractError(
                "direction_interlock.boundary_invariant", path, str(error)
            ) from error

    @property
    def blocked_channels(self) -> tuple[str, ...]:
        return tuple(
            item.command_channel
            for item in self.decisions
            if item.action == "blocked_until_opposing_zero"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "decisions": [item.to_dict() for item in self.decisions],
            "effective_control": self.effective_control.to_dict(),
            "effective_control_sha256": self.effective_control_sha256,
            "fixed_step_index": self.fixed_step_index,
            "interface": self.interface_id,
            "policy": self.policy_id,
            "propulsion_state_sha256": self.propulsion_state_sha256,
            "requested_control": self.requested_control.to_dict(),
            "requested_control_sha256": self.requested_control_sha256,
            "source_hard_fault_opening_sha256": (
                self.source_hard_fault_opening_sha256
            ),
        }


def resolve_governed_propulsion_direction_interlock(
    context: ActualPropulsionContext,
    hard_fault_opening: GovernedPropulsionHardFaultOpening,
    requested_control: DirectionalPropulsionControlInput,
    *,
    fixed_step_index: int,
) -> GovernedPropulsionDirectionInterlockBoundary:
    """生成本步有效命令；不修改状态、governor、时间排程或物理交付。"""

    n = _step(fixed_step_index, "$.fixed_step_index")
    _require(
        isinstance(context, ActualPropulsionContext),
        "context",
        "$.context",
        "必须提供精确推进上下文",
    )
    context.__post_init__()
    _require(
        isinstance(hard_fault_opening, GovernedPropulsionHardFaultOpening),
        "hard_fault_opening",
        "$.hard_fault_opening",
        "必须提供严格硬故障开边界",
    )
    GovernedPropulsionHardFaultOpening.parse(
        hard_fault_opening.to_dict(), "$.hard_fault_opening"
    )
    _require(
        hard_fault_opening.fixed_step_index == n,
        "hard_fault_step",
        "$.hard_fault_opening.fixed_step_index",
        "硬故障开边界与方向互锁必须属于同一步",
    )
    state = hard_fault_opening.state
    _require(
        isinstance(state, TacticalPropulsionState)
        and state.interface_id == DIRECTIONAL_STATE_INTERFACE_ID
        and canonical_sha256(state)
        == hard_fault_opening.resulting_state_sha256,
        "hard_fault_state",
        "$.hard_fault_opening.state",
        "方向互锁必须读取硬故障开边界的精确结果状态",
    )
    _require(
        isinstance(requested_control, DirectionalPropulsionControlInput),
        "requested_control",
        "$.requested_control",
        "必须提供严格原始控制",
    )
    DirectionalPropulsionControlInput.parse(
        requested_control.to_dict(), "$.requested_control"
    )
    bindings = {
        item.actuator_instance_id: item for item in context.bindings
    }
    engines = {item.actuator_instance_id: item for item in state.engines}
    _require(
        len(bindings) == len(context.bindings)
        and len(engines) == len(state.engines)
        and set(bindings) == set(engines),
        "actuator_set",
        "$.hard_fault_opening.state.engines",
        "状态必须精确覆盖当前静态执行器",
    )
    channel_actuators = {channel: [] for channel in DIRECTIONAL_CHANNELS}
    active_actuators = {channel: [] for channel in DIRECTIONAL_CHANNELS}
    for actuator_id in sorted(bindings):
        binding = bindings[actuator_id]
        engine = engines[actuator_id]
        _require(
            len(binding.command_channels) == 1
            and engine.actuator_category == binding.actuator_category,
            "actuator_binding",
            f"$.hard_fault_opening.state.engines.{actuator_id}",
            "执行器类别与唯一物理用途必须匹配",
        )
        channel = binding.command_channels[0]
        channel_actuators[channel].append(actuator_id)
        if engine.actual_output_percent > 0:
            active_actuators[channel].append(actuator_id)
    requested = {
        item.command_channel: item
        for item in requested_control.channel_commands
    }
    emergency_hold = hard_fault_opening.command.emergency_cut_cause is not None
    decisions = []
    effective_commands = []
    for channel in DIRECTIONAL_CHANNELS:
        command = requested[channel]
        blockers = (
            ()
            if emergency_hold or command.requested_percent == 0
            else tuple(active_actuators[OPPOSING_CHANNEL[channel]])
        )
        if emergency_hold:
            action = "emergency_cut_hold"
            effective = ChannelPropulsionCommand.stop(channel)
        elif blockers:
            action = "blocked_until_opposing_zero"
            effective = ChannelPropulsionCommand.stop(channel)
        else:
            action = "pass_through"
            effective = command
        decisions.append(
            PropulsionDirectionInterlockDecision(
                channel,
                OPPOSING_CHANNEL[channel],
                tuple(channel_actuators[channel]),
                blockers,
                command,
                effective,
                action,
            )
        )
        effective_commands.append(effective)
    effective_control = DirectionalPropulsionControlInput(
        tuple(effective_commands),
        requested_control.automatic_brake,
        requested_control.overg_requested,
        requested_control.source_migration_id,
    )
    return GovernedPropulsionDirectionInterlockBoundary(
        n,
        canonical_sha256(hard_fault_opening),
        canonical_sha256(state),
        canonical_sha256(requested_control),
        canonical_sha256(effective_control),
        requested_control,
        effective_control,
        tuple(decisions),
    )


def validate_governed_propulsion_direction_interlock(
    result: GovernedPropulsionDirectionInterlockBoundary,
    context: ActualPropulsionContext,
    hard_fault_opening: GovernedPropulsionHardFaultOpening,
) -> None:
    _require(
        isinstance(result, GovernedPropulsionDirectionInterlockBoundary),
        "boundary_type",
        "$.result",
        "必须提供严格方向互锁边界",
    )
    expected = resolve_governed_propulsion_direction_interlock(
        context,
        hard_fault_opening,
        result.requested_control,
        fixed_step_index=result.fixed_step_index,
    )
    _require(
        result == expected,
        "boundary_replay",
        "$.result",
        "方向互锁边界未通过精确重放",
    )
