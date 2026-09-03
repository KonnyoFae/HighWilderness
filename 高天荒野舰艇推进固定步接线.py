"""d2b.4 的无场景依赖接线层：资源身份、命令持久化、首尾内核调用及审计。"""

from __future__ import annotations

from dataclasses import dataclass, replace
import re
from typing import Any, Mapping

from 高天荒野舰艇数据契约 import ContractError, RESOURCE_ID_PATTERN, canonical_sha256
from 高天荒野舰艇实际推进合同 import fixed_step_index
from 高天荒野舰艇实际推进聚合器 import ActualPropulsionContext
from 高天荒野舰艇推进安全判定器 import PropulsionSafetyProfile
from 高天荒野舰艇推进状态合同 import EngineRuntimeState, TacticalPropulsionState, PropulsionStateEvent
from 高天荒野舰艇推进时间内核 import (
    PropulsionTimeCommand, advance_propulsion_time_boundary, validate_committed_propulsion_time_state,
)
from 高天荒野舰艇定向推进控制桥 import (
    DirectionalPropulsionControlInput, directional_control, automatic_linear_brake_control,
    validate_directional_control_transition,
)
from 高天荒野舰艇推进通道合同 import (
    DIRECTIONAL_CHANNELS, TRANSLATION_CHANNELS, DIRECTIONAL_STATE_INTERFACE_ID,
    DIRECTIONAL_EVENT_INTERFACE_ID, DirectionalPropulsionGovernorState, exact_object,
)

ACTUAL_SCENE_INTERFACE_ID = "gaotian.tactical-scene-timeline/v5alpha1"
ACTUAL_SCENE_POLICY_ID = "gaotian.tactical-scene/actual-propulsion-unprotected/v1"
ACTUAL_STEP_INTERFACE_ID = "gaotian.tactical-scene-step-resolution/v4alpha1"
ACTUAL_STEP_POLICY_ID = "gaotian.tactical-scene-step/actual-open-integrate-close-unprotected/v1"
ACTUAL_BOUNDARY_INTERFACE_ID = "gaotian.actual-propulsion-boundary/v1alpha1"
BOUNDARY_PHASES = ("opening", "closing")


def _hash(value: Any, path: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ContractError("actual_scene.sha256", path, "必须是规范 SHA-256")
    return value


@dataclass(frozen=True)
class ActualPropulsionExecution:
    scene_id: str
    resource_bundle_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.scene_id, str) or not RESOURCE_ID_PATTERN.fullmatch(self.scene_id):
            raise ContractError("actual_scene.scene_id", "$.scene_id", "场景身份非法")
        _hash(self.resource_bundle_sha256, "$.resource_bundle_sha256")

    def to_dict(self) -> dict[str, Any]:
        return {"scene_id": self.scene_id, "resource_bundle_sha256": self.resource_bundle_sha256}

    @classmethod
    def parse(cls, value: Any, path: str = "$") -> "ActualPropulsionExecution":
        return cls(**exact_object(value, {"scene_id", "resource_bundle_sha256"}, path))


@dataclass(frozen=True)
class ActualShipPropulsionResources:
    aggregation_context: ActualPropulsionContext
    sortie_configuration_sha256: str

    def __post_init__(self) -> None:
        _hash(self.sortie_configuration_sha256, "$.sortie_configuration_sha256")

    @property
    def ship_id(self) -> str:
        return self.aggregation_context.ship_id


@dataclass(frozen=True)
class ActualScenePropulsionContext:
    execution: ActualPropulsionExecution
    safety_profile: PropulsionSafetyProfile
    ships: tuple[ActualShipPropulsionResources, ...]

    def __post_init__(self) -> None:
        ids = tuple(s.ship_id for s in self.ships)
        if not ids or ids != tuple(sorted(set(ids))):
            raise ContractError("actual_scene.context_ship_set", "$.ships", "接线资源必须逐舰唯一且排序")
        if any(s.aggregation_context.scene_id != self.execution.scene_id for s in self.ships):
            raise ContractError("actual_scene.context_scene", "$.ships", "接线资源不得跨场景")
        if canonical_sha256(self.safety_profile) != self.safety_profile.source_sha256:
            raise ContractError("actual_scene.profile_hash", "$.safety_profile", "配置指纹失效")

    def ship(self, ship_id: str) -> ActualShipPropulsionResources:
        for ship in self.ships:
            if ship.ship_id == ship_id:
                return ship
        raise ContractError("actual_scene.context_ship", "$.ship_id", ship_id)


