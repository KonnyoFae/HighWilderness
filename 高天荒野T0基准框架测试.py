"""T0a：基准合同、正式矩阵、统计口径、元数据与负载审计回归。"""

from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
import json
from pathlib import Path
from typing import Callable

from benchmarks.t0.__main__ import build_preflight_manifest
from benchmarks.t0.contracts import (
    BenchmarkContractError,
    canonical_sha256,
    load_benchmark_plan,
    parse_benchmark_plan,
)
from benchmarks.t0.fixture_audit import audit_fixture_capacity
from benchmarks.t0.matrix import build_input_descriptor, expand_matrix, matrix_summary
from benchmarks.t0.metadata import collect_environment_metadata, file_sha256
from benchmarks.t0.metrics import nearest_rank_percentile, summarize_samples


ROOT = Path(__file__).resolve().parent
PLAN_PATH = ROOT / "contracts" / "web_bridge" / "t0-benchmark-plan.v1.json"
RESULT_SCHEMA_PATH = ROOT / "contracts" / "web_bridge" / "t0-benchmark-result.v1.schema.json"
REPORT_PATH = ROOT / "舰艇数据" / "报告" / "阶段T0a基准框架与负载审计接口.v1.json"


def require_contract_error(code: str, action: Callable[[], object]) -> None:
    try:
        action()
    except BenchmarkContractError as error:
        if error.code != code:
            raise AssertionError(f"预期 {code}，实际 {error.code}: {error}") from error
    else:
        raise AssertionError(f"预期抛出 {code}")


def test_plan_and_matrix() -> tuple[object, tuple[object, ...]]:
    plan = load_benchmark_plan(PLAN_PATH)
    assert plan.fixed_step_hz == 60
    assert plan.warmup_steps == 600
    assert plan.measured_steps == 3600
    assert plan.repetitions == 3
    assert plan.snapshot_rates_hz == (20, 30)
    assert plan.source_sha256 == canonical_sha256(plan.raw)

    runs = expand_matrix(plan)
    summary = matrix_summary(plan, runs)
    assert summary == {
        "comparison_group_count": 36,
        "input_group_count": 12,
        "mode_rate_variant_count": 7,
        "official_run_count": 252,
        "profiles": 3,
        "repetitions": 3,
        "stages": 4,
        "unique_input_stream_count": 12,
    }
    assert Counter(item.mode for item in runs) == {
        "headless_baseline": 36,
        "full_authoritative_json": 72,
        "experimental_render_full": 72,
        "experimental_render_delta": 72,
    }
    assert Counter(item.snapshot_rate_hz for item in runs) == {None: 36, 20: 108, 30: 108}

    groups: dict[tuple[str, str, int], list[object]] = defaultdict(list)
    for run in runs:
        groups[(run.profile_id, run.load_stage, run.repetition)].append(run)
    assert len(groups) == 36
    for group in groups.values():
        assert len(group) == 7
        assert len({item.input_stream_sha256 for item in group}) == 1
        assert len({item.input_seed for item in group}) == 1

    first_profile = plan.profiles[0]
    descriptors = [
        build_input_descriptor(plan, first_profile, plan.load_stages[0], repetition)
        for repetition in range(1, 4)
    ]
    assert descriptors[0] == build_input_descriptor(plan, first_profile, plan.load_stages[0], 1)
    assert len({canonical_sha256(item) for item in descriptors}) == 1
    assert all("mode" not in item and "snapshot_rate_hz" not in item for item in descriptors)
    return plan, runs


def test_strict_rejection(plan: object) -> None:
    unknown = deepcopy(plan.raw)
    unknown["unexpected"] = True
    require_contract_error("object.keys", lambda: parse_benchmark_plan(unknown))

    bad_rate = deepcopy(plan.raw)
    bad_rate["snapshot_rates_hz"] = [20, True]
    require_contract_error("type.integer", lambda: parse_benchmark_plan(bad_rate))

    bad_composition = deepcopy(plan.raw)
    bad_composition["profiles"][0]["composition"]["minimum_legal"] += 1
    require_contract_error("plan.composition_total", lambda: parse_benchmark_plan(bad_composition))

    bad_mode = deepcopy(plan.raw)
    bad_mode["modes"][0] = "unknown"
    require_contract_error("plan.modes", lambda: parse_benchmark_plan(bad_mode))


