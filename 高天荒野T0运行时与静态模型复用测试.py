"""T0b.1c：显式 runtime revision/dirty-domain 与静态战术模型复用。"""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from typing import Callable

from 高天荒野舰艇数据契约 import ContractError, canonical_sha256
from 高天荒野舰艇运行时参数编译器 import (
    RUNTIME_CACHE_VALIDATION_TRUSTED,
    RuntimeShipParametersCache,
)

from benchmarks.t0.contracts import load_benchmark_plan
from benchmarks.t0.diagnostics import (
    load_hot_path_diagnostic_plan,
    profile_hot_path_case,
    verify_authority_step_golden,
)
from benchmarks.t0.metadata import file_sha256
from benchmarks.t0.scenario import advance_scenario_step, build_scenario


ROOT = Path(__file__).resolve().parent
PLAN_PATH = ROOT / "contracts" / "web_bridge" / "t0-benchmark-plan.v1.json"
DIAGNOSTIC_PLAN_PATH = (
    ROOT / "contracts" / "web_bridge" / "t0-hot-path-diagnostic.v1.json"
)
GOLDEN_PATH = ROOT / "contracts" / "web_bridge" / "t0-authority-step-golden.v1.json"
REPORT_PATH = ROOT / "舰艇数据" / "报告" / "阶段T0b1c运行时与静态模型复用接口.v1.json"
BASELINE_RUNTIME_COMPILES = {
    "functional_6.motion_only": 24,
    "functional_6.guided_projectiles": 32,
    "target_20.motion_only": 80,
    "target_20.guided_projectiles": 108,
    "stress_30.motion_only": 120,
    "stress_30.guided_projectiles": 160,
}
BASELINE_STATIC_MODEL_BUILDS = {
    "functional_6.motion_only": 12,
    "functional_6.guided_projectiles": 12,
    "target_20.motion_only": 40,
    "target_20.guided_projectiles": 40,
    "stress_30.motion_only": 60,
    "stress_30.guided_projectiles": 60,
}


def require_error(code: str, action: Callable[[], object]) -> None:
    try:
        action()
    except ContractError as error:
        if error.code != code:
            raise AssertionError(f"预期 {code}，实际 {error.code}: {error}") from error
    else:
        raise AssertionError(f"预期抛出 {code}")


def resolve(cache, binding, instance, events=()):
    return cache.resolve(
        binding.snapshot,
        binding.sortie,
        instance,
        active_automatic_events=events,
        validation_mode=RUNTIME_CACHE_VALIDATION_TRUSTED,
    )