@dataclass(frozen=True)
class ActualPropulsionBoundaryRecord:
    ship_id: str
    boundary_phase: str
    fixed_step_index: int
    before: EngineRuntimeState
    command: PropulsionTimeCommand
    after: EngineRuntimeState
    events: tuple[PropulsionStateEvent, ...]

    def __post_init__(self) -> None:
        fixed_step_index(self.fixed_step_index)
        if not isinstance(self.ship_id, str) or not RESOURCE_ID_PATTERN.fullmatch(self.ship_id):
            raise ContractError("actual_scene.boundary_ship", "$.ship_id", "舰艇身份非法")
        if self.boundary_phase not in BOUNDARY_PHASES:
            raise ContractError("actual_scene.boundary_phase", "$.boundary_phase", "只接受首尾边界")
        if (self.before.actuator_instance_id, self.before.actuator_category) != (
            self.after.actuator_instance_id, self.after.actuator_category
        ) or self.before == self.after:
            raise ContractError("actual_scene.boundary_change", "$.after", "必须记录同一执行器的实际状态变化")
        EngineRuntimeState.parse(self.before.to_dict(), "$.before")
        EngineRuntimeState.parse(self.after.to_dict(), "$.after")
        self.command.target_for(self.before.actuator_category)
        if any(e.fixed_step_index != self.fixed_step_index or e.actuator_instance_id != self.before.actuator_instance_id for e in self.events):
            raise ContractError("actual_scene.boundary_event", "$.events", "事件必须属于该执行器的真实边界")

    @property
    def sort_key(self) -> tuple[int, int, str, str]:
        return self.fixed_step_index, BOUNDARY_PHASES.index(self.boundary_phase), self.ship_id, self.before.actuator_instance_id

    def to_dict(self) -> dict[str, Any]:
        return {"interface": ACTUAL_BOUNDARY_INTERFACE_ID, "ship_id": self.ship_id,
                "boundary_phase": self.boundary_phase, "fixed_step_index": self.fixed_step_index,
                "before": self.before.to_dict(), "after": self.after.to_dict(),
                "command": {"commanded_notch": self.command.commanded_notch,
                            "target_output_percent": self.command.target_output_percent},
                "events": [e.to_dict() for e in self.events]}

    @classmethod
    def parse(cls, value: Any, path: str = "$") -> "ActualPropulsionBoundaryRecord":
        obj = exact_object(value, {"interface", "ship_id", "boundary_phase", "fixed_step_index", "before", "command", "after", "events"}, path)
        if obj["interface"] != ACTUAL_BOUNDARY_INTERFACE_ID or not isinstance(obj["events"], list):
            raise ContractError("actual_scene.boundary_interface", path, "边界版本或事件列表非法")
        command = exact_object(obj["command"], {"commanded_notch", "target_output_percent"}, f"{path}.command")
        try:
            return cls(obj["ship_id"], obj["boundary_phase"], obj["fixed_step_index"],
                EngineRuntimeState.parse(obj["before"], f"{path}.before"), PropulsionTimeCommand(**command),
                EngineRuntimeState.parse(obj["after"], f"{path}.after"),
                tuple(PropulsionStateEvent.parse(e, f"{path}.events") for e in obj["events"]))
        except ValueError as error:
            raise ContractError("actual_scene.boundary_invariant", path, str(error)) from error


