"""阶段 I：常规有人舰实际布局上的弹药持久化、开火与装填回归。"""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from typing import Callable

from 高天荒野舰艇出航配置编译器 import compile_sortie_configuration
from 高天荒野舰艇数据契约 import (
    AmmunitionInventoryEntryInput,
    ContractError,
    MagazineAmmunitionStateInput,
    ShipAmmunitionStateInput,
    ShipInstanceSnapshotInput,
    WeaponReadyAmmunitionStateInput,
    canonical_json,
    canonical_sha256,
)
from 高天荒野舰艇阶段F三舰集成测试 import build_chain
from 高天荒野舰艇弹药与武器动作结算器 import (
    AMMUNITION_ACTION_INTERFACE_ID,
    WeaponFireRequest,
    WeaponReloadRequest,
    resolve_weapon_fire,
    resolve_weapon_reload,
)
from 高天荒野舰艇运行时参数编译器 import initialize_ship_instance_snapshot


ROOT = Path(__file__).resolve().parent
REPORT_PATH = ROOT / "舰艇数据" / "报告" / "阶段I弹药与武器动作接口.v1.json"
STANDARD = "gtw.munition.fixture.76mm.standard"
SPECIAL = "gtw.munition.fixture.76mm.special"


def require_contract_error(code: str, action: Callable[[], object]) -> None:
    try:
        action()
    except ContractError as error:
        if error.code != code:
            raise AssertionError(f"预期错误 {code}，实际为 {error.code}: {error}") from error
    else:
        raise AssertionError(f"预期抛出 {code}")


def ammunition_state() -> ShipAmmunitionStateInput:
    return ShipAmmunitionStateInput(
        magazines=(
            MagazineAmmunitionStateInput(
                "ammunition_magazine",
                (
                    AmmunitionInventoryEntryInput(SPECIAL, 2),
                    AmmunitionInventoryEntryInput(STANDARD, 4),
                ),
            ),
        ),
        weapons=(
            WeaponReadyAmmunitionStateInput(
                "weapon_upper_port", STANDARD, 1
            ),
        ),
    )


def inventory(instance: ShipInstanceSnapshotInput, munition_id: str) -> int:
    assert instance.ammunition_state is not None
    magazine = instance.ammunition_state.magazines[0]
    return next(
        (item.units for item in magazine.inventory if item.munition_id == munition_id),
        0,
    )


def ready(instance: ShipInstanceSnapshotInput) -> tuple[str | None, int]:
    assert instance.ammunition_state is not None
    weapon = instance.ammunition_state.weapons[0]
    return weapon.munition_id, weapon.ready_rounds


def replace_module_durability(
    instance: ShipInstanceSnapshotInput, instance_id: str, durability: float
) -> ShipInstanceSnapshotInput:
    return replace(
        instance,
        module_states=tuple(
            replace(item, current_durability_points=durability)
            if item.instance_id == instance_id
            else item
            for item in instance.module_states
        ),
    )


def fire_request(
    munition_id: str = STANDARD,
    *,
    fire_control_instance_id: str | None = "fire_control",
    distance_m: float = 10000.0,
) -> WeaponFireRequest:
    return WeaponFireRequest(
        "action.fixture.fire",
        "weapon_upper_port",
        munition_id,
        1,
        "ship",
        distance_m,
        fire_control_instance_id,
    )


