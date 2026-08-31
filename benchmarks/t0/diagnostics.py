"""T0b.1a 权威热路径短程诊断、调用计数与黄金结果复核。"""

from __future__ import annotations

import cProfile
from dataclasses import dataclass
import json
from pathlib import Path
import pstats
from time import perf_counter_ns
from typing import Any, Mapping

from 高天荒野舰艇数据契约 import canonical_sha256 as authority_sha256

from .contracts import (
    BenchmarkContractError,
    BenchmarkPlan,
    canonical_sha256,
)
from .headless import authority_event_stream_sha256
from .metadata import collect_environment_metadata, file_sha256
from .metrics import summarize_samples
from .scenario import advance_scenario_step, build_scenario, scene_entity_counts


DIAGNOSTIC_PLAN_INTERFACE = "gaotian.t0-hot-path-diagnostic-plan/v1"
DIAGNOSTIC_RESULT_INTERFACE = "gaotian.t0-hot-path-diagnostic/v1"
GOLDEN_INTERFACE = "gaotian.t0-authority-step-golden/v1"
DIAGNOSTIC_PLAN_KEYS = {
    "cases",
    "fixed_step_budget_ms",
    "interface",
    "measurement_policy",
    "official_performance",
    "profiled_steps",
    "repetition",
    "status",
    "top_hotspot_count",
    "unprofiled_steps",
}
MEASUREMENT_POLICY_KEYS = {
    "do_not_compare_as_official_t0",
    "profile_cumulative_times_overlap",
    "profiled_and_unprofiled_start_from_same_state",
}
CASE_KEYS = {"load_stage", "profile"}
GOLDEN_KEYS = {
    "cases",
    "interface",
    "plan_sha256",
    "repetition",
    "step_count",
}
GOLDEN_CASE_KEYS = {
    "authority_event_sha256",
    "authority_state_sha256",
    "event_counts",
    "initial_scene_sha256",
    "input_stream_sha256",
    "resource_hash_count",
    "resource_manifest_sha256",
    "resulting_entity_counts",
}
EVENT_FIELDS = (
    "ammunition_cookoff_events",
    "continuous_damage_events",
    "crew_casualty_events",
    "crew_evacuation_events",
    "engagement_events",
    "expired_events",
    "fire_control_support_events",
    "fire_propagation_events",
    "generated_guidance_fact_events",
    "guidance_events",
    "impact_events",
    "lifecycle_events",
    "radar_emission_events",
    "sensor_observation_events",
    "spawned_projectiles",
    "weapon_events",
)


@dataclass(frozen=True)
class HotPathDiagnosticCase:
    profile: str
    load_stage: str

    @property
    def id(self) -> str:
        return f"{self.profile}.{self.load_stage}"


@dataclass(frozen=True)
class HotPathDiagnosticPlan:
    cases: tuple[HotPathDiagnosticCase, ...]
    fixed_step_budget_ms: float
    profiled_steps: int
    repetition: int
    source_path: Path
    source_sha256: str
    top_hotspot_count: int
    unprofiled_steps: int


@dataclass(frozen=True)
class FunctionProbe:
    id: str
    phase: str
    file_name: str
    function_name: str


