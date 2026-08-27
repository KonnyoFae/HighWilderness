"""《高天荒野》阶段 I5：从成功开火事件到弹丸飞行、命中与损伤回写。"""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
from math import cos, hypot, isfinite, sin
from pathlib import Path
from typing import Any, Iterable

from 高天荒野舰艇炮弹与甲弹公式 import (
    Aftereffect,
    ArmorState,
    BallisticProjectileProfile,
    ImpactOutcome,
    ImpactResult,
    PenetrationProjectileProfile,
    incidence_angle_deg,
    integrate_ballistic_step,
    relative_impact_velocity_xy,
    resolve_armor_impact,
)
from 高天荒野舰艇数据契约 import (
    ContractError,
    MaterialRegistry,
    RESOURCE_ID_PATTERN,
    ResourceReference,
    ShipInstanceSnapshotInput,
    canonical_sha256,
)
from 高天荒野舰艇无界面舾装编译器 import (
    CompiledModuleInstance,
    DerivedShipSnapshot,
)
from 高天荒野舰艇武器时间与射击队列 import WeaponTimelineEvent
from 高天荒野舰艇战损原子操作 import apply_module_damage_to_instance
from 高天荒野舰艇导弹制导 import (
    MissileGuidanceEvent,
    MissileGuidanceProfileCatalog,
    MissileGuidanceRuntimeInput,
    MissileGuidanceState,
    advance_missile_guidance_step,
    initialize_missile_guidance_state,
)


PROJECTILE_WORLD_INTERFACE_ID = "gaotian.tactical-projectile-world/v1alpha1"
PROJECTILE_PROFILE_SCHEMA_ID = "gaotian.projectile-profile/v1alpha1"
PROJECTILE_INTEGRATION_POLICY_ID = "gaotian.projectile/fixed-time-step-frozen-cd/v1"
PROJECTILE_HIT_POLICY_ID = "gaotian.projectile/explicit-deck-segment-edge/v1"
PROJECTILE_DAMAGE_POLICY_ID = "gaotian.projectile/profile-adapted-ship-damage/v1"
FIXTURE_LEVELS = {"contract_fixture", "prototype_unbalanced", "balance_reference"}
EPS = 1.0e-8


def _resource_id(value: Any, path: str) -> str:
    if not isinstance(value, str) or not RESOURCE_ID_PATTERN.fullmatch(value):
        raise ContractError("resource.id_invalid", path, str(value))
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


