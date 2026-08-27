"""阶段 I9：旗舰/普通舰角色、唯一直控对象与 RTS 命令仲裁。"""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
from math import atan2, hypot, isfinite, pi, radians
from pathlib import Path
from typing import Any, Iterable

from 高天荒野舰艇数据契约 import (
    ContractError,
    MaterialRegistry,
    RESOURCE_ID_PATTERN,
    ResourceReference,
    canonical_sha256,
)
from 高天荒野舰艇运行时参数编译器 import compile_runtime_ship_parameters
from 高天荒野舰艇武器时间与射击队列 import WeaponTimingProfileCatalog
from 高天荒野舰艇导弹制导 import (
    MissileGuidanceProfileCatalog,
    MissileGuidanceRuntimeInput,
)
from 高天荒野舰艇持续毁伤 import (
    ContinuousDamageProfile,
    DamageControlDirective,
    FireIgnitionOutcome,
)
from 高天荒野舰艇人员伤亡 import CrewCasualtyOutcome
from 高天荒野舰艇人员医疗转移与救生 import CrewEvacuationOutcome
from 高天荒野舰艇二次毁伤 import (
    AmmunitionCookoffOutcome,
    FirePropagationOutcome,
)
from 高天荒野舰艇战术观测与火控 import TacticalObservationStepInput
from 高天荒野舰艇战术弹丸世界 import ProjectileProfileCatalog
from 高天荒野舰艇战术机动求解器 import (
    TacticalControlInput,
    Vec2,
    body_to_world,
    build_tactical_ship_model,
    clamp,
    request_layer_transition,
    world_to_body,
    wrap_angle,
)
from 高天荒野舰艇统一战术场景 import (
    EPS,
    TacticalEngagementBoundaryProfile,
    TacticalSceneExitDirective,
    TacticalSceneLaunchDirective,
    TacticalSceneShipBinding,
    TacticalSceneState,
    TacticalSceneStepResolution,
    advance_tactical_scene_step,
)


TACTICAL_FLEET_COMMAND_INTERFACE_ID = "gaotian.tactical-fleet-command/v1alpha1"
TACTICAL_FLEET_COMMAND_POLICY_ID = (
    "gaotian.tactical-fleet-command/one-direct-ship-and-rts-orders/v1"
)
TACTICAL_COMMAND_TUNING_SCHEMA_ID = "gaotian.tactical-command-tuning/v1alpha1"

SHIP_ROLES = {"main_flagship", "branch_flagship", "ordinary_ship"}
PLAYER_COMMAND_MODES = {"normal", "final_mobilization"}
COMMAND_PHASES = {"active", "command_defeat_withdrawal"}
ORDER_KINDS = {
    "move_route",
    "air_patrol",
    "anti_ship_patrol",
    "free_fire_area",
    "attack_target",
    "hold",
    "rescue_and_withdraw",
}
ORDER_STATUSES = {"active", "completed", "failed", "cancelled"}
TARGET_CATEGORIES = {"aircraft", "fixed_installation", "missile", "ship"}
HEIGHT_LAYERS = {"upper", "cloud", "rain"}


def _resource_id(value: Any, path: str) -> str:
    if not isinstance(value, str) or not RESOURCE_ID_PATTERN.fullmatch(value):
        raise ContractError(
            "resource.id_invalid",
            path,
            "只能使用小写字母、数字、点、横线和下划线",
        )
    return value


