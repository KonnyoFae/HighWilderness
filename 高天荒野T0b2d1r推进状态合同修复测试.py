"""T0b.2d1r：推进状态 interface 修复与 c2b→d1 具名迁移回归。"""

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
from 高天荒野舰艇数据契约 import ContractError, ModuleCapability, canonical_sha256, load_json
from 高天荒野舰艇推进安全判定器 import load_propulsion_safety_profile
from 高天荒野舰艇推进时间内核 import (
    PropulsionTimeCommand,
    advance_propulsion_time_boundary,
)
from 高天荒野舰艇推进状态合同 import (
    C2B_ENGINE_RUNTIME_STATE_INTERFACE_ID,
    C2B_TACTICAL_PROPULSION_STATE_INTERFACE_ID,
    ENGINE_RUNTIME_STATE_INTERFACE_ID,
    TACTICAL_PROPULSION_STATE_INTERFACE_ID,
    EngineRuntimeState,
    TacticalPropulsionState,
    migrate_tactical_propulsion_state_c2b_to_d1,
)
from 高天荒野舰艇统一战术场景 import (
    C2B_TACTICAL_PROPULSION_SCENE_INTERFACE_ID,
    C2B_TACTICAL_PROPULSION_SCENE_POLICY_ID,
    KNOWN_TACTICAL_SCENE_PROPULSION_V2_TO_D1_V3_MIGRATIONS,
    TACTICAL_PROPULSION_SCENE_INTERFACE_ID,
    TACTICAL_PROPULSION_SCENE_POLICY_ID,
    TacticalSceneState,
    advance_tactical_scene_step,
    migrate_known_tactical_scene_propulsion_v2_to_d1_v3,
    migrate_known_tactical_scene_v1_to_propulsion_v2,
)


ROOT = Path(__file__).resolve().parent
PLAN_PATH = ROOT / "contracts" / "web_bridge" / "t0-benchmark-plan.v1.json"
GOLDEN_PATH = ROOT / "contracts" / "web_bridge" / "t0-authority-step-golden.v1.json"
PROFILE_PATH = ROOT / "舰艇数据" / "标定" / "T0推进安全技术替身配置.v1.json"
C2B_STATE_SCHEMA_PATH = (
    ROOT / "舰艇数据" / "模式" / "高天荒野舰艇推进状态契约.v1alpha1.schema.json"
)
D1_STATE_SCHEMA_PATH = (
    ROOT / "舰艇数据" / "模式" / "高天荒野舰艇推进状态契约.v2alpha1.schema.json"
)
C2B_SCENE_SCHEMA_PATH = (
    ROOT / "舰艇数据" / "模式" / "高天荒野舰艇统一战术场景状态契约.v2alpha1.schema.json"
)
D1_SCENE_SCHEMA_PATH = (
    ROOT / "舰艇数据" / "模式" / "高天荒野舰艇统一战术场景状态契约.v3alpha1.schema.json"
)
REPORT_PATH = ROOT / "舰艇数据" / "报告" / "阶段T0b2d1r推进状态合同修复接口.v1.json"


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
    tuple[str, T0ScenarioBundle, TacticalSceneState, TacticalSceneState], ...
]:
    plan = load_benchmark_plan(PLAN_PATH)
    profile = load_propulsion_safety_profile(PROFILE_PATH)
    cases: list[
        tuple[str, T0ScenarioBundle, TacticalSceneState, TacticalSceneState]
    ] = []
    for profile_item in plan.profiles:
        for load_stage in plan.load_stages:
            migration_id = f"{profile_item.id}.{load_stage}"
            bundle = build_scenario(ROOT, plan, profile_item.id, load_stage, 1)
            c2b = migrate_known_tactical_scene_v1_to_propulsion_v2(
                migration_id,
                bundle.initial_scene,
                bundle.bindings,
                profile,
            )
            d1 = migrate_known_tactical_scene_propulsion_v2_to_d1_v3(
                migration_id,
                c2b,
            )
            cases.append((migration_id, bundle, c2b, d1))
    return tuple(sorted(cases, key=lambda item: item[0]))


def _main_capability() -> ModuleCapability:
    return ModuleCapability.parse(
        {
            "fuel_units_per_s": 1.0,
            "kind": "main_engine",
            "local_thrust_axis": "+Y",
            "response_time_s": 1.0,
            "startup_time_s": 1.0,
            "thrust_n": 1000.0,
        },
        "$.capability",
        propulsion_capability_version=2,
    )


