"""《高天荒野》阶段 E：舰艇实例状态、供电、人员与运行时参数派生。"""

from __future__ import annotations

from dataclasses import dataclass
from math import hypot
from typing import Any

from 高天荒野舰艇出航配置编译器 import (
    CompiledSortieState,
    compile_ship_operational_state,
    validate_ship_ammunition_state,
)
from 高天荒野舰艇数据契约 import (
    ContractError,
    ResourceReference,
    RuntimeModuleStateInput,
    RuntimePowerPolicyInput,
    ShipOperationalStateInput,
    ShipInstanceSnapshotInput,
    POWER_CONSUMER_CATEGORIES,
    canonical_sha256,
)
from 高天荒野舰艇无界面舾装编译器 import (
    ActuatorAggregation,
    ActuatorInstance,
    CompiledModuleInstance,
    DerivedShipSnapshot,
    STANDARD_GRAVITY_MPS2,
    aggregate_actuators,
)
from 高天荒野舰艇实例设计状态 import (
    embed_initial_design_state,
    validate_instance_current_design,
)
from 高天荒野舰艇人员伤亡 import (
    persons_aboard_count,
    validate_crew_casualty_capacity,
)


RUNTIME_SHIP_PARAMETERS_INTERFACE_ID = "gaotian.runtime-ship-parameters/v1alpha1"
POWER_ALLOCATION_POLICY_ID = "gaotian.power-allocation/categories-and-nearest/v1"
DAMAGE_RESPONSE_POLICY_ID = "gaotian.module-damage-response/per-function-curves/v1"
CREW_ALLOCATION_POLICY_ID = (
    "gaotian.crew-allocation/critical-minimum-then-proportional/v1"
)
EPS = 1.0e-8

ACTUATOR_FUNCTION_BY_CATEGORY = {
    "main_engine": "engine.throttle",
    "maneuver_thruster": "thruster.throttle",
}
CREW_CRITICAL_CATEGORY_ORDER = {
    "cic": 0,
    "main_engine": 1,
    "maneuver_thruster": 2,
    "generator": 3,
    "damage_control": 4,
}


@dataclass(frozen=True)
class PowerConsumerResult:
    instance_id: str
    power_category: str
    distance_to_cic_m: float
    requested_load_kw: float
    supplied_load_kw: float
    powered: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "distance_to_cic_m": self.distance_to_cic_m,
            "instance_id": self.instance_id,
            "powered": self.powered,
            "power_category": self.power_category,
            "reason": self.reason,
            "requested_load_kw": self.requested_load_kw,
            "supplied_load_kw": self.supplied_load_kw,
        }


@dataclass(frozen=True)
class PowerCategoryResult:
    power_category: str
    disabled_by_command: bool
    requested_load_kw: float
    supplied_load_kw: float
    consumers: tuple[PowerConsumerResult, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "consumers": [item.to_dict() for item in self.consumers],
            "disabled_by_command": self.disabled_by_command,
            "power_category": self.power_category,
            "requested_load_kw": self.requested_load_kw,
            "supplied_load_kw": self.supplied_load_kw,
        }


@dataclass(frozen=True)
class RuntimePowerAllocation:
    policy_id: str
    allocation_mode: str
    category_order: tuple[str, ...]
    generation_kw: float
    requested_load_kw: float
    supplied_load_kw: float
    remaining_generation_kw: float
    categories: tuple[PowerCategoryResult, ...]

    @property
    def powered_instance_ids(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                consumer.instance_id
                for category in self.categories
                for consumer in category.consumers
                if consumer.powered
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "allocation_mode": self.allocation_mode,
            "generation_kw": self.generation_kw,
            "categories": [item.to_dict() for item in self.categories],
            "policy_id": self.policy_id,
            "powered_instance_ids": list(self.powered_instance_ids),
            "category_order": list(self.category_order),
            "remaining_generation_kw": self.remaining_generation_kw,
            "requested_load_kw": self.requested_load_kw,
            "supplied_load_kw": self.supplied_load_kw,
        }


