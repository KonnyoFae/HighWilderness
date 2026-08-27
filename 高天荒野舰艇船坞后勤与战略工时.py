"""《高天荒野》阶段 I2：船坞库存、作业报价、资源预留与战略工时。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from math import ceil, hypot, isfinite
from typing import Any, Iterable

from 高天荒野舰艇出航配置编译器 import validate_ship_ammunition_state
from 高天荒野舰艇出航配置编译器 import CompiledSortieState
from 高天荒野舰艇数据契约 import (
    ContractError,
    MaterialRegistry,
    RESOURCE_ID_PATTERN,
    ResourceReference,
    ShipAmmunitionStateInput,
    ShipInstanceSnapshotInput,
    canonical_sha256,
)
from 高天荒野舰艇无界面船壳编译器 import (
    DECK_EQUIVALENT_THICKNESS_M,
    JOINT_EQUIVALENT_THICKNESS_M,
    polygon_area,
)
from 高天荒野舰艇无界面舾装编译器 import DerivedShipSnapshot
from 高天荒野舰艇运行时参数编译器 import initialize_ship_instance_snapshot
from 高天荒野舰艇实例设计状态 import (
    current_design_sha256,
    transition_current_design,
)


SHIPYARD_LOGISTICS_INTERFACE_ID = "gaotian.shipyard-logistics/v1alpha1"
SHIPYARD_WORK_TIME_POLICY_ID = "gaotian.shipyard-work/confirmed-reserved-strategic-time/v1"
HULL_MATERIAL_ACCOUNTING_POLICY_ID = "gaotian.hull-material/exact-geometry-ceil-kilogram/v1"
MAINTENANCE_ACCOUNTING_POLICY_ID = "gaotian.maintenance/missing-mass-equivalent/v1"
LOGISTICS_SCHEMA_ID = "gaotian.ship-logistics/v1alpha1"
FIXTURE_LEVELS = {"contract_fixture", "prototype_unbalanced", "balance_reference"}
WORK_KINDS = {
    "ship_construction",
    "ship_damaged_module_dismantle",
    "ship_destroyed_residue_clearance",
    "ship_refit",
    "ship_repair",
    "ship_rearm",
    "ship_resupply",
}
WORK_ORDER_STATUSES = {
    "queued",
    "in_progress",
    "completed_unsettled",
    "artifact_applied_unsettled",
    "settled",
}
EPS = 1.0e-8


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
            "resource.id_invalid",
            path,
            "只能使用小写字母、数字、点、横线和下划线",
        )
    return result


def _integer(value: Any, path: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ContractError("type.integer", path, f"必须是不得小于 {minimum} 的整数")
    return value


def _number(value: Any, path: str, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError("type.number", path, "必须是数值")
    result = float(value)
    if not isfinite(result) or result < minimum:
        raise ContractError("value.number", path, f"必须是不得小于 {minimum} 的有限数")
    return result


def _optional_sha(value: Any, path: str) -> str | None:
    if value is None:
        return None
    result = _string(value, path)
    if len(result) != 64 or any(character not in "0123456789abcdef" for character in result):
        raise ContractError("value.sha256", path, "必须是六十四位小写十六进制 SHA-256")
    return result


def _unique(items: Iterable[Any], key, code: str, path: str) -> tuple[Any, ...]:
    result = tuple(sorted(items, key=key))
    values = [key(item) for item in result]
    if len(set(values)) != len(values):
        raise ContractError(code, path, "库存键不得重复")
    return result


@dataclass(frozen=True)
class MaterialStock:
    material: ResourceReference
    units: int

    @classmethod
    def parse(cls, value: Any, path: str) -> "MaterialStock":
        obj = _object(value, path)
        if set(obj) != {"material", "units"}:
            raise ContractError("object.keys", path, "必须只含 material 与 units")
        return cls(
            ResourceReference.parse(obj["material"], f"{path}.material"),
            _integer(obj["units"], f"{path}.units"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"material": self.material.to_dict(), "units": self.units}


@dataclass(frozen=True)
class ModuleStock:
    prototype: ResourceReference
    units: int

    @classmethod
    def parse(cls, value: Any, path: str) -> "ModuleStock":
        obj = _object(value, path)
        if set(obj) != {"prototype", "units"}:
            raise ContractError("object.keys", path, "必须只含 prototype 与 units")
        return cls(
            ResourceReference.parse(obj["prototype"], f"{path}.prototype"),
            _integer(obj["units"], f"{path}.units"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"prototype": self.prototype.to_dict(), "units": self.units}


@dataclass(frozen=True)
class MunitionStock:
    munition_id: str
    units: int

    @classmethod
    def parse(cls, value: Any, path: str) -> "MunitionStock":
        obj = _object(value, path)
        if set(obj) != {"munition_id", "units"}:
            raise ContractError("object.keys", path, "必须只含 munition_id 与 units")
        return cls(
            _resource_id(obj["munition_id"], f"{path}.munition_id"),
            _integer(obj["units"], f"{path}.units"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"munition_id": self.munition_id, "units": self.units}


@dataclass(frozen=True)
class LogisticsStockBundle:
    materials: tuple[MaterialStock, ...] = ()
    maintenance_material_units: int = 0
    modules: tuple[ModuleStock, ...] = ()
    munitions: tuple[MunitionStock, ...] = ()
    fuel_units: float = 0.0

    @classmethod
    def parse(cls, value: Any, path: str) -> "LogisticsStockBundle":
        obj = _object(value, path)
        required = {
            "materials",
            "maintenance_material_units",
            "modules",
            "munitions",
            "fuel_units",
        }
        if set(obj) != required:
            raise ContractError("object.keys", path, f"必须恰含 {sorted(required)}")
        materials = _unique(
            (
                MaterialStock.parse(item, f"{path}.materials[{index}]")
                for index, item in enumerate(_array(obj["materials"], f"{path}.materials"))
            ),
            lambda item: item.material,
            "logistics.material_duplicate",
            f"{path}.materials",
        )
        modules = _unique(
            (
                ModuleStock.parse(item, f"{path}.modules[{index}]")
                for index, item in enumerate(_array(obj["modules"], f"{path}.modules"))
            ),
            lambda item: item.prototype,
            "logistics.module_duplicate",
            f"{path}.modules",
        )
        munitions = _unique(
            (
                MunitionStock.parse(item, f"{path}.munitions[{index}]")
                for index, item in enumerate(_array(obj["munitions"], f"{path}.munitions"))
            ),
            lambda item: item.munition_id,
            "logistics.munition_duplicate",
            f"{path}.munitions",
        )
        return cls(
            materials,
            _integer(
                obj["maintenance_material_units"],
                f"{path}.maintenance_material_units",
            ),
            modules,
            munitions,
            _number(obj["fuel_units"], f"{path}.fuel_units"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "fuel_units": self.fuel_units,
            "maintenance_material_units": self.maintenance_material_units,
            "materials": [item.to_dict() for item in self.materials],
            "modules": [item.to_dict() for item in self.modules],
            "munitions": [item.to_dict() for item in self.munitions],
        }


@dataclass(frozen=True)
class ShipyardInventoryState:
    id: str
    version: int
    name: str
    fixture_level: str
    stock: LogisticsStockBundle

    @classmethod
    def parse(cls, value: Any, path: str = "$") -> "ShipyardInventoryState":
        obj = _object(value, path)
        required = {"schema", "kind", "id", "version", "name", "fixture_level", "stock"}
        if set(obj) != required:
            raise ContractError("object.keys", path, f"必须恰含 {sorted(required)}")
        if obj["schema"] != LOGISTICS_SCHEMA_ID or obj["kind"] != "ShipyardInventoryState":
            raise ContractError("resource.kind", path, "不是船坞库存资源")
        fixture = _string(obj["fixture_level"], f"{path}.fixture_level")
        if fixture not in FIXTURE_LEVELS:
            raise ContractError("logistics.fixture_level", f"{path}.fixture_level", fixture)
        return cls(
            _resource_id(obj["id"], f"{path}.id"),
            _integer(obj["version"], f"{path}.version", 1),
            _string(obj["name"], f"{path}.name"),
            fixture,
            LogisticsStockBundle.parse(obj["stock"], f"{path}.stock"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "fixture_level": self.fixture_level,
            "id": self.id,
            "kind": "ShipyardInventoryState",
            "name": self.name,
            "schema": LOGISTICS_SCHEMA_ID,
            "stock": self.stock.to_dict(),
            "version": self.version,
        }


@dataclass(frozen=True)
class ShipyardFacilityProfile:
    id: str
    version: int
    name: str
    fixture_level: str
    capabilities: tuple[str, ...]
    work_units_per_strategic_second: float

    @property
    def reference(self) -> ResourceReference:
        return ResourceReference(self.id, self.version)

    @classmethod
    def parse(cls, value: Any, path: str = "$") -> "ShipyardFacilityProfile":
        obj = _object(value, path)
        required = {
            "schema",
            "kind",
            "id",
            "version",
            "name",
            "fixture_level",
            "capabilities",
            "work_units_per_strategic_second",
        }
        if set(obj) != required:
            raise ContractError("object.keys", path, f"必须恰含 {sorted(required)}")
        if obj["schema"] != LOGISTICS_SCHEMA_ID or obj["kind"] != "ShipyardFacilityProfile":
            raise ContractError("resource.kind", path, "不是船坞设施资源")
        fixture = _string(obj["fixture_level"], f"{path}.fixture_level")
        if fixture not in FIXTURE_LEVELS:
            raise ContractError("logistics.fixture_level", f"{path}.fixture_level", fixture)
        capabilities = tuple(
            sorted(
                _string(item, f"{path}.capabilities[{index}]")
                for index, item in enumerate(
                    _array(obj["capabilities"], f"{path}.capabilities")
                )
            )
        )
        if len(set(capabilities)) != len(capabilities) or any(
            item not in WORK_KINDS for item in capabilities
        ):
            raise ContractError("logistics.capabilities", f"{path}.capabilities", "能力非法或重复")
        return cls(
            _resource_id(obj["id"], f"{path}.id"),
            _integer(obj["version"], f"{path}.version", 1),
            _string(obj["name"], f"{path}.name"),
            fixture,
            capabilities,
            _number(
                obj["work_units_per_strategic_second"],
                f"{path}.work_units_per_strategic_second",
                EPS,
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "capabilities": list(self.capabilities),
            "fixture_level": self.fixture_level,
            "id": self.id,
            "kind": "ShipyardFacilityProfile",
            "name": self.name,
            "schema": LOGISTICS_SCHEMA_ID,
            "version": self.version,
            "work_units_per_strategic_second": self.work_units_per_strategic_second,
        }


@dataclass(frozen=True)
class ShipyardWorkQuote:
    id: str
    kind: str
    fixture_level: str
    requirements: LogisticsStockBundle
    expected_outputs: LogisticsStockBundle
    work_units: float
    completion_artifact_kind: str
    source_design_sha256: str | None
    source_instance_sha256: str | None
    target_design_sha256: str | None
    target_state_sha256: str | None
    diagnostics: tuple[str, ...]

    @classmethod
    def parse(cls, value: Any, path: str) -> "ShipyardWorkQuote":
        obj = _object(value, path)
        required = {
            "id",
            "kind",
            "fixture_level",
            "requirements",
            "expected_outputs",
            "work_units",
            "completion_artifact_kind",
            "source_design_sha256",
            "source_instance_sha256",
            "target_design_sha256",
            "target_state_sha256",
            "diagnostics",
        }
        if set(obj) != required:
            raise ContractError("object.keys", path, f"必须恰含 {sorted(required)}")
        kind = _string(obj["kind"], f"{path}.kind")
        if kind not in WORK_KINDS:
            raise ContractError("logistics.work_kind", f"{path}.kind", kind)
        fixture = _string(obj["fixture_level"], f"{path}.fixture_level")
        if fixture not in FIXTURE_LEVELS:
            raise ContractError("logistics.fixture_level", f"{path}.fixture_level", fixture)
        return cls(
            _resource_id(obj["id"], f"{path}.id"),
            kind,
            fixture,
            LogisticsStockBundle.parse(obj["requirements"], f"{path}.requirements"),
            LogisticsStockBundle.parse(obj["expected_outputs"], f"{path}.expected_outputs"),
            _number(obj["work_units"], f"{path}.work_units", EPS),
            _string(obj["completion_artifact_kind"], f"{path}.completion_artifact_kind"),
            _optional_sha(obj["source_design_sha256"], f"{path}.source_design_sha256"),
            _optional_sha(obj["source_instance_sha256"], f"{path}.source_instance_sha256"),
            _optional_sha(obj["target_design_sha256"], f"{path}.target_design_sha256"),
            _optional_sha(obj["target_state_sha256"], f"{path}.target_state_sha256"),
            tuple(
                _string(item, f"{path}.diagnostics[{index}]")
                for index, item in enumerate(
                    _array(obj["diagnostics"], f"{path}.diagnostics")
                )
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "completion_artifact_kind": self.completion_artifact_kind,
            "diagnostics": list(self.diagnostics),
            "expected_outputs": self.expected_outputs.to_dict(),
            "fixture_level": self.fixture_level,
            "id": self.id,
            "kind": self.kind,
            "requirements": self.requirements.to_dict(),
            "source_design_sha256": self.source_design_sha256,
            "source_instance_sha256": self.source_instance_sha256,
            "target_design_sha256": self.target_design_sha256,
            "target_state_sha256": self.target_state_sha256,
            "work_units": self.work_units,
        }


@dataclass(frozen=True)
class ShipyardWorkOrder:
    id: str
    version: int
    name: str
    fixture_level: str
    facility: ResourceReference
    quote: ShipyardWorkQuote
    status: str
    confirmed_world_time_s: float
    last_progress_world_time_s: float
    remaining_work_units: float

    @classmethod
    def parse(cls, value: Any, path: str = "$") -> "ShipyardWorkOrder":
        obj = _object(value, path)
        required = {
            "schema",
            "kind",
            "id",
            "version",
            "name",
            "fixture_level",
            "facility",
            "quote",
            "status",
            "confirmed_world_time_s",
            "last_progress_world_time_s",
            "remaining_work_units",
        }
        if set(obj) != required:
            raise ContractError("object.keys", path, f"必须恰含 {sorted(required)}")
        if obj["schema"] != LOGISTICS_SCHEMA_ID or obj["kind"] != "ShipyardWorkOrder":
            raise ContractError("resource.kind", path, "不是船坞作业订单")
        status = _string(obj["status"], f"{path}.status")
        if status not in WORK_ORDER_STATUSES:
            raise ContractError("logistics.order_status", f"{path}.status", status)
        confirmed = _number(obj["confirmed_world_time_s"], f"{path}.confirmed_world_time_s")
        last = _number(obj["last_progress_world_time_s"], f"{path}.last_progress_world_time_s")
        if last < confirmed:
            raise ContractError("logistics.order_time", path, "最后推进时间不得早于确认时间")
        remaining = _number(obj["remaining_work_units"], f"{path}.remaining_work_units")
        if status in {
            "completed_unsettled",
            "artifact_applied_unsettled",
            "settled",
        } and remaining > EPS:
            raise ContractError("logistics.order_completion", path, "完成订单不得保留工作量")
        fixture = _string(obj["fixture_level"], f"{path}.fixture_level")
        if fixture not in FIXTURE_LEVELS:
            raise ContractError("logistics.fixture_level", f"{path}.fixture_level", fixture)
        return cls(
            _resource_id(obj["id"], f"{path}.id"),
            _integer(obj["version"], f"{path}.version", 1),
            _string(obj["name"], f"{path}.name"),
            fixture,
            ResourceReference.parse(obj["facility"], f"{path}.facility"),
            ShipyardWorkQuote.parse(obj["quote"], f"{path}.quote"),
            status,
            confirmed,
            last,
            remaining,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "confirmed_world_time_s": self.confirmed_world_time_s,
            "facility": self.facility.to_dict(),
            "fixture_level": self.fixture_level,
            "id": self.id,
            "kind": "ShipyardWorkOrder",
            "last_progress_world_time_s": self.last_progress_world_time_s,
            "name": self.name,
            "quote": self.quote.to_dict(),
            "remaining_work_units": self.remaining_work_units,
            "schema": LOGISTICS_SCHEMA_ID,
            "status": self.status,
            "version": self.version,
        }


@dataclass(frozen=True)
class WorkOrderConfirmation:
    inventory: ShipyardInventoryState
    order: ShipyardWorkOrder


@dataclass(frozen=True)
class WorkOrderSettlement:
    inventory: ShipyardInventoryState
    order: ShipyardWorkOrder


@dataclass(frozen=True)
class DesignWorkArtifactApplication:
    instance: ShipInstanceSnapshotInput
    order: ShipyardWorkOrder


def _material_map(items: tuple[MaterialStock, ...]) -> dict[ResourceReference, int]:
    return {item.material: item.units for item in items}


def _module_map(items: tuple[ModuleStock, ...]) -> dict[ResourceReference, int]:
    return {item.prototype: item.units for item in items}


def _munition_map(items: tuple[MunitionStock, ...]) -> dict[str, int]:
    return {item.munition_id: item.units for item in items}


def _normalized_bundle(
    materials: dict[ResourceReference, int] | None = None,
    maintenance_material_units: int = 0,
    modules: dict[ResourceReference, int] | None = None,
    munitions: dict[str, int] | None = None,
    fuel_units: float = 0.0,
) -> LogisticsStockBundle:
    return LogisticsStockBundle(
        tuple(
            MaterialStock(reference, units)
            for reference, units in sorted((materials or {}).items())
            if units > 0
        ),
        maintenance_material_units,
        tuple(
            ModuleStock(reference, units)
            for reference, units in sorted((modules or {}).items())
            if units > 0
        ),
        tuple(
            MunitionStock(munition_id, units)
            for munition_id, units in sorted((munitions or {}).items())
            if units > 0
        ),
        fuel_units,
    )


def _apply_stock_bundle(
    inventory: ShipyardInventoryState,
    bundle: LogisticsStockBundle,
    *,
    sign: int,
) -> ShipyardInventoryState:
    current = inventory.stock
    materials = _material_map(current.materials)
    modules = _module_map(current.modules)
    munitions = _munition_map(current.munitions)
    if sign < 0:
        missing: list[str] = []
        for item in bundle.materials:
            if materials.get(item.material, 0) < item.units:
                missing.append(f"material:{item.material}")
        for item in bundle.modules:
            if modules.get(item.prototype, 0) < item.units:
                missing.append(f"module:{item.prototype}")
        for item in bundle.munitions:
            if munitions.get(item.munition_id, 0) < item.units:
                missing.append(f"munition:{item.munition_id}")
        if current.maintenance_material_units < bundle.maintenance_material_units:
            missing.append("maintenance_material")
        if current.fuel_units + EPS < bundle.fuel_units:
            missing.append("fuel")
        if missing:
            raise ContractError(
                "logistics.inventory_insufficient",
                "$.stock",
                f"库存不足：{sorted(missing)}",
            )
    for item in bundle.materials:
        materials[item.material] = materials.get(item.material, 0) + sign * item.units
    for item in bundle.modules:
        modules[item.prototype] = modules.get(item.prototype, 0) + sign * item.units
    for item in bundle.munitions:
        munitions[item.munition_id] = munitions.get(item.munition_id, 0) + sign * item.units
    maintenance = current.maintenance_material_units + sign * bundle.maintenance_material_units
    fuel = current.fuel_units + sign * bundle.fuel_units
    return replace(
        inventory,
        version=inventory.version + 1,
        stock=_normalized_bundle(materials, maintenance, modules, munitions, fuel),
    )


def confirm_work_order(
    quote: ShipyardWorkQuote,
    inventory: ShipyardInventoryState,
    facility: ShipyardFacilityProfile,
    *,
    order_id: str,
    order_name: str,
    current_world_time_s: float,
) -> WorkOrderConfirmation:
    if quote.kind not in facility.capabilities:
        raise ContractError(
            "logistics.facility_capability_missing",
            "$.facility.capabilities",
            f"设施不支持 {quote.kind}",
        )
    world_time = _number(current_world_time_s, "$.current_world_time_s")
    normalized_order_id = _resource_id(order_id, "$.order_id")
    normalized_order_name = _string(order_name, "$.order_name")
    reserved_inventory = _apply_stock_bundle(inventory, quote.requirements, sign=-1)
    order = ShipyardWorkOrder(
        normalized_order_id,
        1,
        normalized_order_name,
        quote.fixture_level,
        facility.reference,
        quote,
        "queued",
        world_time,
        world_time,
        quote.work_units,
    )
    return WorkOrderConfirmation(reserved_inventory, order)


def advance_work_order(
    order: ShipyardWorkOrder,
    facility: ShipyardFacilityProfile,
    *,
    current_world_time_s: float,
) -> ShipyardWorkOrder:
    if order.status in {
        "completed_unsettled",
        "artifact_applied_unsettled",
        "settled",
    }:
        return order
    if order.facility != facility.reference:
        raise ContractError(
            "logistics.facility_reference_mismatch",
            "$.facility",
            "作业订单必须由确认时的精确设施版本推进",
        )
    world_time = _number(current_world_time_s, "$.current_world_time_s")
    if world_time < order.last_progress_world_time_s:
        raise ContractError("logistics.world_time_reversed", "$.current_world_time_s", "战略时间不能倒退")
    elapsed = world_time - order.last_progress_world_time_s
    remaining = max(
        0.0,
        order.remaining_work_units
        - elapsed * facility.work_units_per_strategic_second,
    )
    if remaining <= EPS:
        status = "completed_unsettled"
        remaining = 0.0
    elif elapsed > EPS:
        status = "in_progress"
    else:
        status = order.status
    return replace(
        order,
        version=order.version + 1,
        status=status,
        last_progress_world_time_s=world_time,
        remaining_work_units=remaining,
    )


def settle_completed_work_order(
    order: ShipyardWorkOrder,
    inventory: ShipyardInventoryState,
) -> WorkOrderSettlement:
    design_kinds = {
        "ship_refit",
        "ship_damaged_module_dismantle",
        "ship_destroyed_residue_clearance",
    }
    if order.quote.kind in design_kinds:
        expected_status = "artifact_applied_unsettled"
    else:
        expected_status = "completed_unsettled"
    if order.status != expected_status:
        raise ContractError(
            "logistics.order_not_ready_to_settle",
            "$.status",
            (
                "改变当前舾装的订单必须先原子应用实例迁移；"
                "其他订单必须完成且尚未结算"
            ),
        )
    settled_inventory = _apply_stock_bundle(
        inventory, order.quote.expected_outputs, sign=1
    )
    return WorkOrderSettlement(
        settled_inventory,
        replace(order, version=order.version + 1, status="settled"),
    )


def _require_completed_artifact_order(
    order: ShipyardWorkOrder,
    expected_kind: str,
) -> None:
    if order.status != "completed_unsettled" or order.quote.kind != expected_kind:
        raise ContractError(
            "logistics.artifact_order_not_ready",
            "$.status",
            f"必须是已完成、未结算的 {expected_kind} 订单",
        )


def _require_source_instance(
    quote: ShipyardWorkQuote,
    snapshot: DerivedShipSnapshot,
    instance: ShipInstanceSnapshotInput,
) -> None:
    if quote.source_design_sha256 != snapshot.source_sha256:
        raise ContractError(
            "logistics.artifact_design_mismatch",
            "$.source_design_sha256",
            "订单来源设计已经变化",
        )
    if quote.source_instance_sha256 != canonical_sha256(instance):
        raise ContractError(
            "logistics.artifact_instance_mismatch",
            "$.source_instance_sha256",
            "订单确认后的舰艇实例状态已经变化",
        )


def complete_construction_artifact(
    order: ShipyardWorkOrder,
    snapshot: DerivedShipSnapshot,
    sortie: CompiledSortieState,
) -> ShipInstanceSnapshotInput:
    """完成资源与工时结算后，用既有唯一初始化链生成新舰实例。"""

    _require_completed_artifact_order(order, "ship_construction")
    if order.quote.target_design_sha256 != snapshot.source_sha256:
        raise ContractError(
            "logistics.artifact_design_mismatch",
            "$.target_design_sha256",
            "订单目标设计已经变化",
        )
    return initialize_ship_instance_snapshot(
        snapshot,
        sortie,
        embed_design_state=True,
    )


def complete_repair_artifact(
    order: ShipyardWorkOrder,
    snapshot: DerivedShipSnapshot,
    instance: ShipInstanceSnapshotInput,
) -> ShipInstanceSnapshotInput:
    """完全修复船壳及仍非残骸的模块；归零残骸保持占位。"""

    _require_completed_artifact_order(order, "ship_repair")
    _require_source_instance(order.quote, snapshot, instance)
    modules = {item.id: item for item in snapshot.outfit.instances}
    return replace(
        instance,
        current_hull_integrity_fraction=1.0,
        module_states=tuple(
            replace(
                state,
                current_durability_points=modules[
                    state.instance_id
                ].prototype.durability_points,
            )
            if state.current_durability_points > EPS
            else state
            for state in instance.module_states
        ),
    )


def complete_rearm_artifact(
    order: ShipyardWorkOrder,
    snapshot: DerivedShipSnapshot,
    instance: ShipInstanceSnapshotInput,
    desired_state: ShipAmmunitionStateInput,
) -> ShipInstanceSnapshotInput:
    _require_completed_artifact_order(order, "ship_rearm")
    _require_source_instance(order.quote, snapshot, instance)
    validate_ship_ammunition_state(
        snapshot,
        desired_state,
        namespace="logistics",
        path_prefix="$.desired_ammunition_state",
    )
    if order.quote.target_state_sha256 != canonical_sha256(desired_state):
        raise ContractError(
            "logistics.artifact_target_state_mismatch",
            "$.target_state_sha256",
            "重新武装目标状态已经变化",
        )
    return replace(instance, ammunition_state=desired_state)


def complete_resupply_artifact(
    order: ShipyardWorkOrder,
    snapshot: DerivedShipSnapshot,
    instance: ShipInstanceSnapshotInput,
) -> ShipInstanceSnapshotInput:
    _require_completed_artifact_order(order, "ship_resupply")
    _require_source_instance(order.quote, snapshot, instance)
    target_fuel = instance.operational_state.fuel_units + order.quote.requirements.fuel_units
    return replace(
        instance,
        operational_state=replace(
            instance.operational_state,
            fuel_units=target_fuel,
        ),
    )


def _apply_design_artifact(
    order: ShipyardWorkOrder,
    source_snapshot: DerivedShipSnapshot,
    source_instance: ShipInstanceSnapshotInput,
    target_snapshot: DerivedShipSnapshot,
) -> DesignWorkArtifactApplication:
    _require_source_instance(order.quote, source_snapshot, source_instance)
    if order.quote.target_design_sha256 != target_snapshot.source_sha256:
        raise ContractError(
            "logistics.artifact_design_mismatch",
            "$.target_design_sha256",
            "订单目标设计已经变化",
        )
    transitioned = transition_current_design(
        source_snapshot,
        source_instance,
        target_snapshot,
    )
    return DesignWorkArtifactApplication(
        transitioned,
        replace(
            order,
            version=order.version + 1,
            status="artifact_applied_unsettled",
        ),
    )


def complete_refit_artifact(
    order: ShipyardWorkOrder,
    source_snapshot: DerivedShipSnapshot,
    source_instance: ShipInstanceSnapshotInput,
    target_snapshot: DerivedShipSnapshot,
) -> DesignWorkArtifactApplication:
    _require_completed_artifact_order(order, "ship_refit")
    return _apply_design_artifact(
        order,
        source_snapshot,
        source_instance,
        target_snapshot,
    )


def complete_destroyed_residue_clearance_artifact(
    order: ShipyardWorkOrder,
    source_snapshot: DerivedShipSnapshot,
    source_instance: ShipInstanceSnapshotInput,
    target_snapshot: DerivedShipSnapshot,
) -> DesignWorkArtifactApplication:
    _require_completed_artifact_order(order, "ship_destroyed_residue_clearance")
    return _apply_design_artifact(
        order,
        source_snapshot,
        source_instance,
        target_snapshot,
    )


def complete_damaged_module_dismantle_artifact(
    order: ShipyardWorkOrder,
    source_snapshot: DerivedShipSnapshot,
    source_instance: ShipInstanceSnapshotInput,
    target_snapshot: DerivedShipSnapshot,
    *,
    module_instance_id: str,
    resolved_success: bool,
) -> DesignWorkArtifactApplication:
    _require_completed_artifact_order(order, "ship_damaged_module_dismantle")
    outcome_sha = canonical_sha256(
        {
            "module_instance_id": module_instance_id,
            "resolved_success": resolved_success,
        }
    )
    if order.quote.target_state_sha256 != outcome_sha:
        raise ContractError(
            "logistics.artifact_target_state_mismatch",
            "$.target_state_sha256",
            "拆卸成功/失败结算与确认订单时冻结的结果不一致",
        )
    if resolved_success:
        return _apply_design_artifact(
            order,
            source_snapshot,
            source_instance,
            target_snapshot,
        )
    _require_source_instance(order.quote, source_snapshot, source_instance)
    if target_snapshot.source_sha256 != source_snapshot.source_sha256:
        raise ContractError(
            "logistics.failed_dismantle_changed_design",
            "$.target_design",
            "拆卸失败不得改变舰艇当前舾装",
        )
    return DesignWorkArtifactApplication(
        source_instance,
        replace(
            order,
            version=order.version + 1,
            status="artifact_applied_unsettled",
        ),
    )


def _hull_material_bill(
    snapshot: DerivedShipSnapshot,
    registry: MaterialRegistry,
) -> tuple[dict[ResourceReference, int], float]:
    masses: dict[ResourceReference, float] = {}
    work_units = 0.0
    for deck_index, deck in enumerate(snapshot.hull.normalized_blueprint.decks):
        structure = registry.structure(
            deck.structure_material, f"$.decks[{deck_index}].structure_material"
        )
        thickness = DECK_EQUIVALENT_THICKNESS_M + (
            JOINT_EQUIVALENT_THICKNESS_M if deck.level > 0 else 0.0
        )
        structure_mass = sum(
            polygon_area(region.vertices_m) * thickness * structure.density_kg_m3
            for region in deck.regions
        )
        masses[structure.reference] = masses.get(structure.reference, 0.0) + structure_mass
        work_units += structure_mass * structure.work_difficulty
        for region_index, region in enumerate(deck.regions):
            for edge_index, (start, end, edge_input) in enumerate(
                zip(
                    region.vertices_m,
                    region.vertices_m[1:] + (region.vertices_m[0],),
                    region.edge_armor,
                )
            ):
                armor = registry.base_armor(
                    edge_input.material,
                    f"$.decks[{deck_index}].regions[{region_index}].edge_armor[{edge_index}].material",
                )
                edge_mass = (
                    hypot(end[0] - start[0], end[1] - start[1])
                    * snapshot.hull.normalized_blueprint.grid.deck_height_m
                    * edge_input.thickness_m
                    * armor.density_kg_m3
                )
                masses[armor.reference] = masses.get(armor.reference, 0.0) + edge_mass
                work_units += edge_mass * armor.work_difficulty
    return (
        {reference: ceil(max(0.0, mass - EPS)) for reference, mass in masses.items()},
        work_units,
    )


def quote_ship_construction(
    quote_id: str,
    snapshot: DerivedShipSnapshot,
    registry: MaterialRegistry,
) -> ShipyardWorkQuote:
    materials, hull_work = _hull_material_bill(snapshot, registry)
    modules: dict[ResourceReference, int] = {}
    for instance in snapshot.outfit.instances:
        reference = instance.prototype.reference
        modules[reference] = modules.get(reference, 0) + 1
    work_units = hull_work + sum(
        instance.prototype.mass_kg for instance in snapshot.outfit.instances
    )
    return ShipyardWorkQuote(
        _resource_id(quote_id, "$.quote_id"),
        "ship_construction",
        snapshot.outfit.normalized_plan.fixture_level,
        _normalized_bundle(materials=materials, modules=modules),
        LogisticsStockBundle(),
        work_units,
        "new_ship_instance",
        None,
        None,
        snapshot.source_sha256,
        None,
        (
            "hull_coating_material_and_work_deferred",
            "module_internal_bill_of_materials_deferred_stocked_modules_required",
        ),
    )


def quote_ship_repair(
    quote_id: str,
    snapshot: DerivedShipSnapshot,
    instance: ShipInstanceSnapshotInput,
) -> ShipyardWorkQuote:
    if current_design_sha256(instance) != snapshot.source_sha256:
        raise ContractError("logistics.instance_design_mismatch", "$.source_instance", "实例与设计快照不匹配")
    continuous_damage = instance.continuous_damage_state
    if continuous_damage is not None and continuous_damage.fire_incidents:
        raise ContractError(
            "logistics.active_fire",
            "$.source_instance.continuous_damage_state.fire_incidents",
            "存在活动火灾时不能创建船坞维修报价",
        )
    modules = {item.id: item for item in snapshot.outfit.instances}
    missing_mass_equivalent = snapshot.hull.hull_mass_kg * (
        1.0 - instance.current_hull_integrity_fraction
    )
    repairable: list[str] = []
    residues: list[str] = []
    for state in instance.module_states:
        module = modules[state.instance_id]
        maximum = module.prototype.durability_points
        if state.current_durability_points <= EPS:
            residues.append(state.instance_id)
            continue
        if state.current_durability_points < maximum - EPS:
            repairable.append(state.instance_id)
            missing_mass_equivalent += module.prototype.mass_kg * (
                1.0 - state.current_durability_points / maximum
            )
    units = ceil(max(0.0, missing_mass_equivalent - EPS))
    if units <= 0:
        raise ContractError("logistics.nothing_to_repair", "$.source_instance", "舰艇没有可维修损伤")
    diagnostics = [f"repairable_module:{item}" for item in sorted(repairable)]
    diagnostics.extend(f"destroyed_residue_requires_clearance:{item}" for item in sorted(residues))
    return ShipyardWorkQuote(
        _resource_id(quote_id, "$.quote_id"),
        "ship_repair",
        instance.fixture_level,
        _normalized_bundle(maintenance_material_units=units),
        LogisticsStockBundle(),
        missing_mass_equivalent,
        "repaired_ship_instance_state",
        snapshot.source_sha256,
        canonical_sha256(instance),
        snapshot.source_sha256,
        None,
        tuple(diagnostics),
    )


def _ammunition_totals(state: ShipAmmunitionStateInput) -> dict[str, int]:
    totals: dict[str, int] = {}
    for magazine in state.magazines:
        for item in magazine.inventory:
            totals[item.munition_id] = totals.get(item.munition_id, 0) + item.units
    for weapon in state.weapons:
        if weapon.munition_id is not None:
            totals[weapon.munition_id] = totals.get(weapon.munition_id, 0) + weapon.ready_rounds
    return totals


def quote_ship_rearm(
    quote_id: str,
    snapshot: DerivedShipSnapshot,
    instance: ShipInstanceSnapshotInput,
    desired_state: ShipAmmunitionStateInput,
) -> ShipyardWorkQuote:
    if instance.ammunition_state is None:
        raise ContractError("logistics.ammunition_state_missing", "$.ammunition_state", "实例尚无弹药状态")
    validate_ship_ammunition_state(
        snapshot,
        desired_state,
        namespace="logistics",
        path_prefix="$.desired_ammunition_state",
    )
    current = _ammunition_totals(instance.ammunition_state)
    desired = _ammunition_totals(desired_state)
    if any(desired.get(key, 0) < value for key, value in current.items()):
        raise ContractError(
            "logistics.rearm_cannot_remove_ammunition",
            "$.desired_ammunition_state",
            "重新武装订单不能用于卸载现有弹药",
        )
    required = {
        munition_id: units - current.get(munition_id, 0)
        for munition_id, units in desired.items()
        if units > current.get(munition_id, 0)
    }
    if not required:
        raise ContractError("logistics.nothing_to_rearm", "$.desired_ammunition_state", "没有需要补充的弹药")
    return ShipyardWorkQuote(
        _resource_id(quote_id, "$.quote_id"),
        "ship_rearm",
        instance.fixture_level,
        _normalized_bundle(munitions=required),
        LogisticsStockBundle(),
        float(sum(required.values())),
        "ammunition_state",
        snapshot.source_sha256,
        canonical_sha256(instance),
        snapshot.source_sha256,
        canonical_sha256(desired_state),
        ("munition_resource_conversion_and_rearm_rate_unbalanced",),
    )


def quote_ship_resupply(
    quote_id: str,
    snapshot: DerivedShipSnapshot,
    instance: ShipInstanceSnapshotInput,
    *,
    desired_fuel_units: float,
) -> ShipyardWorkQuote:
    target = _number(desired_fuel_units, "$.desired_fuel_units")
    capacity = sum(
        float(item.prototype.capability.to_dict()["fuel_capacity_units"])
        for item in snapshot.outfit.instances
        if item.prototype.category == "lift_fuel_tank"
    )
    current = instance.operational_state.fuel_units
    if target > capacity + EPS:
        raise ContractError("logistics.fuel_capacity_exceeded", "$.desired_fuel_units", f"目标 {target} 超过容量 {capacity}")
    if target <= current + EPS:
        raise ContractError("logistics.nothing_to_resupply", "$.desired_fuel_units", "目标燃料必须高于当前燃料")
    required = target - current
    return ShipyardWorkQuote(
        _resource_id(quote_id, "$.quote_id"),
        "ship_resupply",
        instance.fixture_level,
        _normalized_bundle(fuel_units=required),
        LogisticsStockBundle(),
        required,
        "operational_fuel_state",
        snapshot.source_sha256,
        canonical_sha256(instance),
        snapshot.source_sha256,
        None,
        ("fuel_purchase_price_and_transfer_rate_unbalanced",),
    )


def quote_ship_refit(
    quote_id: str,
    source_snapshot: DerivedShipSnapshot,
    source_instance: ShipInstanceSnapshotInput,
    target_snapshot: DerivedShipSnapshot,
) -> ShipyardWorkQuote:
    if current_design_sha256(source_instance) != source_snapshot.source_sha256:
        raise ContractError("logistics.instance_design_mismatch", "$.source_instance", "实例与来源设计不匹配")
    if source_snapshot.hull.source_sha256 != target_snapshot.hull.source_sha256:
        raise ContractError(
            "logistics.refit_hull_mismatch",
            "$.target_design",
            "首版改装必须使用同一精确船壳蓝图",
        )
    source_modules = {item.id: item for item in source_snapshot.outfit.instances}
    target_modules = {item.id: item for item in target_snapshot.outfit.instances}
    unchanged = {
        instance_id
        for instance_id in source_modules.keys() & target_modules.keys()
        if source_modules[instance_id].to_dict() == target_modules[instance_id].to_dict()
    }
    removed = [source_modules[key] for key in sorted(source_modules.keys() - unchanged)]
    added = [target_modules[key] for key in sorted(target_modules.keys() - unchanged)]
    if not removed and not added:
        raise ContractError("logistics.nothing_to_refit", "$.target_design", "目标舾装与当前舾装相同")
    requirements: dict[ResourceReference, int] = {}
    for module in added:
        reference = module.prototype.reference
        requirements[reference] = requirements.get(reference, 0) + 1
    states = {item.instance_id: item for item in source_instance.module_states}
    outputs: dict[ResourceReference, int] = {}
    diagnostics: list[str] = []
    for module in removed:
        state = states[module.id]
        if state.current_durability_points >= module.prototype.durability_points - EPS:
            reference = module.prototype.reference
            outputs[reference] = outputs.get(reference, 0) + 1
        elif state.current_durability_points <= EPS:
            raise ContractError(
                "logistics.refit_destroyed_residue_requires_clearance",
                f"$.source_instance.module_states.{module.id}",
                "彻底损毁的模块残骸必须先清理，不能直接改装替换",
            )
        else:
            raise ContractError(
                "logistics.refit_damaged_module_requires_dismantle",
                f"$.source_instance.module_states.{module.id}",
                "受损模块必须先通过独立拆卸作业结算成功与否，不能在改装订单中隐式移除",
            )
    diagnostics.append("installation_material_consumption_unbalanced")
    # 报价阶段先执行一次无副作用迁移校验，避免在资源预留与工时结束后才发现
    # 弹药未卸空、货物仍占用待拆货舱或当前设计快照不一致。
    transition_current_design(source_snapshot, source_instance, target_snapshot)
    work_units = sum(item.prototype.mass_kg for item in removed + added)
    return ShipyardWorkQuote(
        _resource_id(quote_id, "$.quote_id"),
        "ship_refit",
        source_instance.fixture_level,
        _normalized_bundle(modules=requirements),
        _normalized_bundle(modules=outputs),
        work_units,
        "refitted_ship_current_design_snapshot",
        source_snapshot.source_sha256,
        canonical_sha256(source_instance),
        target_snapshot.source_sha256,
        None,
        tuple(sorted(diagnostics)),
    )


def _validate_single_module_removal(
    source_snapshot: DerivedShipSnapshot,
    target_snapshot: DerivedShipSnapshot,
    module_instance_id: str,
):
    if source_snapshot.hull.source_sha256 != target_snapshot.hull.source_sha256:
        raise ContractError(
            "logistics.module_removal_hull_mismatch",
            "$.target_design",
            "拆卸或清残不得改变精确船壳",
        )
    source = {item.id: item for item in source_snapshot.outfit.instances}
    target = {item.id: item for item in target_snapshot.outfit.instances}
    if module_instance_id not in source:
        raise ContractError(
            "logistics.module_instance_missing",
            "$.module_instance_id",
            module_instance_id,
        )
    if set(target) != set(source) - {module_instance_id}:
        raise ContractError(
            "logistics.single_module_removal_required",
            "$.target_design",
            "目标舾装必须只移除指定模块，不得同时增删其他模块",
        )
    changed = sorted(
        instance_id
        for instance_id in target
        if target[instance_id].to_dict() != source[instance_id].to_dict()
    )
    if changed:
        raise ContractError(
            "logistics.single_module_removal_required",
            "$.target_design",
            f"其余模块不得变化：{changed}",
        )
    return source[module_instance_id]


def quote_destroyed_module_residue_clearance(
    quote_id: str,
    source_snapshot: DerivedShipSnapshot,
    source_instance: ShipInstanceSnapshotInput,
    target_snapshot: DerivedShipSnapshot,
    *,
    module_instance_id: str,
    resolved_scrap: MaterialStock,
) -> ShipyardWorkQuote:
    """清除归零残骸；废金属种类与整数产量由未来回收公式先行结算后注入。"""

    if current_design_sha256(source_instance) != source_snapshot.source_sha256:
        raise ContractError(
            "logistics.instance_design_mismatch",
            "$.source_instance",
            "实例与来源设计不匹配",
        )
    module = _validate_single_module_removal(
        source_snapshot,
        target_snapshot,
        module_instance_id,
    )
    states = {item.instance_id: item for item in source_instance.module_states}
    if states[module_instance_id].current_durability_points > EPS:
        raise ContractError(
            "logistics.residue_not_destroyed",
            f"$.source_instance.module_states.{module_instance_id}",
            "只有耐久归零且继续占位的模块才是可清理残骸",
        )
    if resolved_scrap.units <= 0:
        raise ContractError(
            "logistics.scrap_resolution_empty",
            "$.resolved_scrap.units",
            "清残订单必须由外部回收结算提供正整数废金属产量",
        )
    transition_current_design(source_snapshot, source_instance, target_snapshot)
    return ShipyardWorkQuote(
        _resource_id(quote_id, "$.quote_id"),
        "ship_destroyed_residue_clearance",
        source_instance.fixture_level,
        LogisticsStockBundle(),
        _normalized_bundle(
            materials={resolved_scrap.material: resolved_scrap.units}
        ),
        module.prototype.mass_kg,
        "cleared_residue_current_design_snapshot",
        source_snapshot.source_sha256,
        canonical_sha256(source_instance),
        target_snapshot.source_sha256,
        None,
        (
            f"cleared_destroyed_residue:{module_instance_id}",
            "scrap_material_identity_and_yield_externally_resolved",
        ),
    )


def quote_damaged_module_dismantle(
    quote_id: str,
    source_snapshot: DerivedShipSnapshot,
    source_instance: ShipInstanceSnapshotInput,
    target_snapshot: DerivedShipSnapshot,
    *,
    module_instance_id: str,
    resolved_success: bool,
) -> ShipyardWorkQuote:
    """冻结一次受损模块拆卸结果；本层不内置尚未敲定的成功率。"""

    if not isinstance(resolved_success, bool):
        raise ContractError(
            "logistics.dismantle_outcome_type",
            "$.resolved_success",
            "拆卸结果必须是外部概率系统给出的布尔值",
        )
    if current_design_sha256(source_instance) != source_snapshot.source_sha256:
        raise ContractError(
            "logistics.instance_design_mismatch",
            "$.source_instance",
            "实例与来源设计不匹配",
        )
    source_modules = {item.id: item for item in source_snapshot.outfit.instances}
    if module_instance_id not in source_modules:
        raise ContractError(
            "logistics.module_instance_missing",
            "$.module_instance_id",
            module_instance_id,
        )
    module = source_modules[module_instance_id]
    states = {item.instance_id: item for item in source_instance.module_states}
    durability = states[module_instance_id].current_durability_points
    maximum = module.prototype.durability_points
    if durability <= EPS:
        raise ContractError(
            "logistics.destroyed_module_requires_clearance",
            f"$.source_instance.module_states.{module_instance_id}",
            "耐久归零模块必须走残骸清理而非受损拆卸",
        )
    if durability >= maximum - EPS and not resolved_success:
        raise ContractError(
            "logistics.intact_dismantle_cannot_fail",
            "$.resolved_success",
            "完好模块拆卸必定成功，不得注入失败结果",
        )
    if resolved_success:
        _validate_single_module_removal(
            source_snapshot,
            target_snapshot,
            module_instance_id,
        )
        target_sha = target_snapshot.source_sha256
        outputs = _normalized_bundle(modules={module.prototype.reference: 1})
        transition_current_design(source_snapshot, source_instance, target_snapshot)
    else:
        if target_snapshot.source_sha256 != source_snapshot.source_sha256:
            raise ContractError(
                "logistics.failed_dismantle_changed_design",
                "$.target_design",
                "拆卸失败时目标设计必须保持为来源设计",
            )
        target_sha = source_snapshot.source_sha256
        outputs = LogisticsStockBundle()
    outcome_sha = canonical_sha256(
        {
            "module_instance_id": module_instance_id,
            "resolved_success": resolved_success,
        }
    )
    diagnostics = [
        f"dismantle_module:{module_instance_id}",
        "damaged_dismantle_probability_externally_resolved",
    ]
    if not resolved_success:
        diagnostics.append("dismantle_failed_current_design_unchanged")
    return ShipyardWorkQuote(
        _resource_id(quote_id, "$.quote_id"),
        "ship_damaged_module_dismantle",
        source_instance.fixture_level,
        LogisticsStockBundle(),
        outputs,
        module.prototype.mass_kg,
        (
            "dismantled_module_current_design_snapshot"
            if resolved_success
            else "unchanged_ship_instance_after_failed_dismantle"
        ),
        source_snapshot.source_sha256,
        canonical_sha256(source_instance),
        target_sha,
        outcome_sha,
        tuple(sorted(diagnostics)),
    )