def validate_ship_propulsion_state(resources: ActualShipPropulsionResources,
    state: TacticalPropulsionState, control: DirectionalPropulsionControlInput,
    boundary_step: int | None = None) -> None:
    TacticalPropulsionState.parse(state.to_dict(), "$.propulsion_state")
    DirectionalPropulsionControlInput.parse(control.to_dict())
    if state.interface_id != DIRECTIONAL_STATE_INTERFACE_ID:
        raise ContractError("actual_scene.state_interface", "$.propulsion_state", "只接受定向状态")
    expected = {b.actuator_instance_id: b for b in resources.aggregation_context.bindings}
    if set(expected) != {e.actuator_instance_id for e in state.engines}:
        raise ContractError("actual_scene.engine_set", "$.engines", "时间状态必须保留全部精确执行器")
    commands = {c.command_channel: c for c in control.channel_commands}
    for governor in state.governors:
        if replace(governor, command=DirectionalPropulsionGovernorState.initial(governor.command_channel).command) != DirectionalPropulsionGovernorState.initial(governor.command_channel):
            raise ContractError("actual_scene.governor_unwired", "$.governors", "尚未支持软保护历史")
        if governor.command != commands[governor.command_channel]:
            raise ContractError("actual_scene.control_state", "$.governors", "持久控制与 governor 命令不一致")
    for engine in state.engines:
        binding = expected[engine.actuator_instance_id]
        if boundary_step is not None:
            validate_committed_propulsion_time_state(engine,
                resources.aggregation_context.catalog.module(binding.prototype).capability, boundary_step)
        command = commands[binding.command_channels[0]]
        if engine.actuator_category != binding.actuator_category or (
            engine.commanded_notch, engine.target_output_percent
        ) != (command.commanded_notch, command.requested_percent):
            raise ContractError("actual_scene.engine_command", "$.engines", "发动机目标与持久通道命令不一致")
    active = {c: 0 for c in DIRECTIONAL_CHANNELS}
    for engine in state.engines:
        channel = expected[engine.actuator_instance_id].command_channels[0]
        active[channel] = max(active[channel], engine.actual_output_percent)
    # 只做反向门禁；max 只用于冲突检测，绝不参与力学聚合。
    validate_directional_control_transition(control, control, active)


def select_actual_scene_control(resources: ActualShipPropulsionResources,
    state: TacticalPropulsionState, previous: DirectionalPropulsionControlInput,
    requested: DirectionalPropulsionControlInput | None, *, velocity_body: tuple[float, float],
    command_available: bool,
) -> tuple[DirectionalPropulsionControlInput, tuple[str, ...]]:
    validate_ship_propulsion_state(resources, state, previous)
    control = previous if requested is None else requested
    if not isinstance(control, DirectionalPropulsionControlInput):
        raise ContractError("actual_scene.control_type", "$.propulsion_controls", "新路径只接受定向控制")
    DirectionalPropulsionControlInput.parse(control.to_dict())
    available = {c for b in resources.aggregation_context.bindings for c in b.command_channels}
    unavailable = ()
    if not command_available:
        control = directional_control()
    elif control.automatic_brake:
        selection = automatic_linear_brake_control(lateral_velocity_body_mps=velocity_body[0],
            longitudinal_velocity_body_mps=velocity_body[1],
            available_translation_channels=tuple(c for c in TRANSLATION_CHANNELS if c in available),
            overg_requested=control.overg_requested)
        control, unavailable = selection.control, selection.unavailable_channels
    else:
        unavailable = tuple(c.command_channel for c in control.channel_commands if c.requested_percent and c.command_channel not in available)
    active = {c: 0 for c in DIRECTIONAL_CHANNELS}
    by_id = {b.actuator_instance_id: b for b in resources.aggregation_context.bindings}
    for engine in state.engines:
        channel = by_id[engine.actuator_instance_id].command_channels[0]
        active[channel] = max(active[channel], engine.actual_output_percent)
    validate_directional_control_transition(previous, control, active)
    return control, unavailable