@dataclass(frozen=True)
class RuntimeModuleResult:
    instance_id: str
    category: str
    current_durability_points: float
    maximum_durability_points: float
    durability_fraction: float
    condition: str
    stored_operating_mode: str
    operating_mode: str
    automatically_activated: bool
    host_available: bool
    crew_allocations: tuple[tuple[str, float], ...]
    manual_staffing_fraction: float
    powered: bool
    active_available: bool
    automated_functions: tuple[str, ...]
    damage_function_multipliers: tuple[tuple[str, float], ...]

    def function_efficiency(self, function_id: str) -> float:
        if not self.active_available:
            return 0.0
        damage_multiplier = dict(self.damage_function_multipliers).get(function_id, 0.0)
        if "*" in self.automated_functions or function_id in self.automated_functions:
            return damage_multiplier
        return damage_multiplier * self.manual_staffing_fraction

    def to_dict(self) -> dict[str, Any]:
        return {
            "active_available": self.active_available,
            "automatically_activated": self.automatically_activated,
            "automated_functions": list(self.automated_functions),
            "category": self.category,
            "condition": self.condition,
            "crew_allocations": dict(self.crew_allocations),
            "current_durability_points": self.current_durability_points,
            "damage_function_multipliers": dict(self.damage_function_multipliers),
            "durability_fraction": self.durability_fraction,
            "host_available": self.host_available,
            "instance_id": self.instance_id,
            "manual_staffing_fraction": self.manual_staffing_fraction,
            "maximum_durability_points": self.maximum_durability_points,
            "operating_mode": self.operating_mode,
            "powered": self.powered,
            "stored_operating_mode": self.stored_operating_mode,
        }


@dataclass(frozen=True)
class RuntimeShipParameters:
    instance_snapshot: ShipInstanceSnapshotInput
    instance_snapshot_sha256: str
    active_automatic_events: tuple[str, ...]
    derived_snapshot_sha256: str
    sortie_configuration_sha256: str
    height_layer: str
    current_hull_integrity_fraction: float
    current_mass_kg: float
    current_inertia_kg_m2: float
    current_lift_force_n: float
    current_lift_margin_n: float
    lift_sufficient: bool
    crew_safety_lock_enabled: bool
    crew_type_fulfillment: tuple[tuple[str, float], ...]
    modules: tuple[RuntimeModuleResult, ...]
    power: RuntimePowerAllocation
    actuators: tuple[ActuatorInstance, ...]
    actuator_aggregation: ActuatorAggregation
    cic_control_available: bool
    remote_control_available: bool
    fuel_available: bool
    terminal_failures: tuple[str, ...]
    aerodynamic_cache_sha256: str
    hull_rcs_cache_sha256: str
    hull_durability_volume_proxy_m3: float
    longitudinal_bottleneck_m: float
    lateral_bottleneck_m: float
    safe_longitudinal_mps2: float
    safe_lateral_mps2: float
    safe_yaw_acceleration_rad_s2: float
    safe_yaw_rate_rad_s: float

    def module(self, instance_id: str) -> RuntimeModuleResult:
        return next(item for item in self.modules if item.instance_id == instance_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "actuator_aggregation": self.actuator_aggregation.to_dict(),
            "actuators": [item.to_dict() for item in self.actuators],
            "active_automatic_events": list(self.active_automatic_events),
            "control": {
                "cic_control_available": self.cic_control_available,
                "remote_control_available": self.remote_control_available,
            },
            "crew": {
                "allocation_policy": CREW_ALLOCATION_POLICY_ID,
                "safety_lock_enabled": self.crew_safety_lock_enabled,
                "type_fulfillment": dict(self.crew_type_fulfillment),
            },
            "current": {
                "height_layer": self.height_layer,
                "hull_integrity_fraction": self.current_hull_integrity_fraction,
                "inertia_kg_m2": self.current_inertia_kg_m2,
                "mass_kg": self.current_mass_kg,
            },
            "damage_response_policy": DAMAGE_RESPONSE_POLICY_ID,
            "deferred_capabilities": [
                "safe_power_mode_upgrade_entitlement",
            ],
            "environment_sources": {
                "aerodynamic_cache_sha256": self.aerodynamic_cache_sha256,
                "hull_rcs_cache_sha256": self.hull_rcs_cache_sha256,
            },
            "fuel_available": self.fuel_available,
            "interface": RUNTIME_SHIP_PARAMETERS_INTERFACE_ID,
            "lift": {
                "current_force_n": self.current_lift_force_n,
                "current_margin_n": self.current_lift_margin_n,
                "sufficient": self.lift_sufficient,
            },
            "modules": [item.to_dict() for item in self.modules],
            "power": self.power.to_dict(),
            "sources": {
                "derived_snapshot_sha256": self.derived_snapshot_sha256,
                "instance_snapshot": {
                    "id": self.instance_snapshot.id,
                    "source_sha256": self.instance_snapshot_sha256,
                    "version": self.instance_snapshot.version,
                },
                "sortie_configuration_sha256": self.sortie_configuration_sha256,
            },
            "structure": {
                "hull_durability_volume_proxy_m3": self.hull_durability_volume_proxy_m3,
                "lateral_bottleneck_m": self.lateral_bottleneck_m,
                "longitudinal_bottleneck_m": self.longitudinal_bottleneck_m,
                "safe_lateral_mps2": self.safe_lateral_mps2,
                "safe_longitudinal_mps2": self.safe_longitudinal_mps2,
                "safe_yaw_acceleration_rad_s2": self.safe_yaw_acceleration_rad_s2,
                "safe_yaw_rate_rad_s": self.safe_yaw_rate_rad_s,
            },
            "terminal_failures": list(self.terminal_failures),
        }


