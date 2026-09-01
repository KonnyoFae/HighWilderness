"""《高天荒野》舰艇规范数据契约 v1alpha1 的无引擎实现。

当前可执行资源：MaterialCatalog、HullCoatingCatalog、ModulePrototypeCatalog、
HullBlueprint、OutfitPlan、SortieConfiguration、ShipInstanceSnapshot；后两者可保存
弹药库库存与武器待发弹初态/当前态，实例还可选保存自持设计与武器战术时间状态。
本模块只负责字段、单位、精确引用与确定性 JSON；几何、结构与舾装派生位于
《高天荒野舰艇无界面船壳编译器.py》和《高天荒野舰艇无界面舾装编译器.py》。
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path
import re
from typing import Any, Iterable


SCHEMA_ID = "gaotian.ship/v1alpha1"
MODULE_CATALOG_V2_SCHEMA_ID = "gaotian.module-prototype-catalog/v2"
COMBAT_SYSTEM_MODULE_CONTRACT_ID = "gaotian.combat-system-modules/v1alpha1"
RESOURCE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class ContractError(ValueError):
    def __init__(self, code: str, path: str, message: str):
        super().__init__(f"[{code}] {path}: {message}")
        self.code = code
        self.path = path
        self.message = message


def _object(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError("type.object", path, "必须是对象")
    return value


def _array(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise ContractError("type.array", path, "必须是数组")
    return value


def _string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value:
        raise ContractError("type.string", path, "必须是非空字符串")
    return value


def _resource_id(value: Any, path: str) -> str:
    result = _string(value, path)
    if not RESOURCE_ID_PATTERN.fullmatch(result):
        raise ContractError(
            "resource.id_invalid", path, "只能使用小写字母、数字、点、横线和下划线"
        )
    return result


def _sha256_hex(value: Any, path: str) -> str:
    result = _string(value, path)
    if not SHA256_PATTERN.fullmatch(result):
        raise ContractError("value.sha256", path, "必须是六十四位小写十六进制 SHA-256")
    return result


def _integer(value: Any, path: str, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractError("type.integer", path, "必须是整数")
    if minimum is not None and value < minimum:
        raise ContractError("value.minimum", path, f"不得小于 {minimum}")
    return value


def _number(value: Any, path: str, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError("type.number", path, "必须是数值")
    result = float(value)
    if not isfinite(result):
        raise ContractError("value.not_finite", path, "必须是有限数")
    if minimum is not None and result < minimum:
        raise ContractError("value.minimum", path, f"不得小于 {minimum}")
    return result


def _boolean(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise ContractError("type.boolean", path, "必须是布尔值")
    return value


def _keys(
    value: dict[str, Any], path: str, required: Iterable[str], optional: Iterable[str] = ()
) -> None:
    required_set = set(required)
    allowed = required_set | set(optional)
    missing = sorted(required_set - value.keys())
    extra = sorted(value.keys() - allowed)
    if missing:
        raise ContractError("object.missing_keys", path, f"缺少字段 {missing}")
    if extra:
        raise ContractError("object.extra_keys", path, f"未知字段 {extra}")


@dataclass(frozen=True)
class _FrozenJsonObject:
    """区分于数组元组的内部不可变 JSON 对象。"""

    items: tuple[tuple[str, Any], ...]


def _freeze_json_value(value: Any) -> Any:
    """深度冻结已通过契约校验的 JSON 值。"""

    if isinstance(value, dict):
        return _FrozenJsonObject(
            tuple(
                (key, _freeze_json_value(item))
                for key, item in sorted(value.items())
            )
        )
    if isinstance(value, list):
        return tuple(_freeze_json_value(item) for item in value)
    return value


def _thaw_json_value(value: Any) -> Any:
    """为序列化返回全新的标准 JSON 容器。"""

    if isinstance(value, _FrozenJsonObject):
        return {key: _thaw_json_value(item) for key, item in value.items}
    if isinstance(value, tuple):
        return [_thaw_json_value(item) for item in value]
    return value


@dataclass(frozen=True, order=True)
class ResourceReference:
    id: str
    version: int

    @classmethod
    def parse(cls, value: Any, path: str) -> "ResourceReference":
        obj = _object(value, path)
        _keys(obj, path, ("id", "version"))
        return cls(
            _resource_id(obj["id"], f"{path}.id"),
            _integer(obj["version"], f"{path}.version", 1),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "version": self.version}


@dataclass(frozen=True)
class StructureMaterial:
    reference: ResourceReference
    name: str
    density_kg_m3: float
    strength_coefficient: float
    durability_coefficient: float
    cost_coefficient: float
    work_difficulty: float


@dataclass(frozen=True)
class BaseArmorMaterial:
    reference: ResourceReference
    name: str
    density_kg_m3: float
    protection_coefficient: float
    local_durability_coefficient: float
    shell_strength_coefficient: float
    cost_coefficient: float
    work_difficulty: float


@dataclass(frozen=True)
class HullCoatingDefinition:
    reference: ResourceReference
    name: str
    balance_status: str
    rcs_multiplier: float | None
    runtime_usable: bool
    default_for_new_build: bool

    @classmethod
    def parse(cls, value: Any, path: str) -> "HullCoatingDefinition":
        obj = _object(value, path)
        _keys(
            obj,
            path,
            (
                "id",
                "version",
                "name",
                "balance_status",
                "rcs_multiplier",
                "runtime_usable",
                "default_for_new_build",
            ),
        )
        status = _string(obj["balance_status"], f"{path}.balance_status")
        if status not in {"baseline_locked", "awaiting_joint_calibration"}:
            raise ContractError("coating.balance_status", f"{path}.balance_status", status)
        runtime_usable = _boolean(obj["runtime_usable"], f"{path}.runtime_usable")
        default_for_new_build = _boolean(
            obj["default_for_new_build"], f"{path}.default_for_new_build"
        )
        multiplier_value = obj["rcs_multiplier"]
        multiplier = (
            None
            if multiplier_value is None
            else _number(multiplier_value, f"{path}.rcs_multiplier", 0.0)
        )
        if runtime_usable and (multiplier is None or multiplier <= 0.0):
            raise ContractError(
                "coating.runtime_multiplier_missing",
                f"{path}.rcs_multiplier",
                "可装备涂料必须具有正数 RCS 倍率",
            )
        if not runtime_usable and multiplier is not None:
            raise ContractError(
                "coating.unresolved_multiplier_present",
                f"{path}.rcs_multiplier",
                "未完成标定的不可装备涂料不得填写临时倍率",
            )
        if default_for_new_build and not runtime_usable:
            raise ContractError(
                "coating.default_unusable", path, "新造默认涂料必须可以装备"
            )
        return cls(
            reference=ResourceReference(
                _resource_id(obj["id"], f"{path}.id"),
                _integer(obj["version"], f"{path}.version", 1),
            ),
            name=_string(obj["name"], f"{path}.name"),
            balance_status=status,
            rcs_multiplier=multiplier,
            runtime_usable=runtime_usable,
            default_for_new_build=default_for_new_build,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "balance_status": self.balance_status,
            "default_for_new_build": self.default_for_new_build,
            "id": self.reference.id,
            "name": self.name,
            "rcs_multiplier": self.rcs_multiplier,
            "runtime_usable": self.runtime_usable,
            "version": self.reference.version,
        }


@dataclass(frozen=True)
class HullCoatingCatalog:
    id: str
    version: int
    name: str
    coatings: tuple[HullCoatingDefinition, ...]

    @classmethod
    def parse(cls, resource: Any, path: str = "$") -> "HullCoatingCatalog":
        obj = _object(resource, path)
        _keys(obj, path, ("schema", "kind", "id", "version", "name", "coatings"))
        if obj["schema"] != SCHEMA_ID:
            raise ContractError("schema.unsupported", f"{path}.schema", str(obj["schema"]))
        if obj["kind"] != "HullCoatingCatalog":
            raise ContractError(
                "resource.kind_mismatch", f"{path}.kind", "必须是 HullCoatingCatalog"
            )
        coating_values = _array(obj["coatings"], f"{path}.coatings")
        if not coating_values:
            raise ContractError("array.empty", f"{path}.coatings", "涂料目录不能为空")
        coatings = tuple(
            HullCoatingDefinition.parse(item, f"{path}.coatings[{index}]")
            for index, item in enumerate(coating_values)
        )
        references = [coating.reference for coating in coatings]
        if len(set(references)) != len(references):
            raise ContractError("resource.duplicate", f"{path}.coatings", "涂料精确引用不得重复")
        defaults = [coating for coating in coatings if coating.default_for_new_build]
        if len(defaults) != 1:
            raise ContractError(
                "coating.default_count", f"{path}.coatings", "必须恰好有一种新造默认涂料"
            )
        if defaults[0].rcs_multiplier != 1.0:
            raise ContractError(
                "coating.default_multiplier",
                f"{path}.coatings",
                "普通新造默认涂料必须使用 1.0 基准倍率",
            )
        return cls(
            id=_resource_id(obj["id"], f"{path}.id"),
            version=_integer(obj["version"], f"{path}.version", 1),
            name=_string(obj["name"], f"{path}.name"),
            coatings=tuple(sorted(coatings, key=lambda coating: coating.reference)),
        )

    def coating(self, reference: ResourceReference, path: str = "$") -> HullCoatingDefinition:
        for coating in self.coatings:
            if coating.reference == reference:
                return coating
        raise ContractError("resource.reference_missing", path, f"找不到船体涂料 {reference}")

    @property
    def default(self) -> HullCoatingDefinition:
        return next(coating for coating in self.coatings if coating.default_for_new_build)

    def to_dict(self) -> dict[str, Any]:
        return {
            "coatings": [coating.to_dict() for coating in self.coatings],
            "id": self.id,
            "kind": "HullCoatingCatalog",
            "name": self.name,
            "schema": SCHEMA_ID,
            "version": self.version,
        }


MODULE_CATEGORIES = {
    "ammunition_magazine",
    "cic",
    "lift_fuel_tank",
    "main_engine",
    "maneuver_thruster",
    "generator",
    "damage_control",
    "crew_quarters",
    "remote_core",
    "cargo_hold",
    "fire_control",
    "sensor",
    "weapon",
}
CREW_TYPES = {
    "officer",
    "technical_officer",
    "pilot",
    "veteran_damage_control",
    "ordinary",
}
DEPARTURE_MINIMUM_CATEGORIES = {
    "cic",
    "main_engine",
    "maneuver_thruster",
    "generator",
    "damage_control",
}
POWER_CONSUMER_CATEGORIES = (
    "damage_control",
    "weapons_and_active_defense",
    "fire_control",
    "sensors",
)
POWER_CONSUMER_CATEGORY_SET = set(POWER_CONSUMER_CATEGORIES)
BALANCE_STATUSES = {"contract_fixture", "prototype_unbalanced", "balance_reference"}
MODULE_FUNCTIONS_BY_CATEGORY = {
    "ammunition_magazine": ("ammunition.feed", "ammunition.inventory"),
    "cargo_hold": ("cargo.inventory",),
    "cic": ("cic.basic_control",),
    "crew_quarters": ("crew_quarters.habitation",),
    "damage_control": ("damage_control.firefighting",),
    "generator": ("generator.regulation",),
    "fire_control": ("fire_control.guidance", "fire_control.solution"),
    "lift_fuel_tank": ("lift_tank.lift",),
    "main_engine": ("engine.throttle",),
    "maneuver_thruster": ("thruster.throttle",),
    "remote_core": ("remote_core.command_link",),
    "sensor": ("sensor.search", "sensor.track"),
    "weapon": ("weapon.aim", "weapon.fire", "weapon.reload"),
}

WEAPON_CLASSES = {"active_defense", "gun", "missile_launcher"}
ENGAGEMENT_DOMAINS = {"aircraft", "fixed_installation", "missile", "ship"}
FIRE_CONTROL_REQUIREMENTS = {"none", "solution", "continuous_guidance"}
SENSOR_CHANNELS = {"infrared", "optical", "radar"}
SENSOR_MODES = {"active_search", "fire_control_lock", "passive_search", "track"}
COMBAT_POWER_CATEGORY_BY_MODULE_CATEGORY = {
    "weapon": "weapons_and_active_defense",
    "fire_control": "fire_control",
    "sensor": "sensors",
}


HalfCell = tuple[int, int]


def _half_cells(
    value: Any, path: str, *, require_centered_bounds: bool
) -> tuple[HalfCell, ...]:
    result: list[HalfCell] = []
    for index, point_value in enumerate(_array(value, path)):
        point_path = f"{path}[{index}]"
        point = _array(point_value, point_path)
        if len(point) != 2:
            raise ContractError("module.half_cell_size", point_path, "半格坐标必须有两个分量")
        result.append(
            (
                _integer(point[0], f"{point_path}[0]"),
                _integer(point[1], f"{point_path}[1]"),
            )
        )
    if len(set(result)) != len(result):
        raise ContractError("module.half_cell_duplicate", path, "半格坐标不得重复")
    if require_centered_bounds and result:
        xs = [point[0] for point in result]
        ys = [point[1] for point in result]
        if min(xs) + max(xs) != 0 or min(ys) + max(ys) != 0:
            raise ContractError(
                "module.footprint_not_centered",
                path,
                "占用轮廓的包围盒中心必须是模块锚点 [0,0]",
            )
    return tuple(sorted(result, key=lambda point: (point[1], point[0])))


def _validate_half_cell_parity(values: tuple[HalfCell, ...], path: str) -> None:
    if not values:
        return
    x_parity = values[0][0] % 2
    y_parity = values[0][1] % 2
    if any(point[0] % 2 != x_parity or point[1] % 2 != y_parity for point in values):
        raise ContractError(
            "module.half_cell_parity",
            path,
            "同一局部坐标框架中的格心相对锚点必须具有一致的半格奇偶性",
        )


@dataclass(frozen=True)
class ModuleInstallationGeometry:
    internal_footprint_half_cells: tuple[HalfCell, ...]
    internal_deck_span: int
    top_footprint_half_cells: tuple[HalfCell, ...]
    top_deck_offset: int
    top_clearance_half_cells: tuple[HalfCell, ...]
    side_mount_length_steps: int
    side_external_footprint_half_cells: tuple[HalfCell, ...]
    side_clearance_half_cells: tuple[HalfCell, ...]
    exhaust_clearance_half_cells: tuple[HalfCell, ...]
    host_slot: str | None
    provided_slots: tuple[str, ...]
    allowed_rotations_deg: tuple[int, ...]
    deck_rule: str

    @classmethod
    def parse(cls, value: Any, path: str) -> "ModuleInstallationGeometry":
        obj = _object(value, path)
        _keys(
            obj,
            path,
            (
                "internal_footprint_half_cells",
                "internal_deck_span",
                "top_footprint_half_cells",
                "top_deck_offset",
                "top_clearance_half_cells",
                "side_mount_length_steps",
                "side_external_footprint_half_cells",
                "side_clearance_half_cells",
                "exhaust_clearance_half_cells",
                "host_slot",
                "provided_slots",
                "allowed_rotations_deg",
                "deck_rule",
            ),
        )
        internal = _half_cells(
            obj["internal_footprint_half_cells"],
            f"{path}.internal_footprint_half_cells",
            require_centered_bounds=True,
        )
        top = _half_cells(
            obj["top_footprint_half_cells"],
            f"{path}.top_footprint_half_cells",
            require_centered_bounds=True,
        )
        top_clearance = _half_cells(
            obj["top_clearance_half_cells"],
            f"{path}.top_clearance_half_cells",
            require_centered_bounds=False,
        )
        side = _half_cells(
            obj["side_external_footprint_half_cells"],
            f"{path}.side_external_footprint_half_cells",
            require_centered_bounds=True,
        )
        side_clearance = _half_cells(
            obj["side_clearance_half_cells"],
            f"{path}.side_clearance_half_cells",
            require_centered_bounds=False,
        )
        exhaust = _half_cells(
            obj["exhaust_clearance_half_cells"],
            f"{path}.exhaust_clearance_half_cells",
            require_centered_bounds=False,
        )
        internal_span = _integer(
            obj["internal_deck_span"], f"{path}.internal_deck_span", 0
        )
        top_offset = _integer(obj["top_deck_offset"], f"{path}.top_deck_offset", 0)
        side_steps = _integer(
            obj["side_mount_length_steps"], f"{path}.side_mount_length_steps", 0
        )
        if bool(internal) != (internal_span > 0):
            raise ContractError(
                "module.internal_span_mismatch",
                path,
                "内部轮廓与大于零的跨层数必须同时存在",
            )
        if not top and top_offset != 0:
            raise ContractError(
                "module.top_offset_without_footprint", path, "没有顶挂轮廓时层偏移必须为零"
            )
        if bool(side) != (side_steps > 0):
            raise ContractError(
                "module.side_span_mismatch",
                path,
                "侧挂外形与大于零的边步长必须同时存在",
            )
        if set(top) & set(top_clearance):
            raise ContractError("module.clearance_overlap", path, "顶挂本体与顶挂净空不得重叠")
        if set(side) & set(side_clearance):
            raise ContractError("module.clearance_overlap", path, "侧挂本体与侧挂净空不得重叠")
        host_value = obj["host_slot"]
        host_slot = None if host_value is None else _resource_id(host_value, f"{path}.host_slot")
        provided_raw = _array(obj["provided_slots"], f"{path}.provided_slots")
        provided_slots = tuple(
            sorted(
                _resource_id(item, f"{path}.provided_slots[{index}]")
                for index, item in enumerate(provided_raw)
            )
        )
        if len(set(provided_slots)) != len(provided_slots):
            raise ContractError("module.slot_duplicate", f"{path}.provided_slots", "槽位不得重复")
        if host_slot is not None and (internal or top or side):
            raise ContractError(
                "module.hosted_geometry_conflict", path, "嵌入槽位模块不得再直接占用船壳安装空间"
            )
        if host_slot is None and not (internal or top or side):
            raise ContractError("module.installation_empty", path, "模块必须占用空间或嵌入宿主槽位")
        rotations_raw = _array(obj["allowed_rotations_deg"], f"{path}.allowed_rotations_deg")
        rotations = tuple(
            sorted(
                _integer(item, f"{path}.allowed_rotations_deg[{index}]")
                for index, item in enumerate(rotations_raw)
            )
        )
        if not rotations or len(set(rotations)) != len(rotations) or any(
            rotation not in {0, 90, 180, 270} for rotation in rotations
        ):
            raise ContractError(
                "module.rotations_invalid",
                f"{path}.allowed_rotations_deg",
                "必须是 0/90/180/270 的非空无重复子集",
            )
        deck_rule = _string(obj["deck_rule"], f"{path}.deck_rule")
        if deck_rule not in {"any", "base_only", "local_exposed_top"}:
            raise ContractError("module.deck_rule", f"{path}.deck_rule", deck_rule)
        _validate_half_cell_parity(internal, f"{path}.internal_footprint_half_cells")
        _validate_half_cell_parity(top + top_clearance, f"{path}.top_geometry")
        _validate_half_cell_parity(side + side_clearance, f"{path}.side_geometry")
        _validate_half_cell_parity(exhaust, f"{path}.exhaust_clearance_half_cells")
        return cls(
            internal,
            internal_span,
            top,
            top_offset,
            top_clearance,
            side_steps,
            side,
            side_clearance,
            exhaust,
            host_slot,
            provided_slots,
            rotations,
            deck_rule,
        )

    def to_dict(self) -> dict[str, Any]:
        def points(values: tuple[HalfCell, ...]) -> list[list[int]]:
            return [list(point) for point in values]

        return {
            "allowed_rotations_deg": list(self.allowed_rotations_deg),
            "deck_rule": self.deck_rule,
            "exhaust_clearance_half_cells": points(self.exhaust_clearance_half_cells),
            "host_slot": self.host_slot,
            "internal_deck_span": self.internal_deck_span,
            "internal_footprint_half_cells": points(self.internal_footprint_half_cells),
            "provided_slots": list(self.provided_slots),
            "side_clearance_half_cells": points(self.side_clearance_half_cells),
            "side_external_footprint_half_cells": points(
                self.side_external_footprint_half_cells
            ),
            "side_mount_length_steps": self.side_mount_length_steps,
            "top_clearance_half_cells": points(self.top_clearance_half_cells),
            "top_deck_offset": self.top_deck_offset,
            "top_footprint_half_cells": points(self.top_footprint_half_cells),
        }


@dataclass(frozen=True)
class ModulePowerProfile:
    generation_kw: float
    standby_load_kw: float
    active_load_kw: float
    consumer_category: str | None

    @classmethod
    def parse(cls, value: Any, path: str) -> "ModulePowerProfile":
        obj = _object(value, path)
        _keys(
            obj,
            path,
            (
                "generation_kw",
                "standby_load_kw",
                "active_load_kw",
                "consumer_category",
            ),
        )
        generation = _number(obj["generation_kw"], f"{path}.generation_kw", 0.0)
        standby = _number(obj["standby_load_kw"], f"{path}.standby_load_kw", 0.0)
        active = _number(obj["active_load_kw"], f"{path}.active_load_kw", 0.0)
        if active < standby:
            raise ContractError("module.power_active_below_standby", path, "工作负载不得小于待机负载")
        category_value = obj["consumer_category"]
        category = (
            None
            if category_value is None
            else _string(category_value, f"{path}.consumer_category")
        )
        if category is not None and category not in POWER_CONSUMER_CATEGORY_SET:
            raise ContractError(
                "module.power_category",
                f"{path}.consumer_category",
                category,
            )
        if active > 0.0 and category is None:
            raise ContractError(
                "module.power_category_missing",
                path,
                "存在用电负载时必须指定稳定供电类别",
            )
        if active == 0.0 and category is not None:
            raise ContractError(
                "module.power_category_without_load",
                path,
                "无负载模块不得占用供电类别",
            )
        return cls(generation, standby, active, category)

    def to_dict(self) -> dict[str, Any]:
        return {
            "active_load_kw": self.active_load_kw,
            "consumer_category": self.consumer_category,
            "generation_kw": self.generation_kw,
            "standby_load_kw": self.standby_load_kw,
        }


@dataclass(frozen=True)
class DamageResponsePoint:
    durability_fraction: float
    output_fraction: float

    @classmethod
    def parse(cls, value: Any, path: str) -> "DamageResponsePoint":
        obj = _object(value, path)
        _keys(obj, path, ("durability_fraction", "output_fraction"))
        durability = _number(
            obj["durability_fraction"], f"{path}.durability_fraction", 0.0
        )
        output = _number(obj["output_fraction"], f"{path}.output_fraction", 0.0)
        if durability > 1.0 or output > 1.0:
            raise ContractError(
                "module.damage_response_fraction",
                path,
                "耐久比例与输出比例都必须位于 0～1",
            )
        return cls(durability, output)

    def to_dict(self) -> dict[str, float]:
        return {
            "durability_fraction": self.durability_fraction,
            "output_fraction": self.output_fraction,
        }


@dataclass(frozen=True)
class ModuleFunctionDamageResponse:
    function_id: str
    model: str
    points: tuple[DamageResponsePoint, ...]

    @classmethod
    def parse(cls, value: Any, path: str) -> "ModuleFunctionDamageResponse":
        obj = _object(value, path)
        _keys(obj, path, ("function_id", "model", "points"))
        function_id = _resource_id(obj["function_id"], f"{path}.function_id")
        model = _string(obj["model"], f"{path}.model")
        if model not in {"binary_until_destroyed", "piecewise_linear", "stepwise"}:
            raise ContractError("module.damage_response_model", f"{path}.model", model)
        points = tuple(
            DamageResponsePoint.parse(item, f"{path}.points[{index}]")
            for index, item in enumerate(_array(obj["points"], f"{path}.points"))
        )
        if model == "binary_until_destroyed":
            if points:
                raise ContractError(
                    "module.binary_damage_response_has_points",
                    f"{path}.points",
                    "归零失效模型不得额外填写曲线点",
                )
        else:
            if len(points) < 2:
                raise ContractError(
                    "module.damage_response_points_missing",
                    f"{path}.points",
                    "分段模型至少需要起点与终点",
                )
            if (
                abs(points[0].durability_fraction) > 1.0e-9
                or abs(points[0].output_fraction) > 1.0e-9
                or abs(points[-1].durability_fraction - 1.0) > 1.0e-9
                or abs(points[-1].output_fraction - 1.0) > 1.0e-9
            ):
                raise ContractError(
                    "module.damage_response_endpoints",
                    f"{path}.points",
                    "分段模型必须从 (0,0) 开始并以 (1,1) 结束",
                )
            for previous, current in zip(points, points[1:]):
                if current.durability_fraction <= previous.durability_fraction:
                    raise ContractError(
                        "module.damage_response_durability_order",
                        f"{path}.points",
                        "耐久比例必须严格递增",
                    )
                if current.output_fraction < previous.output_fraction:
                    raise ContractError(
                        "module.damage_response_output_order",
                        f"{path}.points",
                        "输出比例不得随耐久增加而下降",
                    )
        return cls(function_id, model, points)

    def output_fraction(self, durability_fraction: float) -> float:
        fraction = max(0.0, min(1.0, durability_fraction))
        if fraction <= 1.0e-9:
            return 0.0
        if self.model == "binary_until_destroyed":
            return 1.0
        if self.model == "stepwise":
            result = self.points[0].output_fraction
            for point in self.points[1:]:
                if fraction + 1.0e-9 < point.durability_fraction:
                    break
                result = point.output_fraction
            return result
        for lower, upper in zip(self.points, self.points[1:]):
            if fraction <= upper.durability_fraction + 1.0e-9:
                span = upper.durability_fraction - lower.durability_fraction
                position = (fraction - lower.durability_fraction) / span
                return lower.output_fraction + position * (
                    upper.output_fraction - lower.output_fraction
                )
        return self.points[-1].output_fraction

    def to_dict(self) -> dict[str, Any]:
        return {
            "function_id": self.function_id,
            "model": self.model,
            "points": [point.to_dict() for point in self.points],
        }


@dataclass(frozen=True)
class ModuleCrewRequirement:
    crew_type: str
    minimum_operating: int
    standard: int

    @classmethod
    def parse(cls, value: Any, path: str) -> "ModuleCrewRequirement":
        obj = _object(value, path)
        _keys(obj, path, ("crew_type", "minimum_operating", "standard"))
        crew_type = _string(obj["crew_type"], f"{path}.crew_type")
        if crew_type not in CREW_TYPES:
            raise ContractError("module.crew_type", f"{path}.crew_type", crew_type)
        minimum = _integer(obj["minimum_operating"], f"{path}.minimum_operating", 0)
        standard = _integer(obj["standard"], f"{path}.standard", 0)
        if minimum > standard:
            raise ContractError("module.crew_minimum_above_standard", path, "最低工作人数不得超过标准人数")
        return cls(crew_type, minimum, standard)

    def to_dict(self) -> dict[str, Any]:
        return {
            "crew_type": self.crew_type,
            "minimum_operating": self.minimum_operating,
            "standard": self.standard,
        }


@dataclass(frozen=True)
class ModuleAutomationProfile:
    level: str
    automated_functions: tuple[str, ...]
    unmanned_variant: ResourceReference | None
    engineering_microclusters_required: int | None

    @classmethod
    def parse(cls, value: Any, path: str) -> "ModuleAutomationProfile":
        obj = _object(value, path)
        _keys(
            obj,
            path,
            (
                "level",
                "automated_functions",
                "unmanned_variant",
                "engineering_microclusters_required",
            ),
        )
        level = _string(obj["level"], f"{path}.level")
        if level not in {"manual", "partial", "full"}:
            raise ContractError("module.automation_level", f"{path}.level", level)
        functions_raw = _array(obj["automated_functions"], f"{path}.automated_functions")
        functions = tuple(
            sorted(
                _resource_id(item, f"{path}.automated_functions[{index}]")
                for index, item in enumerate(functions_raw)
            )
        )
        if len(set(functions)) != len(functions):
            raise ContractError("module.automation_function_duplicate", path, "自动化子功能不得重复")
        if level == "manual" and functions:
            raise ContractError("module.manual_has_automation", path, "手动模块不得列出自动化子功能")
        if level == "partial" and not functions:
            raise ContractError("module.partial_without_automation", path, "部分自动化必须列出子功能")
        variant_value = obj["unmanned_variant"]
        variant = None if variant_value is None else ResourceReference.parse(
            variant_value, f"{path}.unmanned_variant"
        )
        cost_value = obj["engineering_microclusters_required"]
        cost = None if cost_value is None else _integer(
            cost_value, f"{path}.engineering_microclusters_required", 1
        )
        if (variant is None) != (cost is None):
            raise ContractError(
                "module.unmanned_conversion_incomplete",
                path,
                "无人改进版引用与工程微机团块成本必须同时填写或同时为空",
            )
        return cls(level, functions, variant, cost)

    def to_dict(self) -> dict[str, Any]:
        return {
            "automated_functions": list(self.automated_functions),
            "engineering_microclusters_required": self.engineering_microclusters_required,
            "level": self.level,
            "unmanned_variant": None if self.unmanned_variant is None else self.unmanned_variant.to_dict(),
        }


@dataclass(frozen=True)
class ModuleCapability:
    kind: str
    values: tuple[tuple[str, Any], ...]

    @classmethod
    def parse(
        cls,
        value: Any,
        path: str,
        *,
        propulsion_capability_version: int = 1,
    ) -> "ModuleCapability":
        obj = _object(value, path)
        kind = _string(obj.get("kind"), f"{path}.kind")
        if kind not in MODULE_CATEGORIES:
            raise ContractError("module.category", f"{path}.kind", kind)
        parsed: dict[str, Any] = {"kind": kind}
        if kind == "cic":
            _keys(obj, path, ("kind", "fixed_to_cic_origin"))
            if not _boolean(obj["fixed_to_cic_origin"], f"{path}.fixed_to_cic_origin"):
                raise ContractError("module.cic_not_fixed", path, "CIC 必须固定在原点")
            parsed["fixed_to_cic_origin"] = True
        elif kind == "lift_fuel_tank":
            _keys(obj, path, ("kind", "lift_force_n", "fuel_capacity_units"))
            parsed["lift_force_n"] = _number(obj["lift_force_n"], f"{path}.lift_force_n", 0.0)
            parsed["fuel_capacity_units"] = _number(
                obj["fuel_capacity_units"], f"{path}.fuel_capacity_units", 0.0
            )
            if parsed["lift_force_n"] <= 0.0 or parsed["fuel_capacity_units"] <= 0.0:
                raise ContractError(
                    "module.lift_tank_output", path, "升力与燃料容量都必须是正数"
                )
        elif kind in {"main_engine", "maneuver_thruster"}:
            if propulsion_capability_version == 1:
                _keys(
                    obj,
                    path,
                    (
                        "kind",
                        "thrust_n",
                        "fuel_units_per_s",
                        "response_time_s",
                        "local_thrust_axis",
                    ),
                )
            elif propulsion_capability_version == 2:
                _keys(
                    obj,
                    path,
                    (
                        "kind",
                        "thrust_n",
                        "fuel_units_per_s",
                        "startup_time_s",
                        "response_time_s",
                        "local_thrust_axis",
                    ),
                )
                parsed["startup_time_s"] = _number(
                    obj["startup_time_s"],
                    f"{path}.startup_time_s",
                    0.0,
                )
                if (
                    kind == "main_engine"
                    and parsed["startup_time_s"] <= 0.0
                ):
                    raise ContractError(
                        "module.main_engine_startup_time",
                        f"{path}.startup_time_s",
                        "主发动机启动时间必须是正数",
                    )
                if (
                    kind == "maneuver_thruster"
                    and parsed["startup_time_s"] != 0.0
                ):
                    raise ContractError(
                        "module.maneuver_thruster_startup_time",
                        f"{path}.startup_time_s",
                        "首轮姿态推进器启动时间必须严格为 0",
                    )
            else:
                raise ContractError(
                    "module.propulsion_capability_version",
                    path,
                    str(propulsion_capability_version),
                )
            parsed["thrust_n"] = _number(obj["thrust_n"], f"{path}.thrust_n", 0.0)
            parsed["fuel_units_per_s"] = _number(
                obj["fuel_units_per_s"], f"{path}.fuel_units_per_s", 0.0
            )
            parsed["response_time_s"] = _number(
                obj["response_time_s"], f"{path}.response_time_s", 0.0
            )
            if parsed["thrust_n"] <= 0.0 or parsed["response_time_s"] <= 0.0:
                raise ContractError(
                    "module.engine_output", path, "推力与响应时间都必须是正数"
                )
            axis = _string(obj["local_thrust_axis"], f"{path}.local_thrust_axis")
            if axis != "+Y":
                raise ContractError("module.thrust_axis", f"{path}.local_thrust_axis", "原型局部推力轴固定为 +Y")
            parsed["local_thrust_axis"] = axis
        elif kind == "generator":
            _keys(obj, path, ("kind",))
        elif kind == "damage_control":
            _keys(obj, path, ("kind", "team_capacity", "simultaneous_incidents"))
            teams = _integer(obj["team_capacity"], f"{path}.team_capacity", 1)
            simultaneous = _integer(
                obj["simultaneous_incidents"], f"{path}.simultaneous_incidents", 1
            )
            if simultaneous > teams:
                raise ContractError("module.damage_control_capacity", path, "同时处理数不得超过队伍容量")
            parsed["team_capacity"] = teams
            parsed["simultaneous_incidents"] = simultaneous
        elif kind == "crew_quarters":
            _keys(obj, path, ("kind", "capacities"))
            capacities: list[dict[str, Any]] = []
            seen: set[str] = set()
            for index, item_value in enumerate(_array(obj["capacities"], f"{path}.capacities")):
                item_path = f"{path}.capacities[{index}]"
                item = _object(item_value, item_path)
                _keys(item, item_path, ("crew_type", "capacity"))
                crew_type = _string(item["crew_type"], f"{item_path}.crew_type")
                if crew_type not in CREW_TYPES:
                    raise ContractError("module.crew_type", f"{item_path}.crew_type", crew_type)
                if crew_type in seen:
                    raise ContractError("module.crew_capacity_duplicate", item_path, crew_type)
                seen.add(crew_type)
                capacities.append(
                    {"capacity": _integer(item["capacity"], f"{item_path}.capacity", 1), "crew_type": crew_type}
                )
            if not capacities:
                raise ContractError("array.empty", f"{path}.capacities", "人员舱必须提供至少一种容量")
            parsed["capacities"] = sorted(capacities, key=lambda item: item["crew_type"])
        elif kind == "remote_core":
            _keys(obj, path, ("kind",))
        elif kind == "cargo_hold":
            _keys(obj, path, ("kind", "bulk_cargo_capacity_kg"))
            capacity = _number(
                obj["bulk_cargo_capacity_kg"],
                f"{path}.bulk_cargo_capacity_kg",
                0.0,
            )
            if capacity <= 0.0:
                raise ContractError(
                    "module.cargo_capacity", path, "大宗货物容量必须是正数"
                )
            parsed["bulk_cargo_capacity_kg"] = capacity
        elif kind == "weapon":
            _keys(
                obj,
                path,
                (
                    "kind",
                    "weapon_class",
                    "engagement_domains",
                    "compatible_munition_ids",
                    "ready_round_capacity",
                    "minimum_range_m",
                    "maximum_range_m",
                    "fire_control_requirement",
                ),
            )
            weapon_class = _string(obj["weapon_class"], f"{path}.weapon_class")
            if weapon_class not in WEAPON_CLASSES:
                raise ContractError(
                    "module.weapon_class", f"{path}.weapon_class", weapon_class
                )
            domains = tuple(
                sorted(
                    _string(item, f"{path}.engagement_domains[{index}]")
                    for index, item in enumerate(
                        _array(obj["engagement_domains"], f"{path}.engagement_domains")
                    )
                )
            )
            if not domains or len(set(domains)) != len(domains) or any(
                item not in ENGAGEMENT_DOMAINS for item in domains
            ):
                raise ContractError(
                    "module.engagement_domains",
                    f"{path}.engagement_domains",
                    "目标域必须非空、不得重复且只能使用规范枚举",
                )
            munitions = tuple(
                sorted(
                    _resource_id(item, f"{path}.compatible_munition_ids[{index}]")
                    for index, item in enumerate(
                        _array(
                            obj["compatible_munition_ids"],
                            f"{path}.compatible_munition_ids",
                        )
                    )
                )
            )
            if not munitions or len(set(munitions)) != len(munitions):
                raise ContractError(
                    "module.compatible_munitions",
                    f"{path}.compatible_munition_ids",
                    "兼容弹药标识必须非空且不得重复",
                )
            minimum_range = _number(
                obj["minimum_range_m"], f"{path}.minimum_range_m", 0.0
            )
            maximum_range = _number(
                obj["maximum_range_m"], f"{path}.maximum_range_m", 0.0
            )
            if maximum_range <= minimum_range:
                raise ContractError(
                    "module.weapon_range",
                    path,
                    "最大射程必须大于最小射程",
                )
            fire_control_requirement = _string(
                obj["fire_control_requirement"],
                f"{path}.fire_control_requirement",
            )
            if fire_control_requirement not in FIRE_CONTROL_REQUIREMENTS:
                raise ContractError(
                    "module.fire_control_requirement",
                    f"{path}.fire_control_requirement",
                    fire_control_requirement,
                )
            parsed.update(
                {
                    "compatible_munition_ids": list(munitions),
                    "engagement_domains": list(domains),
                    "fire_control_requirement": fire_control_requirement,
                    "maximum_range_m": maximum_range,
                    "minimum_range_m": minimum_range,
                    "ready_round_capacity": _integer(
                        obj["ready_round_capacity"],
                        f"{path}.ready_round_capacity",
                        1,
                    ),
                    "weapon_class": weapon_class,
                }
            )
        elif kind == "ammunition_magazine":
            _keys(
                obj,
                path,
                (
                    "kind",
                    "capacity_units",
                    "compatible_munition_ids",
                    "inventory_scope",
                ),
            )
            munitions = tuple(
                sorted(
                    _resource_id(item, f"{path}.compatible_munition_ids[{index}]")
                    for index, item in enumerate(
                        _array(
                            obj["compatible_munition_ids"],
                            f"{path}.compatible_munition_ids",
                        )
                    )
                )
            )
            if not munitions or len(set(munitions)) != len(munitions):
                raise ContractError(
                    "module.compatible_munitions",
                    f"{path}.compatible_munition_ids",
                    "兼容弹药标识必须非空且不得重复",
                )
            scope = _string(obj["inventory_scope"], f"{path}.inventory_scope")
            if scope != "ship_shared":
                raise ContractError(
                    "module.ammunition_inventory_scope",
                    f"{path}.inventory_scope",
                    "首版弹药池必须为全舰共享",
                )
            parsed.update(
                {
                    "capacity_units": _integer(
                        obj["capacity_units"], f"{path}.capacity_units", 1
                    ),
                    "compatible_munition_ids": list(munitions),
                    "inventory_scope": scope,
                }
            )
        elif kind == "fire_control":
            _keys(
                obj,
                path,
                (
                    "kind",
                    "supported_requirements",
                    "simultaneous_channels",
                    "maximum_lock_range_m",
                ),
            )
            requirements = tuple(
                sorted(
                    _string(item, f"{path}.supported_requirements[{index}]")
                    for index, item in enumerate(
                        _array(
                            obj["supported_requirements"],
                            f"{path}.supported_requirements",
                        )
                    )
                )
            )
            if not requirements or len(set(requirements)) != len(requirements) or any(
                item == "none" or item not in FIRE_CONTROL_REQUIREMENTS
                for item in requirements
            ):
                raise ContractError(
                    "module.fire_control_supported_requirements",
                    f"{path}.supported_requirements",
                    "火控能力必须非空、不得重复，且不能把 none 列为能力",
                )
            maximum_lock_range = _number(
                obj["maximum_lock_range_m"],
                f"{path}.maximum_lock_range_m",
                0.0,
            )
            if maximum_lock_range <= 0.0:
                raise ContractError(
                    "module.fire_control_range",
                    f"{path}.maximum_lock_range_m",
                    "火控锁定距离必须是正数",
                )
            parsed.update(
                {
                    "maximum_lock_range_m": maximum_lock_range,
                    "simultaneous_channels": _integer(
                        obj["simultaneous_channels"],
                        f"{path}.simultaneous_channels",
                        1,
                    ),
                    "supported_requirements": list(requirements),
                }
            )
        elif kind == "sensor":
            _keys(
                obj,
                path,
                (
                    "kind",
                    "sensor_channel",
                    "supported_modes",
                    "maximum_instrumented_range_m",
                ),
            )
            channel = _string(obj["sensor_channel"], f"{path}.sensor_channel")
            if channel not in SENSOR_CHANNELS:
                raise ContractError(
                    "module.sensor_channel", f"{path}.sensor_channel", channel
                )
            modes = tuple(
                sorted(
                    _string(item, f"{path}.supported_modes[{index}]")
                    for index, item in enumerate(
                        _array(obj["supported_modes"], f"{path}.supported_modes")
                    )
                )
            )
            if not modes or len(set(modes)) != len(modes) or any(
                item not in SENSOR_MODES for item in modes
            ):
                raise ContractError(
                    "module.sensor_modes",
                    f"{path}.supported_modes",
                    "传感器模式必须非空、不得重复且只能使用规范枚举",
                )
            maximum_range = _number(
                obj["maximum_instrumented_range_m"],
                f"{path}.maximum_instrumented_range_m",
                0.0,
            )
            if maximum_range <= 0.0:
                raise ContractError(
                    "module.sensor_range",
                    f"{path}.maximum_instrumented_range_m",
                    "传感器仪表距离必须是正数",
                )
            parsed.update(
                {
                    "maximum_instrumented_range_m": maximum_range,
                    "sensor_channel": channel,
                    "supported_modes": list(modes),
                }
            )
        return cls(
            kind,
            tuple(
                (key, _freeze_json_value(item))
                for key, item in sorted(parsed.items(), key=lambda item: item[0])
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {key: _thaw_json_value(value) for key, value in self.values}


@dataclass(frozen=True)
class ModulePrototype:
    reference: ResourceReference
    name: str
    category: str
    balance_status: str
    installation: ModuleInstallationGeometry
    mass_kg: float
    durability_points: float
    base_external_rcs_m2: float | None
    power: ModulePowerProfile
    crew: tuple[ModuleCrewRequirement, ...]
    damage_responses: tuple[ModuleFunctionDamageResponse, ...]
    counts_toward_departure_minimum: bool
    default_operating_mode: str
    automatic_activation_events: tuple[str, ...]
    automation: ModuleAutomationProfile
    capability: ModuleCapability

    @classmethod
    def parse(
        cls,
        value: Any,
        path: str,
        *,
        propulsion_capability_version: int = 1,
    ) -> "ModulePrototype":
        obj = _object(value, path)
        _keys(
            obj,
            path,
            (
                "id",
                "version",
                "name",
                "category",
                "balance_status",
                "installation",
                "mass_kg",
                "durability_points",
                "base_external_rcs_m2",
                "power",
                "crew",
                "damage_responses",
                "counts_toward_departure_minimum",
                "default_operating_mode",
                "automatic_activation_events",
                "automation",
                "capability",
            ),
        )
        reference = ResourceReference(
            _resource_id(obj["id"], f"{path}.id"),
            _integer(obj["version"], f"{path}.version", 1),
        )
        category = _string(obj["category"], f"{path}.category")
        if category not in MODULE_CATEGORIES:
            raise ContractError("module.category", f"{path}.category", category)
        status = _string(obj["balance_status"], f"{path}.balance_status")
        if status not in BALANCE_STATUSES:
            raise ContractError("module.balance_status", f"{path}.balance_status", status)
        installation = ModuleInstallationGeometry.parse(obj["installation"], f"{path}.installation")
        power = ModulePowerProfile.parse(obj["power"], f"{path}.power")
        crew = tuple(
            sorted(
                (
                    ModuleCrewRequirement.parse(item, f"{path}.crew[{index}]")
                    for index, item in enumerate(_array(obj["crew"], f"{path}.crew"))
                ),
                key=lambda requirement: requirement.crew_type,
            )
        )
        if len({requirement.crew_type for requirement in crew}) != len(crew):
            raise ContractError("module.crew_duplicate", f"{path}.crew", "同类船员需求不得重复")
        damage_responses = tuple(
            sorted(
                (
                    ModuleFunctionDamageResponse.parse(
                        item, f"{path}.damage_responses[{index}]"
                    )
                    for index, item in enumerate(
                        _array(obj["damage_responses"], f"{path}.damage_responses")
                    )
                ),
                key=lambda response: response.function_id,
            )
        )
        response_ids = tuple(response.function_id for response in damage_responses)
        if len(set(response_ids)) != len(response_ids):
            raise ContractError(
                "module.damage_response_duplicate",
                f"{path}.damage_responses",
                "同一子功能不得重复声明战损响应",
            )
        expected_functions = MODULE_FUNCTIONS_BY_CATEGORY[category]
        if response_ids != expected_functions:
            raise ContractError(
                "module.damage_response_function_set",
                f"{path}.damage_responses",
                f"{category} 必须且只能声明 {list(expected_functions)}",
            )
        minimum_flag = _boolean(
            obj["counts_toward_departure_minimum"],
            f"{path}.counts_toward_departure_minimum",
        )
        if minimum_flag != (category in DEPARTURE_MINIMUM_CATEGORIES):
            raise ContractError(
                "module.departure_minimum_category",
                path,
                "只有 CIC、推进、发电和损管类别计入最低出航船员",
            )
        default_mode = _string(
            obj["default_operating_mode"], f"{path}.default_operating_mode"
        )
        if default_mode not in {"off", "standby", "active"}:
            raise ContractError(
                "module.default_operating_mode",
                f"{path}.default_operating_mode",
                default_mode,
            )
        activation_events = tuple(
            sorted(
                _resource_id(
                    item, f"{path}.automatic_activation_events[{index}]"
                )
                for index, item in enumerate(
                    _array(
                        obj["automatic_activation_events"],
                        f"{path}.automatic_activation_events",
                    )
                )
            )
        )
        if len(set(activation_events)) != len(activation_events):
            raise ContractError(
                "module.activation_event_duplicate",
                f"{path}.automatic_activation_events",
                "自动激活事件不得重复",
            )
        if activation_events and default_mode != "standby":
            raise ContractError(
                "module.activation_event_default_mode",
                path,
                "具有自动激活事件的模块必须默认待机",
            )
        automation = ModuleAutomationProfile.parse(obj["automation"], f"{path}.automation")
        if automation.level == "full" and any(requirement.standard > 0 for requirement in crew):
            raise ContractError("module.full_automation_has_crew", path, "完全无人化模块的标准船员必须为零")
        unknown_automation_functions = sorted(
            set(automation.automated_functions) - set(response_ids)
        )
        if unknown_automation_functions:
            raise ContractError(
                "module.automation_function_unknown",
                f"{path}.automation.automated_functions",
                f"找不到对应战损响应：{unknown_automation_functions}",
            )
        capability = ModuleCapability.parse(
            obj["capability"],
            f"{path}.capability",
            propulsion_capability_version=propulsion_capability_version,
        )
        if capability.kind != category:
            raise ContractError("module.capability_category_mismatch", path, "能力类型必须与模块类别一致")
        if (
            category in {"main_engine", "maneuver_thruster"}
            and propulsion_capability_version == 2
            and reference.version < 2
        ):
            raise ContractError(
                "module.propulsion_v2_resource_version",
                f"{path}.version",
                "推进 capability v2 的模块资源版本不得低于 2",
            )
        rcs_value = obj["base_external_rcs_m2"]
        rcs = None if rcs_value is None else _number(
            rcs_value, f"{path}.base_external_rcs_m2", 0.0
        )
        mass = _number(obj["mass_kg"], f"{path}.mass_kg", 0.0)
        durability = _number(obj["durability_points"], f"{path}.durability_points", 0.0)
        if mass <= 0.0 or durability <= 0.0:
            raise ContractError(
                "module.physical_nonpositive", path, "模块质量与耐久必须是正数"
            )
        has_external_geometry = bool(
            installation.top_footprint_half_cells
            or installation.side_external_footprint_half_cells
        )
        if not has_external_geometry and rcs is not None:
            raise ContractError("module.internal_rcs_present", path, "纯内部模块不得填写外部 RCS")
        if category == "cic" and "cic_internal" not in installation.provided_slots:
            raise ContractError("module.cic_slot_missing", path, "CIC 必须提供 cic_internal 嵌入槽位")
        if category == "remote_core" and installation.host_slot != "cic_internal":
            raise ContractError("module.remote_core_host", path, "遥控核心舱必须嵌入 cic_internal")
        if category == "main_engine":
            if not installation.internal_footprint_half_cells or not installation.exhaust_clearance_half_cells:
                raise ContractError("module.main_engine_geometry", path, "主发动机必须具有内部本体和尾焰净空")
            _validate_half_cell_parity(
                installation.internal_footprint_half_cells
                + installation.exhaust_clearance_half_cells,
                f"{path}.installation.main_engine_geometry",
            )
        if category == "maneuver_thruster" and installation.side_mount_length_steps <= 0:
            raise ContractError("module.maneuver_thruster_side_mount", path, "转向发动机必须是侧挂模块")
        if category == "maneuver_thruster":
            _validate_half_cell_parity(
                installation.side_external_footprint_half_cells
                + installation.exhaust_clearance_half_cells,
                f"{path}.installation.maneuver_thruster_geometry",
            )
        if category == "generator" and power.generation_kw <= 0.0:
            raise ContractError("module.generator_output", path, "发电设备必须具有正数发电量")
        if category != "generator" and power.generation_kw != 0.0:
            raise ContractError("module.non_generator_output", path, "非发电设备不得填写发电量")
        if (
            category == "damage_control"
            and power.active_load_kw > 0.0
            and power.consumer_category != "damage_control"
        ):
            raise ContractError(
                "module.damage_control_power_category",
                path,
                "损管启动负载必须属于 damage_control 类别",
            )
        if category == "damage_control" and (
            default_mode != "standby"
            or "ship.damage_control_required" not in activation_events
        ):
            raise ContractError(
                "module.damage_control_operating_policy",
                path,
                "损管必须默认待机并响应 ship.damage_control_required",
            )
        expected_combat_power_category = COMBAT_POWER_CATEGORY_BY_MODULE_CATEGORY.get(
            category
        )
        if (
            expected_combat_power_category is not None
            and power.active_load_kw > 0.0
            and power.consumer_category != expected_combat_power_category
        ):
            raise ContractError(
                "module.combat_power_category",
                f"{path}.power.consumer_category",
                f"{category} 必须属于 {expected_combat_power_category} 供电类别",
            )
        if category == "weapon" and (
            not installation.internal_footprint_half_cells
            or not (
                installation.top_footprint_half_cells
                or installation.side_external_footprint_half_cells
            )
        ):
            raise ContractError(
                "module.weapon_geometry",
                path,
                "武器必须同时具有内部本体和顶挂或侧挂外露部分",
            )
        if category == "sensor" and not installation.top_footprint_half_cells:
            raise ContractError(
                "module.sensor_geometry",
                path,
                "首版传感器必须具有顶挂外露部分",
            )
        if category == "remote_core" and (
            default_mode != "standby"
            or "ship.remote_control_selected" not in activation_events
        ):
            raise ContractError(
                "module.remote_core_operating_policy",
                path,
                "遥控核心舱必须默认待机并响应 ship.remote_control_selected",
            )
        return cls(
            reference,
            _string(obj["name"], f"{path}.name"),
            category,
            status,
            installation,
            mass,
            durability,
            rcs,
            power,
            crew,
            damage_responses,
            minimum_flag,
            default_mode,
            activation_events,
            automation,
            capability,
        )

    def minimum_crew_counts(self) -> dict[str, int]:
        if not self.counts_toward_departure_minimum:
            return {}
        return {
            requirement.crew_type: requirement.minimum_operating
            for requirement in self.crew
            if requirement.minimum_operating > 0
        }

    def standard_crew_counts(self) -> dict[str, int]:
        return {
            requirement.crew_type: requirement.standard
            for requirement in self.crew
            if requirement.standard > 0
        }

    def damage_output_fraction(
        self, function_id: str, durability_fraction: float
    ) -> float:
        for response in self.damage_responses:
            if response.function_id == function_id:
                return response.output_fraction(durability_fraction)
        return 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "automatic_activation_events": list(self.automatic_activation_events),
            "automation": self.automation.to_dict(),
            "balance_status": self.balance_status,
            "base_external_rcs_m2": self.base_external_rcs_m2,
            "capability": self.capability.to_dict(),
            "category": self.category,
            "counts_toward_departure_minimum": self.counts_toward_departure_minimum,
            "crew": [requirement.to_dict() for requirement in self.crew],
            "damage_responses": [
                response.to_dict() for response in self.damage_responses
            ],
            "default_operating_mode": self.default_operating_mode,
            "durability_points": self.durability_points,
            "id": self.reference.id,
            "installation": self.installation.to_dict(),
            "mass_kg": self.mass_kg,
            "name": self.name,
            "power": self.power.to_dict(),
            "version": self.reference.version,
        }


@dataclass(frozen=True)
class ModulePrototypeCatalog:
    id: str
    version: int
    name: str
    fixture_level: str
    modules: tuple[ModulePrototype, ...]
    schema: str = SCHEMA_ID

    @classmethod
    def parse(cls, resource: Any, path: str = "$") -> "ModulePrototypeCatalog":
        obj = _object(resource, path)
        _keys(obj, path, ("schema", "kind", "id", "version", "name", "fixture_level", "modules"))
        schema = _string(obj["schema"], f"{path}.schema")
        if schema not in {SCHEMA_ID, MODULE_CATALOG_V2_SCHEMA_ID}:
            raise ContractError("schema.unsupported", f"{path}.schema", schema)
        propulsion_capability_version = (
            2 if schema == MODULE_CATALOG_V2_SCHEMA_ID else 1
        )
        if obj["kind"] != "ModulePrototypeCatalog":
            raise ContractError(
                "resource.kind_mismatch", f"{path}.kind", "必须是 ModulePrototypeCatalog"
            )
        fixture_level = _string(obj["fixture_level"], f"{path}.fixture_level")
        if fixture_level not in BALANCE_STATUSES:
            raise ContractError("module.fixture_level", f"{path}.fixture_level", fixture_level)
        module_values = _array(obj["modules"], f"{path}.modules")
        if not module_values:
            raise ContractError("array.empty", f"{path}.modules", "模块目录不能为空")
        modules = tuple(
            sorted(
                (
                    ModulePrototype.parse(
                        item,
                        f"{path}.modules[{index}]",
                        propulsion_capability_version=propulsion_capability_version,
                    )
                    for index, item in enumerate(module_values)
                ),
                key=lambda module: module.reference,
            )
        )
        references = {module.reference for module in modules}
        if len(references) != len(modules):
            raise ContractError("resource.duplicate", f"{path}.modules", "模块精确引用不得重复")
        by_reference = {module.reference: module for module in modules}
        for module in modules:
            variant = module.automation.unmanned_variant
            if variant is None:
                continue
            target = by_reference.get(variant)
            if target is None:
                raise ContractError(
                    "resource.reference_missing",
                    f"{path}.modules",
                    f"找不到无人改进版 {variant}",
                )
            if target.category != module.category or target.automation.level != "full":
                raise ContractError(
                    "module.unmanned_variant_invalid",
                    f"{path}.modules",
                    "无人改进版必须是同类别的完全无人化模块",
                )
        version = _integer(obj["version"], f"{path}.version", 1)
        if schema == MODULE_CATALOG_V2_SCHEMA_ID and version < 2:
            raise ContractError(
                "module.catalog_v2_resource_version",
                f"{path}.version",
                "模块目录 v2 的资源版本不得低于 2",
            )
        return cls(
            _resource_id(obj["id"], f"{path}.id"),
            version,
            _string(obj["name"], f"{path}.name"),
            fixture_level,
            modules,
            schema,
        )

    def module(self, reference: ResourceReference, path: str = "$") -> ModulePrototype:
        for module in self.modules:
            if module.reference == reference:
                return module
        raise ContractError("resource.reference_missing", path, f"找不到模块原型 {reference}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "fixture_level": self.fixture_level,
            "id": self.id,
            "kind": "ModulePrototypeCatalog",
            "modules": [module.to_dict() for module in self.modules],
            "name": self.name,
            "schema": self.schema,
            "version": self.version,
        }


def merge_module_prototype_catalogs(
    catalogs: Iterable[ModulePrototypeCatalog],
    *,
    id: str,
    version: int,
    name: str,
    fixture_level: str,
    schema: str = SCHEMA_ID,
) -> ModulePrototypeCatalog:
    """把多个模块内容目录确定性合并为舾装编译器使用的精确目录。"""

    modules = [
        module.to_dict()
        for catalog in catalogs
        for module in catalog.modules
    ]
    return ModulePrototypeCatalog.parse(
        {
            "fixture_level": fixture_level,
            "id": id,
            "kind": "ModulePrototypeCatalog",
            "modules": modules,
            "name": name,
            "schema": schema,
            "version": version,
        },
        "$.merged_module_catalog",
    )


@dataclass(frozen=True)
class ModuleCatalogV1ToV2Migration:
    source_id: str
    source_version: int
    source_sha256: str
    target_version: int
    startup_time_s_by_module_id: tuple[tuple[str, float], ...]


KNOWN_MODULE_CATALOG_V1_TO_V2_MIGRATIONS = (
    ModuleCatalogV1ToV2Migration(
        "gtw.module_catalog.fixture.minimum",
        1,
        "94e027d95064a2e5ab90899544ec4f27b1f7042915767d7b4c78afd4647f5a7a",
        2,
        (
            ("gtw.module.fixture.main_engine", 1.0),
            ("gtw.module.fixture.maneuver_thruster", 0.0),
        ),
    ),
    ModuleCatalogV1ToV2Migration(
        "gtw.module_catalog.fixture.stage_f_unmanned",
        1,
        "15825e048d2275b6d20c9b040ec01e437758f1160d9367bc0495b10bdca627bf",
        2,
        (
            ("gtw.module.fixture.unmanned.main_engine", 1.0),
            ("gtw.module.fixture.unmanned.maneuver_thruster", 0.0),
        ),
    ),
    ModuleCatalogV1ToV2Migration(
        "gtw.module_catalog.fixture.combat_system",
        1,
        "1740797702b3de4a7e3c5919a3f59f9d12cfb92f69e338666e1a05639e018ab1",
        2,
        (),
    ),
)


def migrate_known_module_catalog_v1_to_v2(
    catalog: ModulePrototypeCatalog,
) -> ModulePrototypeCatalog:
    """只迁移仓库内具名且内容指纹精确匹配的 v1 模块目录。"""

    migration = next(
        (
            item
            for item in KNOWN_MODULE_CATALOG_V1_TO_V2_MIGRATIONS
            if (item.source_id, item.source_version)
            == (catalog.id, catalog.version)
        ),
        None,
    )
    if migration is None or catalog.schema != SCHEMA_ID:
        raise ContractError(
            "module.catalog_migration_unknown",
            "$.module_catalog",
            f"没有 {catalog.id}@{catalog.version} 的具名 v1→v2 迁移",
        )
    source_sha256 = canonical_sha256(catalog)
    if source_sha256 != migration.source_sha256:
        raise ContractError(
            "module.catalog_migration_source_hash",
            "$.module_catalog",
            f"{catalog.id}@{catalog.version} 内容指纹不匹配",
        )
    startup_by_id = dict(migration.startup_time_s_by_module_id)
    propulsion_ids = {
        module.reference.id
        for module in catalog.modules
        if module.category in {"main_engine", "maneuver_thruster"}
    }
    if set(startup_by_id) != propulsion_ids:
        raise ContractError(
            "module.catalog_migration_table_incomplete",
            "$.module_catalog",
            f"推进模块映射不完整：{sorted(propulsion_ids)}",
        )
    modules: list[dict[str, Any]] = []
    for module in catalog.modules:
        value = module.to_dict()
        if module.category in {"main_engine", "maneuver_thruster"}:
            if module.reference.version != 1:
                raise ContractError(
                    "module.catalog_migration_module_version",
                    "$.module_catalog.modules",
                    f"{module.reference.id} 不是版本 1",
                )
            value["version"] = 2
            value["capability"]["startup_time_s"] = startup_by_id[
                module.reference.id
            ]
        modules.append(value)
    return ModulePrototypeCatalog.parse(
        {
            "fixture_level": catalog.fixture_level,
            "id": catalog.id,
            "kind": "ModulePrototypeCatalog",
            "modules": modules,
            "name": catalog.name,
            "schema": MODULE_CATALOG_V2_SCHEMA_ID,
            "version": migration.target_version,
        },
        "$.migrated_module_catalog",
    )


def _rotation_deg(value: Any, path: str) -> int:
    result = _integer(value, path)
    if result not in {0, 90, 180, 270}:
        raise ContractError("outfit.rotation_invalid", path, "旋转只能是 0/90/180/270")
    return result


@dataclass(frozen=True)
class GridModulePlacement:
    deck_id: str
    anchor_half_cell: HalfCell
    rotation_deg: int

    @classmethod
    def parse(cls, obj: dict[str, Any], path: str) -> "GridModulePlacement":
        _keys(obj, path, ("kind", "deck_id", "anchor_half_cell", "rotation_deg"))
        anchor_raw = _array(obj["anchor_half_cell"], f"{path}.anchor_half_cell")
        if len(anchor_raw) != 2:
            raise ContractError("outfit.anchor_size", f"{path}.anchor_half_cell", "锚点必须有两个分量")
        return cls(
            _resource_id(obj["deck_id"], f"{path}.deck_id"),
            (
                _integer(anchor_raw[0], f"{path}.anchor_half_cell[0]"),
                _integer(anchor_raw[1], f"{path}.anchor_half_cell[1]"),
            ),
            _rotation_deg(obj["rotation_deg"], f"{path}.rotation_deg"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "anchor_half_cell": list(self.anchor_half_cell),
            "deck_id": self.deck_id,
            "kind": "grid",
            "rotation_deg": self.rotation_deg,
        }


@dataclass(frozen=True)
class SideModulePlacement:
    deck_id: str
    region_id: str
    edge_index: int
    start_slot_index: int
    rotation_deg: int

    @classmethod
    def parse(cls, obj: dict[str, Any], path: str) -> "SideModulePlacement":
        _keys(
            obj,
            path,
            ("kind", "deck_id", "region_id", "edge_index", "start_slot_index", "rotation_deg"),
        )
        return cls(
            _resource_id(obj["deck_id"], f"{path}.deck_id"),
            _resource_id(obj["region_id"], f"{path}.region_id"),
            _integer(obj["edge_index"], f"{path}.edge_index", 0),
            _integer(obj["start_slot_index"], f"{path}.start_slot_index", 0),
            _rotation_deg(obj["rotation_deg"], f"{path}.rotation_deg"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "deck_id": self.deck_id,
            "edge_index": self.edge_index,
            "kind": "side",
            "region_id": self.region_id,
            "rotation_deg": self.rotation_deg,
            "start_slot_index": self.start_slot_index,
        }


@dataclass(frozen=True)
class HostedModulePlacement:
    host_instance_id: str

    @classmethod
    def parse(cls, obj: dict[str, Any], path: str) -> "HostedModulePlacement":
        _keys(obj, path, ("kind", "host_instance_id"))
        return cls(_resource_id(obj["host_instance_id"], f"{path}.host_instance_id"))

    def to_dict(self) -> dict[str, Any]:
        return {"host_instance_id": self.host_instance_id, "kind": "hosted"}


ModulePlacement = GridModulePlacement | SideModulePlacement | HostedModulePlacement


def parse_module_placement(value: Any, path: str) -> ModulePlacement:
    obj = _object(value, path)
    kind = _string(obj.get("kind"), f"{path}.kind")
    if kind == "grid":
        return GridModulePlacement.parse(obj, path)
    if kind == "side":
        return SideModulePlacement.parse(obj, path)
    if kind == "hosted":
        return HostedModulePlacement.parse(obj, path)
    raise ContractError("outfit.placement_kind", f"{path}.kind", kind)


@dataclass(frozen=True)
class OutfitModuleInstanceInput:
    id: str
    prototype: ResourceReference
    placement: ModulePlacement

    @classmethod
    def parse(cls, value: Any, path: str) -> "OutfitModuleInstanceInput":
        obj = _object(value, path)
        _keys(obj, path, ("id", "prototype", "placement"))
        return cls(
            _resource_id(obj["id"], f"{path}.id"),
            ResourceReference.parse(obj["prototype"], f"{path}.prototype"),
            parse_module_placement(obj["placement"], f"{path}.placement"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "placement": self.placement.to_dict(),
            "prototype": self.prototype.to_dict(),
        }


@dataclass(frozen=True)
class OutfitPlanInput:
    id: str
    version: int
    name: str
    fixture_level: str
    hull_blueprint: ResourceReference
    hull_coating: ResourceReference
    modules: tuple[OutfitModuleInstanceInput, ...]

    @classmethod
    def parse(cls, resource: Any, path: str = "$") -> "OutfitPlanInput":
        obj = _object(resource, path)
        _keys(
            obj,
            path,
            (
                "schema",
                "kind",
                "id",
                "version",
                "name",
                "fixture_level",
                "hull_blueprint",
                "hull_coating",
                "modules",
            ),
        )
        if obj["schema"] != SCHEMA_ID:
            raise ContractError("schema.unsupported", f"{path}.schema", str(obj["schema"]))
        if obj["kind"] != "OutfitPlan":
            raise ContractError("resource.kind_mismatch", f"{path}.kind", "必须是 OutfitPlan")
        fixture_level = _string(obj["fixture_level"], f"{path}.fixture_level")
        if fixture_level not in BALANCE_STATUSES:
            raise ContractError("outfit.fixture_level", f"{path}.fixture_level", fixture_level)
        modules = tuple(
            sorted(
                (
                    OutfitModuleInstanceInput.parse(item, f"{path}.modules[{index}]")
                    for index, item in enumerate(_array(obj["modules"], f"{path}.modules"))
                ),
                key=lambda instance: instance.id,
            )
        )
        if len({instance.id for instance in modules}) != len(modules):
            raise ContractError("outfit.instance_id_duplicate", f"{path}.modules", "实例 id 不得重复")
        return cls(
            _resource_id(obj["id"], f"{path}.id"),
            _integer(obj["version"], f"{path}.version", 1),
            _string(obj["name"], f"{path}.name"),
            fixture_level,
            ResourceReference.parse(obj["hull_blueprint"], f"{path}.hull_blueprint"),
            ResourceReference.parse(obj["hull_coating"], f"{path}.hull_coating"),
            modules,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "fixture_level": self.fixture_level,
            "hull_blueprint": self.hull_blueprint.to_dict(),
            "hull_coating": self.hull_coating.to_dict(),
            "id": self.id,
            "kind": "OutfitPlan",
            "modules": [instance.to_dict() for instance in self.modules],
            "name": self.name,
            "schema": SCHEMA_ID,
            "version": self.version,
        }


@dataclass(frozen=True)
class SortieCrewCount:
    crew_type: str
    count: int

    @classmethod
    def parse(cls, value: Any, path: str) -> "SortieCrewCount":
        obj = _object(value, path)
        _keys(obj, path, ("crew_type", "count"))
        crew_type = _string(obj["crew_type"], f"{path}.crew_type")
        if crew_type not in CREW_TYPES:
            raise ContractError("sortie.crew_type", f"{path}.crew_type", crew_type)
        return cls(crew_type, _integer(obj["count"], f"{path}.count", 1))

    def to_dict(self) -> dict[str, Any]:
        return {"count": self.count, "crew_type": self.crew_type}


@dataclass(frozen=True)
class BulkCargoLoadInput:
    id: str
    storage_instance_id: str
    mass_kg: float

    @classmethod
    def parse(cls, value: Any, path: str) -> "BulkCargoLoadInput":
        obj = _object(value, path)
        _keys(obj, path, ("id", "storage_instance_id", "mass_kg"))
        mass = _number(obj["mass_kg"], f"{path}.mass_kg", 0.0)
        if mass <= 0.0:
            raise ContractError("sortie.cargo_mass", f"{path}.mass_kg", "大宗货物质量必须是正数")
        return cls(
            _resource_id(obj["id"], f"{path}.id"),
            _resource_id(obj["storage_instance_id"], f"{path}.storage_instance_id"),
            mass,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "mass_kg": self.mass_kg,
            "storage_instance_id": self.storage_instance_id,
        }


@dataclass(frozen=True)
class AmmunitionInventoryEntryInput:
    """一座弹药库内某一具体弹药的当前整数库存。"""

    munition_id: str
    units: int

    @classmethod
    def parse(cls, value: Any, path: str) -> "AmmunitionInventoryEntryInput":
        obj = _object(value, path)
        _keys(obj, path, ("munition_id", "units"))
        return cls(
            _resource_id(obj["munition_id"], f"{path}.munition_id"),
            _integer(obj["units"], f"{path}.units", 1),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"munition_id": self.munition_id, "units": self.units}


@dataclass(frozen=True)
class MagazineAmmunitionStateInput:
    """弹药库实例的物理库存；装填时仍按全舰共享池访问。"""

    instance_id: str
    inventory: tuple[AmmunitionInventoryEntryInput, ...]

    @classmethod
    def parse(cls, value: Any, path: str) -> "MagazineAmmunitionStateInput":
        obj = _object(value, path)
        _keys(obj, path, ("instance_id", "inventory"))
        inventory = tuple(
            sorted(
                (
                    AmmunitionInventoryEntryInput.parse(
                        item, f"{path}.inventory[{index}]"
                    )
                    for index, item in enumerate(
                        _array(obj["inventory"], f"{path}.inventory")
                    )
                ),
                key=lambda item: item.munition_id,
            )
        )
        if len({item.munition_id for item in inventory}) != len(inventory):
            raise ContractError(
                "ammunition.inventory_munition_duplicate",
                f"{path}.inventory",
                "同一弹药库内的弹药标识不得重复",
            )
        return cls(
            _resource_id(obj["instance_id"], f"{path}.instance_id"), inventory
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "inventory": [item.to_dict() for item in self.inventory],
        }


@dataclass(frozen=True)
class WeaponReadyAmmunitionStateInput:
    """武器实例当前已经进入待发位置、可直接射击的弹药。"""

    instance_id: str
    munition_id: str | None
    ready_rounds: int

    @classmethod
    def parse(cls, value: Any, path: str) -> "WeaponReadyAmmunitionStateInput":
        obj = _object(value, path)
        _keys(obj, path, ("instance_id", "munition_id", "ready_rounds"))
        rounds = _integer(obj["ready_rounds"], f"{path}.ready_rounds", 0)
        munition_value = obj["munition_id"]
        munition_id = (
            None
            if munition_value is None
            else _resource_id(munition_value, f"{path}.munition_id")
        )
        if (rounds == 0) != (munition_id is None):
            raise ContractError(
                "ammunition.ready_round_identity",
                path,
                "待发数为零时弹药标识必须为空；有待发弹时必须指定弹药标识",
            )
        return cls(
            _resource_id(obj["instance_id"], f"{path}.instance_id"),
            munition_id,
            rounds,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "munition_id": self.munition_id,
            "ready_rounds": self.ready_rounds,
        }


@dataclass(frozen=True)
class ShipAmmunitionStateInput:
    """舰艇实例持久化的弹药库库存与武器待发弹状态。"""

    magazines: tuple[MagazineAmmunitionStateInput, ...]
    weapons: tuple[WeaponReadyAmmunitionStateInput, ...]

    @classmethod
    def parse(cls, value: Any, path: str) -> "ShipAmmunitionStateInput":
        obj = _object(value, path)
        _keys(obj, path, ("magazines", "weapons"))
        magazines = tuple(
            sorted(
                (
                    MagazineAmmunitionStateInput.parse(
                        item, f"{path}.magazines[{index}]"
                    )
                    for index, item in enumerate(
                        _array(obj["magazines"], f"{path}.magazines")
                    )
                ),
                key=lambda item: item.instance_id,
            )
        )
        weapons = tuple(
            sorted(
                (
                    WeaponReadyAmmunitionStateInput.parse(
                        item, f"{path}.weapons[{index}]"
                    )
                    for index, item in enumerate(
                        _array(obj["weapons"], f"{path}.weapons")
                    )
                ),
                key=lambda item: item.instance_id,
            )
        )
        if len({item.instance_id for item in magazines}) != len(magazines):
            raise ContractError(
                "ammunition.magazine_state_duplicate",
                f"{path}.magazines",
                "弹药库实例状态不得重复",
            )
        if len({item.instance_id for item in weapons}) != len(weapons):
            raise ContractError(
                "ammunition.weapon_state_duplicate",
                f"{path}.weapons",
                "武器实例待发状态不得重复",
            )
        return cls(magazines, weapons)

    def to_dict(self) -> dict[str, Any]:
        return {
            "magazines": [item.to_dict() for item in self.magazines],
            "weapons": [item.to_dict() for item in self.weapons],
        }


@dataclass(frozen=True)
class WeaponTimelineClockInput:
    instance_id: str
    next_fire_time_s: float

    @classmethod
    def parse(cls, value: Any, path: str) -> "WeaponTimelineClockInput":
        obj = _object(value, path)
        _keys(obj, path, ("instance_id", "next_fire_time_s"))
        return cls(
            _resource_id(obj["instance_id"], f"{path}.instance_id"),
            _number(obj["next_fire_time_s"], f"{path}.next_fire_time_s", 0.0),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "next_fire_time_s": self.next_fire_time_s,
        }


@dataclass(frozen=True)
class WeaponFireSequenceStateInput:
    id: str
    group_id: str | None
    weapon_instance_id: str
    munition_id: str
    remaining_rounds: int
    target_domain: str
    target_distance_m: float
    fire_control_instance_id: str | None
    phase: str
    next_event_time_s: float

    @classmethod
    def parse(cls, value: Any, path: str) -> "WeaponFireSequenceStateInput":
        obj = _object(value, path)
        _keys(
            obj,
            path,
            (
                "id",
                "group_id",
                "weapon_instance_id",
                "munition_id",
                "remaining_rounds",
                "target_domain",
                "target_distance_m",
                "fire_control_instance_id",
                "phase",
                "next_event_time_s",
            ),
        )
        group_value = obj["group_id"]
        fire_control_value = obj["fire_control_instance_id"]
        domain = _string(obj["target_domain"], f"{path}.target_domain")
        if domain not in ENGAGEMENT_DOMAINS:
            raise ContractError(
                "weapon_timeline.target_domain",
                f"{path}.target_domain",
                domain,
            )
        phase = _string(obj["phase"], f"{path}.phase")
        if phase not in {"awaiting_fire", "reloading"}:
            raise ContractError(
                "weapon_timeline.phase",
                f"{path}.phase",
                phase,
            )
        return cls(
            _resource_id(obj["id"], f"{path}.id"),
            (
                None
                if group_value is None
                else _resource_id(group_value, f"{path}.group_id")
            ),
            _resource_id(
                obj["weapon_instance_id"], f"{path}.weapon_instance_id"
            ),
            _resource_id(obj["munition_id"], f"{path}.munition_id"),
            _integer(obj["remaining_rounds"], f"{path}.remaining_rounds", 1),
            domain,
            _number(obj["target_distance_m"], f"{path}.target_distance_m", 0.0),
            (
                None
                if fire_control_value is None
                else _resource_id(
                    fire_control_value, f"{path}.fire_control_instance_id"
                )
            ),
            phase,
            _number(obj["next_event_time_s"], f"{path}.next_event_time_s", 0.0),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "fire_control_instance_id": self.fire_control_instance_id,
            "group_id": self.group_id,
            "id": self.id,
            "munition_id": self.munition_id,
            "next_event_time_s": self.next_event_time_s,
            "phase": self.phase,
            "remaining_rounds": self.remaining_rounds,
            "target_distance_m": self.target_distance_m,
            "target_domain": self.target_domain,
            "weapon_instance_id": self.weapon_instance_id,
        }


@dataclass(frozen=True)
class WeaponTimelineStateInput:
    timing_profile_catalog: ResourceReference
    timing_profile_catalog_sha256: str
    tactical_time_s: float
    clocks: tuple[WeaponTimelineClockInput, ...]
    sequences: tuple[WeaponFireSequenceStateInput, ...]

    @classmethod
    def parse(cls, value: Any, path: str) -> "WeaponTimelineStateInput":
        obj = _object(value, path)
        _keys(
            obj,
            path,
            (
                "timing_profile_catalog",
                "timing_profile_catalog_sha256",
                "tactical_time_s",
                "clocks",
                "sequences",
            ),
        )
        tactical_time = _number(
            obj["tactical_time_s"], f"{path}.tactical_time_s", 0.0
        )
        clocks = tuple(
            sorted(
                (
                    WeaponTimelineClockInput.parse(
                        item, f"{path}.clocks[{index}]"
                    )
                    for index, item in enumerate(
                        _array(obj["clocks"], f"{path}.clocks")
                    )
                ),
                key=lambda item: item.instance_id,
            )
        )
        sequences = tuple(
            sorted(
                (
                    WeaponFireSequenceStateInput.parse(
                        item, f"{path}.sequences[{index}]"
                    )
                    for index, item in enumerate(
                        _array(obj["sequences"], f"{path}.sequences")
                    )
                ),
                key=lambda item: item.id,
            )
        )
        if len({item.instance_id for item in clocks}) != len(clocks):
            raise ContractError(
                "weapon_timeline.clock_duplicate",
                f"{path}.clocks",
                "同一武器不得有重复时钟",
            )
        if len({item.id for item in sequences}) != len(sequences):
            raise ContractError(
                "weapon_timeline.sequence_duplicate",
                f"{path}.sequences",
                "射击序列 id 不得重复",
            )
        if len({item.weapon_instance_id for item in sequences}) != len(sequences):
            raise ContractError(
                "weapon_timeline.weapon_sequence_conflict",
                f"{path}.sequences",
                "同一武器同一时刻只能执行一个活动序列",
            )
        if any(item.next_event_time_s + 1.0e-8 < tactical_time for item in sequences):
            raise ContractError(
                "weapon_timeline.event_in_past",
                f"{path}.sequences",
                "活动序列的下一事件不得早于当前战术时刻",
            )
        return cls(
            ResourceReference.parse(
                obj["timing_profile_catalog"],
                f"{path}.timing_profile_catalog",
            ),
            _sha256_hex(
                obj["timing_profile_catalog_sha256"],
                f"{path}.timing_profile_catalog_sha256",
            ),
            tactical_time,
            clocks,
            sequences,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "clocks": [item.to_dict() for item in self.clocks],
            "sequences": [item.to_dict() for item in self.sequences],
            "tactical_time_s": self.tactical_time_s,
            "timing_profile_catalog": self.timing_profile_catalog.to_dict(),
            "timing_profile_catalog_sha256": self.timing_profile_catalog_sha256,
        }


@dataclass(frozen=True)
class FireIncidentStateInput:
    id: str
    source_projectile_id: str
    target_module_instance_id: str
    created_time_s: float
    intensity_units: float
    remaining_fuel_units: float
    propagated_from_fire_incident_id: str | None = None
    source_secondary_explosion_id: str | None = None

    @classmethod
    def parse(cls, value: Any, path: str) -> "FireIncidentStateInput":
        obj = _object(value, path)
        _keys(
            obj,
            path,
            (
                "id",
                "source_projectile_id",
                "target_module_instance_id",
                "created_time_s",
                "intensity_units",
                "remaining_fuel_units",
            ),
            (
                "propagated_from_fire_incident_id",
                "source_secondary_explosion_id",
            ),
        )
        intensity = _number(
            obj["intensity_units"], f"{path}.intensity_units", 0.0
        )
        fuel = _number(
            obj["remaining_fuel_units"],
            f"{path}.remaining_fuel_units",
            0.0,
        )
        if intensity <= 0.0 or fuel <= 0.0:
            raise ContractError(
                "continuous_damage.fire_nonpositive",
                path,
                "活动火灾的强度与剩余燃料都必须为正数",
            )
        propagated_from = (
            None
            if "propagated_from_fire_incident_id" not in obj
            else _resource_id(
                obj["propagated_from_fire_incident_id"],
                f"{path}.propagated_from_fire_incident_id",
            )
        )
        source_explosion = (
            None
            if "source_secondary_explosion_id" not in obj
            else _resource_id(
                obj["source_secondary_explosion_id"],
                f"{path}.source_secondary_explosion_id",
            )
        )
        if propagated_from is not None and source_explosion is not None:
            raise ContractError(
                "continuous_damage.fire_secondary_source_conflict",
                path,
                "传播来源火灾与二次爆炸来源只能记录一种",
            )
        incident_id = _resource_id(obj["id"], f"{path}.id")
        if propagated_from == incident_id:
            raise ContractError(
                "continuous_damage.fire_self_parent",
                f"{path}.propagated_from_fire_incident_id",
                "火灾不能由自身传播产生",
            )
        return cls(
            incident_id,
            _resource_id(
                obj["source_projectile_id"],
                f"{path}.source_projectile_id",
            ),
            _resource_id(
                obj["target_module_instance_id"],
                f"{path}.target_module_instance_id",
            ),
            _number(obj["created_time_s"], f"{path}.created_time_s", 0.0),
            intensity,
            fuel,
            propagated_from,
            source_explosion,
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "created_time_s": self.created_time_s,
            "id": self.id,
            "intensity_units": self.intensity_units,
            "remaining_fuel_units": self.remaining_fuel_units,
            "source_projectile_id": self.source_projectile_id,
            "target_module_instance_id": self.target_module_instance_id,
        }
        if self.propagated_from_fire_incident_id is not None:
            result["propagated_from_fire_incident_id"] = (
                self.propagated_from_fire_incident_id
            )
        if self.source_secondary_explosion_id is not None:
            result["source_secondary_explosion_id"] = (
                self.source_secondary_explosion_id
            )
        return result


@dataclass(frozen=True)
class DamageControlAssignmentInput:
    damage_control_module_instance_id: str
    team_index: int
    fire_incident_id: str

    @property
    def slot(self) -> tuple[str, int]:
        return self.damage_control_module_instance_id, self.team_index

    @classmethod
    def parse(cls, value: Any, path: str) -> "DamageControlAssignmentInput":
        obj = _object(value, path)
        _keys(
            obj,
            path,
            (
                "damage_control_module_instance_id",
                "team_index",
                "fire_incident_id",
            ),
        )
        return cls(
            _resource_id(
                obj["damage_control_module_instance_id"],
                f"{path}.damage_control_module_instance_id",
            ),
            _integer(obj["team_index"], f"{path}.team_index", 0),
            _resource_id(
                obj["fire_incident_id"],
                f"{path}.fire_incident_id",
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "damage_control_module_instance_id": self.damage_control_module_instance_id,
            "fire_incident_id": self.fire_incident_id,
            "team_index": self.team_index,
        }


@dataclass(frozen=True)
class ShipContinuousDamageStateInput:
    profile: ResourceReference
    profile_sha256: str
    tactical_time_s: float
    fire_incidents: tuple[FireIncidentStateInput, ...]
    damage_control_assignments: tuple[DamageControlAssignmentInput, ...]

    @classmethod
    def parse(
        cls, value: Any, path: str
    ) -> "ShipContinuousDamageStateInput":
        obj = _object(value, path)
        _keys(
            obj,
            path,
            (
                "profile",
                "profile_sha256",
                "tactical_time_s",
                "fire_incidents",
                "damage_control_assignments",
            ),
        )
        tactical_time = _number(
            obj["tactical_time_s"], f"{path}.tactical_time_s", 0.0
        )
        fires = tuple(
            sorted(
                (
                    FireIncidentStateInput.parse(
                        item, f"{path}.fire_incidents[{index}]"
                    )
                    for index, item in enumerate(
                        _array(obj["fire_incidents"], f"{path}.fire_incidents")
                    )
                ),
                key=lambda item: item.id,
            )
        )
        assignments = tuple(
            sorted(
                (
                    DamageControlAssignmentInput.parse(
                        item,
                        f"{path}.damage_control_assignments[{index}]",
                    )
                    for index, item in enumerate(
                        _array(
                            obj["damage_control_assignments"],
                            f"{path}.damage_control_assignments",
                        )
                    )
                ),
                key=lambda item: item.slot,
            )
        )
        fire_ids = {item.id for item in fires}
        if len(fire_ids) != len(fires):
            raise ContractError(
                "continuous_damage.fire_duplicate",
                f"{path}.fire_incidents",
                "火灾事件 id 不得重复",
            )
        if any(item.created_time_s > tactical_time + 1.0e-8 for item in fires):
            raise ContractError(
                "continuous_damage.fire_from_future",
                f"{path}.fire_incidents",
                "火灾创建时刻不得晚于持续毁伤状态时钟",
            )
        if len({item.slot for item in assignments}) != len(assignments):
            raise ContractError(
                "continuous_damage.assignment_slot_duplicate",
                f"{path}.damage_control_assignments",
                "同一损管模块队伍槽位不得重复分配",
            )
        unknown = sorted(
            {
                item.fire_incident_id
                for item in assignments
                if item.fire_incident_id not in fire_ids
            }
        )
        if unknown:
            raise ContractError(
                "continuous_damage.assignment_fire_missing",
                f"{path}.damage_control_assignments",
                str(unknown),
            )
        return cls(
            ResourceReference.parse(obj["profile"], f"{path}.profile"),
            _sha256_hex(obj["profile_sha256"], f"{path}.profile_sha256"),
            tactical_time,
            fires,
            assignments,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "damage_control_assignments": [
                item.to_dict() for item in self.damage_control_assignments
            ],
            "fire_incidents": [item.to_dict() for item in self.fire_incidents],
            "profile": self.profile.to_dict(),
            "profile_sha256": self.profile_sha256,
            "tactical_time_s": self.tactical_time_s,
        }


@dataclass(frozen=True)
class CrewCasualtyStatusInput:
    """单一人员类别在舰上的当前战斗状态账本。"""

    crew_type: str
    fit_for_duty_count: int
    wounded_count: int
    dead_count: int

    @classmethod
    def parse(cls, value: Any, path: str) -> "CrewCasualtyStatusInput":
        obj = _object(value, path)
        _keys(
            obj,
            path,
            (
                "crew_type",
                "fit_for_duty_count",
                "wounded_count",
                "dead_count",
            ),
        )
        crew_type = _string(obj["crew_type"], f"{path}.crew_type")
        if crew_type not in CREW_TYPES:
            raise ContractError(
                "crew_casualty.crew_type",
                f"{path}.crew_type",
                crew_type,
            )
        result = cls(
            crew_type,
            _integer(
                obj["fit_for_duty_count"],
                f"{path}.fit_for_duty_count",
                0,
            ),
            _integer(obj["wounded_count"], f"{path}.wounded_count", 0),
            _integer(obj["dead_count"], f"{path}.dead_count", 0),
        )
        if result.total_count <= 0:
            raise ContractError(
                "crew_casualty.empty_status",
                path,
                "人员伤亡账本不得保留全零类别",
            )
        return result

    @property
    def total_count(self) -> int:
        return self.fit_for_duty_count + self.wounded_count + self.dead_count

    def to_dict(self) -> dict[str, Any]:
        return {
            "crew_type": self.crew_type,
            "dead_count": self.dead_count,
            "fit_for_duty_count": self.fit_for_duty_count,
            "wounded_count": self.wounded_count,
        }


@dataclass(frozen=True)
class ShipCrewCasualtyStateInput:
    """舰艇可执勤、负伤与死亡人员的可持久化战斗账本。"""

    tactical_time_s: float
    crew_statuses: tuple[CrewCasualtyStatusInput, ...]
    last_strategic_operation_time_s: float | None = None

    @classmethod
    def parse(cls, value: Any, path: str) -> "ShipCrewCasualtyStateInput":
        obj = _object(value, path)
        _keys(
            obj,
            path,
            ("tactical_time_s", "crew_statuses"),
            ("last_strategic_operation_time_s",),
        )
        statuses = tuple(
            sorted(
                (
                    CrewCasualtyStatusInput.parse(
                        item,
                        f"{path}.crew_statuses[{index}]",
                    )
                    for index, item in enumerate(
                        _array(obj["crew_statuses"], f"{path}.crew_statuses")
                    )
                ),
                key=lambda item: item.crew_type,
            )
        )
        if len({item.crew_type for item in statuses}) != len(statuses):
            raise ContractError(
                "crew_casualty.crew_type_duplicate",
                f"{path}.crew_statuses",
                "人员伤亡账本中的人员类别不得重复",
            )
        return cls(
            _number(obj["tactical_time_s"], f"{path}.tactical_time_s", 0.0),
            statuses,
            (
                None
                if "last_strategic_operation_time_s" not in obj
                else _number(
                    obj["last_strategic_operation_time_s"],
                    f"{path}.last_strategic_operation_time_s",
                    0.0,
                )
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "crew_statuses": [item.to_dict() for item in self.crew_statuses],
            "tactical_time_s": self.tactical_time_s,
        }
        if self.last_strategic_operation_time_s is not None:
            result["last_strategic_operation_time_s"] = (
                self.last_strategic_operation_time_s
            )
        return result


@dataclass(frozen=True)
class SortieConfigurationInput:
    id: str
    version: int
    name: str
    fixture_level: str
    outfit_plan: ResourceReference
    height_layer: str
    control_mode: str
    active_remote_core_instance_id: str | None
    fuel_units: float
    crew: tuple[SortieCrewCount, ...]
    bulk_cargo: tuple[BulkCargoLoadInput, ...]
    ammunition_loadout: ShipAmmunitionStateInput | None = None

    @classmethod
    def parse(cls, resource: Any, path: str = "$") -> "SortieConfigurationInput":
        obj = _object(resource, path)
        _keys(
            obj,
            path,
            (
                "schema",
                "kind",
                "id",
                "version",
                "name",
                "fixture_level",
                "outfit_plan",
                "height_layer",
                "control_mode",
                "active_remote_core_instance_id",
                "fuel_units",
                "crew",
                "bulk_cargo",
            ),
            ("ammunition_loadout",),
        )
        if obj["schema"] != SCHEMA_ID:
            raise ContractError("schema.unsupported", f"{path}.schema", str(obj["schema"]))
        if obj["kind"] != "SortieConfiguration":
            raise ContractError(
                "resource.kind_mismatch",
                f"{path}.kind",
                "必须是 SortieConfiguration",
            )
        fixture_level = _string(obj["fixture_level"], f"{path}.fixture_level")
        if fixture_level not in BALANCE_STATUSES:
            raise ContractError("sortie.fixture_level", f"{path}.fixture_level", fixture_level)
        height_layer = _string(obj["height_layer"], f"{path}.height_layer")
        if height_layer not in {"upper", "cloud", "rain"}:
            raise ContractError("sortie.height_layer", f"{path}.height_layer", height_layer)
        control_mode = _string(obj["control_mode"], f"{path}.control_mode")
        if control_mode not in {"crewed", "remote_core"}:
            raise ContractError("sortie.control_mode", f"{path}.control_mode", control_mode)
        remote_value = obj["active_remote_core_instance_id"]
        remote_id = (
            None
            if remote_value is None
            else _resource_id(remote_value, f"{path}.active_remote_core_instance_id")
        )
        if (control_mode == "remote_core") != (remote_id is not None):
            raise ContractError(
                "sortie.remote_core_mode",
                path,
                "remote_core 控制模式必须且只能指定一个启用的遥控核心舱实例",
            )
        crew = tuple(
            sorted(
                (
                    SortieCrewCount.parse(item, f"{path}.crew[{index}]")
                    for index, item in enumerate(_array(obj["crew"], f"{path}.crew"))
                ),
                key=lambda item: item.crew_type,
            )
        )
        if len({item.crew_type for item in crew}) != len(crew):
            raise ContractError("sortie.crew_type_duplicate", f"{path}.crew", "船员类别不得重复")
        cargo = tuple(
            sorted(
                (
                    BulkCargoLoadInput.parse(item, f"{path}.bulk_cargo[{index}]")
                    for index, item in enumerate(
                        _array(obj["bulk_cargo"], f"{path}.bulk_cargo")
                    )
                ),
                key=lambda item: item.id,
            )
        )
        if len({item.id for item in cargo}) != len(cargo):
            raise ContractError("sortie.cargo_id_duplicate", f"{path}.bulk_cargo", "货物 id 不得重复")
        return cls(
            _resource_id(obj["id"], f"{path}.id"),
            _integer(obj["version"], f"{path}.version", 1),
            _string(obj["name"], f"{path}.name"),
            fixture_level,
            ResourceReference.parse(obj["outfit_plan"], f"{path}.outfit_plan"),
            height_layer,
            control_mode,
            remote_id,
            _number(obj["fuel_units"], f"{path}.fuel_units", 0.0),
            crew,
            cargo,
            (
                None
                if "ammunition_loadout" not in obj
                else ShipAmmunitionStateInput.parse(
                    obj["ammunition_loadout"], f"{path}.ammunition_loadout"
                )
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        result = {
            "active_remote_core_instance_id": self.active_remote_core_instance_id,
            "bulk_cargo": [item.to_dict() for item in self.bulk_cargo],
            "control_mode": self.control_mode,
            "crew": [item.to_dict() for item in self.crew],
            "fixture_level": self.fixture_level,
            "fuel_units": self.fuel_units,
            "height_layer": self.height_layer,
            "id": self.id,
            "kind": "SortieConfiguration",
            "name": self.name,
            "outfit_plan": self.outfit_plan.to_dict(),
            "schema": SCHEMA_ID,
            "version": self.version,
        }
        if self.ammunition_loadout is not None:
            result["ammunition_loadout"] = self.ammunition_loadout.to_dict()
        return result


@dataclass(frozen=True)
class RuntimeModuleStateInput:
    instance_id: str
    current_durability_points: float
    operating_mode: str

    @classmethod
    def parse(cls, value: Any, path: str) -> "RuntimeModuleStateInput":
        obj = _object(value, path)
        _keys(
            obj,
            path,
            ("instance_id", "current_durability_points", "operating_mode"),
        )
        mode = _string(obj["operating_mode"], f"{path}.operating_mode")
        if mode not in {"off", "standby", "active"}:
            raise ContractError(
                "instance.module_operating_mode",
                f"{path}.operating_mode",
                mode,
            )
        return cls(
            _resource_id(obj["instance_id"], f"{path}.instance_id"),
            _number(
                obj["current_durability_points"],
                f"{path}.current_durability_points",
                0.0,
            ),
            mode,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_durability_points": self.current_durability_points,
            "instance_id": self.instance_id,
            "operating_mode": self.operating_mode,
        }


@dataclass(frozen=True)
class RuntimePowerPolicyInput:
    allocation_mode: str
    category_order: tuple[str, ...]
    disabled_categories: tuple[str, ...]

    @classmethod
    def parse(cls, value: Any, path: str) -> "RuntimePowerPolicyInput":
        obj = _object(value, path)
        _keys(
            obj,
            path,
            ("allocation_mode", "category_order", "disabled_categories"),
        )
        mode = _string(obj["allocation_mode"], f"{path}.allocation_mode")
        if mode not in {"strict_categories", "safe_nearest_to_cic"}:
            raise ContractError(
                "instance.power_allocation_mode",
                f"{path}.allocation_mode",
                mode,
            )
        order = tuple(
            _string(item, f"{path}.category_order[{index}]")
            for index, item in enumerate(
                _array(obj["category_order"], f"{path}.category_order")
            )
        )
        if (
            len(order) != len(POWER_CONSUMER_CATEGORIES)
            or set(order) != POWER_CONSUMER_CATEGORY_SET
        ):
            raise ContractError(
                "instance.power_category_permutation",
                f"{path}.category_order",
                "必须恰好排列四个稳定供电类别各一次",
            )
        disabled = tuple(
            sorted(
                _string(item, f"{path}.disabled_categories[{index}]")
                for index, item in enumerate(
                    _array(
                        obj["disabled_categories"],
                        f"{path}.disabled_categories",
                    )
                )
            )
        )
        if len(set(disabled)) != len(disabled):
            raise ContractError(
                "instance.power_disabled_category_duplicate",
                f"{path}.disabled_categories",
                "关闭类别不得重复",
            )
        if any(category not in POWER_CONSUMER_CATEGORY_SET for category in disabled):
            raise ContractError(
                "instance.power_category",
                f"{path}.disabled_categories",
                "只能关闭规范供电类别",
            )
        return cls(mode, order, disabled)

    def to_dict(self) -> dict[str, Any]:
        return {
            "allocation_mode": self.allocation_mode,
            "category_order": list(self.category_order),
            "disabled_categories": list(self.disabled_categories),
        }


@dataclass(frozen=True)
class ShipOperationalStateInput:
    """舰艇离港后持续变化、但不改变初始出航配置的当前资源状态。"""

    height_layer: str
    fuel_units: float
    crew: tuple[SortieCrewCount, ...]
    bulk_cargo: tuple[BulkCargoLoadInput, ...]

    @classmethod
    def parse(cls, value: Any, path: str) -> "ShipOperationalStateInput":
        obj = _object(value, path)
        _keys(obj, path, ("height_layer", "fuel_units", "crew", "bulk_cargo"))
        height_layer = _string(obj["height_layer"], f"{path}.height_layer")
        if height_layer not in {"upper", "cloud", "rain"}:
            raise ContractError(
                "instance.height_layer",
                f"{path}.height_layer",
                height_layer,
            )
        crew = tuple(
            sorted(
                (
                    SortieCrewCount.parse(item, f"{path}.crew[{index}]")
                    for index, item in enumerate(_array(obj["crew"], f"{path}.crew"))
                ),
                key=lambda item: item.crew_type,
            )
        )
        if len({item.crew_type for item in crew}) != len(crew):
            raise ContractError(
                "instance.crew_type_duplicate",
                f"{path}.crew",
                "当前船员类别不得重复",
            )
        cargo = tuple(
            sorted(
                (
                    BulkCargoLoadInput.parse(item, f"{path}.bulk_cargo[{index}]")
                    for index, item in enumerate(
                        _array(obj["bulk_cargo"], f"{path}.bulk_cargo")
                    )
                ),
                key=lambda item: item.id,
            )
        )
        if len({item.id for item in cargo}) != len(cargo):
            raise ContractError(
                "instance.cargo_id_duplicate",
                f"{path}.bulk_cargo",
                "当前大宗货物 id 不得重复",
            )
        return cls(
            height_layer,
            _number(obj["fuel_units"], f"{path}.fuel_units", 0.0),
            crew,
            cargo,
        )

    @classmethod
    def from_sortie(
        cls, configuration: SortieConfigurationInput
    ) -> "ShipOperationalStateInput":
        return cls(
            configuration.height_layer,
            configuration.fuel_units,
            configuration.crew,
            configuration.bulk_cargo,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "bulk_cargo": [item.to_dict() for item in self.bulk_cargo],
            "crew": [item.to_dict() for item in self.crew],
            "fuel_units": self.fuel_units,
            "height_layer": self.height_layer,
        }


@dataclass(frozen=True)
class ShipDesignStateInput:
    """已建成舰艇自持的几何与当前舾装，不依赖外部蓝图继续存在。

    ShipInstanceSnapshotInput 上原有的 outfit_plan、derived_ship_snapshot_sha256
    与 sortie_configuration 字段继续记录建成/首次出航来源；本对象只记录固定船壳
    快照和此刻生效的舾装状态，避免改装反向改写历史来源。
    """

    construction_hull_blueprint: HullBlueprintInput
    construction_hull_blueprint_sha256: str
    current_outfit_plan: OutfitPlanInput
    current_outfit_plan_sha256: str
    current_derived_ship_snapshot_sha256: str
    revision: int

    @classmethod
    def parse(cls, value: Any, path: str) -> "ShipDesignStateInput":
        obj = _object(value, path)
        _keys(
            obj,
            path,
            (
                "construction_hull_blueprint",
                "construction_hull_blueprint_sha256",
                "current_outfit_plan",
                "current_outfit_plan_sha256",
                "current_derived_ship_snapshot_sha256",
                "revision",
            ),
        )
        hull = HullBlueprintInput.parse(
            obj["construction_hull_blueprint"],
            f"{path}.construction_hull_blueprint",
        )
        hull_sha = _sha256_hex(
            obj["construction_hull_blueprint_sha256"],
            f"{path}.construction_hull_blueprint_sha256",
        )
        outfit = OutfitPlanInput.parse(
            obj["current_outfit_plan"], f"{path}.current_outfit_plan"
        )
        outfit_sha = _sha256_hex(
            obj["current_outfit_plan_sha256"],
            f"{path}.current_outfit_plan_sha256",
        )
        if hull_sha != canonical_sha256(hull):
            raise ContractError(
                "instance.construction_hull_snapshot_hash_mismatch",
                f"{path}.construction_hull_blueprint_sha256",
                "建造时船壳快照内容与哈希不一致",
            )
        if outfit_sha != canonical_sha256(outfit):
            raise ContractError(
                "instance.current_outfit_snapshot_hash_mismatch",
                f"{path}.current_outfit_plan_sha256",
                "当前舾装快照内容与哈希不一致",
            )
        expected_hull = ResourceReference(hull.id, hull.version)
        if outfit.hull_blueprint != expected_hull:
            raise ContractError(
                "instance.current_outfit_hull_mismatch",
                f"{path}.current_outfit_plan.hull_blueprint",
                f"当前舾装绑定 {outfit.hull_blueprint}，建造船壳快照为 {expected_hull}",
            )
        return cls(
            hull,
            hull_sha,
            outfit,
            outfit_sha,
            _sha256_hex(
                obj["current_derived_ship_snapshot_sha256"],
                f"{path}.current_derived_ship_snapshot_sha256",
            ),
            _integer(obj["revision"], f"{path}.revision", 1),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "construction_hull_blueprint": self.construction_hull_blueprint.to_dict(),
            "construction_hull_blueprint_sha256": (
                self.construction_hull_blueprint_sha256
            ),
            "current_derived_ship_snapshot_sha256": (
                self.current_derived_ship_snapshot_sha256
            ),
            "current_outfit_plan": self.current_outfit_plan.to_dict(),
            "current_outfit_plan_sha256": self.current_outfit_plan_sha256,
            "revision": self.revision,
        }


@dataclass(frozen=True)
class ShipInstanceSnapshotInput:
    id: str
    version: int
    name: str
    fixture_level: str
    outfit_plan: ResourceReference
    derived_ship_snapshot_sha256: str
    current_hull_integrity_fraction: float
    sortie_configuration: ResourceReference
    sortie_configuration_sha256: str
    module_states: tuple[RuntimeModuleStateInput, ...]
    operational_state: ShipOperationalStateInput
    power_policy: RuntimePowerPolicyInput
    ammunition_state: ShipAmmunitionStateInput | None = None
    design_state: ShipDesignStateInput | None = None
    weapon_timeline_state: WeaponTimelineStateInput | None = None
    continuous_damage_state: ShipContinuousDamageStateInput | None = None
    crew_casualty_state: ShipCrewCasualtyStateInput | None = None

    @classmethod
    def parse(cls, resource: Any, path: str = "$") -> "ShipInstanceSnapshotInput":
        obj = _object(resource, path)
        _keys(
            obj,
            path,
            (
                "schema",
                "kind",
                "id",
                "version",
                "name",
                "fixture_level",
                "outfit_plan",
                "derived_ship_snapshot_sha256",
                "current_hull_integrity_fraction",
                "sortie_configuration",
                "sortie_configuration_sha256",
                "module_states",
                "operational_state",
                "power_policy",
            ),
            (
                "ammunition_state",
                "design_state",
                "weapon_timeline_state",
                "continuous_damage_state",
                "crew_casualty_state",
            ),
        )
        if obj["schema"] != SCHEMA_ID:
            raise ContractError("schema.unsupported", f"{path}.schema", str(obj["schema"]))
        if obj["kind"] != "ShipInstanceSnapshot":
            raise ContractError(
                "resource.kind_mismatch",
                f"{path}.kind",
                "必须是 ShipInstanceSnapshot",
            )
        fixture_level = _string(obj["fixture_level"], f"{path}.fixture_level")
        if fixture_level not in BALANCE_STATUSES:
            raise ContractError(
                "instance.fixture_level", f"{path}.fixture_level", fixture_level
            )
        states = tuple(
            sorted(
                (
                    RuntimeModuleStateInput.parse(
                        item, f"{path}.module_states[{index}]"
                    )
                    for index, item in enumerate(
                        _array(obj["module_states"], f"{path}.module_states")
                    )
                ),
                key=lambda item: item.instance_id,
            )
        )
        if len({item.instance_id for item in states}) != len(states):
            raise ContractError(
                "instance.module_state_duplicate",
                f"{path}.module_states",
                "模块实例状态不得重复",
            )
        hull_integrity = _number(
            obj["current_hull_integrity_fraction"],
            f"{path}.current_hull_integrity_fraction",
            0.0,
        )
        if hull_integrity > 1.0:
            raise ContractError(
                "instance.hull_integrity_fraction",
                f"{path}.current_hull_integrity_fraction",
                "当前船壳完整度必须位于 0～1",
            )
        operational_state = ShipOperationalStateInput.parse(
            obj["operational_state"], f"{path}.operational_state"
        )
        crew_casualty_state = (
            None
            if "crew_casualty_state" not in obj
            else ShipCrewCasualtyStateInput.parse(
                obj["crew_casualty_state"],
                f"{path}.crew_casualty_state",
            )
        )
        if crew_casualty_state is not None:
            operational_crew = {
                item.crew_type: item.count for item in operational_state.crew
            }
            fit_for_duty = {
                item.crew_type: item.fit_for_duty_count
                for item in crew_casualty_state.crew_statuses
                if item.fit_for_duty_count > 0
            }
            if operational_crew != fit_for_duty:
                raise ContractError(
                    "crew_casualty.fit_mismatch",
                    f"{path}.crew_casualty_state.crew_statuses",
                    "可执勤人数必须与 operational_state.crew 完全一致",
                )
        return cls(
            _resource_id(obj["id"], f"{path}.id"),
            _integer(obj["version"], f"{path}.version", 1),
            _string(obj["name"], f"{path}.name"),
            fixture_level,
            ResourceReference.parse(obj["outfit_plan"], f"{path}.outfit_plan"),
            _sha256_hex(
                obj["derived_ship_snapshot_sha256"],
                f"{path}.derived_ship_snapshot_sha256",
            ),
            hull_integrity,
            ResourceReference.parse(
                obj["sortie_configuration"], f"{path}.sortie_configuration"
            ),
            _sha256_hex(
                obj["sortie_configuration_sha256"],
                f"{path}.sortie_configuration_sha256",
            ),
            states,
            operational_state,
            RuntimePowerPolicyInput.parse(obj["power_policy"], f"{path}.power_policy"),
            (
                None
                if "ammunition_state" not in obj
                else ShipAmmunitionStateInput.parse(
                    obj["ammunition_state"], f"{path}.ammunition_state"
                )
            ),
            (
                None
                if "design_state" not in obj
                else ShipDesignStateInput.parse(
                    obj["design_state"], f"{path}.design_state"
                )
            ),
            (
                None
                if "weapon_timeline_state" not in obj
                else WeaponTimelineStateInput.parse(
                    obj["weapon_timeline_state"],
                    f"{path}.weapon_timeline_state",
                )
            ),
            (
                None
                if "continuous_damage_state" not in obj
                else ShipContinuousDamageStateInput.parse(
                    obj["continuous_damage_state"],
                    f"{path}.continuous_damage_state",
                )
            ),
            crew_casualty_state,
        )

    def to_dict(self) -> dict[str, Any]:
        result = {
            "current_hull_integrity_fraction": self.current_hull_integrity_fraction,
            "derived_ship_snapshot_sha256": self.derived_ship_snapshot_sha256,
            "fixture_level": self.fixture_level,
            "id": self.id,
            "kind": "ShipInstanceSnapshot",
            "module_states": [item.to_dict() for item in self.module_states],
            "name": self.name,
            "operational_state": self.operational_state.to_dict(),
            "outfit_plan": self.outfit_plan.to_dict(),
            "power_policy": self.power_policy.to_dict(),
            "schema": SCHEMA_ID,
            "sortie_configuration": self.sortie_configuration.to_dict(),
            "sortie_configuration_sha256": self.sortie_configuration_sha256,
            "version": self.version,
        }
        if self.ammunition_state is not None:
            result["ammunition_state"] = self.ammunition_state.to_dict()
        if self.design_state is not None:
            result["design_state"] = self.design_state.to_dict()
        if self.weapon_timeline_state is not None:
            result["weapon_timeline_state"] = self.weapon_timeline_state.to_dict()
        if self.continuous_damage_state is not None:
            result["continuous_damage_state"] = (
                self.continuous_damage_state.to_dict()
            )
        if self.crew_casualty_state is not None:
            result["crew_casualty_state"] = self.crew_casualty_state.to_dict()
        return result


@dataclass
class MaterialRegistry:
    structures: dict[ResourceReference, StructureMaterial]
    base_armors: dict[ResourceReference, BaseArmorMaterial]

    def __init__(self) -> None:
        self.structures = {}
        self.base_armors = {}

    def add_catalog(self, resource: Any, path: str = "$") -> None:
        obj = _object(resource, path)
        _keys(
            obj,
            path,
            ("schema", "kind", "id", "version", "name", "catalog_type", "materials"),
        )
        if obj["schema"] != SCHEMA_ID:
            raise ContractError("schema.unsupported", f"{path}.schema", str(obj["schema"]))
        if obj["kind"] != "MaterialCatalog":
            raise ContractError(
                "resource.kind_mismatch", f"{path}.kind", "必须是 MaterialCatalog"
            )
        _resource_id(obj["id"], f"{path}.id")
        _integer(obj["version"], f"{path}.version", 1)
        _string(obj["name"], f"{path}.name")
        catalog_type = _string(obj["catalog_type"], f"{path}.catalog_type")
        materials = _array(obj["materials"], f"{path}.materials")
        if not materials:
            raise ContractError("array.empty", f"{path}.materials", "材质目录不能为空")
        for index, material_value in enumerate(materials):
            material_path = f"{path}.materials[{index}]"
            if catalog_type == "structure":
                material = parse_structure_material(material_value, material_path)
                if material.reference in self.structures:
                    raise ContractError(
                        "resource.duplicate", material_path, str(material.reference)
                    )
                self.structures[material.reference] = material
            elif catalog_type == "base_armor":
                material = parse_base_armor_material(material_value, material_path)
                if material.reference in self.base_armors:
                    raise ContractError(
                        "resource.duplicate", material_path, str(material.reference)
                    )
                self.base_armors[material.reference] = material
            else:
                raise ContractError(
                    "catalog.type_invalid", f"{path}.catalog_type", catalog_type
                )

    def structure(self, reference: ResourceReference, path: str) -> StructureMaterial:
        try:
            return self.structures[reference]
        except KeyError as error:
            raise ContractError(
                "resource.reference_missing", path, f"找不到结构材质 {reference}"
            ) from error

    def base_armor(self, reference: ResourceReference, path: str) -> BaseArmorMaterial:
        try:
            return self.base_armors[reference]
        except KeyError as error:
            raise ContractError(
                "resource.reference_missing", path, f"找不到基础装甲材质 {reference}"
            ) from error


def parse_structure_material(value: Any, path: str) -> StructureMaterial:
    obj = _object(value, path)
    _keys(
        obj,
        path,
        (
            "id",
            "version",
            "name",
            "density_kg_m3",
            "strength_coefficient",
            "durability_coefficient",
            "cost_coefficient",
            "work_difficulty",
        ),
    )
    return StructureMaterial(
        ResourceReference(
            _resource_id(obj["id"], f"{path}.id"),
            _integer(obj["version"], f"{path}.version", 1),
        ),
        _string(obj["name"], f"{path}.name"),
        _number(obj["density_kg_m3"], f"{path}.density_kg_m3", 0.0),
        _number(obj["strength_coefficient"], f"{path}.strength_coefficient", 0.0),
        _number(obj["durability_coefficient"], f"{path}.durability_coefficient", 0.0),
        _number(obj["cost_coefficient"], f"{path}.cost_coefficient", 0.0),
        _number(obj["work_difficulty"], f"{path}.work_difficulty", 0.0),
    )


def parse_base_armor_material(value: Any, path: str) -> BaseArmorMaterial:
    obj = _object(value, path)
    _keys(
        obj,
        path,
        (
            "id",
            "version",
            "name",
            "density_kg_m3",
            "protection_coefficient",
            "local_durability_coefficient",
            "shell_strength_coefficient",
            "cost_coefficient",
            "work_difficulty",
        ),
    )
    return BaseArmorMaterial(
        ResourceReference(
            _resource_id(obj["id"], f"{path}.id"),
            _integer(obj["version"], f"{path}.version", 1),
        ),
        _string(obj["name"], f"{path}.name"),
        _number(obj["density_kg_m3"], f"{path}.density_kg_m3", 0.0),
        _number(obj["protection_coefficient"], f"{path}.protection_coefficient", 0.0),
        _number(
            obj["local_durability_coefficient"],
            f"{path}.local_durability_coefficient",
            0.0,
        ),
        _number(
            obj["shell_strength_coefficient"],
            f"{path}.shell_strength_coefficient",
            0.0,
        ),
        _number(obj["cost_coefficient"], f"{path}.cost_coefficient", 0.0),
        _number(obj["work_difficulty"], f"{path}.work_difficulty", 0.0),
    )


@dataclass(frozen=True)
class EdgeArmorInput:
    material: ResourceReference
    thickness_m: float

    @classmethod
    def parse(cls, value: Any, path: str) -> "EdgeArmorInput":
        obj = _object(value, path)
        _keys(obj, path, ("material", "thickness_m"))
        return cls(
            ResourceReference.parse(obj["material"], f"{path}.material"),
            _number(obj["thickness_m"], f"{path}.thickness_m", 0.0),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"material": self.material.to_dict(), "thickness_m": self.thickness_m}


Point = tuple[float, float]


@dataclass(frozen=True)
class HullRegionInput:
    id: str
    vertices_m: tuple[Point, ...]
    edge_armor: tuple[EdgeArmorInput, ...]

    @classmethod
    def parse(cls, value: Any, path: str) -> "HullRegionInput":
        obj = _object(value, path)
        _keys(obj, path, ("id", "vertices_m", "edge_armor"))
        vertices_raw = _array(obj["vertices_m"], f"{path}.vertices_m")
        armor_raw = _array(obj["edge_armor"], f"{path}.edge_armor")
        if len(vertices_raw) < 3:
            raise ContractError("hull.polygon_degenerate", path, "至少需要三个端点")
        if len(armor_raw) != len(vertices_raw):
            raise ContractError(
                "hull.edge_armor_count", path, "edge_armor 必须与 vertices_m 等长"
            )
        vertices: list[Point] = []
        for index, point_value in enumerate(vertices_raw):
            point_path = f"{path}.vertices_m[{index}]"
            point = _array(point_value, point_path)
            if len(point) != 2:
                raise ContractError("hull.point_size", point_path, "坐标必须有两个分量")
            vertices.append(
                (_number(point[0], f"{point_path}[0]"), _number(point[1], f"{point_path}[1]"))
            )
        return cls(
            _resource_id(obj["id"], f"{path}.id"),
            tuple(vertices),
            tuple(
                EdgeArmorInput.parse(item, f"{path}.edge_armor[{index}]")
                for index, item in enumerate(armor_raw)
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "edge_armor": [item.to_dict() for item in self.edge_armor],
            "id": self.id,
            "vertices_m": [[x, y] for x, y in self.vertices_m],
        }


@dataclass(frozen=True)
class DeckInput:
    id: str
    level: int
    is_base: bool
    structure_material: ResourceReference
    regions: tuple[HullRegionInput, ...]

    @classmethod
    def parse(cls, value: Any, path: str) -> "DeckInput":
        obj = _object(value, path)
        _keys(obj, path, ("id", "level", "is_base", "structure_material", "regions"))
        regions_raw = _array(obj["regions"], f"{path}.regions")
        if not regions_raw:
            raise ContractError("array.empty", f"{path}.regions", "甲板至少需要一个区域")
        return cls(
            _resource_id(obj["id"], f"{path}.id"),
            _integer(obj["level"], f"{path}.level", 0),
            _boolean(obj["is_base"], f"{path}.is_base"),
            ResourceReference.parse(obj["structure_material"], f"{path}.structure_material"),
            tuple(
                HullRegionInput.parse(item, f"{path}.regions[{index}]")
                for index, item in enumerate(regions_raw)
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "is_base": self.is_base,
            "level": self.level,
            "regions": [region.to_dict() for region in self.regions],
            "structure_material": self.structure_material.to_dict(),
        }


@dataclass(frozen=True)
class GridInput:
    cell_size_m: float
    deck_height_m: float
    forward_axis: str
    symmetry_axis: str
    cic_origin_cell: tuple[int, int]

    @classmethod
    def parse(cls, value: Any, path: str) -> "GridInput":
        obj = _object(value, path)
        _keys(
            obj,
            path,
            ("cell_size_m", "deck_height_m", "forward_axis", "symmetry_axis", "cic_origin_cell"),
        )
        cell = _number(obj["cell_size_m"], f"{path}.cell_size_m", 0.0)
        height = _number(obj["deck_height_m"], f"{path}.deck_height_m", 0.0)
        forward = _string(obj["forward_axis"], f"{path}.forward_axis")
        symmetry = _string(obj["symmetry_axis"], f"{path}.symmetry_axis")
        origin_raw = _array(obj["cic_origin_cell"], f"{path}.cic_origin_cell")
        if len(origin_raw) != 2:
            raise ContractError("hull.cic_origin_size", path, "CIC 格坐标必须有两个分量")
        origin = (
            _integer(origin_raw[0], f"{path}.cic_origin_cell[0]"),
            _integer(origin_raw[1], f"{path}.cic_origin_cell[1]"),
        )
        if (cell, height, forward, symmetry, origin) != (5.0, 5.0, "+Y", "Y", (0, 0)):
            raise ContractError("hull.grid_convention", path, "v1alpha1 只接受固定五米 CIC 原点约定")
        return cls(cell, height, forward, symmetry, origin)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cell_size_m": self.cell_size_m,
            "cic_origin_cell": list(self.cic_origin_cell),
            "deck_height_m": self.deck_height_m,
            "forward_axis": self.forward_axis,
            "symmetry_axis": self.symmetry_axis,
        }


@dataclass(frozen=True)
class HullBlueprintInput:
    id: str
    version: int
    name: str
    fixture_level: str
    grid: GridInput
    decks: tuple[DeckInput, ...]

    @classmethod
    def parse(cls, resource: Any, path: str = "$") -> "HullBlueprintInput":
        obj = _object(resource, path)
        _keys(
            obj,
            path,
            ("schema", "kind", "id", "version", "name", "fixture_level", "grid", "decks"),
        )
        if obj["schema"] != SCHEMA_ID:
            raise ContractError("schema.unsupported", f"{path}.schema", str(obj["schema"]))
        if obj["kind"] != "HullBlueprint":
            raise ContractError(
                "resource.kind_mismatch", f"{path}.kind", "必须是 HullBlueprint"
            )
        fixture_level = _string(obj["fixture_level"], f"{path}.fixture_level")
        if fixture_level not in {"canonical_blueprint_fixture", "balance_reference"}:
            raise ContractError(
                "hull.fixture_level", f"{path}.fixture_level", fixture_level
            )
        decks_raw = _array(obj["decks"], f"{path}.decks")
        if not decks_raw:
            raise ContractError("array.empty", f"{path}.decks", "至少需要一层甲板")
        return cls(
            _resource_id(obj["id"], f"{path}.id"),
            _integer(obj["version"], f"{path}.version", 1),
            _string(obj["name"], f"{path}.name"),
            fixture_level,
            GridInput.parse(obj["grid"], f"{path}.grid"),
            tuple(DeckInput.parse(item, f"{path}.decks[{index}]") for index, item in enumerate(decks_raw)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "decks": [deck.to_dict() for deck in self.decks],
            "fixture_level": self.fixture_level,
            "grid": self.grid.to_dict(),
            "id": self.id,
            "kind": "HullBlueprint",
            "name": self.name,
            "schema": SCHEMA_ID,
            "version": self.version,
        }


def load_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def canonical_json(resource: Any) -> str:
    if hasattr(resource, "to_dict"):
        resource = resource.to_dict()
    return json.dumps(resource, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def canonical_sha256(resource: Any) -> str:
    return sha256(canonical_json(resource).encode("utf-8")).hexdigest()


def save_canonical_json(path: str | Path, resource: Any) -> None:
    Path(path).write_text(canonical_json(resource), encoding="utf-8")


def load_hull_blueprint(path: str | Path) -> HullBlueprintInput:
    return HullBlueprintInput.parse(load_json(path))


def load_material_registry(paths: Iterable[str | Path]) -> MaterialRegistry:
    registry = MaterialRegistry()
    for path in paths:
        registry.add_catalog(load_json(path), str(path))
    return registry


def load_hull_coating_catalog(path: str | Path) -> HullCoatingCatalog:
    return HullCoatingCatalog.parse(load_json(path), str(path))


def load_module_prototype_catalog(path: str | Path) -> ModulePrototypeCatalog:
    return ModulePrototypeCatalog.parse(load_json(path), str(path))


def load_outfit_plan(path: str | Path) -> OutfitPlanInput:
    return OutfitPlanInput.parse(load_json(path), str(path))


def load_sortie_configuration(path: str | Path) -> SortieConfigurationInput:
    return SortieConfigurationInput.parse(load_json(path), str(path))


def load_ship_instance_snapshot(path: str | Path) -> ShipInstanceSnapshotInput:
    return ShipInstanceSnapshotInput.parse(load_json(path), str(path))