def _integer(value: Any, path: str, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractError("type.integer", path, "必须是整数")
    if minimum is not None and value < minimum:
        raise ContractError("value.integer_range", path, str(value))
    return value


def _vector2(value: Any, path: str) -> tuple[float, float]:
    if not isinstance(value, list) or len(value) != 2:
        raise ContractError("projectile_world.vector2", path, "必须是两元数值数组")
    return _number(value[0], f"{path}[0]"), _number(value[1], f"{path}[1]")


def _exact_object(value: Any, required: set[str], path: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != required:
        raise ContractError("object.keys", path, f"必须恰含 {sorted(required)}")
    return value


@dataclass(frozen=True)
class ProjectileDamageAdapter:
    armor_damage_to_local_durability_proxy: float
    surface_module_damage_points: float
    internal_module_damage_points: float
    hull_integrity_damage_fraction: float
    surface_effect_radius_m: float
    internal_effect_range_m: float
    internal_effect_radius_m: float

    @classmethod
    def parse(cls, value: Any, path: str) -> "ProjectileDamageAdapter":
        keys = {
            "armor_damage_to_local_durability_proxy",
            "surface_module_damage_points",
            "internal_module_damage_points",
            "hull_integrity_damage_fraction",
            "surface_effect_radius_m",
            "internal_effect_range_m",
            "internal_effect_radius_m",
        }
        obj = _exact_object(value, keys, path)
        result = cls(
            _number(obj["armor_damage_to_local_durability_proxy"], f"{path}.armor_damage_to_local_durability_proxy", 0.0),
            _number(obj["surface_module_damage_points"], f"{path}.surface_module_damage_points", 0.0),
            _number(obj["internal_module_damage_points"], f"{path}.internal_module_damage_points", 0.0),
            _number(obj["hull_integrity_damage_fraction"], f"{path}.hull_integrity_damage_fraction", 0.0),
            _number(obj["surface_effect_radius_m"], f"{path}.surface_effect_radius_m", 0.0),
            _number(obj["internal_effect_range_m"], f"{path}.internal_effect_range_m", 0.0),
            _number(obj["internal_effect_radius_m"], f"{path}.internal_effect_radius_m", 0.0),
        )
        if result.hull_integrity_damage_fraction > 1.0:
            raise ContractError("projectile_profile.hull_damage_fraction", path, "船壳损伤比例不得超过1")
        return result

    def to_dict(self) -> dict[str, float]:
        return {
            "armor_damage_to_local_durability_proxy": self.armor_damage_to_local_durability_proxy,
            "hull_integrity_damage_fraction": self.hull_integrity_damage_fraction,
            "internal_effect_radius_m": self.internal_effect_radius_m,
            "internal_effect_range_m": self.internal_effect_range_m,
            "internal_module_damage_points": self.internal_module_damage_points,
            "surface_effect_radius_m": self.surface_effect_radius_m,
            "surface_module_damage_points": self.surface_module_damage_points,
        }


@dataclass(frozen=True)
class MunitionProjectileProfile:
    munition_id: str
    name: str
    ballistic: BallisticProjectileProfile
    penetration: PenetrationProjectileProfile
    damage: ProjectileDamageAdapter
    maximum_lifetime_s: float

    @classmethod
    def parse(cls, value: Any, path: str) -> "MunitionProjectileProfile":
        obj = _exact_object(
            value,
            {"munition_id", "name", "ballistic", "penetration", "damage", "maximum_lifetime_s"},
            path,
        )
        ballistic = _exact_object(
            obj["ballistic"],
            {"caliber_mm", "mass_kg", "muzzle_velocity_mps", "form_factor"},
            f"{path}.ballistic",
        )
        penetration = _exact_object(
            obj["penetration"],
            {
                "reference_penetration_mm", "reference_speed_mps", "velocity_exponent",
                "obliquity_exponent", "normalization_deg", "ricochet_start_deg",
                "ricochet_full_deg", "impact_armor_damage_at_reference_speed",
                "surface_effect_armor_damage", "can_ricochet", "aftereffect",
            },
            f"{path}.penetration",
        )
        aftereffect_raw = penetration["aftereffect"]
        try:
            aftereffect = Aftereffect(aftereffect_raw)
        except (TypeError, ValueError) as error:
            raise ContractError("projectile_profile.aftereffect", f"{path}.penetration.aftereffect", str(aftereffect_raw)) from error
        if not isinstance(penetration["can_ricochet"], bool):
            raise ContractError("type.boolean", f"{path}.penetration.can_ricochet", "必须是布尔值")
        name = obj["name"]
        if not isinstance(name, str) or not name:
            raise ContractError("type.string", f"{path}.name", "名称不得为空")
        return cls(
            _resource_id(obj["munition_id"], f"{path}.munition_id"),
            name,
            BallisticProjectileProfile(
                _positive(ballistic["caliber_mm"], f"{path}.ballistic.caliber_mm"),
                _positive(ballistic["mass_kg"], f"{path}.ballistic.mass_kg"),
                _positive(ballistic["muzzle_velocity_mps"], f"{path}.ballistic.muzzle_velocity_mps"),
                _positive(ballistic["form_factor"], f"{path}.ballistic.form_factor"),
            ),
            PenetrationProjectileProfile(
                _number(penetration["reference_penetration_mm"], f"{path}.penetration.reference_penetration_mm", 0.0),
                _positive(penetration["reference_speed_mps"], f"{path}.penetration.reference_speed_mps"),
                _positive(penetration["velocity_exponent"], f"{path}.penetration.velocity_exponent"),
                _positive(penetration["obliquity_exponent"], f"{path}.penetration.obliquity_exponent"),
                _number(penetration["normalization_deg"], f"{path}.penetration.normalization_deg", 0.0),
                _number(penetration["ricochet_start_deg"], f"{path}.penetration.ricochet_start_deg", 0.0),
                _number(penetration["ricochet_full_deg"], f"{path}.penetration.ricochet_full_deg", 0.0),
                _number(penetration["impact_armor_damage_at_reference_speed"], f"{path}.penetration.impact_armor_damage_at_reference_speed", 0.0),
                _number(penetration["surface_effect_armor_damage"], f"{path}.penetration.surface_effect_armor_damage", 0.0),
                penetration["can_ricochet"],
                aftereffect,
            ),
            ProjectileDamageAdapter.parse(obj["damage"], f"{path}.damage"),
            _positive(obj["maximum_lifetime_s"], f"{path}.maximum_lifetime_s"),
        )

    def to_dict(self) -> dict[str, Any]:
        b, p = self.ballistic, self.penetration
        return {
            "ballistic": {
                "caliber_mm": b.caliber_mm,
                "form_factor": b.form_factor,
                "mass_kg": b.mass_kg,
                "muzzle_velocity_mps": b.muzzle_velocity_mps,
            },
            "damage": self.damage.to_dict(),
            "maximum_lifetime_s": self.maximum_lifetime_s,
            "munition_id": self.munition_id,
            "name": self.name,
            "penetration": {
                "aftereffect": p.aftereffect.value,
                "can_ricochet": p.can_ricochet,
                "impact_armor_damage_at_reference_speed": p.impact_armor_damage_at_reference_speed,
                "normalization_deg": p.normalization_deg,
                "obliquity_exponent": p.obliquity_exponent,
                "reference_penetration_mm": p.reference_penetration_mm,
                "reference_speed_mps": p.reference_speed_mps,
                "ricochet_full_deg": p.ricochet_full_deg,
                "ricochet_start_deg": p.ricochet_start_deg,
                "surface_effect_armor_damage": p.surface_effect_armor_damage,
                "velocity_exponent": p.velocity_exponent,
            },
        }


@dataclass(frozen=True)
class ProjectileProfileCatalog:
    id: str
    version: int
    name: str
    fixture_level: str
    profiles: tuple[MunitionProjectileProfile, ...]

    @property
    def reference(self) -> ResourceReference:
        return ResourceReference(self.id, self.version)

    @property
    def source_sha256(self) -> str:
        return canonical_sha256(self)

    def profile(self, munition_id: str) -> MunitionProjectileProfile:
        try:
            return next(item for item in self.profiles if item.munition_id == munition_id)
        except StopIteration as error:
            raise ContractError("projectile_profile.munition_missing", "$.profiles", munition_id) from error

    @classmethod
    def parse(cls, value: Any, path: str = "$") -> "ProjectileProfileCatalog":
        obj = _exact_object(value, {"schema", "kind", "id", "version", "name", "fixture_level", "profiles"}, path)
        if obj["schema"] != PROJECTILE_PROFILE_SCHEMA_ID or obj["kind"] != "ProjectileProfileCatalog":
            raise ContractError("resource.kind", path, "不是弹丸性能目录")
        if isinstance(obj["version"], bool) or not isinstance(obj["version"], int) or obj["version"] < 1:
            raise ContractError("value.version", f"{path}.version", str(obj["version"]))
        if obj["fixture_level"] not in FIXTURE_LEVELS:
            raise ContractError("projectile_profile.fixture_level", f"{path}.fixture_level", str(obj["fixture_level"]))
        if not isinstance(obj["profiles"], list) or not obj["profiles"]:
            raise ContractError("projectile_profile.profiles", f"{path}.profiles", "目录不得为空")
        profiles = tuple(sorted((MunitionProjectileProfile.parse(item, f"{path}.profiles[{index}]") for index, item in enumerate(obj["profiles"])), key=lambda item: item.munition_id))
        if len({item.munition_id for item in profiles}) != len(profiles):
            raise ContractError("projectile_profile.duplicate", f"{path}.profiles", "弹药 id 不得重复")
        if not isinstance(obj["name"], str) or not obj["name"]:
            raise ContractError("type.string", f"{path}.name", "名称不得为空")
        return cls(_resource_id(obj["id"], f"{path}.id"), obj["version"], obj["name"], obj["fixture_level"], profiles)

    def to_dict(self) -> dict[str, Any]:
        return {"fixture_level": self.fixture_level, "id": self.id, "kind": "ProjectileProfileCatalog", "name": self.name, "profiles": [item.to_dict() for item in self.profiles], "schema": PROJECTILE_PROFILE_SCHEMA_ID, "version": self.version}


def load_projectile_profile_catalog(path: str | Path) -> ProjectileProfileCatalog:
    return ProjectileProfileCatalog.parse(json.loads(Path(path).read_text(encoding="utf-8")), str(path))


@dataclass(frozen=True)
class ArmorEdgeRuntimeState:
    deck_id: str
    deck_level: int
    region_id: str
    edge_index: int
    maximum_durability_proxy: float
    current_durability_proxy: float

    @property
    def key(self) -> tuple[str, int, str, int]:
        return self.deck_id, self.deck_level, self.region_id, self.edge_index

    def to_dict(self) -> dict[str, Any]:
        return {"current_durability_proxy": self.current_durability_proxy, "deck_id": self.deck_id, "deck_level": self.deck_level, "edge_index": self.edge_index, "maximum_durability_proxy": self.maximum_durability_proxy, "region_id": self.region_id}

    @classmethod
    def parse(cls, value: Any, path: str) -> "ArmorEdgeRuntimeState":
        obj = _exact_object(value, {"deck_id", "deck_level", "region_id", "edge_index", "maximum_durability_proxy", "current_durability_proxy"}, path)
        maximum = _number(obj["maximum_durability_proxy"], f"{path}.maximum_durability_proxy", 0.0)
        current = _number(obj["current_durability_proxy"], f"{path}.current_durability_proxy", 0.0)
        if current > maximum + EPS:
            raise ContractError("projectile_world.armor_durability_excess", f"{path}.current_durability_proxy", "当前局部耐久不得超过上限")
        return cls(
            _resource_id(obj["deck_id"], f"{path}.deck_id"),
            _integer(obj["deck_level"], f"{path}.deck_level", 0),
            _resource_id(obj["region_id"], f"{path}.region_id"),
            _integer(obj["edge_index"], f"{path}.edge_index", 0),
            maximum,
            current,
        )


@dataclass(frozen=True)
class ShipCombatState:
    instance: ShipInstanceSnapshotInput
    armor_edges: tuple[ArmorEdgeRuntimeState, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"armor_edges": [item.to_dict() for item in self.armor_edges], "instance": self.instance.to_dict()}

    @classmethod
    def parse(cls, value: Any, path: str = "$") -> "ShipCombatState":
        obj = _exact_object(value, {"instance", "armor_edges"}, path)
        if not isinstance(obj["armor_edges"], list):
            raise ContractError("type.array", f"{path}.armor_edges", "必须是数组")
        edges = tuple(sorted((ArmorEdgeRuntimeState.parse(item, f"{path}.armor_edges[{index}]") for index, item in enumerate(obj["armor_edges"])), key=lambda item: item.key))
        if len({item.key for item in edges}) != len(edges):
            raise ContractError("projectile_world.armor_edge_duplicate", f"{path}.armor_edges", "局部装甲边不得重复")
        return cls(ShipInstanceSnapshotInput.parse(obj["instance"], f"{path}.instance"), edges)


def initialize_ship_combat_state(snapshot: DerivedShipSnapshot, instance: ShipInstanceSnapshotInput) -> ShipCombatState:
    if instance.derived_ship_snapshot_sha256 != snapshot.source_sha256:
        raise ContractError("projectile_world.target_snapshot_mismatch", "$.instance.derived_ship_snapshot_sha256", "目标实例与派生快照不匹配")
    states = tuple(
        ArmorEdgeRuntimeState(deck_id, deck_level, region_id, edge_index, maximum, maximum)
        for deck_id, deck_level, region_id, edge_index, maximum in snapshot.hull.local_armor_durability_proxy
    )
    return ShipCombatState(instance, states)


@dataclass(frozen=True)
class ShipPose2D:
    reference_time_s: float
    position_xy: tuple[float, float]
    heading_rad: float
    velocity_xy: tuple[float, float]
    angular_velocity_rad_s: float

    def at(self, time_s: float) -> "ShipPose2D":
        dt = time_s - self.reference_time_s
        return ShipPose2D(time_s, (self.position_xy[0] + self.velocity_xy[0] * dt, self.position_xy[1] + self.velocity_xy[1] * dt), self.heading_rad + self.angular_velocity_rad_s * dt, self.velocity_xy, self.angular_velocity_rad_s)


@dataclass(frozen=True)
class ProjectileState:
    id: str
    source_ship_id: str
    source_weapon_instance_id: str
    munition_id: str
    target_ship_id: str
    selected_target_deck_level: int
    created_time_s: float
    age_s: float
    position_xy: tuple[float, float]
    velocity_xy: tuple[float, float]
    distance_travelled_m: float
    guidance_state: MissileGuidanceState | None = None

    def to_dict(self) -> dict[str, Any]:
        result = {"age_s": self.age_s, "created_time_s": self.created_time_s, "distance_travelled_m": self.distance_travelled_m, "id": self.id, "munition_id": self.munition_id, "position_xy": list(self.position_xy), "selected_target_deck_level": self.selected_target_deck_level, "source_ship_id": self.source_ship_id, "source_weapon_instance_id": self.source_weapon_instance_id, "target_ship_id": self.target_ship_id, "velocity_xy": list(self.velocity_xy)}
        if self.guidance_state is not None:
            result["guidance_state"] = self.guidance_state.to_dict()
        return result

    @classmethod
    def parse(cls, value: Any, path: str) -> "ProjectileState":
        required = {"id", "source_ship_id", "source_weapon_instance_id", "munition_id", "target_ship_id", "selected_target_deck_level", "created_time_s", "age_s", "position_xy", "velocity_xy", "distance_travelled_m"}
        if not isinstance(value, dict) or set(value) not in (required, required | {"guidance_state"}):
            raise ContractError(
                "object.keys",
                path,
                f"必须恰含 {sorted(required)}，并可选 guidance_state",
            )
        obj = value
        projectile_id = _resource_id(obj["id"], f"{path}.id")
        munition_id = _resource_id(obj["munition_id"], f"{path}.munition_id")
        source_ship_id = _resource_id(obj["source_ship_id"], f"{path}.source_ship_id")
        target_ship_id = _resource_id(obj["target_ship_id"], f"{path}.target_ship_id")
        guidance = (
            None
            if "guidance_state" not in obj
            else MissileGuidanceState.parse(obj["guidance_state"], f"{path}.guidance_state")
        )
        if guidance is not None and (
            guidance.projectile_id != projectile_id
            or guidance.munition_id != munition_id
            or guidance.source_ship_id != source_ship_id
            or guidance.intended_target_ship_id != target_ship_id
        ):
            raise ContractError(
                "projectile_world.guidance_binding_mismatch",
                f"{path}.guidance_state",
                "制导状态必须与所属弹丸、弹药、发射舰和预定目标精确一致",
            )
        return cls(
            projectile_id,
            source_ship_id,
            _resource_id(obj["source_weapon_instance_id"], f"{path}.source_weapon_instance_id"),
            munition_id,
            target_ship_id,
            _integer(obj["selected_target_deck_level"], f"{path}.selected_target_deck_level", 0),
            _number(obj["created_time_s"], f"{path}.created_time_s", 0.0),
            _number(obj["age_s"], f"{path}.age_s", 0.0),
            _vector2(obj["position_xy"], f"{path}.position_xy"),
            _vector2(obj["velocity_xy"], f"{path}.velocity_xy"),
            _number(obj["distance_travelled_m"], f"{path}.distance_travelled_m", 0.0),
            guidance,
        )


@dataclass(frozen=True)
class ProjectileWorldState:
    profile_catalog: ResourceReference
    profile_catalog_sha256: str
    tactical_time_s: float
    projectiles: tuple[ProjectileState, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"profile_catalog": self.profile_catalog.to_dict(), "profile_catalog_sha256": self.profile_catalog_sha256, "projectiles": [item.to_dict() for item in self.projectiles], "tactical_time_s": self.tactical_time_s}

    @classmethod
    def parse(cls, value: Any, path: str = "$") -> "ProjectileWorldState":
        obj = _exact_object(value, {"profile_catalog", "profile_catalog_sha256", "tactical_time_s", "projectiles"}, path)
        sha = obj["profile_catalog_sha256"]
        if not isinstance(sha, str) or len(sha) != 64 or any(character not in "0123456789abcdef" for character in sha):
            raise ContractError("value.sha256", f"{path}.profile_catalog_sha256", str(sha))
        if not isinstance(obj["projectiles"], list):
            raise ContractError("type.array", f"{path}.projectiles", "必须是数组")
        projectiles = tuple(sorted((ProjectileState.parse(item, f"{path}.projectiles[{index}]") for index, item in enumerate(obj["projectiles"])), key=lambda item: item.id))
        if len({item.id for item in projectiles}) != len(projectiles):
            raise ContractError("projectile_world.projectile_duplicate", f"{path}.projectiles", "弹丸 id 不得重复")
        time_s = _number(obj["tactical_time_s"], f"{path}.tactical_time_s", 0.0)
        if any(item.created_time_s + item.age_s > time_s + EPS for item in projectiles):
            raise ContractError("projectile_world.projectile_time_ahead", f"{path}.projectiles", "弹丸已演算时刻不得超前世界时刻")
        return cls(ResourceReference.parse(obj["profile_catalog"], f"{path}.profile_catalog"), sha, time_s, projectiles)


def initialize_projectile_world(catalog: ProjectileProfileCatalog, *, tactical_time_s: float = 0.0) -> ProjectileWorldState:
    if tactical_time_s < 0.0 or not isfinite(tactical_time_s):
        raise ContractError("projectile_world.time", "$.tactical_time_s", str(tactical_time_s))
    return ProjectileWorldState(catalog.reference, catalog.source_sha256, tactical_time_s, ())


@dataclass(frozen=True)
class ProjectileSpawnRequest:
    projectile_id: str
    source_ship_id: str
    target_ship_id: str
    selected_target_deck_level: int
    launch_direction_local_xy: tuple[float, float]


@dataclass(frozen=True)
class ProjectileSpawnResolution:
    source_world_sha256: str
    resulting_world: ProjectileWorldState
    projectile: ProjectileState


def _rotate(vector: tuple[float, float], angle: float) -> tuple[float, float]:
    c, s = cos(angle), sin(angle)
    return c * vector[0] - s * vector[1], s * vector[0] + c * vector[1]


def _to_local(point: tuple[float, float], pose: ShipPose2D) -> tuple[float, float]:
    offset = point[0] - pose.position_xy[0], point[1] - pose.position_xy[1]
    return _rotate(offset, -pose.heading_rad)


def _to_world(point: tuple[float, float], pose: ShipPose2D) -> tuple[float, float]:
    rotated = _rotate(point, pose.heading_rad)
    return rotated[0] + pose.position_xy[0], rotated[1] + pose.position_xy[1]


def _validate_catalog(world: ProjectileWorldState, catalog: ProjectileProfileCatalog) -> None:
    if world.profile_catalog != catalog.reference or world.profile_catalog_sha256 != catalog.source_sha256:
        raise ContractError("projectile_world.profile_catalog_mismatch", "$.profile_catalog", "弹丸世界已绑定其他性能目录或内容哈希")


def spawn_projectile_from_weapon_event(
    snapshot: DerivedShipSnapshot,
    event: WeaponTimelineEvent,
    world: ProjectileWorldState,
    catalog: ProjectileProfileCatalog,
    source_pose: ShipPose2D,
    request: ProjectileSpawnRequest,
    *,
    guidance_catalog: MissileGuidanceProfileCatalog | None = None,
) -> ProjectileSpawnResolution:
    _validate_catalog(world, catalog)
    if event.status != "resolved" or event.action_kind != "fire" or event.action_resolution is None:
        raise ContractError("projectile_world.fire_event_unresolved", "$.event", "只能由成功的单发开火事件生成弹丸")
    action = event.action_resolution
    if action.rounds != 1:
        raise ContractError("projectile_world.atomic_fire_required", "$.event.action_resolution.rounds", "I4 事件必须是单发原子开火")
    if abs(event.tactical_time_s - world.tactical_time_s) > EPS:
        raise ContractError("projectile_world.spawn_time_mismatch", "$.event.tactical_time_s", "开火事件时刻必须等于弹丸世界当前时刻")
    if any(item.id == request.projectile_id for item in world.projectiles):
        raise ContractError("projectile_world.projectile_duplicate", "$.projectile_id", request.projectile_id)
    _resource_id(request.projectile_id, "$.projectile_id")
    modules = {item.id: item for item in snapshot.outfit.instances}
    weapon = modules.get(event.weapon_instance_id)
    if weapon is None or weapon.prototype.category != "weapon":
        raise ContractError("projectile_world.weapon_missing", "$.event.weapon_instance_id", event.weapon_instance_id)
    if request.selected_target_deck_level < 0:
        raise ContractError("projectile_world.deck_level", "$.selected_target_deck_level", str(request.selected_target_deck_level))
    magnitude = hypot(*request.launch_direction_local_xy)
    if magnitude <= EPS:
        raise ContractError("projectile_world.launch_direction", "$.launch_direction_local_xy", "发射方向不得为零")
    direction_local = request.launch_direction_local_xy[0] / magnitude, request.launch_direction_local_xy[1] / magnitude
    pose = source_pose.at(world.tactical_time_s)
    origin_offset_world = _rotate(weapon.anchor_m, pose.heading_rad)
    origin = pose.position_xy[0] + origin_offset_world[0], pose.position_xy[1] + origin_offset_world[1]
    surface_velocity = (
        pose.velocity_xy[0] - pose.angular_velocity_rad_s * origin_offset_world[1],
        pose.velocity_xy[1] + pose.angular_velocity_rad_s * origin_offset_world[0],
    )
    profile = catalog.profile(action.munition_id)
    muzzle_world = _rotate(direction_local, pose.heading_rad)
    velocity = (
        surface_velocity[0] + muzzle_world[0] * profile.ballistic.muzzle_velocity_mps,
        surface_velocity[1] + muzzle_world[1] * profile.ballistic.muzzle_velocity_mps,
    )
    guidance = (
        None
        if guidance_catalog is None
        else initialize_missile_guidance_state(
            guidance_catalog,
            projectile_id=request.projectile_id,
            munition_id=action.munition_id,
            source_ship_id=request.source_ship_id,
            intended_target_ship_id=request.target_ship_id,
            launch_time_s=world.tactical_time_s,
        )
    )
    projectile = ProjectileState(
        request.projectile_id, request.source_ship_id, event.weapon_instance_id,
        action.munition_id, request.target_ship_id, request.selected_target_deck_level,
        world.tactical_time_s, 0.0, origin, velocity, 0.0, guidance,
    )
    resulting = replace(world, projectiles=tuple(sorted(world.projectiles + (projectile,), key=lambda item: item.id)))
    return ProjectileSpawnResolution(canonical_sha256(world), resulting, projectile)


@dataclass(frozen=True)
class TacticalProjectileTarget:
    ship_id: str
    snapshot: DerivedShipSnapshot
    combat_state: ShipCombatState
    pose: ShipPose2D
    pose_end: ShipPose2D | None = None
    density_kg_m3: float | None = None
    sound_speed_mps: float | None = None
    height_layer: str | None = None

    def pose_at(self, time_s: float) -> ShipPose2D:
        """返回目标在指定时刻的位姿。

        I5 独立使用时继续沿用 ``pose`` 的常速/定角速度外推；统一战术场景
        可以额外给出固定步末端的实际机动状态，本层便在这一个固定步内对
        位置、速度、朝向和角速度作确定性插值。这样碰撞检测不再依赖场景
        编排器伪造一条常速目标轨迹。
        """

        end = self.pose_end
        if end is None:
            return self.pose.at(time_s)
        duration = end.reference_time_s - self.pose.reference_time_s
        if duration <= EPS:
            return self.pose.at(time_s)
        fraction = max(
            0.0,
            min(1.0, (time_s - self.pose.reference_time_s) / duration),
        )
        heading_delta = _wrap_angle(end.heading_rad - self.pose.heading_rad)
        return ShipPose2D(
            time_s,
            (
                self.pose.position_xy[0]
                + (end.position_xy[0] - self.pose.position_xy[0]) * fraction,
                self.pose.position_xy[1]
                + (end.position_xy[1] - self.pose.position_xy[1]) * fraction,
            ),
            self.pose.heading_rad + heading_delta * fraction,
            (
                self.pose.velocity_xy[0]
                + (end.velocity_xy[0] - self.pose.velocity_xy[0]) * fraction,
                self.pose.velocity_xy[1]
                + (end.velocity_xy[1] - self.pose.velocity_xy[1]) * fraction,
            ),
            self.pose.angular_velocity_rad_s
            + (
                end.angular_velocity_rad_s - self.pose.angular_velocity_rad_s
            )
            * fraction,
        )


def _wrap_angle(angle_rad: float) -> float:
    return (angle_rad + 3.141592653589793) % (2.0 * 3.141592653589793) - 3.141592653589793


@dataclass(frozen=True)
class ProjectileImpactEvent:
    projectile_id: str
    tactical_time_s: float
    target_ship_id: str
    deck_id: str
    deck_level: int
    region_id: str
    edge_index: int
    impact_world_xy: tuple[float, float]
    relative_speed_mps: float
    armor_result: ImpactResult
    armor_durability_before: float
    armor_durability_after: float
    damaged_module_instance_ids: tuple[str, ...]
    hull_integrity_before: float
    hull_integrity_after: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "armor": {
                "available_penetration_mm": self.armor_result.available_penetration_mm,
                "damage_formula_points": self.armor_result.armor_damage_formula_points,
                "durability_after": self.armor_durability_after,
                "durability_before": self.armor_durability_before,
                "impact_angle_deg": self.armor_result.impact_angle_deg,
                "outcome": self.armor_result.outcome.value,
                "required_penetration_mm": self.armor_result.required_penetration_mm,
                "residual_energy_ratio": self.armor_result.residual_energy_ratio,
                "ricochet_probability": self.armor_result.ricochet_probability,
            },
            "damaged_module_instance_ids": list(self.damaged_module_instance_ids),
            "deck_id": self.deck_id,
            "deck_level": self.deck_level,
            "edge_index": self.edge_index,
            "hull_integrity_after": self.hull_integrity_after,
            "hull_integrity_before": self.hull_integrity_before,
            "impact_world_xy": list(self.impact_world_xy),
            "projectile_id": self.projectile_id,
            "region_id": self.region_id,
            "relative_speed_mps": self.relative_speed_mps,
            "tactical_time_s": self.tactical_time_s,
            "target_ship_id": self.target_ship_id,
        }


@dataclass(frozen=True)
class ProjectileExpiredEvent:
    projectile_id: str
    tactical_time_s: float
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"projectile_id": self.projectile_id, "reason": self.reason, "tactical_time_s": self.tactical_time_s}


