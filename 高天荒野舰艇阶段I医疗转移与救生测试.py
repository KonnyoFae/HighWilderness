"""阶段 I11c：医疗、舰间人员转移、弃舰与战略救援回归。"""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from typing import Callable

from 高天荒野舰艇数据契约 import (
    ContractError,
    ShipCrewCasualtyStateInput,
    ShipInstanceSnapshotInput,
    canonical_json,
    canonical_sha256,
    load_material_registry,
)
from 高天荒野舰艇阶段F三舰集成测试 import build_chain
from 高天荒野舰艇阶段I武器时间与射击队列测试 import live_ship
from 高天荒野舰艇阶段I统一战术场景时间线测试 import scene_fixture
from 高天荒野舰艇阶段I持续毁伤与损管测试 import fire_projectile_catalog
from 高天荒野舰艇武器时间与射击队列 import (
    load_weapon_timing_profile_catalog,
)
from 高天荒野舰艇人员伤亡 import (
    CrewCasualtyBreakdown,
    CrewCasualtyOutcome,
    apply_crew_casualty_outcomes,
    persons_aboard_count,
)
from 高天荒野舰艇人员医疗转移与救生 import (
    CREW_RECOVERY_INTERFACE_ID,
    CREW_RECOVERY_POLICY_ID,
    CrewEvacuationCount,
    CrewEvacuationOutcome,
    CrewMedicalChange,
    CrewMedicalOutcome,
    CrewRescueDispositionOutcome,
    CrewRescueManifest,
    CrewTransferCount,
    CrewTransferDirective,
    apply_crew_evacuation_outcome,
    apply_crew_medical_outcome,
    resolve_crew_rescue_manifest,
    transfer_crew_between_ships,
)
from 高天荒野舰艇统一战术场景 import advance_tactical_scene_step


ROOT = Path(__file__).resolve().parent
MAIN_SCHEMA = ROOT / "舰艇数据" / "模式" / "高天荒野舰艇数据契约.v1alpha1.schema.json"
RESCUE_MANIFEST_SCHEMA = ROOT / "舰艇数据" / "模式" / "高天荒野舰艇人员救援清单契约.v1alpha1.schema.json"
TIMING_CATALOG = ROOT / "舰艇数据" / "标定" / "阶段I武器时间技术替身配置.v1.json"
STRUCTURE_CATALOG = ROOT / "舰艇数据" / "材料" / "结构材质.v1.json"
ARMOR_CATALOG = ROOT / "舰艇数据" / "材料" / "基础装甲材质.v1.json"
REPORT_PATH = ROOT / "舰艇数据" / "报告" / "阶段I医疗转移与救生接口.v1.json"


def require_contract_error(code: str, action: Callable[[], object]) -> None:
    try:
        action()
    except ContractError as error:
        if error.code != code:
            raise AssertionError(f"预期 {code}，实际为 {error.code}: {error}") from error
    else:
        raise AssertionError(f"预期抛出 {code}")


def status_map(instance: ShipInstanceSnapshotInput):
    state = instance.crew_casualty_state
    assert state is not None
    return {item.crew_type: item for item in state.crew_statuses}


def evacuation_counts(instance: ShipInstanceSnapshotInput, *, kill_one_ordinary: bool):
    state = instance.crew_casualty_state
    if state is None:
        rows = tuple(
            (item.crew_type, item.count, 0)
            for item in instance.operational_state.crew
        )
    else:
        rows = tuple(
            (item.crew_type, item.fit_for_duty_count, item.wounded_count)
            for item in state.crew_statuses
            if item.fit_for_duty_count + item.wounded_count > 0
        )
    result = []
    for crew_type, fit, wounded in rows:
        killed_fit = 1 if kill_one_ordinary and crew_type == "ordinary" else 0
        result.append(
            CrewEvacuationCount(
                crew_type,
                fit - killed_fit,
                wounded,
                killed_fit,
                0,
            )
        )
    return tuple(result)