def _number(value: Any, path: str, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError("type.number", path, "必须是数值")
    result = float(value)
    if not isfinite(result) or (minimum is not None and result < minimum):
        raise ContractError("value.number", path, "不是允许的有限数值")
    return result


def _integer(value: Any, path: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ContractError("type.integer", path, f"必须是大于等于 {minimum} 的整数")
    return value


def _sha256(value: Any, path: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ContractError("value.sha256", path, str(value))
    return value


def _exact_object(value: Any, keys: set[str], path: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ContractError("object.keys", path, f"必须恰含 {sorted(keys)}")
    return value


def _optional_resource_id(value: Any, path: str) -> str | None:
    return None if value is None else _resource_id(value, path)


def _optional_number(
    value: Any,
    path: str,
    minimum: float | None = None,
) -> float | None:
    return None if value is None else _number(value, path, minimum)


def _vec2(value: Any, path: str) -> Vec2:
    if not isinstance(value, list) or len(value) != 2:
        raise ContractError("type.vector2", path, "必须是两个有限数值")
    return Vec2(_number(value[0], f"{path}[0]"), _number(value[1], f"{path}[1]"))


def _control_to_dict(value: TacticalControlInput) -> dict[str, Any]:
    return {
        "brake": value.brake,
        "move_body": value.move_body.to_list(),
        "overg": value.overg,
        "wheel": value.wheel,
    }


@dataclass(frozen=True)
class TacticalCommandTuningProfile:
    id: str
    version: int
    name: str
    fixture_level: str
    waypoint_tolerance_m: float
    heading_full_wheel_error_deg: float
    velocity_error_full_command_mps: float

    @property
    def reference(self) -> ResourceReference:
        return ResourceReference(self.id, self.version)

    @property
    def source_sha256(self) -> str:
        return canonical_sha256(self)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fixture_level": self.fixture_level,
            "heading_full_wheel_error_deg": self.heading_full_wheel_error_deg,
            "id": self.id,
            "kind": "TacticalCommandTuningProfile",
            "name": self.name,
            "schema": TACTICAL_COMMAND_TUNING_SCHEMA_ID,
            "velocity_error_full_command_mps": self.velocity_error_full_command_mps,
            "version": self.version,
            "waypoint_tolerance_m": self.waypoint_tolerance_m,
        }

    @classmethod
    def parse(
        cls,
        value: Any,
        path: str = "$",
    ) -> "TacticalCommandTuningProfile":
        obj = _exact_object(
            value,
            {
                "fixture_level",
                "heading_full_wheel_error_deg",
                "id",
                "kind",
                "name",
                "schema",
                "velocity_error_full_command_mps",
                "version",
                "waypoint_tolerance_m",
            },
            path,
        )
        if (
            obj["schema"] != TACTICAL_COMMAND_TUNING_SCHEMA_ID
            or obj["kind"] != "TacticalCommandTuningProfile"
        ):
            raise ContractError("tactical_command.resource", path, "不是战术指挥技术配置")
        fixture = obj["fixture_level"]
        if fixture not in {
            "contract_fixture",
            "prototype_unbalanced",
            "balance_reference",
        }:
            raise ContractError(
                "tactical_command.fixture_level",
                f"{path}.fixture_level",
                str(fixture),
            )
        name = obj["name"]
        if not isinstance(name, str) or not name:
            raise ContractError("type.string", f"{path}.name", "名称不得为空")
        return cls(
            _resource_id(obj["id"], f"{path}.id"),
            _integer(obj["version"], f"{path}.version", 1),
            name,
            fixture,
            _number(obj["waypoint_tolerance_m"], f"{path}.waypoint_tolerance_m", EPS),
            _number(
                obj["heading_full_wheel_error_deg"],
                f"{path}.heading_full_wheel_error_deg",
                EPS,
            ),
            _number(
                obj["velocity_error_full_command_mps"],
                f"{path}.velocity_error_full_command_mps",
                EPS,
            ),
        )


def load_tactical_command_tuning_profile(
    path: str | Path,
) -> TacticalCommandTuningProfile:
    return TacticalCommandTuningProfile.parse(
        json.loads(Path(path).read_text(encoding="utf-8")),
        str(path),
    )


@dataclass(frozen=True)
class TacticalShipRoleAssignment:
    ship_id: str
    role: str
    formation_anchor_ship_id: str | None = None
    formation_offset_body_m: Vec2 = Vec2()

    def to_dict(self) -> dict[str, Any]:
        return {
            "formation_anchor_ship_id": self.formation_anchor_ship_id,
            "formation_offset_body_m": self.formation_offset_body_m.to_list(),
            "role": self.role,
            "ship_id": self.ship_id,
        }

    @classmethod
    def parse(cls, value: Any, path: str) -> "TacticalShipRoleAssignment":
        obj = _exact_object(
            value,
            {
                "formation_anchor_ship_id",
                "formation_offset_body_m",
                "role",
                "ship_id",
            },
            path,
        )
        role = obj["role"]
        if role not in SHIP_ROLES:
            raise ContractError("tactical_command.ship_role", f"{path}.role", str(role))
        ship_id = _resource_id(obj["ship_id"], f"{path}.ship_id")
        anchor = _optional_resource_id(
            obj["formation_anchor_ship_id"],
            f"{path}.formation_anchor_ship_id",
        )
        if anchor == ship_id:
            raise ContractError(
                "tactical_command.self_formation_anchor",
                f"{path}.formation_anchor_ship_id",
                "舰艇不能以自身作为编队锚点",
            )
        return cls(
            ship_id,
            role,
            anchor,
            _vec2(obj["formation_offset_body_m"], f"{path}.formation_offset_body_m"),
        )


@dataclass(frozen=True)
class TacticalShipOrder:
    id: str
    ship_id: str
    kind: str
    issued_step_index: int
    status: str = "active"
    waypoints_world_m: tuple[Vec2, ...] = ()
    waypoint_index: int = 0
    target_layer: str | None = None
    target_heading_rad: float | None = None
    target_speed_mps: float = 0.0
    target_ship_id: str | None = None
    ammunition_id: str | None = None
    engagement_distance_m: float | None = None
    target_priority_categories: tuple[str, ...] = ()
    failure_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ammunition_id": self.ammunition_id,
            "engagement_distance_m": self.engagement_distance_m,
            "failure_reason": self.failure_reason,
            "id": self.id,
            "issued_step_index": self.issued_step_index,
            "kind": self.kind,
            "ship_id": self.ship_id,
            "status": self.status,
            "target_heading_rad": self.target_heading_rad,
            "target_layer": self.target_layer,
            "target_priority_categories": list(self.target_priority_categories),
            "target_ship_id": self.target_ship_id,
            "target_speed_mps": self.target_speed_mps,
            "waypoint_index": self.waypoint_index,
            "waypoints_world_m": [item.to_list() for item in self.waypoints_world_m],
        }

    @classmethod
    def parse(cls, value: Any, path: str) -> "TacticalShipOrder":
        obj = _exact_object(
            value,
            {
                "ammunition_id",
                "engagement_distance_m",
                "failure_reason",
                "id",
                "issued_step_index",
                "kind",
                "ship_id",
                "status",
                "target_heading_rad",
                "target_layer",
                "target_priority_categories",
                "target_ship_id",
                "target_speed_mps",
                "waypoint_index",
                "waypoints_world_m",
            },
            path,
        )
        kind = obj["kind"]
        status = obj["status"]
        if kind not in ORDER_KINDS:
            raise ContractError("tactical_command.order_kind", f"{path}.kind", str(kind))
        if status not in ORDER_STATUSES:
            raise ContractError("tactical_command.order_status", f"{path}.status", str(status))
        waypoint_values = obj["waypoints_world_m"]
        if not isinstance(waypoint_values, list):
            raise ContractError("type.array", f"{path}.waypoints_world_m", "必须是数组")
        waypoints = tuple(
            _vec2(item, f"{path}.waypoints_world_m[{index}]")
            for index, item in enumerate(waypoint_values)
        )
        waypoint_index = _integer(obj["waypoint_index"], f"{path}.waypoint_index")
        if waypoint_index > len(waypoints):
            raise ContractError(
                "tactical_command.waypoint_index",
                f"{path}.waypoint_index",
                "航路索引不得超过航点数量",
            )
        target_layer = obj["target_layer"]
        if target_layer is not None and target_layer not in HEIGHT_LAYERS:
            raise ContractError(
                "tactical_command.target_layer",
                f"{path}.target_layer",
                str(target_layer),
            )
        heading = _optional_number(obj["target_heading_rad"], f"{path}.target_heading_rad")
        if heading is not None and not -pi - EPS <= heading <= pi + EPS:
            raise ContractError(
                "tactical_command.target_heading",
                f"{path}.target_heading_rad",
                "舰艏朝向必须位于[-pi, pi]",
            )
        priorities_value = obj["target_priority_categories"]
        if not isinstance(priorities_value, list):
            raise ContractError(
                "type.array",
                f"{path}.target_priority_categories",
                "必须是数组",
            )
        priorities = tuple(priorities_value)
        if (
            any(item not in TARGET_CATEGORIES for item in priorities)
            or len(set(priorities)) != len(priorities)
        ):
            raise ContractError(
                "tactical_command.target_priorities",
                f"{path}.target_priority_categories",
                "目标类别必须合法且不得重复",
            )
        reason = obj["failure_reason"]
        if reason is not None and (not isinstance(reason, str) or not reason):
            raise ContractError("type.string", f"{path}.failure_reason", "失败原因不得为空")
        order = cls(
            _resource_id(obj["id"], f"{path}.id"),
            _resource_id(obj["ship_id"], f"{path}.ship_id"),
            kind,
            _integer(obj["issued_step_index"], f"{path}.issued_step_index"),
            status,
            waypoints,
            waypoint_index,
            target_layer,
            heading,
            _number(obj["target_speed_mps"], f"{path}.target_speed_mps", 0.0),
            _optional_resource_id(obj["target_ship_id"], f"{path}.target_ship_id"),
            _optional_resource_id(obj["ammunition_id"], f"{path}.ammunition_id"),
            _optional_number(
                obj["engagement_distance_m"],
                f"{path}.engagement_distance_m",
                0.0,
            ),
            priorities,
            reason,
        )
        _validate_order_shape(order, path)
        return order


def _validate_order_shape(order: TacticalShipOrder, path: str) -> None:
    _resource_id(order.id, f"{path}.id")
    _resource_id(order.ship_id, f"{path}.ship_id")
    if order.kind not in ORDER_KINDS:
        raise ContractError("tactical_command.order_kind", f"{path}.kind", str(order.kind))
    if order.status not in ORDER_STATUSES:
        raise ContractError("tactical_command.order_status", f"{path}.status", str(order.status))
    _integer(order.issued_step_index, f"{path}.issued_step_index")
    _integer(order.waypoint_index, f"{path}.waypoint_index")
    if order.waypoint_index > len(order.waypoints_world_m):
        raise ContractError("tactical_command.waypoint_index", f"{path}.waypoint_index", "航路索引不得超过航点数量")
    for index, waypoint in enumerate(order.waypoints_world_m):
        _number(waypoint.x, f"{path}.waypoints_world_m[{index}][0]")
        _number(waypoint.y, f"{path}.waypoints_world_m[{index}][1]")
    if order.target_layer is not None and order.target_layer not in HEIGHT_LAYERS:
        raise ContractError("tactical_command.target_layer", f"{path}.target_layer", str(order.target_layer))
    if order.target_heading_rad is not None and not -pi - EPS <= _number(order.target_heading_rad, f"{path}.target_heading_rad") <= pi + EPS:
        raise ContractError("tactical_command.target_heading", f"{path}.target_heading_rad", "舰艏朝向必须位于[-pi, pi]")
    _number(order.target_speed_mps, f"{path}.target_speed_mps", 0.0)
    if order.target_ship_id is not None:
        _resource_id(order.target_ship_id, f"{path}.target_ship_id")
    if order.ammunition_id is not None:
        _resource_id(order.ammunition_id, f"{path}.ammunition_id")
    if order.engagement_distance_m is not None:
        _number(order.engagement_distance_m, f"{path}.engagement_distance_m", 0.0)
    if any(item not in TARGET_CATEGORIES for item in order.target_priority_categories) or len(set(order.target_priority_categories)) != len(order.target_priority_categories):
        raise ContractError("tactical_command.target_priorities", f"{path}.target_priority_categories", "目标类别必须合法且不得重复")
    route_kinds = {"move_route", "air_patrol", "anti_ship_patrol", "free_fire_area"}
    if order.kind in route_kinds and not order.waypoints_world_m:
        raise ContractError(
            "tactical_command.route_required",
            f"{path}.waypoints_world_m",
            "移动和巡逻命令必须至少包含一个航点",
        )
    if order.kind == "attack_target":
        if order.target_ship_id is None or order.engagement_distance_m is None:
            raise ContractError(
                "tactical_command.attack_target_required",
                path,
                "指定攻击必须保存目标舰与交战距离",
            )
    elif order.target_ship_id is not None:
        raise ContractError(
            "tactical_command.target_ship_unexpected",
            f"{path}.target_ship_id",
            "只有指定攻击命令保存目标舰",
        )
    if order.kind == "hold" and order.waypoints_world_m:
        raise ContractError(
            "tactical_command.hold_route",
            f"{path}.waypoints_world_m",
            "原地稳定命令不得附带航路",
        )
    if order.status == "failed" and order.failure_reason is None:
        raise ContractError(
            "tactical_command.failure_reason",
            path,
            "失败命令必须保存稳定原因",
        )
    if order.status != "failed" and order.failure_reason is not None:
        raise ContractError(
            "tactical_command.failure_reason",
            path,
            "非失败命令不得保存失败原因",
        )


@dataclass(frozen=True)
class TacticalFleetCommandState:
    player_side_id: str
    mode: str
    phase: str
    direct_control_ship_id: str | None
    tuning_profile: ResourceReference
    tuning_profile_sha256: str
    assignments: tuple[TacticalShipRoleAssignment, ...]
    orders: tuple[TacticalShipOrder, ...]
    source_scene_sha256: str
    last_scene_step_index: int
    direct_control_loss_reason: str | None = None
    direct_control_loss_step_index: int | None = None
    withdrawal_extra_loss_pending: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "assignments": [item.to_dict() for item in self.assignments],
            "direct_control_loss_reason": self.direct_control_loss_reason,
            "direct_control_loss_step_index": self.direct_control_loss_step_index,
            "direct_control_ship_id": self.direct_control_ship_id,
            "interface": TACTICAL_FLEET_COMMAND_INTERFACE_ID,
            "last_scene_step_index": self.last_scene_step_index,
            "mode": self.mode,
            "orders": [item.to_dict() for item in self.orders],
            "phase": self.phase,
            "player_side_id": self.player_side_id,
            "policy": TACTICAL_FLEET_COMMAND_POLICY_ID,
            "source_scene_sha256": self.source_scene_sha256,
            "tuning_profile": self.tuning_profile.to_dict(),
            "tuning_profile_sha256": self.tuning_profile_sha256,
            "withdrawal_extra_loss_pending": self.withdrawal_extra_loss_pending,
        }

    @classmethod
    def parse(cls, value: Any, path: str = "$") -> "TacticalFleetCommandState":
        obj = _exact_object(
            value,
            {
                "assignments",
                "direct_control_loss_reason",
                "direct_control_loss_step_index",
                "direct_control_ship_id",
                "interface",
                "last_scene_step_index",
                "mode",
                "orders",
                "phase",
                "player_side_id",
                "policy",
                "source_scene_sha256",
                "tuning_profile",
                "tuning_profile_sha256",
                "withdrawal_extra_loss_pending",
            },
            path,
        )
        if (
            obj["interface"] != TACTICAL_FLEET_COMMAND_INTERFACE_ID
            or obj["policy"] != TACTICAL_FLEET_COMMAND_POLICY_ID
        ):
            raise ContractError("tactical_command.interface", path, "不是当前舰队指挥合同")
        if not isinstance(obj["assignments"], list) or not obj["assignments"]:
            raise ContractError("tactical_command.assignments", f"{path}.assignments", "必须至少有一条角色绑定")
        if not isinstance(obj["orders"], list):
            raise ContractError("type.array", f"{path}.orders", "命令必须是数组")
        mode = obj["mode"]
        phase = obj["phase"]
        if mode not in PLAYER_COMMAND_MODES:
            raise ContractError("tactical_command.mode", f"{path}.mode", str(mode))
        if phase not in COMMAND_PHASES:
            raise ContractError("tactical_command.phase", f"{path}.phase", str(phase))
        loss_reason = obj["direct_control_loss_reason"]
        if loss_reason is not None and (not isinstance(loss_reason, str) or not loss_reason):
            raise ContractError("type.string", f"{path}.direct_control_loss_reason", "原因不得为空")
        loss_step_value = obj["direct_control_loss_step_index"]
        loss_step = (
            None
            if loss_step_value is None
            else _integer(loss_step_value, f"{path}.direct_control_loss_step_index")
        )
        extra = obj["withdrawal_extra_loss_pending"]
        if not isinstance(extra, bool):
            raise ContractError("type.boolean", f"{path}.withdrawal_extra_loss_pending", "必须是布尔值")
        state = cls(
            _resource_id(obj["player_side_id"], f"{path}.player_side_id"),
            mode,
            phase,
            _optional_resource_id(obj["direct_control_ship_id"], f"{path}.direct_control_ship_id"),
            ResourceReference.parse(obj["tuning_profile"], f"{path}.tuning_profile"),
            _sha256(obj["tuning_profile_sha256"], f"{path}.tuning_profile_sha256"),
            tuple(
                sorted(
                    (
                        TacticalShipRoleAssignment.parse(item, f"{path}.assignments[{index}]")
                        for index, item in enumerate(obj["assignments"])
                    ),
                    key=lambda item: item.ship_id,
                )
            ),
            tuple(
                sorted(
                    (
                        TacticalShipOrder.parse(item, f"{path}.orders[{index}]")
                        for index, item in enumerate(obj["orders"])
                    ),
                    key=lambda item: (item.ship_id, item.id),
                )
            ),
            _sha256(obj["source_scene_sha256"], f"{path}.source_scene_sha256"),
            _integer(obj["last_scene_step_index"], f"{path}.last_scene_step_index"),
            loss_reason,
            loss_step,
            extra,
        )
        _validate_command_state_shape(state, path)
        return state


@dataclass(frozen=True)
class TacticalDirectControlFrame:
    controls: TacticalControlInput = TacticalControlInput()
    target_layer: str | None = None


@dataclass(frozen=True)
class TacticalCommandApplication:
    ship_id: str
    source: str
    controls: TacticalControlInput

    def to_dict(self) -> dict[str, Any]:
        return {
            "controls": _control_to_dict(self.controls),
            "ship_id": self.ship_id,
            "source": self.source,
        }


@dataclass(frozen=True)
class TacticalFleetCommandEvent:
    kind: str
    tactical_time_s: float
    step_index: int
    ship_id: str | None
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "reason": self.reason,
            "ship_id": self.ship_id,
            "step_index": self.step_index,
            "tactical_time_s": self.tactical_time_s,
        }


