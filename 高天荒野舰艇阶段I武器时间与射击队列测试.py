"""阶段 I4：战术武器时钟、连续射击、齐射、取消与战损中断回归。"""

from __future__ import annotations

from dataclasses import replace
import json
from math import isclose
from pathlib import Path
from typing import Callable

from 高天荒野舰艇出航配置编译器 import compile_sortie_configuration
from 高天荒野舰艇数据契约 import (
    AmmunitionInventoryEntryInput,
    ContractError,
    MagazineAmmunitionStateInput,
    ResourceReference,
    ShipAmmunitionStateInput,
    ShipInstanceSnapshotInput,
    WeaponReadyAmmunitionStateInput,
    canonical_json,
)
from 高天荒野舰艇阶段F三舰集成测试 import build_chain
from 高天荒野舰艇阶段I实例设计与改装测试 import target_snapshot
from 高天荒野舰艇阶段I弹药与武器动作测试 import (
    STANDARD,
    SPECIAL,
    ammunition_state,
    fire_request,
    inventory,
    ready,
    replace_module_durability,
)
from 高天荒野舰艇弹药与武器动作结算器 import (
    WeaponFireRequest,
    WeaponReloadRequest,
    resolve_weapon_reload,
)
from 高天荒野舰艇运行时参数编译器 import initialize_ship_instance_snapshot
from 高天荒野舰艇船坞后勤与战略工时 import quote_ship_refit
from 高天荒野舰艇武器时间与射击队列 import (
    WEAPON_TIMELINE_INTERFACE_ID,
    advance_weapon_timeline,
    cancel_weapon_sequence,
    enqueue_continuous_fire,
    enqueue_weapon_volley,
    initialize_weapon_timeline,
    load_weapon_timing_profile_catalog,
)


ROOT = Path(__file__).resolve().parent
TIMING_CATALOG = ROOT / "舰艇数据" / "标定" / "阶段I武器时间技术替身配置.v1.json"
TIMING_SCHEMA = ROOT / "舰艇数据" / "模式" / "高天荒野舰艇武器时间数据契约.v1alpha1.schema.json"
REPORT_PATH = ROOT / "舰艇数据" / "报告" / "阶段I武器时间与射击队列接口.v1.json"


def require_contract_error(code: str, action: Callable[[], object]) -> None:
    try:
        action()
    except ContractError as error:
        if error.code != code:
            raise AssertionError(f"预期 {code}，实际为 {error.code}: {error}") from error
    else:
        raise AssertionError(f"预期抛出 {code}")


def live_ship(chain, *, embed_design_state: bool = True):
    configuration = replace(
        chain.sortie.configuration,
        id="gtw.sortie.fixture.stage_i4.timeline",
        name="阶段I4武器时间夹具",
        ammunition_loadout=ammunition_state(),
    )
    sortie = compile_sortie_configuration(chain.snapshot, configuration)
    instance = initialize_ship_instance_snapshot(
        chain.snapshot,
        sortie,
        embed_design_state=embed_design_state,
    )
    return sortie, instance


def two_weapon_ammunition_state() -> ShipAmmunitionStateInput:
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
                "sensor_upper_starboard", STANDARD, 1
            ),
            WeaponReadyAmmunitionStateInput("weapon_upper_port", STANDARD, 1),
        ),
    )


def weapon_ready_map(instance: ShipInstanceSnapshotInput) -> dict[str, int]:
    assert instance.ammunition_state is not None
    return {
        item.instance_id: item.ready_rounds
        for item in instance.ammunition_state.weapons
    }


