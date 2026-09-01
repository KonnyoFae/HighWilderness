"""T0b.2c2b：推进场景状态、事件合同与具名旧场景迁移回归。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from functools import lru_cache
import json
from pathlib import Path
from typing import Callable

from benchmarks.t0.contracts import load_benchmark_plan
from benchmarks.t0.diagnostics import (
    load_authority_step_golden,
    verify_authority_step_golden,
)
from benchmarks.t0.metadata import file_sha256
from benchmarks.t0.scenario import T0ScenarioBundle, build_scenario
from 高天荒野舰艇数据契约 import (
    ContractError,
    canonical_json,
    canonical_sha256,
    load_json,
)
from 高天荒野舰艇推进安全判定器 import load_propulsion_safety_profile
from 高天荒野舰艇推进状态合同 import (
    ENGINE_PHASES,
    ENGINE_RUNTIME_STATE_INTERFACE_ID,
    PROPULSION_EVENT_KIND_ORDER,
    PROPULSION_STATE_EVENT_INTERFACE_ID,
    TACTICAL_PROPULSION_STATE_INTERFACE_ID,
    EngineRuntimeState,
    PropulsionStateEvent,
    TacticalPropulsionState,
    migrate_engine_runtime_state_from_module_mode,
)
from 高天荒野舰艇统一战术场景 import (
    KNOWN_TACTICAL_SCENE_V1_TO_PROPULSION_V2_MIGRATIONS,
    TACTICAL_PROPULSION_SCENE_INTERFACE_ID,
    TACTICAL_PROPULSION_SCENE_POLICY_ID,
    TACTICAL_SCENE_INTERFACE_ID,
    TacticalSceneState,
    advance_tactical_scene_step,
    migrate_known_tactical_scene_v1_to_propulsion_v2,
    validate_tactical_scene_propulsion_profile,
)


ROOT = Path(__file__).resolve().parent
PLAN_PATH = ROOT / "contracts" / "web_bridge" / "t0-benchmark-plan.v1.json"
GOLDEN_PATH = ROOT / "contracts" / "web_bridge" / "t0-authority-step-golden.v1.json"
PROFILE_PATH = ROOT / "舰艇数据" / "标定" / "T0推进安全技术替身配置.v1.json"
OLD_SCENE_SCHEMA_PATH = (
    ROOT / "舰艇数据" / "模式" / "高天荒野舰艇统一战术场景状态契约.v1alpha1.schema.json"
)
NEW_SCENE_SCHEMA_PATH = (
    ROOT / "舰艇数据" / "模式" / "高天荒野舰艇统一战术场景状态契约.v2alpha1.schema.json"
)
PROPULSION_STATE_SCHEMA_PATH = (
    ROOT / "舰艇数据" / "模式" / "高天荒野舰艇推进状态契约.v1alpha1.schema.json"
)
PROPULSION_EVENT_SCHEMA_PATH = (
    ROOT / "舰艇数据" / "模式" / "高天荒野舰艇推进事件契约.v1alpha1.schema.json"
)
REPORT_PATH = ROOT / "舰艇数据" / "报告" / "阶段T0b2c2b推进场景状态与迁移接口.v1.json"


def require_contract_error(code: str, action: Callable[[], object]) -> None:
    try:
        action()
    except ContractError as error:
        if error.code != code:
            raise AssertionError(f"预期错误 {code}，实际为 {error.code}: {error}") from error
    else:
        raise AssertionError(f"预期抛出 {code}")


@lru_cache(maxsize=1)
def migrated_cases() -> tuple[
    tuple[str, T0ScenarioBundle, TacticalSceneState], ...
]:
    plan = load_benchmark_plan(PLAN_PATH)
    profile = load_propulsion_safety_profile(PROFILE_PATH)
    cases: list[tuple[str, T0ScenarioBundle, TacticalSceneState]] = []
    for profile_item in plan.profiles:
        for load_stage in plan.load_stages:
            migration_id = f"{profile_item.id}.{load_stage}"
            bundle = build_scenario(
                ROOT,
                plan,
                profile_item.id,
                load_stage,
                1,
            )
            migrated = migrate_known_tactical_scene_v1_to_propulsion_v2(
                migration_id,
                bundle.initial_scene,
                bundle.bindings,
                profile,
            )
            cases.append((migration_id, bundle, migrated))
    return tuple(sorted(cases, key=lambda item: item[0]))


def _first_propulsion_value() -> dict[str, object]:
    return deepcopy(migrated_cases()[0][2].ships[0].propulsion_state.to_dict())  # type: ignore[union-attr]


def _first_main_engine(value: dict[str, object]) -> dict[str, object]:
    return next(
        item
        for item in value["engines"]  # type: ignore[index]
        if item["actuator_category"] == "main_engine"
    )


def test_propulsion_state_contracts() -> dict[str, object]:
    state_schema = load_json(PROPULSION_STATE_SCHEMA_PATH)
    scene_schema = load_json(NEW_SCENE_SCHEMA_PATH)
    assert state_schema["$id"] == TACTICAL_PROPULSION_STATE_INTERFACE_ID
    assert state_schema["additionalProperties"] is False
    assert scene_schema["$id"] == TACTICAL_PROPULSION_SCENE_INTERFACE_ID
    assert scene_schema["properties"]["policy"]["const"] == (
        TACTICAL_PROPULSION_SCENE_POLICY_ID
    )
    assert "propulsion_state" not in OLD_SCENE_SCHEMA_PATH.read_text(encoding="utf-8")

    source = _first_propulsion_value()
    parsed = TacticalPropulsionState.parse(source, "$.propulsion")
    assert parsed.to_dict() == source
    assert parsed.source_sha256 == canonical_sha256(parsed)

    main_source = deepcopy(_first_main_engine(source))
    phase_examples = {
        "starting": (25, 0, 5, 5),
        "ready": (0, 0, 0, None),
        "running": (25, 20, 0, 5),
        "stopping": (0, 20, 0, 5),
        "tripped": (0, 0, None, None),
    }
    for phase, values in phase_examples.items():
        candidate = deepcopy(main_source)
        candidate["phase"] = phase
        candidate["target_output_percent"] = values[0]
        candidate["actual_output_percent"] = values[1]
        candidate["ready_at_fixed_step"] = values[2]
        candidate["next_transition_step"] = values[3]
        assert EngineRuntimeState.parse(candidate, f"$.phase.{phase}").phase == phase
    assert set(phase_examples) | {"off"} == set(ENGINE_PHASES)
    assert tuple(
        migrate_engine_runtime_state_from_module_mode(
            f"engine.mode.{mode}",
            "main_engine",
            mode,
            7,
        ).phase
        for mode in ("active", "standby", "off")
    ) == ("ready", "off", "off")
    require_contract_error(
        "propulsion_state.module_operating_mode",
        lambda: migrate_engine_runtime_state_from_module_mode(
            "engine.mode.invalid",
            "main_engine",
            "warm",
            7,
        ),
    )

    invalid: list[tuple[str, dict[str, object]]] = []
    extra = deepcopy(source)
    extra["implicit_default"] = True
    invalid.append(("object.keys", extra))
    no_engines = deepcopy(source)
    no_engines["engines"] = []
    invalid.append(("propulsion_state.collection_invariant", no_engines))
    reversed_engines = deepcopy(source)
    reversed_engines["engines"] = list(reversed(reversed_engines["engines"]))  # type: ignore[arg-type]
    invalid.append(("propulsion_state.collection_invariant", reversed_engines))
    duplicate_engine = deepcopy(source)
    duplicate_engine["engines"][1] = deepcopy(duplicate_engine["engines"][0])  # type: ignore[index]
    invalid.append(("propulsion_state.collection_invariant", duplicate_engine))
    reversed_governors = deepcopy(source)
    reversed_governors["governors"] = list(reversed(reversed_governors["governors"]))  # type: ignore[arg-type]
    invalid.append(("propulsion_state.collection_invariant", reversed_governors))
    bad_channel = deepcopy(source)
    bad_channel["governors"][0]["command_channel"] = "turn"  # type: ignore[index]
    invalid.append(("propulsion_state.command_channel", bad_channel))
    limited_without_reason = deepcopy(source)
    limited_without_reason["governors"][0]["safety_ceiling_percent"] = 75  # type: ignore[index]
    invalid.append(("propulsion_state.governor_invariant", limited_without_reason))
    bad_reason_order = deepcopy(source)
    bad_reason_order["governors"][0]["safety_ceiling_percent"] = 75  # type: ignore[index]
    bad_reason_order["governors"][0]["safety_reasons"] = [  # type: ignore[index]
        "crew_limit",
        "structure_limit",
    ]
    bad_reason_order["governors"][0]["safety_limited_since_step"] = 0  # type: ignore[index]
    invalid.append(("propulsion_state.reason_order", bad_reason_order))
    bad_phase = deepcopy(source)
    _first_main_engine(bad_phase)["phase"] = "warm"
    invalid.append(("propulsion_state.engine_phase", bad_phase))
    missing_notch = deepcopy(source)
    _first_main_engine(missing_notch)["commanded_notch"] = None
    invalid.append(("propulsion_state.engine_invariant", missing_notch))
    invalid_stage = deepcopy(source)
    _first_main_engine(invalid_stage)["actual_output_percent"] = 3
    invalid.append(("propulsion_state.output_stage", invalid_stage))
    off_with_output = deepcopy(source)
    _first_main_engine(off_with_output)["actual_output_percent"] = 2
    invalid.append(("propulsion_state.engine_invariant", off_with_output))
    for code, candidate in invalid:
        require_contract_error(
            code,
            lambda candidate=candidate: TacticalPropulsionState.parse(
                candidate,
                "$.invalid",
            ),
        )
    return {
        "engine_phases": len(ENGINE_PHASES),
        "governor_channels": 4,
        "state_sha256": parsed.source_sha256,
        "module_mode_mappings": 3,
        "strict_negative_cases": len(invalid) + 1,
        "v1_schema_unchanged": True,
    }


def test_propulsion_event_contract() -> dict[str, object]:
    schema = load_json(PROPULSION_EVENT_SCHEMA_PATH)
    assert schema["$id"] == PROPULSION_STATE_EVENT_INTERFACE_ID
    assert schema["additionalProperties"] is False
    events = (
        PropulsionStateEvent(
            1,
            "engine.1",
            "forward",
            "engine_start_requested",
            "off",
            "starting",
            None,
            None,
            (),
        ),
        PropulsionStateEvent(
            1,
            "engine.1",
            "forward",
            "engine_safety_limit_engaged",
            None,
            None,
            100,
            75,
            ("structure_limit",),
        ),
        PropulsionStateEvent(
            1,
            "engine.1",
            None,
            "engine_output_stage_changed",
            None,
            None,
            20,
            25,
            (),
        ),
        PropulsionStateEvent(
            1,
            "engine.1",
            None,
            "engine_tripped",
            "running",
            "tripped",
            20,
            0,
            ("fuel_unavailable",),
        ),
    )
    for event in events:
        assert PropulsionStateEvent.parse(event.to_dict(), "$.event") == event
    assert tuple(item.sort_key for item in sorted(events, key=lambda item: item.sort_key)) == tuple(
        sorted(item.sort_key for item in events)
    )

    source = events[2].to_dict()
    invalid: list[tuple[str, dict[str, object]]] = []
    extra = deepcopy(source)
    extra["sequence"] = 1
    invalid.append(("object.keys", extra))
    unknown = deepcopy(source)
    unknown["kind"] = "engine_unknown"
    invalid.append(("propulsion_event.kind", unknown))
    same_stage = deepcopy(source)
    same_stage["resulting_stage_percent"] = same_stage["previous_stage_percent"]
    invalid.append(("propulsion_event.invariant", same_stage))
    bad_stage = deepcopy(source)
    bad_stage["resulting_stage_percent"] = 3
    invalid.append(("propulsion_state.output_stage", bad_stage))
    safety_without_channel = events[1].to_dict()
    safety_without_channel["command_channel"] = None
    invalid.append(("propulsion_event.invariant", safety_without_channel))
    bad_reasons = events[3].to_dict()
    bad_reasons["reasons"] = ["engine_tripped", "fuel_unavailable"]
    invalid.append(("propulsion_state.reason_order", bad_reasons))
    for code, candidate in invalid:
        require_contract_error(
            code,
            lambda candidate=candidate: PropulsionStateEvent.parse(
                candidate,
                "$.invalid_event",
            ),
        )
    return {
        "event_kinds": len(PROPULSION_EVENT_KIND_ORDER),
        "roundtrip_examples": len(events),
        "strict_negative_cases": len(invalid),
    }


def test_named_scene_migrations() -> dict[str, object]:
    profile = load_propulsion_safety_profile(PROFILE_PATH)
    expected_golden = load_authority_step_golden(GOLDEN_PATH)
    specifications = {
        item.migration_id: item
        for item in KNOWN_TACTICAL_SCENE_V1_TO_PROPULSION_V2_MIGRATIONS
    }
    assert set(specifications) == set(expected_golden["cases"])
    target_hashes: dict[str, str] = {}
    ready_engines = 0
    off_engines = 0
    engine_states = 0
    governor_states = 0
    for migration_id, bundle, migrated in migrated_cases():
        source = bundle.initial_scene
        assert canonical_sha256(source) == specifications[migration_id].source_scene_sha256
        assert canonical_sha256(source) == expected_golden["cases"][migration_id]["initial_scene_sha256"]
        assert source.to_dict()["interface"] == TACTICAL_SCENE_INTERFACE_ID
        assert "propulsion_safety_profile" not in source.to_dict()
        runs = tuple(
            migrate_known_tactical_scene_v1_to_propulsion_v2(
                migration_id,
                source,
                bundle.bindings,
                profile,
            )
            for _ in range(3)
        )
        hashes = tuple(canonical_sha256(item) for item in runs)
        assert len(set(hashes)) == 1
        assert runs[0] == migrated
        value = json.loads(canonical_json(migrated))
        assert value["interface"] == TACTICAL_PROPULSION_SCENE_INTERFACE_ID
        assert value["policy"] == TACTICAL_PROPULSION_SCENE_POLICY_ID
        assert TacticalSceneState.parse(value) == migrated
        validate_tactical_scene_propulsion_profile(migrated, profile)
        target_hashes[migration_id] = hashes[0]
        for ship in migrated.ships:
            assert ship.propulsion_state is not None
            engine_states += len(ship.propulsion_state.engines)
            governor_states += len(ship.propulsion_state.governors)
            for engine in ship.propulsion_state.engines:
                ready_engines += engine.phase == "ready"
                off_engines += engine.phase == "off"

    migration_id, bundle, _ = migrated_cases()[0]
    require_contract_error(
        "tactical_scene.propulsion_migration_unknown",
        lambda: migrate_known_tactical_scene_v1_to_propulsion_v2(
            "unknown.scene",
            bundle.initial_scene,
            bundle.bindings,
            profile,
        ),
    )
    tampered = replace(
        bundle.initial_scene,
        ships=tuple(reversed(bundle.initial_scene.ships)),
    )
    require_contract_error(
        "tactical_scene.propulsion_migration_source_hash",
        lambda: migrate_known_tactical_scene_v1_to_propulsion_v2(
            migration_id,
            tampered,
            bundle.bindings,
            profile,
        ),
    )
    wrong_profile = replace(profile, id="gtw.propulsion_safety.fixture.other")
    require_contract_error(
        "tactical_scene.propulsion_migration_profile",
        lambda: migrate_known_tactical_scene_v1_to_propulsion_v2(
            migration_id,
            bundle.initial_scene,
            bundle.bindings,
            wrong_profile,
        ),
    )
    return {
        "deterministic_replays_per_scene": 3,
        "engine_states_migrated": engine_states,
        "governor_states_migrated": governor_states,
        "off_engines": off_engines,
        "ready_engines": ready_engines,
        "scenes_migrated": len(target_hashes),
        "target_hashes": target_hashes,
        "unknown_tampered_and_profile_mismatch_rejected": 3,
    }


def test_scene_strictness_and_isolation() -> dict[str, object]:
    migration_id, bundle, migrated = migrated_cases()[0]
    value = migrated.to_dict()
    missing_profile = deepcopy(value)
    missing_profile.pop("propulsion_safety_profile")
    require_contract_error(
        "object.keys",
        lambda: TacticalSceneState.parse(missing_profile),
    )
    missing_ship_state = deepcopy(value)
    missing_ship_state["ships"][0].pop("propulsion_state")
    require_contract_error(
        "object.keys",
        lambda: TacticalSceneState.parse(missing_ship_state),
    )
    wrong_policy = deepcopy(value)
    wrong_policy["policy"] = "gaotian.tactical-scene/wrong"
    require_contract_error(
        "tactical_scene.interface",
        lambda: TacticalSceneState.parse(wrong_policy),
    )
    bad_hash = deepcopy(value)
    bad_hash["propulsion_safety_profile_sha256"] = "0" * 63
    require_contract_error(
        "value.sha256",
        lambda: TacticalSceneState.parse(bad_hash),
    )
    old_with_new_field = bundle.initial_scene.to_dict()
    old_with_new_field["propulsion_safety_profile"] = value[
        "propulsion_safety_profile"
    ]
    require_contract_error(
        "object.keys",
        lambda: TacticalSceneState.parse(old_with_new_field),
    )
    require_contract_error(
        "tactical_scene.propulsion_unwired",
        lambda: advance_tactical_scene_step(
            migrated,
            bundle.bindings,
            bundle.timing_catalog,
            bundle.projectile_catalog,
            bundle.material_registry,
        ),
    )
    assert canonical_sha256(bundle.initial_scene) == (
        load_authority_step_golden(GOLDEN_PATH)["cases"][migration_id][
            "initial_scene_sha256"
        ]
    )
    assert verify_authority_step_golden(
        ROOT,
        load_benchmark_plan(PLAN_PATH),
        GOLDEN_PATH,
    )["cases"] == load_authority_step_golden(GOLDEN_PATH)["cases"]
    return {
        "authority_golden": "12_of_12_PASS",
        "new_scene_fixed_steps_advanced": 0,
        "strict_scene_negative_cases": 5,
        "unwired_advance_rejected": True,
    }


def main() -> None:
    state_evidence = test_propulsion_state_contracts()
    event_evidence = test_propulsion_event_contract()
    migration_evidence = test_named_scene_migrations()
    isolation_evidence = test_scene_strictness_and_isolation()

    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    assert report["interface"] == "gaotian.stage-t0b2c2b-propulsion-scene-state/v1"
    assert report["status"] == "PASS"
    assert report["state_evidence"] == state_evidence
    assert report["event_evidence"] == event_evidence
    assert report["migration_evidence"] == migration_evidence
    assert report["isolation_evidence"] == isolation_evidence
    assert report["authority_golden"] == "12_of_12_PASS"
    assert report["official_performance_runs_executed"] == 0
    assert report["next_slice"] == "T0b.2d1_propulsion_time_kernel"
    for relative_path in (
        "舰艇数据/模式/高天荒野舰艇推进状态契约.v1alpha1.schema.json",
        "舰艇数据/模式/高天荒野舰艇推进事件契约.v1alpha1.schema.json",
        "舰艇数据/模式/高天荒野舰艇统一战术场景状态契约.v2alpha1.schema.json",
        "高天荒野T0b2c2b推进场景状态与迁移测试.py",
        "高天荒野T0b2推进响应与权威性能优化规划.md",
        "高天荒野Web客户端编辑器与战术验证实施计划.md",
        "高天荒野舰艇推进状态合同.py",
        "高天荒野舰艇统一战术场景.py",
    ):
        assert report["implementation_hashes"][relative_path] == file_sha256(
            ROOT / relative_path
        )

    print(
        json.dumps(
            {
                "authority_golden": "12_of_12_PASS",
                "interface": "gaotian.stage-t0b2c2b-propulsion-scene-state-test/v1",
                "scenes_migrated": migration_evidence["scenes_migrated"],
                "status": "PASS",
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