@dataclass(frozen=True)
class TacticalFleetCommandStepResolution:
    source_command_state_sha256: str
    scene_resolution: TacticalSceneStepResolution
    resulting_command_state: TacticalFleetCommandState
    applications: tuple[TacticalCommandApplication, ...]
    command_events: tuple[TacticalFleetCommandEvent, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "applications": [item.to_dict() for item in self.applications],
            "command_events": [item.to_dict() for item in self.command_events],
            "interface": TACTICAL_FLEET_COMMAND_INTERFACE_ID,
            "policy": TACTICAL_FLEET_COMMAND_POLICY_ID,
            "resulting_command_state_sha256": canonical_sha256(
                self.resulting_command_state
            ),
            "resulting_scene_sha256": canonical_sha256(
                self.scene_resolution.resulting_scene
            ),
            "source_command_state_sha256": self.source_command_state_sha256,
            "source_scene_sha256": self.scene_resolution.source_scene_sha256,
        }


def _validate_command_state_shape(
    state: TacticalFleetCommandState,
    path: str = "$",
) -> None:
    assignments = {item.ship_id: item for item in state.assignments}
    if len(assignments) != len(state.assignments):
        raise ContractError("tactical_command.assignment_duplicate", f"{path}.assignments", "舰艇角色不得重复")
    if tuple(sorted(assignments)) != tuple(item.ship_id for item in state.assignments):
        raise ContractError("tactical_command.assignment_order", f"{path}.assignments", "角色绑定必须按舰艇 id 排序")
    if sum(item.role == "main_flagship" for item in state.assignments) > 1:
        raise ContractError("tactical_command.main_flagship_duplicate", f"{path}.assignments", "同一场景至多有一艘玩家主旗舰")
    for assignment in state.assignments:
        _resource_id(assignment.ship_id, f"{path}.assignments.ship_id")
        if assignment.role not in SHIP_ROLES:
            raise ContractError("tactical_command.ship_role", f"{path}.assignments.{assignment.ship_id}.role", assignment.role)
        if assignment.formation_anchor_ship_id is not None:
            _resource_id(
                assignment.formation_anchor_ship_id,
                f"{path}.assignments.{assignment.ship_id}.formation_anchor_ship_id",
            )
        _number(assignment.formation_offset_body_m.x, f"{path}.assignments.{assignment.ship_id}.formation_offset_body_m[0]")
        _number(assignment.formation_offset_body_m.y, f"{path}.assignments.{assignment.ship_id}.formation_offset_body_m[1]")
        if (
            assignment.formation_anchor_ship_id is not None
            and assignment.formation_anchor_ship_id not in assignments
        ):
            raise ContractError(
                "tactical_command.formation_anchor_missing",
                f"{path}.assignments.{assignment.ship_id}",
                "编队锚点必须是同一玩家阵营的场景舰艇",
            )
        if assignment.formation_anchor_ship_id == assignment.ship_id:
            raise ContractError(
                "tactical_command.self_formation_anchor",
                f"{path}.assignments.{assignment.ship_id}",
                "舰艇不能以自身作为编队锚点",
            )
        if (
            assignment.formation_anchor_ship_id is not None
            and state.mode != "final_mobilization"
            and assignments[assignment.formation_anchor_ship_id].role
            not in {"main_flagship", "branch_flagship"}
        ):
            raise ContractError(
                "tactical_command.formation_anchor_role",
                f"{path}.assignments.{assignment.ship_id}",
                "编队锚点必须是主旗舰或分旗舰",
            )
    order_ships = [item.ship_id for item in state.orders]
    if len(order_ships) != len(set(order_ships)):
        raise ContractError("tactical_command.order_duplicate", f"{path}.orders", "同一舰艇只能保存一条当前命令")
    if any(item.ship_id not in assignments for item in state.orders):
        raise ContractError("tactical_command.order_ship_missing", f"{path}.orders", "命令只能发给玩家阵营舰艇")
    for order in state.orders:
        _validate_order_shape(order, f"{path}.orders.{order.ship_id}")
    if state.mode == "normal":
        if state.direct_control_ship_id is None:
            raise ContractError("tactical_command.direct_required", f"{path}.direct_control_ship_id", "常规战术场景必须冻结一艘直控舰")
        direct = assignments.get(state.direct_control_ship_id)
        if direct is None or direct.role not in {"main_flagship", "branch_flagship"}:
            raise ContractError("tactical_command.direct_role", f"{path}.direct_control_ship_id", "只能直控主旗舰或分旗舰")
    else:
        if state.direct_control_ship_id is not None:
            raise ContractError("tactical_command.final_mobilization_direct", f"{path}.direct_control_ship_id", "最终动员不能直接操纵舰艇")
        if any(item.role != "ordinary_ship" for item in state.assignments):
            raise ContractError("tactical_command.final_mobilization_roles", f"{path}.assignments", "最终动员仅由剩余普通舰艇组成")
    if state.phase == "active":
        if state.direct_control_loss_reason is not None or state.direct_control_loss_step_index is not None or state.withdrawal_extra_loss_pending:
            raise ContractError("tactical_command.active_loss_state", path, "活动指挥状态不得保存直控失效结果")
    else:
        if state.mode != "normal" or state.direct_control_loss_reason is None or state.direct_control_loss_step_index is None:
            raise ContractError("tactical_command.withdrawal_state", path, "指挥失败撤离必须保存失效原因和步号")