def initialize_ship_instance_snapshot(
    snapshot: DerivedShipSnapshot,
    sortie: CompiledSortieState,
    *,
    power_policy: RuntimePowerPolicyInput | None = None,
    embed_design_state: bool = False,
) -> ShipInstanceSnapshotInput:
    """从设计态与出航配置生成完好、全启用的实例初态。

    旧夹具默认不嵌入设计状态以保持已有规范 JSON 稳定；真正的新造舰应传入
    embed_design_state=True，使蓝图删除后仍可从舰艇实例重建设计。
    """

    if power_policy is None:
        power_policy = RuntimePowerPolicyInput(
            "strict_categories", POWER_CONSUMER_CATEGORIES, ()
        )
    configuration = sortie.configuration
    instance = ShipInstanceSnapshotInput(
        id=f"{configuration.id}.instance",
        version=configuration.version,
        name=f"{configuration.name}·完好实例",
        fixture_level=configuration.fixture_level,
        outfit_plan=configuration.outfit_plan,
        derived_ship_snapshot_sha256=snapshot.source_sha256,
        current_hull_integrity_fraction=1.0,
        sortie_configuration=ResourceReference(configuration.id, configuration.version),
        sortie_configuration_sha256=sortie.source_sha256,
        module_states=tuple(
            RuntimeModuleStateInput(
                module.id,
                module.prototype.durability_points,
                (
                    "active"
                    if configuration.control_mode == "remote_core"
                    and configuration.active_remote_core_instance_id == module.id
                    else module.prototype.default_operating_mode
                ),
            )
            for module in snapshot.outfit.instances
        ),
        operational_state=ShipOperationalStateInput.from_sortie(configuration),
        power_policy=power_policy,
        ammunition_state=configuration.ammunition_loadout,
    )
    return (
        embed_initial_design_state(snapshot, instance)
        if embed_design_state
        else instance
    )


def _validate_sources(
    snapshot: DerivedShipSnapshot,
    sortie: CompiledSortieState,
    instance: ShipInstanceSnapshotInput,
) -> None:
    validate_instance_current_design(snapshot, instance)
    configuration = sortie.configuration
    expected_sortie = ResourceReference(configuration.id, configuration.version)
    if instance.sortie_configuration != expected_sortie:
        raise ContractError(
            "runtime.sortie_reference_mismatch",
            "$.sortie_configuration",
            f"实例绑定 {instance.sortie_configuration}，当前出航配置为 {expected_sortie}",
        )
    if instance.sortie_configuration_sha256 != sortie.source_sha256:
        raise ContractError(
            "runtime.sortie_hash_mismatch",
            "$.sortie_configuration_sha256",
            "实例引用的出航配置内容已经变化",
        )
    if configuration.outfit_plan != instance.outfit_plan:
        raise ContractError(
            "runtime.construction_outfit_history_mismatch",
            "$.outfit_plan",
            "实例建成/首次出航舾装来源与冻结的出航配置不一致",
        )