def test_metrics() -> None:
    assert nearest_rank_percentile([], 0.95) is None
    assert nearest_rank_percentile([7], 0.95) == 7.0
    assert nearest_rank_percentile(range(1, 21), 0.95) == 19.0
    summary = summarize_samples([4.0, 1.0, 3.0, 2.0])
    assert summary.to_dict() == {
        "maximum": 4.0,
        "mean": 2.5,
        "p95_nearest_rank": 4.0,
        "sample_count": 4,
    }
    assert summarize_samples([]).to_dict() == {
        "maximum": None,
        "mean": None,
        "p95_nearest_rank": None,
        "sample_count": 0,
    }
    require_contract_error("metric.sample_finite", lambda: summarize_samples([float("nan")]))
    require_contract_error("metric.percentile", lambda: nearest_rank_percentile([1], 0.0))


def test_schema() -> dict[str, object]:
    schema = json.loads(RESULT_SCHEMA_PATH.read_text(encoding="utf-8"))
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"] == "gaotian.tactical-performance-result/v1"
    assert schema["additionalProperties"] is False
    assert schema["$defs"]["status"]["enum"] == ["PASS", "FAIL", "NOT_COVERED"]
    metric = schema["$defs"]["metricSummary"]
    assert "null" in metric["properties"]["mean"]["type"]
    assert "null" in metric["properties"]["p95_nearest_rank"]["type"]
    assert "null" in metric["properties"]["maximum"]["type"]
    return schema


def test_metadata(plan: object) -> dict[str, object]:
    metadata = collect_environment_metadata(
        ROOT, command="python -X utf8 高天荒野T0基准框架测试.py"
    )
    assert set(metadata) == {
        "command",
        "commit",
        "cpu",
        "dirty",
        "dirty_diff_sha256",
        "node",
        "os",
        "power_mode",
        "python",
        "ram_bytes",
        "rust",
        "webview2",
    }
    assert metadata["commit"] != "unknown"
    dirty_hash = metadata["dirty_diff_sha256"]
    assert dirty_hash == "unknown" or (
        isinstance(dirty_hash, str)
        and len(dirty_hash) == 64
        and set(dirty_hash) <= set("0123456789abcdef")
    )
    assert metadata["ram_bytes"] is None or metadata["ram_bytes"] > 0
    assert set(plan.required_metadata) >= {
        "profile",
        "fixture_resource_hashes",
        "input_stream_sha256",
        "actual_entity_counts",
    }
    return metadata


def test_fixture_audit(plan: object) -> dict[str, object]:
    audit = audit_fixture_capacity(ROOT, plan)
    assert audit["status"] == "NOT_COVERED"
    assert audit["t0_performance_measured"] is False
    assert len(audit["fixture_resource_hashes"]) == 23
    assert all(len(value) == 64 for value in audit["fixture_resource_hashes"].values())

    ships = {item["ship_fixture"]: item for item in audit["ship_fixture_audits"]}
    assert ships["minimum_legal"]["installed_weapon_count"] == 0
    assert ships["conventional_crewed"]["installed_weapon_count"] == 1
    assert ships["conventional_crewed"]["timing_profiled_weapon_count"] == 1
    assert ships["conventional_crewed"]["legal_weapon_fires_60s_capacity_with_full_benchmark_loadout"] == 60
    assert ships["conventional_crewed"]["legal_weapon_action_events_60s_capacity_with_full_benchmark_loadout"] == 120
    assert ships["unmanned_flagship"]["installed_weapon_count"] == 1
    assert ships["unmanned_flagship"]["timing_profiled_weapon_count"] == 0
    assert all(item["runtime_recompile_probe"] == "PASS" for item in ships.values())

    profiles = {item["profile"]: item for item in audit["profile_audits"]}
    expected = {
        "functional_6": (4, 2, 4.0, 240),
        "target_20": (14, 10, 20.0, 1200),
        "stress_30": (20, 14, 28.0, 1680),
    }
    for profile_id, values in expected.items():
        capacity = profiles[profile_id]["actual_fixture_capacity"]
        assert (
            capacity["installed_weapon_count"],
            capacity["timing_profiled_weapon_count"],
            capacity["maximum_sustained_weapon_action_events_per_second"],
            capacity["legal_weapon_action_events_60s_capacity_with_full_benchmark_loadout"],
        ) == values
        assert profiles[profile_id]["load_readiness"]["motion_only"] == "PASS"
        assert profiles[profile_id]["load_readiness"]["weapon_event_target"] == "NOT_COVERED"
        assert profiles[profile_id]["reasons"]
    return audit