def _scene_ship_map(scene: TacticalSceneState) -> dict[str, Any]:
    return {item.ship_id: item for item in scene.ships}


def _validate_scene_binding(
    state: TacticalFleetCommandState,
    scene: TacticalSceneState,
) -> None:
    if state.source_scene_sha256 != canonical_sha256(scene):
        raise ContractError("tactical_command.scene_mismatch", "$.source_scene_sha256", "舰队指挥状态没有绑定当前精确战术场景")
    if state.last_scene_step_index != scene.fixed_step_index:
        raise ContractError("tactical_command.clock_mismatch", "$.last_scene_step_index", "舰队指挥状态与场景步号不一致")
    player_ships = {
        item.ship_id for item in scene.ships if item.side_id == state.player_side_id
    }
    if player_ships != {item.ship_id for item in state.assignments}:
        raise ContractError("tactical_command.player_ship_set", "$.assignments", "角色绑定必须恰好覆盖场景中的全部玩家舰艇")


def initialize_tactical_fleet_command_state(
    scene: TacticalSceneState,
    *,
    tuning: TacticalCommandTuningProfile,
    player_side_id: str,
    assignments: Iterable[TacticalShipRoleAssignment],
    direct_control_ship_id: str | None,
    mode: str = "normal",
) -> TacticalFleetCommandState:
    player_side = _resource_id(player_side_id, "$.player_side_id")
    assignment_tuple = tuple(sorted(assignments, key=lambda item: item.ship_id))
    state = TacticalFleetCommandState(
        player_side,
        mode,
        "active",
        direct_control_ship_id,
        tuning.reference,
        tuning.source_sha256,
        assignment_tuple,
        (),
        canonical_sha256(scene),
        scene.fixed_step_index,
    )
    _validate_command_state_shape(state)
    _validate_scene_binding(state, scene)
    if mode == "normal":
        ship = _scene_ship_map(scene)[str(direct_control_ship_id)]
        if (
            ship.lifecycle_state.physical_status != "operational"
            or ship.lifecycle_state.command_status != "scene_command"
        ):
            raise ContractError(
                "tactical_command.direct_unavailable",
                "$.direct_control_ship_id",
                "进入战术场景时直控旗舰必须可战且具有场景指挥权",
            )
    return state


