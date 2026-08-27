"""《高天荒野》阶段 I4：武器战术时钟、连续射击、齐射与装填队列。"""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
from math import isfinite
from pathlib import Path
from typing import Any, Iterable

from 高天荒野舰艇数据契约 import (
    ContractError,
    RESOURCE_ID_PATTERN,
    ResourceReference,
    ShipInstanceSnapshotInput,
    WeaponFireSequenceStateInput,
    WeaponTimelineClockInput,
    WeaponTimelineStateInput,
    canonical_sha256,
)
from 高天荒野舰艇出航配置编译器 import (
    CompiledSortieState,
    validate_ship_ammunition_state,
)
from 高天荒野舰艇实例设计状态 import validate_instance_current_design
from 高天荒野舰艇无界面舾装编译器 import (
    CompiledModuleInstance,
    DerivedShipSnapshot,
)
from 高天荒野舰艇弹药与武器动作结算器 import (
    WeaponActionResolution,
    WeaponFireRequest,
    WeaponReloadRequest,
    resolve_weapon_fire,
    resolve_weapon_reload,
)


WEAPON_TIMELINE_INTERFACE_ID = "gaotian.weapon-action-timeline/v1alpha1"
WEAPON_TIMING_SCHEMA_ID = "gaotian.weapon-timing/v1alpha1"
WEAPON_TIMELINE_POLICY_ID = "gaotian.weapon-timeline/event-ordered-atomic-actions/v1"
FIXTURE_LEVELS = {"contract_fixture", "prototype_unbalanced", "balance_reference"}
EPS = 1.0e-8
MAX_EVENTS_PER_ADVANCE = 10000


def _resource_id(value: Any, path: str) -> str:
    if not isinstance(value, str) or not RESOURCE_ID_PATTERN.fullmatch(value):
        raise ContractError(
            "resource.id_invalid",
            path,
            "只能使用小写字母、数字、点、横线和下划线",
        )
    return value


