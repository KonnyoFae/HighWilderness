"""d2b.1 定向控制与绑定 v2；只编译命令，不推进时间或计算力学。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from math import isfinite
import re
from typing import Any, Iterable, Mapping

from 高天荒野舰艇数据契约 import ContractError, ModulePrototypeCatalog, ResourceReference, canonical_sha256
from 高天荒野舰艇无界面舾装编译器 import CompiledOutfit
from 高天荒野舰艇战术机动求解器 import TacticalControlInput
from 高天荒野舰艇推进通道合同 import (
    TRANSLATION_CHANNELS, YAW_CHANNELS, DIRECTIONAL_CHANNELS, OPPOSING_CHANNEL_PAIRS,
    DIRECTIONAL_STATE_INTERFACE_ID, ChannelPropulsionCommand,
    DirectionalPropulsionGovernorState, exact_object, strict_stage,
)
from 高天荒野舰艇推进资源与控制桥 import (
    PropulsionActuatorBinding, TacticalPropulsionControlInput,
    bind_compiled_outfit_propulsion, migrate_known_t0_continuous_control,
    MAIN_ENGINE_QUANTIZATION_POLICY_ID, MANEUVER_QUANTIZATION_POLICY_ID,
)
from 高天荒野舰艇推进状态合同 import (
    TacticalPropulsionState, TACTICAL_PROPULSION_STATE_INTERFACE_ID,
    PropulsionGovernorState,
)

DIRECTIONAL_CONTROL_INTERFACE_ID = "gaotian.tactical-propulsion-control/v2alpha1"
DIRECTIONAL_BINDING_INTERFACE_ID = "gaotian.propulsion-actuator-binding/v2alpha1"
LINEAR_BRAKE_POLICY_ID = "gaotian.propulsion-control/translation-only-quarter-brake/v2"
IDLE_STATE_MIGRATION_ID = "gtw.migration.propulsion.d1-idle-to-directional"
BINDING_MIGRATION_ID = "gtw.migration.propulsion.d2a-binding-to-directional"
KNOWN_D2A_CONTROL_MIGRATIONS = (
    ("gtw.migration.t0.control.d2a-forward-left-to-directional", "334510b68d872394c95a371ce30eae34a60ee24ff86bbd6278013a4d1ed369d8", YAW_CHANNELS[0]),
    ("gtw.migration.t0.control.d2a-forward-right-to-directional", "b23c42e4ebf98f566e22607c0907b3dd6a5c7efcf22c0ff8ee8396d6ee518385", YAW_CHANNELS[1]),
)


@dataclass(frozen=True)
class DirectionalPropulsionControlInput:
    channel_commands: tuple[ChannelPropulsionCommand, ...]
    automatic_brake: bool = False
    overg_requested: bool = False
    source_migration_id: str | None = None

    def __post_init__(self) -> None:
        if any(not isinstance(x, ChannelPropulsionCommand) for x in self.channel_commands):
            raise ValueError("channel_commands 只接受通道命令")
        if tuple(x.command_channel for x in self.channel_commands) != DIRECTIONAL_CHANNELS:
            raise ValueError("定向命令必须按规范顺序恰含六个物理通道")
        if type(self.automatic_brake) is not bool or type(self.overg_requested) is not bool:
            raise ValueError("控制开关必须是布尔值")
        if self.source_migration_id is not None and self.source_migration_id not in {x[0] for x in KNOWN_D2A_CONTROL_MIGRATIONS}:
            raise ValueError("未知的控制来源迁移 id")
        requests = {x.command_channel: x.requested_percent for x in self.channel_commands}
        if any(requests[a] > 0 and requests[b] > 0 for a, b in OPPOSING_CHANNEL_PAIRS):
            raise ValueError("同轴对向请求尚未支持")
        if self.automatic_brake and any(requests[x] for x in YAW_CHANNELS):
            raise ValueError("自动线性制动不得请求转向推力")
        if self.automatic_brake and any(x.commanded_notch not in ("stop", "quarter") for x in self.channel_commands[:4]):
            raise ValueError("自动线性制动只能请求 quarter 或 stop")

    @classmethod
    def parse(cls, value: Any, path: str = "$") -> "DirectionalPropulsionControlInput":
        obj = exact_object(value, {"interface", "automatic_brake_policy", "main_engine_quantization_policy",
            "maneuver_quantization_policy", "channel_commands", "automatic_brake", "overg_requested", "source_migration_id"}, path)
        expected = {"interface": DIRECTIONAL_CONTROL_INTERFACE_ID, "automatic_brake_policy": LINEAR_BRAKE_POLICY_ID,
            "main_engine_quantization_policy": MAIN_ENGINE_QUANTIZATION_POLICY_ID,
            "maneuver_quantization_policy": MANEUVER_QUANTIZATION_POLICY_ID}
        if any(obj[key] != expected_value for key, expected_value in expected.items()):
            raise ContractError("propulsion_control.directional_interface", path, "接口或策略版本不匹配")
        if not isinstance(obj["channel_commands"], list):
            raise ContractError("type.array", path, "channel_commands 必须是数组")
        try:
            return cls(tuple(ChannelPropulsionCommand.parse(x, f"{path}.channel_commands[{i}]")
                for i, x in enumerate(obj["channel_commands"])), obj["automatic_brake"],
                obj["overg_requested"], obj["source_migration_id"])
        except (TypeError, ValueError) as error:
            raise ContractError("propulsion_control.directional_invariant", path, str(error)) from error

    def to_dict(self) -> dict[str, Any]:
        return {"interface": DIRECTIONAL_CONTROL_INTERFACE_ID, "automatic_brake_policy": LINEAR_BRAKE_POLICY_ID,
            "main_engine_quantization_policy": MAIN_ENGINE_QUANTIZATION_POLICY_ID,
            "maneuver_quantization_policy": MANEUVER_QUANTIZATION_POLICY_ID,
            "channel_commands": [x.to_dict() for x in self.channel_commands], "automatic_brake": self.automatic_brake,
            "overg_requested": self.overg_requested, "source_migration_id": self.source_migration_id}


def directional_control(
    commands: Iterable[ChannelPropulsionCommand] = (), *, automatic_brake: bool = False,
    overg_requested: bool = False, source_migration_id: str | None = None,
) -> DirectionalPropulsionControlInput:
    supplied = tuple(commands)
    if any(not isinstance(x, ChannelPropulsionCommand) for x in supplied):
        raise ContractError("propulsion_control.command_type", "$.commands", "必须传入通道命令")
    by_channel = {x.command_channel: x for x in supplied}
    if len(by_channel) != len(supplied):
        raise ContractError("propulsion_control.duplicate_channel", "$.commands", "通道不得重复")
    try:
        return DirectionalPropulsionControlInput(tuple(by_channel.get(x, ChannelPropulsionCommand.stop(x))
            for x in DIRECTIONAL_CHANNELS), automatic_brake, overg_requested, source_migration_id)
    except ValueError as error:
        raise ContractError("propulsion_control.directional_invariant", "$.commands", str(error)) from error


def migrate_known_d2a_control_to_directional(control: TacticalPropulsionControlInput) -> DirectionalPropulsionControlInput:
    if not isinstance(control, TacticalPropulsionControlInput):
        raise ContractError("propulsion_control.d2a_source", "$.control", "只接受 d2a 控制")
    source = canonical_sha256(control)
    specification = next((x for x in KNOWN_D2A_CONTROL_MIGRATIONS if x[1] == source), None)
    if specification is None:
        raise ContractError("propulsion_control.d2a_migration_unknown", "$.control", "仅支持已知 T0 手动命令；自动制动必须从速度重新生成")
    # 已知夹具没有 forward 机动喷口。退役该未绑定的 10% 字段，不把它变成 yaw。
    return directional_control((ChannelPropulsionCommand(TRANSLATION_CHANNELS[0], "dead_slow", None),
        ChannelPropulsionCommand(specification[2], None, 5)), source_migration_id=specification[0])


def migrate_known_t0_control_to_directional(control: TacticalControlInput) -> DirectionalPropulsionControlInput:
    return migrate_known_d2a_control_to_directional(migrate_known_t0_continuous_control(control))


@dataclass(frozen=True)
class LinearBrakeSelection:
    control: DirectionalPropulsionControlInput
    unavailable_channels: tuple[str, ...]


def automatic_linear_brake_control(*, lateral_velocity_body_mps: float,
    longitudinal_velocity_body_mps: float, available_translation_channels: Iterable[str],
    overg_requested: bool = False) -> LinearBrakeSelection:
    for value in (lateral_velocity_body_mps, longitudinal_velocity_body_mps):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
            raise ContractError("propulsion_control.brake_velocity", "$.velocity", "速度必须是有限数")
    available = tuple(available_translation_channels)
    if any(x not in TRANSLATION_CHANNELS for x in available) or len(set(available)) != len(available):
        raise ContractError("propulsion_control.brake_channels", "$.available_translation_channels", "只接受不重复的平移通道")
    requested = []
    if longitudinal_velocity_body_mps:
        requested.append(TRANSLATION_CHANNELS[1 if longitudinal_velocity_body_mps > 0 else 0])
    if lateral_velocity_body_mps:
        requested.append(TRANSLATION_CHANNELS[2 if lateral_velocity_body_mps > 0 else 3])
    return LinearBrakeSelection(directional_control(
        (ChannelPropulsionCommand(x, "quarter", None) for x in requested if x in available),
        automatic_brake=True, overg_requested=overg_requested),
        tuple(x for x in TRANSLATION_CHANNELS if x in requested and x not in available))


def validate_directional_control_transition(previous: DirectionalPropulsionControlInput,
    current: DirectionalPropulsionControlInput, actual_output_by_channel: Mapping[str, int]) -> None:
    """d4 前的显式拒绝门；不模拟方向互锁、停机或恢复。"""
    if set(actual_output_by_channel) != set(DIRECTIONAL_CHANNELS) or any(not strict_stage(x) for x in actual_output_by_channel.values()):
        raise ContractError("propulsion_control.actual_channels", "$.actual_output_by_channel", "必须提供全部通道的整数实际阶段")
    old = {x.command_channel: x.requested_percent for x in previous.channel_commands}
    new = {x.command_channel: x.requested_percent for x in current.channel_commands}
    for a, b in OPPOSING_CHANNEL_PAIRS:
        for target, opposite in ((a, b), (b, a)):
            if new[target] > 0 and (old[opposite] > 0 or actual_output_by_channel[opposite] > 0):
                raise ContractError("propulsion_control.direction_switch_unwired", "$.control", "动态反向切换留待 d4")


@dataclass(frozen=True)
class DirectionalPropulsionActuatorBinding(PropulsionActuatorBinding):
    def __post_init__(self) -> None:
        allowed = TRANSLATION_CHANNELS if self.actuator_category == "main_engine" else YAW_CHANNELS
        if not self.command_channels or self.command_channels != tuple(x for x in allowed if x in self.command_channels):
            raise ValueError("执行器用途与定向通道不匹配，或通道重复/未排序")
        if type(self.startup_steps) is not int or type(self.response_steps) is not int:
            raise ValueError("排程步数必须是整数")
        if (self.actuator_category == "main_engine" and self.startup_steps < 1) or (self.actuator_category == "maneuver_thruster" and self.startup_steps != 0):
            raise ValueError("启动步数与执行器类别不匹配")
        if not isinstance(self.module_catalog_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", self.module_catalog_sha256):
            raise ValueError("目录指纹非法")
        legacy_channels = tuple(x.removeprefix("translation.").replace("yaw.counterclockwise", "left").replace("yaw.clockwise", "right") for x in self.command_channels)
        PropulsionActuatorBinding(self.scene_id, self.ship_id, self.actuator_instance_id, self.actuator_category,
            self.prototype, legacy_channels, self.startup_steps, self.response_steps, self.module_catalog, self.module_catalog_sha256)

    @classmethod
    def parse(cls, value: Any, path: str = "$") -> "DirectionalPropulsionActuatorBinding":
        obj = exact_object(value, {"interface", "scene_id", "ship_id", "actuator_instance_id", "actuator_category", "prototype",
            "command_channels", "startup_steps", "response_steps", "module_catalog", "module_catalog_sha256"}, path)
        if obj["interface"] != DIRECTIONAL_BINDING_INTERFACE_ID:
            raise ContractError("propulsion_binding.interface", path, str(obj["interface"]))
        if not isinstance(obj["command_channels"], list):
            raise ContractError("type.array", path, "command_channels 必须是数组")
        try:
            return cls(obj["scene_id"], obj["ship_id"], obj["actuator_instance_id"], obj["actuator_category"],
                ResourceReference.parse(obj["prototype"], f"{path}.prototype"), tuple(obj["command_channels"]),
                obj["startup_steps"], obj["response_steps"], ResourceReference.parse(obj["module_catalog"], f"{path}.module_catalog"),
                obj["module_catalog_sha256"])
        except (TypeError, ValueError) as error:
            raise ContractError("propulsion_binding.invariant", path, str(error)) from error

    def to_dict(self) -> dict[str, Any]:
        value = super().to_dict()
        value["interface"] = DIRECTIONAL_BINDING_INTERFACE_ID
        return value


def bind_directional_outfit_propulsion(scene_id: str, ship_id: str, outfit: CompiledOutfit, catalog: ModulePrototypeCatalog) -> tuple[DirectionalPropulsionActuatorBinding, ...]:
    legacy = bind_compiled_outfit_propulsion(scene_id, ship_id, outfit, catalog)
    result = []
    for item in legacy:
        channels = tuple(f"translation.{x}" if item.actuator_category == "main_engine" else
            {"left": YAW_CHANNELS[0], "right": YAW_CHANNELS[1]}[x] for x in item.command_channels)
        result.append(DirectionalPropulsionActuatorBinding(item.scene_id, item.ship_id, item.actuator_instance_id,
            item.actuator_category, item.prototype, channels, item.startup_steps, item.response_steps,
            item.module_catalog, item.module_catalog_sha256))
    return tuple(result)


def validate_directional_binding(binding: DirectionalPropulsionActuatorBinding, outfit: CompiledOutfit, catalog: ModulePrototypeCatalog) -> None:
    expected = {x.actuator_instance_id: x for x in bind_directional_outfit_propulsion(binding.scene_id, binding.ship_id, outfit, catalog)}
    if expected.get(binding.actuator_instance_id) != binding:
        raise ContractError("propulsion_binding.resource_mismatch", "$.binding", "绑定与当前精确舾装/目录不一致")


def migrate_d2a_binding_to_directional(
    migration_id: str, binding: PropulsionActuatorBinding,
    outfit: CompiledOutfit, catalog: ModulePrototypeCatalog,
) -> DirectionalPropulsionActuatorBinding:
    """迁移前重建精确 d2a 来源，禁止只给旧裸通道加字符串前缀。"""
    if migration_id != BINDING_MIGRATION_ID or type(binding) is not PropulsionActuatorBinding:
        raise ContractError("propulsion_binding.migration_source", "$.binding", "未知迁移或非 d2a 绑定")
    sources = {x.actuator_instance_id: x for x in bind_compiled_outfit_propulsion(
        binding.scene_id, binding.ship_id, outfit, catalog)}
    if sources.get(binding.actuator_instance_id) != binding:
        raise ContractError("propulsion_binding.migration_reference", "$.binding", "来源不是当前精确 d2a 绑定")
    return next(x for x in bind_directional_outfit_propulsion(binding.scene_id, binding.ship_id, outfit, catalog)
        if x.actuator_instance_id == binding.actuator_instance_id)


def migrate_idle_d1_propulsion_state(migration_id: str, state: TacticalPropulsionState) -> TacticalPropulsionState:
    if migration_id != IDLE_STATE_MIGRATION_ID or state.interface_id != TACTICAL_PROPULSION_STATE_INTERFACE_ID:
        raise ContractError("propulsion_state.directional_migration_source", "$.state", "未知迁移或非 d1 来源")
    if any(x != PropulsionGovernorState.initial(x.command_channel) for x in state.governors) or any(
        x.phase not in ("off", "ready") or x.actual_output_percent != 0 or x.target_output_percent != 0 for x in state.engines
    ):
        raise ContractError("propulsion_state.directional_migration_ambiguous", "$.state", "只迁移空闲、无 governor 历史的状态")
    return replace(state, governors=tuple(DirectionalPropulsionGovernorState.initial(x) for x in DIRECTIONAL_CHANNELS),
        interface_id=DIRECTIONAL_STATE_INTERFACE_ID)