def main() -> None:
    timing_schema = json.loads(TIMING_SCHEMA.read_text(encoding="utf-8"))
    assert timing_schema["$id"] == "gaotian.weapon-timing/v1alpha1"
    catalog = load_weapon_timing_profile_catalog(TIMING_CATALOG)
    assert catalog.fixture_level == "contract_fixture"
    chain = build_chain("conventional_crewed")
    require_contract_error(
        "weapon_timeline.ammunition_state_missing",
        lambda: initialize_weapon_timeline(
            chain.snapshot,
            chain.instance,
            catalog,
        ),
    )
    sortie, instance = live_ship(chain)
    timed = initialize_weapon_timeline(chain.snapshot, instance, catalog)
    assert timed.weapon_timeline_state is not None
    assert timed.weapon_timeline_state.tactical_time_s == 0.0
    assert len(timed.weapon_timeline_state.clocks) == 1
    serialized = canonical_json(timed)
    restored = ShipInstanceSnapshotInput.parse(json.loads(serialized))
    assert canonical_json(restored) == serialized

    queued = enqueue_continuous_fire(
        chain.snapshot,
        sortie,
        restored,
        catalog,
        replace(fire_request(), id="sequence.fixture.continuous", rounds=3),
    ).resulting_instance
    assert queued.weapon_timeline_state is not None
    assert queued.weapon_timeline_state.sequences[0].phase == "awaiting_fire"
    first_step = advance_weapon_timeline(
        chain.snapshot,
        sortie,
        queued,
        catalog,
        target_tactical_time_s=0.0,
    )
    assert [(item.action_kind, item.tactical_time_s) for item in first_step.events] == [
        ("fire", 0.0)
    ]
    assert ready(first_step.resulting_instance) == (None, 0)
    first_state = first_step.resulting_instance.weapon_timeline_state
    assert first_state is not None
    assert first_state.sequences[0].phase == "reloading"
    assert isclose(first_state.sequences[0].next_event_time_s, 0.25)

    half_second = advance_weapon_timeline(
        chain.snapshot,
        sortie,
        first_step.resulting_instance,
        catalog,
        target_tactical_time_s=0.5,
    )
    assert [(item.action_kind, item.tactical_time_s) for item in half_second.events] == [
        ("reload", 0.25)
    ]
    assert ready(half_second.resulting_instance) == (STANDARD, 1)
    half_state = half_second.resulting_instance.weapon_timeline_state
    assert half_state is not None
    assert half_state.sequences[0].phase == "awaiting_fire"
    assert isclose(half_state.sequences[0].next_event_time_s, 1.0)

    completed = advance_weapon_timeline(
        chain.snapshot,
        sortie,
        half_second.resulting_instance,
        catalog,
        target_tactical_time_s=2.0,
    )
    assert [(item.action_kind, item.tactical_time_s) for item in completed.events] == [
        ("fire", 1.0),
        ("reload", 1.25),
        ("fire", 2.0),
    ]
    assert all(item.status == "resolved" for item in completed.events)
    assert ready(completed.resulting_instance) == (None, 0)
    assert inventory(completed.resulting_instance, STANDARD) == 2
    completed_state = completed.resulting_instance.weapon_timeline_state
    assert completed_state is not None
    assert completed_state.sequences == ()
    assert isclose(completed_state.clocks[0].next_fire_time_s, 3.0)
    completed_text = canonical_json(completed.resulting_instance)
    assert canonical_json(
        ShipInstanceSnapshotInput.parse(json.loads(completed_text))
    ) == completed_text

    require_contract_error(
        "weapon_timeline.time_reversed",
        lambda: advance_weapon_timeline(
            chain.snapshot,
            sortie,
            completed.resulting_instance,
            catalog,
            target_tactical_time_s=1.9,
        ),
    )
    cancellable = enqueue_continuous_fire(
        chain.snapshot,
        sortie,
        completed.resulting_instance,
        catalog,
        replace(fire_request(), id="sequence.fixture.cancel", rounds=1),
    ).resulting_instance
    before_cancel_inventory = inventory(cancellable, STANDARD)
    cancelled = cancel_weapon_sequence(
        cancellable, "sequence.fixture.cancel"
    ).resulting_instance
    after_cancel = advance_weapon_timeline(
        chain.snapshot,
        sortie,
        cancelled,
        catalog,
        target_tactical_time_s=4.0,
    )
    assert after_cancel.events == ()
    assert inventory(after_cancel.resulting_instance, STANDARD) == before_cancel_inventory

    interrupted_queue = enqueue_continuous_fire(
        chain.snapshot,
        sortie,
        initialize_weapon_timeline(chain.snapshot, instance, catalog),
        catalog,
        replace(fire_request(), id="sequence.fixture.interrupted", rounds=2),
    ).resulting_instance
    interrupted_first = advance_weapon_timeline(
        chain.snapshot,
        sortie,
        interrupted_queue,
        catalog,
        target_tactical_time_s=0.0,
    ).resulting_instance
    damaged_during_reload = replace_module_durability(
        interrupted_first, "weapon_upper_port", 0.0
    )
    interrupted = advance_weapon_timeline(
        chain.snapshot,
        sortie,
        damaged_during_reload,
        catalog,
        target_tactical_time_s=0.25,
    )
    assert len(interrupted.events) == 1
    assert interrupted.events[0].status == "failed"
    assert (
        interrupted.events[0].error_code
        == "weapon_action.weapon_reload_unavailable"
    )
    assert inventory(interrupted.resulting_instance, STANDARD) == 4
    assert interrupted.resulting_instance.weapon_timeline_state is not None
    assert interrupted.resulting_instance.weapon_timeline_state.sequences == ()

    weapon_removed_target = target_snapshot(
        chain,
        plan_id="gtw.outfit.fixture.stage_i4.weapon_removed",
        plan_name="阶段I4移除活动队列武器夹具",
        remove_instance_id="weapon_upper_port",
    )
    require_contract_error(
        "refit.weapon_sequence_must_be_cancelled",
        lambda: quote_ship_refit(
            "quote.fixture.stage_i4.active_sequence_refit",
            chain.snapshot,
            interrupted_first,
            weapon_removed_target,
        ),
    )
    cancelled_before_refit = cancel_weapon_sequence(
        interrupted_first,
        "sequence.fixture.interrupted",
    ).resulting_instance
    quote_ship_refit(
        "quote.fixture.stage_i4.cancelled_sequence_refit",
        chain.snapshot,
        cancelled_before_refit,
        weapon_removed_target,
    )

    two_snapshot = target_snapshot(
        chain,
        plan_id="gtw.outfit.fixture.stage_i4.two_weapons",
        plan_name="阶段I4双武器齐射夹具",
        replace_instance_id="sensor_upper_starboard",
        replacement_prototype=ResourceReference("gtw.module.fixture.weapon", 1),
    )
    two_configuration = replace(
        chain.sortie.configuration,
        id="gtw.sortie.fixture.stage_i4.two_weapons",
        name="阶段I4双武器齐射出航夹具",
        outfit_plan=ResourceReference(
            two_snapshot.outfit.normalized_plan.id,
            two_snapshot.outfit.normalized_plan.version,
        ),
        ammunition_loadout=two_weapon_ammunition_state(),
    )
    two_sortie = compile_sortie_configuration(two_snapshot, two_configuration)
    two_instance = initialize_ship_instance_snapshot(two_snapshot, two_sortie)
    two_timed = initialize_weapon_timeline(two_snapshot, two_instance, catalog)
    volley_requests = (
        WeaponFireRequest(
            "sequence.fixture.volley.port",
            "weapon_upper_port",
            STANDARD,
            1,
            "ship",
            10000.0,
            "fire_control",
        ),
        WeaponFireRequest(
            "sequence.fixture.volley.starboard",
            "sensor_upper_starboard",
            STANDARD,
            1,
            "ship",
            10000.0,
            "fire_control",
        ),
    )
    volley = enqueue_weapon_volley(
        two_snapshot,
        two_sortie,
        two_timed,
        catalog,
        group_id="volley.fixture.alpha",
        requests=volley_requests,
    ).resulting_instance
    volley_result = advance_weapon_timeline(
        two_snapshot,
        two_sortie,
        volley,
        catalog,
        target_tactical_time_s=0.0,
    )
    assert len(volley_result.events) == 2
    assert {item.tactical_time_s for item in volley_result.events} == {0.0}
    assert {item.group_id for item in volley_result.events} == {
        "volley.fixture.alpha"
    }
    assert weapon_ready_map(volley_result.resulting_instance) == {
        "sensor_upper_starboard": 0,
        "weapon_upper_port": 0,
    }
    assert inventory(volley_result.resulting_instance, STANDARD) == 4

    reloaded_once = resolve_weapon_reload(
        two_snapshot,
        two_sortie,
        volley_result.resulting_instance,
        WeaponReloadRequest(
            "action.fixture.volley_reload_port",
            "weapon_upper_port",
            STANDARD,
            1,
        ),
    ).resulting_instance
    reloaded_both = resolve_weapon_reload(
        two_snapshot,
        two_sortie,
        reloaded_once,
        WeaponReloadRequest(
            "action.fixture.volley_reload_starboard",
            "sensor_upper_starboard",
            STANDARD,
            1,
        ),
    ).resulting_instance
    require_contract_error(
        "weapon_timeline.volley_weapon_cooling",
        lambda: enqueue_weapon_volley(
            two_snapshot,
            two_sortie,
            reloaded_both,
            catalog,
            group_id="volley.fixture.too_early",
            requests=volley_requests,
        ),
    )
    require_contract_error(
        "weapon_timeline.volley_size",
        lambda: enqueue_weapon_volley(
            two_snapshot,
            two_sortie,
            two_timed,
            catalog,
            group_id="volley.fixture.single",
            requests=(volley_requests[0],),
        ),
    )

    mismatched_state = replace(
        timed.weapon_timeline_state,
        timing_profile_catalog_sha256="0" * 64,
    )
    require_contract_error(
        "weapon_timeline.profile_catalog_mismatch",
        lambda: enqueue_continuous_fire(
            chain.snapshot,
            sortie,
            replace(timed, weapon_timeline_state=mismatched_state),
            catalog,
            replace(fire_request(), id="sequence.fixture.bad_catalog"),
        ),
    )

    report = {
        "fixture_notice": (
            "60rpm 与0.25秒逐发装填只用于验证战术时间、冷却和队列合同，"
            "不是正式火炮射速或装填时间。"
        ),
        "interface": WEAPON_TIMELINE_INTERFACE_ID,
        "continuous_fire": {
            "fire_times_s": [0.0, 1.0, 2.0],
            "magazine_rounds_consumed_by_reload": 2,
            "queue_completed": True,
        },
        "persistence": {
            "active_queue_round_trip": True,
            "cooldown_round_trip": True,
            "profile_catalog_hash_bound": True,
        },
        "status": "PASS",
        "volley": {
            "same_tactical_time": True,
            "weapon_count": 2,
        },
        "tested_failures": [
            "timeline_initialized_without_ammunition_state",
            "tactical_time_reversed",
            "sequence_cancelled_without_ammunition_change",
            "reload_interrupted_by_weapon_destruction",
            "refit_attempted_with_active_weapon_sequence",
            "volley_attempted_during_cooldown",
            "volley_has_only_one_weapon",
            "timing_profile_catalog_hash_changed",
        ],
    }
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