def issue_tactical_ship_order(
    state: TacticalFleetCommandState,
    scene: TacticalSceneState,
    order: TacticalShipOrder,
) -> TacticalFleetCommandState:
    _validate_command_state_shape(state)
    _validate_scene_binding(state, scene)
    _validate_order_shape(order, "$.order")
    if state.phase != "active":
        raise ContractError("tactical_command.withdrawal_locked", "$.phase", "直控失效后不能再下达一般命令")
    assignments = {item.ship_id: item for item in state.assignments}
    if order.ship_id not in assignments:
        raise ContractError("tactical_command.order_ship_missing", "$.order.ship_id", order.ship_id)
    if order.issued_step_index != scene.fixed_step_index:
        raise ContractError("tactical_command.order_clock", "$.order.issued_step_index", "命令必须在当前固定步下达")
    ship = _scene_ship_map(scene)[order.ship_id]
    if ship.lifecycle_state.command_status != "scene_command" or ship.lifecycle_state.physical_status != "operational":
        raise ContractError("tactical_command.order_unavailable", "$.order.ship_id", "舰艇当前不能接收舰队命令")
    if order.kind == "attack_target":
        target = _scene_ship_map(scene).get(str(order.target_ship_id))
        if target is None or target.side_id == state.player_side_id:
            raise ContractError(
                "tactical_command.attack_target_invalid",
                "$.order.target_ship_id",
                "指定攻击目标必须是当前场景中的敌对舰艇",
            )
    orders = {item.ship_id: item for item in state.orders}
    orders[order.ship_id] = order
    result = replace(
        state,
        orders=tuple(sorted(orders.values(), key=lambda item: (item.ship_id, item.id))),
    )
    _validate_command_state_shape(result)
    return result


