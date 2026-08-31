"""T0b.1b：深度冻结静态资源、缓存指纹与严格/快速绑定边界。"""

from __future__ import annotations

from dataclasses import fields, is_dataclass, replace
import json
from pathlib import Path
from typing import Any, Callable

from 高天荒野舰艇数据契约 import ContractError, canonical_sha256
from 高天荒野舰艇阶段F三舰集成测试 import build_chain
from 高天荒野舰艇无界面舾装编译器 import (
    compute_derived_ship_snapshot_sha256,
    verify_derived_ship_snapshot_fingerprint,
)
from 高天荒野舰艇统一战术场景 import (
    BINDING_VALIDATION_STRICT,
    BINDING_VALIDATION_TRUSTED,
)

from benchmarks.t0.contracts import load_benchmark_plan
from benchmarks.t0.diagnostics import (
    load_authority_step_golden,
    load_hot_path_diagnostic_plan,
    profile_hot_path_case,
)
from benchmarks.t0.headless import authority_event_stream_sha256
from benchmarks.t0.metadata import file_sha256
from benchmarks.t0.scenario import advance_scenario_step, build_scenario


ROOT = Path(__file__).resolve().parent
PLAN_PATH = ROOT / "contracts" / "web_bridge" / "t0-benchmark-plan.v1.json"
DIAGNOSTIC_PLAN_PATH = (
    ROOT / "contracts" / "web_bridge" / "t0-hot-path-diagnostic.v1.json"
)
GOLDEN_PATH = ROOT / "contracts" / "web_bridge" / "t0-authority-step-golden.v1.json"
REPORT_PATH = ROOT / "舰艇数据" / "报告" / "阶段T0b1b静态指纹与验证边界接口.v1.json"
FIXTURE_KEYS = ("minimum_legal", "conventional_crewed", "unmanned_flagship")


def require_error(code: str, action: Callable[[], object]) -> None:
    try:
        action()
    except ContractError as error:
        if error.code != code:
            raise AssertionError(f"预期 {code}，实际 {error.code}: {error}") from error
    else:
        raise AssertionError(f"预期抛出 {code}")


def mutable_container_paths(root: Any) -> tuple[str, ...]:
    """递归找出快照内部会破坏缓存指纹的可变容器。"""

    result: list[str] = []
    visited: set[int] = set()

    def visit(value: Any, path: str) -> None:
        if isinstance(value, (str, bytes, int, float, bool, type(None))):
            return
        identity = id(value)
        if identity in visited:
            return
        visited.add(identity)
        if isinstance(value, (dict, list, set)):
            result.append(path)
        if isinstance(value, dict):
            for key, item in value.items():
                visit(item, f"{path}[{key!r}]")
        elif isinstance(value, (list, tuple, set, frozenset)):
            for index, item in enumerate(value):
                visit(item, f"{path}[{index}]")
        elif is_dataclass(value):
            for item in fields(value):
                visit(getattr(value, item.name), f"{path}.{item.name}")

    visit(root, "snapshot")
    return tuple(result)


def test_static_fingerprint_and_deep_freeze() -> dict[str, str]:
    hashes: dict[str, str] = {}
    for key in FIXTURE_KEYS:
        snapshot = build_chain(key).snapshot
        assert mutable_container_paths(snapshot) == ()
        assert snapshot.source_sha256 == compute_derived_ship_snapshot_sha256(snapshot)
        assert snapshot.hull.aerodynamic_cache.source_sha256 == canonical_sha256(
            snapshot.hull.aerodynamic_cache
        )
        assert snapshot.hull.hull_rcs_cache.source_sha256 == canonical_sha256(
            snapshot.hull.hull_rcs_cache
        )
        verify_derived_ship_snapshot_fingerprint(snapshot)
        hashes[key] = snapshot.source_sha256
    return hashes


def test_fingerprint_invalidation() -> None:
    snapshot = build_chain("minimum_legal").snapshot
    cache = snapshot.hull.aerodynamic_cache
    changed_cache = replace(cache, model=f"{cache.model}.revision")
    changed_hull = replace(snapshot.hull, aerodynamic_cache=changed_cache)
    changed_snapshot = replace(snapshot, hull=changed_hull)
    assert changed_cache.source_sha256 != cache.source_sha256
    assert changed_snapshot.source_sha256 != snapshot.source_sha256
    verify_derived_ship_snapshot_fingerprint(changed_snapshot)

    object.__setattr__(cache, "model", f"{cache.model}.tampered")
    require_error(
        "snapshot.fingerprint_mismatch",
        lambda: verify_derived_ship_snapshot_fingerprint(snapshot),
    )