def test_contract_versioning_and_strict_migration() -> dict[str, object]:
    c2b_state_schema = load_json(C2B_STATE_SCHEMA_PATH)
    d1_state_schema = load_json(D1_STATE_SCHEMA_PATH)
    c2b_scene_schema = load_json(C2B_SCENE_SCHEMA_PATH)
    d1_scene_schema = load_json(D1_SCENE_SCHEMA_PATH)
    assert c2b_state_schema["$id"] == C2B_TACTICAL_PROPULSION_STATE_INTERFACE_ID
    assert d1_state_schema["$id"] == TACTICAL_PROPULSION_STATE_INTERFACE_ID
    assert c2b_scene_schema["$id"] == C2B_TACTICAL_PROPULSION_SCENE_INTERFACE_ID
    assert d1_scene_schema["$id"] == TACTICAL_PROPULSION_SCENE_INTERFACE_ID
    assert c2b_scene_schema["properties"]["policy"]["const"] == (
        C2B_TACTICAL_PROPULSION_SCENE_POLICY_ID
    )
    assert d1_scene_schema["properties"]["policy"]["const"] == (
        TACTICAL_PROPULSION_SCENE_POLICY_ID
    )

    _, _, c2b_scene, d1_scene = migrated_cases()[0]
    c2b_state = c2b_scene.ships[0].propulsion_state
    d1_state = d1_scene.ships[0].propulsion_state
    assert c2b_state is not None and d1_state is not None
    c2b_value = c2b_state.to_dict()
    d1_value = d1_state.to_dict()
    assert TacticalPropulsionState.parse(c2b_value, "$.c2b") == c2b_state
    assert TacticalPropulsionState.parse(d1_value, "$.d1") == d1_state
    assert {
        "response_start_output_percent",
        "response_started_at_fixed_step",
    }.isdisjoint(c2b_value["engines"][0])
    assert {
        "response_start_output_percent",
        "response_started_at_fixed_step",
    }.issubset(d1_value["engines"][0])

    c2b_engine = deepcopy(c2b_value["engines"][0])
    c2b_with_d1_fields = deepcopy(c2b_engine)
    c2b_with_d1_fields["response_start_output_percent"] = None
    c2b_with_d1_fields["response_started_at_fixed_step"] = None
    require_contract_error(
        "object.keys",
        lambda: EngineRuntimeState.parse(c2b_with_d1_fields, "$.c2b_extra"),
    )
    d1_missing_anchor = deepcopy(d1_value["engines"][0])
    d1_missing_anchor.pop("response_started_at_fixed_step")
    require_contract_error(
        "object.keys",
        lambda: EngineRuntimeState.parse(d1_missing_anchor, "$.d1_missing"),
    )
    d1_parent_with_c2b_engines = deepcopy(c2b_value)
    d1_parent_with_c2b_engines["interface"] = TACTICAL_PROPULSION_STATE_INTERFACE_ID
    require_contract_error(
        "propulsion_state.collection_invariant",
        lambda: TacticalPropulsionState.parse(d1_parent_with_c2b_engines, "$.mixed"),
    )
    require_contract_error(
        "propulsion_state.engine_invariant",
        lambda: EngineRuntimeState.parse(
            {
                **c2b_engine,
                "actual_output_percent": 0,
                "next_transition_step": 5,
                "phase": "running",
                "ready_at_fixed_step": 0,
                "target_output_percent": 25,
            },
            "$.c2b_zero_running",
        ),
    )

    first_c2b_engine = c2b_state.engines[0]
    require_contract_error(
        "propulsion_time.state_interface",
        lambda: advance_propulsion_time_boundary(
            first_c2b_engine,
            _main_capability(),
            0,
            PropulsionTimeCommand.main_engine("stop"),
        ),
    )
    running = EngineRuntimeState.parse(
        {
            **c2b_engine,
            "actual_output_percent": 20,
            "next_transition_step": 5,
            "phase": "running",
            "ready_at_fixed_step": 0,
            "target_output_percent": 25,
        },
        "$.c2b_running",
    )
    ambiguous = replace(
        c2b_state,
        engines=(running, *c2b_state.engines[1:]),
    )
    require_contract_error(
        "propulsion_state.c2b_migration_ambiguous_schedule",
        lambda: migrate_tactical_propulsion_state_c2b_to_d1(ambiguous),
    )
    return {
        "c2b_engine_interface": C2B_ENGINE_RUNTIME_STATE_INTERFACE_ID,
        "c2b_scene_interface": C2B_TACTICAL_PROPULSION_SCENE_INTERFACE_ID,
        "c2b_state_interface": C2B_TACTICAL_PROPULSION_STATE_INTERFACE_ID,
        "d1_engine_interface": ENGINE_RUNTIME_STATE_INTERFACE_ID,
        "d1_scene_interface": TACTICAL_PROPULSION_SCENE_INTERFACE_ID,
        "d1_state_interface": TACTICAL_PROPULSION_STATE_INTERFACE_ID,
        "strict_cross_version_negative_cases": 6,
    }


