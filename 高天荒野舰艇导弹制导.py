"""《高天荒野》阶段 I10：导弹导引头配置与可持久化制导状态。"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import json
from math import atan2, cos, hypot, isfinite, sin
from pathlib import Path
from typing import Any

from 高天荒野舰艇数据契约 import (
    ContractError,
    RESOURCE_ID_PATTERN,
    ResourceReference,
    canonical_sha256,
)


MISSILE_GUIDANCE_SCHEMA_ID = "gaotian.missile-guidance/v1alpha1"
MISSILE_GUIDANCE_STATE_INTERFACE_ID = "gaotian.missile-guidance-state/v1alpha1"
MISSILE_GUIDANCE_INITIALIZATION_POLICY_ID = (
    "gaotian.missile-guidance/profile-bound-persistent-state/v1"
)
MISSILE_GUIDANCE_RUNTIME_INTERFACE_ID = "gaotian.missile-guidance-runtime/v1alpha1"
MISSILE_GUIDANCE_RUNTIME_POLICY_ID = (
    "gaotian.missile-guidance/fixed-step-limited-lateral-acceleration-pursuit/v1"
)

FIXTURE_LEVELS = {"contract_fixture", "prototype_unbalanced", "balance_reference"}
SEEKER_KINDS = {
    "passive_radar",
    "active_radar",
    "anti_radiation",
    "electro_optical",
}
LAUNCH_SUPPORT_MODES = {"continuous_illumination", "optional_fire_control"}
TARGET_LOSS_BEHAVIORS = {
    "self_destruct",
    "last_known_position_then_self_destruct",
}
GUIDANCE_PHASES = {"inertial", "searching", "tracking", "memory", "lost"}
HEIGHT_LAYER_ORDER = {"upper": 0, "cloud": 1, "rain": 2}
EPS = 1.0e-8


def _resource_id(value: Any, path: str) -> str:
    if not isinstance(value, str) or not RESOURCE_ID_PATTERN.fullmatch(value):
        raise ContractError("resource.id_invalid", path, str(value))
    return value


def _string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value:
        raise ContractError("type.string", path, "必须是非空字符串")
    return value


def _number(value: Any, path: str, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError("type.number", path, "必须是数值")
    result = float(value)
    if not isfinite(result) or (minimum is not None and result < minimum):
        raise ContractError("value.number_range", path, str(value))
    return result


def _positive(value: Any, path: str) -> float:
    result = _number(value, path)
    if result <= 0.0:
        raise ContractError("value.positive", path, "必须为正有限数")
    return result


def _sha256(value: Any, path: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ContractError("value.sha256", path, str(value))
    return value


def _optional_resource_id(value: Any, path: str) -> str | None:
    return None if value is None else _resource_id(value, path)


def _optional_number(value: Any, path: str, minimum: float | None = None) -> float | None:
    return None if value is None else _number(value, path, minimum)


def _optional_vector2(value: Any, path: str) -> tuple[float, float] | None:
    if value is None:
        return None
    if not isinstance(value, list) or len(value) != 2:
        raise ContractError("type.vector2", path, "必须是二元数值数组或 null")
    return _number(value[0], f"{path}[0]"), _number(value[1], f"{path}[1]")


def _exact_object(value: Any, keys: set[str], path: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ContractError("object.keys", path, f"必须恰含 {sorted(keys)}")
    return value


@dataclass(frozen=True)
class MissileGuidanceProfile:
    munition_id: str
    name: str
    seeker_kind: str
    launch_support: str
    activation_distance_m: float | None
    seeker_search_range_m: float
    allowed_height_layers: tuple[str, ...]
    target_loss_behavior: str
    target_memory_s: float
    self_destruct_delay_s: float
    maximum_lateral_acceleration_mps2: float

    @classmethod
    def parse(cls, value: Any, path: str) -> "MissileGuidanceProfile":
        obj = _exact_object(
            value,
            {
                "munition_id",
                "name",
                "seeker_kind",
                "launch_support",
                "activation_distance_m",
                "seeker_search_range_m",
                "allowed_height_layers",
                "target_loss_behavior",
                "target_memory_s",
                "self_destruct_delay_s",
                "maximum_lateral_acceleration_mps2",
            },
            path,
        )
        seeker_kind = obj["seeker_kind"]
        if seeker_kind not in SEEKER_KINDS:
            raise ContractError(
                "missile_guidance.seeker_kind",
                f"{path}.seeker_kind",
                str(seeker_kind),
            )
        launch_support = obj["launch_support"]
        if launch_support not in LAUNCH_SUPPORT_MODES:
            raise ContractError(
                "missile_guidance.launch_support",
                f"{path}.launch_support",
                str(launch_support),
            )
        if seeker_kind == "passive_radar" and launch_support != "continuous_illumination":
            raise ContractError(
                "missile_guidance.passive_support",
                f"{path}.launch_support",
                "被动雷达制导必须依赖母舰持续照射",
            )
        if seeker_kind != "passive_radar" and launch_support != "optional_fire_control":
            raise ContractError(
                "missile_guidance.independent_support",
                f"{path}.launch_support",
                "主动雷达、反辐射与光电导引头不得把母舰持续照射设为发射硬前提",
            )
        layers_value = obj["allowed_height_layers"]
        if not isinstance(layers_value, list) or not layers_value:
            raise ContractError(
                "missile_guidance.height_layers",
                f"{path}.allowed_height_layers",
                "必须至少允许一个高度层",
            )
        if (
            any(item not in HEIGHT_LAYER_ORDER for item in layers_value)
            or len(set(layers_value)) != len(layers_value)
        ):
            raise ContractError(
                "missile_guidance.height_layers",
                f"{path}.allowed_height_layers",
                "高度层必须合法且不得重复",
            )
        layers = tuple(sorted(layers_value, key=HEIGHT_LAYER_ORDER.__getitem__))
        if seeker_kind == "electro_optical" and layers != ("upper",):
            raise ContractError(
                "missile_guidance.electro_optical_layer",
                f"{path}.allowed_height_layers",
                "首版光电导引头只能在上层工作",
            )
        loss_behavior = obj["target_loss_behavior"]
        if loss_behavior not in TARGET_LOSS_BEHAVIORS:
            raise ContractError(
                "missile_guidance.target_loss_behavior",
                f"{path}.target_loss_behavior",
                str(loss_behavior),
            )
        if (
            seeker_kind == "anti_radiation"
            and loss_behavior != "last_known_position_then_self_destruct"
        ):
            raise ContractError(
                "missile_guidance.anti_radiation_loss",
                f"{path}.target_loss_behavior",
                "反辐射导引头在辐射源关闭后必须继续飞向最后已知位置",
            )
        memory = _number(obj["target_memory_s"], f"{path}.target_memory_s", 0.0)
        if loss_behavior == "self_destruct" and memory > EPS:
            raise ContractError(
                "missile_guidance.unused_memory",
                f"{path}.target_memory_s",
                "直接自毁策略不得保存未使用的目标记忆时间",
            )
        self_destruct_delay = _positive(
            obj["self_destruct_delay_s"],
            f"{path}.self_destruct_delay_s",
        )
        if self_destruct_delay + EPS < memory:
            raise ContractError(
                "missile_guidance.memory_after_self_destruct",
                f"{path}.self_destruct_delay_s",
                "自毁期限不得早于目标记忆结束时刻",
            )
        return cls(
            _resource_id(obj["munition_id"], f"{path}.munition_id"),
            _string(obj["name"], f"{path}.name"),
            seeker_kind,
            launch_support,
            _optional_number(
                obj["activation_distance_m"],
                f"{path}.activation_distance_m",
                0.0,
            ),
            _positive(obj["seeker_search_range_m"], f"{path}.seeker_search_range_m"),
            layers,
            loss_behavior,
            memory,
            self_destruct_delay,
            _positive(
                obj["maximum_lateral_acceleration_mps2"],
                f"{path}.maximum_lateral_acceleration_mps2",
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "activation_distance_m": self.activation_distance_m,
            "allowed_height_layers": list(self.allowed_height_layers),
            "launch_support": self.launch_support,
            "maximum_lateral_acceleration_mps2": self.maximum_lateral_acceleration_mps2,
            "munition_id": self.munition_id,
            "name": self.name,
            "seeker_kind": self.seeker_kind,
            "seeker_search_range_m": self.seeker_search_range_m,
            "self_destruct_delay_s": self.self_destruct_delay_s,
            "target_loss_behavior": self.target_loss_behavior,
            "target_memory_s": self.target_memory_s,
        }


@dataclass(frozen=True)
class MissileGuidanceProfileCatalog:
    id: str
    version: int
    name: str
    fixture_level: str
    profiles: tuple[MissileGuidanceProfile, ...]
    _source_sha256: str = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_source_sha256", canonical_sha256(self))

    @property
    def reference(self) -> ResourceReference:
        return ResourceReference(self.id, self.version)

    @property
    def source_sha256(self) -> str:
        return self._source_sha256

    def profile_or_none(self, munition_id: str) -> MissileGuidanceProfile | None:
        return next(
            (profile for profile in self.profiles if profile.munition_id == munition_id),
            None,
        )

    def profile(self, munition_id: str) -> MissileGuidanceProfile:
        profile = self.profile_or_none(munition_id)
        if profile is None:
            raise ContractError(
                "missile_guidance.munition_missing",
                "$.profiles",
                munition_id,
            )
        return profile

    @classmethod
    def parse(cls, value: Any, path: str = "$") -> "MissileGuidanceProfileCatalog":
        obj = _exact_object(
            value,
            {"schema", "kind", "id", "version", "name", "fixture_level", "profiles"},
            path,
        )
        if (
            obj["schema"] != MISSILE_GUIDANCE_SCHEMA_ID
            or obj["kind"] != "MissileGuidanceProfileCatalog"
        ):
            raise ContractError("resource.kind", path, "不是导弹制导配置目录")
        version = obj["version"]
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise ContractError("value.version", f"{path}.version", str(version))
        fixture_level = obj["fixture_level"]
        if fixture_level not in FIXTURE_LEVELS:
            raise ContractError(
                "missile_guidance.fixture_level",
                f"{path}.fixture_level",
                str(fixture_level),
            )
        profiles_value = obj["profiles"]
        if not isinstance(profiles_value, list) or not profiles_value:
            raise ContractError(
                "missile_guidance.profiles",
                f"{path}.profiles",
                "目录不得为空",
            )
        profiles = tuple(
            sorted(
                (
                    MissileGuidanceProfile.parse(item, f"{path}.profiles[{index}]")
                    for index, item in enumerate(profiles_value)
                ),
                key=lambda item: item.munition_id,
            )
        )
        if len({profile.munition_id for profile in profiles}) != len(profiles):
            raise ContractError(
                "missile_guidance.duplicate_munition",
                f"{path}.profiles",
                "同一弹药只能具有一份制导配置",
            )
        return cls(
            _resource_id(obj["id"], f"{path}.id"),
            version,
            _string(obj["name"], f"{path}.name"),
            fixture_level,
            profiles,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "fixture_level": self.fixture_level,
            "id": self.id,
            "kind": "MissileGuidanceProfileCatalog",
            "name": self.name,
            "profiles": [profile.to_dict() for profile in self.profiles],
            "schema": MISSILE_GUIDANCE_SCHEMA_ID,
            "version": self.version,
        }


@dataclass(frozen=True)
class MissileGuidanceState:
    profile_catalog: ResourceReference
    profile_catalog_sha256: str
    projectile_id: str
    munition_id: str
    source_ship_id: str
    intended_target_ship_id: str
    seeker_kind: str
    phase: str
    launch_time_s: float
    updated_time_s: float
    tracked_target_ship_id: str | None = None
    last_known_target_position_xy: tuple[float, float] | None = None
    target_lost_time_s: float | None = None
    self_destruct_deadline_s: float | None = None

    @classmethod
    def parse(cls, value: Any, path: str = "$") -> "MissileGuidanceState":
        obj = _exact_object(
            value,
            {
                "profile_catalog",
                "profile_catalog_sha256",
                "projectile_id",
                "munition_id",
                "source_ship_id",
                "intended_target_ship_id",
                "seeker_kind",
                "phase",
                "launch_time_s",
                "updated_time_s",
                "tracked_target_ship_id",
                "last_known_target_position_xy",
                "target_lost_time_s",
                "self_destruct_deadline_s",
            },
            path,
        )
        seeker_kind = obj["seeker_kind"]
        if seeker_kind not in SEEKER_KINDS:
            raise ContractError(
                "missile_guidance.seeker_kind",
                f"{path}.seeker_kind",
                str(seeker_kind),
            )
        phase = obj["phase"]
        if phase not in GUIDANCE_PHASES:
            raise ContractError("missile_guidance.phase", f"{path}.phase", str(phase))
        launch_time = _number(obj["launch_time_s"], f"{path}.launch_time_s", 0.0)
        updated_time = _number(obj["updated_time_s"], f"{path}.updated_time_s", 0.0)
        if updated_time + EPS < launch_time:
            raise ContractError(
                "missile_guidance.time_reversed",
                f"{path}.updated_time_s",
                "制导状态更新时间不得早于发射时刻",
            )
        state = cls(
            ResourceReference.parse(obj["profile_catalog"], f"{path}.profile_catalog"),
            _sha256(obj["profile_catalog_sha256"], f"{path}.profile_catalog_sha256"),
            _resource_id(obj["projectile_id"], f"{path}.projectile_id"),
            _resource_id(obj["munition_id"], f"{path}.munition_id"),
            _resource_id(obj["source_ship_id"], f"{path}.source_ship_id"),
            _resource_id(
                obj["intended_target_ship_id"],
                f"{path}.intended_target_ship_id",
            ),
            seeker_kind,
            phase,
            launch_time,
            updated_time,
            _optional_resource_id(
                obj["tracked_target_ship_id"],
                f"{path}.tracked_target_ship_id",
            ),
            _optional_vector2(
                obj["last_known_target_position_xy"],
                f"{path}.last_known_target_position_xy",
            ),
            _optional_number(
                obj["target_lost_time_s"],
                f"{path}.target_lost_time_s",
                0.0,
            ),
            _optional_number(
                obj["self_destruct_deadline_s"],
                f"{path}.self_destruct_deadline_s",
                0.0,
            ),
        )
        _validate_state_shape(state, path)
        return state

    def to_dict(self) -> dict[str, Any]:
        return {
            "intended_target_ship_id": self.intended_target_ship_id,
            "last_known_target_position_xy": (
                None
                if self.last_known_target_position_xy is None
                else list(self.last_known_target_position_xy)
            ),
            "launch_time_s": self.launch_time_s,
            "munition_id": self.munition_id,
            "phase": self.phase,
            "profile_catalog": self.profile_catalog.to_dict(),
            "profile_catalog_sha256": self.profile_catalog_sha256,
            "projectile_id": self.projectile_id,
            "seeker_kind": self.seeker_kind,
            "self_destruct_deadline_s": self.self_destruct_deadline_s,
            "source_ship_id": self.source_ship_id,
            "target_lost_time_s": self.target_lost_time_s,
            "tracked_target_ship_id": self.tracked_target_ship_id,
            "updated_time_s": self.updated_time_s,
        }


def _validate_state_shape(state: MissileGuidanceState, path: str = "$") -> None:
    tracking = state.tracked_target_ship_id is not None
    has_last_position = state.last_known_target_position_xy is not None
    has_loss_time = state.target_lost_time_s is not None
    has_deadline = state.self_destruct_deadline_s is not None
    if state.phase in {"inertial", "searching"}:
        if tracking or has_last_position or has_loss_time or has_deadline:
            raise ContractError(
                "missile_guidance.phase_state",
                path,
                "惯性飞行或搜索状态不得持有跟踪目标或丢失计时",
            )
    elif state.phase == "tracking":
        if not tracking or not has_last_position or has_loss_time or has_deadline:
            raise ContractError(
                "missile_guidance.phase_state",
                path,
                "跟踪状态必须保存当前目标与最后位置，且不得启动丢失计时",
            )
    elif state.phase == "memory":
        if tracking or not has_last_position or not has_loss_time or not has_deadline:
            raise ContractError(
                "missile_guidance.phase_state",
                path,
                "记忆飞行必须保存最后位置、丢失时刻与自毁期限",
            )
    elif state.phase == "lost":
        if tracking or not has_loss_time or not has_deadline:
            raise ContractError(
                "missile_guidance.phase_state",
                path,
                "目标丢失状态必须保存丢失时刻与自毁期限",
            )
    if has_loss_time and state.target_lost_time_s is not None:
        if state.target_lost_time_s + EPS < state.launch_time_s:
            raise ContractError(
                "missile_guidance.loss_before_launch",
                f"{path}.target_lost_time_s",
                "目标丢失时刻不得早于发射时刻",
            )
    if has_deadline and state.self_destruct_deadline_s is not None:
        if state.target_lost_time_s is None or state.self_destruct_deadline_s <= state.target_lost_time_s:
            raise ContractError(
                "missile_guidance.self_destruct_deadline",
                f"{path}.self_destruct_deadline_s",
                "自毁期限必须晚于目标丢失时刻",
            )


def initialize_missile_guidance_state(
    catalog: MissileGuidanceProfileCatalog,
    *,
    projectile_id: str,
    munition_id: str,
    source_ship_id: str,
    intended_target_ship_id: str,
    launch_time_s: float,
) -> MissileGuidanceState | None:
    profile = catalog.profile_or_none(munition_id)
    if profile is None:
        return None
    launch_time = _number(launch_time_s, "$.launch_time_s", 0.0)
    state = MissileGuidanceState(
        catalog.reference,
        catalog.source_sha256,
        _resource_id(projectile_id, "$.projectile_id"),
        profile.munition_id,
        _resource_id(source_ship_id, "$.source_ship_id"),
        _resource_id(intended_target_ship_id, "$.intended_target_ship_id"),
        profile.seeker_kind,
        "searching" if profile.activation_distance_m is None else "inertial",
        launch_time,
        launch_time,
    )
    _validate_state_shape(state)
    return state


def validate_missile_guidance_state(
    state: MissileGuidanceState,
    catalog: MissileGuidanceProfileCatalog,
) -> None:
    _validate_state_shape(state)
    if (
        state.profile_catalog != catalog.reference
        or state.profile_catalog_sha256 != catalog.source_sha256
    ):
        raise ContractError(
            "missile_guidance.profile_catalog_mismatch",
            "$.profile_catalog",
            "制导状态已绑定其他配置目录或内容指纹",
        )
    profile = catalog.profile(state.munition_id)
    if state.seeker_kind != profile.seeker_kind:
        raise ContractError(
            "missile_guidance.seeker_profile_mismatch",
            "$.seeker_kind",
            "持久状态导引头类型与精确配置不一致",
        )


@dataclass(frozen=True)
class MissileGuidanceRuntimeInput:
    """一个固定步内由场景明确提供给单枚导弹的可观测事实。"""

    projectile_id: str
    target_track_available: bool
    target_radar_emitting: bool
    continuous_illumination_available: bool

    def validate(self, path: str = "$") -> None:
        _resource_id(self.projectile_id, f"{path}.projectile_id")
        for field_name in (
            "target_track_available",
            "target_radar_emitting",
            "continuous_illumination_available",
        ):
            if not isinstance(getattr(self, field_name), bool):
                raise ContractError(
                    "type.boolean",
                    f"{path}.{field_name}",
                    "制导运行时事实必须是布尔值",
                )

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "continuous_illumination_available": self.continuous_illumination_available,
            "projectile_id": self.projectile_id,
            "target_radar_emitting": self.target_radar_emitting,
            "target_track_available": self.target_track_available,
        }


@dataclass(frozen=True)
class MissileGuidanceEvent:
    projectile_id: str
    tactical_time_s: float
    previous_phase: str
    resulting_phase: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "previous_phase": self.previous_phase,
            "projectile_id": self.projectile_id,
            "reason": self.reason,
            "resulting_phase": self.resulting_phase,
            "tactical_time_s": self.tactical_time_s,
        }


@dataclass(frozen=True)
class MissileGuidanceStepResolution:
    resulting_state: MissileGuidanceState
    resulting_velocity_xy: tuple[float, float]
    events: tuple[MissileGuidanceEvent, ...]
    self_destruct: bool


def _target_available(
    profile: MissileGuidanceProfile,
    runtime_input: MissileGuidanceRuntimeInput,
    *,
    distance_m: float,
    target_height_layer: str,
) -> bool:
    if (
        distance_m > profile.seeker_search_range_m + EPS
        or target_height_layer not in profile.allowed_height_layers
    ):
        return False
    if profile.seeker_kind == "passive_radar":
        return (
            runtime_input.target_track_available
            and runtime_input.continuous_illumination_available
        )
    if profile.seeker_kind == "anti_radiation":
        return runtime_input.target_radar_emitting
    return runtime_input.target_track_available


def _steer_velocity(
    velocity_xy: tuple[float, float],
    position_xy: tuple[float, float],
    guidance_point_xy: tuple[float, float],
    *,
    maximum_lateral_acceleration_mps2: float,
    duration_s: float,
) -> tuple[float, float]:
    speed = hypot(*velocity_xy)
    target_offset = (
        guidance_point_xy[0] - position_xy[0],
        guidance_point_xy[1] - position_xy[1],
    )
    if speed <= EPS or hypot(*target_offset) <= EPS:
        return velocity_xy
    current_angle = atan2(velocity_xy[1], velocity_xy[0])
    target_angle = atan2(target_offset[1], target_offset[0])
    angle_delta = (target_angle - current_angle + 3.141592653589793) % (
        2.0 * 3.141592653589793
    ) - 3.141592653589793
    maximum_turn = maximum_lateral_acceleration_mps2 * duration_s / speed
    turn = max(-maximum_turn, min(maximum_turn, angle_delta))
    resulting_angle = current_angle + turn
    return speed * cos(resulting_angle), speed * sin(resulting_angle)


def advance_missile_guidance_step(
    state: MissileGuidanceState,
    catalog: MissileGuidanceProfileCatalog,
    runtime_input: MissileGuidanceRuntimeInput,
    *,
    position_xy: tuple[float, float],
    velocity_xy: tuple[float, float],
    target_position_xy: tuple[float, float],
    target_height_layer: str,
    tactical_time_s: float,
    duration_s: float,
) -> MissileGuidanceStepResolution:
    """推进一个确定性制导子步，并在弹道积分前修正速度方向。"""

    validate_missile_guidance_state(state, catalog)
    runtime_input.validate("$.runtime_input")
    if runtime_input.projectile_id != state.projectile_id:
        raise ContractError(
            "missile_guidance.runtime_input_binding",
            "$.runtime_input.projectile_id",
            "运行时事实没有绑定当前弹丸",
        )
    now = _number(tactical_time_s, "$.tactical_time_s", 0.0)
    duration = _positive(duration_s, "$.duration_s")
    if now + EPS < state.updated_time_s:
        raise ContractError(
            "missile_guidance.time_reversed",
            "$.tactical_time_s",
            "制导运行时不得早于持久状态更新时间",
        )
    if (
        not isinstance(target_height_layer, str)
        or target_height_layer not in HEIGHT_LAYER_ORDER
    ):
        raise ContractError(
            "missile_guidance.target_height_layer",
            "$.target_height_layer",
            str(target_height_layer),
        )
    for name, vector in (
        ("position_xy", position_xy),
        ("velocity_xy", velocity_xy),
        ("target_position_xy", target_position_xy),
    ):
        if not isinstance(vector, (tuple, list)) or len(vector) != 2:
            raise ContractError("type.vector2", f"$.{name}", "必须是二元向量")
        _number(vector[0], f"$.{name}[0]")
        _number(vector[1], f"$.{name}[1]")

    profile = catalog.profile(state.munition_id)
    events: list[MissileGuidanceEvent] = []
    current = state
    distance = hypot(
        target_position_xy[0] - position_xy[0],
        target_position_xy[1] - position_xy[1],
    )
    available = _target_available(
        profile,
        runtime_input,
        distance_m=distance,
        target_height_layer=target_height_layer,
    )

    def transition(resulting: MissileGuidanceState, reason: str) -> None:
        nonlocal current
        events.append(
            MissileGuidanceEvent(
                state.projectile_id,
                now,
                current.phase,
                resulting.phase,
                reason,
            )
        )
        current = resulting

    if (
        current.self_destruct_deadline_s is not None
        and current.self_destruct_deadline_s <= now + EPS
    ):
        events.append(
            MissileGuidanceEvent(
                state.projectile_id,
                current.self_destruct_deadline_s,
                current.phase,
                current.phase,
                "self_destruct_deadline_reached",
            )
        )
        return MissileGuidanceStepResolution(current, velocity_xy, tuple(events), True)

    if current.phase == "inertial" and (
        profile.activation_distance_m is None
        or distance <= profile.activation_distance_m + EPS
    ):
        transition(replace(current, phase="searching"), "seeker_activated")

    if current.phase == "searching" and available:
        transition(
            replace(
                current,
                phase="tracking",
                tracked_target_ship_id=current.intended_target_ship_id,
                last_known_target_position_xy=target_position_xy,
            ),
            "target_acquired",
        )
    elif current.phase == "tracking":
        if available:
            current = replace(
                current,
                last_known_target_position_xy=target_position_xy,
            )
        else:
            loss_time = now
            deadline = loss_time + profile.self_destruct_delay_s
            if (
                profile.target_loss_behavior
                == "last_known_position_then_self_destruct"
                and profile.target_memory_s > EPS
            ):
                transition(
                    replace(
                        current,
                        phase="memory",
                        tracked_target_ship_id=None,
                        target_lost_time_s=loss_time,
                        self_destruct_deadline_s=deadline,
                    ),
                    "target_lost_memory",
                )
            else:
                transition(
                    replace(
                        current,
                        phase="lost",
                        tracked_target_ship_id=None,
                        target_lost_time_s=loss_time,
                        self_destruct_deadline_s=deadline,
                    ),
                    "target_lost",
                )
    elif current.phase == "memory":
        assert current.target_lost_time_s is not None
        if available:
            transition(
                replace(
                    current,
                    phase="tracking",
                    tracked_target_ship_id=current.intended_target_ship_id,
                    last_known_target_position_xy=target_position_xy,
                    target_lost_time_s=None,
                    self_destruct_deadline_s=None,
                ),
                "target_reacquired",
            )
        elif now + EPS >= current.target_lost_time_s + profile.target_memory_s:
            transition(replace(current, phase="lost"), "target_memory_expired")

    guidance_point = None
    if current.phase == "tracking":
        guidance_point = target_position_xy
    elif current.phase == "memory":
        guidance_point = current.last_known_target_position_xy
    resulting_velocity = velocity_xy
    if guidance_point is not None:
        resulting_velocity = _steer_velocity(
            velocity_xy,
            position_xy,
            guidance_point,
            maximum_lateral_acceleration_mps2=profile.maximum_lateral_acceleration_mps2,
            duration_s=duration,
        )
    current = replace(current, updated_time_s=now + duration)
    _validate_state_shape(current)
    return MissileGuidanceStepResolution(
        current,
        resulting_velocity,
        tuple(events),
        False,
    )


def load_missile_guidance_profile_catalog(
    path: str | Path,
) -> MissileGuidanceProfileCatalog:
    return MissileGuidanceProfileCatalog.parse(
        json.loads(Path(path).read_text(encoding="utf-8")),
        str(path),
    )
