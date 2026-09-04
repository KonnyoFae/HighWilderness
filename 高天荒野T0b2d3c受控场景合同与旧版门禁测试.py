"""d3.3 第一阶段：受控场景合同、严格负例与 v5/v4 隔离门禁。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path

from 高天荒野舰艇数据契约 import ContractError, canonical_sha256, load_json
from 高天荒野舰艇实际推进合同 import ActualActuationRequest
from 高天荒野舰艇战术机动求解器 import ActualTacticalStepDiagnostics, Vec2
from 高天荒野舰艇气动缓存 import DragBreakdown
from 高天荒野舰艇受控推进时间边界 import GOVERNED_TIME_POLICY_ID
from 高天荒野舰艇整舰推进安全判定 import VECTOR_SAFETY_POLICY_ID
from 高天荒野舰艇统一战术场景 import (
    TacticalSceneState,
    advance_tactical_scene_step,
    validate_actual_scene_context,
)
from 高天荒野舰艇受控推进场景合同 import (
    GOVERNED_SCENE_INTERFACE_ID,
    GOVERNED_SCENE_POLICY_ID,
    GOVERNED_STEP_INTERFACE_ID,
    GOVERNED_STEP_POLICY_ID,
    GovernedActualTacticalStepDiagnostics,
    GovernedPropulsionClosingRecord,
    GovernedPropulsionExecutionPolicy,
    GovernedPropulsionOpeningRecord,
    GovernedScenePropulsionSafetyEvent,
    GovernedSceneSave,
    validate_governed_scene_step_contract,
)
from 高天荒野T0b2d2b4场景接线与新黄金测试 import case, step
from 高天荒野T0b2d3b整舰安全判定测试 import (
    controls,
    governors,
    next_states,
    run,
    states,
)


ROOT = Path(__file__).resolve().parent
REPORT = ROOT / "舰艇数据/报告/阶段T0b2d3c受控场景合同与旧版门禁接口.v1.json"
SCHEMAS = (
    "舰艇数据/模式/高天荒野舰艇受控推进治理标记契约.v1alpha1.schema.json",
    "舰艇数据/模式/高天荒野舰艇受控推进开边界记录契约.v1alpha1.schema.json",
    "舰艇数据/模式/高天荒野舰艇受控推进收边界记录契约.v1alpha1.schema.json",
    "舰艇数据/模式/高天荒野舰艇场景推进安全事件契约.v1alpha1.schema.json",
    "舰艇数据/模式/高天荒野舰艇实际推进积分诊断契约.v2alpha1.schema.json",
    "舰艇数据/模式/高天荒野舰艇统一战术场景状态契约.v6alpha1.schema.json",
    "舰艇数据/模式/高天荒野舰艇受控实际推进场景存档契约.v1alpha1.schema.json",
    "舰艇数据/模式/高天荒野舰艇场景单步推进结果契约.v5alpha1.schema.json",
)


def refused(action, code=None):
    try:
        action()
    except ContractError as error:
        if code is not None:
            assert error.code == code, str(error)
        return
    raise AssertionError("非法输入必须以 ContractError 拒绝")


def contract_fixtures():
    control = controls()
    safety0 = run(states({}), governors(control), 0, lambda vector: (0.1, 1.0))
    opening = GovernedPropulsionOpeningRecord(
        "ship.contract",
        0,
        "0" * 64,
        "1" * 64,
        control,
        control,
        safety0.governors,
        control.channel_commands,
        safety0.engine_results,
    )
    closing_result = run(
        next_states(safety0),
        safety0.governors,
        1,
        lambda vector: (2.0, 13.0),
    )
    closing = GovernedPropulsionClosingRecord(
        "ship.contract",
        1,
        "1" * 64,
        "2" * 64,
        "3" * 64,
        "4" * 64,
        "delivered",
        True,
        closing_result,
    )
    safety_events = tuple(
        GovernedScenePropulsionSafetyEvent(closing.ship_id, "closing", intent)
        for intent in closing_result.event_intents
    )
    request = ActualActuationRequest("5" * 64, "6" * 64, 0, (0.0, 0.0), 0.0, 0.0)
    actual = ActualTacticalStepDiagnostics(
        request,
        1,
        0.0,
        1.0,
        0.0,
        1.0,
        0.0,
        0.0,
        Vec2(),
        0.0,
        Vec2(),
        DragBreakdown(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    )
    diagnostic = GovernedActualTacticalStepDiagnostics(
        actual,
        opening.resulting_propulsion_state_sha256,
        canonical_sha256([item.to_dict() for item in opening.resulting_governors]),
    )
    step_payload = {
        "interface": GOVERNED_STEP_INTERFACE_ID,
        "policy": GOVERNED_STEP_POLICY_ID,
        "source_scene_sha256": "9" * 64,
        "resulting_scene_sha256": "a" * 64,
        "source_fixed_step_index": 0,
        "resulting_fixed_step_index": 1,
        "weapon_events": [],
        "spawned_projectiles": [],
        "impact_events": [],
        "expired_events": [],
        "lifecycle_events": [],
        "engagement_events": [],
        "ship_results": [
            {
                "ship_id": "ship.contract",
                "resulting_runtime_parameters_sha256": "3" * 64,
                "diagnostics": diagnostic.to_dict(),
                "propulsion_aggregation": {"interface": "contract-fixture"},
                "propulsion_delivery_status": "delivered",
                "missing_propulsion_channels": [],
            },
            {
                "ship_id": "ship.exited",
                "resulting_runtime_parameters_sha256": "b" * 64,
                "diagnostics": None,
                "propulsion_aggregation": None,
                "propulsion_delivery_status": "suppressed_exited",
                "missing_propulsion_channels": [],
            }
        ],
        "propulsion_opening_records": [opening.to_dict()],
        "propulsion_closing_records": [closing.to_dict()],
        "propulsion_events": [],
        "propulsion_safety_events": [item.to_dict() for item in safety_events],
        "soft_governor_status": "wired",
        "hard_fault_status": "unwired",
        "direction_interlock_status": "unwired",
    }
    validate_governed_scene_step_contract(step_payload)
    return opening, closing, safety_events, diagnostic, step_payload


def governed_scene_fixture():
    old, source, session = case("functional_6.motion_only")
    ships = tuple(
        replace(
            ship,
            propulsion_state=replace(
                ship.propulsion_state,
                governors=tuple(
                    replace(governor, last_evaluated_step_index=0)
                    for governor in ship.propulsion_state.governors
                ),
            ),
        )
        for ship in session.scene.ships
    )
    governed = replace(
        session.scene,
        ships=ships,
        propulsion_governance=GovernedPropulsionExecutionPolicy(),
    )
    assert governed.to_dict()["interface"] == GOVERNED_SCENE_INTERFACE_ID
    assert governed.to_dict()["policy"] == GOVERNED_SCENE_POLICY_ID
    assert TacticalSceneState.parse(governed.to_dict()) == governed
    return old, source, session, governed


def check_round_trips_and_negative_contracts():
    opening, closing, safety_events, diagnostic, step_payload = contract_fixtures()
    assert GovernedPropulsionExecutionPolicy.parse(GovernedPropulsionExecutionPolicy().to_dict()) == GovernedPropulsionExecutionPolicy()
    assert GovernedPropulsionOpeningRecord.parse(opening.to_dict()) == opening
    assert GovernedPropulsionClosingRecord.parse(closing.to_dict()) == closing
    assert all(GovernedScenePropulsionSafetyEvent.parse(item.to_dict()) == item for item in safety_events)
    assert GovernedActualTacticalStepDiagnostics.parse(diagnostic.to_dict()) == diagnostic
    assert GovernedPropulsionExecutionPolicy().time_policy == GOVERNED_TIME_POLICY_ID
    assert GovernedPropulsionExecutionPolicy().safety_policy == VECTOR_SAFETY_POLICY_ID
    negative = 0

    def reject_payload(mutate, code=None):
        nonlocal negative
        value = deepcopy(step_payload)
        mutate(value)
        refused(lambda: validate_governed_scene_step_contract(value), code)
        negative += 1

    mutations = (
        lambda value: value.update(interface="gaotian.tactical-scene-step-resolution/v4alpha1"),
        lambda value: value.update(policy="gaotian.tactical-scene-step/actual-open-integrate-close-unprotected/v1"),
        lambda value: value.update(soft_governor_status="unwired"),
        lambda value: value.update(propulsion_boundaries=[]),
        lambda value: value.update(resulting_fixed_step_index=2),
        lambda value: value["propulsion_opening_records"][0].update(boundary_phase="closing"),
        lambda value: value["propulsion_closing_records"][0].update(fixed_step_index=2),
        lambda value: value["propulsion_safety_events"].pop(),
        lambda value: value["propulsion_safety_events"].append(deepcopy(value["propulsion_safety_events"][0])),
        lambda value: value["ship_results"][0]["diagnostics"].update(interface="gaotian.actual-propulsion-step-diagnostics/v1alpha1"),
        lambda value: value["ship_results"][0].update(extra=0),
        lambda value: value["ship_results"][0].update(missing_propulsion_channels=["yaw.clockwise", "translation.forward"]),
        lambda value: value["propulsion_opening_records"][0].update(resulting_propulsion_state_sha256="f" * 64),
        lambda value: value["ship_results"][0]["diagnostics"].update(source_propulsion_state_sha256="f" * 64),
        lambda value: value["ship_results"][0]["diagnostics"].update(source_governors_sha256="f" * 64),
        lambda value: value["propulsion_closing_records"][0].update(runtime_parameters_sha256="f" * 64),
        lambda value: value["propulsion_closing_records"][0].update(propulsion_delivery_status="suppressed_falling"),
        lambda value: value["ship_results"].reverse(),
        lambda value: value["ship_results"][1].update(diagnostics=deepcopy(value["ship_results"][0]["diagnostics"])),
        lambda value: value["ship_results"][1].update(propulsion_aggregation={"interface": "forbidden"}),
    )
    for mutation in mutations:
        reject_payload(mutation)
    for payload, parser in (
        (opening.to_dict(), GovernedPropulsionOpeningRecord.parse),
        (closing.to_dict(), GovernedPropulsionClosingRecord.parse),
        (safety_events[0].to_dict(), GovernedScenePropulsionSafetyEvent.parse),
        (diagnostic.to_dict(), GovernedActualTacticalStepDiagnostics.parse),
    ):
        changed = deepcopy(payload)
        changed["extra"] = 0
        refused(lambda changed=changed, parser=parser: parser(changed))
        negative += 1
    return {
        "strict_contract_forms": 6,
        "negative_contracts": negative,
        "opening_governor_commands": len(opening.governor_commands),
        "closing_safety_events": len(safety_events),
    }


def check_v6_state_save_and_legacy_gates():
    old, source, session, governed = governed_scene_fixture()
    old_payload = session.scene.to_dict()
    old_hash = canonical_sha256(old_payload)
    saved = GovernedSceneSave(governed.to_dict())
    assert GovernedSceneSave.parse(saved.to_dict()) == saved
    assert canonical_sha256(session.scene.to_dict()) == old_hash
    assert session.scene.to_dict()["interface"] == "gaotian.tactical-scene-timeline/v5alpha1"
    refused(lambda: GovernedSceneSave(old_payload), "governed_scene.save_scene_interface")
    refused(lambda: validate_actual_scene_context(governed, session.propulsion_context), "actual_scene.governed_version")
    refused(
        lambda: advance_tactical_scene_step(
            governed,
            session.bindings,
            old.timing_catalog,
            old.projectile_catalog,
            old.material_registry,
        ),
        "governed_scene.context_required",
    )
    governed_result = advance_tactical_scene_step(
        governed,
        session.bindings,
        old.timing_catalog,
        old.projectile_catalog,
        old.material_registry,
        propulsion_context=session.propulsion_context,
    )
    assert governed_result.to_dict()["interface"] == (
        "gaotian.tactical-scene-step-resolution/v5alpha1"
    )
    v5_mixed = deepcopy(old_payload)
    v5_mixed["propulsion_governance"] = GovernedPropulsionExecutionPolicy().to_dict()
    refused(lambda: TacticalSceneState.parse(v5_mixed))
    missing_marker = governed.to_dict()
    missing_marker.pop("propulsion_governance")
    refused(lambda: TacticalSceneState.parse(missing_marker))
    wrong_marker = governed.to_dict()
    wrong_marker["propulsion_governance"]["safety_policy"] = "unknown"
    refused(lambda: TacticalSceneState.parse(wrong_marker), "governed_scene.safety_policy")
    stale_clock = governed.to_dict()
    stale_clock["ships"][0]["propulsion_state"]["governors"][0]["last_evaluated_step_index"] = None
    refused(lambda: TacticalSceneState.parse(stale_clock), "governed_scene.governor_clock")
    legacy_result = step(old, session, session.scene)
    legacy_payload = legacy_result.to_dict()
    assert legacy_payload["interface"] == "gaotian.tactical-scene-step-resolution/v4alpha1"
    assert legacy_payload["soft_governor_status"] == "unwired"
    assert "propulsion_opening_records" not in legacy_payload
    refused(lambda: replace(legacy_result, resulting_scene=governed).to_dict())
    return {
        "v6_round_trip": True,
        "governed_save_round_trip": True,
        "legacy_v5_hash_unchanged": old_hash,
        "legacy_v4_step_unchanged": True,
        "v6_runtime_wired": True,
        "mixed_or_unwired_paths_rejected": 7,
    }


def check_schemas():
    opening, closing, safety_events, diagnostic, step_payload = contract_fixtures()
    _, _, _, governed = governed_scene_fixture()
    save = GovernedSceneSave(governed.to_dict())
    policy = GovernedPropulsionExecutionPolicy()
    samples = (
        policy.to_dict(),
        opening.to_dict(),
        closing.to_dict(),
        safety_events[0].to_dict(),
        diagnostic.to_dict(),
        governed.to_dict(),
        save.to_dict(),
        step_payload,
    )
    ids = {
        load_json(path)["$id"]: load_json(path)
        for path in (ROOT / "舰艇数据/模式").glob("*.schema.json")
    }
    references = 0

    def visit(value):
        nonlocal references
        if isinstance(value, dict):
            if "$ref" in value:
                target, _, pointer = value["$ref"].partition("#")
                assert target in ids, target
                node = ids[target]
                if pointer:
                    for part in pointer.lstrip("/").split("/"):
                        node = node[part.replace("~1", "/").replace("~0", "~")]
                references += 1
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    for relative, sample in zip(SCHEMAS, samples):
        schema = load_json(ROOT / relative)
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(sample)
        assert set(sample) <= set(schema["properties"])
        visit(schema)
    return {"schemas": len(SCHEMAS), "references_checked": references, "strict_top_level_samples": len(samples)}


def collect_evidence():
    return {
        "contracts": check_round_trips_and_negative_contracts(),
        "scene_and_legacy_gates": check_v6_state_save_and_legacy_gates(),
        "schemas": check_schemas(),
    }


def main():
    evidence = collect_evidence()
    if REPORT.exists():
        report = load_json(REPORT)
        assert report["status"] == "PASS" and report["evidence"] == evidence
        from benchmarks.t0.metadata import file_sha256
        for path, expected in report["implementation_hashes"].items():
            assert file_sha256(ROOT / path) == expected, path
    print(json.dumps({
        "status": "PASS",
        "interface": "gaotian.stage-t0b2d3c-governed-scene-contracts/v1",
        "evidence": evidence,
    }, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
