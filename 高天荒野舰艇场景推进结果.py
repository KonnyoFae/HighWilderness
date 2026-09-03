"""d2b.1 场景相关适配层：具名状态迁移与首尾边界结果，不接入力学。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Iterable

from 高天荒野舰艇数据契约 import ContractError, ModuleCapability, RESOURCE_ID_PATTERN, canonical_sha256
from 高天荒野舰艇推进通道合同 import (
    DIRECTIONAL_SCENE_INTERFACE_ID, DIRECTIONAL_EVENT_INTERFACE_ID, D1_SCENE_INTERFACE_ID,
    TRANSLATION_CHANNELS, YAW_CHANNELS, exact_object,
)
from 高天荒野舰艇定向推进控制桥 import IDLE_STATE_MIGRATION_ID, migrate_idle_d1_propulsion_state
from 高天荒野舰艇推进状态合同 import EngineRuntimeState, PropulsionStateEvent, PROPULSION_STATE_EVENT_INTERFACE_ID, SAFETY_EVENT_KINDS
from 高天荒野舰艇推进时间内核 import PropulsionTimeCommand, PropulsionTimeBoundaryResult, advance_propulsion_time_boundary
from 高天荒野舰艇统一战术场景 import TacticalSceneState, TacticalSceneStepResolution

DIRECTIONAL_SCENE_EVENT_INTERFACE_ID = "gaotian.tactical-scene-propulsion-event/v2alpha1"
INTERVAL_STEP_RESULT_INTERFACE_ID = "gaotian.tactical-scene-step-resolution/v3alpha1"
INTERVAL_STEP_POLICY_ID = "gaotian.tactical-scene-step/open-integrate-close-propulsion/v2"
BOUNDARY_PHASES = ("opening", "closing")
ENGINE_EVENT_MIGRATION_ID = "gtw.migration.propulsion.channel-free-engine-event-v1-to-v2"
KNOWN_D1_INITIAL_SCENE_HASHES = {
    "functional_6.guided_projectiles": "9855ef6792314b82477d899c0242dbb26dda8a42e70917f875fc17d786fc85ef",
    "functional_6.motion_only": "bebcf376b14060e032783be7b83ae020d59815b009161f7f2e1c2cad86cd667d",
    "functional_6.ordinary_projectiles": "62fe481a028df3eff9c5e83a36fafa3607eb0bd32ac00f69c0ec0b6cc6d94d36",
    "functional_6.scripted_damage_and_recompile": "ae32ce92ea7706322a72954ee9424de025f165dd96393f977a811d01785c52ed",
    "stress_30.guided_projectiles": "57969cea7afb603286ee2e4f152ddf8088ea35e65bac40412e727fe60e4b1594",
    "stress_30.motion_only": "0ec6638a4437ac829a8a3cf1167a1996da8caf2caa24a936ad9b88c9fb38cf5b",
    "stress_30.ordinary_projectiles": "a3d13ffddcdc3df1738f012f14fc3ebf1c04527377223a7e7c53269c472c36c8",
    "stress_30.scripted_damage_and_recompile": "0e361265f9488d5ad9d138dc0c18c58165e3ae391e6e4a29528480b706187c13",
    "target_20.guided_projectiles": "87dfda7f721f0223a97a4aa0cc8a782706137be80e396118431d00e2e8a2bd1d",
    "target_20.motion_only": "26bd89fb88a6495ebece0fbd2aae2deed03a61c241617908214c7822dedc5da0",
    "target_20.ordinary_projectiles": "571554b204f8df650fdd8e0ea6bfc7a3bef9c1322d54f03cbcb9f8e7f5769dc8",
    "target_20.scripted_damage_and_recompile": "49ecc29217ae73fa54dc75d609a9812ea761ee45fb7adc46e9734a03c3448f92",
}


def migrate_known_d1_scene_to_directional(migration_id: str, state: TacticalSceneState) -> TacticalSceneState:
    """只升级已知初始状态的通道合同；快照/出航资源链仍留给 d2b.2。"""
    if state.to_dict()["interface"] != D1_SCENE_INTERFACE_ID or state.fixed_step_index != 0:
        raise ContractError("tactical_scene.directional_migration_source", "$.scene", "必须是 d1 初始场景")
    expected = KNOWN_D1_INITIAL_SCENE_HASHES.get(migration_id)
    if expected is None or canonical_sha256(state) != expected:
        raise ContractError("tactical_scene.directional_migration_hash", "$.scene", "未知具名来源或场景指纹不匹配")
    result = replace(state, ships=tuple(replace(ship,
        propulsion_state=migrate_idle_d1_propulsion_state(IDLE_STATE_MIGRATION_ID, ship.propulsion_state))
        for ship in state.ships))
    return TacticalSceneState.parse(result.to_dict())


def migrate_channel_free_engine_event(migration_id: str, event: PropulsionStateEvent) -> PropulsionStateEvent:
    if migration_id != ENGINE_EVENT_MIGRATION_ID or event.interface_id != PROPULSION_STATE_EVENT_INTERFACE_ID or event.command_channel is not None:
        raise ContractError("propulsion_event.directional_migration_source", "$.event", "只迁移无通道歧义的 v1 发动机事件")
    return replace(event, interface_id=DIRECTIONAL_EVENT_INTERFACE_ID)


@dataclass(frozen=True)
class BoundaryScenePropulsionEvent:
    ship_id: str
    boundary_phase: str
    event: PropulsionStateEvent

    def __post_init__(self) -> None:
        if not isinstance(self.ship_id, str) or not RESOURCE_ID_PATTERN.fullmatch(self.ship_id):
            raise ValueError("ship_id 非法")
        if self.boundary_phase not in BOUNDARY_PHASES or self.event.interface_id != DIRECTIONAL_EVENT_INTERFACE_ID:
            raise ValueError("边界阶段或推进事件版本非法")

    @property
    def sort_key(self) -> tuple[int, int, str, str, int]:
        step, actuator, kind = self.event.sort_key
        return step, BOUNDARY_PHASES.index(self.boundary_phase), self.ship_id, actuator, kind

    @classmethod
    def parse(cls, value: Any, path: str = "$") -> "BoundaryScenePropulsionEvent":
        obj = exact_object(value, {"interface", "ship_id", "boundary_phase", "event"}, path)
        if obj["interface"] != DIRECTIONAL_SCENE_EVENT_INTERFACE_ID:
            raise ContractError("propulsion_event.scene_interface", path, str(obj["interface"]))
        try:
            return cls(obj["ship_id"], obj["boundary_phase"], PropulsionStateEvent.parse(obj["event"], f"{path}.event"))
        except (TypeError, ValueError) as error:
            raise ContractError("propulsion_event.scene_invariant", path, str(error)) from error

    def to_dict(self) -> dict[str, Any]:
        return {"interface": DIRECTIONAL_SCENE_EVENT_INTERFACE_ID, "ship_id": self.ship_id,
            "boundary_phase": self.boundary_phase, "event": self.event.to_dict()}


def propulsion_time_interval(
    state: EngineRuntimeState, capability: ModuleCapability, source_step: int,
    command: PropulsionTimeCommand,
) -> tuple[PropulsionTimeBoundaryResult, PropulsionTimeBoundaryResult]:
    """纯时间合同探针：opening 的实际阶段用于积分，closing 的状态用于持久化。"""
    if state.next_transition_step is not None and state.next_transition_step <= source_step:
        raise ContractError("propulsion_interval.source_uncommitted", "$.state", "源步必须已经提交到期转换")
    opening = advance_propulsion_time_boundary(state, capability, source_step, command)
    closing = advance_propulsion_time_boundary(opening.state, capability, source_step + 1, command)
    return opening, closing


@dataclass(frozen=True)
class IntervalPropulsionStepResolution:
    source_scene: TacticalSceneState
    base_resolution: TacticalSceneStepResolution
    propulsion_events: tuple[BoundaryScenePropulsionEvent, ...]

    def __post_init__(self) -> None:
        source, result = self.source_scene, self.base_resolution.resulting_scene
        for scene in (source, result):
            if scene.to_dict()["interface"] != DIRECTIONAL_SCENE_INTERFACE_ID:
                raise ValueError("新结果必须绑定定向推进场景 v4")
            TacticalSceneState.parse(scene.to_dict())
        if self.base_resolution.source_scene_sha256 != canonical_sha256(source):
            raise ValueError("结果的源场景指纹不匹配")
        if result.fixed_step_index != source.fixed_step_index + 1 or result.fixed_step_s != source.fixed_step_s or abs(result.tactical_time_s - source.tactical_time_s - source.fixed_step_s) > 1e-8:
            raise ValueError("结果必须恰好覆盖一个固定步")
        if (source.propulsion_safety_profile, source.propulsion_safety_profile_sha256) != (result.propulsion_safety_profile, result.propulsion_safety_profile_sha256):
            raise ValueError("推进配置引用与指纹不得在单步结果中丢失或替换")
        source_governors = {(ship.ship_id, governor.command_channel): governor
            for ship in source.ships for governor in ship.propulsion_state.governors}
        for ship in result.ships:
            for governor in ship.propulsion_state.governors:
                previous = source_governors.get((ship.ship_id, governor.command_channel))
                if previous is None or replace(governor, command=previous.command) != previous:
                    raise ValueError("当前结果策略不接受尚未接线的 governor 安全状态变化")
        old = {(ship.ship_id, engine.actuator_instance_id): engine for ship in source.ships for engine in ship.propulsion_state.engines}
        new = {(ship.ship_id, engine.actuator_instance_id): engine for ship in result.ships for engine in ship.propulsion_state.engines}
        if old.keys() != new.keys() or any(old[key].actuator_category != new[key].actuator_category for key in old):
            raise ValueError("单步结果不得改变推进执行器身份/类别")
        keys = tuple(x.sort_key for x in self.propulsion_events)
        if keys != tuple(sorted(keys)) or len(set(keys)) != len(keys):
            raise ValueError("推进事件必须稳定排序且不得重复")
        phases = {key: engine.phase for key, engine in old.items()}
        stages = {key: engine.actual_output_percent for key, engine in old.items()}
        for wrapped in self.propulsion_events:
            event = wrapped.event
            expected_step = source.fixed_step_index if wrapped.boundary_phase == "opening" else result.fixed_step_index
            if event.fixed_step_index != expected_step:
                raise ValueError("推进事件步号与首尾边界归属不匹配")
            key = (wrapped.ship_id, event.actuator_instance_id)
            if key not in old:
                raise ValueError("推进事件必须引用同舰的已知执行器")
            allowed = TRANSLATION_CHANNELS if old[key].actuator_category == "main_engine" else YAW_CHANNELS
            if event.command_channel is not None and event.command_channel not in allowed:
                raise ValueError("事件通道与执行器物理类别不匹配")
            if event.kind in SAFETY_EVENT_KINDS:
                raise ValueError("安全事件的场景接线留待 d3")
            if event.kind in ("engine_tripped", "engine_reset"):
                raise ValueError("硬故障事件的场景接线留待 d4")
            if event.previous_phase is not None:
                if phases[key] != event.previous_phase:
                    raise ValueError("发动机 phase 事件链不连续")
                phases[key] = event.resulting_phase
            if event.previous_stage_percent is not None:
                if stages[key] != event.previous_stage_percent:
                    raise ValueError("发动机实际输出事件链不连续")
                stages[key] = event.resulting_stage_percent
        if any(phases[key] != engine.phase or stages[key] != engine.actual_output_percent for key, engine in new.items()):
            raise ValueError("推进结果缺少对应的 phase/实际输出变化事件")

    def to_dict(self) -> dict[str, Any]:
        value = self.base_resolution.to_dict()
        value.update(interface=INTERVAL_STEP_RESULT_INTERFACE_ID, policy=INTERVAL_STEP_POLICY_ID,
            source_fixed_step_index=self.source_scene.fixed_step_index,
            resulting_fixed_step_index=self.base_resolution.resulting_scene.fixed_step_index,
            propulsion_events=[x.to_dict() for x in self.propulsion_events])
        return value

    @classmethod
    def parse(
        cls, value: Any, source_scene: TacticalSceneState,
        base_resolution: TacticalSceneStepResolution, path: str = "$",
    ) -> "IntervalPropulsionStepResolution":
        """结果只传场景 hash；载入必须同时提供精确源场景和完整基础结果。"""
        expected_keys = set(base_resolution.to_dict()) | {
            "source_fixed_step_index", "resulting_fixed_step_index", "propulsion_events",
        }
        obj = exact_object(value, expected_keys, path)
        if not isinstance(obj["propulsion_events"], list):
            raise ContractError("type.array", path, "propulsion_events 必须是数组")
        for key in ("source_fixed_step_index", "resulting_fixed_step_index"):
            if type(obj[key]) is not int or obj[key] < 0:
                raise ContractError("type.integer", f"{path}.{key}", "必须是非负整数")
        try:
            result = cls(source_scene, base_resolution, tuple(
                BoundaryScenePropulsionEvent.parse(x, f"{path}.propulsion_events[{i}]")
                for i, x in enumerate(obj["propulsion_events"])))
            if obj != result.to_dict():
                raise ValueError("结果接口、策略、步号、场景指纹或基础事件内容不匹配")
            return result
        except (TypeError, ValueError) as error:
            raise ContractError("propulsion_interval.result_invariant", path, str(error)) from error


def build_interval_propulsion_step_resolution(source_scene: TacticalSceneState,
    base_resolution: TacticalSceneStepResolution,
    events: Iterable[BoundaryScenePropulsionEvent]) -> IntervalPropulsionStepResolution:
    try:
        return IntervalPropulsionStepResolution(source_scene, base_resolution, tuple(sorted(events, key=lambda x: x.sort_key)))
    except ValueError as error:
        raise ContractError("propulsion_interval.result_invariant", "$.result", str(error)) from error