def test_manifest(plan: object, schema: dict[str, object]) -> None:
    manifest = build_preflight_manifest(
        ROOT,
        PLAN_PATH,
        command="python -X utf8 -m benchmarks.t0 audit",
    )
    assert set(manifest) == set(schema["required"])
    assert manifest["interface"] == schema["$id"]
    assert manifest["status"] == "NOT_COVERED"
    assert manifest["t0_performance_measured"] is False
    assert manifest["t1_density_recommendation"] is None
    assert manifest["runs"] == []
    assert manifest["matrix"]["official_run_count"] == 252
    assert len(manifest["matrix"]["runs"]) == 252
    assert len(manifest["gates"]) == 5
    assert all(item["status"] == "NOT_COVERED" for item in manifest["gates"])
    assert manifest["plan_sha256"] == plan.source_sha256


def main() -> None:
    plan, runs = test_plan_and_matrix()
    test_strict_rejection(plan)
    test_metrics()
    schema = test_schema()
    metadata = test_metadata(plan)
    audit = test_fixture_audit(plan)
    test_manifest(plan, schema)

    profile_capacity = {
        item["profile"]: item["actual_fixture_capacity"]
        for item in audit["profile_audits"]
    }
    implementation_paths = (
        ROOT / "benchmarks" / "t0" / "contracts.py",
        ROOT / "benchmarks" / "t0" / "fixture_audit.py",
        ROOT / "benchmarks" / "t0" / "matrix.py",
        ROOT / "benchmarks" / "t0" / "metadata.py",
        ROOT / "benchmarks" / "t0" / "metrics.py",
        ROOT / "benchmarks" / "t0" / "__main__.py",
        ROOT / "tools" / "Invoke-HighWildernessT0.ps1",
    )
    report = {
        "acceptance": {
            "fixture_capacity_audit": "PASS",
            "input_hash_determinism": "PASS",
            "machine_metadata_collection": "PASS",
            "matrix_expansion": "252_of_252",
            "nearest_rank_p95": "PASS",
            "strict_plan_rejection": "PASS",
        },
        "fixture_audit": {
            "profile_capacity": profile_capacity,
            "resource_hash_count": len(audit["fixture_resource_hashes"]),
            "status": audit["status"],
        },
        "implementation_hashes": {
            path.relative_to(ROOT).as_posix(): file_sha256(path)
            for path in implementation_paths
        },
        "interface": "gaotian.stage-t0a-benchmark-framework-regression/v1",
        "matrix": matrix_summary(plan, runs),
        "next_slice": "T0b_deterministic_scene_generator_and_headless_baseline",
        "official_performance_runs_executed": 0,
        "plan_sha256": plan.source_sha256,
        "reference_environment_fields_verified": sorted(metadata),
        "result_schema_sha256": file_sha256(RESULT_SCHEMA_PATH),
        "scope": "t0a_contract_matrix_statistics_metadata_and_fixture_capacity_audit_only",
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