def test_named_scene_migrations() -> dict[str, object]:
    specifications = {
        item.migration_id: item
        for item in KNOWN_TACTICAL_SCENE_PROPULSION_V2_TO_D1_V3_MIGRATIONS
    }
    expected_cases = load_authority_step_golden(GOLDEN_PATH)["cases"]
    assert set(specifications) == set(expected_cases)
    target_hashes: dict[str, str] = {}
    engine_states = 0
    governor_states = 0
    for migration_id, _, c2b, d1 in migrated_cases():
        assert canonical_sha256(c2b) == specifications[migration_id].source_scene_sha256
        runs = tuple(
            migrate_known_tactical_scene_propulsion_v2_to_d1_v3(
                migration_id,
                c2b,
            )
            for _ in range(3)
        )
        hashes = tuple(canonical_sha256(item) for item in runs)
        assert len(set(hashes)) == 1
        assert runs[0] == d1
        value = d1.to_dict()
        assert value["interface"] == TACTICAL_PROPULSION_SCENE_INTERFACE_ID
        assert value["policy"] == TACTICAL_PROPULSION_SCENE_POLICY_ID
        assert TacticalSceneState.parse(value) == d1
        target_hashes[migration_id] = hashes[0]
        for ship in d1.ships:
            assert ship.propulsion_state is not None
            engine_states += len(ship.propulsion_state.engines)
            governor_states += len(ship.propulsion_state.governors)
            assert all(
                engine.interface_id == ENGINE_RUNTIME_STATE_INTERFACE_ID
                for engine in ship.propulsion_state.engines
            )
            assert all(
                engine.response_started_at_fixed_step is None
                and engine.response_start_output_percent is None
                for engine in ship.propulsion_state.engines
            )

    migration_id, _, c2b, d1 = migrated_cases()[0]
    require_contract_error(
        "tactical_scene.d1_migration_unknown",
        lambda: migrate_known_tactical_scene_propulsion_v2_to_d1_v3(
            "unknown.scene",
            c2b,
        ),
    )
    tampered = replace(c2b, ships=tuple(reversed(c2b.ships)))
    require_contract_error(
        "tactical_scene.d1_migration_source_hash",
        lambda: migrate_known_tactical_scene_propulsion_v2_to_d1_v3(
            migration_id,
            tampered,
        ),
    )
    require_contract_error(
        "tactical_scene.d1_migration_source_interface",
        lambda: migrate_known_tactical_scene_propulsion_v2_to_d1_v3(
            migration_id,
            d1,
        ),
    )
    return {
        "deterministic_replays_per_scene": 3,
        "engine_states_migrated": engine_states,
        "governor_states_preserved": governor_states,
        "scenes_migrated": len(target_hashes),
        "target_hashes": target_hashes,
        "unknown_tampered_and_repeat_rejected": 3,
    }


def test_scene_isolation_and_legacy_golden() -> dict[str, object]:
    _, bundle, c2b, d1 = migrated_cases()[0]
    wrong_policy = d1.to_dict()
    wrong_policy["policy"] = C2B_TACTICAL_PROPULSION_SCENE_POLICY_ID
    require_contract_error(
        "tactical_scene.interface",
        lambda: TacticalSceneState.parse(wrong_policy),
    )
    wrong_parent = d1.to_dict()
    wrong_parent["interface"] = C2B_TACTICAL_PROPULSION_SCENE_INTERFACE_ID
    wrong_parent["policy"] = C2B_TACTICAL_PROPULSION_SCENE_POLICY_ID
    require_contract_error(
        "tactical_scene.propulsion_state_interface",
        lambda: TacticalSceneState.parse(wrong_parent),
    )
    mixed = replace(
        d1,
        ships=(
            replace(d1.ships[0], propulsion_state=c2b.ships[0].propulsion_state),
            *d1.ships[1:],
        ),
    )
    try:
        mixed.to_dict()
    except ValueError as error:
        assert "interface" in str(error)
    else:
        raise AssertionError("预期拒绝混用 c2b/d1 状态的场景")
    require_contract_error(
        "tactical_scene.propulsion_unwired",
        lambda: advance_tactical_scene_step(
            d1,
            bundle.bindings,
            bundle.timing_catalog,
            bundle.projectile_catalog,
            bundle.material_registry,
        ),
    )
    assert verify_authority_step_golden(
        ROOT,
        load_benchmark_plan(PLAN_PATH),
        GOLDEN_PATH,
    )["cases"] == load_authority_step_golden(GOLDEN_PATH)["cases"]
    return {
        "authority_golden": "12_of_12_PASS",
        "new_scene_fixed_steps_advanced": 0,
        "propulsion_mechanics_wired": False,
        "strict_scene_negative_cases": 4,
    }


def main() -> None:
    contract_evidence = test_contract_versioning_and_strict_migration()
    migration_evidence = test_named_scene_migrations()
    isolation_evidence = test_scene_isolation_and_legacy_golden()
    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    assert report["interface"] == "gaotian.stage-t0b2d1r-propulsion-state-contract-repair/v1"
    assert report["status"] == "PASS"
    assert report["contract_evidence"] == contract_evidence
    assert report["migration_evidence"] == migration_evidence
    assert report["isolation_evidence"] == isolation_evidence
    assert report["official_performance_runs_executed"] == 0
    assert report["next_slice"] == "T0b.2d2a_propulsion_resource_and_control_bridge"
    for relative_path, expected_hash in report["implementation_hashes"].items():
        assert expected_hash == file_sha256(ROOT / relative_path)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