def _positive_number(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError("type.number", path, "必须是数值")
    result = float(value)
    if not isfinite(result) or result <= 0.0:
        raise ContractError("value.positive", path, "必须是正有限数")
    return result


@dataclass(frozen=True)
class WeaponTimingProfile:
    prototype: ResourceReference
    cyclic_rate_rpm: float
    reload_seconds_per_round: float

    @classmethod
    def parse(cls, value: Any, path: str) -> "WeaponTimingProfile":
        if not isinstance(value, dict) or set(value) != {
            "prototype",
            "cyclic_rate_rpm",
            "reload_seconds_per_round",
        }:
            raise ContractError(
                "weapon_timing.profile_keys",
                path,
                "时间配置必须恰含原型、循环射速和逐发装填基准时长",
            )
        return cls(
            ResourceReference.parse(value["prototype"], f"{path}.prototype"),
            _positive_number(value["cyclic_rate_rpm"], f"{path}.cyclic_rate_rpm"),
            _positive_number(
                value["reload_seconds_per_round"],
                f"{path}.reload_seconds_per_round",
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "cyclic_rate_rpm": self.cyclic_rate_rpm,
            "prototype": self.prototype.to_dict(),
            "reload_seconds_per_round": self.reload_seconds_per_round,
        }


@dataclass(frozen=True)
class WeaponTimingProfileCatalog:
    id: str
    version: int
    name: str
    fixture_level: str
    profiles: tuple[WeaponTimingProfile, ...]

    @property
    def reference(self) -> ResourceReference:
        return ResourceReference(self.id, self.version)

    @property
    def source_sha256(self) -> str:
        return canonical_sha256(self)

    def profile(self, prototype: ResourceReference) -> WeaponTimingProfile:
        try:
            return next(item for item in self.profiles if item.prototype == prototype)
        except StopIteration as error:
            raise ContractError(
                "weapon_timing.profile_missing",
                "$.profiles",
                f"缺少武器原型 {prototype} 的时间配置",
            ) from error

    @classmethod
    def parse(cls, value: Any, path: str = "$") -> "WeaponTimingProfileCatalog":
        if not isinstance(value, dict):
            raise ContractError("type.object", path, "必须是对象")
        required = {
            "schema",
            "kind",
            "id",
            "version",
            "name",
            "fixture_level",
            "profiles",
        }
        if set(value) != required:
            raise ContractError("object.keys", path, f"必须恰含 {sorted(required)}")
        if (
            value["schema"] != WEAPON_TIMING_SCHEMA_ID
            or value["kind"] != "WeaponTimingProfileCatalog"
        ):
            raise ContractError("resource.kind", path, "不是武器时间配置目录")
        if (
            isinstance(value["version"], bool)
            or not isinstance(value["version"], int)
            or value["version"] < 1
        ):
            raise ContractError("value.version", f"{path}.version", "版本必须是正整数")
        fixture = value["fixture_level"]
        if not isinstance(fixture, str) or fixture not in FIXTURE_LEVELS:
            raise ContractError(
                "weapon_timing.fixture_level", f"{path}.fixture_level", str(fixture)
            )
        raw_profiles = value["profiles"]
        if not isinstance(raw_profiles, list) or not raw_profiles:
            raise ContractError(
                "weapon_timing.profiles", f"{path}.profiles", "配置目录不得为空"
            )
        profiles = tuple(
            sorted(
                (
                    WeaponTimingProfile.parse(item, f"{path}.profiles[{index}]")
                    for index, item in enumerate(raw_profiles)
                ),
                key=lambda item: item.prototype,
            )
        )
        if len({item.prototype for item in profiles}) != len(profiles):
            raise ContractError(
                "weapon_timing.profile_duplicate",
                f"{path}.profiles",
                "同一武器原型不得重复配置",
            )
        name = value["name"]
        if not isinstance(name, str) or not name:
            raise ContractError("type.string", f"{path}.name", "名称不得为空")
        return cls(
            _resource_id(value["id"], f"{path}.id"),
            value["version"],
            name,
            fixture,
            profiles,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "fixture_level": self.fixture_level,
            "id": self.id,
            "kind": "WeaponTimingProfileCatalog",
            "name": self.name,
            "profiles": [item.to_dict() for item in self.profiles],
            "schema": WEAPON_TIMING_SCHEMA_ID,
            "version": self.version,
        }


def load_weapon_timing_profile_catalog(
    path: str | Path,
) -> WeaponTimingProfileCatalog:
    return WeaponTimingProfileCatalog.parse(
        json.loads(Path(path).read_text(encoding="utf-8")), str(path)
    )


@dataclass(frozen=True)
class WeaponTimelineEvent:
    sequence_id: str
    group_id: str | None
    weapon_instance_id: str
    tactical_time_s: float
    action_kind: str
    status: str
    action_resolution: WeaponActionResolution | None
    error_code: str | None
    error_message: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_kind": self.action_kind,
            "action_resolution": (
                None
                if self.action_resolution is None
                else self.action_resolution.to_dict()
            ),
            "error_code": self.error_code,
            "error_message": self.error_message,
            "group_id": self.group_id,
            "sequence_id": self.sequence_id,
            "status": self.status,
            "tactical_time_s": self.tactical_time_s,
            "weapon_instance_id": self.weapon_instance_id,
        }


@dataclass(frozen=True)
class WeaponTimelineMutation:
    source_instance_sha256: str
    resulting_instance: ShipInstanceSnapshotInput
    affected_sequence_ids: tuple[str, ...]


@dataclass(frozen=True)
class WeaponTimelineAdvanceResolution:
    source_instance_sha256: str
    resulting_instance: ShipInstanceSnapshotInput
    events: tuple[WeaponTimelineEvent, ...]

    def to_dict(self) -> dict[str, Any]:
        state = self.resulting_instance.weapon_timeline_state
        assert state is not None
        return {
            "events": [item.to_dict() for item in self.events],
            "interface": WEAPON_TIMELINE_INTERFACE_ID,
            "policy": WEAPON_TIMELINE_POLICY_ID,
            "resulting_instance_sha256": canonical_sha256(self.resulting_instance),
            "source_instance_sha256": self.source_instance_sha256,
            "timeline_state": state.to_dict(),
        }


def _weapon_modules(
    snapshot: DerivedShipSnapshot,
) -> dict[str, CompiledModuleInstance]:
    return {
        item.id: item
        for item in snapshot.outfit.instances
        if item.prototype.category == "weapon"
    }


def initialize_weapon_timeline(
    snapshot: DerivedShipSnapshot,
    instance: ShipInstanceSnapshotInput,
    catalog: WeaponTimingProfileCatalog,
    *,
    tactical_time_s: float = 0.0,
) -> ShipInstanceSnapshotInput:
    if instance.weapon_timeline_state is not None:
        raise ContractError(
            "weapon_timeline.already_initialized",
            "$.weapon_timeline_state",
            "舰艇已经具有武器时间状态",
        )
    validate_instance_current_design(snapshot, instance)
    if instance.ammunition_state is None:
        raise ContractError(
            "weapon_timeline.ammunition_state_missing",
            "$.ammunition_state",
            "初始化武器时间状态前必须建立完整弹药状态",
        )
    validate_ship_ammunition_state(
        snapshot,
        instance.ammunition_state,
        namespace="weapon_timeline",
        path_prefix="$.ammunition_state",
    )
    if not isfinite(tactical_time_s) or tactical_time_s < 0.0:
        raise ContractError(
            "weapon_timeline.tactical_time",
            "$.tactical_time_s",
            "战术时刻必须是非负有限数",
        )
    weapons = _weapon_modules(snapshot)
    for weapon in weapons.values():
        catalog.profile(weapon.prototype.reference)
    state = WeaponTimelineStateInput(
        catalog.reference,
        catalog.source_sha256,
        float(tactical_time_s),
        tuple(
            WeaponTimelineClockInput(instance_id, float(tactical_time_s))
            for instance_id in sorted(weapons)
        ),
        (),
    )
    return replace(instance, weapon_timeline_state=state)


def _require_timeline(
    snapshot: DerivedShipSnapshot,
    instance: ShipInstanceSnapshotInput,
    catalog: WeaponTimingProfileCatalog,
) -> WeaponTimelineStateInput:
    state = instance.weapon_timeline_state
    if state is None:
        raise ContractError(
            "weapon_timeline.state_missing",
            "$.weapon_timeline_state",
            "执行时间队列前必须初始化武器时间状态",
        )
    validate_instance_current_design(snapshot, instance)
    if (
        state.timing_profile_catalog != catalog.reference
        or state.timing_profile_catalog_sha256 != catalog.source_sha256
    ):
        raise ContractError(
            "weapon_timeline.profile_catalog_mismatch",
            "$.weapon_timeline_state.timing_profile_catalog",
            "活动时间状态必须继续使用初始化时的精确配置目录",
        )
    weapons = _weapon_modules(snapshot)
    clocks = {item.instance_id for item in state.clocks}
    if clocks != set(weapons):
        raise ContractError(
            "weapon_timeline.clock_set_mismatch",
            "$.weapon_timeline_state.clocks",
            f"缺少 {sorted(set(weapons) - clocks)}；多出 {sorted(clocks - set(weapons))}",
        )
    for weapon in weapons.values():
        catalog.profile(weapon.prototype.reference)
    if any(item.weapon_instance_id not in weapons for item in state.sequences):
        raise ContractError(
            "weapon_timeline.sequence_weapon_missing",
            "$.weapon_timeline_state.sequences",
            "活动序列引用了当前舾装中不存在的武器",
        )
    return state


def _ready_state(instance: ShipInstanceSnapshotInput, weapon_id: str):
    if instance.ammunition_state is None:
        raise ContractError(
            "weapon_action.ammunition_state_missing",
            "$.ammunition_state",
            "武器时间队列需要完整弹药状态",
        )
    return next(
        item
        for item in instance.ammunition_state.weapons
        if item.instance_id == weapon_id
    )


def _physical_ammunition_available(
    instance: ShipInstanceSnapshotInput,
    weapon: CompiledModuleInstance,
    munition_id: str,
) -> int:
    assert instance.ammunition_state is not None
    ready = _ready_state(instance, weapon.id)
    result = ready.ready_rounds if ready.munition_id == munition_id else 0
    compatible = set(weapon.prototype.capability.to_dict()["compatible_munition_ids"])
    if munition_id not in compatible:
        return result
    for magazine in instance.ammunition_state.magazines:
        result += next(
            (
                item.units
                for item in magazine.inventory
                if item.munition_id == munition_id
            ),
            0,
        )
    return result


def _reload_efficiency(resolution: WeaponActionResolution) -> float:
    values = tuple(value for _, value in resolution.function_efficiencies)
    if not values:
        raise ContractError(
            "weapon_timeline.reload_efficiency_missing",
            "$.function_efficiencies",
            "原子装填结果没有返回功能效率",
        )
    return min(values)


def _reload_duration_s(
    profile: WeaponTimingProfile,
    resolution: WeaponActionResolution,
) -> float:
    return profile.reload_seconds_per_round / _reload_efficiency(resolution)


def _fire_interval_s(
    profile: WeaponTimingProfile,
    resolution: WeaponActionResolution,
) -> float:
    efficiency = dict(resolution.function_efficiencies)["weapon.fire"]
    return 60.0 / profile.cyclic_rate_rpm / efficiency


def _preflight_reload(
    snapshot: DerivedShipSnapshot,
    sortie: CompiledSortieState,
    instance: ShipInstanceSnapshotInput,
    weapon_instance_id: str,
    munition_id: str,
    action_id: str,
) -> WeaponActionResolution:
    return resolve_weapon_reload(
        snapshot,
        sortie,
        instance,
        WeaponReloadRequest(action_id, weapon_instance_id, munition_id, 1),
    )


def _preflight_fire_request(
    snapshot: DerivedShipSnapshot,
    sortie: CompiledSortieState,
    instance: ShipInstanceSnapshotInput,
    request: WeaponFireRequest,
) -> WeaponActionResolution | None:
    ready = _ready_state(instance, request.weapon_instance_id)
    probe = replace(request, rounds=1)
    if ready.munition_id == request.munition_id and ready.ready_rounds > 0:
        resolve_weapon_fire(snapshot, sortie, instance, probe)
        return None
    if ready.ready_rounds > 0:
        raise ContractError(
            "weapon_timeline.ready_munition_conflict",
            "$.munition_id",
            f"待发位置仍装有 {ready.munition_id}",
        )
    reload_resolution = _preflight_reload(
        snapshot,
        sortie,
        instance,
        request.weapon_instance_id,
        request.munition_id,
        f"{request.id}.preflight_reload",
    )
    resolve_weapon_fire(
        snapshot,
        sortie,
        reload_resolution.resulting_instance,
        probe,
    )
    return reload_resolution


def _replace_timeline(
    instance: ShipInstanceSnapshotInput,
    state: WeaponTimelineStateInput,
) -> ShipInstanceSnapshotInput:
    return replace(instance, weapon_timeline_state=state)


def enqueue_continuous_fire(
    snapshot: DerivedShipSnapshot,
    sortie: CompiledSortieState,
    instance: ShipInstanceSnapshotInput,
    catalog: WeaponTimingProfileCatalog,
    request: WeaponFireRequest,
) -> WeaponTimelineMutation:
    source_sha = canonical_sha256(instance)
    state = _require_timeline(snapshot, instance, catalog)
    _resource_id(request.id, "$.id")
    if isinstance(request.rounds, bool) or not isinstance(request.rounds, int) or request.rounds < 1:
        raise ContractError("weapon_action.rounds", "$.rounds", "发数必须是正整数")
    if request.id in {item.id for item in state.sequences}:
        raise ContractError(
            "weapon_timeline.sequence_duplicate", "$.id", request.id
        )
    if request.weapon_instance_id in {
        item.weapon_instance_id for item in state.sequences
    }:
        raise ContractError(
            "weapon_timeline.weapon_busy",
            "$.weapon_instance_id",
            "该武器已有活动射击或装填序列",
        )
    weapons = _weapon_modules(snapshot)
    try:
        weapon = weapons[request.weapon_instance_id]
    except KeyError as error:
        raise ContractError(
            "weapon_timeline.weapon_instance_invalid",
            "$.weapon_instance_id",
            "必须指定当前舾装中的武器",
        ) from error
    available = _physical_ammunition_available(
        instance, weapon, request.munition_id
    )
    if request.rounds > available:
        raise ContractError(
            "weapon_timeline.sequence_ammunition_insufficient",
            "$.rounds",
            f"连续射击需要 {request.rounds} 发，舰上物理库存只有 {available} 发",
        )
    preflight_reload = _preflight_fire_request(
        snapshot, sortie, instance, request
    )
    profile = catalog.profile(weapon.prototype.reference)
    clocks = {item.instance_id: item for item in state.clocks}
    if preflight_reload is None:
        phase = "awaiting_fire"
        next_event = max(
            state.tactical_time_s,
            clocks[request.weapon_instance_id].next_fire_time_s,
        )
    else:
        phase = "reloading"
        next_event = state.tactical_time_s + _reload_duration_s(
            profile, preflight_reload
        )
    sequence = WeaponFireSequenceStateInput.parse(
        {
            "fire_control_instance_id": request.fire_control_instance_id,
            "group_id": None,
            "id": request.id,
            "munition_id": request.munition_id,
            "next_event_time_s": next_event,
            "phase": phase,
            "remaining_rounds": request.rounds,
            "target_distance_m": request.target_distance_m,
            "target_domain": request.target_domain,
            "weapon_instance_id": request.weapon_instance_id,
        },
        "$.sequence",
    )
    updated_state = replace(
        state,
        sequences=tuple(sorted((*state.sequences, sequence), key=lambda item: item.id)),
    )
    return WeaponTimelineMutation(
        source_sha,
        _replace_timeline(instance, updated_state),
        (sequence.id,),
    )


def enqueue_weapon_volley(
    snapshot: DerivedShipSnapshot,
    sortie: CompiledSortieState,
    instance: ShipInstanceSnapshotInput,
    catalog: WeaponTimingProfileCatalog,
    *,
    group_id: str,
    requests: Iterable[WeaponFireRequest],
) -> WeaponTimelineMutation:
    source_sha = canonical_sha256(instance)
    state = _require_timeline(snapshot, instance, catalog)
    normalized_group = _resource_id(group_id, "$.group_id")
    entries = tuple(requests)
    if len(entries) < 2:
        raise ContractError(
            "weapon_timeline.volley_size",
            "$.requests",
            "齐射至少需要两件武器",
        )
    if len({item.id for item in entries}) != len(entries) or len(
        {item.weapon_instance_id for item in entries}
    ) != len(entries):
        raise ContractError(
            "weapon_timeline.volley_duplicate",
            "$.requests",
            "同一次齐射不得重复序列 id 或武器",
        )
    active_ids = {item.id for item in state.sequences}
    active_weapons = {item.weapon_instance_id for item in state.sequences}
    clocks = {item.instance_id: item for item in state.clocks}
    sequences: list[WeaponFireSequenceStateInput] = []
    for request in entries:
        _resource_id(request.id, "$.requests.id")
        if request.rounds != 1:
            raise ContractError(
                "weapon_timeline.volley_rounds",
                "$.requests.rounds",
                "首版齐射的每件武器必须恰好发射一发",
            )
        if request.id in active_ids or request.weapon_instance_id in active_weapons:
            raise ContractError(
                "weapon_timeline.weapon_busy",
                "$.requests",
                "齐射武器已有活动序列",
            )
        if request.weapon_instance_id not in clocks:
            raise ContractError(
                "weapon_timeline.weapon_instance_invalid",
                "$.requests.weapon_instance_id",
                "齐射必须指定当前舾装中的武器",
            )
        if clocks[request.weapon_instance_id].next_fire_time_s > state.tactical_time_s + EPS:
            raise ContractError(
                "weapon_timeline.volley_weapon_cooling",
                "$.requests",
                "齐射只接受当前已经完成冷却的武器",
            )
        ready = _ready_state(instance, request.weapon_instance_id)
        if ready.munition_id != request.munition_id or ready.ready_rounds < 1:
            raise ContractError(
                "weapon_timeline.volley_weapon_not_ready",
                "$.requests",
                "齐射只接受已经装入指定待发弹的武器",
            )
        resolve_weapon_fire(snapshot, sortie, instance, request)
        sequences.append(
            WeaponFireSequenceStateInput.parse(
                {
                    "fire_control_instance_id": request.fire_control_instance_id,
                    "group_id": normalized_group,
                    "id": request.id,
                    "munition_id": request.munition_id,
                    "next_event_time_s": state.tactical_time_s,
                    "phase": "awaiting_fire",
                    "remaining_rounds": 1,
                    "target_distance_m": request.target_distance_m,
                    "target_domain": request.target_domain,
                    "weapon_instance_id": request.weapon_instance_id,
                },
                "$.sequence",
            )
        )
    updated = replace(
        state,
        sequences=tuple(
            sorted((*state.sequences, *sequences), key=lambda item: item.id)
        ),
    )
    return WeaponTimelineMutation(
        source_sha,
        _replace_timeline(instance, updated),
        tuple(sorted(item.id for item in sequences)),
    )


def cancel_weapon_sequence(
    instance: ShipInstanceSnapshotInput,
    sequence_id: str,
) -> WeaponTimelineMutation:
    source_sha = canonical_sha256(instance)
    normalized_id = _resource_id(sequence_id, "$.sequence_id")
    state = instance.weapon_timeline_state
    if state is None:
        raise ContractError(
            "weapon_timeline.state_missing",
            "$.weapon_timeline_state",
            "没有可取消的武器时间状态",
        )
    if normalized_id not in {item.id for item in state.sequences}:
        raise ContractError(
            "weapon_timeline.sequence_missing", "$.sequence_id", normalized_id
        )
    updated = replace(
        state,
        sequences=tuple(
            item for item in state.sequences if item.id != normalized_id
        ),
    )
    return WeaponTimelineMutation(
        source_sha,
        _replace_timeline(instance, updated),
        (normalized_id,),
    )


def _event_failure(
    sequence: WeaponFireSequenceStateInput,
    tactical_time_s: float,
    action_kind: str,
    error: ContractError,
) -> WeaponTimelineEvent:
    return WeaponTimelineEvent(
        sequence.id,
        sequence.group_id,
        sequence.weapon_instance_id,
        tactical_time_s,
        action_kind,
        "failed",
        None,
        error.code,
        error.message,
    )


def _event_success(
    sequence: WeaponFireSequenceStateInput,
    tactical_time_s: float,
    action_kind: str,
    resolution: WeaponActionResolution,
) -> WeaponTimelineEvent:
    return WeaponTimelineEvent(
        sequence.id,
        sequence.group_id,
        sequence.weapon_instance_id,
        tactical_time_s,
        action_kind,
        "resolved",
        resolution,
        None,
        None,
    )


def advance_weapon_timeline(
    snapshot: DerivedShipSnapshot,
    sortie: CompiledSortieState,
    instance: ShipInstanceSnapshotInput,
    catalog: WeaponTimingProfileCatalog,
    *,
    target_tactical_time_s: float,
) -> WeaponTimelineAdvanceResolution:
    source_sha = canonical_sha256(instance)
    state = _require_timeline(snapshot, instance, catalog)
    if (
        isinstance(target_tactical_time_s, bool)
        or not isinstance(target_tactical_time_s, (int, float))
        or not isfinite(float(target_tactical_time_s))
        or target_tactical_time_s + EPS < state.tactical_time_s
    ):
        raise ContractError(
            "weapon_timeline.time_reversed",
            "$.target_tactical_time_s",
            "目标战术时刻必须是不早于当前时刻的有限数",
        )
    target_time = float(target_tactical_time_s)
    working = instance
    events: list[WeaponTimelineEvent] = []
    event_counter = 0
    while True:
        current = _require_timeline(snapshot, working, catalog)
        due = sorted(
            (
                item
                for item in current.sequences
                if item.next_event_time_s <= target_time + EPS
            ),
            key=lambda item: (item.next_event_time_s, item.id),
        )
        if not due:
            break
        if event_counter >= MAX_EVENTS_PER_ADVANCE:
            raise ContractError(
                "weapon_timeline.event_limit",
                "$.target_tactical_time_s",
                "单次推进事件过多，必须拆分战术时间步",
            )
        sequence = due[0]
        event_time = sequence.next_event_time_s
        current = replace(current, tactical_time_s=event_time)
        working = _replace_timeline(working, current)
        sequence_map = {item.id: item for item in current.sequences}
        clock_map = {item.instance_id: item for item in current.clocks}
        weapon = _weapon_modules(snapshot)[sequence.weapon_instance_id]
        profile = catalog.profile(weapon.prototype.reference)
        if sequence.phase == "reloading":
            action_kind = "reload"
            try:
                resolution = resolve_weapon_reload(
                    snapshot,
                    sortie,
                    working,
                    WeaponReloadRequest(
                        f"{sequence.id}.reload.{event_counter}",
                        sequence.weapon_instance_id,
                        sequence.munition_id,
                        1,
                    ),
                )
            except ContractError as error:
                events.append(_event_failure(sequence, event_time, action_kind, error))
                del sequence_map[sequence.id]
            else:
                events.append(
                    _event_success(sequence, event_time, action_kind, resolution)
                )
                working = resolution.resulting_instance
                sequence_map[sequence.id] = replace(
                    sequence,
                    phase="awaiting_fire",
                    next_event_time_s=max(
                        event_time,
                        clock_map[sequence.weapon_instance_id].next_fire_time_s,
                    ),
                )
        else:
            action_kind = "fire"
            request = WeaponFireRequest(
                f"{sequence.id}.fire.{event_counter}",
                sequence.weapon_instance_id,
                sequence.munition_id,
                1,
                sequence.target_domain,
                sequence.target_distance_m,
                sequence.fire_control_instance_id,
            )
            try:
                resolution = resolve_weapon_fire(
                    snapshot, sortie, working, request
                )
            except ContractError as error:
                events.append(_event_failure(sequence, event_time, action_kind, error))
                del sequence_map[sequence.id]
            else:
                events.append(
                    _event_success(sequence, event_time, action_kind, resolution)
                )
                working = resolution.resulting_instance
                fire_ready_time = event_time + _fire_interval_s(profile, resolution)
                clock_map[sequence.weapon_instance_id] = replace(
                    clock_map[sequence.weapon_instance_id],
                    next_fire_time_s=fire_ready_time,
                )
                remaining = sequence.remaining_rounds - 1
                if remaining == 0:
                    del sequence_map[sequence.id]
                else:
                    ready = _ready_state(working, sequence.weapon_instance_id)
                    if (
                        ready.munition_id == sequence.munition_id
                        and ready.ready_rounds > 0
                    ):
                        sequence_map[sequence.id] = replace(
                            sequence,
                            remaining_rounds=remaining,
                            next_event_time_s=fire_ready_time,
                        )
                    else:
                        try:
                            preflight = _preflight_reload(
                                snapshot,
                                sortie,
                                working,
                                sequence.weapon_instance_id,
                                sequence.munition_id,
                                f"{sequence.id}.schedule_reload.{event_counter}",
                            )
                        except ContractError as error:
                            events.append(
                                _event_failure(
                                    sequence,
                                    event_time,
                                    "schedule_reload",
                                    error,
                                )
                            )
                            del sequence_map[sequence.id]
                        else:
                            sequence_map[sequence.id] = replace(
                                sequence,
                                remaining_rounds=remaining,
                                phase="reloading",
                                next_event_time_s=(
                                    event_time + _reload_duration_s(profile, preflight)
                                ),
                            )
        updated_timeline = replace(
            current,
            clocks=tuple(sorted(clock_map.values(), key=lambda item: item.instance_id)),
            sequences=tuple(sorted(sequence_map.values(), key=lambda item: item.id)),
        )
        working = _replace_timeline(working, updated_timeline)
        event_counter += 1

    final_state = _require_timeline(snapshot, working, catalog)
    final_state = replace(final_state, tactical_time_s=target_time)
    working = _replace_timeline(working, final_state)
    return WeaponTimelineAdvanceResolution(source_sha, working, tuple(events))
