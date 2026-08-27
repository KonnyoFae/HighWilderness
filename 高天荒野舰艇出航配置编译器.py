"""《高天荒野》阶段 E 出航配置与当前载荷派生首切片。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from 高天荒野舰艇数据契约 import (
    ContractError,
    ResourceReference,
    ShipAmmunitionStateInput,
    ShipOperationalStateInput,
    SortieConfigurationInput,
    canonical_sha256,
)
from 高天荒野舰艇无界面舾装编译器 import (
    STANDARD_GRAVITY_MPS2,
    CompiledModuleInstance,
    DerivedShipSnapshot,
)


SORTIE_COMPILER_INTERFACE_ID = "gaotian.sortie-compiler/v1alpha1"
AMMUNITION_STATE_POLICY_ID = "gaotian.ammunition-state/ship-shared-pool/v1"
EPS = 1.0e-8


@dataclass(frozen=True)
class SortieWarning:
    code: str
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "path": self.path}


@dataclass(frozen=True)
class CargoMassContribution:
    cargo_id: str
    storage_instance_id: str
    mass_kg: float
    application_point_m: tuple[float, float]
    inertia_kg_m2: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "application_point_m": list(self.application_point_m),
            "cargo_id": self.cargo_id,
            "inertia_kg_m2": self.inertia_kg_m2,
            "mass_kg": self.mass_kg,
            "storage_instance_id": self.storage_instance_id,
        }


@dataclass(frozen=True)
class CompiledSortieState:
    configuration: SortieConfigurationInput
    source_sha256: str
    derived_snapshot_source_sha256: str
    design_mass_kg: float
    cargo_mass_kg: float
    current_mass_kg: float
    design_inertia_kg_m2: float
    cargo_inertia_kg_m2: float
    current_inertia_kg_m2: float
    lift_force_n: float
    current_lift_margin_n: float
    fuel_capacity_units: float
    fuel_units: float
    crew: tuple[tuple[str, int], ...]
    crew_present: bool
    crew_safety_lock_enabled: bool
    cargo_contributions: tuple[CargoMassContribution, ...]
    warnings: tuple[SortieWarning, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "cargo": {
                "contributions": [item.to_dict() for item in self.cargo_contributions],
                "inertia_kg_m2": self.cargo_inertia_kg_m2,
                "mass_kg": self.cargo_mass_kg,
            },
            "compiler_interface": SORTIE_COMPILER_INTERFACE_ID,
            "crew": {
                "counts": dict(self.crew),
                "crew_present": self.crew_present,
                "crew_safety_lock_enabled": self.crew_safety_lock_enabled,
            },
            "current": {
                "height_layer": self.configuration.height_layer,
                "inertia_kg_m2": self.current_inertia_kg_m2,
                "lift_margin_n": self.current_lift_margin_n,
                "mass_kg": self.current_mass_kg,
            },
            "downstream_runtime_capabilities": [
                "module_damage_power_and_crew_efficiency",
                "runtime_actuator_reaggregation_after_damage",
            ],
            "deferred_capabilities": [
                "safe_power_mode_upgrade_entitlement",
            ],
            "design": {
                "inertia_kg_m2": self.design_inertia_kg_m2,
                "mass_kg": self.design_mass_kg,
            },
            "fuel": {
                "capacity_units": self.fuel_capacity_units,
                "current_units": self.fuel_units,
                "mass_counted": False,
            },
            "mass_excluded_resources": [
                "crew",
                "fuel",
                "loaded_ammunition",
                "loaded_missiles",
            ],
            "normalized_configuration": self.configuration.to_dict(),
            "source_sha256": self.source_sha256,
            "sources": {
                "derived_snapshot_sha256": self.derived_snapshot_source_sha256,
                "outfit_plan": self.configuration.outfit_plan.to_dict(),
                "sortie_configuration": {
                    "id": self.configuration.id,
                    "version": self.configuration.version,
                },
            },
            "warnings": [warning.to_dict() for warning in self.warnings],
        }


@dataclass(frozen=True)
class CompiledOperationalState:
    state: ShipOperationalStateInput
    source_sha256: str
    cargo_mass_kg: float
    current_mass_kg: float
    cargo_inertia_kg_m2: float
    current_inertia_kg_m2: float
    current_lift_margin_n: float
    fuel_capacity_units: float
    fuel_units: float
    crew: tuple[tuple[str, int], ...]
    crew_present: bool
    crew_safety_lock_enabled: bool
    cargo_contributions: tuple[CargoMassContribution, ...]
    warnings: tuple[SortieWarning, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "cargo": {
                "contributions": [item.to_dict() for item in self.cargo_contributions],
                "inertia_kg_m2": self.cargo_inertia_kg_m2,
                "mass_kg": self.cargo_mass_kg,
            },
            "crew": {
                "counts": dict(self.crew),
                "crew_present": self.crew_present,
                "crew_safety_lock_enabled": self.crew_safety_lock_enabled,
            },
            "current": {
                "height_layer": self.state.height_layer,
                "inertia_kg_m2": self.current_inertia_kg_m2,
                "lift_margin_n": self.current_lift_margin_n,
                "mass_kg": self.current_mass_kg,
            },
            "fuel": {
                "capacity_units": self.fuel_capacity_units,
                "current_units": self.fuel_units,
                "mass_counted": False,
            },
            "normalized_operational_state": self.state.to_dict(),
            "source_sha256": self.source_sha256,
            "warnings": [warning.to_dict() for warning in self.warnings],
        }


def _instance_map(snapshot: DerivedShipSnapshot) -> dict[str, CompiledModuleInstance]:
    return {instance.id: instance for instance in snapshot.outfit.instances}


def _validate_outfit_reference(
    snapshot: DerivedShipSnapshot, configuration: SortieConfigurationInput
) -> None:
    plan = snapshot.outfit.normalized_plan
    expected = ResourceReference(plan.id, plan.version)
    if configuration.outfit_plan != expected:
        raise ContractError(
            "sortie.outfit_reference_mismatch",
            "$.outfit_plan",
            f"出航配置绑定 {configuration.outfit_plan}，派生快照来自 {expected}",
        )


def validate_ship_ammunition_state(
    snapshot: DerivedShipSnapshot,
    state: ShipAmmunitionStateInput,
    *,
    namespace: str,
    path_prefix: str,
) -> None:
    """把持久弹药状态与当前精确舾装中的武器、弹药库逐项对齐。"""

    instances = _instance_map(snapshot)
    magazines = {
        instance_id: instance
        for instance_id, instance in instances.items()
        if instance.prototype.category == "ammunition_magazine"
    }
    weapons = {
        instance_id: instance
        for instance_id, instance in instances.items()
        if instance.prototype.category == "weapon"
    }
    stated_magazines = {item.instance_id: item for item in state.magazines}
    stated_weapons = {item.instance_id: item for item in state.weapons}
    missing_magazines = sorted(magazines.keys() - stated_magazines.keys())
    extra_magazines = sorted(stated_magazines.keys() - magazines.keys())
    if missing_magazines or extra_magazines:
        raise ContractError(
            f"{namespace}.ammunition_magazine_state_set_mismatch",
            f"{path_prefix}.magazines",
            f"缺少 {missing_magazines}；多出 {extra_magazines}",
        )
    missing_weapons = sorted(weapons.keys() - stated_weapons.keys())
    extra_weapons = sorted(stated_weapons.keys() - weapons.keys())
    if missing_weapons or extra_weapons:
        raise ContractError(
            f"{namespace}.ammunition_weapon_state_set_mismatch",
            f"{path_prefix}.weapons",
            f"缺少 {missing_weapons}；多出 {extra_weapons}",
        )

    for instance_id, magazine_state in stated_magazines.items():
        capability = magazines[instance_id].prototype.capability.to_dict()
        compatible = set(capability["compatible_munition_ids"])
        incompatible = sorted(
            item.munition_id
            for item in magazine_state.inventory
            if item.munition_id not in compatible
        )
        if incompatible:
            raise ContractError(
                f"{namespace}.ammunition_magazine_incompatible",
                f"{path_prefix}.magazines.{instance_id}.inventory",
                f"弹药库不兼容 {incompatible}",
            )
        loaded = sum(item.units for item in magazine_state.inventory)
        capacity = int(capability["capacity_units"])
        if loaded > capacity:
            raise ContractError(
                f"{namespace}.ammunition_magazine_capacity_exceeded",
                f"{path_prefix}.magazines.{instance_id}.inventory",
                f"当前装载 {loaded} 单位，容量仅 {capacity} 单位",
            )

    for instance_id, weapon_state in stated_weapons.items():
        capability = weapons[instance_id].prototype.capability.to_dict()
        capacity = int(capability["ready_round_capacity"])
        if weapon_state.ready_rounds > capacity:
            raise ContractError(
                f"{namespace}.weapon_ready_capacity_exceeded",
                f"{path_prefix}.weapons.{instance_id}.ready_rounds",
                f"当前待发 {weapon_state.ready_rounds} 发，容量仅 {capacity} 发",
            )
        if (
            weapon_state.munition_id is not None
            and weapon_state.munition_id
            not in set(capability["compatible_munition_ids"])
        ):
            raise ContractError(
                f"{namespace}.weapon_ready_munition_incompatible",
                f"{path_prefix}.weapons.{instance_id}.munition_id",
                f"武器不兼容 {weapon_state.munition_id}",
            )


def _compile_load_state(
    snapshot: DerivedShipSnapshot,
    *,
    height_layer: str,
    fuel_units: float,
    crew_entries: tuple,
    cargo_entries: tuple,
    namespace: str,
    path_prefix: str,
    enforce_minimum_crew: bool,
    require_sufficient_lift: bool,
) -> dict[str, Any]:
    instances = _instance_map(snapshot)
    crew = {item.crew_type: item.count for item in crew_entries}
    capacity = dict(snapshot.outfit.crew_capacity)
    minimum = dict(snapshot.outfit.minimum_crew)
    standard = dict(snapshot.outfit.standard_crew)
    for crew_type, count in crew.items():
        if count > capacity.get(crew_type, 0):
            raise ContractError(
                f"{namespace}.crew_capacity_exceeded",
                f"{path_prefix}.crew.{crew_type}",
                f"配置 {count} 人，舱位容量仅 {capacity.get(crew_type, 0)}",
            )
    if enforce_minimum_crew:
        for crew_type, required in minimum.items():
            if crew.get(crew_type, 0) < required:
                raise ContractError(
                    f"{namespace}.minimum_crew_shortfall",
                    f"{path_prefix}.crew.{crew_type}",
                    f"有人出航最低需要 {required}，当前为 {crew.get(crew_type, 0)}",
                )

    fuel_capacity = sum(
        float(instance.prototype.capability.to_dict()["fuel_capacity_units"])
        for instance in instances.values()
        if instance.prototype.category == "lift_fuel_tank"
    )
    if fuel_units > fuel_capacity + EPS:
        raise ContractError(
            f"{namespace}.fuel_capacity_exceeded",
            f"{path_prefix}.fuel_units",
            f"燃料 {fuel_units} 超过容量 {fuel_capacity}",
        )

    cargo_by_storage: dict[str, float] = {}
    contributions: list[CargoMassContribution] = []
    for cargo in cargo_entries:
        storage = instances.get(cargo.storage_instance_id)
        if storage is None:
            raise ContractError(
                f"{namespace}.storage_instance_missing",
                f"{path_prefix}.bulk_cargo[{cargo.id}].storage_instance_id",
                "找不到货物绑定的模块实例",
            )
        if storage.prototype.category != "cargo_hold":
            raise ContractError(
                f"{namespace}.storage_not_cargo_hold",
                f"{path_prefix}.bulk_cargo[{cargo.id}].storage_instance_id",
                "大宗货物只能存入货仓模块",
            )
        cargo_by_storage[storage.id] = cargo_by_storage.get(storage.id, 0.0) + cargo.mass_kg
        x_m, y_m = storage.anchor_m
        contributions.append(
            CargoMassContribution(
                cargo_id=cargo.id,
                storage_instance_id=storage.id,
                mass_kg=cargo.mass_kg,
                application_point_m=storage.anchor_m,
                inertia_kg_m2=cargo.mass_kg * (x_m * x_m + y_m * y_m),
            )
        )
    for storage_id, loaded_mass in cargo_by_storage.items():
        storage = instances[storage_id]
        capacity_kg = float(
            storage.prototype.capability.to_dict()["bulk_cargo_capacity_kg"]
        )
        if loaded_mass > capacity_kg + EPS:
            raise ContractError(
                f"{namespace}.cargo_capacity_exceeded",
                f"{path_prefix}.bulk_cargo[{storage_id}]",
                f"货物质量 {loaded_mass}kg 超过货仓容量 {capacity_kg}kg",
            )

    cargo_mass = sum(item.mass_kg for item in contributions)
    cargo_inertia = sum(item.inertia_kg_m2 for item in contributions)
    current_mass = snapshot.outfit.design_mass_kg + cargo_mass
    current_inertia = snapshot.outfit.design_inertia_kg_m2 + cargo_inertia
    lift_margin = snapshot.outfit.lift_force_n - current_mass * STANDARD_GRAVITY_MPS2
    if require_sufficient_lift and lift_margin < -EPS:
        raise ContractError(
            f"{namespace}.insufficient_lift",
            path_prefix,
            f"当前质量需要 {current_mass * STANDARD_GRAVITY_MPS2:.3f}N，现有升力 {snapshot.outfit.lift_force_n:.3f}N",
        )

    warnings: list[SortieWarning] = []
    for crew_type, target in sorted(standard.items()):
        if crew.get(crew_type, 0) < target:
            warnings.append(
                SortieWarning(
                    f"{namespace}.below_standard_crew",
                    f"{path_prefix}.crew.{crew_type}",
                    f"标准人数 {target}，当前为 {crew.get(crew_type, 0)}",
                )
            )
    crew_present = sum(crew.values()) > 0
    return {
        "cargo_mass_kg": cargo_mass,
        "current_mass_kg": current_mass,
        "cargo_inertia_kg_m2": cargo_inertia,
        "current_inertia_kg_m2": current_inertia,
        "current_lift_margin_n": lift_margin,
        "fuel_capacity_units": fuel_capacity,
        "fuel_units": fuel_units,
        "crew": tuple(sorted(crew.items())),
        "crew_present": crew_present,
        "crew_safety_lock_enabled": crew_present,
        "cargo_contributions": tuple(sorted(contributions, key=lambda item: item.cargo_id)),
        "warnings": tuple(warnings),
        "height_layer": height_layer,
    }


def compile_sortie_configuration(
    snapshot: DerivedShipSnapshot,
    configuration: SortieConfigurationInput,
) -> CompiledSortieState:
    """把设计态快照与出航输入合成为当前载荷状态。"""

    _validate_outfit_reference(snapshot, configuration)
    instances = _instance_map(snapshot)
    if configuration.control_mode == "remote_core":
        remote_id = configuration.active_remote_core_instance_id
        remote = instances.get(remote_id or "")
        if remote is None or remote.prototype.category != "remote_core":
            raise ContractError(
                "sortie.remote_core_instance_invalid",
                "$.active_remote_core_instance_id",
                "启用实例必须是本舰舾装方案中的遥控核心舱",
            )
    if configuration.ammunition_loadout is not None:
        validate_ship_ammunition_state(
            snapshot,
            configuration.ammunition_loadout,
            namespace="sortie",
            path_prefix="$.ammunition_loadout",
        )
    load = _compile_load_state(
        snapshot,
        height_layer=configuration.height_layer,
        fuel_units=configuration.fuel_units,
        crew_entries=configuration.crew,
        cargo_entries=configuration.bulk_cargo,
        namespace="sortie",
        path_prefix="$",
        enforce_minimum_crew=configuration.control_mode == "crewed",
        require_sufficient_lift=True,
    )
    return CompiledSortieState(
        configuration=configuration,
        source_sha256=canonical_sha256(configuration),
        derived_snapshot_source_sha256=snapshot.source_sha256,
        design_mass_kg=snapshot.outfit.design_mass_kg,
        cargo_mass_kg=load["cargo_mass_kg"],
        current_mass_kg=load["current_mass_kg"],
        design_inertia_kg_m2=snapshot.outfit.design_inertia_kg_m2,
        cargo_inertia_kg_m2=load["cargo_inertia_kg_m2"],
        current_inertia_kg_m2=load["current_inertia_kg_m2"],
        lift_force_n=snapshot.outfit.lift_force_n,
        current_lift_margin_n=load["current_lift_margin_n"],
        fuel_capacity_units=load["fuel_capacity_units"],
        fuel_units=load["fuel_units"],
        crew=load["crew"],
        crew_present=load["crew_present"],
        crew_safety_lock_enabled=load["crew_safety_lock_enabled"],
        cargo_contributions=load["cargo_contributions"],
        warnings=load["warnings"],
    )


def compile_ship_operational_state(
    snapshot: DerivedShipSnapshot,
    state: ShipOperationalStateInput,
) -> CompiledOperationalState:
    """校验并派生战斗中可变化的当前燃料、人员、货物与高度状态。"""

    load = _compile_load_state(
        snapshot,
        height_layer=state.height_layer,
        fuel_units=state.fuel_units,
        crew_entries=state.crew,
        cargo_entries=state.bulk_cargo,
        namespace="instance",
        path_prefix="$.operational_state",
        enforce_minimum_crew=False,
        require_sufficient_lift=False,
    )
    return CompiledOperationalState(
        state=state,
        source_sha256=canonical_sha256(state),
        cargo_mass_kg=load["cargo_mass_kg"],
        current_mass_kg=load["current_mass_kg"],
        cargo_inertia_kg_m2=load["cargo_inertia_kg_m2"],
        current_inertia_kg_m2=load["current_inertia_kg_m2"],
        current_lift_margin_n=load["current_lift_margin_n"],
        fuel_capacity_units=load["fuel_capacity_units"],
        fuel_units=load["fuel_units"],
        crew=load["crew"],
        crew_present=load["crew_present"],
        crew_safety_lock_enabled=load["crew_safety_lock_enabled"],
        cargo_contributions=load["cargo_contributions"],
        warnings=load["warnings"],
    )