def _validate_and_index_states(
    snapshot: DerivedShipSnapshot,
    instance: ShipInstanceSnapshotInput,
) -> tuple[dict[str, CompiledModuleInstance], dict[str, RuntimeModuleStateInput]]:
    modules = {item.id: item for item in snapshot.outfit.instances}
    states = {item.instance_id: item for item in instance.module_states}
    missing = sorted(modules.keys() - states.keys())
    extra = sorted(states.keys() - modules.keys())
    if missing or extra:
        raise ContractError(
            "runtime.module_state_set_mismatch",
            "$.module_states",
            f"缺少 {missing}；多出 {extra}",
        )
    for instance_id, state in states.items():
        maximum = modules[instance_id].prototype.durability_points
        if state.current_durability_points > maximum + EPS:
            raise ContractError(
                "runtime.module_durability_exceeded",
                f"$.module_states.{instance_id}.current_durability_points",
                f"当前耐久 {state.current_durability_points} 超过原型上限 {maximum}",
            )
    return modules, states


def _host_availability(
    module: CompiledModuleInstance,
    modules: dict[str, CompiledModuleInstance],
    states: dict[str, RuntimeModuleStateInput],
    memo: dict[str, bool],
) -> bool:
    if module.id in memo:
        return memo[module.id]
    state = states[module.id]
    available = state.current_durability_points > EPS and state.operating_mode != "off"
    if available and module.host_instance_id is not None:
        available = _host_availability(
            modules[module.host_instance_id], modules, states, memo
        )
    memo[module.id] = available
    return available


def _manual_staffing(
    modules: dict[str, CompiledModuleInstance],
    states: dict[str, RuntimeModuleStateInput],
    host_available: dict[str, bool],
    crew_counts: dict[str, int],
    power_policy: RuntimePowerPolicyInput,
) -> tuple[
    dict[str, float],
    dict[str, float],
    dict[str, tuple[tuple[str, float], ...]],
]:
    demand: dict[str, int] = {}
    requirements_by_type: dict[
        str, list[tuple[CompiledModuleInstance, int, int]]
    ] = {}
    for instance_id, module in modules.items():
        if not host_available[instance_id] or states[instance_id].operating_mode != "active":
            continue
        for requirement in module.prototype.crew:
            demand[requirement.crew_type] = demand.get(requirement.crew_type, 0) + requirement.standard
            requirements_by_type.setdefault(requirement.crew_type, []).append(
                (
                    module,
                    requirement.minimum_operating,
                    requirement.standard,
                )
            )
    fulfillment = {
        crew_type: min(1.0, crew_counts.get(crew_type, 0) / required)
        for crew_type, required in demand.items()
        if required > 0
    }
    category_rank = {
        category: index for index, category in enumerate(power_policy.category_order)
    }
    allocations: dict[str, dict[str, float]] = {
        instance_id: {} for instance_id in modules
    }
    for crew_type, entries in requirements_by_type.items():
        remaining = float(crew_counts.get(crew_type, 0))

        def entry_rank(
            entry: tuple[CompiledModuleInstance, int, int]
        ) -> tuple[int, int, float, str]:
            module, _, _ = entry
            if module.prototype.counts_toward_departure_minimum:
                return (
                    0,
                    CREW_CRITICAL_CATEGORY_ORDER.get(module.prototype.category, 99),
                    hypot(*module.anchor_m),
                    module.id,
                )
            consumer_category = module.prototype.power.consumer_category
            return (
                1,
                category_rank.get(consumer_category or "", 99),
                hypot(*module.anchor_m),
                module.id,
            )

        ordered = sorted(entries, key=entry_rank)
        for phase in (0, 1):
            for module, minimum, _ in ordered:
                is_critical = module.prototype.counts_toward_departure_minimum
                if (phase == 0) != is_critical or minimum <= 0 or remaining <= EPS:
                    continue
                assigned = min(float(minimum), remaining)
                allocations[module.id][crew_type] = assigned
                remaining -= assigned

        deficits = [
            (
                module,
                max(
                    0.0,
                    float(standard)
                    - allocations[module.id].get(crew_type, 0.0),
                ),
            )
            for module, _, standard in ordered
        ]
        total_deficit = sum(deficit for _, deficit in deficits)
        if remaining > EPS and total_deficit > EPS:
            distributed = min(remaining, total_deficit)
            for module, deficit in deficits:
                if deficit <= EPS:
                    continue
                allocations[module.id][crew_type] = (
                    allocations[module.id].get(crew_type, 0.0)
                    + distributed * deficit / total_deficit
                )

    staffing: dict[str, float] = {}
    for instance_id, module in modules.items():
        requirements = [
            min(
                1.0,
                allocations[instance_id].get(requirement.crew_type, 0.0)
                / requirement.standard,
            )
            for requirement in module.prototype.crew
            if requirement.standard > 0
        ]
        staffing[instance_id] = min(requirements) if requirements else 1.0
    normalized_allocations = {
        instance_id: tuple(sorted(values.items()))
        for instance_id, values in allocations.items()
    }
    return staffing, fulfillment, normalized_allocations


