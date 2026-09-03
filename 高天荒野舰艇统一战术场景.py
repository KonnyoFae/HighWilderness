"""《高天荒野》阶段 I6：统一舰艇机动、武器事件与弹丸世界的战术场景时钟。"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import json
from math import hypot, isfinite
from pathlib import Path
from typing import Any, Iterable

from 高天荒野舰艇推进通道合同 import (
    D1_SCENE_INTERFACE_ID,
    DIRECTIONAL_STATE_INTERFACE_ID,
    DIRECTIONAL_SCENE_INTERFACE_ID,
    DIRECTIONAL_SCENE_POLICY_ID,
)

from 高天荒野舰艇炮弹与甲弹公式 import Aftereffect
from 高天荒野舰艇出航配置编译器 import CompiledSortieState
from 高天荒野舰艇数据契约 import (
    ContractError,
    MaterialRegistry,
    RESOURCE_ID_PATTERN,
    ResourceReference,
    ShipInstanceSnapshotInput,
    canonical_sha256,
)
from 高天荒野舰艇推进状态合同 import (
    C2B_ENGINE_RUNTIME_STATE_INTERFACE_ID,
    C2B_TACTICAL_PROPULSION_STATE_INTERFACE_ID,
    ENGINE_RUNTIME_STATE_INTERFACE_ID,
    EngineRuntimeState,
    PROPULSION_COMMAND_CHANNELS,
    PropulsionGovernorState,
    TACTICAL_PROPULSION_STATE_INTERFACE_ID,
    TacticalPropulsionState,
    migrate_engine_runtime_state_from_module_mode,
    migrate_tactical_propulsion_state_c2b_to_d1,
)
from 高天荒野舰艇无界面舾装编译器 import (
    DerivedShipSnapshot,
    verify_derived_ship_snapshot_fingerprint,
)
from 高天荒野舰艇运行时参数编译器 import (
    RUNTIME_CACHE_VALIDATION_STRICT,
    RUNTIME_CACHE_VALIDATION_TRUSTED,
    RuntimeShipParameters,
    RuntimeShipParametersCache,
)
from 高天荒野舰艇弹药与武器动作结算器 import (
    FIRE_CONTROL_WAKE_EVENT,
    WEAPON_ACTION_WAKE_EVENT,
)
from 高天荒野舰艇武器时间与射击队列 import (
    WEAPON_TIMELINE_ADVANCE_FULL,
    WeaponTimelineEvent,
    WeaponTimelineAdvancePlan,
    WeaponTimingProfileCatalog,
    apply_weapon_timeline_advance_plan,
    advance_weapon_timeline,
    plan_weapon_timeline_advance,
)
from 高天荒野舰艇导弹制导 import (
    MissileGuidanceEvent,
    MissileGuidanceProfileCatalog,
    MissileGuidanceRuntimeInput,
)
from 高天荒野舰艇持续毁伤 import (
    ContinuousDamageEvent,
    ContinuousDamageProfile,
    DamageControlDirective,
    FireIgnitionOutcome,
    advance_continuous_damage,
    apply_damage_control_directives,
    continuous_damage_automatic_events,
    register_fire_ignition,
    validate_instance_continuous_damage,
)
from 高天荒野舰艇人员伤亡 import (
    CrewCasualtyEvent,
    CrewCasualtyOutcome,
    apply_crew_casualty_outcomes,
    validate_instance_crew_casualty_state,
)
from 高天荒野舰艇人员医疗转移与救生 import (
    CrewEvacuationEvent,
    CrewEvacuationOutcome,
    CrewRescueManifest,
    apply_crew_evacuation_outcome,
)
from 高天荒野舰艇二次毁伤 import (
    AmmunitionCookoffEvent,
    AmmunitionCookoffOutcome,
    FirePropagationEvent,
    FirePropagationOutcome,
    apply_secondary_damage_outcomes,
)
from 高天荒野舰艇战术观测与火控 import (
    FireControlSupportEvent,
    GeneratedGuidanceFactEvent,
    RadarEmissionEvent,
    SensorObservationEvent,
    TacticalObservationResolution,
    TacticalObservationShipContext,
    TacticalObservationStepInput,
    generate_guidance_runtime_inputs,
    resolve_tactical_observation_step,
    validate_weapon_fire_control_support,
)
from 高天荒野舰艇战术机动求解器 import (
    LayerTransitionState,
    TacticalControlInput,
    TacticalMotionState,
    TacticalShipModel,
    TacticalShipStaticModel,
    TacticalStepDiagnostics,
    Vec2,
    bind_tactical_ship_model,
    build_tactical_ship_static_model,
    commit_tactical_state_to_instance,
    initialize_tactical_motion_state,
    integrate_tactical_step,
)
from 高天荒野舰艇战术弹丸世界 import (
    ProjectileSpawnInput,
    ProjectileExpiredEvent,
    ProjectileImpactEvent,
    ProjectileProfileCatalog,
    ProjectileSpawnRequest,
    ProjectileState,
    ProjectileTargetGeometry,
    ProjectileWorldState,
    ShipCombatState,
    ShipPose2D,
    TacticalProjectileTarget,
    advance_projectile_world,
    compile_projectile_target_geometry,
    initialize_projectile_world,
    initialize_ship_combat_state,
    spawn_projectiles_from_weapon_events,
)


BINDING_VALIDATION_STRICT = "strict"
BINDING_VALIDATION_TRUSTED = "trusted_prevalidated"
BINDING_VALIDATION_MODES = frozenset(
    {BINDING_VALIDATION_STRICT, BINDING_VALIDATION_TRUSTED}
)


TACTICAL_SCENE_INTERFACE_ID = "gaotian.tactical-scene-timeline/v1alpha1"
TACTICAL_SCENE_POLICY_ID = "gaotian.tactical-scene/boundary-lifecycle-fire-motion-impact/v2"
C2B_TACTICAL_PROPULSION_SCENE_INTERFACE_ID = (
    "gaotian.tactical-scene-timeline/v2alpha1"
)
C2B_TACTICAL_PROPULSION_SCENE_POLICY_ID = (
    "gaotian.tactical-scene/boundary-lifecycle-fire-motion-impact-propulsion-state/v3"
)
TACTICAL_PROPULSION_SCENE_INTERFACE_ID = D1_SCENE_INTERFACE_ID
TACTICAL_PROPULSION_SCENE_POLICY_ID = (
    "gaotian.tactical-scene/boundary-lifecycle-fire-motion-impact-propulsion-state/v4"
)
TACTICAL_ENGAGEMENT_BOUNDARY_SCHEMA_ID = "gaotian.tactical-engagement-boundary/v1alpha1"
TACTICAL_ENGAGEMENT_POLICY_ID = "gaotian.tactical-engagement/responding-ship-layer-pairwise-distance/v1"
PROJECTILE_SUBSTEP_S = 0.005
EPS = 1.0e-8
PHYSICAL_STATUSES = {"operational", "falling", "exited"}
COMMAND_STATUSES = {"scene_command", "local_only", "uncommanded"}
EXIT_REASONS = {"distance_disengaged", "fell_below_scene", "scripted_transfer"}


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


def _vec2(value: Any, path: str) -> Vec2:
    if not isinstance(value, list) or len(value) != 2:
        raise ContractError("type.vector2", path, "必须是两个有限数值")
    return Vec2(_number(value[0], f"{path}[0]"), _number(value[1], f"{path}[1]"))


def _parse_motion_state(value: Any, path: str) -> TacticalMotionState:
    obj = _exact_object(
        value,
        {
            "fixed_step_index",
            "fuel_units",
            "heading_rad",
            "height_layer",
            "hull_integrity_fraction",
            "layer_transition",
            "position_world_m",
            "velocity_world_mps",
            "yaw_rate_radps",
        },
        path,
    )
    transition_value = obj["layer_transition"]
    transition: LayerTransitionState | None
    if transition_value is None:
        transition = None
    else:
        item = _exact_object(
            transition_value,
            {"duration_s", "elapsed_s", "progress", "source_layer", "target_layer"},
            f"{path}.layer_transition",
        )
        transition = LayerTransitionState(
            str(item["source_layer"]),
            str(item["target_layer"]),
            _number(item["elapsed_s"], f"{path}.layer_transition.elapsed_s", 0.0),
            _number(item["duration_s"], f"{path}.layer_transition.duration_s", EPS),
        )
        if transition.source_layer not in {"upper", "cloud", "rain"} or transition.target_layer not in {"upper", "cloud", "rain"}:
            raise ContractError("tactical_scene.height_layer", f"{path}.layer_transition", "换层起止层无效")
        if transition.elapsed_s > transition.duration_s + EPS:
            raise ContractError("tactical_scene.transition_elapsed", f"{path}.layer_transition.elapsed_s", "换层已用时间不得超过总时长")
        if abs(
            _number(item["progress"], f"{path}.layer_transition.progress", 0.0)
            - transition.progress
        ) > EPS:
            raise ContractError(
                "tactical_scene.transition_progress_mismatch",
                f"{path}.layer_transition.progress",
                "换层进度不是由已用时间和总时长派生所得",
            )
    layer = obj["height_layer"]
    if layer not in {"upper", "cloud", "rain"}:
        raise ContractError("tactical_scene.height_layer", f"{path}.height_layer", str(layer))
    hull = _number(obj["hull_integrity_fraction"], f"{path}.hull_integrity_fraction", 0.0)
    if hull > 1.0 + EPS:
        raise ContractError("tactical_scene.hull_integrity", f"{path}.hull_integrity_fraction", "不得超过 1")
    return TacticalMotionState(
        _vec2(obj["position_world_m"], f"{path}.position_world_m"),
        _vec2(obj["velocity_world_mps"], f"{path}.velocity_world_mps"),
        _number(obj["heading_rad"], f"{path}.heading_rad"),
        _number(obj["yaw_rate_radps"], f"{path}.yaw_rate_radps"),
        layer,
        transition,
        hull,
        _number(obj["fuel_units"], f"{path}.fuel_units", 0.0),
        _integer(obj["fixed_step_index"], f"{path}.fixed_step_index"),
    )


@dataclass(frozen=True)
class TacticalEngagementBoundaryProfile:
    id: str
    version: int
    name: str
    fixture_level: str
    layer_distances_m: tuple[tuple[str, float], ...]

    @property
    def reference(self) -> ResourceReference:
        return ResourceReference(self.id, self.version)

    @property
    def source_sha256(self) -> str:
        return canonical_sha256(self)

    def distance_m(self, layer: str) -> float:
        try:
            return dict(self.layer_distances_m)[layer]
        except KeyError as error:
            raise ContractError(
                "tactical_engagement.layer_missing",
                "$.layer_distances_m",
                layer,
            ) from error

    def to_dict(self) -> dict[str, Any]:
        return {
            "fixture_level": self.fixture_level,
            "id": self.id,
            "kind": "TacticalEngagementBoundaryProfile",
            "layer_distances_m": dict(self.layer_distances_m),
            "name": self.name,
            "schema": TACTICAL_ENGAGEMENT_BOUNDARY_SCHEMA_ID,
            "version": self.version,
        }

    @classmethod
    def parse(
        cls,
        value: Any,
        path: str = "$",
    ) -> "TacticalEngagementBoundaryProfile":
        obj = _exact_object(
            value,
            {
                "fixture_level",
                "id",
                "kind",
                "layer_distances_m",
                "name",
                "schema",
                "version",
            },
            path,
        )
        if obj["schema"] != TACTICAL_ENGAGEMENT_BOUNDARY_SCHEMA_ID or obj["kind"] != "TacticalEngagementBoundaryProfile":
            raise ContractError("tactical_engagement.resource", path, "不是战术交战边界配置")
        fixture = obj["fixture_level"]
        if fixture not in {"contract_fixture", "prototype_unbalanced", "balance_reference"}:
            raise ContractError("tactical_engagement.fixture_level", f"{path}.fixture_level", str(fixture))
        distances = _exact_object(
            obj["layer_distances_m"],
            {"upper", "cloud", "rain"},
            f"{path}.layer_distances_m",
        )
        name = obj["name"]
        if not isinstance(name, str) or not name:
            raise ContractError("type.string", f"{path}.name", "名称不得为空")
        return cls(
            _resource_id(obj["id"], f"{path}.id"),
            _integer(obj["version"], f"{path}.version", 1),
            name,
            fixture,
            tuple(
                (layer, _number(distances[layer], f"{path}.layer_distances_m.{layer}", EPS))
                for layer in ("cloud", "rain", "upper")
            ),
        )


def load_tactical_engagement_boundary_profile(
    path: str | Path,
) -> TacticalEngagementBoundaryProfile:
    return TacticalEngagementBoundaryProfile.parse(
        json.loads(Path(path).read_text(encoding="utf-8")),
        str(path),
    )


@dataclass(frozen=True)
class TacticalSceneShipBinding:
    ship_id: str
    snapshot: DerivedShipSnapshot
    sortie: CompiledSortieState
    active_automatic_events: tuple[str, ...] = ()
    side_id: str = "side.neutral"
    fleet_id: str = "fleet.neutral"
    _validated_snapshot_sha256: str | None = field(
        init=False,
        default=None,
        repr=False,
        compare=False,
    )
    _runtime_cache: RuntimeShipParametersCache = field(
        init=False,
        default_factory=RuntimeShipParametersCache,
        repr=False,
        compare=False,
    )
    _static_tactical_model: TacticalShipStaticModel | None = field(
        init=False,
        default=None,
        repr=False,
        compare=False,
    )
    _projectile_target_geometry: ProjectileTargetGeometry | None = field(
        init=False,
        default=None,
        repr=False,
        compare=False,
    )

    @property
    def validated_snapshot_sha256(self) -> str | None:
        return self._validated_snapshot_sha256

    @property
    def runtime_cache(self) -> RuntimeShipParametersCache:
        return self._runtime_cache

    @property
    def static_tactical_model(self) -> TacticalShipStaticModel | None:
        return self._static_tactical_model

    @property
    def projectile_target_geometry(self) -> ProjectileTargetGeometry | None:
        return self._projectile_target_geometry


@dataclass(frozen=True)
class ShipStepContext:
    """只在一个场景固定步内复用的精确舰艇边界视图。"""

    ship_id: str
    boundary_step_index: int
    boundary_tactical_time_s: float
    instance_generation: int
    instance_snapshot: ShipInstanceSnapshotInput
    automatic_events: tuple[str, ...]
    runtime: RuntimeShipParameters
    tactical_model: TacticalShipModel | None = field(
        default=None,
        repr=False,
        compare=False,
    )


@dataclass(frozen=True)
class TacticalEngagementDefinition:
    initiating_side_id: str
    responding_side_id: str


@dataclass(frozen=True)
class TacticalEngagementState:
    initiating_side_id: str
    responding_side_id: str
    boundary_profile: ResourceReference
    boundary_profile_sha256: str
    status: str
    last_evaluated_step_index: int
    closest_cross_side_distance_m: float | None
    qualifying_pair_count: int
    termination_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "boundary_profile": self.boundary_profile.to_dict(),
            "boundary_profile_sha256": self.boundary_profile_sha256,
            "closest_cross_side_distance_m": self.closest_cross_side_distance_m,
            "initiating_side_id": self.initiating_side_id,
            "last_evaluated_step_index": self.last_evaluated_step_index,
            "policy": TACTICAL_ENGAGEMENT_POLICY_ID,
            "qualifying_pair_count": self.qualifying_pair_count,
            "responding_side_id": self.responding_side_id,
            "status": self.status,
            "termination_reason": self.termination_reason,
        }

    @classmethod
    def parse(cls, value: Any, path: str) -> "TacticalEngagementState":
        obj = _exact_object(
            value,
            {
                "boundary_profile",
                "boundary_profile_sha256",
                "closest_cross_side_distance_m",
                "initiating_side_id",
                "last_evaluated_step_index",
                "policy",
                "qualifying_pair_count",
                "responding_side_id",
                "status",
                "termination_reason",
            },
            path,
        )
        if obj["policy"] != TACTICAL_ENGAGEMENT_POLICY_ID:
            raise ContractError("tactical_engagement.policy", f"{path}.policy", str(obj["policy"]))
        status = obj["status"]
        if status not in {"active", "disengaged", "resolved"}:
            raise ContractError("tactical_engagement.status", f"{path}.status", str(status))
        reason = obj["termination_reason"]
        allowed_reasons = {
            None,
            "separation",
            "initiating_side_no_combat_capable_ship",
            "responding_side_no_combat_capable_ship",
            "mutual_no_combat_capable_ship",
        }
        if reason not in allowed_reasons:
            raise ContractError("tactical_engagement.termination_reason", f"{path}.termination_reason", str(reason))
        if (status == "active") != (reason is None):
            raise ContractError("tactical_engagement.status_reason", path, "活动状态不得有终止原因，结束状态必须有终止原因")
        closest_value = obj["closest_cross_side_distance_m"]
        closest = None if closest_value is None else _number(closest_value, f"{path}.closest_cross_side_distance_m", 0.0)
        initiating = _resource_id(obj["initiating_side_id"], f"{path}.initiating_side_id")
        responding = _resource_id(obj["responding_side_id"], f"{path}.responding_side_id")
        if initiating == responding:
            raise ContractError("tactical_engagement.same_side", path, "主动接战方与被动应战方不得相同")
        return cls(
            initiating,
            responding,
            ResourceReference.parse(obj["boundary_profile"], f"{path}.boundary_profile"),
            _sha256(obj["boundary_profile_sha256"], f"{path}.boundary_profile_sha256"),
            status,
            _integer(obj["last_evaluated_step_index"], f"{path}.last_evaluated_step_index"),
            closest,
            _integer(obj["qualifying_pair_count"], f"{path}.qualifying_pair_count"),
            reason,
        )


@dataclass(frozen=True)
class TacticalEngagementEvent:
    tactical_time_s: float
    previous_status: str
    resulting_state: TacticalEngagementState

    def to_dict(self) -> dict[str, Any]:
        return {
            "previous_status": self.previous_status,
            "resulting_state": self.resulting_state.to_dict(),
            "tactical_time_s": self.tactical_time_s,
        }


@dataclass(frozen=True)
class TacticalShipLifecycleState:
    physical_status: str
    command_status: str
    failure_causes: tuple[str, ...]
    last_transition_step_index: int
    exit_reason: str | None = None
    exit_tactical_time_s: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "command_status": self.command_status,
            "exit_reason": self.exit_reason,
            "exit_tactical_time_s": self.exit_tactical_time_s,
            "failure_causes": list(self.failure_causes),
            "last_transition_step_index": self.last_transition_step_index,
            "physical_status": self.physical_status,
        }

    @classmethod
    def parse(cls, value: Any, path: str) -> "TacticalShipLifecycleState":
        obj = _exact_object(
            value,
            {
                "command_status",
                "exit_reason",
                "exit_tactical_time_s",
                "failure_causes",
                "last_transition_step_index",
                "physical_status",
            },
            path,
        )
        physical = obj["physical_status"]
        command = obj["command_status"]
        if physical not in PHYSICAL_STATUSES:
            raise ContractError("tactical_scene.physical_status", f"{path}.physical_status", str(physical))
        if command not in COMMAND_STATUSES:
            raise ContractError("tactical_scene.command_status", f"{path}.command_status", str(command))
        causes_value = obj["failure_causes"]
        if not isinstance(causes_value, list) or any(not isinstance(item, str) or not item for item in causes_value):
            raise ContractError("tactical_scene.failure_causes", f"{path}.failure_causes", "必须是非空字符串数组")
        causes = tuple(sorted(set(causes_value)))
        if causes != tuple(causes_value):
            raise ContractError("tactical_scene.failure_causes", f"{path}.failure_causes", "故障原因必须排序且不得重复")
        reason = obj["exit_reason"]
        if reason is not None and reason not in EXIT_REASONS:
            raise ContractError("tactical_scene.exit_reason", f"{path}.exit_reason", str(reason))
        exit_time = obj["exit_tactical_time_s"]
        parsed_exit_time = None if exit_time is None else _number(exit_time, f"{path}.exit_tactical_time_s", 0.0)
        if physical == "exited":
            if command != "uncommanded" or reason is None or parsed_exit_time is None:
                raise ContractError("tactical_scene.exited_state", path, "离场舰必须无场景指挥权并保存离场原因/时刻")
        elif reason is not None or parsed_exit_time is not None:
            raise ContractError("tactical_scene.exit_state", path, "未离场舰不得保存离场原因或时刻")
        if physical == "falling" and command != "uncommanded":
            raise ContractError("tactical_scene.falling_command", path, "失控坠落舰不得继续接受指令")
        return cls(
            physical,
            command,
            causes,
            _integer(obj["last_transition_step_index"], f"{path}.last_transition_step_index"),
            reason,
            parsed_exit_time,
        )


@dataclass(frozen=True)
class TacticalSceneShipState:
    ship_id: str
    side_id: str
    fleet_id: str
    derived_snapshot_sha256: str
    sortie_configuration_sha256: str
    combat_state: ShipCombatState
    motion_state: TacticalMotionState
    lifecycle_state: TacticalShipLifecycleState
    propulsion_state: TacticalPropulsionState | None = None

    def to_dict(self) -> dict[str, Any]:
        result = {
            "combat_state": self.combat_state.to_dict(),
            "derived_snapshot_sha256": self.derived_snapshot_sha256,
            "fleet_id": self.fleet_id,
            "lifecycle_state": self.lifecycle_state.to_dict(),
            "motion_state": self.motion_state.to_dict(),
            "ship_id": self.ship_id,
            "side_id": self.side_id,
            "sortie_configuration_sha256": self.sortie_configuration_sha256,
        }
        if self.propulsion_state is not None:
            result["propulsion_state"] = self.propulsion_state.to_dict()
        return result

    @classmethod
    def parse(
        cls,
        value: Any,
        path: str,
        *,
        propulsion_interface_id: str | None = None,
    ) -> "TacticalSceneShipState":
        keys = {
            "ship_id",
            "side_id",
            "fleet_id",
            "derived_snapshot_sha256",
            "sortie_configuration_sha256",
            "combat_state",
            "lifecycle_state",
            "motion_state",
        }
        if propulsion_interface_id is not None:
            keys.add("propulsion_state")
        obj = _exact_object(
            value,
            keys,
            path,
        )
        result = cls(
            _resource_id(obj["ship_id"], f"{path}.ship_id"),
            _resource_id(obj["side_id"], f"{path}.side_id"),
            _resource_id(obj["fleet_id"], f"{path}.fleet_id"),
            _sha256(obj["derived_snapshot_sha256"], f"{path}.derived_snapshot_sha256"),
            _sha256(obj["sortie_configuration_sha256"], f"{path}.sortie_configuration_sha256"),
            ShipCombatState.parse(obj["combat_state"], f"{path}.combat_state"),
            _parse_motion_state(obj["motion_state"], f"{path}.motion_state"),
            TacticalShipLifecycleState.parse(obj["lifecycle_state"], f"{path}.lifecycle_state"),
            (
                TacticalPropulsionState.parse(
                    obj["propulsion_state"],
                    f"{path}.propulsion_state",
                )
                if propulsion_interface_id is not None
                else None
            ),
        )
        if (
            result.propulsion_state is not None
            and result.propulsion_state.interface_id != propulsion_interface_id
        ):
            raise ContractError(
                "tactical_scene.propulsion_state_interface",
                f"{path}.propulsion_state.interface",
                "场景与逐舰推进状态 interface 不匹配",
            )
        return result


@dataclass(frozen=True)
class TacticalSceneState:
    fixed_step_s: float
    fixed_step_index: int
    tactical_time_s: float
    projectile_world: ProjectileWorldState
    ships: tuple[TacticalSceneShipState, ...]
    engagement_state: TacticalEngagementState | None = None
    propulsion_safety_profile: ResourceReference | None = None
    propulsion_safety_profile_sha256: str | None = None

    def to_dict(self) -> dict[str, Any]:
        has_propulsion = self.propulsion_safety_profile is not None
        propulsion_interfaces = {
            ship.propulsion_state.interface_id
            for ship in self.ships
            if ship.propulsion_state is not None
        }
        if not has_propulsion and propulsion_interfaces:
            raise ValueError("旧场景不得携带逐舰推进状态")
        if not has_propulsion:
            scene_interface = TACTICAL_SCENE_INTERFACE_ID
            scene_policy = TACTICAL_SCENE_POLICY_ID
        elif propulsion_interfaces == {
            C2B_TACTICAL_PROPULSION_STATE_INTERFACE_ID
        }:
            scene_interface = C2B_TACTICAL_PROPULSION_SCENE_INTERFACE_ID
            scene_policy = C2B_TACTICAL_PROPULSION_SCENE_POLICY_ID
        elif propulsion_interfaces == {TACTICAL_PROPULSION_STATE_INTERFACE_ID}:
            scene_interface = TACTICAL_PROPULSION_SCENE_INTERFACE_ID
            scene_policy = TACTICAL_PROPULSION_SCENE_POLICY_ID
        elif propulsion_interfaces == {DIRECTIONAL_STATE_INTERFACE_ID}:
            scene_interface = DIRECTIONAL_SCENE_INTERFACE_ID
            scene_policy = DIRECTIONAL_SCENE_POLICY_ID
        else:
            raise ValueError("场景推进状态 interface 缺失或混用")
        result = {
            "fixed_step_index": self.fixed_step_index,
            "fixed_step_s": self.fixed_step_s,
            "engagement_state": (
                None
                if self.engagement_state is None
                else self.engagement_state.to_dict()
            ),
            "interface": scene_interface,
            "policy": scene_policy,
            "projectile_world": self.projectile_world.to_dict(),
            "ships": [item.to_dict() for item in self.ships],
            "tactical_time_s": self.tactical_time_s,
        }
        if has_propulsion:
            assert self.propulsion_safety_profile is not None
            result["propulsion_safety_profile"] = (
                self.propulsion_safety_profile.to_dict()
            )
            result["propulsion_safety_profile_sha256"] = (
                self.propulsion_safety_profile_sha256
            )
        return result

    @classmethod
    def parse(cls, value: Any, path: str = "$") -> "TacticalSceneState":
        if not isinstance(value, dict):
            raise ContractError("object.keys", path, "场景必须是对象")
        interface = value.get("interface")
        if interface == TACTICAL_SCENE_INTERFACE_ID:
            propulsion_interface_id = None
            expected_policy = TACTICAL_SCENE_POLICY_ID
        elif interface == C2B_TACTICAL_PROPULSION_SCENE_INTERFACE_ID:
            propulsion_interface_id = C2B_TACTICAL_PROPULSION_STATE_INTERFACE_ID
            expected_policy = C2B_TACTICAL_PROPULSION_SCENE_POLICY_ID
        elif interface == TACTICAL_PROPULSION_SCENE_INTERFACE_ID:
            propulsion_interface_id = TACTICAL_PROPULSION_STATE_INTERFACE_ID
            expected_policy = TACTICAL_PROPULSION_SCENE_POLICY_ID
        elif interface == DIRECTIONAL_SCENE_INTERFACE_ID:
            propulsion_interface_id = DIRECTIONAL_STATE_INTERFACE_ID
            expected_policy = DIRECTIONAL_SCENE_POLICY_ID
        else:
            raise ContractError(
                "tactical_scene.interface",
                f"{path}.interface",
                str(interface),
            )
        keys = {
            "fixed_step_index",
            "fixed_step_s",
            "engagement_state",
            "interface",
            "policy",
            "projectile_world",
            "ships",
            "tactical_time_s",
        }
        if propulsion_interface_id is not None:
            keys.update(
                {
                    "propulsion_safety_profile",
                    "propulsion_safety_profile_sha256",
                }
            )
        obj = _exact_object(
            value,
            keys,
            path,
        )
        if obj["policy"] != expected_policy:
            raise ContractError("tactical_scene.interface", path, "不是当前统一战术场景合同")
        if not isinstance(obj["ships"], list) or not obj["ships"]:
            raise ContractError("tactical_scene.ships", f"{path}.ships", "场景必须至少包含一艘舰艇")
        ships = tuple(
            sorted(
                (
                    TacticalSceneShipState.parse(
                        item,
                        f"{path}.ships[{index}]",
                        propulsion_interface_id=propulsion_interface_id,
                    )
                    for index, item in enumerate(obj["ships"])
                ),
                key=lambda item: item.ship_id,
            )
        )
        if len({item.ship_id for item in ships}) != len(ships):
            raise ContractError("tactical_scene.ship_duplicate", f"{path}.ships", "舰艇 id 不得重复")
        state = cls(
            _number(obj["fixed_step_s"], f"{path}.fixed_step_s", EPS),
            _integer(obj["fixed_step_index"], f"{path}.fixed_step_index"),
            _number(obj["tactical_time_s"], f"{path}.tactical_time_s", 0.0),
            ProjectileWorldState.parse(obj["projectile_world"], f"{path}.projectile_world"),
            ships,
            (
                None
                if obj["engagement_state"] is None
                else TacticalEngagementState.parse(
                    obj["engagement_state"],
                    f"{path}.engagement_state",
                )
            ),
            (
                ResourceReference.parse(
                    obj["propulsion_safety_profile"],
                    f"{path}.propulsion_safety_profile",
                )
                if propulsion_interface_id is not None
                else None
            ),
            (
                _sha256(
                    obj["propulsion_safety_profile_sha256"],
                    f"{path}.propulsion_safety_profile_sha256",
                )
                if propulsion_interface_id is not None
                else None
            ),
        )
        _validate_internal_state(state)
        return state


@dataclass(frozen=True)
class TacticalSceneLaunchDirective:
    source_ship_id: str
    sequence_id: str
    tactical_time_s: float
    projectile_id: str
    target_ship_id: str
    selected_target_deck_level: int
    launch_direction_local_xy: tuple[float, float]


@dataclass(frozen=True)
class TacticalSceneExitDirective:
    ship_id: str
    tactical_time_s: float
    reason: str


@dataclass(frozen=True)
class TacticalSceneWeaponEvent:
    ship_id: str
    event: WeaponTimelineEvent

    def to_dict(self) -> dict[str, Any]:
        return {"event": self.event.to_dict(), "ship_id": self.ship_id}


@dataclass(frozen=True)
class TacticalShipLifecycleEvent:
    ship_id: str
    tactical_time_s: float
    previous_state: TacticalShipLifecycleState
    resulting_state: TacticalShipLifecycleState

    def to_dict(self) -> dict[str, Any]:
        return {
            "previous_state": self.previous_state.to_dict(),
            "resulting_state": self.resulting_state.to_dict(),
            "ship_id": self.ship_id,
            "tactical_time_s": self.tactical_time_s,
        }


@dataclass(frozen=True)
class TacticalSceneShipStepResult:
    ship_id: str
    diagnostics: TacticalStepDiagnostics | None
    resulting_runtime: RuntimeShipParameters

    def to_dict(self) -> dict[str, Any]:
        return {
            "diagnostics": (
                None
                if self.diagnostics is None
                else {
                    "command_scale": self.diagnostics.command_scale,
                    "crew_g": self.diagnostics.crew_g,
                    "fuel_units_consumed": self.diagnostics.fuel_units_consumed,
                    "hull_integrity_damage": self.diagnostics.hull_integrity_damage,
                    "structure_ratio": self.diagnostics.structure_ratio,
                }
            ),
            "resulting_runtime_parameters_sha256": self.resulting_runtime.source_sha256,
            "ship_id": self.ship_id,
        }


@dataclass(frozen=True)
class TacticalSceneStepResolution:
    source_scene_sha256: str
    resulting_scene: TacticalSceneState
    weapon_events: tuple[TacticalSceneWeaponEvent, ...]
    spawned_projectiles: tuple[ProjectileState, ...]
    impact_events: tuple[ProjectileImpactEvent, ...]
    expired_events: tuple[ProjectileExpiredEvent, ...]
    lifecycle_events: tuple[TacticalShipLifecycleEvent, ...]
    engagement_events: tuple[TacticalEngagementEvent, ...]
    ship_results: tuple[TacticalSceneShipStepResult, ...]
    guidance_events: tuple[MissileGuidanceEvent, ...] = ()
    continuous_damage_events: tuple[ContinuousDamageEvent, ...] = ()
    crew_casualty_events: tuple[CrewCasualtyEvent, ...] = ()
    crew_evacuation_events: tuple[CrewEvacuationEvent, ...] = ()
    crew_rescue_manifests: tuple[CrewRescueManifest, ...] = ()
    fire_propagation_events: tuple[FirePropagationEvent, ...] = ()
    ammunition_cookoff_events: tuple[AmmunitionCookoffEvent, ...] = ()
    sensor_observation_events: tuple[SensorObservationEvent, ...] = ()
    radar_emission_events: tuple[RadarEmissionEvent, ...] = ()
    fire_control_support_events: tuple[FireControlSupportEvent, ...] = ()
    generated_guidance_fact_events: tuple[GeneratedGuidanceFactEvent, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        result = {
            "engagement_events": [item.to_dict() for item in self.engagement_events],
            "expired_events": [item.to_dict() for item in self.expired_events],
            "impact_events": [item.to_dict() for item in self.impact_events],
            "interface": TACTICAL_SCENE_INTERFACE_ID,
            "lifecycle_events": [item.to_dict() for item in self.lifecycle_events],
            "policy": TACTICAL_SCENE_POLICY_ID,
            "resulting_scene_sha256": canonical_sha256(self.resulting_scene),
            "ship_results": [item.to_dict() for item in self.ship_results],
            "source_scene_sha256": self.source_scene_sha256,
            "spawned_projectiles": [item.to_dict() for item in self.spawned_projectiles],
            "weapon_events": [item.to_dict() for item in self.weapon_events],
        }
        if self.guidance_events:
            result["guidance_events"] = [
                item.to_dict() for item in self.guidance_events
            ]
        if self.continuous_damage_events:
            result["continuous_damage_events"] = [
                item.to_dict() for item in self.continuous_damage_events
            ]
        if self.crew_casualty_events:
            result["crew_casualty_events"] = [
                item.to_dict() for item in self.crew_casualty_events
            ]
        if self.crew_evacuation_events:
            result["crew_evacuation_events"] = [
                item.to_dict() for item in self.crew_evacuation_events
            ]
        if self.crew_rescue_manifests:
            result["crew_rescue_manifests"] = [
                item.to_dict() for item in self.crew_rescue_manifests
            ]
        if self.fire_propagation_events:
            result["fire_propagation_events"] = [
                item.to_dict() for item in self.fire_propagation_events
            ]
        if self.ammunition_cookoff_events:
            result["ammunition_cookoff_events"] = [
                item.to_dict() for item in self.ammunition_cookoff_events
            ]
        if self.sensor_observation_events:
            result["sensor_observation_events"] = [
                item.to_dict() for item in self.sensor_observation_events
            ]
        if self.radar_emission_events:
            result["radar_emission_events"] = [
                item.to_dict() for item in self.radar_emission_events
            ]
        if self.fire_control_support_events:
            result["fire_control_support_events"] = [
                item.to_dict() for item in self.fire_control_support_events
            ]
        if self.generated_guidance_fact_events:
            result["generated_guidance_fact_events"] = [
                item.to_dict() for item in self.generated_guidance_fact_events
            ]
        return result


def _pose(time_s: float, motion: TacticalMotionState) -> ShipPose2D:
    return ShipPose2D(
        time_s,
        (motion.position_world_m.x, motion.position_world_m.y),
        motion.heading_rad,
        (motion.velocity_world_mps.x, motion.velocity_world_mps.y),
        motion.yaw_rate_radps,
    )


def _on_grid(time_s: float, fixed_step_s: float) -> bool:
    nearest = round(time_s / fixed_step_s)
    return abs(time_s - nearest * fixed_step_s) <= EPS


@dataclass(frozen=True)
class TacticalShipLifecycleProjection:
    """不含步号的生命周期语义投影，可安全比较同一步边界结果。"""

    physical_status: str
    command_status: str
    failure_causes: tuple[str, ...]
    exit_reason: str | None = None
    exit_tactical_time_s: float | None = None


def _lifecycle_projection_from_state(
    state: TacticalShipLifecycleState,
) -> TacticalShipLifecycleProjection:
    return TacticalShipLifecycleProjection(
        state.physical_status,
        state.command_status,
        state.failure_causes,
        state.exit_reason,
        state.exit_tactical_time_s,
    )


def project_tactical_ship_lifecycle(
    runtime: RuntimeShipParameters,
    sortie: CompiledSortieState,
    *,
    previous: TacticalShipLifecycleState | None = None,
) -> TacticalShipLifecycleProjection:
    """只计算生命周期语义；不决定转移发生在哪个固定步。"""

    if previous is not None and previous.physical_status == "exited":
        return _lifecycle_projection_from_state(previous)
    causes: set[str] = set()
    if runtime.current_hull_integrity_fraction <= EPS:
        causes.add("hull_structure_collapsed")
    cic = next((item for item in runtime.modules if item.category == "cic"), None)
    if cic is None or cic.condition == "destroyed":
        causes.add("cic_destroyed")
    if "insufficient_lift" in runtime.terminal_failures:
        causes.add("insufficient_lift")
    falling = bool(causes) or (
        previous is not None and previous.physical_status == "falling"
    )
    if falling:
        physical_status = "falling"
        command_status = "uncommanded"
        if previous is not None and previous.physical_status == "falling":
            causes.update(previous.failure_causes)
    else:
        physical_status = "operational"
        if not runtime.cic_control_available:
            command_status = "uncommanded"
            causes.add("cic_control_unavailable")
        elif sortie.configuration.control_mode == "remote_core":
            if runtime.remote_control_available:
                command_status = "scene_command"
            elif runtime.crew_safety_lock_enabled:
                command_status = "local_only"
                causes.add("remote_control_lost")
            else:
                command_status = "uncommanded"
                causes.add("remote_control_lost")
        else:
            command_status = "scene_command"
    return TacticalShipLifecycleProjection(
        physical_status,
        command_status,
        tuple(sorted(causes)),
    )


def _materialize_tactical_ship_lifecycle(
    projection: TacticalShipLifecycleProjection,
    *,
    step_index: int,
    previous: TacticalShipLifecycleState | None = None,
) -> TacticalShipLifecycleState:
    if previous is not None and previous.physical_status == "exited":
        return previous
    candidate = TacticalShipLifecycleState(
        projection.physical_status,
        projection.command_status,
        projection.failure_causes,
        step_index,
        projection.exit_reason,
        projection.exit_tactical_time_s,
    )
    if previous is not None and (
        _lifecycle_projection_from_state(previous) == projection
    ):
        return replace(
            candidate,
            last_transition_step_index=previous.last_transition_step_index,
        )
    return candidate


def derive_tactical_ship_lifecycle(
    runtime: RuntimeShipParameters,
    sortie: CompiledSortieState,
    *,
    step_index: int,
    previous: TacticalShipLifecycleState | None = None,
) -> TacticalShipLifecycleState:
    """由物理损伤与实际控制能力派生舰艇生命周期，不伪造坠落耗时。"""

    projection = project_tactical_ship_lifecycle(
        runtime,
        sortie,
        previous=previous,
    )
    return _materialize_tactical_ship_lifecycle(
        projection,
        step_index=step_index,
        previous=previous,
    )


def evaluate_tactical_engagement(
    ships: Iterable[TacticalSceneShipState],
    profile: TacticalEngagementBoundaryProfile,
    definition: TacticalEngagementDefinition,
    *,
    step_index: int,
    previous: TacticalEngagementState | None = None,
) -> TacticalEngagementState:
    initiating_side_id = _resource_id(
        definition.initiating_side_id,
        "$.engagement.initiating_side_id",
    )
    responding_side_id = _resource_id(
        definition.responding_side_id,
        "$.engagement.responding_side_id",
    )
    if initiating_side_id == responding_side_id:
        raise ContractError(
            "tactical_engagement.same_side",
            "$.engagement",
            "主动接战方与被动应战方不得相同",
        )
    if previous is not None:
        if (
            previous.initiating_side_id != initiating_side_id
            or previous.responding_side_id != responding_side_id
        ):
            raise ContractError(
                "tactical_engagement.definition_mismatch",
                "$.engagement",
                "活动交战不能更换主动或被动方",
            )
        if (
            previous.boundary_profile != profile.reference
            or previous.boundary_profile_sha256 != profile.source_sha256
        ):
            raise ContractError(
                "tactical_engagement.profile_mismatch",
                "$.engagement.boundary_profile",
                "活动交战必须继续使用创建时的精确边界配置",
            )
        if previous.status != "active":
            return previous
    ship_items = tuple(ships)
    sides = {item.side_id for item in ship_items}
    if sides - {initiating_side_id, responding_side_id}:
        raise ContractError(
            "tactical_engagement.third_side_unsupported",
            "$.ships.side_id",
            "首版自动边界只接受主动方与被动方两个阵营",
        )
    initiating = tuple(
        item
        for item in ship_items
        if item.side_id == initiating_side_id
        and item.lifecycle_state.physical_status == "operational"
    )
    responding = tuple(
        item
        for item in ship_items
        if item.side_id == responding_side_id
        and item.lifecycle_state.physical_status == "operational"
    )
    closest: float | None = None
    qualifying = 0
    for attacker in initiating:
        for responder in responding:
            dx = attacker.motion_state.position_world_m.x - responder.motion_state.position_world_m.x
            dy = attacker.motion_state.position_world_m.y - responder.motion_state.position_world_m.y
            distance = hypot(dx, dy)
            closest = distance if closest is None else min(closest, distance)
            if distance <= profile.distance_m(responder.motion_state.height_layer) + EPS:
                qualifying += 1
    if not initiating and not responding:
        status, reason = "resolved", "mutual_no_combat_capable_ship"
    elif not initiating:
        status, reason = "resolved", "initiating_side_no_combat_capable_ship"
    elif not responding:
        status, reason = "resolved", "responding_side_no_combat_capable_ship"
    elif qualifying == 0:
        status, reason = "disengaged", "separation"
    else:
        status, reason = "active", None
    return TacticalEngagementState(
        initiating_side_id,
        responding_side_id,
        profile.reference,
        profile.source_sha256,
        status,
        step_index,
        closest,
        qualifying,
        reason,
    )


def _suspend_weapon_timeline(
    instance: ShipInstanceSnapshotInput,
    tactical_time_s: float,
) -> ShipInstanceSnapshotInput:
    timeline = instance.weapon_timeline_state
    assert timeline is not None
    return replace(
        instance,
        weapon_timeline_state=replace(
            timeline,
            tactical_time_s=tactical_time_s,
            sequences=(),
        ),
    )


def _runtime_automatic_events(
    binding: TacticalSceneShipBinding,
    instance: ShipInstanceSnapshotInput,
    extra_events: Iterable[str] = (),
) -> tuple[str, ...]:
    return tuple(
        sorted(
            set(binding.active_automatic_events)
            | set(continuous_damage_automatic_events(instance))
            | set(extra_events)
        )
    )


def _resolve_binding_runtime(
    binding: TacticalSceneShipBinding,
    instance: ShipInstanceSnapshotInput,
    extra_events: Iterable[str] = (),
    *,
    validation_mode: str = RUNTIME_CACHE_VALIDATION_TRUSTED,
) -> RuntimeShipParameters:
    events = _runtime_automatic_events(binding, instance, extra_events)
    return binding.runtime_cache.resolve(
        binding.snapshot,
        binding.sortie,
        instance,
        active_automatic_events=events,
        validation_mode=validation_mode,
    ).runtime


def _binding_static_model(
    binding: TacticalSceneShipBinding,
) -> TacticalShipStaticModel:
    static = binding.static_tactical_model
    if (
        static is None
        or static.derived_snapshot_sha256 != binding.snapshot.source_sha256
    ):
        static = build_tactical_ship_static_model(binding.snapshot)
        object.__setattr__(binding, "_static_tactical_model", static)
    return static


def _binding_projectile_target_geometry(
    binding: TacticalSceneShipBinding,
) -> ProjectileTargetGeometry:
    geometry = binding.projectile_target_geometry
    if (
        geometry is None
        or geometry.snapshot_sha256 != binding.snapshot.source_sha256
    ):
        geometry = compile_projectile_target_geometry(binding.snapshot)
        object.__setattr__(binding, "_projectile_target_geometry", geometry)
    return geometry


def _binding_tactical_model(
    binding: TacticalSceneShipBinding,
    runtime: RuntimeShipParameters,
) -> TacticalShipModel:
    return bind_tactical_ship_model(runtime, _binding_static_model(binding))


def _prewarm_binding_runtime_variants(
    binding: TacticalSceneShipBinding,
    instance: ShipInstanceSnapshotInput,
    *,
    validation_mode: str = RUNTIME_CACHE_VALIDATION_STRICT,
) -> None:
    timeline = instance.weapon_timeline_state
    if timeline is None or not timeline.sequences:
        return
    _resolve_binding_runtime(
        binding,
        instance,
        (WEAPON_ACTION_WAKE_EVENT,),
        validation_mode=validation_mode,
    )
    _resolve_binding_runtime(
        binding,
        instance,
        (WEAPON_ACTION_WAKE_EVENT, FIRE_CONTROL_WAKE_EVENT),
        validation_mode=validation_mode,
    )


def _lifecycle_event(
    ship_id: str,
    tactical_time_s: float,
    previous: TacticalShipLifecycleState,
    resulting: TacticalShipLifecycleState,
) -> TacticalShipLifecycleEvent | None:
    return (
        None
        if previous == resulting
        else TacticalShipLifecycleEvent(
            ship_id,
            tactical_time_s,
            previous,
            resulting,
        )
    )


def _validate_pending_event_grid(instance: ShipInstanceSnapshotInput, fixed_step_s: float, ship_id: str) -> None:
    timeline = instance.weapon_timeline_state
    assert timeline is not None
    for sequence in timeline.sequences:
        if not _on_grid(sequence.next_event_time_s, fixed_step_s):
            raise ContractError(
                "tactical_scene.weapon_event_off_grid",
                f"$.ships.{ship_id}.weapon_timeline_state.sequences.{sequence.id}.next_event_time_s",
                "生成战术实体的武器事件必须落在 60Hz 场景固定步边界；不得隐式取整",
            )


def _validate_internal_state(state: TacticalSceneState) -> None:
    expected_time = state.fixed_step_index * state.fixed_step_s
    if abs(state.tactical_time_s - expected_time) > EPS:
        raise ContractError("tactical_scene.clock_mismatch", "$.tactical_time_s", "场景时刻必须由固定步号唯一派生")
    if abs(state.projectile_world.tactical_time_s - state.tactical_time_s) > EPS:
        raise ContractError("tactical_scene.projectile_clock_mismatch", "$.projectile_world.tactical_time_s", "弹丸世界与场景时钟不同步")
    has_propulsion_profile = state.propulsion_safety_profile is not None
    if has_propulsion_profile != (
        state.propulsion_safety_profile_sha256 is not None
    ):
        raise ContractError(
            "tactical_scene.propulsion_profile_partial",
            "$.propulsion_safety_profile",
            "推进安全配置引用与内容指纹必须同时存在或同时省略",
        )
    if state.propulsion_safety_profile_sha256 is not None:
        _sha256(
            state.propulsion_safety_profile_sha256,
            "$.propulsion_safety_profile_sha256",
        )
    if any(
        (ship.propulsion_state is not None) != has_propulsion_profile
        for ship in state.ships
    ):
        raise ContractError(
            "tactical_scene.propulsion_state_partial",
            "$.ships",
            "新场景每艘舰都必须携带推进状态，旧场景则全部不得携带",
        )
    propulsion_interfaces = {
        ship.propulsion_state.interface_id
        for ship in state.ships
        if ship.propulsion_state is not None
    }
    if has_propulsion_profile and len(propulsion_interfaces) != 1:
        raise ContractError(
            "tactical_scene.propulsion_state_interface_mixed",
            "$.ships",
            "同一场景不得混用 c2b 与 d1 推进状态 interface",
        )
    engagement = state.engagement_state
    if engagement is not None:
        if engagement.last_evaluated_step_index != state.fixed_step_index:
            raise ContractError("tactical_engagement.clock_mismatch", "$.engagement_state.last_evaluated_step_index", "交战边界必须在当前场景步完成评估")
        if engagement.status == "active" and engagement.qualifying_pair_count < 1:
            raise ContractError("tactical_engagement.active_without_contact", "$.engagement_state", "活动交战必须至少有一组仍在边界内的敌对舰")
        if engagement.status != "active" and engagement.termination_reason is None:
            raise ContractError("tactical_engagement.termination_reason", "$.engagement_state", "已结束交战必须保存终止原因")
    for ship in state.ships:
        motion = ship.motion_state
        instance = ship.combat_state.instance
        timeline = instance.weapon_timeline_state
        lifecycle = ship.lifecycle_state
        continuous_damage = instance.continuous_damage_state
        crew_casualty = instance.crew_casualty_state
        propulsion = ship.propulsion_state
        if propulsion is not None:
            for engine in propulsion.engines:
                if (
                    engine.phase in {"ready", "running", "stopping"}
                    and engine.ready_at_fixed_step is not None
                    and engine.ready_at_fixed_step > state.fixed_step_index
                ):
                    raise ContractError(
                        "tactical_scene.propulsion_ready_clock_ahead",
                        f"$.ships.{ship.ship_id}.propulsion_state.engines.{engine.actuator_instance_id}",
                        "已就绪执行器的就绪步不得超前场景",
                    )
                if (
                    engine.phase == "starting"
                    and engine.ready_at_fixed_step is not None
                    and engine.ready_at_fixed_step <= state.fixed_step_index
                ):
                    raise ContractError(
                        "tactical_scene.propulsion_start_due",
                        f"$.ships.{ship.ship_id}.propulsion_state.engines.{engine.actuator_instance_id}",
                        "已到启动完成边界的状态不得继续保存为 starting",
                    )
                if (
                    engine.next_transition_step is not None
                    and engine.next_transition_step <= state.fixed_step_index
                ):
                    raise ContractError(
                        "tactical_scene.propulsion_transition_due",
                        f"$.ships.{ship.ship_id}.propulsion_state.engines.{engine.actuator_instance_id}",
                        "待转换步必须位于当前已提交场景之后",
                    )
            for governor in propulsion.governors:
                for value, field_name in (
                    (
                        governor.safety_limited_since_step,
                        "safety_limited_since_step",
                    ),
                    (
                        governor.release_candidate_since_step,
                        "release_candidate_since_step",
                    ),
                    (
                        governor.last_evaluated_step_index,
                        "last_evaluated_step_index",
                    ),
                ):
                    if value is not None and value > state.fixed_step_index:
                        raise ContractError(
                            "tactical_scene.propulsion_governor_clock_ahead",
                            f"$.ships.{ship.ship_id}.propulsion_state.governors.{governor.command_channel}.{field_name}",
                            "governor 步号不得超前场景",
                        )
        if motion.fixed_step_index != state.fixed_step_index:
            raise ContractError("tactical_scene.motion_clock_mismatch", f"$.ships.{ship.ship_id}.motion_state.fixed_step_index", "舰艇机动步号与场景不同步")
        if timeline is None or abs(timeline.tactical_time_s - state.tactical_time_s) > EPS:
            raise ContractError("tactical_scene.weapon_clock_mismatch", f"$.ships.{ship.ship_id}.combat_state.instance.weapon_timeline_state", "舰艇武器时钟与场景不同步")
        if continuous_damage is not None:
            if lifecycle.physical_status == "exited":
                if continuous_damage.tactical_time_s > state.tactical_time_s + EPS:
                    raise ContractError("tactical_scene.continuous_damage_clock_ahead", f"$.ships.{ship.ship_id}.combat_state.instance.continuous_damage_state", "离场舰持续毁伤时钟不得超前场景")
            elif abs(continuous_damage.tactical_time_s - state.tactical_time_s) > EPS:
                raise ContractError("tactical_scene.continuous_damage_clock_mismatch", f"$.ships.{ship.ship_id}.combat_state.instance.continuous_damage_state", "活动舰持续毁伤时钟必须与场景同步")
        validate_instance_crew_casualty_state(instance)
        if crew_casualty is not None:
            if lifecycle.physical_status == "exited":
                if crew_casualty.tactical_time_s > state.tactical_time_s + EPS:
                    raise ContractError("tactical_scene.crew_casualty_clock_ahead", f"$.ships.{ship.ship_id}.combat_state.instance.crew_casualty_state", "离场舰人员账本时钟不得超前场景")
            elif abs(crew_casualty.tactical_time_s - state.tactical_time_s) > EPS:
                raise ContractError("tactical_scene.crew_casualty_clock_mismatch", f"$.ships.{ship.ship_id}.combat_state.instance.crew_casualty_state", "活动舰人员账本时钟必须与场景同步")
        if abs(motion.hull_integrity_fraction - instance.current_hull_integrity_fraction) > EPS:
            raise ContractError("tactical_scene.hull_state_mismatch", f"$.ships.{ship.ship_id}", "机动与战损状态的船壳完整度不同步")
        if abs(motion.fuel_units - instance.operational_state.fuel_units) > EPS:
            raise ContractError("tactical_scene.fuel_state_mismatch", f"$.ships.{ship.ship_id}", "机动与实例燃料不同步")
        if motion.height_layer != instance.operational_state.height_layer:
            raise ContractError("tactical_scene.height_state_mismatch", f"$.ships.{ship.ship_id}", "机动与实例高度层不同步")
        if lifecycle.last_transition_step_index > state.fixed_step_index:
            raise ContractError("tactical_scene.lifecycle_clock_ahead", f"$.ships.{ship.ship_id}.lifecycle_state", "生命周期转换步号不得超前场景")
        if tuple(sorted(set(lifecycle.failure_causes))) != lifecycle.failure_causes:
            raise ContractError("tactical_scene.failure_causes", f"$.ships.{ship.ship_id}.lifecycle_state.failure_causes", "故障原因必须排序且不得重复")
        if lifecycle.physical_status == "falling" and lifecycle.command_status != "uncommanded":
            raise ContractError("tactical_scene.falling_command", f"$.ships.{ship.ship_id}.lifecycle_state", "失控坠落舰不得继续接受指令")
        if lifecycle.physical_status == "exited":
            if lifecycle.command_status != "uncommanded" or lifecycle.exit_reason not in EXIT_REASONS or lifecycle.exit_tactical_time_s is None or lifecycle.exit_tactical_time_s > state.tactical_time_s + EPS:
                raise ContractError("tactical_scene.exited_state", f"$.ships.{ship.ship_id}.lifecycle_state", "离场状态缺少合法原因、时刻或无指挥状态")
        elif lifecycle.exit_reason is not None or lifecycle.exit_tactical_time_s is not None:
            raise ContractError("tactical_scene.exit_state", f"$.ships.{ship.ship_id}.lifecycle_state", "未离场舰不得保存离场信息")
        if lifecycle.physical_status in {"falling", "exited"} and timeline.sequences:
            raise ContractError("tactical_scene.terminal_weapon_sequence", f"$.ships.{ship.ship_id}.combat_state.instance.weapon_timeline_state.sequences", "坠落或离场舰不得保留活动武器序列")


def _binding_map(bindings: Iterable[TacticalSceneShipBinding]) -> dict[str, TacticalSceneShipBinding]:
    items = tuple(bindings)
    result = {item.ship_id: item for item in items}
    if len(result) != len(items):
        raise ContractError("tactical_scene.binding_duplicate", "$.bindings", "舰艇绑定 id 不得重复")
    for item in items:
        _resource_id(item.ship_id, "$.bindings.ship_id")
        _resource_id(item.side_id, "$.bindings.side_id")
        _resource_id(item.fleet_id, "$.bindings.fleet_id")
    return result


def _validate_binding_snapshot_fingerprints(
    bindings: dict[str, TacticalSceneShipBinding],
) -> None:
    """对进入权威场景的每个静态快照实例只做一次完整校验。"""

    verified: dict[int, str] = {}
    for ship_id, binding in bindings.items():
        identity = id(binding.snapshot)
        if identity not in verified:
            verify_derived_ship_snapshot_fingerprint(
                binding.snapshot,
                path=f"$.bindings.{ship_id}.snapshot",
            )
            verified[identity] = binding.snapshot.source_sha256
        object.__setattr__(
            binding,
            "_validated_snapshot_sha256",
            verified[identity],
        )


def _validate_bindings(
    state: TacticalSceneState,
    bindings: dict[str, TacticalSceneShipBinding],
    validation_mode: str,
) -> None:
    if validation_mode not in BINDING_VALIDATION_MODES:
        raise ContractError(
            "tactical_scene.binding_validation_mode",
            "$.binding_validation_mode",
            validation_mode,
        )
    if validation_mode == BINDING_VALIDATION_STRICT:
        _validate_binding_snapshot_fingerprints(bindings)
    ships = {item.ship_id: item for item in state.ships}
    if set(ships) != set(bindings):
        raise ContractError("tactical_scene.binding_set_mismatch", "$.bindings", "绑定舰艇集合必须与场景精确一致")
    for ship_id, binding in bindings.items():
        ship = ships[ship_id]
        if binding.validated_snapshot_sha256 is None:
            raise ContractError(
                "tactical_scene.binding_not_validated",
                f"$.bindings.{ship_id}.snapshot",
                "快速路径只能使用已经严格校验的场景绑定",
            )
        if binding.validated_snapshot_sha256 != binding.snapshot.source_sha256:
            raise ContractError(
                "tactical_scene.binding_token_stale",
                f"$.bindings.{ship_id}.snapshot",
                "场景绑定的已验证快照 token 已失效",
            )
        if ship.side_id != binding.side_id or ship.fleet_id != binding.fleet_id:
            raise ContractError("tactical_scene.affiliation_mismatch", f"$.bindings.{ship_id}", "舰艇阵营或舰队归属已经变化")
        if ship.derived_snapshot_sha256 != binding.snapshot.source_sha256:
            raise ContractError("tactical_scene.snapshot_mismatch", f"$.bindings.{ship_id}.snapshot", "派生快照内容指纹已经变化")
        if ship.sortie_configuration_sha256 != binding.sortie.source_sha256:
            raise ContractError("tactical_scene.sortie_mismatch", f"$.bindings.{ship_id}.sortie", "出航配置内容指纹已经变化")
        expected_edges = initialize_ship_combat_state(
            binding.snapshot,
            ship.combat_state.instance,
        ).armor_edges
        expected_maximum = {item.key: item.maximum_durability_proxy for item in expected_edges}
        actual_maximum = {item.key: item.maximum_durability_proxy for item in ship.combat_state.armor_edges}
        if actual_maximum != expected_maximum:
            raise ContractError("tactical_scene.armor_state_mismatch", f"$.ships.{ship_id}.combat_state.armor_edges", "局部装甲状态必须与精确船壳的全部边一一对应")


@dataclass(frozen=True)
class TacticalSceneV1ToPropulsionV2Migration:
    migration_id: str
    source_scene_sha256: str


KNOWN_TACTICAL_SCENE_V1_TO_PROPULSION_V2_MIGRATIONS = (
    TacticalSceneV1ToPropulsionV2Migration(
        "functional_6.guided_projectiles",
        "71ebacb179fbaaa09ccf5af0570ba0dbdb5c38726cb732a87497cc0d3f7a10c9",
    ),
    TacticalSceneV1ToPropulsionV2Migration(
        "functional_6.motion_only",
        "69c4c1d79952501d86475f71becad8285df25114f304bb434a8205c9aaacf708",
    ),
    TacticalSceneV1ToPropulsionV2Migration(
        "functional_6.ordinary_projectiles",
        "df7b06bbfad95b5f917436a61222bb1643f96ec2d671545c64033192f08e75de",
    ),
    TacticalSceneV1ToPropulsionV2Migration(
        "functional_6.scripted_damage_and_recompile",
        "f8d36945b39a2a6a87a06bd47c05b0604edeb2bb29f1aa2bdbc2f0d00f4000ec",
    ),
    TacticalSceneV1ToPropulsionV2Migration(
        "stress_30.guided_projectiles",
        "1b106fbe36b8709c86118a88f1c1ff909adb6693fd2aa2f28c7e3420a61a5010",
    ),
    TacticalSceneV1ToPropulsionV2Migration(
        "stress_30.motion_only",
        "df26a513ba5013271c06fc33b0eb9dd3866cea7f4b2ec10364b21147ab7dc998",
    ),
    TacticalSceneV1ToPropulsionV2Migration(
        "stress_30.ordinary_projectiles",
        "65a6b13baa08b0883ad35d2397f6c72f16be095adf248714a188425c577d8f6b",
    ),
    TacticalSceneV1ToPropulsionV2Migration(
        "stress_30.scripted_damage_and_recompile",
        "e0dc8aea10da978d1db38c1a399e104e63e873945abf303a02bb32fc5641d295",
    ),
    TacticalSceneV1ToPropulsionV2Migration(
        "target_20.guided_projectiles",
        "2d4f1997dd8d6329d3ad3c9cb80a6773ccaaf52fb55b0320829b205ba2888cf4",
    ),
    TacticalSceneV1ToPropulsionV2Migration(
        "target_20.motion_only",
        "b0d1d8fc5bf24a0ce9113e1c510013aa9b9782bae4a0b0feaf592957cd3ff4df",
    ),
    TacticalSceneV1ToPropulsionV2Migration(
        "target_20.ordinary_projectiles",
        "e120b60071e02cb43a97e4c11e34b6ee4eaf212f6dcd70027aca8de6656674f8",
    ),
    TacticalSceneV1ToPropulsionV2Migration(
        "target_20.scripted_damage_and_recompile",
        "fce5f11e841dde12b9b0777526455ab710692718ff3d7dcbfecf365203d91f40",
    ),
)
T0_PROPULSION_SAFETY_PROFILE_REFERENCE = ResourceReference(
    "gtw.propulsion_safety.fixture.t0",
    1,
)
T0_PROPULSION_SAFETY_PROFILE_SHA256 = (
    "a67f71517c39243d54dc01bf84f3981ca73e59551fc85787ce8da7e336ea5d6f"
)


def validate_tactical_scene_propulsion_profile(
    state: TacticalSceneState,
    profile: Any,
) -> None:
    if state.propulsion_safety_profile is None:
        raise ContractError(
            "tactical_scene.propulsion_profile_unexpected",
            "$.propulsion_safety_profile",
            "旧场景没有推进安全配置引用",
        )
    if (
        state.propulsion_safety_profile != profile.reference
        or state.propulsion_safety_profile_sha256 != profile.source_sha256
    ):
        raise ContractError(
            "tactical_scene.propulsion_profile_mismatch",
            "$.propulsion_safety_profile",
            "推进安全配置引用或内容指纹不匹配",
        )


def migrate_known_tactical_scene_v1_to_propulsion_v2(
    migration_id: str,
    state: TacticalSceneState,
    bindings: Iterable[TacticalSceneShipBinding],
    profile: Any,
) -> TacticalSceneState:
    """只迁移具名、指纹锁定的旧 T0 初始场景，不推进任何权威时间。"""

    if (
        state.propulsion_safety_profile is not None
        or any(ship.propulsion_state is not None for ship in state.ships)
    ):
        raise ContractError(
            "tactical_scene.propulsion_migration_source_interface",
            "$.interface",
            "推进场景不得再次执行 v1→v2 迁移",
        )
    specification = next(
        (
            item
            for item in KNOWN_TACTICAL_SCENE_V1_TO_PROPULSION_V2_MIGRATIONS
            if item.migration_id == migration_id
        ),
        None,
    )
    if specification is None:
        raise ContractError(
            "tactical_scene.propulsion_migration_unknown",
            "$.migration_id",
            migration_id,
        )
    source_sha256 = canonical_sha256(state)
    if source_sha256 != specification.source_scene_sha256:
        raise ContractError(
            "tactical_scene.propulsion_migration_source_hash",
            "$.scene",
            f"{migration_id} 的旧场景内容指纹不匹配",
        )
    if (
        profile.reference != T0_PROPULSION_SAFETY_PROFILE_REFERENCE
        or profile.source_sha256 != T0_PROPULSION_SAFETY_PROFILE_SHA256
    ):
        raise ContractError(
            "tactical_scene.propulsion_migration_profile",
            "$.propulsion_safety_profile",
            "具名 T0 场景迁移必须绑定冻结的兼容推进安全配置",
        )
    binding_by_id = _binding_map(bindings)
    _validate_bindings(state, binding_by_id, BINDING_VALIDATION_STRICT)
    migrated_ships: list[TacticalSceneShipState] = []
    for ship in state.ships:
        binding = binding_by_id[ship.ship_id]
        module_state_by_id = {
            item.instance_id: item
            for item in ship.combat_state.instance.module_states
        }
        engines: list[EngineRuntimeState] = []
        for actuator in sorted(
            binding.snapshot.outfit.actuators,
            key=lambda item: item.instance_id,
        ):
            module_state = module_state_by_id.get(actuator.instance_id)
            if module_state is None:
                raise ContractError(
                    "tactical_scene.propulsion_migration_actuator_state_missing",
                    f"$.ships.{ship.ship_id}.combat_state.instance.module_states",
                    actuator.instance_id,
                )
            engines.append(
                migrate_engine_runtime_state_from_module_mode(
                    actuator.instance_id,
                    actuator.category,
                    module_state.operating_mode,
                    state.fixed_step_index,
                    interface_id=C2B_ENGINE_RUNTIME_STATE_INTERFACE_ID,
                )
            )
        propulsion = TacticalPropulsionState(
            tuple(engines),
            tuple(
                PropulsionGovernorState.initial(channel)
                for channel in PROPULSION_COMMAND_CHANNELS
            ),
            C2B_TACTICAL_PROPULSION_STATE_INTERFACE_ID,
        )
        migrated_ships.append(replace(ship, propulsion_state=propulsion))
    migrated = replace(
        state,
        ships=tuple(migrated_ships),
        propulsion_safety_profile=profile.reference,
        propulsion_safety_profile_sha256=profile.source_sha256,
    )
    _validate_internal_state(migrated)
    validate_tactical_scene_propulsion_profile(migrated, profile)
    return migrated


@dataclass(frozen=True)
class TacticalScenePropulsionV2ToD1V3Migration:
    migration_id: str
    source_scene_sha256: str


KNOWN_TACTICAL_SCENE_PROPULSION_V2_TO_D1_V3_MIGRATIONS: tuple[
    TacticalScenePropulsionV2ToD1V3Migration, ...
] = (
    TacticalScenePropulsionV2ToD1V3Migration(
        "functional_6.guided_projectiles",
        "e08e72039b409fc96fa9e85a7fa3fcecfa81871b46498903f8ebb0d87979ee82",
    ),
    TacticalScenePropulsionV2ToD1V3Migration(
        "functional_6.motion_only",
        "3c01237be110ebd5514c6f97abb612f88a96584fbfbc5d887b8c3d352435b4ad",
    ),
    TacticalScenePropulsionV2ToD1V3Migration(
        "functional_6.ordinary_projectiles",
        "b916839b55b9edead1fae90f155ae917098eceb12b958267deb817666aad841a",
    ),
    TacticalScenePropulsionV2ToD1V3Migration(
        "functional_6.scripted_damage_and_recompile",
        "b99ef863534f8b29dd1deaeeaddb8a1fd916db9a4a9f2e3c4267b18de45f8951",
    ),
    TacticalScenePropulsionV2ToD1V3Migration(
        "stress_30.guided_projectiles",
        "a5c4127d80d6b94e100f3116ad0eace09692c2344ab1a0bf24e6e9d28e82be7b",
    ),
    TacticalScenePropulsionV2ToD1V3Migration(
        "stress_30.motion_only",
        "4ab7b9031b01867085f5a8b5da7c1eca2727beb6b20e53bbe8ec1792255bc005",
    ),
    TacticalScenePropulsionV2ToD1V3Migration(
        "stress_30.ordinary_projectiles",
        "c7471c74fdf4dbb80fb279f577cbb1b4e75e23daef1b799586404d15f3e51e3e",
    ),
    TacticalScenePropulsionV2ToD1V3Migration(
        "stress_30.scripted_damage_and_recompile",
        "d47e239f93f6ca99cedd85c0d6f4a1415c83513b13fbde808b717bc801869fc0",
    ),
    TacticalScenePropulsionV2ToD1V3Migration(
        "target_20.guided_projectiles",
        "09a050f7314a417ee60b01b9615af6e749bc7df5c62a58e92dd0c0093beaa391",
    ),
    TacticalScenePropulsionV2ToD1V3Migration(
        "target_20.motion_only",
        "635807fe0960e6c62a197d0d44fb93126a329a1cc55cf0369eca037650852970",
    ),
    TacticalScenePropulsionV2ToD1V3Migration(
        "target_20.ordinary_projectiles",
        "e712408c962558ca8229b998935f171a2eb46635e9b9305cadd1f33c478e36de",
    ),
    TacticalScenePropulsionV2ToD1V3Migration(
        "target_20.scripted_damage_and_recompile",
        "16c074608e7cecd671379d18043dab7ca81bbf2ce80b49adcf5b4d062ade9a8f",
    ),
)


def migrate_known_tactical_scene_propulsion_v2_to_d1_v3(
    migration_id: str,
    state: TacticalSceneState,
) -> TacticalSceneState:
    """把具名、指纹锁定的 c2b 推进场景升级到 d1 状态 interface。"""

    propulsion_states = tuple(
        ship.propulsion_state for ship in state.ships if ship.propulsion_state is not None
    )
    if (
        state.propulsion_safety_profile is None
        or len(propulsion_states) != len(state.ships)
        or any(
            item.interface_id != C2B_TACTICAL_PROPULSION_STATE_INTERFACE_ID
            for item in propulsion_states
        )
    ):
        raise ContractError(
            "tactical_scene.d1_migration_source_interface",
            "$.interface",
            "只接受完整的 c2b propulsion v2 场景",
        )
    specification = next(
        (
            item
            for item in KNOWN_TACTICAL_SCENE_PROPULSION_V2_TO_D1_V3_MIGRATIONS
            if item.migration_id == migration_id
        ),
        None,
    )
    if specification is None:
        raise ContractError(
            "tactical_scene.d1_migration_unknown",
            "$.migration_id",
            migration_id,
        )
    if canonical_sha256(state) != specification.source_scene_sha256:
        raise ContractError(
            "tactical_scene.d1_migration_source_hash",
            "$.scene",
            f"{migration_id} 的 c2b 场景内容指纹不匹配",
        )
    migrated = replace(
        state,
        ships=tuple(
            replace(
                ship,
                propulsion_state=migrate_tactical_propulsion_state_c2b_to_d1(
                    ship.propulsion_state
                ),
            )
            for ship in state.ships
            if ship.propulsion_state is not None
        ),
    )
    _validate_internal_state(migrated)
    return migrated


def initialize_tactical_scene(
    bindings: Iterable[TacticalSceneShipBinding],
    projectile_catalog: ProjectileProfileCatalog,
    timing_catalog: WeaponTimingProfileCatalog,
    *,
    initial_motion_states: dict[str, TacticalMotionState] | None = None,
    initial_combat_states: dict[str, ShipCombatState] | None = None,
    engagement_definition: TacticalEngagementDefinition | None = None,
    engagement_boundary_profile: TacticalEngagementBoundaryProfile | None = None,
    continuous_damage_profile: ContinuousDamageProfile | None = None,
) -> TacticalSceneState:
    binding_by_id = _binding_map(bindings)
    if not binding_by_id:
        raise ContractError("tactical_scene.ships", "$.bindings", "场景必须至少包含一艘舰艇")
    _validate_binding_snapshot_fingerprints(binding_by_id)
    supplied_motion = {} if initial_motion_states is None else initial_motion_states
    supplied_combat = {} if initial_combat_states is None else initial_combat_states
    if set(supplied_motion) - set(binding_by_id) or set(supplied_combat) - set(binding_by_id):
        raise ContractError("tactical_scene.initial_state_unknown_ship", "$", "初态引用了未绑定舰艇")
    ships: list[TacticalSceneShipState] = []
    fixed_step_s: float | None = None
    for ship_id, binding in sorted(binding_by_id.items()):
        binding.runtime_cache.clear()
        combat = supplied_combat.get(ship_id)
        if combat is None:
            raise ContractError("tactical_scene.combat_state_required", f"$.initial_combat_states.{ship_id}", "统一场景必须显式提供已初始化武器时钟的战损状态")
        timeline = combat.instance.weapon_timeline_state
        if timeline is None or abs(timeline.tactical_time_s) > EPS:
            raise ContractError("tactical_scene.weapon_clock_mismatch", f"$.initial_combat_states.{ship_id}", "初始武器时钟必须已在零时刻初始化")
        if timeline.timing_profile_catalog != timing_catalog.reference or timeline.timing_profile_catalog_sha256 != timing_catalog.source_sha256:
            raise ContractError("tactical_scene.timing_catalog_mismatch", f"$.initial_combat_states.{ship_id}", "武器时钟绑定了其他时间配置")
        continuous_damage = combat.instance.continuous_damage_state
        if continuous_damage is not None:
            if continuous_damage_profile is None:
                raise ContractError("continuous_damage.profile_required", f"$.initial_combat_states.{ship_id}", "带持续毁伤状态的舰艇必须提供精确配置")
            validate_instance_continuous_damage(binding.snapshot, combat.instance, continuous_damage_profile)
            if abs(continuous_damage.tactical_time_s) > EPS:
                raise ContractError("tactical_scene.continuous_damage_clock_mismatch", f"$.initial_combat_states.{ship_id}", "初始持续毁伤时钟必须位于零时刻")
        validate_instance_crew_casualty_state(combat.instance)
        crew_casualty = combat.instance.crew_casualty_state
        if crew_casualty is not None and abs(crew_casualty.tactical_time_s) > EPS:
            raise ContractError("tactical_scene.crew_casualty_clock_mismatch", f"$.initial_combat_states.{ship_id}", "初始人员账本时钟必须位于零时刻")
        runtime = _resolve_binding_runtime(
            binding,
            combat.instance,
            validation_mode=RUNTIME_CACHE_VALIDATION_STRICT,
        )
        _prewarm_binding_runtime_variants(
            binding,
            combat.instance,
            validation_mode=RUNTIME_CACHE_VALIDATION_STRICT,
        )
        model = _binding_tactical_model(binding, runtime)
        if fixed_step_s is None:
            fixed_step_s = model.tuning.fixed_step_s
        elif abs(fixed_step_s - model.tuning.fixed_step_s) > EPS:
            raise ContractError("tactical_scene.fixed_step_mismatch", "$.bindings", "同一场景舰艇必须采用同一固定步")
        motion = supplied_motion.get(ship_id, initialize_tactical_motion_state(model))
        motion = replace(
            motion,
            hull_integrity_fraction=combat.instance.current_hull_integrity_fraction,
            fuel_units=combat.instance.operational_state.fuel_units,
            fixed_step_index=0,
        )
        lifecycle = derive_tactical_ship_lifecycle(
            runtime,
            binding.sortie,
            step_index=0,
        )
        if lifecycle.physical_status == "falling":
            combat = replace(
                combat,
                instance=_suspend_weapon_timeline(combat.instance, 0.0),
            )
        ships.append(
            TacticalSceneShipState(
                ship_id,
                binding.side_id,
                binding.fleet_id,
                binding.snapshot.source_sha256,
                binding.sortie.source_sha256,
                combat,
                motion,
                lifecycle,
            )
        )
    assert fixed_step_s is not None
    if (engagement_definition is None) != (engagement_boundary_profile is None):
        raise ContractError("tactical_engagement.initialization", "$.engagement", "交战定义与边界配置必须同时提供或同时省略")
    engagement = (
        None
        if engagement_definition is None or engagement_boundary_profile is None
        else evaluate_tactical_engagement(
            ships,
            engagement_boundary_profile,
            engagement_definition,
            step_index=0,
        )
    )
    if engagement is not None and engagement.status != "active":
        raise ContractError(
            "tactical_engagement.not_in_contact",
            "$.engagement",
            "创建战术场景时必须至少有一组敌对舰处于被动应战方层级对应的边界内",
        )
    state = TacticalSceneState(
        fixed_step_s,
        0,
        0.0,
        initialize_projectile_world(projectile_catalog),
        tuple(ships),
        engagement,
    )
    for ship in state.ships:
        _validate_pending_event_grid(ship.combat_state.instance, fixed_step_s, ship.ship_id)
    _validate_internal_state(state)
    return state


def _directive_key(directive: TacticalSceneLaunchDirective, fixed_step_s: float) -> tuple[str, str, int]:
    _resource_id(directive.source_ship_id, "$.launch_directives.source_ship_id")
    _resource_id(directive.sequence_id, "$.launch_directives.sequence_id")
    _resource_id(directive.projectile_id, "$.launch_directives.projectile_id")
    _resource_id(directive.target_ship_id, "$.launch_directives.target_ship_id")
    tactical_time_s = _number(directive.tactical_time_s, "$.launch_directives.tactical_time_s", 0.0)
    if not _on_grid(tactical_time_s, fixed_step_s):
        raise ContractError("tactical_scene.launch_directive_off_grid", "$.launch_directives.tactical_time_s", "发射指令必须指向固定步边界")
    _integer(directive.selected_target_deck_level, "$.launch_directives.selected_target_deck_level")
    if not isinstance(directive.launch_direction_local_xy, tuple) or len(directive.launch_direction_local_xy) != 2:
        raise ContractError("type.vector2", "$.launch_directives.launch_direction_local_xy", "必须是两个有限数值的元组")
    _number(directive.launch_direction_local_xy[0], "$.launch_directives.launch_direction_local_xy[0]")
    _number(directive.launch_direction_local_xy[1], "$.launch_directives.launch_direction_local_xy[1]")
    return directive.source_ship_id, directive.sequence_id, round(tactical_time_s / fixed_step_s)


def _exit_directive_key(
    directive: TacticalSceneExitDirective,
    fixed_step_s: float,
) -> tuple[str, int]:
    ship_id = _resource_id(directive.ship_id, "$.exit_directives.ship_id")
    tactical_time_s = _number(
        directive.tactical_time_s,
        "$.exit_directives.tactical_time_s",
        0.0,
    )
    if not _on_grid(tactical_time_s, fixed_step_s):
        raise ContractError(
            "tactical_scene.exit_directive_off_grid",
            "$.exit_directives.tactical_time_s",
            "场景退出必须发生在固定步边界",
        )
    if directive.reason not in EXIT_REASONS:
        raise ContractError(
            "tactical_scene.exit_reason",
            "$.exit_directives.reason",
            directive.reason,
        )
    return ship_id, round(tactical_time_s / fixed_step_s)


def advance_tactical_scene_step(
    state: TacticalSceneState,
    bindings: Iterable[TacticalSceneShipBinding],
    timing_catalog: WeaponTimingProfileCatalog,
    projectile_catalog: ProjectileProfileCatalog,
    material_registry: MaterialRegistry,
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
    controls: dict[str, TacticalControlInput] | None = None,
    launch_directives: Iterable[TacticalSceneLaunchDirective] = (),
    exit_directives: Iterable[TacticalSceneExitDirective] = (),
    engagement_boundary_profile: TacticalEngagementBoundaryProfile | None = None,
    ricochet_rolls: dict[str, float] | None = None,
    binding_validation_mode: str = BINDING_VALIDATION_STRICT,
) -> TacticalSceneStepResolution:
    _validate_internal_state(state)
    if state.propulsion_safety_profile is not None:
        raise ContractError(
            "tactical_scene.propulsion_unwired",
            "$.interface",
            "推进场景合同已迁移，但时间内核与力学接线留待 T0b.2d1/d2",
        )
    if state.engagement_state is not None:
        if state.engagement_state.status != "active":
            raise ContractError("tactical_engagement.closed", "$.engagement_state.status", "已经脱离或分出结果的战术场景不能继续推进")
        if engagement_boundary_profile is None:
            raise ContractError("tactical_engagement.profile_required", "$.engagement_boundary_profile", "推进自动交战边界场景必须提供原精确配置")
        if (
            state.engagement_state.boundary_profile != engagement_boundary_profile.reference
            or state.engagement_state.boundary_profile_sha256 != engagement_boundary_profile.source_sha256
        ):
            raise ContractError("tactical_engagement.profile_mismatch", "$.engagement_boundary_profile", "交战边界配置引用或内容指纹不匹配")
    elif engagement_boundary_profile is not None:
        raise ContractError("tactical_engagement.profile_unexpected", "$.engagement_boundary_profile", "沙盒场景没有自动交战边界")
    binding_by_id = _binding_map(bindings)
    _validate_bindings(state, binding_by_id, binding_validation_mode)
    runtime_validation_mode = (
        RUNTIME_CACHE_VALIDATION_STRICT
        if binding_validation_mode == BINDING_VALIDATION_STRICT
        else RUNTIME_CACHE_VALIDATION_TRUSTED
    )
    ship_map = {item.ship_id: item for item in state.ships}
    manual_guidance_inputs = tuple(guidance_inputs)
    if observation_step_input is not None:
        observation_step_input.validate("$.observation_step_input")
        if manual_guidance_inputs:
            raise ContractError(
                "tactical_observation.manual_guidance_mixed",
                "$.guidance_inputs",
                "自动观测模式不得同时接收调用方手填的制导布尔事实",
            )
        observation_events_by_ship = observation_step_input.automatic_events_by_ship()
        unknown_observation_ships = sorted(set(observation_events_by_ship) - set(ship_map))
        if unknown_observation_ships:
            raise ContractError(
                "tactical_observation.ship_missing",
                "$.observation_step_input",
                str(unknown_observation_ships),
            )
    else:
        observation_events_by_ship = {}
    source_fire_incidents_by_ship = {
        ship_id: (
            ()
            if ship.combat_state.instance.continuous_damage_state is None
            else ship.combat_state.instance.continuous_damage_state.fire_incidents
        )
        for ship_id, ship in ship_map.items()
    }
    ignition_items = tuple(fire_ignition_outcomes)
    for index, item in enumerate(ignition_items):
        item.validate(f"$.fire_ignition_outcomes[{index}]")
        if item.target_ship_id not in ship_map:
            raise ContractError("continuous_damage.ignition_ship_missing", f"$.fire_ignition_outcomes[{index}].target_ship_id", item.target_ship_id)
    if len({item.incident_id for item in ignition_items}) != len(ignition_items):
        raise ContractError("continuous_damage.ignition_incident_duplicate", "$.fire_ignition_outcomes", "同一固定步的火灾事件 id 不得重复")
    damage_directive_items = tuple(damage_control_directives)
    for index, item in enumerate(damage_directive_items):
        item.validate(f"$.damage_control_directives[{index}]")
        if item.ship_id not in ship_map:
            raise ContractError("continuous_damage.directive_ship_missing", f"$.damage_control_directives[{index}].ship_id", item.ship_id)
    if len({item.slot for item in damage_directive_items}) != len(damage_directive_items):
        raise ContractError("continuous_damage.directive_slot_duplicate", "$.damage_control_directives", "同一固定步不得重复修改同一损管队槽位")
    propagation_items = tuple(fire_propagation_outcomes)
    for index, item in enumerate(propagation_items):
        item.validate(f"$.fire_propagation_outcomes[{index}]")
        if item.target_ship_id not in ship_map:
            raise ContractError("secondary_damage.ship_missing", f"$.fire_propagation_outcomes[{index}].target_ship_id", item.target_ship_id)
    cookoff_items = tuple(ammunition_cookoff_outcomes)
    for index, item in enumerate(cookoff_items):
        item.validate(f"$.ammunition_cookoff_outcomes[{index}]")
        if item.target_ship_id not in ship_map:
            raise ContractError("secondary_damage.ship_missing", f"$.ammunition_cookoff_outcomes[{index}].target_ship_id", item.target_ship_id)
    casualty_items = tuple(crew_casualty_outcomes)
    for index, item in enumerate(casualty_items):
        item.validate(f"$.crew_casualty_outcomes[{index}]")
        if item.target_ship_id not in ship_map:
            raise ContractError("crew_casualty.ship_missing", f"$.crew_casualty_outcomes[{index}].target_ship_id", item.target_ship_id)
    if len({item.outcome_id for item in casualty_items}) != len(casualty_items):
        raise ContractError("crew_casualty.outcome_duplicate", "$.crew_casualty_outcomes", "同一固定步的伤亡结果 id 不得重复")
    if len({item.source_key for item in casualty_items}) != len(casualty_items):
        raise ContractError("crew_casualty.source_duplicate", "$.crew_casualty_outcomes", "同一来源事件不得重复结算伤亡")
    evacuation_items = tuple(crew_evacuation_outcomes)
    for index, item in enumerate(evacuation_items):
        item.validate(f"$.crew_evacuation_outcomes[{index}]")
        if item.ship_id not in ship_map:
            raise ContractError("crew_recovery.ship_missing", f"$.crew_evacuation_outcomes[{index}].ship_id", item.ship_id)
    if len({item.operation_id for item in evacuation_items}) != len(evacuation_items):
        raise ContractError("crew_recovery.evacuation_operation_duplicate", "$.crew_evacuation_outcomes", "同一固定步的弃舰操作 id 不得重复")
    if len({item.rescue_manifest_id for item in evacuation_items}) != len(evacuation_items):
        raise ContractError("crew_recovery.manifest_duplicate", "$.crew_evacuation_outcomes", "同一固定步的待救援清单 id 不得重复")
    if len({item.ship_id for item in evacuation_items}) != len(evacuation_items):
        raise ContractError("crew_recovery.evacuation_ship_duplicate", "$.crew_evacuation_outcomes", "同一舰艇在同一固定步只能弃舰一次")
    requires_continuous_damage = bool(
        ignition_items
        or damage_directive_items
        or propagation_items
        or cookoff_items
    ) or any(
        item.combat_state.instance.continuous_damage_state is not None
        for item in ship_map.values()
    )
    if requires_continuous_damage and continuous_damage_profile is None:
        raise ContractError("continuous_damage.profile_required", "$.continuous_damage_profile", "持续毁伤状态、点燃结果或损管指令需要精确配置")
    if continuous_damage_profile is not None:
        for ship_id, ship in ship_map.items():
            validate_instance_continuous_damage(
                binding_by_id[ship_id].snapshot,
                ship.combat_state.instance,
                continuous_damage_profile,
            )
    control_map = {} if controls is None else controls
    if set(control_map) - set(ship_map):
        raise ContractError("tactical_scene.control_unknown_ship", "$.controls", "控制输入引用了未绑定舰艇")
    directives = tuple(launch_directives)
    directive_map: dict[tuple[str, str, int], TacticalSceneLaunchDirective] = {}
    projectile_ids: set[str] = set()
    for directive in directives:
        key = _directive_key(directive, state.fixed_step_s)
        if key in directive_map or directive.projectile_id in projectile_ids:
            raise ContractError("tactical_scene.launch_directive_duplicate", "$.launch_directives", "事件键和弹丸 id 均不得重复")
        if directive.source_ship_id not in ship_map or directive.target_ship_id not in ship_map:
            raise ContractError("tactical_scene.launch_ship_missing", "$.launch_directives", "发射舰和目标舰都必须位于当前场景")
        directive_map[key] = directive
        projectile_ids.add(directive.projectile_id)
    permitted_steps = {state.fixed_step_index, state.fixed_step_index + 1}
    if any(key[2] not in permitted_steps for key in directive_map):
        raise ContractError("tactical_scene.launch_directive_outside_step", "$.launch_directives", "单步调用只接受当前或下一边界的发射指令")
    exit_map: dict[tuple[str, int], TacticalSceneExitDirective] = {}
    for directive in tuple(exit_directives):
        key = _exit_directive_key(directive, state.fixed_step_s)
        if key in exit_map:
            raise ContractError("tactical_scene.exit_directive_duplicate", "$.exit_directives", "同一舰艇在同一边界只能退出一次")
        if directive.ship_id not in ship_map:
            raise ContractError("tactical_scene.exit_ship_missing", "$.exit_directives.ship_id", directive.ship_id)
        if key[1] not in permitted_steps:
            raise ContractError("tactical_scene.exit_directive_outside_step", "$.exit_directives", "单步调用只接受当前或下一边界的退出指令")
        exit_map[key] = directive

    world = state.projectile_world
    weapon_events: list[TacticalSceneWeaponEvent] = []
    spawned: list[ProjectileState] = []
    used_directives: set[tuple[str, str, int]] = set()
    lifecycle_events: list[TacticalShipLifecycleEvent] = []
    lifecycle_expired: list[ProjectileExpiredEvent] = []
    engagement_events: list[TacticalEngagementEvent] = []
    continuous_damage_events: list[ContinuousDamageEvent] = []
    fire_propagation_events: list[FirePropagationEvent] = []
    ammunition_cookoff_events: list[AmmunitionCookoffEvent] = []
    crew_casualty_events: list[CrewCasualtyEvent] = []
    crew_evacuation_events: list[CrewEvacuationEvent] = []
    crew_rescue_manifests: list[CrewRescueManifest] = []
    observation_resolution = TacticalObservationResolution((), (), (), ())
    generated_guidance_fact_events: tuple[GeneratedGuidanceFactEvent, ...] = ()
    effective_guidance_inputs = manual_guidance_inputs
    ship_step_contexts: dict[str, ShipStepContext] = {}
    ship_instance_generations = {ship_id: 0 for ship_id in ship_map}
    last_context_instances = {
        ship_id: ship.combat_state.instance for ship_id, ship in ship_map.items()
    }

    def step_context(
        ship_id: str,
        boundary_time_s: float,
        boundary_step: int,
        *,
        require_model: bool = False,
    ) -> ShipStepContext:
        """以精确实例值为最终门禁复用 runtime，并按需绑定战术模型。"""

        ship = ship_map[ship_id]
        binding = binding_by_id[ship_id]
        instance = ship.combat_state.instance
        if last_context_instances[ship_id] != instance:
            ship_instance_generations[ship_id] += 1
            last_context_instances[ship_id] = instance
        generation = ship_instance_generations[ship_id]
        events = _runtime_automatic_events(
            binding,
            instance,
            observation_events_by_ship.get(ship_id, ()),
        )
        context = ship_step_contexts.get(ship_id)
        if not (
            context is not None
            and context.boundary_step_index == boundary_step
            and context.boundary_tactical_time_s == boundary_time_s
            and context.instance_generation == generation
            and context.instance_snapshot == instance
            and context.automatic_events == events
        ):
            runtime = binding.runtime_cache.resolve(
                binding.snapshot,
                binding.sortie,
                instance,
                active_automatic_events=events,
                validation_mode=runtime_validation_mode,
            ).runtime
            context = ShipStepContext(
                ship_id,
                boundary_step,
                boundary_time_s,
                generation,
                instance,
                events,
                runtime,
            )
            ship_step_contexts[ship_id] = context
        if require_model and context.tactical_model is None:
            context = replace(
                context,
                tactical_model=_binding_tactical_model(binding, context.runtime),
            )
            ship_step_contexts[ship_id] = context
        return context

    def observation_contexts(
        motions: dict[str, TacticalMotionState],
    ) -> tuple[TacticalObservationShipContext, ...]:
        contexts: list[TacticalObservationShipContext] = []
        for ship_id in sorted(ship_map):
            ship = ship_map[ship_id]
            binding = binding_by_id[ship_id]
            runtime = step_context(
                ship_id,
                state.tactical_time_s,
                state.fixed_step_index,
            ).runtime
            motion = motions[ship_id]
            contexts.append(
                TacticalObservationShipContext(
                    ship_id,
                    binding.snapshot,
                    runtime,
                    (motion.position_world_m.x, motion.position_world_m.y),
                    ship.lifecycle_state.physical_status,
                )
            )
        return tuple(contexts)

    def apply_exit_boundary(boundary_time_s: float, boundary_step: int) -> None:
        nonlocal world, ship_map
        for (ship_id, step_index), directive in sorted(exit_map.items()):
            if step_index != boundary_step:
                continue
            ship = ship_map[ship_id]
            previous = ship.lifecycle_state
            if previous.physical_status == "exited":
                raise ContractError("tactical_scene.ship_already_exited", f"$.exit_directives.{ship_id}", "舰艇已经离开场景")
            if directive.reason == "fell_below_scene" and previous.physical_status != "falling":
                raise ContractError("tactical_scene.ship_not_falling", f"$.exit_directives.{ship_id}", "只有失控坠落舰可使用坠出场景原因")
            lifecycle = TacticalShipLifecycleState(
                "exited",
                "uncommanded",
                previous.failure_causes,
                boundary_step,
                directive.reason,
                boundary_time_s,
            )
            combat = replace(
                ship.combat_state,
                instance=_suspend_weapon_timeline(
                    ship.combat_state.instance,
                    boundary_time_s,
                ),
            )
            ship_map[ship_id] = replace(
                ship,
                combat_state=combat,
                lifecycle_state=lifecycle,
            )
            event = _lifecycle_event(ship_id, boundary_time_s, previous, lifecycle)
            assert event is not None
            lifecycle_events.append(event)
            retained = []
            for projectile in world.projectiles:
                if projectile.target_ship_id == ship_id:
                    lifecycle_expired.append(
                        ProjectileExpiredEvent(
                            projectile.id,
                            boundary_time_s,
                            "target_left_scene",
                        )
                    )
                else:
                    retained.append(projectile)
            world = replace(world, projectiles=tuple(retained))

    def refresh_lifecycle_boundary(
        boundary_time_s: float,
        boundary_step: int,
        *,
        reuse_unchanged_projection: bool = False,
    ) -> None:
        nonlocal ship_map
        for ship_id in sorted(ship_map):
            ship = ship_map[ship_id]
            if ship.lifecycle_state.physical_status == "exited":
                continue
            binding = binding_by_id[ship_id]
            runtime = step_context(
                ship_id,
                boundary_time_s,
                boundary_step,
            ).runtime
            if reuse_unchanged_projection:
                projection = project_tactical_ship_lifecycle(
                    runtime,
                    binding.sortie,
                    previous=ship.lifecycle_state,
                )
                if projection == _lifecycle_projection_from_state(
                    ship.lifecycle_state
                ):
                    lifecycle = ship.lifecycle_state
                else:
                    lifecycle = _materialize_tactical_ship_lifecycle(
                        projection,
                        step_index=boundary_step,
                        previous=ship.lifecycle_state,
                    )
            else:
                lifecycle = derive_tactical_ship_lifecycle(
                    runtime,
                    binding.sortie,
                    step_index=boundary_step,
                    previous=ship.lifecycle_state,
                )
            if lifecycle.physical_status == "falling":
                ship = replace(
                    ship,
                    combat_state=replace(
                        ship.combat_state,
                        instance=_suspend_weapon_timeline(
                            ship.combat_state.instance,
                            boundary_time_s,
                        ),
                    ),
                )
            event = _lifecycle_event(
                ship_id,
                boundary_time_s,
                ship.lifecycle_state,
                lifecycle,
            )
            if event is not None:
                lifecycle_events.append(event)
            ship_map[ship_id] = replace(ship, lifecycle_state=lifecycle)

    def resolve_boundary(boundary_time_s: float, boundary_step: int, motions: dict[str, TacticalMotionState]) -> None:
        nonlocal world, ship_map
        spawn_inputs: list[ProjectileSpawnInput] = []
        timeline_plans: dict[
            str,
            tuple[WeaponTimelineAdvancePlan, str],
        ] = {}
        for ship_id in sorted(ship_map):
            ship = ship_map[ship_id]
            if ship.lifecycle_state.physical_status != "operational":
                continue
            context = step_context(ship_id, boundary_time_s, boundary_step)
            timeline_plans[ship_id] = (
                plan_weapon_timeline_advance(
                    binding_by_id[ship_id].snapshot,
                    ship.combat_state.instance,
                    timing_catalog,
                    target_tactical_time_s=boundary_time_s,
                ),
                context.runtime.instance_snapshot_sha256,
            )
        for ship_id in sorted(ship_map):
            ship = ship_map[ship_id]
            binding = binding_by_id[ship_id]
            if ship.lifecycle_state.physical_status != "operational":
                suspended = _suspend_weapon_timeline(
                    ship.combat_state.instance,
                    boundary_time_s,
                )
                ship_map[ship_id] = replace(
                    ship,
                    combat_state=replace(ship.combat_state, instance=suspended),
                )
                continue
            plan, source_instance_sha256 = timeline_plans[ship_id]
            if plan.mode == WEAPON_TIMELINE_ADVANCE_FULL:
                resolution = advance_weapon_timeline(
                    binding.snapshot,
                    binding.sortie,
                    ship.combat_state.instance,
                    timing_catalog,
                    target_tactical_time_s=boundary_time_s,
                    runtime_cache=binding.runtime_cache,
                    runtime_validation_mode=runtime_validation_mode,
                    _source_instance_sha256=source_instance_sha256,
                )
            else:
                resolution = apply_weapon_timeline_advance_plan(
                    ship.combat_state.instance,
                    plan,
                    _source_instance_sha256=source_instance_sha256,
                )
            if resolution.resulting_instance is not ship.combat_state.instance:
                ship = replace(
                    ship,
                    combat_state=replace(
                        ship.combat_state,
                        instance=resolution.resulting_instance,
                    ),
                )
                ship_map[ship_id] = ship
            for event in resolution.events:
                if abs(event.tactical_time_s - boundary_time_s) > EPS:
                    raise ContractError("tactical_scene.missed_weapon_boundary", f"$.ships.{ship_id}", "存在早于当前边界而未处理的武器事件")
                weapon_events.append(TacticalSceneWeaponEvent(ship_id, event))
                if event.status != "resolved" or event.action_kind != "fire":
                    continue
                key = ship_id, event.sequence_id, boundary_step
                directive = directive_map.get(key)
                if directive is None:
                    raise ContractError("tactical_scene.launch_directive_missing", f"$.ships.{ship_id}.weapon_events.{event.sequence_id}", "成功开火事件必须具有唯一弹丸发射指令")
                if ship_map[directive.target_ship_id].lifecycle_state.physical_status == "exited":
                    raise ContractError("tactical_scene.launch_target_exited", "$.launch_directives.target_ship_id", "不能向已经离开场景的舰艇生成弹丸")
                if observation_step_input is not None:
                    weapon = next(
                        (
                            item
                            for item in binding.snapshot.outfit.instances
                            if item.id == event.weapon_instance_id
                        ),
                        None,
                    )
                    if weapon is None or weapon.prototype.category != "weapon":
                        raise ContractError(
                            "tactical_observation.weapon_missing",
                            f"$.ships.{ship_id}.weapon_events.{event.sequence_id}",
                            event.weapon_instance_id,
                        )
                    assert event.action_resolution is not None
                    requirement = str(
                        weapon.prototype.capability.to_dict()[
                            "fire_control_requirement"
                        ]
                    )
                    validate_weapon_fire_control_support(
                        observation_resolution,
                        source_ship_id=ship_id,
                        target_ship_id=directive.target_ship_id,
                        fire_control_instance_id=(
                            event.action_resolution.fire_control_instance_id
                        ),
                        requirement=requirement,
                    )
                spawn_inputs.append(
                    ProjectileSpawnInput(
                        binding.snapshot,
                        event,
                        _pose(boundary_time_s, motions[ship_id]),
                        ProjectileSpawnRequest(
                            directive.projectile_id,
                            ship_id,
                            directive.target_ship_id,
                            directive.selected_target_deck_level,
                            directive.launch_direction_local_xy,
                        ),
                    )
                )
                used_directives.add(key)
            _validate_pending_event_grid(ship.combat_state.instance, state.fixed_step_s, ship_id)
        if spawn_inputs:
            spawn = spawn_projectiles_from_weapon_events(
                world,
                projectile_catalog,
                spawn_inputs,
                guidance_catalog=guidance_catalog,
            )
            world = spawn.resulting_world
            spawned.extend(spawn.projectiles)

    if continuous_damage_profile is not None:
        directives_by_ship: dict[str, list[DamageControlDirective]] = {}
        for directive in damage_directive_items:
            directives_by_ship.setdefault(directive.ship_id, []).append(directive)
        for ship_id in sorted(ship_map):
            resolution = apply_damage_control_directives(
                binding_by_id[ship_id].snapshot,
                ship_map[ship_id].combat_state.instance,
                continuous_damage_profile,
                ship_id=ship_id,
                tactical_time_s=state.tactical_time_s,
                directives=directives_by_ship.get(ship_id, ()),
            )
            continuous_damage_events.extend(resolution.events)
            ship = ship_map[ship_id]
            ship_map[ship_id] = replace(
                ship,
                combat_state=replace(
                    ship.combat_state,
                    instance=resolution.resulting_instance,
                ),
            )

    refresh_lifecycle_boundary(state.tactical_time_s, state.fixed_step_index)
    apply_exit_boundary(state.tactical_time_s, state.fixed_step_index)
    start_motions = {ship_id: item.motion_state for ship_id, item in ship_map.items()}
    if observation_step_input is not None:
        observation_resolution = resolve_tactical_observation_step(
            observation_contexts(start_motions),
            observation_step_input,
            tactical_time_s=state.tactical_time_s,
        )
    for ship_id in control_map:
        lifecycle = ship_map[ship_id].lifecycle_state
        if lifecycle.command_status == "uncommanded" or lifecycle.physical_status != "operational":
            raise ContractError(
                "tactical_scene.command_unavailable",
                f"$.controls.{ship_id}",
                "失控、坠落或离场舰不能接受本步操纵输入",
            )
    resolve_boundary(state.tactical_time_s, state.fixed_step_index, start_motions)
    if observation_step_input is not None:
        guidance_fact_resolution = generate_guidance_runtime_inputs(
            world.projectiles,
            observation_contexts(start_motions),
            guidance_catalog,
            observation_resolution,
            observation_step_input.seeker_observation_outcomes,
            tactical_time_s=state.tactical_time_s,
        )
        effective_guidance_inputs = guidance_fact_resolution.runtime_inputs
        generated_guidance_fact_events = guidance_fact_resolution.events

    models = {}
    runtimes_at_step_start: dict[str, RuntimeShipParameters] = {}
    next_motions: dict[str, TacticalMotionState] = {}
    diagnostics: dict[str, TacticalStepDiagnostics | None] = {}
    for ship_id in sorted(ship_map):
        ship = ship_map[ship_id]
        context = step_context(
            ship_id,
            state.tactical_time_s,
            state.fixed_step_index,
            require_model=ship.lifecycle_state.physical_status != "exited",
        )
        runtime = context.runtime
        runtimes_at_step_start[ship_id] = runtime
        if ship.lifecycle_state.physical_status == "exited":
            next_motions[ship_id] = replace(
                ship.motion_state,
                fixed_step_index=ship.motion_state.fixed_step_index + 1,
            )
            diagnostics[ship_id] = None
            continue
        model = context.tactical_model
        assert model is not None
        if abs(model.tuning.fixed_step_s - state.fixed_step_s) > EPS:
            raise ContractError("tactical_scene.fixed_step_mismatch", "$.fixed_step_s", "场景固定步与舰艇动力学配置不一致")
        models[ship_id] = model
        synced_motion = replace(
            ship.motion_state,
            hull_integrity_fraction=ship.combat_state.instance.current_hull_integrity_fraction,
            fuel_units=ship.combat_state.instance.operational_state.fuel_units,
        )
        controls_for_ship = (
            TacticalControlInput()
            if ship.lifecycle_state.physical_status == "falling"
            else control_map.get(ship_id, TacticalControlInput())
        )
        next_motion, diagnostic = integrate_tactical_step(model, synced_motion, controls_for_ship)
        committed_instance = commit_tactical_state_to_instance(model, next_motion)
        ship_map[ship_id] = replace(ship, combat_state=replace(ship.combat_state, instance=committed_instance))
        next_motions[ship_id] = next_motion
        diagnostics[ship_id] = diagnostic

    end_time_s = (state.fixed_step_index + 1) * state.fixed_step_s
    targets = []
    for ship_id in sorted(ship_map):
        ship = ship_map[ship_id]
        if ship.lifecycle_state.physical_status == "exited":
            continue
        model = models[ship_id]
        layer = model.environment.layer(start_motions[ship_id].height_layer)
        targets.append(
            TacticalProjectileTarget(
                ship_id,
                binding_by_id[ship_id].snapshot,
                ship.combat_state,
                _pose(state.tactical_time_s, start_motions[ship_id]),
                _pose(end_time_s, next_motions[ship_id]),
                layer.density_kg_m3,
                layer.sound_speed_mps,
                start_motions[ship_id].height_layer,
                _binding_projectile_target_geometry(binding_by_id[ship_id]),
            )
        )
    projectile_munition_by_id = {
        item.id: item.munition_id for item in world.projectiles
    }
    projectile_resolution = advance_projectile_world(
        world,
        projectile_catalog,
        targets,
        material_registry,
        target_tactical_time_s=end_time_s,
        density_kg_m3=0.0,
        sound_speed_mps=340.0,
        fixed_step_s=PROJECTILE_SUBSTEP_S,
        ricochet_rolls=ricochet_rolls,
        guidance_catalog=guidance_catalog,
        guidance_inputs=effective_guidance_inputs,
    )
    world = projectile_resolution.resulting_world
    for target in projectile_resolution.resulting_targets:
        ship = ship_map[target.ship_id]
        motion = next_motions[target.ship_id]
        motion = replace(
            motion,
            hull_integrity_fraction=target.combat_state.instance.current_hull_integrity_fraction,
            fuel_units=target.combat_state.instance.operational_state.fuel_units,
        )
        next_motions[target.ship_id] = motion
        ship_map[target.ship_id] = replace(ship, combat_state=target.combat_state, motion_state=motion)

    if continuous_damage_profile is not None:
        for ship_id in sorted(ship_map):
            ship = ship_map[ship_id]
            if ship.lifecycle_state.physical_status == "exited":
                continue
            resolution = advance_continuous_damage(
                binding_by_id[ship_id].snapshot,
                ship.combat_state.instance,
                runtimes_at_step_start[ship_id],
                continuous_damage_profile,
                ship_id=ship_id,
                target_tactical_time_s=end_time_s,
            )
            continuous_damage_events.extend(resolution.events)
            motion = replace(
                next_motions[ship_id],
                hull_integrity_fraction=resolution.resulting_instance.current_hull_integrity_fraction,
                fuel_units=resolution.resulting_instance.operational_state.fuel_units,
            )
            next_motions[ship_id] = motion
            ship_map[ship_id] = replace(
                ship,
                combat_state=replace(
                    ship.combat_state,
                    instance=resolution.resulting_instance,
                ),
                motion_state=motion,
            )

        impact_by_projectile = {
            item.projectile_id: item for item in projectile_resolution.impact_events
        }
        for outcome in sorted(ignition_items, key=lambda item: item.incident_id):
            impact = impact_by_projectile.get(outcome.projectile_id)
            if impact is None:
                raise ContractError("continuous_damage.ignition_impact_unmatched", "$.fire_ignition_outcomes", outcome.projectile_id)
            if impact.target_ship_id != outcome.target_ship_id:
                raise ContractError("continuous_damage.ignition_ship_mismatch", "$.fire_ignition_outcomes.target_ship_id", impact.target_ship_id)
            if outcome.target_module_instance_id not in impact.damaged_module_instance_ids:
                raise ContractError("continuous_damage.ignition_module_unmatched", "$.fire_ignition_outcomes.target_module_instance_id", outcome.target_module_instance_id)
            munition_id = projectile_munition_by_id[outcome.projectile_id]
            if projectile_catalog.profile(munition_id).penetration.aftereffect != Aftereffect.FIRE:
                raise ContractError("continuous_damage.ignition_aftereffect", "$.fire_ignition_outcomes.projectile_id", "只有 fire 后效弹丸的实际命中可接受点燃结果")
            ship = ship_map[outcome.target_ship_id]
            resolution = register_fire_ignition(
                binding_by_id[outcome.target_ship_id].snapshot,
                ship.combat_state.instance,
                continuous_damage_profile,
                outcome,
                ship_id=outcome.target_ship_id,
                created_time_s=impact.tactical_time_s,
                state_tactical_time_s=end_time_s,
            )
            continuous_damage_events.extend(resolution.events)
            ship_map[outcome.target_ship_id] = replace(
                ship,
                combat_state=replace(
                    ship.combat_state,
                    instance=resolution.resulting_instance,
                ),
            )

        propagations_by_ship: dict[str, list[FirePropagationOutcome]] = {}
        for item in propagation_items:
            propagations_by_ship.setdefault(item.target_ship_id, []).append(item)
        cookoffs_by_ship: dict[str, list[AmmunitionCookoffOutcome]] = {}
        for item in cookoff_items:
            cookoffs_by_ship.setdefault(item.target_ship_id, []).append(item)
        for ship_id in sorted(set(propagations_by_ship) | set(cookoffs_by_ship)):
            ship = ship_map[ship_id]
            resolution = apply_secondary_damage_outcomes(
                binding_by_id[ship_id].snapshot,
                ship.combat_state.instance,
                continuous_damage_profile,
                ship_id=ship_id,
                target_tactical_time_s=end_time_s,
                source_fire_incidents=source_fire_incidents_by_ship[ship_id],
                source_fire_events=continuous_damage_events,
                fire_propagation_outcomes=propagations_by_ship.get(ship_id, ()),
                ammunition_cookoff_outcomes=cookoffs_by_ship.get(ship_id, ()),
            )
            fire_propagation_events.extend(resolution.fire_propagation_events)
            ammunition_cookoff_events.extend(resolution.ammunition_cookoff_events)
            ship_map[ship_id] = replace(
                ship,
                combat_state=replace(
                    ship.combat_state,
                    instance=resolution.resulting_instance,
                ),
            )

    impact_by_projectile = {
        item.projectile_id: item for item in projectile_resolution.impact_events
    }
    fire_damage_events = tuple(
        item
        for item in continuous_damage_events
        if item.event_kind == "fire_damage_applied"
    )
    casualties_by_ship: dict[str, list[CrewCasualtyOutcome]] = {}
    for index, outcome in enumerate(casualty_items):
        path = f"$.crew_casualty_outcomes[{index}]"
        if outcome.source_kind == "projectile_impact":
            source = impact_by_projectile.get(outcome.source_id)
            if source is None or abs(source.tactical_time_s - outcome.source_tactical_time_s) > EPS:
                raise ContractError("crew_casualty.impact_source_unmatched", path, outcome.source_id)
            if source.target_ship_id != outcome.target_ship_id:
                raise ContractError("crew_casualty.source_ship_mismatch", f"{path}.target_ship_id", source.target_ship_id)
            if (
                outcome.target_module_instance_id is not None
                and outcome.target_module_instance_id not in source.damaged_module_instance_ids
            ):
                raise ContractError("crew_casualty.source_module_unmatched", f"{path}.target_module_instance_id", outcome.target_module_instance_id)
        elif outcome.source_kind == "fire_damage":
            source = next(
                (
                    item
                    for item in fire_damage_events
                    if item.fire_incident_id == outcome.source_id
                    and abs(
                        item.tactical_time_s - outcome.source_tactical_time_s
                    )
                    <= EPS
                ),
                None,
            )
            if source is None:
                raise ContractError("crew_casualty.fire_source_unmatched", path, outcome.source_id)
            if source.ship_id != outcome.target_ship_id:
                raise ContractError("crew_casualty.source_ship_mismatch", f"{path}.target_ship_id", source.ship_id)
            if (
                outcome.target_module_instance_id is not None
                and outcome.target_module_instance_id != source.target_module_instance_id
            ):
                raise ContractError("crew_casualty.source_module_unmatched", f"{path}.target_module_instance_id", outcome.target_module_instance_id)
        else:
            source = next(
                (
                    item
                    for item in ammunition_cookoff_events
                    if item.explosion_id == outcome.source_id
                    and abs(item.tactical_time_s - outcome.source_tactical_time_s)
                    <= EPS
                ),
                None,
            )
            if source is None:
                raise ContractError("crew_casualty.secondary_explosion_source_unmatched", path, outcome.source_id)
            if source.ship_id != outcome.target_ship_id:
                raise ContractError("crew_casualty.source_ship_mismatch", f"{path}.target_ship_id", source.ship_id)
            if (
                outcome.target_module_instance_id is not None
                and outcome.target_module_instance_id
                not in source.damaged_module_instance_ids
            ):
                raise ContractError("crew_casualty.source_module_unmatched", f"{path}.target_module_instance_id", outcome.target_module_instance_id)
        casualties_by_ship.setdefault(outcome.target_ship_id, []).append(outcome)

    for ship_id in sorted(ship_map):
        ship = ship_map[ship_id]
        if ship.lifecycle_state.physical_status == "exited":
            continue
        resolution = apply_crew_casualty_outcomes(
            ship.combat_state.instance,
            casualties_by_ship.get(ship_id, ()),
            ship_id=ship_id,
            target_tactical_time_s=end_time_s,
        )
        crew_casualty_events.extend(resolution.events)
        ship_map[ship_id] = replace(
            ship,
            combat_state=replace(
                ship.combat_state,
                instance=resolution.resulting_instance,
            ),
        )

    refresh_lifecycle_boundary(
        end_time_s,
        state.fixed_step_index + 1,
        reuse_unchanged_projection=True,
    )
    for outcome in sorted(evacuation_items, key=lambda item: item.ship_id):
        ship = ship_map[outcome.ship_id]
        evacuation = apply_crew_evacuation_outcome(
            ship.combat_state.instance,
            outcome,
            ship_id=outcome.ship_id,
            physical_status=ship.lifecycle_state.physical_status,
            target_tactical_time_s=end_time_s,
        )
        crew_evacuation_events.extend(evacuation.events)
        if evacuation.rescue_manifest is not None:
            crew_rescue_manifests.append(evacuation.rescue_manifest)
        ship_map[outcome.ship_id] = replace(
            ship,
            combat_state=replace(
                ship.combat_state,
                instance=evacuation.resulting_instance,
            ),
        )
    apply_exit_boundary(end_time_s, state.fixed_step_index + 1)
    resolve_boundary(end_time_s, state.fixed_step_index + 1, next_motions)
    unused = sorted(set(directive_map) - used_directives)
    if unused:
        raise ContractError("tactical_scene.launch_directive_unmatched", "$.launch_directives", f"没有对应成功开火事件：{unused}")

    ship_results: list[TacticalSceneShipStepResult] = []
    final_ships: list[TacticalSceneShipState] = []
    for ship_id in sorted(ship_map):
        ship = ship_map[ship_id]
        motion = replace(
            next_motions[ship_id],
            hull_integrity_fraction=ship.combat_state.instance.current_hull_integrity_fraction,
            fuel_units=ship.combat_state.instance.operational_state.fuel_units,
        )
        ship = replace(ship, motion_state=motion)
        ship_map[ship_id] = ship
        runtime = step_context(
            ship_id,
            end_time_s,
            state.fixed_step_index + 1,
        ).runtime
        ship_results.append(TacticalSceneShipStepResult(ship_id, diagnostics[ship_id], runtime))
        final_ships.append(ship)
    engagement = state.engagement_state
    if engagement is not None:
        assert engagement_boundary_profile is not None
        evaluated = evaluate_tactical_engagement(
            final_ships,
            engagement_boundary_profile,
            TacticalEngagementDefinition(
                engagement.initiating_side_id,
                engagement.responding_side_id,
            ),
            step_index=state.fixed_step_index + 1,
            previous=engagement,
        )
        if evaluated.status != engagement.status:
            engagement_events.append(
                TacticalEngagementEvent(
                    end_time_s,
                    engagement.status,
                    evaluated,
                )
            )
        engagement = evaluated
    resulting_scene = TacticalSceneState(
        state.fixed_step_s,
        state.fixed_step_index + 1,
        end_time_s,
        world,
        tuple(final_ships),
        engagement,
    )
    _validate_internal_state(resulting_scene)
    return TacticalSceneStepResolution(
        canonical_sha256(state),
        resulting_scene,
        tuple(sorted(weapon_events, key=lambda item: (item.event.tactical_time_s, item.ship_id, item.event.sequence_id, item.event.action_kind))),
        tuple(spawned),
        projectile_resolution.impact_events,
        tuple(
            sorted(
                (*projectile_resolution.expired_events, *lifecycle_expired),
                key=lambda item: (item.tactical_time_s, item.projectile_id),
            )
        ),
        tuple(
            sorted(
                lifecycle_events,
                key=lambda item: (item.tactical_time_s, item.ship_id),
            )
        ),
        tuple(engagement_events),
        tuple(ship_results),
        projectile_resolution.guidance_events,
        tuple(
            sorted(
                continuous_damage_events,
                key=lambda item: (
                    item.tactical_time_s,
                    item.ship_id,
                    item.fire_incident_id,
                    item.event_kind,
                ),
            )
        ),
        tuple(
            sorted(
                crew_casualty_events,
                key=lambda item: (
                    item.tactical_time_s,
                    item.ship_id,
                    item.outcome_id,
                    item.crew_type,
                ),
            )
        ),
        tuple(
            sorted(
                crew_evacuation_events,
                key=lambda item: (
                    item.tactical_time_s,
                    item.ship_id,
                    item.operation_id,
                    item.crew_type,
                ),
            )
        ),
        tuple(sorted(crew_rescue_manifests, key=lambda item: item.manifest_id)),
        tuple(
            sorted(
                fire_propagation_events,
                key=lambda item: (
                    item.tactical_time_s,
                    item.ship_id,
                    item.outcome_id,
                ),
            )
        ),
        tuple(
            sorted(
                ammunition_cookoff_events,
                key=lambda item: (
                    item.tactical_time_s,
                    item.ship_id,
                    item.outcome_id,
                ),
            )
        ),
        observation_resolution.observation_events,
        observation_resolution.radar_emission_events,
        observation_resolution.fire_control_support_events,
        generated_guidance_fact_events,
    )