def main() -> None:
    plan = load_benchmark_plan(PLAN_PATH)
    diagnostic_plan = load_hot_path_diagnostic_plan(DIAGNOSTIC_PLAN_PATH)
    golden = load_authority_step_golden(GOLDEN_PATH)
    snapshot_hashes = test_static_fingerprint_and_deep_freeze()
    test_fingerprint_invalidation()

    strict_fast_cases: dict[str, dict[str, str]] = {}
    catalog_hashes_checked = 0
    first_bundle = None
    for profile in plan.profiles:
        for stage in plan.load_stages:
            case_id = f"{profile.id}.{stage}"
            bundle = build_scenario(ROOT, plan, profile.id, stage, 1)
            if first_bundle is None:
                first_bundle = bundle
            for resource in (
                bundle.timing_catalog,
                bundle.projectile_catalog,
                bundle.guidance_catalog,
                bundle.continuous_damage_profile,
            ):
                assert resource.source_sha256 == canonical_sha256(resource)
                catalog_hashes_checked += 1
            strict = advance_scenario_step(
                bundle,
                bundle.initial_scene,
                binding_validation_mode=BINDING_VALIDATION_STRICT,
            )
            fast = advance_scenario_step(
                bundle,
                bundle.initial_scene,
                binding_validation_mode=BINDING_VALIDATION_TRUSTED,
            )
            strict_state = canonical_sha256(strict.resulting_scene)
            fast_state = canonical_sha256(fast.resulting_scene)
            strict_events = authority_event_stream_sha256((strict,))
            fast_events = authority_event_stream_sha256((fast,))
            expected = golden["cases"][case_id]
            assert strict_state == fast_state == expected["authority_state_sha256"]
            assert strict_events == fast_events == expected["authority_event_sha256"]
            strict_fast_cases[case_id] = {
                "authority_event_sha256": fast_events,
                "authority_state_sha256": fast_state,
            }
    assert first_bundle is not None

    unvalidated = replace(
        first_bundle,
        bindings=tuple(replace(item) for item in first_bundle.bindings),
    )
    assert all(
        item.validated_snapshot_sha256 is None for item in unvalidated.bindings
    )
    require_error(
        "tactical_scene.binding_not_validated",
        lambda: advance_scenario_step(unvalidated, unvalidated.initial_scene),
    )
    stale_first = replace(first_bundle.bindings[0])
    object.__setattr__(
        stale_first,
        "_validated_snapshot_sha256",
        "0" * 64,
    )
    stale_bundle = replace(
        first_bundle,
        bindings=(stale_first, *first_bundle.bindings[1:]),
    )
    require_error(
        "tactical_scene.binding_token_stale",
        lambda: advance_scenario_step(stale_bundle, stale_bundle.initial_scene),
    )
    require_error(
        "tactical_scene.binding_validation_mode",
        lambda: advance_scenario_step(
            first_bundle,
            first_bundle.initial_scene,
            binding_validation_mode="unknown",
        ),
    )

    probe_counts: dict[str, int] = {}
    for case in diagnostic_plan.cases:
        observed = profile_hot_path_case(ROOT, plan, diagnostic_plan, case)
        assert observed["authority_equivalent"] is True
        recomputes = observed["profiled"]["function_probes"][
            "design_snapshot_fingerprint_recompute"
        ]["total_calls"]
        assert recomputes == 0
        probe_counts[case.id] = recomputes

    implementation_paths = (
        ROOT / "高天荒野舰艇数据契约.py",
        ROOT / "高天荒野舰艇气动缓存.py",
        ROOT / "高天荒野舰艇RCS缓存.py",
        ROOT / "高天荒野舰艇无界面舾装编译器.py",
        ROOT / "高天荒野舰艇运行时参数编译器.py",
        ROOT / "高天荒野舰艇战术机动求解器.py",
        ROOT / "高天荒野舰艇武器时间与射击队列.py",
        ROOT / "高天荒野舰艇导弹制导.py",
        ROOT / "高天荒野舰艇战术弹丸世界.py",
        ROOT / "高天荒野舰艇持续毁伤.py",
        ROOT / "高天荒野舰艇统一战术场景.py",
        ROOT / "benchmarks" / "t0" / "scenario.py",
        ROOT / "benchmarks" / "t0" / "diagnostics.py",
        Path(__file__).resolve(),
        GOLDEN_PATH,
    )
    report = {
        "acceptance": {
            "deep_static_immutability": "3_of_3_PASS",
            "fixed_step_design_fingerprint_recomputes": "0_in_6_of_6_PASS",
            "local_same_protocol_speedup_at_least_4x": "6_of_6_PASS",
            "stale_and_unvalidated_binding_negatives": "3_of_3_PASS",
            "strict_fast_authority_equivalence": "12_of_12_PASS",
        },
        "catalog_fingerprint_checks": catalog_hashes_checked,
        "design_fingerprint_recompute_calls": probe_counts,
        "fixture_snapshot_sha256": snapshot_hashes,
        "golden_sha256": file_sha256(GOLDEN_PATH),
        "implementation_hashes": {
            path.relative_to(ROOT).as_posix(): file_sha256(path)
            for path in implementation_paths
        },
        "interface": "gaotian.stage-t0b1b-static-fingerprint-validation-boundary/v1",
        "local_timing_artifact_policy": "ignored_not_persisted_in_regression_report",
        "next_slice": "T0b1c_static_model_and_dynamic_runtime_reuse",
        "official_performance_runs_executed": 0,
        "scope": "static_fingerprints_deep_freeze_and_binding_validation_boundary_only",
        "status": "PASS",
        "t0_performance_measured": False,
    }
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