def cancel_tactical_ship_order(
    state: TacticalFleetCommandState,
    scene: TacticalSceneState,
    ship_id: str,
) -> TacticalFleetCommandState:
    _validate_scene_binding(state, scene)
    normalized = _resource_id(ship_id, "$.ship_id")
    orders = {item.ship_id: item for item in state.orders}
    if normalized not in orders:
        raise ContractError("tactical_command.order_missing", "$.ship_id", normalized)
    orders[normalized] = replace(orders[normalized], status="cancelled")
    return replace(
        state,
        orders=tuple(sorted(orders.values(), key=lambda item: (item.ship_id, item.id))),
    )


def _direct_loss_reason(ship: Any) -> str | None:
    if ship.lifecycle_state.physical_status == "falling":
        return "direct_ship_falling"
    if ship.lifecycle_state.physical_status == "exited":
        return "direct_ship_exited"
    if ship.lifecycle_state.command_status != "scene_command":
        return "direct_control_link_lost"
    return None


def _synchronize_state_to_scene(
    state: TacticalFleetCommandState,
    scene: TacticalSceneState,
    *,
    require_source_match: bool,
    waypoint_tolerance_m: float,
) -> tuple[TacticalFleetCommandState, tuple[TacticalFleetCommandEvent, ...]]:
    if require_source_match:
        _validate_scene_binding(state, scene)
    ship_map = _scene_ship_map(scene)
    assignments = {item.ship_id: item for item in state.assignments}
    events: list[TacticalFleetCommandEvent] = []
    orders: dict[str, TacticalShipOrder] = {item.ship_id: item for item in state.orders}

    for ship_id, order in tuple(orders.items()):
        if order.status != "active":
            continue
        ship = ship_map[ship_id]
        if ship.lifecycle_state.physical_status != "operational":
            orders[ship_id] = replace(order, status="failed", failure_reason="ship_not_operational")
            events.append(
                TacticalFleetCommandEvent(
                    "order_failed",
                    scene.tactical_time_s,
                    scene.fixed_step_index,
                    ship_id,
                    "ship_not_operational",
                )
            )
            continue
        if order.kind == "attack_target":
            target = ship_map.get(str(order.target_ship_id))
            if target is None or target.lifecycle_state.physical_status != "operational":
                orders[ship_id] = replace(order, status="failed", failure_reason="target_lost")
                events.append(
                    TacticalFleetCommandEvent(
                        "order_failed",
                        scene.tactical_time_s,
                        scene.fixed_step_index,
                        ship_id,
                        "target_lost",
                    )
                )
                continue
        if order.kind in {"move_route", "air_patrol", "anti_ship_patrol", "free_fire_area", "rescue_and_withdraw"} and order.waypoints_world_m:
            index = min(order.waypoint_index, len(order.waypoints_world_m) - 1)
            target = order.waypoints_world_m[index]
            distance = hypot(
                target.x - ship.motion_state.position_world_m.x,
                target.y - ship.motion_state.position_world_m.y,
            )
            if distance <= waypoint_tolerance_m + EPS:
                if order.kind in {"air_patrol", "anti_ship_patrol", "free_fire_area"}:
                    orders[ship_id] = replace(
                        order,
                        waypoint_index=(index + 1) % len(order.waypoints_world_m),
                    )
                elif index + 1 < len(order.waypoints_world_m):
                    orders[ship_id] = replace(order, waypoint_index=index + 1)
                else:
                    orders[ship_id] = replace(order, waypoint_index=len(order.waypoints_world_m), status="completed")
                    events.append(
                        TacticalFleetCommandEvent(
                            "order_completed",
                            scene.tactical_time_s,
                            scene.fixed_step_index,
                            ship_id,
                            "route_completed",
                        )
                    )

    result = replace(
        state,
        orders=tuple(sorted(orders.values(), key=lambda item: (item.ship_id, item.id))),
        source_scene_sha256=canonical_sha256(scene),
        last_scene_step_index=scene.fixed_step_index,
    )
    if result.mode == "normal" and result.phase == "active":
        direct_id = str(result.direct_control_ship_id)
        reason = _direct_loss_reason(ship_map[direct_id])
        if reason is not None:
            role = assignments[direct_id].role
            withdrawal_orders = {
                item.ship_id: item
                for item in result.orders
                if item.ship_id == direct_id
            }
            for ship_id in sorted(assignments):
                if ship_id == direct_id:
                    continue
                ship = ship_map[ship_id]
                if ship.lifecycle_state.physical_status != "operational":
                    continue
                withdrawal_orders[ship_id] = TacticalShipOrder(
                    f"order.auto.rescue-withdraw.{scene.fixed_step_index}.{ship_id}",
                    ship_id,
                    "rescue_and_withdraw",
                    scene.fixed_step_index,
                )
            result = replace(
                result,
                phase="command_defeat_withdrawal",
                orders=tuple(
                    sorted(
                        withdrawal_orders.values(),
                        key=lambda item: (item.ship_id, item.id),
                    )
                ),
                direct_control_loss_reason=reason,
                direct_control_loss_step_index=scene.fixed_step_index,
                withdrawal_extra_loss_pending=role == "branch_flagship",
            )
            events.append(
                TacticalFleetCommandEvent(
                    "direct_control_lost",
                    scene.tactical_time_s,
                    scene.fixed_step_index,
                    direct_id,
                    reason,
                )
            )
    _validate_command_state_shape(result)
    return result, tuple(events)


def _destination_control(
    position: Vec2,
    velocity: Vec2,
    heading_rad: float,
    destination: Vec2,
    target_speed_mps: float,
    target_heading_rad: float | None,
    tuning: TacticalCommandTuningProfile,
) -> TacticalControlInput:
    delta = destination - position
    distance = delta.length
    if distance <= tuning.waypoint_tolerance_m:
        desired_heading = heading_rad if target_heading_rad is None else target_heading_rad
        heading_error = wrap_angle(desired_heading - heading_rad)
        return TacticalControlInput(
            wheel=clamp(
                heading_error / radians(tuning.heading_full_wheel_error_deg),
                -1.0,
                1.0,
            ),
            brake=target_speed_mps <= EPS,
        )
    direction = delta * (1.0 / distance)
    desired_velocity_world = direction * target_speed_mps
    velocity_error_body = world_to_body(desired_velocity_world - velocity, heading_rad)
    desired_heading = (
        atan2(delta.x, delta.y)
        if target_heading_rad is None
        else target_heading_rad
    )
    heading_error = wrap_angle(desired_heading - heading_rad)
    return TacticalControlInput(
        move_body=Vec2(
            clamp(
                velocity_error_body.x / tuning.velocity_error_full_command_mps,
                -1.0,
                1.0,
            ),
            clamp(
                velocity_error_body.y / tuning.velocity_error_full_command_mps,
                -1.0,
                1.0,
            ),
        ),
        wheel=clamp(
            heading_error / radians(tuning.heading_full_wheel_error_deg),
            -1.0,
            1.0,
        ),
        overg=False,
    )


