"""阶段 I2：真实常规有人舰上的船坞库存、资源预留与战略工时回归。"""

from __future__ import annotations

from dataclasses import replace
import json
from math import ceil, isclose
from pathlib import Path
from typing import Callable

from 高天荒野舰艇出航配置编译器 import compile_sortie_configuration
from 高天荒野舰艇数据契约 import (
    ContractError,
    OutfitPlanInput,
    ShipInstanceSnapshotInput,
    canonical_json,
    load_hull_coating_catalog,
    load_material_registry,
)
from 高天荒野舰艇无界面舾装编译器 import (
    build_derived_ship_snapshot,
    compile_outfit,
)
from 高天荒野舰艇阶段F三舰集成测试 import build_chain
from 高天荒野舰艇阶段I弹药与武器动作测试 import ammunition_state, fire_request
from 高天荒野舰艇弹药与武器动作结算器 import resolve_weapon_fire
from 高天荒野舰艇运行时参数编译器 import initialize_ship_instance_snapshot
from 高天荒野舰艇船坞后勤与战略工时 import (
    SHIPYARD_LOGISTICS_INTERFACE_ID,
    LogisticsStockBundle,
    MaterialStock,
    ModuleStock,
    MunitionStock,
    ShipyardFacilityProfile,
    ShipyardInventoryState,
    ShipyardWorkOrder,
    advance_work_order,
    complete_construction_artifact,
    complete_rearm_artifact,
    complete_refit_artifact,
    complete_repair_artifact,
    complete_resupply_artifact,
    confirm_work_order,
    quote_ship_construction,
    quote_ship_rearm,
    quote_ship_refit,
    quote_ship_repair,
    quote_ship_resupply,
    settle_completed_work_order,
)


ROOT = Path(__file__).resolve().parent
STRUCTURE_CATALOG = ROOT / "舰艇数据" / "材料" / "结构材质.v1.json"
ARMOR_CATALOG = ROOT / "舰艇数据" / "材料" / "基础装甲材质.v1.json"
COATING_CATALOG = ROOT / "舰艇数据" / "涂料" / "船体涂料.v1.json"
REPORT_PATH = ROOT / "舰艇数据" / "报告" / "阶段I船坞后勤与战略工时接口.v1.json"
SCHEMA_PATH = ROOT / "舰艇数据" / "模式" / "高天荒野舰艇后勤数据契约.v1alpha1.schema.json"
STANDARD = "gtw.munition.fixture.76mm.standard"


def require_contract_error(code: str, action: Callable[[], object]) -> None:
    try:
        action()
    except ContractError as error:
        if error.code != code:
            raise AssertionError(f"预期 {code}，实际为 {error.code}: {error}") from error
    else:
        raise AssertionError(f"预期抛出 {code}")


def facility(*capabilities: str) -> ShipyardFacilityProfile:
    if not capabilities:
        capabilities = (
            "ship_construction",
            "ship_refit",
            "ship_repair",
            "ship_rearm",
            "ship_resupply",
        )
    return ShipyardFacilityProfile.parse(
        {
            "capabilities": list(capabilities),
            "fixture_level": "contract_fixture",
            "id": "gtw.facility.fixture.stage_i.shipyard",
            "kind": "ShipyardFacilityProfile",
            "name": "阶段I船坞工时夹具",
            "schema": "gaotian.ship-logistics/v1alpha1",
            "version": 1,
            "work_units_per_strategic_second": 10000.0,
        }
    )


def inventory_from_bundle(
    bundle: LogisticsStockBundle,
    *,
    inventory_id: str,
) -> ShipyardInventoryState:
    return ShipyardInventoryState(
        inventory_id,
        1,
        "阶段I船坞库存夹具",
        "contract_fixture",
        bundle,
    )


def complete_order(order, shipyard, *, start_time: float = 100.0):
    duration = order.remaining_work_units / shipyard.work_units_per_strategic_second
    unchanged = advance_work_order(
        order, shipyard, current_world_time_s=start_time
    )
    assert unchanged.status == "queued"
    partial = advance_work_order(
        unchanged,
        shipyard,
        current_world_time_s=start_time + duration * 0.5,
    )
    assert partial.status == "in_progress"
    completed = advance_work_order(
        partial,
        shipyard,
        current_world_time_s=start_time + duration + 1.0,
    )
    assert completed.status == "completed_unsettled"
    assert completed.remaining_work_units == 0.0
    return completed