@dataclass(frozen=True)
class ProjectileWorldAdvanceResolution:
    source_world_sha256: str
    resulting_world: ProjectileWorldState
    resulting_targets: tuple[TacticalProjectileTarget, ...]
    impact_events: tuple[ProjectileImpactEvent, ...]
    expired_events: tuple[ProjectileExpiredEvent, ...]
    guidance_events: tuple[MissileGuidanceEvent, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        result = {
            "expired_events": [item.to_dict() for item in self.expired_events],
            "impact_events": [item.to_dict() for item in self.impact_events],
            "interface": PROJECTILE_WORLD_INTERFACE_ID,
            "policies": {"damage": PROJECTILE_DAMAGE_POLICY_ID, "hit": PROJECTILE_HIT_POLICY_ID, "integration": PROJECTILE_INTEGRATION_POLICY_ID},
            "resulting_target_states": {item.ship_id: item.combat_state.to_dict() for item in self.resulting_targets},
            "resulting_world": self.resulting_world.to_dict(),
            "source_world_sha256": self.source_world_sha256,
        }
        if self.guidance_events:
            result["guidance_events"] = [
                item.to_dict() for item in self.guidance_events
            ]
        return result


@dataclass(frozen=True)
class _GeometryHit:
    fraction: float
    target_ship_id: str
    deck_id: str
    deck_level: int
    region_id: str
    edge_index: int
    edge_start_local: tuple[float, float]
    edge_end_local: tuple[float, float]
    impact_local_xy: tuple[float, float]
    impact_world_xy: tuple[float, float]
    impact_velocity_world_xy: tuple[float, float]
    target_pose: ShipPose2D


def _cross(a: tuple[float, float], b: tuple[float, float]) -> float:
    return a[0] * b[1] - a[1] * b[0]


def _segment_intersection_fraction(
    start: tuple[float, float], end: tuple[float, float],
    edge_start: tuple[float, float], edge_end: tuple[float, float],
) -> float | None:
    r = end[0] - start[0], end[1] - start[1]
    s = edge_end[0] - edge_start[0], edge_end[1] - edge_start[1]
    denominator = _cross(r, s)
    if abs(denominator) <= EPS:
        return None
    offset = edge_start[0] - start[0], edge_start[1] - start[1]
    t = _cross(offset, s) / denominator
    u = _cross(offset, r) / denominator
    if EPS < t <= 1.0 + EPS and -EPS <= u <= 1.0 + EPS:
        return max(0.0, min(1.0, t))
    return None


def _geometry_hit(
    projectile: ProjectileState,
    position_after: tuple[float, float],
    velocity_after: tuple[float, float],
    step_start_s: float,
    step_duration_s: float,
    target: TacticalProjectileTarget,
) -> _GeometryHit | None:
    if target.ship_id != projectile.target_ship_id:
        return None
    deck = next((item for item in target.snapshot.hull.normalized_blueprint.decks if item.level == projectile.selected_target_deck_level), None)
    if deck is None:
        raise ContractError("projectile_world.target_deck_missing", "$.selected_target_deck_level", f"{target.ship_id}:{projectile.selected_target_deck_level}")
    pose_start = target.pose_at(step_start_s)
    pose_end = target.pose_at(step_start_s + step_duration_s)
    local_start = _to_local(projectile.position_xy, pose_start)
    local_end = _to_local(position_after, pose_end)
    candidates: list[tuple[float, Any, int, tuple[float, float], tuple[float, float]]] = []
    for region in deck.regions:
        vertices = region.vertices_m
        for edge_index, (edge_start, edge_end) in enumerate(zip(vertices, vertices[1:] + (vertices[0],))):
            fraction = _segment_intersection_fraction(local_start, local_end, edge_start, edge_end)
            if fraction is not None:
                candidates.append((fraction, region, edge_index, edge_start, edge_end))
    if not candidates:
        return None
    fraction, region, edge_index, edge_start, edge_end = min(candidates, key=lambda item: (item[0], item[1].id, item[2]))
    event_time = step_start_s + step_duration_s * fraction
    pose = target.pose_at(event_time)
    impact_local = (local_start[0] + (local_end[0] - local_start[0]) * fraction, local_start[1] + (local_end[1] - local_start[1]) * fraction)
    impact_world = _to_world(impact_local, pose)
    impact_velocity = (projectile.velocity_xy[0] + (velocity_after[0] - projectile.velocity_xy[0]) * fraction, projectile.velocity_xy[1] + (velocity_after[1] - projectile.velocity_xy[1]) * fraction)
    return _GeometryHit(fraction, target.ship_id, deck.id, deck.level, region.id, edge_index, edge_start, edge_end, impact_local, impact_world, impact_velocity, pose)


def _point_to_segment_distance(point: tuple[float, float], start: tuple[float, float], end: tuple[float, float]) -> float:
    dx, dy = end[0] - start[0], end[1] - start[1]
    length_sq = dx * dx + dy * dy
    if length_sq <= EPS:
        return hypot(point[0] - start[0], point[1] - start[1])
    t = max(0.0, min(1.0, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / length_sq))
    return hypot(point[0] - (start[0] + dx * t), point[1] - (start[1] + dy * t))


def _segment_aabb_entry_fraction(
    start: tuple[float, float],
    end: tuple[float, float],
    minimum: tuple[float, float],
    maximum: tuple[float, float],
) -> float | None:
    """返回线段进入一个五米内部格的最早比例。"""
    direction = end[0] - start[0], end[1] - start[1]
    lower, upper = 0.0, 1.0
    for axis in (0, 1):
        if abs(direction[axis]) <= EPS:
            if start[axis] < minimum[axis] - EPS or start[axis] > maximum[axis] + EPS:
                return None
            continue
        first = (minimum[axis] - start[axis]) / direction[axis]
        second = (maximum[axis] - start[axis]) / direction[axis]
        enter, leave = min(first, second), max(first, second)
        lower = max(lower, enter)
        upper = min(upper, leave)
        if lower > upper + EPS:
            return None
    if upper < -EPS or lower > 1.0 + EPS:
        return None
    return max(0.0, min(1.0, lower))


def _cell_center(cell: tuple[int, int, int]) -> tuple[float, float]:
    return cell[1] * 5.0, cell[2] * 5.0


def _surface_modules(target: TacticalProjectileTarget, hit: _GeometryHit, radius: float) -> tuple[CompiledModuleInstance, ...]:
    slot_map = {
        (slot.deck_id, slot.region_id, slot.edge_index, slot.slot_index): slot
        for deck in target.snapshot.hull.decks
        for slot in deck.side_mount_slots
    }
    result: list[CompiledModuleInstance] = []
    for module in target.snapshot.outfit.instances:
        direct_side = any(
            deck_id == hit.deck_id
            and region_id == hit.region_id
            and edge_index == hit.edge_index
            and _point_to_segment_distance(
                hit.impact_local_xy,
                slot_map[(deck_id, region_id, edge_index, slot_index)].start_m,
                slot_map[(deck_id, region_id, edge_index, slot_index)].end_m,
            )
            <= EPS
            for deck_id, region_id, edge_index, slot_index in module.side_slots
        )
        top_near = any(level == hit.deck_level and hypot(center[0] - hit.impact_local_xy[0], center[1] - hit.impact_local_xy[1]) <= radius + EPS for cell in module.top_cells for level, center in ((cell[0], _cell_center(cell)),))
        if direct_side or top_near:
            result.append(module)
    return tuple(sorted(result, key=lambda item: item.id))


def _internal_modules(
    target: TacticalProjectileTarget,
    hit: _GeometryHit,
    direction_local: tuple[float, float],
    profile: MunitionProjectileProfile,
) -> tuple[CompiledModuleInstance, ...]:
    end = (hit.impact_local_xy[0] + direction_local[0] * profile.damage.internal_effect_range_m, hit.impact_local_xy[1] + direction_local[1] * profile.damage.internal_effect_range_m)
    crossed: list[tuple[float, CompiledModuleInstance, tuple[float, float]]] = []
    for module in target.snapshot.outfit.instances:
        matching = [cell for cell in module.internal_cells if cell[0] == hit.deck_level]
        accepted: list[tuple[float, tuple[float, float]]] = []
        for cell in matching:
            center = _cell_center(cell)
            fraction = _segment_aabb_entry_fraction(
                hit.impact_local_xy,
                end,
                (center[0] - 2.5, center[1] - 2.5),
                (center[0] + 2.5, center[1] + 2.5),
            )
            if fraction is not None:
                accepted.append(
                    (fraction * profile.damage.internal_effect_range_m, center)
                )
        if accepted:
            along, center = min(accepted, key=lambda item: (item[0], item[1]))
            crossed.append((along, module, center))
    crossed.sort(key=lambda item: (item[0], item[1].id))
    if profile.penetration.aftereffect == Aftereffect.KINETIC_RAY:
        return tuple(item[1] for item in crossed)
    if not crossed:
        return ()
    center = crossed[0][2]
    radius = profile.damage.internal_effect_radius_m
    return tuple(item[1] for item in crossed if hypot(item[2][0] - center[0], item[2][1] - center[1]) <= radius + EPS)


def _resolve_hit(
    projectile: ProjectileState,
    hit: _GeometryHit,
    target: TacticalProjectileTarget,
    profile: MunitionProjectileProfile,
    registry: MaterialRegistry,
    event_time: float,
    ricochet_roll: float,
) -> tuple[TacticalProjectileTarget, ProjectileImpactEvent]:
    region = next(item for deck in target.snapshot.hull.normalized_blueprint.decks if deck.id == hit.deck_id for item in deck.regions if item.id == hit.region_id)
    edge_input = region.edge_armor[hit.edge_index]
    material = registry.base_armor(edge_input.material, f"$.targets.{target.ship_id}.armor")
    armor_map = {item.key: item for item in target.combat_state.armor_edges}
    key = hit.deck_id, hit.deck_level, hit.region_id, hit.edge_index
    armor_runtime = armor_map[key]
    offset_world = hit.impact_world_xy[0] - hit.target_pose.position_xy[0], hit.impact_world_xy[1] - hit.target_pose.position_xy[1]
    relative_world = relative_impact_velocity_xy(hit.impact_velocity_world_xy, hit.target_pose.velocity_xy, hit.target_pose.angular_velocity_rad_s, offset_world)
    relative_local = _rotate(relative_world, -hit.target_pose.heading_rad)
    relative_speed = hypot(*relative_local)
    angle = incidence_angle_deg(relative_local, hit.edge_start_local, hit.edge_end_local)
    armor_result = resolve_armor_impact(
        profile.penetration,
        ArmorState(material.protection_coefficient, edge_input.thickness_m * 1000.0, armor_runtime.current_durability_proxy),
        relative_speed,
        angle,
        ricochet_roll=ricochet_roll,
    )
    armor_after = max(0.0, armor_runtime.current_durability_proxy - armor_result.armor_damage_formula_points * profile.damage.armor_damage_to_local_durability_proxy)
    armor_map[key] = replace(armor_runtime, current_durability_proxy=armor_after)
    before_hull = target.combat_state.instance.current_hull_integrity_fraction
    instance = target.combat_state.instance
    damaged_ids: tuple[str, ...] = ()
    if armor_result.outcome == ImpactOutcome.PENETRATED:
        direction = relative_local[0] / relative_speed, relative_local[1] / relative_speed
        modules = _internal_modules(target, hit, direction, profile)
        instance, damaged_ids = apply_module_damage_to_instance(instance, (item.id for item in modules), profile.damage.internal_module_damage_points * armor_result.residual_energy_ratio)
        instance = replace(instance, current_hull_integrity_fraction=max(0.0, before_hull - profile.damage.hull_integrity_damage_fraction * armor_result.residual_energy_ratio))
    else:
        modules = _surface_modules(target, hit, profile.damage.surface_effect_radius_m)
        instance, damaged_ids = apply_module_damage_to_instance(instance, (item.id for item in modules), profile.damage.surface_module_damage_points)
    after_hull = instance.current_hull_integrity_fraction
    resulting_target = replace(target, combat_state=ShipCombatState(instance, tuple(sorted(armor_map.values(), key=lambda item: item.key))))
    event = ProjectileImpactEvent(projectile.id, event_time, target.ship_id, hit.deck_id, hit.deck_level, hit.region_id, hit.edge_index, hit.impact_world_xy, relative_speed, armor_result, armor_runtime.current_durability_proxy, armor_after, damaged_ids, before_hull, after_hull)
    return resulting_target, event


def advance_projectile_world(
    world: ProjectileWorldState,
    catalog: ProjectileProfileCatalog,
    targets: Iterable[TacticalProjectileTarget],
    registry: MaterialRegistry,
    *,
    target_tactical_time_s: float,
    density_kg_m3: float,
    sound_speed_mps: float,
    fixed_step_s: float = 0.01,
    ricochet_rolls: dict[str, float] | None = None,
    guidance_catalog: MissileGuidanceProfileCatalog | None = None,
    guidance_inputs: Iterable[MissileGuidanceRuntimeInput] = (),
) -> ProjectileWorldAdvanceResolution:
    _validate_catalog(world, catalog)
    if target_tactical_time_s + EPS < world.tactical_time_s:
        raise ContractError("projectile_world.time_reversed", "$.target_tactical_time_s", "弹丸世界时间不得倒退")
    if fixed_step_s <= 0.0 or not isfinite(fixed_step_s):
        raise ContractError("projectile_world.fixed_step", "$.fixed_step_s", str(fixed_step_s))
    target_items = tuple(targets)
    target_map = {item.ship_id: item for item in target_items}
    if len(target_map) != len(target_items):
        raise ContractError("projectile_world.target_duplicate", "$.targets", "目标舰 id 不得重复")
    projectiles = {item.id: item for item in world.projectiles}
    guidance_items = tuple(guidance_inputs)
    guidance_input_map = {item.projectile_id: item for item in guidance_items}
    if len(guidance_input_map) != len(guidance_items):
        raise ContractError(
            "missile_guidance.runtime_input_duplicate",
            "$.guidance_inputs",
            "同一弹丸在一个固定步内只能有一份制导事实",
        )
    for index, item in enumerate(guidance_items):
        item.validate(f"$.guidance_inputs[{index}]")
    guided_ids = {
        item.id for item in projectiles.values() if item.guidance_state is not None
    }
    if guided_ids and guidance_catalog is None:
        raise ContractError(
            "missile_guidance.catalog_required",
            "$.guidance_catalog",
            "推进制导弹丸必须提供其绑定的精确制导配置目录",
        )
    missing_guidance_inputs = sorted(guided_ids - set(guidance_input_map))
    if missing_guidance_inputs:
        raise ContractError(
            "missile_guidance.runtime_input_missing",
            "$.guidance_inputs",
            str(missing_guidance_inputs),
        )
    unmatched_guidance_inputs = sorted(set(guidance_input_map) - guided_ids)
    if unmatched_guidance_inputs:
        raise ContractError(
            "missile_guidance.runtime_input_unmatched",
            "$.guidance_inputs",
            str(unmatched_guidance_inputs),
        )
    impacts: list[ProjectileImpactEvent] = []
    expired: list[ProjectileExpiredEvent] = []
    guidance_events: list[MissileGuidanceEvent] = []
    current_time = world.tactical_time_s
    rolls = {} if ricochet_rolls is None else ricochet_rolls
    while current_time + EPS < target_tactical_time_s and projectiles:
        duration = min(fixed_step_s, target_tactical_time_s - current_time)
        updates: dict[str, ProjectileState] = {}
        hit_candidates: list[tuple[float, str, _GeometryHit, tuple[float, float], float]] = []
        for projectile in sorted(projectiles.values(), key=lambda item: item.id):
            profile = catalog.profile(projectile.munition_id)
            remaining_life = profile.maximum_lifetime_s - projectile.age_s
            actual_duration = min(duration, max(0.0, remaining_life))
            if actual_duration <= EPS:
                expired.append(ProjectileExpiredEvent(projectile.id, current_time, "maximum_lifetime"))
                continue
            target = target_map.get(projectile.target_ship_id)
            if target is None:
                raise ContractError("projectile_world.target_missing", "$.targets", projectile.target_ship_id)
            integrated_projectile = projectile
            if projectile.guidance_state is not None:
                assert guidance_catalog is not None
                if target.height_layer is None:
                    raise ContractError(
                        "missile_guidance.target_height_layer_missing",
                        "$.targets",
                        target.ship_id,
                    )
                target_pose = target.pose_at(current_time)
                guidance = advance_missile_guidance_step(
                    projectile.guidance_state,
                    guidance_catalog,
                    guidance_input_map[projectile.id],
                    position_xy=projectile.position_xy,
                    velocity_xy=projectile.velocity_xy,
                    target_position_xy=target_pose.position_xy,
                    target_height_layer=target.height_layer,
                    tactical_time_s=current_time,
                    duration_s=actual_duration,
                )
                guidance_events.extend(guidance.events)
                if guidance.self_destruct:
                    expired.append(
                        ProjectileExpiredEvent(
                            projectile.id,
                            current_time,
                            "guidance_self_destruct",
                        )
                    )
                    continue
                integrated_projectile = replace(
                    projectile,
                    guidance_state=guidance.resulting_state,
                    velocity_xy=guidance.resulting_velocity_xy,
                )
            target_density = (
                density_kg_m3
                if target.density_kg_m3 is None
                else target.density_kg_m3
            )
            target_sound_speed = (
                sound_speed_mps
                if target.sound_speed_mps is None
                else target.sound_speed_mps
            )
            step = integrate_ballistic_step(integrated_projectile.position_xy, integrated_projectile.velocity_xy, profile.ballistic, density_kg_m3=target_density, sound_speed_mps=target_sound_speed, duration_s=actual_duration)
            updated = replace(integrated_projectile, age_s=projectile.age_s + actual_duration, position_xy=step.position_xy, velocity_xy=step.velocity_xy, distance_travelled_m=projectile.distance_travelled_m + step.distance_m)
            hit = _geometry_hit(integrated_projectile, step.position_xy, step.velocity_xy, current_time, actual_duration, target)
            if hit is not None:
                hit_candidates.append((current_time + actual_duration * hit.fraction, projectile.id, hit, step.velocity_xy, step.distance_m))
            elif remaining_life <= duration + EPS:
                expired.append(ProjectileExpiredEvent(projectile.id, current_time + actual_duration, "maximum_lifetime"))
            else:
                updates[projectile.id] = updated
        for event_time, projectile_id, hit, _, _ in sorted(hit_candidates, key=lambda item: (item[0], item[1])):
            projectile = projectiles[projectile_id]
            target = target_map[hit.target_ship_id]
            target, event = _resolve_hit(projectile, hit, target, catalog.profile(projectile.munition_id), registry, event_time, rolls.get(projectile_id, 1.0))
            target_map[target.ship_id] = target
            impacts.append(event)
        projectiles = updates
        current_time += duration
    resulting_world = replace(world, tactical_time_s=target_tactical_time_s, projectiles=tuple(sorted(projectiles.values(), key=lambda item: item.id)))
    return ProjectileWorldAdvanceResolution(canonical_sha256(world), resulting_world, tuple(sorted(target_map.values(), key=lambda item: item.ship_id)), tuple(impacts), tuple(sorted(expired, key=lambda item: (item.tactical_time_s, item.projectile_id))), tuple(sorted(guidance_events, key=lambda item: (item.tactical_time_s, item.projectile_id, item.reason))))