def _order_destination(
    order: TacticalShipOrder,
    scene: TacticalSceneState,
) -> Vec2 | None:
    if order.kind == "attack_target":
        target = _scene_ship_map(scene).get(str(order.target_ship_id))
        return None if target is None else target.motion_state.position_world_m
    if order.waypoints_world_m and order.waypoint_index < len(order.waypoints_world_m):
        return order.waypoints_world_m[order.waypoint_index]
    return None


def _formation_destination(
    assignment: TacticalShipRoleAssignment,
    scene: TacticalSceneState,
) -> tuple[Vec2, float, float] | None:
    if assignment.formation_anchor_ship_id is None:
        return None
    anchor = _scene_ship_map(scene).get(assignment.formation_anchor_ship_id)
    if anchor is None or anchor.lifecycle_state.physical_status != "operational":
        return None
    offset_world = body_to_world(
        assignment.formation_offset_body_m,
        anchor.motion_state.heading_rad,
    )
    return (
        anchor.motion_state.position_world_m + offset_world,
        anchor.motion_state.velocity_world_mps.length,
        anchor.motion_state.heading_rad,
    )


def _command_for_ship(
    ship: Any,
    assignment: TacticalShipRoleAssignment,
    order: TacticalShipOrder | None,
    scene: TacticalSceneState,
    tuning: TacticalCommandTuningProfile,
) -> tuple[TacticalControlInput, str, str | None]:
    if ship.lifecycle_state.command_status != "scene_command":
        return TacticalControlInput(), "local_or_uncommanded", None
    if order is not None and order.status == "active":
        if order.kind == "hold":
            return TacticalControlInput(brake=True), "order.hold", order.target_layer
        destination = _order_destination(order, scene)
        if destination is not None:
            speed = order.target_speed_mps
            if order.kind == "attack_target" and order.engagement_distance_m is not None:
                distance = (destination - ship.motion_state.position_world_m).length
                if distance <= order.engagement_distance_m + EPS:
                    desired = _destination_control(
                        ship.motion_state.position_world_m,
                        ship.motion_state.velocity_world_mps,
                        ship.motion_state.heading_rad,
                        destination,
                        0.0,
                        None,
                        tuning,
                    )
                    return replace(desired, brake=True), "order.attack_in_range", order.target_layer
            return (
                _destination_control(
                    ship.motion_state.position_world_m,
                    ship.motion_state.velocity_world_mps,
                    ship.motion_state.heading_rad,
                    destination,
                    speed,
                    order.target_heading_rad,
                    tuning,
                ),
                f"order.{order.kind}",
                order.target_layer,
            )
        if order.kind == "rescue_and_withdraw":
            return TacticalControlInput(brake=True), "order.rescue_before_withdrawal_route", order.target_layer
    formation = _formation_destination(assignment, scene)
    if formation is not None:
        destination, speed, heading = formation
        return (
            _destination_control(
                ship.motion_state.position_world_m,
                ship.motion_state.velocity_world_mps,
                ship.motion_state.heading_rad,
                destination,
                speed,
                heading,
                tuning,
            ),
            "formation_return_or_maintain",
            None,
        )
    return TacticalControlInput(), "stable_flight", None


def _apply_layer_request(
    scene: TacticalSceneState,
    binding_by_id: dict[str, TacticalSceneShipBinding],
    ship_id: str,
    target_layer: str | None,
) -> TacticalSceneState:
    if target_layer is None:
        return scene
    if target_layer not in HEIGHT_LAYERS:
        raise ContractError("tactical_command.target_layer", "$.target_layer", str(target_layer))
    ship_map = _scene_ship_map(scene)
    ship = ship_map[ship_id]
    if ship.motion_state.height_layer == target_layer:
        return scene
    if ship.motion_state.layer_transition is not None:
        if ship.motion_state.layer_transition.target_layer != target_layer:
            raise ContractError("tactical_command.layer_transition_conflict", f"$.ships.{ship_id}", "现有换层完成前不能改换目标层")
        return scene
    binding = binding_by_id[ship_id]
    runtime = compile_runtime_ship_parameters(
        binding.snapshot,
        binding.sortie,
        ship.combat_state.instance,
        active_automatic_events=binding.active_automatic_events,
    )
    model = build_tactical_ship_model(runtime, binding.snapshot)
    motion = request_layer_transition(model, ship.motion_state, target_layer)
    ship_map[ship_id] = replace(ship, motion_state=motion)
    return replace(scene, ships=tuple(sorted(ship_map.values(), key=lambda item: item.ship_id)))


