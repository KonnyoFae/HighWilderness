"""T0b.2d2a：可排程推进资源、精确执行器绑定与离散控制合同。

本模块只建立资源和命令桥。它不推进场景、不计算力/力矩/油耗，也不调用
安全 governor、硬故障或方向互锁。
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, Iterable, TYPE_CHECKING

from 高天荒野舰艇数据契约 import (
    MODULE_CATALOG_V2_SCHEMA_ID,
    MODULE_CATALOG_V3_SCHEMA_ID,
    RESOURCE_ID_PATTERN,
    ContractError,
    ModulePrototypeCatalog,
    OutfitPlanInput,
    ResourceReference,
    canonical_sha256,
    merge_module_prototype_catalogs,
    migrate_known_module_catalog_v1_to_v2,
)
from 高天荒野舰艇无界面舾装编译器 import CompiledOutfit
from 高天荒野舰艇战术机动求解器 import TacticalControlInput
from 高天荒野舰艇推进安全判定器 import (
    TELEGRAPH_NOTCH_PERCENT,
    TELEGRAPH_NOTCHES,
    THRUST_OUTPUT_STAGES_PERCENT,
)
from 高天荒野舰艇推进时间内核 import validate_propulsion_timing_capability
from 高天荒野舰艇推进状态合同 import (
    PROPULSION_COMMAND_CHANNELS,
    PROPULSION_STATE_EVENT_INTERFACE_ID,
    PropulsionStateEvent,
)
from 高天荒野舰艇推进通道合同 import D1_SCENE_INTERFACE_ID

if TYPE_CHECKING:
    from 高天荒野舰艇统一战术场景 import TacticalSceneStepResolution


PROPULSION_CONTROL_INTERFACE_ID = "gaotian.tactical-propulsion-control/v1alpha1"
LEGACY_CONTINUOUS_CONTROL_INTERFACE_ID = "gaotian.tactical-control-input/v1alpha1"
PROPULSION_ACTUATOR_BINDING_INTERFACE_ID = (
    "gaotian.propulsion-actuator-binding/v1alpha1"
)
TACTICAL_PROPULSION_STEP_RESULT_INTERFACE_ID = (
    "gaotian.tactical-scene-step-resolution/v2alpha1"
)
TACTICAL_PROPULSION_STEP_RESULT_POLICY_ID = (
    "gaotian.tactical-scene-step/boundary-events-propulsion-stable/v1"
)
TACTICAL_SCENE_PROPULSION_EVENT_INTERFACE_ID = (
    "gaotian.tactical-scene-propulsion-event/v1alpha1"
)

SCENE_PROFILE_KEYS = (
    "minimum_legal",
    "conventional_crewed",
    "unmanned_flagship",
)
PROPULSION_CATEGORIES = ("main_engine", "maneuver_thruster")
AUTOMATIC_BRAKE_MAIN_ENGINE_NOTCH = "quarter"
AUTOMATIC_BRAKE_MANEUVER_TARGET_PERCENT = 25
AUTOMATIC_BRAKE_SELECTION_POLICY_ID = (
    "gaotian.propulsion-control/opposing-direction-quarter-brake/v1"
)
MANEUVER_QUANTIZATION_POLICY_ID = (
    "gaotian.propulsion-control/nearest-stage-ties-up/v1"
)
MAIN_ENGINE_QUANTIZATION_POLICY_ID = (
    "gaotian.propulsion-control/nearest-telegraph-ties-up/v1"
)


@dataclass(frozen=True)
class SceneCatalogV2ToV3Migration:
    profile_key: str
    source_id: str
    source_sha256: str
    target_version: int = 3
    response_time_s: float = 1.0


KNOWN_SCENE_CATALOG_V2_TO_V3_MIGRATIONS = (
    SceneCatalogV2ToV3Migration(
        "minimum_legal",
        "gtw.module_catalog.fixture.minimum",
        "75c4ef8c6cf873d0d94455a0fdb27ec0f062924aa5c3b8854861ecb8c00ed396",
    ),
    SceneCatalogV2ToV3Migration(
        "conventional_crewed",
        "gtw.module_catalog.fixture.stage_f_conventional_crewed_combined",
        "5c4620ddd9db8cf436a526c5638a89324bfb61edd3d0c828f38b255e7b9fdd5b",
    ),
    SceneCatalogV2ToV3Migration(
        "unmanned_flagship",
        "gtw.module_catalog.fixture.stage_f_unmanned_flagship_combined",
        "a7d482132f28e82767dae76c0d3e09b28e0ced001646d24b77562bfb690402f7",
    ),
)

_SCENE_COMPONENT_IDS = {
    "minimum_legal": ("gtw.module_catalog.fixture.minimum",),
    "conventional_crewed": (
        "gtw.module_catalog.fixture.minimum",
        "gtw.module_catalog.fixture.combat_system",
    ),
    "unmanned_flagship": (
        "gtw.module_catalog.fixture.minimum",
        "gtw.module_catalog.fixture.combat_system",
        "gtw.module_catalog.fixture.stage_f_unmanned",
    ),
}


def compose_known_scene_catalog_v2(
    profile_key: str,
    source_catalogs: Iterable[ModulePrototypeCatalog],
) -> ModulePrototypeCatalog:
    """从具名 v1 内容目录确定性复现某一场景类型的 c2a v2 目录。"""

    expected_ids = _SCENE_COMPONENT_IDS.get(profile_key)
    if expected_ids is None:
        raise ContractError(
            "propulsion_bridge.scene_profile_unknown",
            "$.profile_key",
            profile_key,
        )
    source_tuple = tuple(source_catalogs)
    by_id = {catalog.id: catalog for catalog in source_tuple}
    if (
        tuple(sorted(by_id)) != tuple(sorted(expected_ids))
        or len(by_id) != len(expected_ids)
        or len(source_tuple) != len(expected_ids)
    ):
        raise ContractError(
            "propulsion_bridge.scene_catalog_components",
            "$.source_catalogs",
            f"{profile_key} 必须且只能提供 {list(expected_ids)}",
        )
    migrated = tuple(
        migrate_known_module_catalog_v1_to_v2(by_id[catalog_id])
        for catalog_id in expected_ids
    )
    if profile_key == "minimum_legal":
        result = migrated[0]
    else:
        result = merge_module_prototype_catalogs(
            migrated,
            id=f"gtw.module_catalog.fixture.stage_f_{profile_key}_combined",
            version=2,
            name=f"阶段F·{profile_key}·组合模块目录",
            fixture_level="contract_fixture",
            schema=MODULE_CATALOG_V2_SCHEMA_ID,
        )
    specification = next(
        item
        for item in KNOWN_SCENE_CATALOG_V2_TO_V3_MIGRATIONS
        if item.profile_key == profile_key
    )
    if (
        result.id != specification.source_id
        or canonical_sha256(result) != specification.source_sha256
    ):
        raise ContractError(
            "propulsion_bridge.scene_catalog_v2_hash",
            "$.source_catalogs",
            f"{profile_key} 的 v2 组合结果不匹配冻结指纹",
        )
    return result


def migrate_known_scene_catalog_v2_to_v3(
    catalog: ModulePrototypeCatalog,
) -> ModulePrototypeCatalog:
    """把三类指纹锁定的场景 v2 目录迁移为可排程 v3 技术替身。"""

    specification = next(
        (
            item
            for item in KNOWN_SCENE_CATALOG_V2_TO_V3_MIGRATIONS
            if item.source_id == catalog.id
        ),
        None,
    )
    if (
        specification is None
        or catalog.schema != MODULE_CATALOG_V2_SCHEMA_ID
        or catalog.version != 2
    ):
        raise ContractError(
            "propulsion_bridge.catalog_migration_unknown",
            "$.module_catalog",
            f"没有 {catalog.id}@{catalog.version} 的具名 v2→v3 迁移",
        )
    if canonical_sha256(catalog) != specification.source_sha256:
        raise ContractError(
            "propulsion_bridge.catalog_migration_source_hash",
            "$.module_catalog",
            f"{catalog.id}@{catalog.version} 内容指纹不匹配",
        )
    modules: list[dict[str, Any]] = []
    for module in catalog.modules:
        value = module.to_dict()
        if module.category in PROPULSION_CATEGORIES:
            if module.reference.version != 2:
                raise ContractError(
                    "propulsion_bridge.catalog_module_version",
                    "$.module_catalog.modules",
                    f"{module.reference.id} 不是版本 2",
                )
            value["version"] = 3
            value["capability"]["response_time_s"] = (
                specification.response_time_s
            )
        modules.append(value)
    return ModulePrototypeCatalog.parse(
        {
            "fixture_level": catalog.fixture_level,
            "id": catalog.id,
            "kind": "ModulePrototypeCatalog",
            "modules": modules,
            "name": catalog.name,
            "schema": MODULE_CATALOG_V3_SCHEMA_ID,
            "version": specification.target_version,
        },
        "$.migrated_scene_module_catalog",
    )


@dataclass(frozen=True)
class OutfitV1ToD2AMigration:
    profile_key: str
    source_id: str
    source_sha256: str
    propulsion_module_ids: tuple[str, ...]
    target_version: int = 2


KNOWN_OUTFIT_V1_TO_D2A_MIGRATIONS = (
    OutfitV1ToD2AMigration(
        "minimum_legal",
        "gtw.outfit.fixture.stage_f.minimum_legal",
        "440be69e67713ba2395fcf0855c5246ef0d47446273dae33f3dcde414fe0085e",
        (
            "gtw.module.fixture.main_engine",
            "gtw.module.fixture.maneuver_thruster",
        ),
    ),
    OutfitV1ToD2AMigration(
        "conventional_crewed",
        "gtw.outfit.fixture.stage_f.conventional_crewed",
        "cb8862db2ea80727bfc85a53a0520a1f4c1e2730a49dce87531c86c829765570",
        (
            "gtw.module.fixture.main_engine",
            "gtw.module.fixture.maneuver_thruster",
        ),
    ),
    OutfitV1ToD2AMigration(
        "unmanned_flagship",
        "gtw.outfit.fixture.stage_f.unmanned_flagship",
        "84dd234ad868d35834bb2b87c6fe2c82a01509333520be3ad8e5791f448377e9",
        (
            "gtw.module.fixture.unmanned.main_engine",
            "gtw.module.fixture.unmanned.maneuver_thruster",
        ),
    ),
)


def migrate_known_scene_outfit_v1_to_d2a(
    outfit: OutfitPlanInput,
) -> OutfitPlanInput:
    """只升级三份已知舾装中的推进原型精确引用，其他字段保持不变。"""

    specification = next(
        (
            item
            for item in KNOWN_OUTFIT_V1_TO_D2A_MIGRATIONS
            if item.source_id == outfit.id and outfit.version == 1
        ),
        None,
    )
    if specification is None:
        raise ContractError(
            "propulsion_bridge.outfit_migration_unknown",
            "$.outfit",
            f"没有 {outfit.id}@{outfit.version} 的具名推进引用迁移",
        )
    if canonical_sha256(outfit) != specification.source_sha256:
        raise ContractError(
            "propulsion_bridge.outfit_migration_source_hash",
            "$.outfit",
            f"{outfit.id}@{outfit.version} 内容指纹不匹配",
        )
    propulsion_ids = set(specification.propulsion_module_ids)
    migrated_count = 0
    value = outfit.to_dict()
    value["version"] = specification.target_version
    for instance in value["modules"]:
        reference = instance["prototype"]
        if reference["id"] not in propulsion_ids:
            continue
        if reference["version"] != 1:
            raise ContractError(
                "propulsion_bridge.outfit_prototype_version",
                "$.outfit.modules",
                f"{reference['id']} 不是版本 1",
            )
        reference["version"] = 3
        migrated_count += 1
    expected_count = sum(
        item.prototype.id in propulsion_ids for item in outfit.modules
    )
    if migrated_count != expected_count or migrated_count == 0:
        raise ContractError(
            "propulsion_bridge.outfit_mapping_incomplete",
            "$.outfit.modules",
            "推进原型引用迁移不完整",
        )
    return OutfitPlanInput.parse(value, "$.migrated_outfit")


def _nearest_stage_percent(fraction: float) -> int:
    if isinstance(fraction, bool) or not isinstance(fraction, (int, float)):
        raise ContractError(
            "propulsion_control.fraction_type",
            "$.fraction",
            "必须是数值",
        )
    if not isfinite(float(fraction)):
        raise ContractError("propulsion_control.fraction_finite", "$.fraction", "必须是有限数")
    clamped_percent = min(100.0, max(0.0, abs(float(fraction)) * 100.0))
    return min(
        THRUST_OUTPUT_STAGES_PERCENT,
        key=lambda stage: (abs(stage - clamped_percent), -stage),
    )


def _nearest_telegraph_notch(fraction: float) -> str:
    _nearest_stage_percent(fraction)  # 两条量化入口使用相同的严格数值闸门。
    target_percent = min(100.0, max(0.0, abs(float(fraction)) * 100.0))
    return min(
        TELEGRAPH_NOTCH_PERCENT,
        key=lambda item: (abs(item[1] - target_percent), -item[1]),
    )[0]


@dataclass(frozen=True)
class DirectionPropulsionCommand:
    command_channel: str
    main_engine_notch: str
    maneuver_target_percent: int

    def __post_init__(self) -> None:
        if self.command_channel not in PROPULSION_COMMAND_CHANNELS:
            raise ValueError("command_channel 非法")
        if self.main_engine_notch not in TELEGRAPH_NOTCHES:
            raise ValueError("main_engine_notch 非法")
        if type(self.maneuver_target_percent) is not int or self.maneuver_target_percent not in THRUST_OUTPUT_STAGES_PERCENT:
            raise ValueError("maneuver_target_percent 非法")

    @classmethod
    def parse(cls, value: Any, path: str) -> "DirectionPropulsionCommand":
        if not isinstance(value, dict) or set(value) != {
            "command_channel",
            "main_engine_notch",
            "maneuver_target_percent",
        }:
            raise ContractError("object.keys", path, "方向命令字段集合不匹配")
        try:
            return cls(
                value["command_channel"],
                value["main_engine_notch"],
                value["maneuver_target_percent"],
            )
        except ValueError as error:
            raise ContractError(
                "propulsion_control.direction_command",
                path,
                str(error),
            ) from error

    def to_dict(self) -> dict[str, Any]:
        return {
            "command_channel": self.command_channel,
            "main_engine_notch": self.main_engine_notch,
            "maneuver_target_percent": self.maneuver_target_percent,
        }


@dataclass(frozen=True)
class TacticalPropulsionControlInput:
    direction_commands: tuple[DirectionPropulsionCommand, ...]
    automatic_brake: bool = False
    overg_requested: bool = False
    source_migration_id: str | None = None

    def __post_init__(self) -> None:
        channels = tuple(item.command_channel for item in self.direction_commands)
        if channels != PROPULSION_COMMAND_CHANNELS:
            raise ValueError("方向命令必须按规范顺序恰好覆盖四个通道")
        if not isinstance(self.automatic_brake, bool) or not isinstance(
            self.overg_requested, bool
        ):
            raise ValueError("控制开关必须是布尔值")
        if self.source_migration_id is not None and (
            not isinstance(self.source_migration_id, str)
            or not RESOURCE_ID_PATTERN.fullmatch(self.source_migration_id)
        ):
            raise ValueError("source_migration_id 非法")

    @classmethod
    def parse(cls, value: Any, path: str = "$") -> "TacticalPropulsionControlInput":
        if not isinstance(value, dict) or set(value) != {
            "automatic_brake",
            "automatic_brake_policy",
            "direction_commands",
            "interface",
            "main_engine_quantization_policy",
            "maneuver_quantization_policy",
            "overg_requested",
            "source_migration_id",
        }:
            raise ContractError("object.keys", path, "推进控制字段集合不匹配")
        if value["interface"] != PROPULSION_CONTROL_INTERFACE_ID:
            raise ContractError(
                "propulsion_control.interface",
                f"{path}.interface",
                str(value["interface"]),
            )
        expected_policies = {
            "automatic_brake_policy": AUTOMATIC_BRAKE_SELECTION_POLICY_ID,
            "main_engine_quantization_policy": MAIN_ENGINE_QUANTIZATION_POLICY_ID,
            "maneuver_quantization_policy": MANEUVER_QUANTIZATION_POLICY_ID,
        }
        for key, expected in expected_policies.items():
            if value[key] != expected:
                raise ContractError(
                    "propulsion_control.policy",
                    f"{path}.{key}",
                    str(value[key]),
                )
        try:
            return cls(
                tuple(
                    DirectionPropulsionCommand.parse(
                        item,
                        f"{path}.direction_commands[{index}]",
                    )
                    for index, item in enumerate(value["direction_commands"])
                ),
                value["automatic_brake"],
                value["overg_requested"],
                value["source_migration_id"],
            )
        except (TypeError, ValueError) as error:
            raise ContractError(
                "propulsion_control.invariant",
                path,
                str(error),
            ) from error

    def to_dict(self) -> dict[str, Any]:
        return {
            "automatic_brake": self.automatic_brake,
            "automatic_brake_policy": AUTOMATIC_BRAKE_SELECTION_POLICY_ID,
            "direction_commands": [item.to_dict() for item in self.direction_commands],
            "interface": PROPULSION_CONTROL_INTERFACE_ID,
            "main_engine_quantization_policy": MAIN_ENGINE_QUANTIZATION_POLICY_ID,
            "maneuver_quantization_policy": MANEUVER_QUANTIZATION_POLICY_ID,
            "overg_requested": self.overg_requested,
            "source_migration_id": self.source_migration_id,
        }


def _control(
    values: dict[str, tuple[str, int]],
    *,
    automatic_brake: bool = False,
    overg_requested: bool = False,
    source_migration_id: str | None = None,
) -> TacticalPropulsionControlInput:
    return TacticalPropulsionControlInput(
        tuple(
            DirectionPropulsionCommand(
                channel,
                values.get(channel, ("stop", 0))[0],
                values.get(channel, ("stop", 0))[1],
            )
            for channel in PROPULSION_COMMAND_CHANNELS
        ),
        automatic_brake,
        overg_requested,
        source_migration_id,
    )


@dataclass(frozen=True)
class LegacyContinuousControlMigration:
    migration_id: str
    source_sha256: str
    wheel: float


KNOWN_T0_CONTINUOUS_CONTROL_MIGRATIONS = (
    LegacyContinuousControlMigration(
        "gtw.migration.t0.control.forward-left",
        "08d3d33067a03aac14fb13f64806ecfdaf2a71f1dd02b12e5d48d0abe2122493",
        0.05,
    ),
    LegacyContinuousControlMigration(
        "gtw.migration.t0.control.forward-right",
        "745da2122ca9035395e9d939c09a8fbb3e532784558270b224290245a3b67872",
        -0.05,
    ),
)


def _legacy_control_dict(control: TacticalControlInput) -> dict[str, Any]:
    return {
        "brake": control.brake,
        "interface": LEGACY_CONTINUOUS_CONTROL_INTERFACE_ID,
        "move_body": [control.move_body.x, control.move_body.y],
        "overg": control.overg,
        "wheel": control.wheel,
    }


def migrate_known_t0_continuous_control(
    control: TacticalControlInput,
) -> TacticalPropulsionControlInput:
    """只接受两种已冻结 T0 连续输入，并量化为版本化离散命令。"""

    if not isinstance(control, TacticalControlInput):
        raise ContractError(
            "propulsion_control.legacy_type",
            "$.control",
            "必须传入 TacticalControlInput",
        )
    source_sha256 = canonical_sha256(_legacy_control_dict(control))
    specification = next(
        (
            item
            for item in KNOWN_T0_CONTINUOUS_CONTROL_MIGRATIONS
            if item.source_sha256 == source_sha256
        ),
        None,
    )
    if specification is None:
        raise ContractError(
            "propulsion_control.legacy_migration_unknown",
            "$.control",
            "连续输入没有具名迁移",
        )
    turn_channel = "left" if specification.wheel > 0.0 else "right"
    values = {
        "forward": (
            _nearest_telegraph_notch(control.move_body.y),
            _nearest_stage_percent(control.move_body.y),
        ),
        turn_channel: ("stop", _nearest_stage_percent(control.wheel)),
    }
    return _control(
        values,
        overg_requested=control.overg,
        source_migration_id=specification.migration_id,
    )


def automatic_brake_control(
    *,
    lateral_velocity_body_mps: float,
    longitudinal_velocity_body_mps: float,
    overg_requested: bool = False,
) -> TacticalPropulsionControlInput:
    """冻结自动制动为反向通道 quarter/25%，零速度轴不发命令。"""

    for path, value in (
        ("$.lateral_velocity_body_mps", lateral_velocity_body_mps),
        ("$.longitudinal_velocity_body_mps", longitudinal_velocity_body_mps),
    ):
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not isfinite(float(value))
        ):
            raise ContractError(
                "propulsion_control.brake_velocity",
                path,
                "速度必须是有限数",
            )
    values: dict[str, tuple[str, int]] = {}
    if longitudinal_velocity_body_mps > 0.0:
        values["reverse"] = (
            AUTOMATIC_BRAKE_MAIN_ENGINE_NOTCH,
            AUTOMATIC_BRAKE_MANEUVER_TARGET_PERCENT,
        )
    elif longitudinal_velocity_body_mps < 0.0:
        values["forward"] = (
            AUTOMATIC_BRAKE_MAIN_ENGINE_NOTCH,
            AUTOMATIC_BRAKE_MANEUVER_TARGET_PERCENT,
        )
    if lateral_velocity_body_mps > 0.0:
        values["left"] = ("stop", AUTOMATIC_BRAKE_MANEUVER_TARGET_PERCENT)
    elif lateral_velocity_body_mps < 0.0:
        values["right"] = ("stop", AUTOMATIC_BRAKE_MANEUVER_TARGET_PERCENT)
    return _control(
        values,
        automatic_brake=True,
        overg_requested=overg_requested,
    )


@dataclass(frozen=True)
class PropulsionActuatorBinding:
    scene_id: str
    ship_id: str
    actuator_instance_id: str
    actuator_category: str
    prototype: ResourceReference
    command_channels: tuple[str, ...]
    startup_steps: int
    response_steps: int
    module_catalog: ResourceReference
    module_catalog_sha256: str

    def __post_init__(self) -> None:
        for value in (self.scene_id, self.ship_id, self.actuator_instance_id):
            if not isinstance(value, str) or not RESOURCE_ID_PATTERN.fullmatch(value):
                raise ValueError("绑定资源 id 非法")
        if self.actuator_category not in PROPULSION_CATEGORIES:
            raise ValueError("执行器类别非法")
        expected_channels = tuple(
            item for item in PROPULSION_COMMAND_CHANNELS if item in self.command_channels
        )
        if (
            not self.command_channels
            or self.command_channels != expected_channels
            or len(set(self.command_channels)) != len(self.command_channels)
        ):
            raise ValueError("命令通道必须非空并按规范顺序排列")
        if self.prototype.version < 3 or self.module_catalog.version < 3:
            raise ValueError("d2a 绑定必须指向 v3 资源")
        if self.startup_steps < 0 or self.response_steps < 1:
            raise ValueError("排程步数非法")

    def to_dict(self) -> dict[str, Any]:
        return {
            "actuator_category": self.actuator_category,
            "actuator_instance_id": self.actuator_instance_id,
            "command_channels": list(self.command_channels),
            "interface": PROPULSION_ACTUATOR_BINDING_INTERFACE_ID,
            "module_catalog": self.module_catalog.to_dict(),
            "module_catalog_sha256": self.module_catalog_sha256,
            "prototype": self.prototype.to_dict(),
            "response_steps": self.response_steps,
            "scene_id": self.scene_id,
            "ship_id": self.ship_id,
            "startup_steps": self.startup_steps,
        }


def bind_compiled_outfit_propulsion(
    scene_id: str,
    ship_id: str,
    outfit: CompiledOutfit,
    catalog: ModulePrototypeCatalog,
) -> tuple[PropulsionActuatorBinding, ...]:
    """把已编译舾装中的每个推进执行器绑定到精确 v3 原型与命令通道。"""

    catalog_reference = ResourceReference(catalog.id, catalog.version)
    catalog_sha256 = canonical_sha256(catalog)
    if catalog.schema != MODULE_CATALOG_V3_SCHEMA_ID:
        raise ContractError(
            "propulsion_bridge.binding_catalog_interface",
            "$.catalog.schema",
            catalog.schema,
        )
    if (
        outfit.module_catalog_reference != catalog_reference
        or outfit.module_catalog_source_sha256 != catalog_sha256
    ):
        raise ContractError(
            "propulsion_bridge.binding_catalog_mismatch",
            "$.outfit",
            "编译舾装没有绑定当前精确 v3 目录",
        )
    instance_by_id = {item.id: item for item in outfit.instances}
    bindings: list[PropulsionActuatorBinding] = []
    for actuator in outfit.actuators:
        instance = instance_by_id.get(actuator.instance_id)
        if instance is None or instance.actuator != actuator:
            raise ContractError(
                "propulsion_bridge.binding_actuator_missing",
                "$.outfit.actuators",
                actuator.instance_id,
            )
        prototype = catalog.module(
            instance.prototype.reference,
            f"$.outfit.instances.{instance.id}.prototype",
        )
        startup_steps, response_steps = validate_propulsion_timing_capability(
            prototype.capability,
            actuator.category,
        )
        channels: set[str] = set()
        for capability in outfit.actuator_aggregation.main_directions:
            if any(
                use.instance_id == actuator.instance_id and use.output_scale > 0.0
                for use in capability.uses
            ):
                channels.add(capability.direction)
        for capability in outfit.actuator_aggregation.turning_directions:
            if any(
                use.instance_id == actuator.instance_id and use.output_scale > 0.0
                for use in capability.uses
            ):
                channels.add(
                    "left"
                    if capability.direction == "counterclockwise"
                    else "right"
                )
        try:
            bindings.append(
                PropulsionActuatorBinding(
                    scene_id,
                    ship_id,
                    actuator.instance_id,
                    actuator.category,
                    prototype.reference,
                    tuple(
                        item for item in PROPULSION_COMMAND_CHANNELS if item in channels
                    ),
                    startup_steps,
                    response_steps,
                    catalog_reference,
                    catalog_sha256,
                )
            )
        except ValueError as error:
            raise ContractError(
                "propulsion_bridge.binding_invariant",
                f"$.outfit.actuators.{actuator.instance_id}",
                str(error),
            ) from error
    expected_ids = tuple(sorted(item.instance_id for item in outfit.actuators))
    actual_ids = tuple(sorted(item.actuator_instance_id for item in bindings))
    if actual_ids != expected_ids or len(set(actual_ids)) != len(actual_ids):
        raise ContractError(
            "propulsion_bridge.binding_not_exact",
            "$.outfit.actuators",
            "推进执行器没有一一精确绑定",
        )
    return tuple(sorted(bindings, key=lambda item: item.actuator_instance_id))


@dataclass(frozen=True)
class TacticalScenePropulsionEvent:
    ship_id: str
    event: PropulsionStateEvent

    def __post_init__(self) -> None:
        if self.event.interface_id != PROPULSION_STATE_EVENT_INTERFACE_ID:
            raise ValueError("旧场景事件封装只接受 v1 推进事件")
        if not isinstance(self.ship_id, str) or not RESOURCE_ID_PATTERN.fullmatch(
            self.ship_id
        ):
            raise ValueError("ship_id 非法")

    @property
    def sort_key(self) -> tuple[int, str, str, int]:
        step, actuator, kind_index = self.event.sort_key
        return step, self.ship_id, actuator, kind_index

    @classmethod
    def parse(cls, value: Any, path: str) -> "TacticalScenePropulsionEvent":
        if not isinstance(value, dict) or set(value) != {
            "event",
            "interface",
            "ship_id",
        }:
            raise ContractError("object.keys", path, "场景推进事件字段集合不匹配")
        if value["interface"] != TACTICAL_SCENE_PROPULSION_EVENT_INTERFACE_ID:
            raise ContractError(
                "propulsion_bridge.scene_event_interface",
                f"{path}.interface",
                str(value["interface"]),
            )
        try:
            return cls(
                value["ship_id"],
                PropulsionStateEvent.parse(value["event"], f"{path}.event"),
            )
        except ValueError as error:
            raise ContractError(
                "propulsion_bridge.scene_event_invariant",
                path,
                str(error),
            ) from error

    def to_dict(self) -> dict[str, Any]:
        return {
            "event": self.event.to_dict(),
            "interface": TACTICAL_SCENE_PROPULSION_EVENT_INTERFACE_ID,
            "ship_id": self.ship_id,
        }


@dataclass(frozen=True)
class TacticalPropulsionStepResolutionEnvelope:
    base_resolution: TacticalSceneStepResolution
    propulsion_events: tuple[TacticalScenePropulsionEvent, ...]

    def __post_init__(self) -> None:
        scene_value = self.base_resolution.resulting_scene.to_dict()
        if scene_value["interface"] != D1_SCENE_INTERFACE_ID:
            raise ValueError("新结果接口必须绑定 d1 推进场景")
        if tuple(sorted(self.propulsion_events, key=lambda item: item.sort_key)) != (
            self.propulsion_events
        ):
            raise ValueError("推进事件必须按固定步、舰艇、执行器、事件类型稳定排序")
        if any(
            item.event.fixed_step_index
            != self.base_resolution.resulting_scene.fixed_step_index
            for item in self.propulsion_events
        ):
            raise ValueError("推进事件必须属于结果场景固定步边界")
        sort_keys = tuple(item.sort_key for item in self.propulsion_events)
        if len(set(sort_keys)) != len(sort_keys):
            raise ValueError("推进事件稳定键不得重复")
        ship_by_id = {
            item.ship_id: item for item in self.base_resolution.resulting_scene.ships
        }
        for item in self.propulsion_events:
            ship = ship_by_id.get(item.ship_id)
            if ship is None or ship.propulsion_state is None:
                raise ValueError("推进事件必须绑定结果场景中的推进舰艇")
            if item.event.actuator_instance_id not in {
                engine.actuator_instance_id
                for engine in ship.propulsion_state.engines
            }:
                raise ValueError("推进事件必须绑定同舰的已知执行器")

    def to_dict(self) -> dict[str, Any]:
        result = self.base_resolution.to_dict()
        result["interface"] = TACTICAL_PROPULSION_STEP_RESULT_INTERFACE_ID
        result["policy"] = TACTICAL_PROPULSION_STEP_RESULT_POLICY_ID
        result["propulsion_events"] = [
            item.to_dict() for item in self.propulsion_events
        ]
        return result


def build_propulsion_step_resolution_envelope(
    base_resolution: TacticalSceneStepResolution,
    propulsion_events: Iterable[TacticalScenePropulsionEvent],
) -> TacticalPropulsionStepResolutionEnvelope:
    """稳定收集推进事件；只构造输出合同，不调用场景推进器。"""

    events = tuple(sorted(propulsion_events, key=lambda item: item.sort_key))
    try:
        return TacticalPropulsionStepResolutionEnvelope(base_resolution, events)
    except ValueError as error:
        raise ContractError(
            "propulsion_bridge.step_result_invariant",
            "$",
            str(error),
        ) from error