def _function_efficiency_before_power(
    module: CompiledModuleInstance,
    state: RuntimeModuleStateInput,
    host_available: bool,
    manual_staffing_fraction: float,
    function_id: str,
) -> float:
    if not host_available or state.operating_mode != "active":
        return 0.0
    durability_fraction = (
        state.current_durability_points / module.prototype.durability_points
    )
    damage_multiplier = module.prototype.damage_output_fraction(
        function_id, durability_fraction
    )
    automation = module.prototype.automation
    if automation.level == "full" or function_id in automation.automated_functions:
        return damage_multiplier
    return damage_multiplier * manual_staffing_fraction


def _allocate_power(
    modules: dict[str, CompiledModuleInstance],
    states: dict[str, RuntimeModuleStateInput],
    host_available: dict[str, bool],
    staffing: dict[str, float],
    policy: RuntimePowerPolicyInput,
) -> RuntimePowerAllocation:
    generation = 0.0
    for instance_id, module in modules.items():
        if module.prototype.category != "generator":
            continue
        efficiency = _function_efficiency_before_power(
            module,
            states[instance_id],
            host_available[instance_id],
            staffing[instance_id],
            "generator.regulation",
        )
        generation += module.prototype.power.generation_kw * efficiency

    consumers_by_category: dict[str, list[tuple[CompiledModuleInstance, float]]] = {
        category: [] for category in POWER_CONSUMER_CATEGORIES
    }
    for instance_id, module in modules.items():
        state = states[instance_id]
        profile = module.prototype.power
        category = profile.consumer_category
        if category is None or not host_available[instance_id]:
            continue
        if state.operating_mode == "active":
            load = profile.active_load_kw
        elif state.operating_mode == "standby":
            load = profile.standby_load_kw
        else:
            load = 0.0
        if load > EPS:
            consumers_by_category[category].append((module, load))

    remaining = generation
    category_results: list[PowerCategoryResult] = []
    for category in policy.category_order:
        entries = sorted(
            consumers_by_category[category],
            key=lambda item: (hypot(*item[0].anchor_m), item[0].id),
        )
        requested = sum(load for _, load in entries)
        consumer_results: list[PowerConsumerResult] = []
        if category in policy.disabled_categories:
            for module, load in entries:
                consumer_results.append(
                    PowerConsumerResult(
                        module.id,
                        category,
                        hypot(*module.anchor_m),
                        load,
                        0.0,
                        False,
                        "disabled_by_group_command",
                    )
                )
        elif policy.allocation_mode == "strict_categories":
            category_powered = requested <= remaining + EPS
            if category_powered:
                remaining -= requested
            for module, load in entries:
                consumer_results.append(
                    PowerConsumerResult(
                        module.id,
                        category,
                        hypot(*module.anchor_m),
                        load,
                        load if category_powered else 0.0,
                        category_powered,
                        "powered" if category_powered else "whole_category_tripped",
                    )
                )
        else:
            for module, load in entries:
                powered = load <= remaining + EPS
                if powered:
                    remaining -= load
                consumer_results.append(
                    PowerConsumerResult(
                        module.id,
                        category,
                        hypot(*module.anchor_m),
                        load,
                        load if powered else 0.0,
                        powered,
                        "powered" if powered else "individual_module_tripped",
                    )
                )
        supplied = sum(item.supplied_load_kw for item in consumer_results)
        category_results.append(
            PowerCategoryResult(
                category,
                category in policy.disabled_categories,
                requested,
                supplied,
                tuple(consumer_results),
            )
        )
    return RuntimePowerAllocation(
        POWER_ALLOCATION_POLICY_ID,
        policy.allocation_mode,
        policy.category_order,
        generation,
        sum(category.requested_load_kw for category in category_results),
        sum(category.supplied_load_kw for category in category_results),
        max(0.0, remaining),
        tuple(category_results),
    )