def advance_commanded_tactical_scene_step(
    scene: TacticalSceneState,
    command_state: TacticalFleetCommandState,
    bindings: Iterable[TacticalSceneShipBinding],
    timing_catalog: WeaponTimingProfileCatalog,
    projectile_catalog: ProjectileProfileCatalog,
    material_registry: MaterialRegistry,
    tuning: TacticalCommandTuningProfile,
    *,
    guidance_catalog: MissileGuidanceProfileCatalog | None = None,
    guidance_inputs: Iterable[MissileGuidanceRuntimeInput] = (),
    observation_step_input: TacticalObservationStepInput | None = None,
    continuous_damage_profile: ContinuousDamageProfile | None = None,
    fire_ignition_outcomes: Iterable[FireIgnitionOutcome] = (),
    damage_control_directives: Iterable[DamageControlDirective] = (),
    fire_propagation_outcomes: Iterable[FirePropagationOutcome] = (),
    ammunition_cookoff_outcomes: Iterable[AmmunitionCookoffOutcome] = (),
    crew_casualty_outcomes: Iterable[CrewCasualtyOutcome] = (),
    crew_evacuation_outcomes: Iterable[CrewEvacuationOutcome] = (),
    direct_control: TacticalDirectControlFrame | None = None,
    npc_controls: dict[str, TacticalControlInput] | None = None,
    launch_directives: Iterable[TacticalSceneLaunchDirective] = (),
    exit_directives: Iterable[TacticalSceneExitDirective] = (),
    engagement_boundary_profile: TacticalEngagementBoundaryProfile | None = None,
    ricochet_rolls: dict[str, float] | None = None,
) -> TacticalFleetCommandStepResolution:
    _validate_command_state_shape(command_state)
    synchronized, pre_events = _synchronize_state_to_scene(
        command_state,
        scene,
        require_source_match=True,
        waypoint_tolerance_m=tuning.waypoint_tolerance_m,
    )
    if (
        synchronized.tuning_profile != tuning.reference
        or synchronized.tuning_profile_sha256 != tuning.source_sha256
    ):
        raise ContractError(
            "tactical_command.tuning_mismatch",
            "$.tuning",
            "战术指挥配置引用或内容指纹不匹配",
        )
    if synchronized.phase != "active" and direct_control is not None:
        raise ContractError("tactical_command.direct_after_loss", "$.direct_control", "直控失效后不能切换或继续注入玩家操纵")
    if synchronized.mode == "final_mobilization" and direct_control is not None:
        raise ContractError("tactical_command.final_mobilization_direct", "$.direct_control", "最终动员只能下达命令")

    binding_tuple = tuple(bindings)
    exit_directive_tuple = tuple(exit_directives)
    binding_by_id = {item.ship_id: item for item in binding_tuple}
    if len(binding_by_id) != len(binding_tuple) or set(binding_by_id) != {item.ship_id for item in scene.ships}:
        raise ContractError("tactical_command.binding_set", "$.bindings", "绑定必须恰好覆盖场景舰艇")
    ship_map = _scene_ship_map(scene)
    assignments = {item.ship_id: item for item in synchronized.assignments}
    orders = {item.ship_id: item for item in synchronized.orders}
    npc_map = {} if npc_controls is None else npc_controls
    for ship_id in npc_map:
        ship = ship_map.get(ship_id)
        if ship is None:
            raise ContractError("tactical_command.npc_ship_missing", "$.npc_controls", ship_id)
        if ship.side_id == synchronized.player_side_id:
            raise ContractError("tactical_command.player_control_bypass", f"$.npc_controls.{ship_id}", "玩家舰艇必须经过唯一直接操纵或RTS命令仲裁")

    prepared_scene = scene
    controls: dict[str, TacticalControlInput] = dict(npc_map)
    applications: list[TacticalCommandApplication] = [
        TacticalCommandApplication(ship_id, "npc_external", value)
        for ship_id, value in sorted(npc_map.items())
    ]
    for ship_id in sorted(assignments):
        ship = _scene_ship_map(prepared_scene)[ship_id]
        if (
            synchronized.mode == "normal"
            and synchronized.phase == "active"
            and ship_id == synchronized.direct_control_ship_id
        ):
            if direct_control is not None:
                if ship.lifecycle_state.command_status != "scene_command" or ship.lifecycle_state.physical_status != "operational":
                    raise ContractError("tactical_command.direct_unavailable", "$.direct_control", "直控舰当前不可接受玩家操纵")
                controls[ship_id] = direct_control.controls
                applications.append(
                    TacticalCommandApplication(ship_id, "player_direct", direct_control.controls)
                )
                prepared_scene = _apply_layer_request(
                    prepared_scene,
                    binding_by_id,
                    ship_id,
                    direct_control.target_layer,
                )
            elif not any(
                item.ship_id == ship_id
                and abs(item.tactical_time_s - scene.tactical_time_s) <= EPS
                for item in exit_directive_tuple
            ):
                direct_order = orders.get(ship_id)
                if direct_order is not None and direct_order.status == "active":
                    control, source, target_layer = _command_for_ship(
                        ship,
                        assignments[ship_id],
                        direct_order,
                        prepared_scene,
                        tuning,
                    )
                    controls[ship_id] = control
                    applications.append(
                        TacticalCommandApplication(
                            ship_id,
                            f"direct_autopilot.{source}",
                            control,
                        )
                    )
                    prepared_scene = _apply_layer_request(
                        prepared_scene,
                        binding_by_id,
                        ship_id,
                        target_layer,
                    )
            # 没有本帧玩家输入和预设动作时，由底层默认零舵/零推力保持稳定。
            continue
        control, source, target_layer = _command_for_ship(
            ship,
            assignments[ship_id],
            orders.get(ship_id),
            prepared_scene,
            tuning,
        )
        if ship.lifecycle_state.command_status == "scene_command" and ship.lifecycle_state.physical_status == "operational":
            controls[ship_id] = control
            applications.append(TacticalCommandApplication(ship_id, source, control))
            prepared_scene = _apply_layer_request(
                prepared_scene,
                binding_by_id,
                ship_id,
                target_layer,
            )

    resolution = advance_tactical_scene_step(
        prepared_scene,
        binding_tuple,
        timing_catalog,
        projectile_catalog,
        material_registry,
        guidance_catalog=guidance_catalog,
        guidance_inputs=guidance_inputs,
        observation_step_input=observation_step_input,
        continuous_damage_profile=continuous_damage_profile,
        fire_ignition_outcomes=fire_ignition_outcomes,
        damage_control_directives=damage_control_directives,
        fire_propagation_outcomes=fire_propagation_outcomes,
        ammunition_cookoff_outcomes=ammunition_cookoff_outcomes,
        crew_casualty_outcomes=crew_casualty_outcomes,
        crew_evacuation_outcomes=crew_evacuation_outcomes,
        controls=controls,
        launch_directives=launch_directives,
        exit_directives=exit_directive_tuple,
        engagement_boundary_profile=engagement_boundary_profile,
        ricochet_rolls=ricochet_rolls,
    )
    resulting_state = replace(
        synchronized,
        source_scene_sha256=canonical_sha256(resolution.resulting_scene),
        last_scene_step_index=resolution.resulting_scene.fixed_step_index,
    )
    resulting_state, post_events = _synchronize_state_to_scene(
        resulting_state,
        resolution.resulting_scene,
        require_source_match=True,
        waypoint_tolerance_m=tuning.waypoint_tolerance_m,
    )
    return TacticalFleetCommandStepResolution(
        canonical_sha256(command_state),
        resolution,
        resulting_state,
        tuple(sorted(applications, key=lambda item: item.ship_id)),
        (*pre_events, *post_events),
    )
