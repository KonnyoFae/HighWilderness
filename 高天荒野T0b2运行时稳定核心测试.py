"""T0b.2b2：runtime 稳定核心、实例轻量视图与哈希边界回归。"""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
import json
from pathlib import Path

from benchmarks.t0.contracts import load_benchmark_plan
from benchmarks.t0.diagnostics import (
    load_authority_step_golden,
    load_hot_path_diagnostic_plan,
    profile_hot_path_case,
    verify_authority_step_golden,
)
from benchmarks.t0.metadata import file_sha256
from benchmarks.t0.scenario import build_scenario
from 高天荒野T0运行时与静态模型复用测试 import (
    test_passthrough_ammunition_and_timeline,
    test_runtime_dirty_domains,
)
from 高天荒野舰艇数据契约 import canonical_sha256
import 高天荒野舰艇运行时参数编译器 as runtime_compiler


ROOT = Path(__file__).resolve().parent
PLAN_PATH = ROOT / "contracts" / "web_bridge" / "t0-benchmark-plan.v1.json"
DIAGNOSTIC_PLAN_PATH = (
    ROOT / "contracts" / "web_bridge" / "t0-hot-path-diagnostic.v1.json"
)
GOLDEN_PATH = ROOT / "contracts" / "web_bridge" / "t0-authority-step-golden.v1.json"
REPORT_PATH = ROOT / "舰艇数据" / "报告" / "阶段T0b2b2运行时稳定核心接口.v1.json"
BASELINE_TARGET_MOTION_CANONICAL_CALLS = 222


def resolve(cache, binding, instance, events=()):
    return cache.resolve(
        binding.snapshot,
        binding.sortie,
        instance,
        active_automatic_events=events,
        validation_mode=runtime_compiler.RUNTIME_CACHE_VALIDATION_TRUSTED,
    )


def test_runtime_view_hash_boundaries(plan) -> dict[str, object]:
    bundle = build_scenario(ROOT, plan, "functional_6", "motion_only", 1)
    binding = bundle.bindings[0]
    ship = next(
        item
        for item in bundle.initial_scene.ships
        if item.ship_id == binding.ship_id
    )
    instance = ship.combat_state.instance
    cache = runtime_compiler.RuntimeShipParametersCache()

    calls: list[str] = []
    original_canonical_sha256 = runtime_compiler.canonical_sha256

    def traced_canonical_sha256(value):
        calls.append(type(value).__name__)
        return original_canonical_sha256(value)

    runtime_compiler.canonical_sha256 = traced_canonical_sha256
    try:
        first = resolve(cache, binding, instance)
        first_call_count = len(calls)
        repeat = resolve(cache, binding, instance)
        equal_copy = resolve(cache, binding, replace(instance))

        fuel_changed = replace(
            instance,
            operational_state=replace(
                instance.operational_state,
                fuel_units=instance.operational_state.fuel_units - 1.0,
            ),
        )
        rebound = resolve(cache, binding, fuel_changed)
        rebound_call_count = len(calls) - first_call_count
        rebound_repeat = resolve(cache, binding, fuel_changed)

        event_variant = resolve(cache, binding, fuel_changed, ("event.one",))
        event_variant_call_count = len(calls) - first_call_count - rebound_call_count
        event_variant_repeat = resolve(
            cache,
            binding,
            fuel_changed,
            ("event.one",),
        )
        replayed_first = resolve(cache, binding, instance)
    finally:
        runtime_compiler.canonical_sha256 = original_canonical_sha256

    assert first_call_count == 2
    assert rebound_call_count == 2
    assert event_variant_call_count == 1
    assert Counter(calls) == {
        "RuntimeShipParameters": 3,
        "ShipInstanceSnapshotInput": 2,
    }
    assert repeat.runtime is first.runtime
    assert equal_copy.runtime is first.runtime
    assert rebound.runtime is not first.runtime
    assert rebound.runtime.stable_core is first.runtime.stable_core
    assert rebound_repeat.runtime is rebound.runtime
    assert event_variant.runtime.stable_core is not first.runtime.stable_core
    assert event_variant_repeat.runtime is event_variant.runtime
    assert replayed_first.runtime is first.runtime
    assert rebound.runtime.instance_snapshot == fuel_changed
    assert rebound.runtime.instance_snapshot_sha256 == canonical_sha256(fuel_changed)
    assert rebound.runtime.source_sha256 == canonical_sha256(rebound.runtime)

    stats = cache.stats()
    assert stats["direct_view_hit_count"] == 5
    assert stats["entry_count"] == 2
    assert stats["hit_count"] == 6
    assert stats["instance_hash_count"] == 2
    assert stats["instance_view_count"] == 3
    assert stats["miss_count"] == 2
    assert stats["runtime_rebind_count"] == 1

    bounded_cache = runtime_compiler.RuntimeShipParametersCache()
    resolve(bounded_cache, binding, instance)
    for offset in range(1, 6):
        changed = replace(
            instance,
            operational_state=replace(
                instance.operational_state,
                fuel_units=instance.operational_state.fuel_units - offset,
            ),
        )
        resolve(bounded_cache, binding, changed)
    assert bounded_cache.stats()["instance_view_count"] == 4
    return {
        "bounded_view_history": True,
        "canonical_calls": dict(sorted(Counter(calls).items())),
        "cache_stats": stats,
        "event_variant_core_separated": True,
        "replay_view_reused": True,
        "stable_core_reused_across_passthrough_change": True,
    }