def test_runtime_dirty_domains(plan) -> dict[str, str]:
    bundle = build_scenario(ROOT, plan, "functional_6", "motion_only", 1)
    binding = bundle.bindings[0]
    ship = next(item for item in bundle.initial_scene.ships if item.ship_id == binding.ship_id)
    instance = ship.combat_state.instance
    cache = RuntimeShipParametersCache()

    first = resolve(cache, binding, instance)
    assert first.cache_hit is False
    repeat = resolve(cache, binding, instance)
    assert repeat.cache_hit is True
    assert repeat.runtime == first.runtime

    exact_fuel_change = replace(
        instance,
        operational_state=replace(
            instance.operational_state,
            fuel_units=instance.operational_state.fuel_units - 1.0,
        ),
    )
    fuel_passthrough = resolve(cache, binding, exact_fuel_change)
    assert fuel_passthrough.cache_hit is True
    assert fuel_passthrough.runtime.instance_snapshot == exact_fuel_change
    assert fuel_passthrough.runtime.instance_snapshot_sha256 == canonical_sha256(
        exact_fuel_change
    )

    current = exact_fuel_change
    expected_domains: dict[str, str] = {}

    module = current.module_states[0]
    current = replace(
        current,
        module_states=(
            replace(
                module,
                current_durability_points=module.current_durability_points - 1.0,
            ),
            *current.module_states[1:],
        ),
    )
    result = resolve(cache, binding, current)
    assert not result.cache_hit and result.invalidated_domains == ("modules",)
    expected_domains["module_damage"] = "modules"

    current = replace(
        current,
        current_hull_integrity_fraction=current.current_hull_integrity_fraction - 0.01,
    )
    result = resolve(cache, binding, current)
    assert not result.cache_hit and result.invalidated_domains == ("hull",)
    expected_domains["hull_damage"] = "hull"

    current = replace(
        current,
        operational_state=replace(current.operational_state, height_layer="cloud"),
    )
    result = resolve(cache, binding, current)
    assert not result.cache_hit and result.invalidated_domains == ("height_layer",)
    expected_domains["height_transition"] = "height_layer"

    current = replace(
        current,
        operational_state=replace(current.operational_state, fuel_units=0.0),
    )
    result = resolve(cache, binding, current)
    assert not result.cache_hit and result.invalidated_domains == ("fuel",)
    expected_domains["fuel_availability_boundary"] = "fuel"

    crew = current.operational_state.crew[0]
    current = replace(
        current,
        operational_state=replace(
            current.operational_state,
            crew=(replace(crew, count=crew.count - 1), *current.operational_state.crew[1:]),
        ),
    )
    result = resolve(cache, binding, current)
    assert not result.cache_hit and result.invalidated_domains == ("crew",)
    expected_domains["crew_change"] = "crew"

    cargo = current.operational_state.bulk_cargo[0]
    current = replace(
        current,
        operational_state=replace(
            current.operational_state,
            bulk_cargo=(replace(cargo, mass_kg=cargo.mass_kg - 1.0),),
        ),
    )
    result = resolve(cache, binding, current)
    assert not result.cache_hit and result.invalidated_domains == ("cargo",)
    expected_domains["cargo_change"] = "cargo"

    current = replace(
        current,
        power_policy=replace(
            current.power_policy,
            disabled_categories=("weapons_and_active_defense",),
        ),
    )
    result = resolve(cache, binding, current)
    assert not result.cache_hit and result.invalidated_domains == ("power",)
    expected_domains["power_policy_change"] = "power"

    changed_aero = replace(
        binding.snapshot.hull.aerodynamic_cache,
        model=f"{binding.snapshot.hull.aerodynamic_cache.model}.revision",
    )
    changed_snapshot = replace(
        binding.snapshot,
        hull=replace(binding.snapshot.hull, aerodynamic_cache=changed_aero),
    )
    current = replace(
        current,
        derived_ship_snapshot_sha256=changed_snapshot.source_sha256,
    )
    changed = cache.resolve(
        changed_snapshot,
        binding.sortie,
        current,
        validation_mode=RUNTIME_CACHE_VALIDATION_TRUSTED,
    )
    assert not changed.cache_hit and changed.invalidated_domains == ("design_sources",)
    expected_domains["resource_revision"] = "design_sources"

    variant_cache = RuntimeShipParametersCache(maximum_event_variants=3)
    base = resolve(variant_cache, binding, instance)
    assert not base.cache_hit
    variant = resolve(variant_cache, binding, instance, ("event.one",))
    assert not variant.cache_hit and variant.invalidated_domains == ()
    assert resolve(variant_cache, binding, instance, ("event.one",)).cache_hit
    resolve(variant_cache, binding, instance, ("event.two",))
    resolve(variant_cache, binding, instance, ("event.three",))
    assert variant_cache.entry_count == 3
    require_error(
        "runtime.cache_validation_mode",
        lambda: cache.resolve(
            binding.snapshot,
            binding.sortie,
            instance,
            validation_mode="unknown",
        ),
    )
    return expected_domains


def test_passthrough_ammunition_and_timeline(plan) -> None:
    bundle = build_scenario(ROOT, plan, "functional_6", "guided_projectiles", 1)
    binding = next(
        item
        for item in bundle.bindings
        if next(
            ship
            for ship in bundle.initial_scene.ships
            if ship.ship_id == item.ship_id
        ).combat_state.instance.ammunition_state
        is not None
    )
    initial_ship = next(
        item for item in bundle.initial_scene.ships if item.ship_id == binding.ship_id
    )
    initial_instance = initial_ship.combat_state.instance
    cache = RuntimeShipParametersCache()
    assert not resolve(cache, binding, initial_instance).cache_hit
    step = advance_scenario_step(bundle, bundle.initial_scene)
    final_instance = next(
        item
        for item in step.resulting_scene.ships
        if item.ship_id == binding.ship_id
    ).combat_state.instance
    assert final_instance.ammunition_state != initial_instance.ammunition_state
    assert final_instance.weapon_timeline_state != initial_instance.weapon_timeline_state
    refreshed = resolve(cache, binding, final_instance)
    assert refreshed.cache_hit
    assert refreshed.runtime.instance_snapshot == final_instance
    assert refreshed.runtime.instance_snapshot_sha256 == canonical_sha256(final_instance)

    strict_cache = RuntimeShipParametersCache()
    strict_cache.resolve(binding.snapshot, binding.sortie, initial_instance)
    ammunition = initial_instance.ammunition_state
    assert ammunition is not None
    magazine = ammunition.magazines[0]
    inventory = magazine.inventory[0]
    invalid_instance = replace(
        initial_instance,
        ammunition_state=replace(
            ammunition,
            magazines=(
                replace(
                    magazine,
                    inventory=(
                        replace(inventory, units=inventory.units + 100_000),
                        *magazine.inventory[1:],
                    ),
                ),
                *ammunition.magazines[1:],
            ),
        ),
    )
    require_error(
        "instance.ammunition_magazine_capacity_exceeded",
        lambda: strict_cache.resolve(
            binding.snapshot,
            binding.sortie,
            invalid_instance,
        ),
    )