def main() -> None:
    chain = build_chain("conventional_crewed")
    require_contract_error(
        "weapon_action.ammunition_state_missing",
        lambda: resolve_weapon_fire(
            chain.snapshot, chain.sortie, chain.instance, fire_request()
        ),
    )

    configuration = replace(
        chain.sortie.configuration,
        id="gtw.sortie.fixture.stage_i.conventional_crewed_live_ammunition",
        name="阶段I常规有人舰实弹动作夹具",
        ammunition_loadout=ammunition_state(),
    )
    sortie = compile_sortie_configuration(chain.snapshot, configuration)
    instance = initialize_ship_instance_snapshot(chain.snapshot, sortie)
    assert instance.ammunition_state == configuration.ammunition_loadout
    assert ready(instance) == (STANDARD, 1)
    assert inventory(instance, STANDARD) == 4
    assert inventory(instance, SPECIAL) == 2

    serialized = canonical_json(instance)
    restored = ShipInstanceSnapshotInput.parse(json.loads(serialized))
    assert canonical_json(restored) == serialized
    assert canonical_sha256(restored) == canonical_sha256(instance)

    fired_standard = resolve_weapon_fire(
        chain.snapshot, sortie, restored, fire_request()
    )
    assert ready(fired_standard.resulting_instance) == (None, 0)
    assert inventory(fired_standard.resulting_instance, STANDARD) == 4
    assert dict(fired_standard.function_efficiencies)["weapon.fire"] > 0.0
    assert dict(fired_standard.function_efficiencies)["fire_control.solution"] > 0.0

    reloaded_special = resolve_weapon_reload(
        chain.snapshot,
        sortie,
        fired_standard.resulting_instance,
        WeaponReloadRequest(
            "action.fixture.reload_special",
            "weapon_upper_port",
            SPECIAL,
            1,
        ),
    )
    assert ready(reloaded_special.resulting_instance) == (SPECIAL, 1)
    assert inventory(reloaded_special.resulting_instance, SPECIAL) == 1
    assert reloaded_special.magazine_withdrawals[0].magazine_instance_id == (
        "ammunition_magazine"
    )
    assert dict(reloaded_special.function_efficiencies)["weapon.reload"] > 0.0

    fired_special = resolve_weapon_fire(
        chain.snapshot,
        sortie,
        reloaded_special.resulting_instance,
        fire_request(SPECIAL),
    )
    final_reload = resolve_weapon_reload(
        chain.snapshot,
        sortie,
        fired_special.resulting_instance,
        WeaponReloadRequest(
            "action.fixture.reload_standard",
            "weapon_upper_port",
            STANDARD,
            1,
        ),
    )
    final_instance = final_reload.resulting_instance
    assert ready(final_instance) == (STANDARD, 1)
    assert inventory(final_instance, STANDARD) == 3
    assert inventory(final_instance, SPECIAL) == 1
    final_roundtrip = ShipInstanceSnapshotInput.parse(
        json.loads(canonical_json(final_instance))
    )
    assert canonical_json(final_roundtrip) == canonical_json(final_instance)

    require_contract_error(
        "weapon_action.fire_control_required",
        lambda: resolve_weapon_fire(
            chain.snapshot,
            sortie,
            instance,
            fire_request(fire_control_instance_id=None),
        ),
    )
    require_contract_error(
        "weapon_action.target_out_of_range",
        lambda: resolve_weapon_fire(
            chain.snapshot, sortie, instance, fire_request(distance_m=25000.0)
        ),
    )
    require_contract_error(
        "weapon_action.target_distance",
        lambda: resolve_weapon_fire(
            chain.snapshot, sortie, instance, fire_request(distance_m=float("nan"))
        ),
    )
    require_contract_error(
        "weapon_action.ready_rounds_insufficient",
        lambda: resolve_weapon_fire(
            chain.snapshot, sortie, instance, fire_request(SPECIAL)
        ),
    )
    destroyed_fire_control = replace_module_durability(instance, "fire_control", 0.0)
    require_contract_error(
        "weapon_action.fire_control_unavailable",
        lambda: resolve_weapon_fire(
            chain.snapshot, sortie, destroyed_fire_control, fire_request()
        ),
    )
    empty_ready = fired_standard.resulting_instance
    destroyed_magazine = replace_module_durability(
        empty_ready, "ammunition_magazine", 0.0
    )
    require_contract_error(
        "weapon_action.shared_ammunition_insufficient",
        lambda: resolve_weapon_reload(
            chain.snapshot,
            sortie,
            destroyed_magazine,
            WeaponReloadRequest(
                "action.fixture.reload_from_destroyed_magazine",
                "weapon_upper_port",
                STANDARD,
                1,
            ),
        ),
    )
    weapons_unpowered = replace(
        instance,
        power_policy=replace(
            instance.power_policy,
            disabled_categories=("weapons_and_active_defense",),
        ),
    )
    require_contract_error(
        "weapon_action.weapon_fire_unavailable",
        lambda: resolve_weapon_fire(
            chain.snapshot, sortie, weapons_unpowered, fire_request()
        ),
    )
    fire_control_unpowered = replace(
        instance,
        power_policy=replace(
            instance.power_policy,
            disabled_categories=("fire_control",),
        ),
    )
    require_contract_error(
        "weapon_action.fire_control_unavailable",
        lambda: resolve_weapon_fire(
            chain.snapshot, sortie, fire_control_unpowered, fire_request()
        ),
    )
    no_ordinary_crew = replace(
        empty_ready,
        operational_state=replace(
            empty_ready.operational_state,
            crew=tuple(
                item
                for item in empty_ready.operational_state.crew
                if item.crew_type != "ordinary"
            ),
        ),
    )
    require_contract_error(
        "weapon_action.weapon_reload_unavailable",
        lambda: resolve_weapon_reload(
            chain.snapshot,
            sortie,
            no_ordinary_crew,
            WeaponReloadRequest(
                "action.fixture.reload_without_crew",
                "weapon_upper_port",
                STANDARD,
                1,
            ),
        ),
    )

    over_capacity = replace(
        configuration,
        ammunition_loadout=replace(
            ammunition_state(),
            magazines=(
                MagazineAmmunitionStateInput(
                    "ammunition_magazine",
                    (AmmunitionInventoryEntryInput(STANDARD, 101),),
                ),
            ),
        ),
    )
    require_contract_error(
        "sortie.ammunition_magazine_capacity_exceeded",
        lambda: compile_sortie_configuration(chain.snapshot, over_capacity),
    )

    report = {
        "fixture_notice": (
            "弹量仅用于验证状态闭环，不是教程舰或76毫米火炮的正式平衡值。"
        ),
        "interface": AMMUNITION_ACTION_INTERFACE_ID,
        "layout_sources": {
            "outfit_plan": {
                "id": chain.snapshot.outfit.normalized_plan.id,
                "version": chain.snapshot.outfit.normalized_plan.version,
            },
            "outfit_snapshot_sha256": chain.snapshot.source_sha256,
            "ship_role": "stage_f_conventional_crewed_technical_surrogate",
        },
        "positive_chain": {
            "actions": [
                fired_standard.to_dict(),
                reloaded_special.to_dict(),
                fired_special.to_dict(),
                final_reload.to_dict(),
            ],
            "final_inventory": {
                SPECIAL: inventory(final_instance, SPECIAL),
                STANDARD: inventory(final_instance, STANDARD),
            },
            "final_ready": {
                "munition_id": ready(final_instance)[0],
                "rounds": ready(final_instance)[1],
            },
            "save_reload_roundtrip": True,
        },
        "status": "PASS",
        "tested_failures": [
            "missing_ammunition_state",
            "missing_fire_control",
            "target_out_of_range",
            "target_distance_nonfinite",
            "wrong_ready_munition",
            "destroyed_fire_control",
            "destroyed_magazine",
            "weapon_power_category_disabled",
            "fire_control_power_category_disabled",
            "reload_crew_missing",
            "magazine_capacity_exceeded",
        ],
    }
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