def main() -> None:
    schema = json.loads(MAIN_SCHEMA.read_text(encoding="utf-8"))
    casualty_schema = schema["$defs"]["shipCrewCasualtyState"]
    assert "last_strategic_operation_time_s" in casualty_schema["properties"]
    assert "last_strategic_operation_time_s" not in casualty_schema["required"]
    manifest_schema = json.loads(RESCUE_MANIFEST_SCHEMA.read_text(encoding="utf-8"))
    assert manifest_schema["$id"] == CREW_RECOVERY_INTERFACE_ID

    chain = build_chain("conventional_crewed")
    sortie, legacy_instance = live_ship(chain)
    medical_ship_id = "ship.fixture.stage_i11c.medical"
    casualty = apply_crew_casualty_outcomes(
        legacy_instance,
        (
            CrewCasualtyOutcome(
                "casualty.fixture.stage_i11c.medical",
                "projectile_impact",
                "projectile.fixture.stage_i11c.medical",
                0.1,
                medical_ship_id,
                None,
                (CrewCasualtyBreakdown("ordinary", 3, 0),),
            ),
        ),
        ship_id=medical_ship_id,
        target_tactical_time_s=0.2,
    ).resulting_instance
    medical_outcome = CrewMedicalOutcome(
        "medical.fixture.stage_i11c.primary",
        100.0,
        medical_ship_id,
        (CrewMedicalChange("ordinary", 1, 1),),
    )
    medical = apply_crew_medical_outcome(
        casualty,
        chain.snapshot,
        medical_outcome,
        ship_id=medical_ship_id,
    )
    repeated_medical = apply_crew_medical_outcome(
        casualty,
        chain.snapshot,
        medical_outcome,
        ship_id=medical_ship_id,
    )
    assert repeated_medical == medical
    medical_status = status_map(medical.resulting_instance)["ordinary"]
    assert (
        medical_status.fit_for_duty_count,
        medical_status.wounded_count,
        medical_status.dead_count,
    ) == (8, 1, 1)
    assert medical.resulting_instance.crew_casualty_state is not None
    assert (
        medical.resulting_instance.crew_casualty_state.last_strategic_operation_time_s
        == 100.0
    )
    serialized_instance = canonical_json(medical.resulting_instance)
    restored_instance = ShipInstanceSnapshotInput.parse(json.loads(serialized_instance))
    assert canonical_json(restored_instance) == serialized_instance
    require_contract_error(
        "crew_recovery.strategic_time_reversed",
        lambda: apply_crew_medical_outcome(
            medical.resulting_instance,
            chain.snapshot,
            CrewMedicalOutcome(
                "medical.fixture.stage_i11c.reversed",
                99.0,
                medical_ship_id,
                (CrewMedicalChange("ordinary", 1, 0),),
            ),
            ship_id=medical_ship_id,
        ),
    )
    require_contract_error(
        "crew_recovery.insufficient_wounded",
        lambda: apply_crew_medical_outcome(
            medical.resulting_instance,
            chain.snapshot,
            CrewMedicalOutcome(
                "medical.fixture.stage_i11c.excess",
                101.0,
                medical_ship_id,
                (CrewMedicalChange("ordinary", 2, 0),),
            ),
            ship_id=medical_ship_id,
        ),
    )

    target_ship_id = "ship.fixture.stage_i11c.transfer-target"
    transfer = transfer_crew_between_ships(
        medical.resulting_instance,
        chain.snapshot,
        legacy_instance,
        chain.snapshot,
        CrewTransferDirective(
            "transfer.fixture.stage_i11c.primary",
            200.0,
            medical_ship_id,
            target_ship_id,
            (CrewTransferCount("ordinary", 1, 1),),
        ),
        source_ship_id=medical_ship_id,
        target_ship_id=target_ship_id,
    )
    source_ordinary = status_map(transfer.resulting_source_instance)["ordinary"]
    target_ordinary = status_map(transfer.resulting_target_instance)["ordinary"]
    assert (source_ordinary.fit_for_duty_count, source_ordinary.wounded_count) == (7, 0)
    assert (target_ordinary.fit_for_duty_count, target_ordinary.wounded_count) == (11, 1)
    assert (
        source_ordinary.fit_for_duty_count
        + source_ordinary.wounded_count
        + target_ordinary.fit_for_duty_count
        + target_ordinary.wounded_count
        == 19
    )
    source_sha = canonical_sha256(medical.resulting_instance)
    target_sha = canonical_sha256(legacy_instance)
    require_contract_error(
        "crew_casualty.capacity_exceeded",
        lambda: transfer_crew_between_ships(
            medical.resulting_instance,
            chain.snapshot,
            legacy_instance,
            chain.snapshot,
            CrewTransferDirective(
                "transfer.fixture.stage_i11c.capacity",
                201.0,
                medical_ship_id,
                target_ship_id,
                (CrewTransferCount("technical_officer", 1, 0),),
            ),
            source_ship_id=medical_ship_id,
            target_ship_id=target_ship_id,
        ),
    )
    assert canonical_sha256(medical.resulting_instance) == source_sha
    assert canonical_sha256(legacy_instance) == target_sha

    evacuation_outcome = CrewEvacuationOutcome(
        "evacuation.fixture.stage_i11c.direct",
        "rescue-manifest.fixture.stage_i11c.direct",
        0.2,
        medical_ship_id,
        evacuation_counts(medical.resulting_instance, kill_one_ordinary=True),
    )
    require_contract_error(
        "crew_recovery.ship_not_falling",
        lambda: apply_crew_evacuation_outcome(
            medical.resulting_instance,
            evacuation_outcome,
            ship_id=medical_ship_id,
            physical_status="operational",
            target_tactical_time_s=0.2,
        ),
    )
    evacuation = apply_crew_evacuation_outcome(
        medical.resulting_instance,
        evacuation_outcome,
        ship_id=medical_ship_id,
        physical_status="falling",
        target_tactical_time_s=0.2,
    )
    assert persons_aboard_count(evacuation.resulting_instance) == 0
    assert evacuation.rescue_manifest is not None
    manifest = evacuation.rescue_manifest
    assert sum(
        item.fit_for_duty_count + item.wounded_count
        for item in manifest.survivors
    ) == 16
    restored_manifest = CrewRescueManifest.parse(
        json.loads(canonical_json(manifest))
    )
    assert restored_manifest == manifest

    empty_target = replace(
        legacy_instance,
        operational_state=replace(legacy_instance.operational_state, crew=()),
        crew_casualty_state=ShipCrewCasualtyStateInput(0.0, ()),
    )
    rescued = resolve_crew_rescue_manifest(
        manifest,
        CrewRescueDispositionOutcome(
            "rescue.fixture.stage_i11c.recovered",
            300.0,
            "recovered_to_ship",
            target_ship_id,
        ),
        target_instance=empty_target,
        target_snapshot=chain.snapshot,
    )
    assert rescued.resulting_target_instance is not None
    assert persons_aboard_count(rescued.resulting_target_instance) == 16
    assert rescued.resulting_manifest.status == "recovered"
    assert rescued.resulting_manifest.destination_ship_id == target_ship_id
    lost = resolve_crew_rescue_manifest(
        manifest,
        CrewRescueDispositionOutcome(
            "rescue.fixture.stage_i11c.lost",
            300.0,
            "lost",
        ),
    )
    assert lost.resulting_manifest.status == "lost"
    require_contract_error(
        "crew_recovery.manifest_already_resolved",
        lambda: resolve_crew_rescue_manifest(
            lost.resulting_manifest,
            CrewRescueDispositionOutcome(
                "rescue.fixture.stage_i11c.repeated",
                301.0,
                "lost",
            ),
        ),
    )
    require_contract_error(
        "crew_casualty.capacity_exceeded",
        lambda: resolve_crew_rescue_manifest(
            manifest,
            CrewRescueDispositionOutcome(
                "rescue.fixture.stage_i11c.capacity",
                300.0,
                "recovered_to_ship",
                target_ship_id,
            ),
            target_instance=legacy_instance,
            target_snapshot=chain.snapshot,
        ),
    )

    timing_catalog = load_weapon_timing_profile_catalog(TIMING_CATALOG)
    projectile_catalog = fire_projectile_catalog()
    registry = load_material_registry((STRUCTURE_CATALOG, ARMOR_CATALOG))
    bindings, initial_scene, launch_directive = scene_fixture(
        chain,
        timing_catalog,
        projectile_catalog,
    )
    target_scene_ship = next(
        item for item in initial_scene.ships if item.ship_id.endswith("target")
    )
    falling_candidate = replace(
        target_scene_ship,
        motion_state=replace(
            target_scene_ship.motion_state,
            hull_integrity_fraction=0.0,
        ),
        combat_state=replace(
            target_scene_ship.combat_state,
            instance=replace(
                target_scene_ship.combat_state.instance,
                current_hull_integrity_fraction=0.0,
            ),
        ),
    )
    broken_scene = replace(
        initial_scene,
        ships=tuple(
            falling_candidate if item.ship_id == falling_candidate.ship_id else item
            for item in initial_scene.ships
        ),
    )
    no_automatic_evacuation = advance_tactical_scene_step(
        broken_scene,
        bindings,
        timing_catalog,
        projectile_catalog,
        registry,
        launch_directives=(launch_directive,),
    )
    no_auto_target = next(
        item
        for item in no_automatic_evacuation.resulting_scene.ships
        if item.ship_id == falling_candidate.ship_id
    )
    assert no_auto_target.lifecycle_state.physical_status == "falling"
    assert persons_aboard_count(no_auto_target.combat_state.instance) == 18
    assert not no_automatic_evacuation.crew_evacuation_events
    scene_evacuation = CrewEvacuationOutcome(
        "evacuation.fixture.stage_i11c.scene",
        "rescue-manifest.fixture.stage_i11c.scene",
        initial_scene.fixed_step_s,
        falling_candidate.ship_id,
        evacuation_counts(
            falling_candidate.combat_state.instance,
            kill_one_ordinary=True,
        ),
    )
    scene_result = advance_tactical_scene_step(
        broken_scene,
        bindings,
        timing_catalog,
        projectile_catalog,
        registry,
        crew_evacuation_outcomes=(scene_evacuation,),
        launch_directives=(launch_directive,),
    )
    evacuated_scene_ship = next(
        item
        for item in scene_result.resulting_scene.ships
        if item.ship_id == falling_candidate.ship_id
    )
    assert evacuated_scene_ship.lifecycle_state.physical_status == "falling"
    assert persons_aboard_count(evacuated_scene_ship.combat_state.instance) == 0
    assert len(scene_result.crew_rescue_manifests) == 1
    assert "crew_rescue_manifests" in scene_result.to_dict()

    report = {
        "deterministic_medical_repeat_equal": repeated_medical == medical,
        "evacuation_events": [item.to_dict() for item in evacuation.events],
        "interface": CREW_RECOVERY_INTERFACE_ID,
        "medical_events": [item.to_dict() for item in medical.events],
        "policy": CREW_RECOVERY_POLICY_ID,
        "rescue_event": rescued.event.to_dict(),
        "scene_evacuation_events": [
            item.to_dict() for item in scene_result.crew_evacuation_events
        ],
        "status": "PASS",
        "tested_error_codes": [
            "crew_casualty.capacity_exceeded",
            "crew_recovery.insufficient_wounded",
            "crew_recovery.manifest_already_resolved",
            "crew_recovery.ship_not_falling",
            "crew_recovery.strategic_time_reversed",
        ],
        "tested_paths": [
            "explicit_medical_resolution",
            "atomic_ship_to_ship_transfer",
            "falling_ship_evacuation_boundary",
            "persistent_rescue_manifest",
            "whole_manifest_recovered_to_ship",
            "whole_manifest_lost",
            "no_automatic_evacuation",
            "scene_evacuation_output",
        ],
        "transfer_events": [item.to_dict() for item in transfer.events],
    }
    REPORT_PATH.write_text(canonical_json(report), encoding="utf-8")
    assert json.loads(REPORT_PATH.read_text(encoding="utf-8"))["status"] == "PASS"


if __name__ == "__main__":
    main()
