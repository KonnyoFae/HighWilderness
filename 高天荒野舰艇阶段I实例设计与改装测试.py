"""阶段 I3：建造船壳快照、当前舾装迁移、残骸清理与受损拆卸回归。"""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from typing import Callable

from 高天荒野舰艇出航配置编译器 import compile_sortie_configuration
from 高天荒野舰艇数据契约 import (
    ContractError,
    OutfitPlanInput,
    ResourceReference,
    ShipInstanceSnapshotInput,
    canonical_json,
    load_hull_coating_catalog,
    load_material_registry,
)
from 高天荒野舰艇无界面船壳编译器 import compile_hull
from 高天荒野舰艇无界面舾装编译器 import (
    DerivedShipSnapshot,
    build_derived_ship_snapshot,
    compile_outfit,
)
from 高天荒野舰艇实例设计状态 import (
    SHIP_DESIGN_STATE_INTERFACE_ID,
    embed_initial_design_state,
    reconstruct_current_outfit_plan_from_ship,
    reconstruct_hull_blueprint_from_ship,
)
from 高天荒野舰艇运行时参数编译器 import (
    compile_runtime_ship_parameters,
    initialize_ship_instance_snapshot,
)
from 高天荒野舰艇船坞后勤与战略工时 import (
    LogisticsStockBundle,
    MaterialStock,
    ModuleStock,
    complete_damaged_module_dismantle_artifact,
    complete_destroyed_residue_clearance_artifact,
    complete_refit_artifact,
    confirm_work_order,
    quote_damaged_module_dismantle,
    quote_destroyed_module_residue_clearance,
    quote_ship_refit,
    settle_completed_work_order,
)
from 高天荒野舰艇阶段F三舰集成测试 import build_chain
from 高天荒野舰艇阶段I弹药与武器动作测试 import ammunition_state
from 高天荒野舰艇阶段I船坞后勤与战略工时测试 import (
    complete_order,
    facility,
    inventory_from_bundle,
)


ROOT = Path(__file__).resolve().parent
STRUCTURE_CATALOG = ROOT / "舰艇数据" / "材料" / "结构材质.v1.json"
ARMOR_CATALOG = ROOT / "舰艇数据" / "材料" / "基础装甲材质.v1.json"
COATING_CATALOG = ROOT / "舰艇数据" / "涂料" / "船体涂料.v1.json"
REPORT_PATH = ROOT / "舰艇数据" / "报告" / "阶段I实例设计与改装接口.v1.json"
SCRAP = ResourceReference("gtw.material.fixture.scrap-metal", 1)


def require_contract_error(code: str, action: Callable[[], object]) -> None:
    try:
        action()
    except ContractError as error:
        if error.code != code:
            raise AssertionError(f"预期 {code}，实际为 {error.code}: {error}") from error
    else:
        raise AssertionError(f"预期抛出 {code}")


def target_snapshot(
    chain,
    *,
    plan_id: str,
    plan_name: str,
    remove_instance_id: str | None = None,
    replace_instance_id: str | None = None,
    replacement_prototype: ResourceReference | None = None,
) -> DerivedShipSnapshot:
    source = chain.outfit.normalized_plan.to_dict()
    source["id"] = plan_id
    source["name"] = plan_name
    source["version"] = 1
    if remove_instance_id is not None:
        source["modules"] = [
            item for item in source["modules"] if item["id"] != remove_instance_id
        ]
    if replace_instance_id is not None:
        assert replacement_prototype is not None
        module = next(
            item for item in source["modules"] if item["id"] == replace_instance_id
        )
        module["prototype"] = replacement_prototype.to_dict()
    plan = OutfitPlanInput.parse(source)
    outfit = compile_outfit(
        plan,
        chain.hull,
        chain.module_catalog,
        load_hull_coating_catalog(COATING_CATALOG),
    )
    return build_derived_ship_snapshot(chain.hull, outfit)