FUNCTION_PROBES = (
    FunctionProbe("canonical_sha256", "integrity_validation", "高天荒野舰艇数据契约.py", "canonical_sha256"),
    FunctionProbe("canonical_json", "integrity_validation", "高天荒野舰艇数据契约.py", "canonical_json"),
    FunctionProbe("design_snapshot_source_sha256", "integrity_validation", "高天荒野舰艇无界面舾装编译器.py", "source_sha256"),
    FunctionProbe("design_snapshot_fingerprint_recompute", "integrity_validation", "高天荒野舰艇无界面舾装编译器.py", "compute_derived_ship_snapshot_sha256"),
    FunctionProbe("validate_internal_state", "integrity_validation", "高天荒野舰艇统一战术场景.py", "_validate_internal_state"),
    FunctionProbe("validate_bindings", "integrity_validation", "高天荒野舰艇统一战术场景.py", "_validate_bindings"),
    FunctionProbe("validate_instance_current_design", "integrity_validation", "高天荒野舰艇实例设计状态.py", "validate_instance_current_design"),
    FunctionProbe("compile_runtime_ship_parameters", "runtime_compile", "高天荒野舰艇运行时参数编译器.py", "compile_runtime_ship_parameters"),
    FunctionProbe("runtime_cache_resolve", "runtime_compile", "高天荒野舰艇运行时参数编译器.py", "resolve"),
    FunctionProbe("runtime_state_revision", "runtime_compile", "高天荒野舰艇运行时参数编译器.py", "runtime_state_revision"),
    FunctionProbe("derive_tactical_ship_lifecycle", "lifecycle", "高天荒野舰艇统一战术场景.py", "derive_tactical_ship_lifecycle"),
    FunctionProbe("refresh_lifecycle_boundary", "lifecycle", "高天荒野舰艇统一战术场景.py", "refresh_lifecycle_boundary"),
    FunctionProbe("advance_weapon_timeline", "weapon_timeline", "高天荒野舰艇武器时间与射击队列.py", "advance_weapon_timeline"),
    FunctionProbe("resolve_weapon_fire", "weapon_timeline", "高天荒野舰艇弹药与武器动作结算器.py", "resolve_weapon_fire"),
    FunctionProbe("resolve_weapon_reload", "weapon_timeline", "高天荒野舰艇弹药与武器动作结算器.py", "resolve_weapon_reload"),
    FunctionProbe("build_tactical_ship_model", "motion", "高天荒野舰艇战术机动求解器.py", "build_tactical_ship_model"),
    FunctionProbe("build_tactical_ship_static_model", "motion", "高天荒野舰艇战术机动求解器.py", "build_tactical_ship_static_model"),
    FunctionProbe("bind_tactical_ship_model", "motion", "高天荒野舰艇战术机动求解器.py", "bind_tactical_ship_model"),
    FunctionProbe("integrate_tactical_step", "motion", "高天荒野舰艇战术机动求解器.py", "integrate_tactical_step"),
    FunctionProbe("spawn_projectile_from_weapon_event", "projectile", "高天荒野舰艇战术弹丸世界.py", "spawn_projectile_from_weapon_event"),
    FunctionProbe("spawn_projectiles_from_weapon_events", "projectile", "高天荒野舰艇战术弹丸世界.py", "spawn_projectiles_from_weapon_events"),
    FunctionProbe("compile_projectile_target_geometry", "projectile", "高天荒野舰艇战术弹丸世界.py", "compile_projectile_target_geometry"),
    FunctionProbe("projectile_geometry_hit", "projectile", "高天荒野舰艇战术弹丸世界.py", "_geometry_hit"),
    FunctionProbe("advance_projectile_world", "projectile", "高天荒野舰艇战术弹丸世界.py", "advance_projectile_world"),
    FunctionProbe("advance_missile_guidance_step", "projectile", "高天荒野舰艇导弹制导.py", "advance_missile_guidance_step"),
    FunctionProbe("apply_damage_control_directives", "damage", "高天荒野舰艇持续毁伤.py", "apply_damage_control_directives"),
    FunctionProbe("advance_continuous_damage", "damage", "高天荒野舰艇持续毁伤.py", "advance_continuous_damage"),
    FunctionProbe("apply_secondary_damage_outcomes", "damage", "高天荒野舰艇二次毁伤.py", "apply_secondary_damage_outcomes"),
)