def main() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert schema["$id"] == "gaotian.ship-logistics/v1alpha1"
    assert set(schema["$defs"]["workKind"]["enum"]) == {
        "ship_construction",
        "ship_damaged_module_dismantle",
        "ship_destroyed_residue_clearance",
        "ship_refit",
        "ship_repair",
        "ship_rearm",
        "ship_resupply",
    }
    chain = build_chain("conventional_crewed")
    registry = load_material_registry((STRUCTURE_CATALOG, ARMOR_CATALOG))
    shipyard = facility()

    construction = quote_ship_construction(
        "quote.fixture.construction", chain.snapshot, registry
    )
    assert construction.target_design_sha256 == chain.snapshot.source_sha256
    assert sum(item.units for item in construction.requirements.materials) >= ceil(
        chain.hull.hull_mass_kg
    )
    assert sum(item.units for item in construction.requirements.modules) == len(
        chain.outfit.instances
    )
    construction_inventory = inventory_from_bundle(
        construction.requirements,
        inventory_id="gtw.inventory.fixture.construction",
    )
    confirmed_construction = confirm_work_order(
        construction,
        construction_inventory,
        shipyard,
        order_id="order.fixture.construction",
        order_name="阶段I建造订单",
        current_world_time_s=100.0,
    )
    assert confirmed_construction.order.status == "queued"
    assert confirmed_construction.inventory.stock.materials == ()
    assert confirmed_construction.inventory.stock.modules == ()
    order_text = canonical_json(confirmed_construction.order)
    restored_order = ShipyardWorkOrder.parse(json.loads(order_text))
    assert canonical_json(restored_order) == order_text
    inventory_text = canonical_json(confirmed_construction.inventory)
    assert canonical_json(
        ShipyardInventoryState.parse(json.loads(inventory_text))
    ) == inventory_text
    completed_construction = complete_order(
        restored_order, shipyard, start_time=100.0
    )
    new_ship = complete_construction_artifact(
        completed_construction, chain.snapshot, chain.sortie
    )
    assert new_ship.design_state is not None
    assert canonical_json(replace(new_ship, design_state=None)) == canonical_json(
        chain.instance
    )
    settled_construction = settle_completed_work_order(
        completed_construction, confirmed_construction.inventory
    )
    assert settled_construction.order.status == "settled"

    damaged = replace(
        chain.instance,
        current_hull_integrity_fraction=0.8,
        module_states=tuple(
            replace(item, current_durability_points=50.0)
            if item.instance_id == "weapon_upper_port"
            else replace(item, current_durability_points=0.0)
            if item.instance_id == "fire_control"
            else item
            for item in chain.instance.module_states
        ),
    )
    repair = quote_ship_repair("quote.fixture.repair", chain.snapshot, damaged)
    assert repair.requirements.maintenance_material_units > 0
    assert "repairable_module:weapon_upper_port" in repair.diagnostics
    assert (
        "destroyed_residue_requires_clearance:fire_control" in repair.diagnostics
    )
    require_contract_error(
        "logistics.inventory_insufficient",
        lambda: confirm_work_order(
            repair,
            inventory_from_bundle(
                LogisticsStockBundle(), inventory_id="gtw.inventory.fixture.empty"
            ),
            shipyard,
            order_id="order.fixture.repair_insufficient",
            order_name="资源不足维修订单",
            current_world_time_s=200.0,
        ),
    )
    repair_inventory = inventory_from_bundle(
        repair.requirements, inventory_id="gtw.inventory.fixture.repair"
    )
    repair_confirmation = confirm_work_order(
        repair,
        repair_inventory,
        shipyard,
        order_id="order.fixture.repair",
        order_name="阶段I维修订单",
        current_world_time_s=200.0,
    )
    completed_repair = complete_order(
        repair_confirmation.order, shipyard, start_time=200.0
    )
    repaired = complete_repair_artifact(
        completed_repair, chain.snapshot, damaged
    )
    assert repaired.current_hull_integrity_fraction == 1.0
    repaired_states = {item.instance_id: item for item in repaired.module_states}
    assert repaired_states["weapon_upper_port"].current_durability_points == 100.0
    assert repaired_states["fire_control"].current_durability_points == 0.0

    live_configuration = replace(
        chain.sortie.configuration,
        id="gtw.sortie.fixture.stage_i.logistics_live_ammunition",
        name="阶段I后勤实弹夹具",
        ammunition_loadout=ammunition_state(),
    )
    live_sortie = compile_sortie_configuration(chain.snapshot, live_configuration)
    live_instance = initialize_ship_instance_snapshot(chain.snapshot, live_sortie)
    depleted = resolve_weapon_fire(
        chain.snapshot, live_sortie, live_instance, fire_request()
    ).resulting_instance
    rearm = quote_ship_rearm(
        "quote.fixture.rearm", chain.snapshot, depleted, ammunition_state()
    )
    assert rearm.requirements.munitions == (MunitionStock(STANDARD, 1),)
    rearm_inventory = inventory_from_bundle(
        rearm.requirements, inventory_id="gtw.inventory.fixture.rearm"
    )
    rearm_confirmation = confirm_work_order(
        rearm,
        rearm_inventory,
        shipyard,
        order_id="order.fixture.rearm",
        order_name="阶段I重新武装订单",
        current_world_time_s=300.0,
    )
    completed_rearm = complete_order(
        rearm_confirmation.order, shipyard, start_time=300.0
    )
    rearmed = complete_rearm_artifact(
        completed_rearm, chain.snapshot, depleted, ammunition_state()
    )
    assert rearmed.ammunition_state == ammunition_state()
    changed_during_order = replace(
        depleted,
        operational_state=replace(depleted.operational_state, fuel_units=799.0),
    )
    require_contract_error(
        "logistics.artifact_instance_mismatch",
        lambda: complete_rearm_artifact(
            completed_rearm,
            chain.snapshot,
            changed_during_order,
            ammunition_state(),
        ),
    )

    low_fuel = replace(
        chain.instance,
        operational_state=replace(chain.instance.operational_state, fuel_units=500.0),
    )
    resupply = quote_ship_resupply(
        "quote.fixture.resupply",
        chain.snapshot,
        low_fuel,
        desired_fuel_units=800.0,
    )
    assert isclose(resupply.requirements.fuel_units, 300.0)
    fuel_inventory = inventory_from_bundle(
        resupply.requirements, inventory_id="gtw.inventory.fixture.fuel"
    )
    fuel_confirmation = confirm_work_order(
        resupply,
        fuel_inventory,
        shipyard,
        order_id="order.fixture.resupply",
        order_name="阶段I补给订单",
        current_world_time_s=400.0,
    )
    completed_resupply = complete_order(
        fuel_confirmation.order, shipyard, start_time=400.0
    )
    resupplied = complete_resupply_artifact(
        completed_resupply, chain.snapshot, low_fuel
    )
    assert isclose(resupplied.operational_state.fuel_units, 800.0)

    target_source = chain.outfit.normalized_plan.to_dict()
    target_source["id"] = "gtw.outfit.fixture.stage_i.refit_target"
    target_source["name"] = "阶段I改装目标夹具"
    target_source["version"] = 1
    converted = next(
        item
        for item in target_source["modules"]
        if item["id"] == "sensor_upper_starboard"
    )
    converted["prototype"] = {
        "id": "gtw.module.fixture.weapon",
        "version": 1,
    }
    target_plan = OutfitPlanInput.parse(target_source)
    target_outfit = compile_outfit(
        target_plan,
        chain.hull,
        chain.module_catalog,
        load_hull_coating_catalog(COATING_CATALOG),
    )
    target_snapshot = build_derived_ship_snapshot(chain.hull, target_outfit)
    refit = quote_ship_refit(
        "quote.fixture.refit", chain.snapshot, chain.instance, target_snapshot
    )
    converted_module = next(
        item for item in target_outfit.instances
        if item.id == "sensor_upper_starboard"
    )
    assert refit.requirements.modules == (
        ModuleStock(converted_module.prototype.reference, 1),
    )
    assert sum(item.units for item in refit.expected_outputs.modules) == 1
    refit_inventory = inventory_from_bundle(
        refit.requirements, inventory_id="gtw.inventory.fixture.refit"
    )
    refit_confirmation = confirm_work_order(
        refit,
        refit_inventory,
        shipyard,
        order_id="order.fixture.refit",
        order_name="阶段I改装订单",
        current_world_time_s=500.0,
    )
    completed_refit = complete_order(
        refit_confirmation.order, shipyard, start_time=500.0
    )
    require_contract_error(
        "logistics.order_not_ready_to_settle",
        lambda: settle_completed_work_order(
            completed_refit,
            refit_confirmation.inventory,
        ),
    )
    applied_refit = complete_refit_artifact(
        completed_refit,
        chain.snapshot,
        chain.instance,
        target_snapshot,
    )
    assert applied_refit.instance.design_state is not None
    assert (
        applied_refit.instance.design_state.current_derived_ship_snapshot_sha256
        == target_snapshot.source_sha256
    )
    settled_refit = settle_completed_work_order(
        applied_refit.order,
        refit_confirmation.inventory,
    )
    assert sum(item.units for item in settled_refit.inventory.stock.modules) == 1
    destroyed_sensor = replace(
        chain.instance,
        module_states=tuple(
            replace(item, current_durability_points=0.0)
            if item.instance_id == "sensor_upper_starboard"
            else item
            for item in chain.instance.module_states
        ),
    )
    require_contract_error(
        "logistics.refit_destroyed_residue_requires_clearance",
        lambda: quote_ship_refit(
            "quote.fixture.refit_destroyed_sensor",
            chain.snapshot,
            destroyed_sensor,
            target_snapshot,
        ),
    )

    require_contract_error(
        "logistics.facility_capability_missing",
        lambda: confirm_work_order(
            construction,
            construction_inventory,
            facility("ship_repair"),
            order_id="order.fixture.unsupported",
            order_name="设施能力不足订单",
            current_world_time_s=600.0,
        ),
    )
    require_contract_error(
        "logistics.world_time_reversed",
        lambda: advance_work_order(
            repair_confirmation.order,
            shipyard,
            current_world_time_s=199.0,
        ),
    )

    report = {
        "fixture_notice": (
            "材料单位、维护材料等效量、设施吞吐与作业时长只验证合同关系，"
            "不是正式价格、产能或教程舰工时。"
        ),
        "interface": SHIPYARD_LOGISTICS_INTERFACE_ID,
        "orders": {
            "construction": {
                "material_sku_count": len(construction.requirements.materials),
                "module_units": sum(
                    item.units for item in construction.requirements.modules
                ),
                "new_instance_matches_legacy_initialization_plus_design_state": True,
                "work_units": construction.work_units,
            },
            "rearm": {
                "munition_units": sum(
                    item.units for item in rearm.requirements.munitions
                ),
                "state_applied": rearmed.ammunition_state == ammunition_state(),
            },
            "refit": {
                "added_module_units": sum(
                    item.units for item in refit.requirements.modules
                ),
                "returned_module_units": sum(
                    item.units for item in refit.expected_outputs.modules
                ),
                "current_design_transition_applied": True,
            },
            "repair": {
                "destroyed_residue_preserved": (
                    repaired_states["fire_control"].current_durability_points == 0.0
                ),
                "maintenance_material_units": (
                    repair.requirements.maintenance_material_units
                ),
                "repairable_damage_restored": True,
            },
            "resupply": {
                "fuel_units": resupply.requirements.fuel_units,
                "state_applied": isclose(
                    resupplied.operational_state.fuel_units, 800.0
                ),
            },
        },
        "policies": {
            "confirmation_reserves_inventory": True,
            "refit_outputs_wait_for_current_design_transition": True,
            "special_event_same_world_time_no_progress": True,
            "strategic_time_only_progress": True,
        },
        "status": "PASS",
        "tested_failures": [
            "inventory_insufficient",
            "facility_capability_missing",
            "world_time_reversed",
            "source_instance_changed_during_order",
            "refit_settlement_before_design_transition",
            "refit_destroyed_residue_requires_clearance",
        ],
    }
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
