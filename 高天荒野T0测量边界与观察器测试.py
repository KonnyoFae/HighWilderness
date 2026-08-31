"""T0b.1e：权威推进、观察器 drain、元数据、内存采样与续跑边界。"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter_ns
from typing import Any, Callable, Iterable

import benchmarks.t0.headless as headless_module
from benchmarks.t0.__main__ import _resume_matches
from benchmarks.t0.contracts import (
    BenchmarkContractError,
    canonical_json_bytes,
    load_benchmark_plan,
)
from benchmarks.t0.diagnostics import load_authority_step_golden
from benchmarks.t0.headless import (
    EVENT_KEYS,
    EVENT_STREAM_DOMAIN,
    HEADLESS_INTERFACE,
    HEADLESS_MEASUREMENT_POLICY,
    REAL_TIME_FACTOR_SCOPE,
    authority_event_stream_sha256,
    run_headless_baseline,
)
from benchmarks.t0.metadata import collect_environment_metadata
from benchmarks.t0.scenario import advance_scenario_step, build_scenario


ROOT = Path(__file__).resolve().parent
PLAN_PATH = ROOT / "contracts" / "web_bridge" / "t0-benchmark-plan.v1.json"
GOLDEN_PATH = ROOT / "contracts" / "web_bridge" / "t0-authority-step-golden.v1.json"
RESULT_SCHEMA_PATH = (
    ROOT / "contracts" / "web_bridge" / "t0-benchmark-result.v1.schema.json"
)


def require_error(code: str, action: Callable[[], object]) -> None:
    try:
        action()
    except BenchmarkContractError as error:
        if error.code != code:
            raise AssertionError(f"预期 {code}，实际 {error.code}: {error}") from error
    else:
        raise AssertionError(f"预期抛出 {code}")


def legacy_event_stream_sha256(resolutions: Iterable[Any]) -> str:
    digest = hashlib.sha256(EVENT_STREAM_DOMAIN).digest()
    for resolution in resolutions:
        value = resolution.to_dict()
        payload = {
            key: value[key]
            for key in sorted(EVENT_KEYS)
            if key in value
        }
        encoded = canonical_json_bytes(payload)
        digest = hashlib.sha256(
            digest + len(encoded).to_bytes(8, "big") + encoded
        ).digest()
    return digest.hex()


class EventOnlyResolution:
    """若摘要器越界物化整个 resolution，本包装会立即失败。"""

    def __init__(self, resolution: Any) -> None:
        for key in EVENT_KEYS:
            setattr(self, key, getattr(resolution, key))

    def to_dict(self) -> dict[str, Any]:
        raise AssertionError("事件摘要不得调用 resolution.to_dict()")


def test_direct_event_digest(plan) -> int:
    golden = load_authority_step_golden(GOLDEN_PATH)
    checked = 0
    for profile in plan.profiles:
        for stage in plan.load_stages:
            bundle = build_scenario(ROOT, plan, profile.id, stage, 1)
            resolution = advance_scenario_step(bundle, bundle.initial_scene)
            direct = authority_event_stream_sha256((resolution,))
            assert direct == legacy_event_stream_sha256((resolution,))
            assert direct == golden["cases"][f"{profile.id}.{stage}"][
                "authority_event_sha256"
            ]
            assert authority_event_stream_sha256(
                (EventOnlyResolution(resolution),)
            ) == direct
            checked += 1
    return checked


def test_measurement_boundary(plan) -> tuple[dict[str, Any], dict[str, int]]:
    bundle = build_scenario(ROOT, plan, "functional_6", "motion_only", 1)
    command = "python -X utf8 高天荒野T0测量边界与观察器测试.py"
    calls = {"environment": 0, "memory": 0}
    original_environment = headless_module.collect_environment_metadata
    original_memory = headless_module.process_resident_memory_bytes
    original_digest = headless_module._event_digest

    def tracking_environment(root, *, command):
        calls["environment"] += 1
        return original_environment(root, command=command)

    def tracking_memory():
        calls["memory"] += 1
        return 100_000_000 + calls["memory"]

    def deliberately_visible_drain(previous, resolution):
        digest = original_digest(previous, resolution)
        deadline = perf_counter_ns() + 2_000_000
        while perf_counter_ns() < deadline:
            pass
        return digest

    headless_module.collect_environment_metadata = tracking_environment
    headless_module.process_resident_memory_bytes = tracking_memory
    headless_module._event_digest = deliberately_visible_drain
    try:
        result = run_headless_baseline(
            bundle,
            command=command,
            warmup_steps=0,
            measured_steps=5,
            memory_sample_interval_steps=2,
        )
    finally:
        headless_module.collect_environment_metadata = original_environment
        headless_module.process_resident_memory_bytes = original_memory
        headless_module._event_digest = original_digest

    assert calls == {"environment": 1, "memory": 3}
    metrics = result["run_result"]["metrics"]
    execution = result["execution"]
    metadata = result["run_result"]["metadata"]
    assert result["interface"] == HEADLESS_INTERFACE
    assert metrics["fixed_step"]["sample_count"] == 5
    assert metrics["observer_drain"]["sample_count"] == 5
    assert metrics["observer_drain"]["mean"] >= 2.0
    assert metrics["resident_memory"]["sample_count"] == 3
    assert execution["resident_memory_sample_interval_steps"] == 2
    assert execution["resident_memory_sample_count"] == 3
    assert metadata["resident_memory_sample_interval_steps"] == 2
    assert metadata["resident_memory_sample_count"] == 3
    assert execution["measurement_policy"] == HEADLESS_MEASUREMENT_POLICY
    assert metadata["measurement_policy"] == HEADLESS_MEASUREMENT_POLICY
    assert execution["real_time_factor_scope"] == REAL_TIME_FACTOR_SCOPE
    assert metadata["real_time_factor_scope"] == REAL_TIME_FACTOR_SCOPE
    assert abs(
        execution["measured_wall_s"]
        - execution["authoritative_advance_wall_s"]
        - execution["observer_drain_wall_s"]
    ) <= 1.0e-9
    assert metrics["real_time_factor"]["mean"] == (
        execution["simulated_measured_s"] / execution["measured_wall_s"]
    )
    assert execution["observer_drain_wall_s"] >= 0.0095
    return result, calls


def test_supplied_environment_and_resume(plan) -> None:
    bundle = build_scenario(ROOT, plan, "functional_6", "motion_only", 1)
    command = "python -X utf8 -m benchmarks.t0 headless --diagnostic"
    environment = collect_environment_metadata(ROOT, command=command)
    wrong_command = dict(environment)
    wrong_command["command"] = "wrong"
    require_error(
        "headless.environment_command",
        lambda: run_headless_baseline(
            bundle,
            command=command,
            warmup_steps=0,
            measured_steps=1,
            environment_metadata=wrong_command,
        ),
    )
    require_error(
        "headless.memory_sample_interval_steps",
        lambda: run_headless_baseline(
            bundle,
            command=command,
            warmup_steps=0,
            measured_steps=1,
            environment_metadata=environment,
            memory_sample_interval_steps=0,
        ),
    )
    original_environment = headless_module.collect_environment_metadata

    def forbidden_environment(*args, **kwargs):
        raise AssertionError("已提供运行级元数据时不得重复采集")

    headless_module.collect_environment_metadata = forbidden_environment
    try:
        result = run_headless_baseline(
            bundle,
            command=command,
            warmup_steps=0,
            measured_steps=2,
            environment_metadata=environment,
        )
    finally:
        headless_module.collect_environment_metadata = original_environment

    assert result["authority_state_sha256"] == result["run_result"][
        "authority_state_sha256"
    ]
    with TemporaryDirectory() as directory:
        path = Path(directory) / "headless.json"
        path.write_text(
            json.dumps(result, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        assert _resume_matches(
            path,
            bundle,
            warmup_steps=0,
            measured_steps=2,
        )

        stale = deepcopy(result)
        stale["interface"] = "gaotian.t0-headless-baseline/v1"
        path.write_text(json.dumps(stale), encoding="utf-8")
        assert not _resume_matches(path, bundle, warmup_steps=0, measured_steps=2)

        missing_observer = deepcopy(result)
        del missing_observer["run_result"]["metrics"]["observer_drain"]
        path.write_text(json.dumps(missing_observer), encoding="utf-8")
        assert not _resume_matches(path, bundle, warmup_steps=0, measured_steps=2)

        bad_split = deepcopy(result)
        bad_split["execution"]["observer_drain_wall_s"] += 1.0
        path.write_text(json.dumps(bad_split), encoding="utf-8")
        assert not _resume_matches(path, bundle, warmup_steps=0, measured_steps=2)


def test_result_schema() -> None:
    schema = json.loads(RESULT_SCHEMA_PATH.read_text(encoding="utf-8"))
    metadata = schema["$defs"]["runMetadata"]
    metrics = schema["$defs"]["runResult"]["properties"]["metrics"]
    assert {
        "measurement_policy",
        "real_time_factor_scope",
        "resident_memory_sample_count",
        "resident_memory_sample_interval_steps",
    } <= set(metadata["required"])
    assert "observer_drain" in metrics["required"]
    assert metadata["properties"]["measurement_policy"]["const"] == (
        HEADLESS_MEASUREMENT_POLICY
    )
    assert metadata["properties"]["real_time_factor_scope"]["const"] == (
        REAL_TIME_FACTOR_SCOPE
    )


def main() -> None:
    plan = load_benchmark_plan(PLAN_PATH)
    digest_cases = test_direct_event_digest(plan)
    result, calls = test_measurement_boundary(plan)
    test_supplied_environment_and_resume(plan)
    test_result_schema()
    report = {
        "acceptance": {
            "direct_event_digest_equivalence": f"{digest_cases}_of_{digest_cases}_PASS",
            "environment_metadata_collection": "1_per_run_PASS",
            "fixed_step_excludes_observer_drain": "PASS",
            "low_frequency_memory_sampling": "PASS",
            "measurement_input_negative_cases": "2_of_2_PASS",
            "real_time_factor_includes_drain": "PASS",
            "result_schema": "PASS",
            "resume_rejects_stale_or_incomplete_results": "3_of_3_PASS",
        },
        "headless_interface": HEADLESS_INTERFACE,
        "interface": "gaotian.stage-t0b1e-measurement-boundary/v1",
        "measurement_policy": HEADLESS_MEASUREMENT_POLICY,
        "observer_probe": {
            "environment_collection_calls": calls["environment"],
            "fixed_step_samples": result["run_result"]["metrics"]["fixed_step"][
                "sample_count"
            ],
            "memory_collection_calls": calls["memory"],
            "memory_samples": result["run_result"]["metrics"]["resident_memory"][
                "sample_count"
            ],
            "observer_drain_samples": result["run_result"]["metrics"][
                "observer_drain"
            ]["sample_count"],
        },
        "official_performance_runs_executed": 0,
        "status": "PASS",
        "t0_performance_measured": False,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