def _require_exact_keys(value: Any, expected: set[str], path: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise BenchmarkContractError("diagnostic.type_object", path, "必须是对象")
    actual = set(value)
    if actual != expected:
        raise BenchmarkContractError(
            "diagnostic.object_keys",
            path,
            f"缺少 {sorted(expected - actual)}，未知 {sorted(actual - expected)}",
        )
    return value


def _positive_int(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise BenchmarkContractError("diagnostic.positive_integer", path, str(value))
    return value


def load_hot_path_diagnostic_plan(path: str | Path) -> HotPathDiagnosticPlan:
    source = Path(path).resolve()
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise BenchmarkContractError("diagnostic.read", str(source), str(error)) from error
    value = _require_exact_keys(raw, DIAGNOSTIC_PLAN_KEYS, "$")
    if value["interface"] != DIAGNOSTIC_PLAN_INTERFACE:
        raise BenchmarkContractError("diagnostic.interface", "$.interface", str(value["interface"]))
    if value["status"] != "diagnostic_only" or value["official_performance"] is not False:
        raise BenchmarkContractError("diagnostic.scope", "$", "短跑协议不得声明正式性能")
    budget = value["fixed_step_budget_ms"]
    if isinstance(budget, bool) or not isinstance(budget, (int, float)) or budget <= 0:
        raise BenchmarkContractError("diagnostic.fixed_step_budget", "$.fixed_step_budget_ms", str(budget))
    policy = _require_exact_keys(
        value["measurement_policy"], MEASUREMENT_POLICY_KEYS, "$.measurement_policy"
    )
    if any(policy[key] is not True for key in MEASUREMENT_POLICY_KEYS):
        raise BenchmarkContractError("diagnostic.measurement_policy", "$.measurement_policy", "策略布尔值必须全部为 true")
    cases_value = value["cases"]
    if not isinstance(cases_value, list) or not cases_value:
        raise BenchmarkContractError("diagnostic.cases", "$.cases", "至少需要一个诊断场景")
    cases = []
    for index, raw_case in enumerate(cases_value):
        item = _require_exact_keys(raw_case, CASE_KEYS, f"$.cases[{index}]")
        if not isinstance(item["profile"], str) or not item["profile"]:
            raise BenchmarkContractError("diagnostic.profile", f"$.cases[{index}].profile", str(item["profile"]))
        if not isinstance(item["load_stage"], str) or not item["load_stage"]:
            raise BenchmarkContractError("diagnostic.load_stage", f"$.cases[{index}].load_stage", str(item["load_stage"]))
        cases.append(HotPathDiagnosticCase(item["profile"], item["load_stage"]))
    if len({item.id for item in cases}) != len(cases):
        raise BenchmarkContractError("diagnostic.case_duplicate", "$.cases", "诊断场景不得重复")
    profiled_steps = _positive_int(value["profiled_steps"], "$.profiled_steps")
    unprofiled_steps = _positive_int(value["unprofiled_steps"], "$.unprofiled_steps")
    if profiled_steps != unprofiled_steps:
        raise BenchmarkContractError(
            "diagnostic.step_count_mismatch",
            "$",
            "剖析与非剖析运行必须从同一初态推进相同步数",
        )
    return HotPathDiagnosticPlan(
        tuple(cases),
        float(budget),
        profiled_steps,
        _positive_int(value["repetition"], "$.repetition"),
        source,
        file_sha256(source),
        _positive_int(value["top_hotspot_count"], "$.top_hotspot_count"),
        unprofiled_steps,
    )


def _event_counts(resolution: Any) -> dict[str, int]:
    return {
        key: count
        for key in EVENT_FIELDS
        if (count := len(getattr(resolution, key))) > 0
    }


def capture_authority_step_golden(
    root: str | Path,
    plan: BenchmarkPlan,
    *,
    repetition: int = 1,
) -> dict[str, Any]:
    base = Path(root).resolve()
    cases: dict[str, Any] = {}
    for profile in plan.profiles:
        for stage in plan.load_stages:
            bundle = build_scenario(base, plan, profile.id, stage, repetition)
            resolution = advance_scenario_step(bundle, bundle.initial_scene)
            cases[f"{profile.id}.{stage}"] = {
                "authority_event_sha256": authority_event_stream_sha256((resolution,)),
                "authority_state_sha256": authority_sha256(resolution.resulting_scene),
                "event_counts": _event_counts(resolution),
                "initial_scene_sha256": authority_sha256(bundle.initial_scene),
                "input_stream_sha256": bundle.input_stream_sha256,
                "resource_hash_count": len(bundle.fixture_resource_hashes),
                "resource_manifest_sha256": canonical_sha256(bundle.fixture_resource_hashes),
                "resulting_entity_counts": scene_entity_counts(resolution.resulting_scene),
            }
    return {
        "cases": cases,
        "interface": GOLDEN_INTERFACE,
        "plan_sha256": plan.source_sha256,
        "repetition": repetition,
        "step_count": 1,
    }


def load_authority_step_golden(path: str | Path) -> dict[str, Any]:
    source = Path(path).resolve()
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise BenchmarkContractError("diagnostic.golden_read", str(source), str(error)) from error
    value = _require_exact_keys(raw, GOLDEN_KEYS, "$")
    if value["interface"] != GOLDEN_INTERFACE or value["step_count"] != 1:
        raise BenchmarkContractError("diagnostic.golden_interface", "$", "黄金结果接口或步数不匹配")
    if not isinstance(value["cases"], dict) or not value["cases"]:
        raise BenchmarkContractError("diagnostic.golden_cases", "$.cases", "黄金结果不得为空")
    for case_id, raw_case in value["cases"].items():
        _require_exact_keys(raw_case, GOLDEN_CASE_KEYS, f"$.cases.{case_id}")
    return raw


def verify_authority_step_golden(
    root: str | Path,
    plan: BenchmarkPlan,
    golden_path: str | Path,
) -> dict[str, Any]:
    expected = load_authority_step_golden(golden_path)
    actual = capture_authority_step_golden(root, plan, repetition=int(expected["repetition"]))
    if actual != expected:
        differing = sorted(
            key
            for key in set(actual["cases"]) | set(expected["cases"])
            if actual["cases"].get(key) != expected["cases"].get(key)
        )
        raise BenchmarkContractError(
            "diagnostic.golden_mismatch",
            "$.cases",
            f"权威单步黄金结果漂移：{differing}",
        )
    return actual


def _run_steps(bundle: Any, step_count: int) -> tuple[Any, tuple[Any, ...], list[float]]:
    scene = bundle.initial_scene
    resolutions = []
    elapsed_ms = []
    for _ in range(step_count):
        started = perf_counter_ns()
        resolution = advance_scenario_step(bundle, scene)
        elapsed_ms.append((perf_counter_ns() - started) / 1_000_000.0)
        resolutions.append(resolution)
        scene = resolution.resulting_scene
    return scene, tuple(resolutions), elapsed_ms


def _profile_function_stats(profiler: cProfile.Profile) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    stats = pstats.Stats(profiler).stats
    probes: dict[str, Any] = {}
    for probe in FUNCTION_PROBES:
        matching = [
            value
            for (filename, _line, function_name), value in stats.items()
            if Path(filename).name == probe.file_name and function_name == probe.function_name
        ]
        probes[probe.id] = {
            "cumulative_seconds": sum(value[3] for value in matching),
            "phase": probe.phase,
            "primitive_calls": sum(value[0] for value in matching),
            "self_seconds": sum(value[2] for value in matching),
            "total_calls": sum(value[1] for value in matching),
        }
    hotspots = []
    for (filename, line, function_name), value in sorted(
        stats.items(), key=lambda item: item[1][3], reverse=True
    ):
        hotspots.append(
            {
                "cumulative_seconds": value[3],
                "function": f"{Path(filename).name}:{line}({function_name})",
                "primitive_calls": value[0],
                "self_seconds": value[2],
                "total_calls": value[1],
            }
        )
    return probes, hotspots


def profile_hot_path_case(
    root: str | Path,
    benchmark_plan: BenchmarkPlan,
    diagnostic_plan: HotPathDiagnosticPlan,
    case: HotPathDiagnosticCase,
) -> dict[str, Any]:
    bundle = build_scenario(
        Path(root).resolve(),
        benchmark_plan,
        case.profile,
        case.load_stage,
        diagnostic_plan.repetition,
    )
    unprofiled_scene, unprofiled_resolutions, unprofiled_ms = _run_steps(
        bundle, diagnostic_plan.unprofiled_steps
    )
    profiler = cProfile.Profile()
    profiled_started = perf_counter_ns()
    profiler.enable()
    profiled_scene, profiled_resolutions, _ = _run_steps(
        bundle, diagnostic_plan.profiled_steps
    )
    profiler.disable()
    profiled_wall_ms = (perf_counter_ns() - profiled_started) / 1_000_000.0
    probes, hotspots = _profile_function_stats(profiler)

    result_payload_started = perf_counter_ns()
    unprofiled_resolutions[-1].to_dict()
    result_materialization_ms = (perf_counter_ns() - result_payload_started) / 1_000_000.0
    event_digest_started = perf_counter_ns()
    unprofiled_event_sha = authority_event_stream_sha256(unprofiled_resolutions)
    event_digest_ms = (perf_counter_ns() - event_digest_started) / 1_000_000.0
    profiled_event_sha = authority_event_stream_sha256(profiled_resolutions)
    unprofiled_state_sha = authority_sha256(unprofiled_scene)
    profiled_state_sha = authority_sha256(profiled_scene)
    equivalent = (
        diagnostic_plan.unprofiled_steps == diagnostic_plan.profiled_steps
        and unprofiled_state_sha == profiled_state_sha
        and unprofiled_event_sha == profiled_event_sha
    )
    return {
        "authority_equivalent": equivalent,
        "case": case.id,
        "estimated_observer_overhead_ms": max(0.0, profiled_wall_ms - sum(unprofiled_ms)),
        "event_digest_ms": event_digest_ms,
        "initial_scene_sha256": authority_sha256(bundle.initial_scene),
        "input_stream_sha256": bundle.input_stream_sha256,
        "profiled": {
            "authority_event_sha256": profiled_event_sha,
            "authority_state_sha256": profiled_state_sha,
            "function_probes": probes,
            "step_count": diagnostic_plan.profiled_steps,
            "top_hotspots": hotspots[: diagnostic_plan.top_hotspot_count],
            "wall_ms": profiled_wall_ms,
        },
        "result_materialization_ms": result_materialization_ms,
        "unprofiled": {
            "authority_event_sha256": unprofiled_event_sha,
            "authority_state_sha256": unprofiled_state_sha,
            "fixed_step_ms": summarize_samples(unprofiled_ms).to_dict(),
            "step_count": diagnostic_plan.unprofiled_steps,
        },
    }


def run_hot_path_diagnostic(
    root: str | Path,
    benchmark_plan: BenchmarkPlan,
    diagnostic_plan: HotPathDiagnosticPlan,
    *,
    command: str,
    profile: str | None = None,
    load_stage: str | None = None,
) -> dict[str, Any]:
    selected = tuple(
        item
        for item in diagnostic_plan.cases
        if (profile is None or item.profile == profile)
        and (load_stage is None or item.load_stage == load_stage)
    )
    if not selected:
        raise BenchmarkContractError("diagnostic.case_missing", "$.cases", f"{profile}.{load_stage}")
    environment = collect_environment_metadata(Path(root).resolve(), command=command)
    results = [
        profile_hot_path_case(root, benchmark_plan, diagnostic_plan, item)
        for item in selected
    ]
    return {
        "cases": results,
        "diagnostic_plan_sha256": diagnostic_plan.source_sha256,
        "environment_metadata": environment,
        "fixed_step_budget_ms": diagnostic_plan.fixed_step_budget_ms,
        "interface": DIAGNOSTIC_RESULT_INTERFACE,
        "official_performance_runs_executed": 0,
        "observer_default_enabled": False,
        "status": "PASS" if all(item["authority_equivalent"] for item in results) else "FAIL",
        "t0_performance_measured": False,
    }