def main() -> None:
    plan = load_benchmark_plan(PLAN_PATH)
    view_evidence = test_runtime_view_hash_boundaries(plan)
    invalidation_matrix = test_runtime_dirty_domains(plan)
    assert len(invalidation_matrix) == 8
    test_passthrough_ammunition_and_timeline(plan)

    expected_golden = load_authority_step_golden(GOLDEN_PATH)
    assert verify_authority_step_golden(ROOT, plan, GOLDEN_PATH) == expected_golden

    diagnostic_plan = load_hot_path_diagnostic_plan(DIAGNOSTIC_PLAN_PATH)
    target_motion = next(
        item
        for item in diagnostic_plan.cases
        if item.id == "target_20.motion_only"
    )
    observed = profile_hot_path_case(ROOT, plan, diagnostic_plan, target_motion)
    assert observed["authority_equivalent"] is True
    probes = observed["profiled"]["function_probes"]
    canonical_calls = probes["canonical_sha256"]["total_calls"]
    runtime_view_binds = probes["runtime_view_bind"]["total_calls"]
    assert canonical_calls <= 62
    assert canonical_calls < BASELINE_TARGET_MOTION_CANONICAL_CALLS
    assert runtime_view_binds == 0
    # 后续等价优化可以继续收敛边界重复解析，但不得倒退超过 b2 基线。
    assert probes["runtime_cache_resolve"]["total_calls"] <= 80
    assert probes["compile_runtime_ship_parameters"]["total_calls"] == 0

    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    assert report["interface"] == "gaotian.stage-t0b2b2-runtime-stable-core/v1"
    assert report["status"] == "PASS"
    assert report["official_performance_runs_executed"] == 0
    assert report["t0_performance_measured"] is False
    assert report["target_20_motion_profile"]["canonical_sha256_calls"]["after"] == 62
    assert report["target_20_motion_profile"]["runtime_view_bind_calls"] == 0
    assert report["unit_evidence"] == view_evidence
    assert report["dirty_domain_matrix"] == invalidation_matrix
    for relative_path in (
        "benchmarks/t0/diagnostics.py",
        "contracts/web_bridge/t0-authority-step-golden.v1.json",
        "高天荒野T0b2运行时稳定核心测试.py",
        "高天荒野T0b2推进响应与权威性能优化规划.md",
        "高天荒野Web客户端编辑器与战术验证实施计划.md",
        "高天荒野舰艇战术机动求解器测试.py",
        "高天荒野舰艇统一战术场景.py",
        "高天荒野舰艇运行时参数编译器.py",
    ):
        assert report["implementation_hashes"][relative_path] == file_sha256(
            ROOT / relative_path
        )

    print(
        json.dumps(
            {
                "authority_golden": "12_of_12_PASS",
                "canonical_sha256_calls": canonical_calls,
                "dirty_domains": len(invalidation_matrix),
                "interface": "gaotian.stage-t0b2b2-runtime-stable-core-test/v1",
                "runtime_view_bind_calls": runtime_view_binds,
                "status": "PASS",
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
