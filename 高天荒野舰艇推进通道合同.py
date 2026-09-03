"""d2b.1：物理用途明确的推进通道及 governor v2；不依赖场景。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from 高天荒野舰艇数据契约 import ContractError
from 高天荒野舰艇推进安全判定器 import (
    SOFT_LIMIT_REASON_ORDER, TELEGRAPH_NOTCHES, THRUST_OUTPUT_STAGES_PERCENT,
    telegraph_notch_percent,
)

TRANSLATION_CHANNELS = tuple(f"translation.{x}" for x in ("forward", "reverse", "left", "right"))
YAW_CHANNELS = ("yaw.counterclockwise", "yaw.clockwise")
DIRECTIONAL_CHANNELS = TRANSLATION_CHANNELS + YAW_CHANNELS
OPPOSING_CHANNEL_PAIRS = (
    TRANSLATION_CHANNELS[:2], TRANSLATION_CHANNELS[2:], YAW_CHANNELS,
)
DIRECTIONAL_GOVERNOR_INTERFACE_ID = "gaotian.propulsion-governor-state/v2alpha1"
DIRECTIONAL_STATE_INTERFACE_ID = "gaotian.tactical-propulsion-state/v3alpha1"
DIRECTIONAL_EVENT_INTERFACE_ID = "gaotian.propulsion-state-event/v2alpha1"
D1_SCENE_INTERFACE_ID = "gaotian.tactical-scene-timeline/v3alpha1"
DIRECTIONAL_SCENE_INTERFACE_ID = "gaotian.tactical-scene-timeline/v4alpha1"
DIRECTIONAL_SCENE_POLICY_ID = "gaotian.tactical-scene/directional-propulsion-contract-only/v1"


def exact_object(value: Any, keys: set[str], path: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ContractError("object.keys", path, f"必须恰含 {sorted(keys)}")
    return value


def strict_stage(value: Any) -> bool:
    return type(value) is int and value in THRUST_OUTPUT_STAGES_PERCENT


@dataclass(frozen=True)
class ChannelPropulsionCommand:
    command_channel: str
    commanded_notch: str | None
    target_output_percent: int | None

    def __post_init__(self) -> None:
        if self.command_channel in TRANSLATION_CHANNELS:
            valid = self.commanded_notch in TELEGRAPH_NOTCHES and self.target_output_percent is None
        elif self.command_channel in YAW_CHANNELS:
            valid = self.commanded_notch is None and strict_stage(self.target_output_percent)
        else:
            valid = False
        if not valid:
            raise ValueError("平移通道只接受车钟，转向通道只接受整数离散输出")

    @property
    def requested_percent(self) -> int:
        if self.commanded_notch is not None:
            return telegraph_notch_percent(self.commanded_notch)
        assert self.target_output_percent is not None
        return self.target_output_percent

    @classmethod
    def stop(cls, channel: str) -> "ChannelPropulsionCommand":
        return cls(channel, "stop", None) if channel in TRANSLATION_CHANNELS else cls(channel, None, 0)

    @classmethod
    def parse(cls, value: Any, path: str = "$") -> "ChannelPropulsionCommand":
        obj = exact_object(value, {"command_channel", "commanded_notch", "target_output_percent"}, path)
        try:
            return cls(**obj)
        except (TypeError, ValueError) as error:
            raise ContractError("propulsion_control.channel_command", path, str(error)) from error

    def to_dict(self) -> dict[str, Any]:
        return {"command_channel": self.command_channel, "commanded_notch": self.commanded_notch,
                "target_output_percent": self.target_output_percent}


@dataclass(frozen=True)
class DirectionalPropulsionGovernorState:
    command: ChannelPropulsionCommand
    safety_ceiling_percent: int = 100
    safety_reasons: tuple[str, ...] = ()
    safety_limited_since_step: int | None = None
    release_candidate_since_step: int | None = None
    last_evaluated_step_index: int | None = None
    safety_revision: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.command, ChannelPropulsionCommand) or not strict_stage(self.safety_ceiling_percent):
            raise ValueError("governor 命令或安全阶段非法")
        if self.safety_reasons != tuple(x for x in SOFT_LIMIT_REASON_ORDER if x in self.safety_reasons):
            raise ValueError("安全原因必须按稳定顺序排列且不得重复")
        limited = self.safety_ceiling_percent < 100
        if limited != bool(self.safety_reasons) or limited != (self.safety_limited_since_step is not None):
            raise ValueError("安全阶段、原因与介入步不匹配")
        if not limited and self.release_candidate_since_step is not None:
            raise ValueError("未限幅状态不得保存释放候选步")
        for step in (self.safety_limited_since_step, self.release_candidate_since_step, self.last_evaluated_step_index):
            if step is not None and (type(step) is not int or step < 0):
                raise ValueError("governor 步号必须是非负整数")
        if type(self.safety_revision) is not int or self.safety_revision < 0:
            raise ValueError("safety_revision 非法")
        if self.release_candidate_since_step is not None and self.release_candidate_since_step < self.safety_limited_since_step:
            raise ValueError("释放候选步不得早于介入步")

    @property
    def command_channel(self) -> str:
        return self.command.command_channel

    @classmethod
    def initial(cls, channel: str) -> "DirectionalPropulsionGovernorState":
        return cls(ChannelPropulsionCommand.stop(channel))

    @classmethod
    def parse(cls, value: Any, path: str) -> "DirectionalPropulsionGovernorState":
        obj = exact_object(value, {"command", "interface", "safety_ceiling_percent", "safety_reasons",
            "safety_limited_since_step", "release_candidate_since_step", "last_evaluated_step_index", "safety_revision"}, path)
        if obj["interface"] != DIRECTIONAL_GOVERNOR_INTERFACE_ID:
            raise ContractError("propulsion_state.governor_interface", path, str(obj["interface"]))
        if not isinstance(obj["safety_reasons"], list) or any(not isinstance(x, str) for x in obj["safety_reasons"]):
            raise ContractError("type.string_array", path, "安全原因必须是字符串数组")
        try:
            return cls(ChannelPropulsionCommand.parse(obj["command"], f"{path}.command"),
                obj["safety_ceiling_percent"], tuple(obj["safety_reasons"]), obj["safety_limited_since_step"],
                obj["release_candidate_since_step"], obj["last_evaluated_step_index"], obj["safety_revision"])
        except (TypeError, ValueError) as error:
            raise ContractError("propulsion_state.governor_invariant", path, str(error)) from error

    def to_dict(self) -> dict[str, Any]:
        return {"interface": DIRECTIONAL_GOVERNOR_INTERFACE_ID, "command": self.command.to_dict(),
            "safety_ceiling_percent": self.safety_ceiling_percent, "safety_reasons": list(self.safety_reasons),
            "safety_limited_since_step": self.safety_limited_since_step,
            "release_candidate_since_step": self.release_candidate_since_step,
            "last_evaluated_step_index": self.last_evaluated_step_index, "safety_revision": self.safety_revision}