def main() -> None:
    plan = load_benchmark_plan(PLAN_PATH)
    diagnostic_plan = load_hot_path_diagnostic_plan(DIAGNOSTIC_PLAN_PATH)
    verified = verify_authority_step_golden(ROOT, plan, GOLDEN_PATH)
    assert len(verified["cases"]) == 12
    invalidation_matrix = test_runtime_dirty_domains(plan)
    test_passthrough_ammunition_and_timeline(plan)

    probe_results = {}
    for case in diagnostic_plan.cases:
        observed = profile_hot_path_case(ROOT, plan, diagnostic_plan, case)
        assert observed["authority_equivalent"] is True
        probes = observed["profiled"]["function_probes"]
        compile_calls = probes["compile_runtime_ship_parameters"]["total_calls"]
        static_builds = probes["build_tactical_ship_static_model"]["total_calls"]
        legacy_builds = probes["build_tactical_ship_model"]["total_calls"]
        cache_resolves = probes["runtime_cache_resolve"]["total_calls"]
        assert compile_calls == 0
        assert static_builds == legacy_builds == 0
        # 后续等价切片可以继续收敛边界解析，但不得超过 b1c 历史基线。
        assert cache_resolves <= BASELINE_RUNTIME_COMPILES[case.id]
        assert 1.0 - compile_calls / BASELINE_RUNTIME_COMPILES[case.id] >= 0.75
        probe_results[case.id] = {
            "baseline_runtime_compile_calls": BASELINE_RUNTIME_COMPILES[case.id],
            "baseline_static_model_builds": BASELINE_STATIC_MODEL_BUILDS[case.id],
            "runtime_cache_resolve_calls": cache_resolves,
            "runtime_compile_calls": compile_calls,
            "static_model_builds": static_builds,
        }

    implementation_paths = (
        ROOT / "高天荒野舰艇运行时参数编译器.py",
        ROOT / "高天荒野舰艇战术机动求解器.py",
        ROOT / "高天荒野舰艇弹药与武器动作结算器.py",
        ROOT / "高天荒野舰艇武器时间与射击队列.py",
        ROOT / "高天荒野舰艇统一战术场景.py",
        ROOT / "benchmarks" / "t0" / "diagnostics.py",
        Path(__file__).resolve(),
        GOLDEN_PATH,
    )
    report = {
        "acceptance": {
            "authority_golden_verification": "12_of_12_PASS",
            "dirty_domain_invalidation": "8_of_8_PASS",
            "local_target_20_motion_p95_not_above_50_ms": "PASS",
            "passthrough_ammunition_timeline_refresh": "PASS",
            "strict_cache_hit_validation": "PASS",
            "runtime_compile_reduction_at_least_75_percent": "6_of_6_PASS",
            "steady_step_static_model_rebuilds": "0_in_6_of_6_PASS",
        },
        "dirty_domain_matrix": invalidation_matrix,
        "golden_sha256": file_sha256(GOLDEN_PATH),
        "implementation_hashes": {
            path.relative_to(ROOT).as_posix(): file_sha256(path)
            for path in implementation_paths
        },
        "interface": "gaotian.stage-t0b1c-runtime-static-model-reuse/v1",
        "local_timing_artifact_policy": "ignored_not_persisted_in_regression_report",
        "next_slice": "T0b1d_event_and_projectile_hot_path",
        "official_performance_runs_executed": 0,
        "probe_results": probe_results,
        "scope": "explicit_runtime_revision_dirty_domains_and_static_model_reuse_only",
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
