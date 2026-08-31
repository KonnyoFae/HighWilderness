"""T0b：确定性 6/20/30 舰场景、合法装载与无界面基线回归。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from 高天荒野舰艇数据契约 import ContractError, canonical_json, canonical_sha256
from 高天荒野舰艇武器时间与射击队列 import load_weapon_timing_profile_catalog

from benchmarks.t0.contracts import BenchmarkContractError, load_benchmark_plan
from benchmarks.t0.headless import run_headless_baseline
from benchmarks.t0.matrix import expand_matrix
from benchmarks.t0.metadata import file_sha256
from benchmarks.t0.scenario import (
    TIMING_RELATIVE,
    advance_scenario_step,
    build_scenario,
    scene_entity_counts,
)


ROOT = Path(__file__).resolve().parent
PLAN_PATH = ROOT / "contracts" / "web_bridge" / "t0-benchmark-plan.v1.json"
RESULT_SCHEMA_PATH = ROOT / "contracts" / "web_bridge" / "t0-benchmark-result.v1.schema.json"
REPORT_PATH = ROOT / "舰艇数据" / "报告" / "阶段T0b确定性场景与无界面基线接口.v1.json"


def require_error(code: str, action: Callable[[], object]) -> None:
    try:
        action()
    except (BenchmarkContractError, ContractError) as error:
        if error.code != code:
            raise AssertionError(f"预期 {code}，实际 {error.code}: {error}") from error
    else:
        raise AssertionError(f"预期抛出 {code}")


def main() -> None:
    plan = load_benchmark_plan(PLAN_PATH)
    timing = load_weapon_timing_profile_catalog(ROOT / TIMING_RELATIVE)
    assert timing.fixture_level == "contract_fixture"
    assert {item.prototype.id for item in timing.profiles} == {
        "gtw.module.fixture.weapon",
        "gtw.module.fixture.unmanned.weapon",
    }
    assert canonical_json(timing) == (ROOT / TIMING_RELATIVE).read_text(encoding="utf-8")

    expected = {
        "functional_6": {"ships": 6, "combat": 4, "ordinary": 120, "guided": 12},
        "target_20": {"ships": 20, "combat": 14, "ordinary": 600, "guided": 60},
        "stress_30": {"ships": 30, "combat": 20, "ordinary": 1500, "guided": 150},
    }
    bundles = {}
    manifests = {}
    for profile_id, targets in expected.items():
        for stage in plan.load_stages:
            bundle = build_scenario(ROOT, plan, profile_id, stage, 1)
            bundles[(profile_id, stage)] = bundle
            manifest = bundle.to_manifest()
            manifests[(profile_id, stage)] = manifest
            counts = manifest["actual_initial_entity_counts"]
            assert counts["active_ships"] == targets["ships"]
            assert counts["ammunition_units"] == targets["combat"] * 101
            assert counts["weapon_sequences"] == (
                0 if stage == "motion_only" else targets["combat"]
            )
            assert counts["ordinary_projectiles"] == (
                0 if stage == "motion_only" else targets["ordinary"]
            )
            assert counts["guided_projectiles"] == (
                targets["guided"]
                if stage in {"guided_projectiles", "scripted_damage_and_recompile"}
                else 0
            )
            assert len(bundle.fixture_resource_hashes) == 24
            assert "舰艇数据/标定/T0基准武器时间技术替身配置.v1.json" in bundle.fixture_resource_hashes

    matrix = expand_matrix(plan)
    for key, bundle in bundles.items():
        headless = next(
            item
            for item in matrix
            if item.profile_id == key[0]
            and item.load_stage == key[1]
            and item.repetition == 1
            and item.mode == "headless_baseline"
        )
        assert headless.input_stream_sha256 == bundle.input_stream_sha256

    first = bundles[("functional_6", "motion_only")]
    rebuilt = build_scenario(ROOT, plan, "functional_6", "motion_only", 1)
    assert first.input_descriptor == rebuilt.input_descriptor
    assert first.input_stream_sha256 == rebuilt.input_stream_sha256
    assert canonical_sha256(first.initial_scene) == canonical_sha256(rebuilt.initial_scene)
    repetition_two = build_scenario(ROOT, plan, "functional_6", "motion_only", 2)
    assert repetition_two.input_stream_sha256 == first.input_stream_sha256

    require_error(
        "plan.profile_missing",
        lambda: build_scenario(ROOT, plan, "missing", "motion_only", 1),
    )
    require_error(
        "scenario.load_stage",
        lambda: build_scenario(ROOT, plan, "functional_6", "missing", 1),
    )
    require_error(
        "scenario.repetition",
        lambda: build_scenario(ROOT, plan, "functional_6", "motion_only", 0),
    )

    first_step_hashes = {}
    for stage in plan.load_stages:
        bundle = bundles[("functional_6", stage)]
        step = advance_scenario_step(bundle, bundle.initial_scene)
        first_step_hashes[stage] = canonical_sha256(step.resulting_scene)
        if stage == "motion_only":
            assert len(step.weapon_events) == 0
            assert len(step.spawned_projectiles) == 0
        else:
            assert len(step.weapon_events) == 4
            assert len(step.spawned_projectiles) == 4
        if stage == "scripted_damage_and_recompile":
            assert len(step.continuous_damage_events) == 6
        else:
            assert len(step.continuous_damage_events) == 0
        assert scene_entity_counts(step.resulting_scene)["active_ships"] == 6
    assert len(set(first_step_hashes.values())) == len(plan.load_stages)

    first_run = run_headless_baseline(
        first,
        command="python -X utf8 高天荒野T0确定性场景与无界面基线测试.py",
        warmup_steps=0,
        measured_steps=2,
    )
    second_run = run_headless_baseline(
        rebuilt,
        command="python -X utf8 高天荒野T0确定性场景与无界面基线测试.py",
        warmup_steps=0,
        measured_steps=2,
    )
    assert first_run["authority_state_sha256"] == second_run["authority_state_sha256"]
    assert first_run["authority_event_sha256"] == second_run["authority_event_sha256"]
    assert first_run["run_result"]["metadata"]["actual_entity_counts"] == second_run["run_result"]["metadata"]["actual_entity_counts"]
    assert first_run["run_result"]["run_spec"] == second_run["run_result"]["run_spec"]
    assert first_run["run_result"]["status"] == "NOT_COVERED"
    assert first_run["load_coverage"]["status"] == "NOT_COVERED"
    assert first_run["execution"]["official_duration"] is False
    assert first_run["headless_baseline_measured"] is False
    assert first_run["t0_performance_measured"] is False
    assert first_run["run_result"]["metrics"]["fixed_step"]["sample_count"] == 2
    assert first_run["run_result"]["metrics"]["observer_drain"]["sample_count"] == 2
    assert first_run["run_result"]["metrics"]["resident_memory"]["sample_count"] == 1
    assert first_run["execution"]["resident_memory_sample_interval_steps"] == 60
    assert first_run["execution"]["resident_memory_sample_count"] == 1
    assert abs(
        first_run["execution"]["measured_wall_s"]
        - first_run["execution"]["authoritative_advance_wall_s"]
        - first_run["execution"]["observer_drain_wall_s"]
    ) <= 1.0e-9

    schema = json.loads(RESULT_SCHEMA_PATH.read_text(encoding="utf-8"))
    run_result = first_run["run_result"]
    assert set(run_result) == set(schema["$defs"]["runResult"]["required"])
    assert set(run_result["run_spec"]) == set(schema["$defs"]["runSpec"]["required"])
    assert set(run_result["metadata"]) == set(schema["$defs"]["runMetadata"]["required"])
    assert set(run_result["metrics"]) == set(
        schema["$defs"]["runResult"]["properties"]["metrics"]["required"]
    )

    implementation_paths = (
        ROOT / "benchmarks" / "t0" / "headless.py",
        ROOT / "benchmarks" / "t0" / "matrix.py",
        ROOT / "benchmarks" / "t0" / "metadata.py",
        ROOT / "benchmarks" / "t0" / "scenario.py",
        ROOT / "benchmarks" / "t0" / "__main__.py",
        ROOT / "tools" / "Invoke-HighWildernessT0.ps1",
        ROOT / TIMING_RELATIVE,
    )
    report = {
        "acceptance": {
            "all_profile_stage_initial_states": "12_of_12_PASS",
            "authority_hash_repeatability": "PASS",
            "headless_result_contract": "PASS",
            "legal_ammunition_and_weapon_timeline": "PASS",
            "negative_inputs": "PASS",
            "step_smoke": "4_of_4_PASS",
        },
        "headless_smoke": {
            "authority_event_sha256": first_run["authority_event_sha256"],
            "authority_state_sha256": first_run["authority_state_sha256"],
            "measured_steps": 2,
            "official_duration": False,
            "status": first_run["run_result"]["status"],
            "warmup_steps": 0,
        },
        "implementation_hashes": {
            path.relative_to(ROOT).as_posix(): file_sha256(path)
            for path in implementation_paths
        },
        "interface": "gaotian.stage-t0b-deterministic-scene-headless-regression/v1",
        "load_integrity": {
            "maximum_sustained_weapon_action_events_per_second": {
                "functional_6": 8.0,
                "stress_30": 40.0,
                "target_20": 28.0
            },
            "planned_targets_remain_not_covered": True,
            "reason": "补足无人武器时间配置后仍低于 24/120/300 次每秒目标",
        },
        "next_slice": "T0b1_authoritative_hot_path_optimization",
        "official_headless_runs_executed": 0,
        "scenario_manifests": {
            f"{profile_id}.{stage}": {
                "actual_initial_entity_counts": manifest["actual_initial_entity_counts"],
                "initial_scene_sha256": manifest["initial_scene_sha256"],
                "input_stream_sha256": manifest["input_stream_sha256"],
            }
            for (profile_id, stage), manifest in sorted(manifests.items())
        },
        "scope": "t0b_deterministic_scenarios_legal_loadout_and_headless_baseline_only",
        "status": "PASS",
        "t0_gate_status": "NOT_COVERED",
        "t0_performance_measured": False,
    }
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