def complete_design_order(quote, shipyard, *, order_id: str, start_time: float):
    inventory = inventory_from_bundle(
        quote.requirements,
        inventory_id=f"gtw.inventory.fixture.{order_id}",
    )
    confirmation = confirm_work_order(
        quote,
        inventory,
        shipyard,
        order_id=order_id,
        order_name=f"{order_id}夹具订单",
        current_world_time_s=start_time,
    )
    completed = complete_order(
        confirmation.order,
        shipyard,
        start_time=start_time,
    )
    return confirmation, completed


def main() -> None:
    chain = build_chain("conventional_crewed")
    shipyard = facility(
        "ship_refit",
        "ship_destroyed_residue_clearance",
        "ship_damaged_module_dismantle",
    )
    embedded = embed_initial_design_state(chain.snapshot, chain.instance)
    assert embedded.design_state is not None
    assert embedded.design_state.revision == 1
    assert embedded.outfit_plan == chain.instance.outfit_plan
    assert (
        embedded.derived_ship_snapshot_sha256
        == chain.instance.derived_ship_snapshot_sha256
    )
    embedded_text = canonical_json(embedded)
    restored_embedded = ShipInstanceSnapshotInput.parse(json.loads(embedded_text))
    assert canonical_json(restored_embedded) == embedded_text

    refit_target = target_snapshot(
        chain,
        plan_id="gtw.outfit.fixture.stage_i3.refit_target",
        plan_name="阶段I3改装目标夹具",
        replace_instance_id="sensor_upper_starboard",
        replacement_prototype=ResourceReference("gtw.module.fixture.weapon", 1),
    )
    refit_quote = quote_ship_refit(
        "quote.fixture.i3.refit",
        chain.snapshot,
        embedded,
        refit_target,
    )
    refit_confirmation, completed_refit = complete_design_order(
        refit_quote,
        shipyard,
        order_id="order.fixture.i3.refit",
        start_time=100.0,
    )
    require_contract_error(
        "logistics.order_not_ready_to_settle",
        lambda: settle_completed_work_order(
            completed_refit,
            refit_confirmation.inventory,
        ),
    )
    refit_application = complete_refit_artifact(
        completed_refit,
        chain.snapshot,
        embedded,
        refit_target,
    )
    refitted = refit_application.instance
    assert refitted.design_state is not None
    assert refitted.design_state.revision == 2
    assert (
        refitted.design_state.current_derived_ship_snapshot_sha256
        == refit_target.source_sha256
    )
    assert refitted.outfit_plan == embedded.outfit_plan
    assert (
        refitted.derived_ship_snapshot_sha256
        == embedded.derived_ship_snapshot_sha256
    )
    assert refitted.sortie_configuration == embedded.sortie_configuration
    assert (
        refitted.sortie_configuration_sha256
        == embedded.sortie_configuration_sha256
    )
    refitted_states = {item.instance_id: item for item in refitted.module_states}
    replacement = next(
        item
        for item in refit_target.outfit.instances
        if item.id == "sensor_upper_starboard"
    )
    assert (
        refitted_states["sensor_upper_starboard"].current_durability_points
        == replacement.prototype.durability_points
    )
    compile_runtime_ship_parameters(refit_target, chain.sortie, refitted)
    settled_refit = settle_completed_work_order(
        refit_application.order,
        refit_confirmation.inventory,
    )
    returned_sensor = ResourceReference("gtw.module.fixture.sensor", 1)
    assert settled_refit.inventory.stock.modules == (ModuleStock(returned_sensor, 1),)

    second_refit_quote = quote_ship_refit(
        "quote.fixture.i3.second_refit",
        refit_target,
        refitted,
        chain.snapshot,
    )
    second_confirmation, completed_second_refit = complete_design_order(
        second_refit_quote,
        shipyard,
        order_id="order.fixture.i3.second_refit",
        start_time=150.0,
    )
    second_application = complete_refit_artifact(
        completed_second_refit,
        refit_target,
        refitted,
        chain.snapshot,
    )
    twice_refitted = second_application.instance
    assert twice_refitted.design_state is not None
    assert twice_refitted.design_state.revision == 3
    assert twice_refitted.outfit_plan == embedded.outfit_plan
    compile_runtime_ship_parameters(chain.snapshot, chain.sortie, twice_refitted)
    settle_completed_work_order(
        second_application.order,
        second_confirmation.inventory,
    )

    reconstructed_hull_input = reconstruct_hull_blueprint_from_ship(refitted)
    reconstructed_plan = reconstruct_current_outfit_plan_from_ship(refitted)
    registry = load_material_registry((STRUCTURE_CATALOG, ARMOR_CATALOG))
    reconstructed_hull = compile_hull(reconstructed_hull_input, registry)
    reconstructed_outfit = compile_outfit(
        reconstructed_plan,
        reconstructed_hull,
        chain.module_catalog,
        load_hull_coating_catalog(COATING_CATALOG),
    )
    reconstructed_snapshot = build_derived_ship_snapshot(
        reconstructed_hull,
        reconstructed_outfit,
    )
    assert reconstructed_snapshot.source_sha256 == refit_target.source_sha256
    refitted_text = canonical_json(refitted)
    assert canonical_json(
        ShipInstanceSnapshotInput.parse(json.loads(refitted_text))
    ) == refitted_text

    removed_sensor_target = target_snapshot(
        chain,
        plan_id="gtw.outfit.fixture.stage_i3.sensor_removed",
        plan_name="阶段I3移除传感器夹具",
        remove_instance_id="sensor_upper_starboard",
    )
    destroyed_sensor = replace(
        embedded,
        module_states=tuple(
            replace(item, current_durability_points=0.0)
            if item.instance_id == "sensor_upper_starboard"
            else item
            for item in embedded.module_states
        ),
    )
    clearance_quote = quote_destroyed_module_residue_clearance(
        "quote.fixture.i3.clearance",
        chain.snapshot,
        destroyed_sensor,
        removed_sensor_target,
        module_instance_id="sensor_upper_starboard",
        resolved_scrap=MaterialStock(SCRAP, 7),
    )
    clearance_confirmation, completed_clearance = complete_design_order(
        clearance_quote,
        shipyard,
        order_id="order.fixture.i3.clearance",
        start_time=200.0,
    )
    clearance_application = complete_destroyed_residue_clearance_artifact(
        completed_clearance,
        chain.snapshot,
        destroyed_sensor,
        removed_sensor_target,
    )
    cleared = clearance_application.instance
    assert "sensor_upper_starboard" not in {
        item.instance_id for item in cleared.module_states
    }
    settled_clearance = settle_completed_work_order(
        clearance_application.order,
        clearance_confirmation.inventory,
    )
    assert settled_clearance.inventory.stock.materials == (MaterialStock(SCRAP, 7),)
    compile_runtime_ship_parameters(removed_sensor_target, chain.sortie, cleared)

    damaged_sensor = replace(
        embedded,
        module_states=tuple(
            replace(item, current_durability_points=50.0)
            if item.instance_id == "sensor_upper_starboard"
            else item
            for item in embedded.module_states
        ),
    )
    failed_quote = quote_damaged_module_dismantle(
        "quote.fixture.i3.dismantle_failed",
        chain.snapshot,
        damaged_sensor,
        chain.snapshot,
        module_instance_id="sensor_upper_starboard",
        resolved_success=False,
    )
    failed_confirmation, completed_failed = complete_design_order(
        failed_quote,
        shipyard,
        order_id="order.fixture.i3.dismantle_failed",
        start_time=300.0,
    )
    failed_application = complete_damaged_module_dismantle_artifact(
        completed_failed,
        chain.snapshot,
        damaged_sensor,
        chain.snapshot,
        module_instance_id="sensor_upper_starboard",
        resolved_success=False,
    )
    assert canonical_json(failed_application.instance) == canonical_json(damaged_sensor)
    failed_settlement = settle_completed_work_order(
        failed_application.order,
        failed_confirmation.inventory,
    )
    assert failed_settlement.inventory.stock == LogisticsStockBundle()

    success_quote = quote_damaged_module_dismantle(
        "quote.fixture.i3.dismantle_success",
        chain.snapshot,
        damaged_sensor,
        removed_sensor_target,
        module_instance_id="sensor_upper_starboard",
        resolved_success=True,
    )
    success_confirmation, completed_success = complete_design_order(
        success_quote,
        shipyard,
        order_id="order.fixture.i3.dismantle_success",
        start_time=400.0,
    )
    require_contract_error(
        "logistics.artifact_target_state_mismatch",
        lambda: complete_damaged_module_dismantle_artifact(
            completed_success,
            chain.snapshot,
            damaged_sensor,
            removed_sensor_target,
            module_instance_id="sensor_upper_starboard",
            resolved_success=False,
        ),
    )
    success_application = complete_damaged_module_dismantle_artifact(
        completed_success,
        chain.snapshot,
        damaged_sensor,
        removed_sensor_target,
        module_instance_id="sensor_upper_starboard",
        resolved_success=True,
    )
    success_settlement = settle_completed_work_order(
        success_application.order,
        success_confirmation.inventory,
    )
    assert success_settlement.inventory.stock.modules == (
        ModuleStock(returned_sensor, 1),
    )

    require_contract_error(
        "logistics.residue_not_destroyed",
        lambda: quote_destroyed_module_residue_clearance(
            "quote.fixture.i3.clearance_live",
            chain.snapshot,
            embedded,
            removed_sensor_target,
            module_instance_id="sensor_upper_starboard",
            resolved_scrap=MaterialStock(SCRAP, 1),
        ),
    )
    require_contract_error(
        "logistics.destroyed_module_requires_clearance",
        lambda: quote_damaged_module_dismantle(
            "quote.fixture.i3.dismantle_destroyed",
            chain.snapshot,
            destroyed_sensor,
            removed_sensor_target,
            module_instance_id="sensor_upper_starboard",
            resolved_success=True,
        ),
    )
    require_contract_error(
        "logistics.refit_damaged_module_requires_dismantle",
        lambda: quote_ship_refit(
            "quote.fixture.i3.refit_damaged",
            chain.snapshot,
            damaged_sensor,
            refit_target,
        ),
    )

    live_configuration = replace(
        chain.sortie.configuration,
        id="gtw.sortie.fixture.stage_i3.loaded",
        name="阶段I3装弹拆卸夹具",
        ammunition_loadout=ammunition_state(),
    )
    live_sortie = compile_sortie_configuration(chain.snapshot, live_configuration)
    live_instance = initialize_ship_instance_snapshot(
        chain.snapshot,
        live_sortie,
        embed_design_state=True,
    )
    weapon_removed_target = target_snapshot(
        chain,
        plan_id="gtw.outfit.fixture.stage_i3.weapon_removed",
        plan_name="阶段I3移除已装弹武器夹具",
        remove_instance_id="weapon_upper_port",
    )
    require_contract_error(
        "refit.ammunition_must_be_unloaded",
        lambda: quote_ship_refit(
            "quote.fixture.i3.refit_loaded_weapon",
            chain.snapshot,
            live_instance,
            weapon_removed_target,
        ),
    )

    report = {
        "fixture_notice": (
            "拆卸成功/失败、废金属种类与7单位产量均为外部注入的合同夹具，"
            "不是正式概率或回收公式。"
        ),
        "interface": SHIP_DESIGN_STATE_INTERFACE_ID,
        "persistence": {
            "construction_hull_embedded": True,
            "current_outfit_embedded": True,
            "deleted_blueprint_reconstruction_verified": True,
            "historical_sortie_and_construction_sources_unchanged": True,
            "round_trip_verified": True,
        },
        "refit": {
            "artifact_before_inventory_settlement_required": True,
            "continuous_refit_revision": twice_refitted.design_state.revision,
            "original_module_returned": True,
            "runtime_compiles_against_current_design": True,
        },
        "residue_and_dismantle": {
            "destroyed_residue_cleared": True,
            "externally_resolved_scrap_units": 7,
            "failed_dismantle_preserves_ship": True,
            "successful_dismantle_returns_module": True,
        },
        "status": "PASS",
        "tested_failures": [
            "design_order_settlement_before_artifact_application",
            "dismantle_outcome_changed_after_confirmation",
            "live_module_sent_to_residue_clearance",
            "destroyed_module_sent_to_dismantle",
            "damaged_module_implicitly_removed_by_refit",
            "loaded_weapon_removed_before_unloading",
        ],
    }
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