def _scaled_actuator(actuator: ActuatorInstance, efficiency: float) -> ActuatorInstance:
    return ActuatorInstance(
        actuator.instance_id,
        actuator.category,
        actuator.thrust_n * efficiency,
        actuator.application_point_m,
        actuator.direction_body,
        actuator.torque_about_cic_n_m * efficiency,
        actuator.fuel_units_per_s * efficiency,
        actuator.response_time_s,
    )


def compile_runtime_ship_parameters(
    snapshot: DerivedShipSnapshot,
    sortie: CompiledSortieState,
    instance: ShipInstanceSnapshotInput,
    *,
    active_automatic_events: tuple[str, ...] = (),
) -> RuntimeShipParameters:
    """从设计、出航配置和当前实例状态生成唯一战术运行参数。"""

    _validate_sources(snapshot, sortie, instance)
    validate_crew_casualty_capacity(
        instance,
        dict(snapshot.outfit.crew_capacity),
    )
    if instance.ammunition_state is not None:
        validate_ship_ammunition_state(
            snapshot,
            instance.ammunition_state,
            namespace="instance",
            path_prefix="$.ammunition_state",
        )
    operational = compile_ship_operational_state(snapshot, instance.operational_state)
    modules, stored_states = _validate_and_index_states(snapshot, instance)
    event_set = set(active_automatic_events)
    normalized_events = tuple(sorted(event_set))
    automatically_activated: set[str] = set()
    states: dict[str, RuntimeModuleStateInput] = {}
    for instance_id, module in modules.items():
        stored = stored_states[instance_id]
        activate = (
            stored.operating_mode == "standby"
            and bool(event_set.intersection(module.prototype.automatic_activation_events))
        )
        if activate:
            automatically_activated.add(instance_id)
            states[instance_id] = RuntimeModuleStateInput(
                stored.instance_id,
                stored.current_durability_points,
                "active",
            )
        else:
            states[instance_id] = stored
    host_memo: dict[str, bool] = {}
    host_available = {
        instance_id: _host_availability(module, modules, states, host_memo)
        for instance_id, module in modules.items()
    }
    crew_counts = dict(operational.crew)
    staffing, fulfillment, crew_allocations = _manual_staffing(
        modules,
        states,
        host_available,
        crew_counts,
        instance.power_policy,
    )
    power = _allocate_power(
        modules,
        states,
        host_available,
        staffing,
        instance.power_policy,
    )
    powered_ids = set(power.powered_instance_ids)

    runtime_modules: list[RuntimeModuleResult] = []
    for instance_id, module in sorted(modules.items()):
        state = states[instance_id]
        maximum = module.prototype.durability_points
        fraction = state.current_durability_points / maximum
        condition = (
            "destroyed"
            if state.current_durability_points <= EPS
            else "damaged"
            if state.current_durability_points < maximum - EPS
            else "intact"
        )
        has_load = module.prototype.power.consumer_category is not None and (
            module.prototype.power.active_load_kw
            if state.operating_mode == "active"
            else module.prototype.power.standby_load_kw
            if state.operating_mode == "standby"
            else 0.0
        ) > EPS
        powered = not has_load or instance_id in powered_ids
        active_available = (
            host_available[instance_id]
            and state.operating_mode == "active"
            and powered
        )
        automated = module.prototype.automation.automated_functions
        if module.prototype.automation.level == "full":
            automated = tuple(sorted(set(automated) | {"*"}))
        damage_multipliers = tuple(
            (
                response.function_id,
                response.output_fraction(fraction),
            )
            for response in module.prototype.damage_responses
        )
        runtime_modules.append(
            RuntimeModuleResult(
                instance_id,
                module.prototype.category,
                state.current_durability_points,
                maximum,
                fraction,
                condition,
                stored_states[instance_id].operating_mode,
                state.operating_mode,
                instance_id in automatically_activated,
                host_available[instance_id],
                crew_allocations[instance_id],
                staffing[instance_id],
                powered,
                active_available,
                automated,
                damage_multipliers,
            )
        )
    runtime_by_id = {item.instance_id: item for item in runtime_modules}

    fuel_available = operational.fuel_units > EPS
    actuators: list[ActuatorInstance] = []
    if fuel_available:
        for actuator in snapshot.outfit.actuators:
            function_id = ACTUATOR_FUNCTION_BY_CATEGORY[actuator.category]
            module_result = runtime_by_id[actuator.instance_id]
            efficiency = module_result.function_efficiency(function_id)
            if efficiency > EPS:
                actuators.append(_scaled_actuator(actuator, efficiency))
    actuator_tuple = tuple(sorted(actuators, key=lambda item: item.instance_id))
    actuator_aggregation = aggregate_actuators(actuator_tuple)

    lift_force = 0.0
    for instance_id, module in modules.items():
        if module.prototype.category != "lift_fuel_tank":
            continue
        lift_force += float(
            module.prototype.capability.to_dict()["lift_force_n"]
        ) * runtime_by_id[instance_id].function_efficiency("lift_tank.lift")
    lift_margin = lift_force - operational.current_mass_kg * STANDARD_GRAVITY_MPS2
    lift_sufficient = lift_margin >= -EPS

    cic = next(
        (item for item in runtime_modules if item.category == "cic"), None
    )
    cic_control = False
    if cic is not None and cic.active_available:
        cic_control = cic.function_efficiency("cic.basic_control") > EPS
    remote_control = False
    if sortie.configuration.control_mode == "remote_core":
        remote_id = sortie.configuration.active_remote_core_instance_id or ""
        remote = runtime_by_id.get(remote_id)
        remote_control = bool(
            remote is not None
            and remote.function_efficiency("remote_core.command_link") > EPS
            and cic_control
        )

    failures: list[str] = []
    if not cic_control:
        failures.append("cic_control_lost")
    if sortie.configuration.control_mode == "remote_core" and not remote_control:
        failures.append("remote_control_lost")
    if instance.current_hull_integrity_fraction <= EPS:
        failures.append("hull_structure_collapsed")
    if not lift_sufficient:
        failures.append("insufficient_lift")

    return RuntimeShipParameters(
        instance,
        canonical_sha256(instance),
        normalized_events,
        snapshot.source_sha256,
        sortie.source_sha256,
        operational.state.height_layer,
        instance.current_hull_integrity_fraction,
        operational.current_mass_kg,
        operational.current_inertia_kg_m2,
        lift_force,
        lift_margin,
        lift_sufficient,
        persons_aboard_count(instance) > 0,
        tuple(sorted(fulfillment.items())),
        tuple(runtime_modules),
        power,
        actuator_tuple,
        actuator_aggregation,
        cic_control,
        remote_control,
        fuel_available,
        tuple(failures),
        canonical_sha256(snapshot.hull.aerodynamic_cache),
        canonical_sha256(snapshot.hull.hull_rcs_cache),
        snapshot.hull.hull_durability_volume_proxy_m3,
        snapshot.hull.longitudinal_bottleneck_m,
        snapshot.hull.lateral_bottleneck_m,
        snapshot.hull.safe_longitudinal_mps2,
        snapshot.hull.safe_lateral_mps2,
        snapshot.hull.safe_yaw_acceleration_rad_s2,
        snapshot.hull.safe_yaw_rate_rad_s,
    )