def advance_ship_propulsion_boundary(resources: ActualShipPropulsionResources,
    state: TacticalPropulsionState, control: DirectionalPropulsionControlInput,
    boundary_step: int, boundary_phase: str,
) -> tuple[TacticalPropulsionState, tuple[ActualPropulsionBoundaryRecord, ...]]:
    bindings = {b.actuator_instance_id: b for b in resources.aggregation_context.bindings}
    commands = {c.command_channel: c for c in control.channel_commands}
    engines, records = [], []
    for engine in state.engines:
        binding = bindings[engine.actuator_instance_id]
        channel = commands[binding.command_channels[0]]
        command = PropulsionTimeCommand(channel.commanded_notch, channel.target_output_percent)
        capability = resources.aggregation_context.catalog.module(binding.prototype).capability
        result = advance_propulsion_time_boundary(engine, capability, boundary_step, command)
        engines.append(result.state)
        if result.state != engine:
            records.append(ActualPropulsionBoundaryRecord(resources.ship_id, boundary_phase,
                boundary_step, engine, command, result.state, result.events))
        elif result.events:
            raise ContractError("actual_scene.event_without_change", "$.events", "内核事件必须有对应状态变化")
    governors = tuple(replace(g, command=commands[g.command_channel]) for g in state.governors)
    result = replace(state, engines=tuple(engines), governors=governors)
    validate_ship_propulsion_state(resources, result, control, boundary_step)
    return result, tuple(records)


def serialized_propulsion_events(records: tuple[ActualPropulsionBoundaryRecord, ...]) -> list[dict[str, Any]]:
    events = []
    for record in records:
        for event in record.events:
            migrated = replace(event, interface_id=DIRECTIONAL_EVENT_INTERFACE_ID)
            key = (event.fixed_step_index, BOUNDARY_PHASES.index(record.boundary_phase), record.ship_id, *event.sort_key[1:])
            events.append((key, {"interface": "gaotian.tactical-scene-propulsion-event/v2alpha1",
                "ship_id": record.ship_id, "boundary_phase": record.boundary_phase, "event": migrated.to_dict()}))
    keys = [key for key, _ in events]
    if len(set(keys)) != len(keys):
        raise ContractError("actual_scene.event_duplicate", "$.events", "同一边界事件不得重复")
    return [event for _, event in sorted(events, key=lambda pair: pair[0])]


def validate_boundary_replay(source_states: Mapping[str, TacticalPropulsionState],
    resulting_states: Mapping[str, TacticalPropulsionState], context: ActualScenePropulsionContext,
    source_step: int, records: tuple[ActualPropulsionBoundaryRecord, ...],
) -> None:
    """精确重放边界记录；不靠 phase 事件推断未发事件的接令/排程变化。"""
    keys = tuple(r.sort_key for r in records)
    if keys != tuple(sorted(set(keys))):
        raise ContractError("actual_scene.boundary_order", "$.propulsion_boundaries", "边界必须唯一且稳定排序")
    current = {(ship, e.actuator_instance_id): e for ship, state in source_states.items() for e in state.engines}
    for record in records:
        key = record.ship_id, record.before.actuator_instance_id
        if record.fixed_step_index != source_step + BOUNDARY_PHASES.index(record.boundary_phase) or current.get(key) != record.before:
            raise ContractError("actual_scene.boundary_chain", "$.propulsion_boundaries", "源状态或首尾步号不匹配")
        resources = context.ship(record.ship_id)
        binding = next(b for b in resources.aggregation_context.bindings if b.actuator_instance_id == key[1])
        capability = resources.aggregation_context.catalog.module(binding.prototype).capability
        result = advance_propulsion_time_boundary(record.before, capability, record.fixed_step_index, record.command)
        if result.state != record.after or result.events != record.events:
            raise ContractError("actual_scene.boundary_replay", "$.propulsion_boundaries", "记录与精确时间内核不一致")
        current[key] = record.after
    final = {(ship, e.actuator_instance_id): e for ship, state in resulting_states.items() for e in state.engines}
    if current != final:
        raise ContractError("actual_scene.boundary_incomplete", "$.propulsion_boundaries", "结果状态缺少对应边界记录")
    serialized_propulsion_events(records)
