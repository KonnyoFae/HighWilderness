"""T0b.1f 短程诊断决策门。

该模块只比较同一机器元数据下的 10+60 步短跑，不产生正式 T0 成绩。
"""

from __future__ import annotations

import json
from math import isfinite
from pathlib import Path
from typing import Any, Mapping

from .contracts import BenchmarkContractError, BenchmarkPlan, canonical_sha256
from .diagnostics import (
    HotPathDiagnosticPlan,
    profile_hot_path_case,
)
from .headless import run_headless_baseline
from .metadata import collect_environment_metadata
from .scenario import build_scenario


SHORT_DECISION_INTERFACE = "gaotian.t0b1f-short-diagnostic-decision/v1"
SHORT_DECISION_WARMUP_STEPS = 10
SHORT_DECISION_MEASURED_STEPS = 60
SHORT_DECISION_REPETITIONS = 3
SHORT_DECISION_TARGET_PROFILE = "target_20"
SHORT_DECISION_STAGES = ("motion_only", "guided_projectiles")
SHORT_DECISION_CASE_COUNT = 6
SHORT_DECISION_RUN_COUNT = 18
MEMORY_GROWTH_ABSOLUTE_ALLOWANCE_BYTES = 64 * 1024 * 1024
MEMORY_GROWTH_RELATIVE_ALLOWANCE = 0.25

_TOP_LEVEL_KEYS = {
    "benchmark_plan_sha256",
    "cases",
    "decision",
    "diagnostic_plan_sha256",
    "environment_metadata",
    "environment_metadata_sha256",
    "interface",
    "official_performance_runs_executed",
    "protocol",
    "short_diagnostic_runs_executed",
    "status",
    "t0_performance_measured",
}
_CASE_KEYS = {
    "authority_event_sha256",
    "authority_repeatable",
    "authority_state_sha256",
    "case",
    "current_probe_total_calls",
    "historical_call_comparison",
    "hotspot_evidence",
    "initial_scene_sha256",
    "input_stream_sha256",
    "load_stage",
    "profile",
    "repetitions",
    "resource_bounded_within_short_gate",
    "top_hotspots",
    "worst_across_repetitions",
}
_REPETITION_KEYS = {
    "authority_event_sha256",
    "authority_state_sha256",
    "fixed_step",
    "initial_scene_sha256",
    "input_stream_sha256",
    "load_coverage",
    "observer_drain",
    "real_time_factor",
    "repetition",
    "resident_memory",
    "wall_time",
}
_PROTOCOL_KEYS = {
    "fixed_step_budget_ms",
    "measured_steps",
    "measurement_scope",
    "memory_growth_absolute_allowance_bytes",
    "memory_growth_relative_allowance",
    "repetitions",
    "warmup_steps",
}
_DECISION_KEYS = {
    "authority_repeatable",
    "next_slice",
    "reason",
    "resources_bounded_within_short_gate",
    "selected_branch",
    "status",
    "target_guided_all_realtime",
    "target_guided_realtime_factors",
    "target_motion_all_realtime",
    "target_motion_hotspot_supported",
    "target_motion_realtime_factors",
}
_EXPECTED_CASE_IDS = {
    f"{profile}.{stage}"
    for profile in ("functional_6", "target_20", "stress_30")
    for stage in SHORT_DECISION_STAGES
}


def _exact_keys(value: Any, expected: set[str], path: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise BenchmarkContractError("decision.type_object", path, "必须是对象")
    actual = set(value)
    if actual != expected:
        raise BenchmarkContractError(
            "decision.object_keys",
            path,
            f"缺少 {sorted(expected - actual)}，未知 {sorted(actual - expected)}",
        )
    return value


def _read_json(path: Path, code: str) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise BenchmarkContractError(code, str(path), str(error)) from error
    if not isinstance(value, dict):
        raise BenchmarkContractError(code, str(path), "必须是对象")
    return value


def _historical_call_baselines(root: Path) -> dict[str, dict[str, int]]:
    report = _read_json(
        root / "舰艇数据" / "报告" / "阶段T0b1c运行时与静态模型复用接口.v1.json",
        "decision.baseline_report",
    )
    try:
        probes = report["probe_results"]
    except KeyError as error:
        raise BenchmarkContractError(
            "decision.baseline_report", "$.probe_results", "缺少历史调用数"
        ) from error
    if not isinstance(probes, dict):
        raise BenchmarkContractError(
            "decision.baseline_report", "$.probe_results", "必须是对象"
        )
    result: dict[str, dict[str, int]] = {}
    for case_id, raw in probes.items():
        if not isinstance(case_id, str) or not isinstance(raw, dict):
            raise BenchmarkContractError(
                "decision.baseline_report", "$.probe_results", "场景记录无效"
            )
        try:
            result[case_id] = {
                "build_tactical_ship_static_model": int(
                    raw["baseline_static_model_builds"]
                ),
                "compile_runtime_ship_parameters": int(
                    raw["baseline_runtime_compile_calls"]
                ),
            }
        except (KeyError, TypeError, ValueError) as error:
            raise BenchmarkContractError(
                "decision.baseline_report",
                f"$.probe_results.{case_id}",
                "历史调用数字段无效",
            ) from error
    return result


def _metric(result: Mapping[str, Any], metric_id: str) -> Mapping[str, Any]:
    return result["run_result"]["metrics"][metric_id]


def _repetition_summary(repetition: int, result: Mapping[str, Any]) -> dict[str, Any]:
    execution = result["execution"]
    return {
        "authority_event_sha256": result["authority_event_sha256"],
        "authority_state_sha256": result["authority_state_sha256"],
        "fixed_step": dict(_metric(result, "fixed_step")),
        "initial_scene_sha256": result["initial_scene_sha256"],
        "input_stream_sha256": result["run_result"]["run_spec"][
            "input_stream_sha256"
        ],
        "load_coverage": dict(result["load_coverage"]),
        "observer_drain": dict(_metric(result, "observer_drain")),
        "real_time_factor": _metric(result, "real_time_factor")["mean"],
        "repetition": repetition,
        "resident_memory": dict(_metric(result, "resident_memory")),
        "wall_time": {
            "authoritative_advance_wall_s": execution[
                "authoritative_advance_wall_s"
            ],
            "measured_wall_s": execution["measured_wall_s"],
            "observer_drain_wall_s": execution["observer_drain_wall_s"],
        },
    }


def _finite_positive(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and isfinite(float(value))
        and float(value) > 0.0
    )


def _bounded_short_run(
    repetitions: list[dict[str, Any]], ram_bytes: object
) -> tuple[bool, dict[str, Any]]:
    memory_values = [item["resident_memory"]["maximum"] for item in repetitions]
    valid_memory = all(_finite_positive(value) for value in memory_values)
    memory_numbers = [float(value) for value in memory_values if _finite_positive(value)]
    memory_growth = (
        max(memory_numbers) - min(memory_numbers) if memory_numbers else None
    )
    allowance = (
        max(
            float(MEMORY_GROWTH_ABSOLUTE_ALLOWANCE_BYTES),
            min(memory_numbers) * MEMORY_GROWTH_RELATIVE_ALLOWANCE,
        )
        if memory_numbers
        else None
    )
    within_ram = (
        True
        if not _finite_positive(ram_bytes)
        else bool(memory_numbers)
        and max(memory_numbers) <= float(ram_bytes)
    )
    fixed_maxima = [item["fixed_step"]["maximum"] for item in repetitions]
    bounded = (
        len(repetitions) == SHORT_DECISION_REPETITIONS
        and all(item["resident_memory"]["sample_count"] == 1 for item in repetitions)
        and valid_memory
        and within_ram
        and memory_growth is not None
        and allowance is not None
        and memory_growth <= allowance
        and all(_finite_positive(value) for value in fixed_maxima)
        and all(_finite_positive(item["real_time_factor"]) for item in repetitions)
    )
    evidence = {
        "fixed_step_maximums_finite": all(
            _finite_positive(value) for value in fixed_maxima
        ),
        "headless_queue_scope": "not_applicable_no_transport_queue",
        "memory_growth_allowance_bytes": allowance,
        "memory_growth_bytes": memory_growth,
        "memory_samples_per_repetition": [
            item["resident_memory"]["sample_count"] for item in repetitions
        ],
        "resident_memory_within_physical_ram": within_ram,
    }
    return bounded, evidence


def select_decision_branch(
    *,
    target_motion_realtime_factors: list[float],
    target_guided_realtime_factors: list[float],
    authority_repeatable: bool,
    resources_bounded: bool,
    motion_hotspot_supported: bool,
) -> dict[str, Any]:
    """按实施计划 17.5 选择且只选择一个分支。"""

    expected = SHORT_DECISION_REPETITIONS
    if (
        len(target_motion_realtime_factors) != expected
        or len(target_guided_realtime_factors) != expected
    ):
        raise BenchmarkContractError(
            "decision.repetition_count", "$.decision", f"每档必须恰有 {expected} 次"
        )
    motion_pass = all(value >= 1.0 for value in target_motion_realtime_factors)
    guided_pass = all(value >= 1.0 for value in target_guided_realtime_factors)
    if not authority_repeatable:
        branch = "authority_mismatch_stop"
        next_slice = "stop_and_investigate_authority_drift"
        reason = "相同输入的权威状态或事件 hash 不一致，性能结论无效"
        status = "FAIL"
    elif motion_pass and guided_pass and resources_bounded:
        branch = "branch_1_resume_t0c"
        next_slice = "T0c_authoritative_serialization_baseline"
        reason = "target_20 运动与制导三次均达到实时因子门槛，且短跑未见无界资源证据"
        status = "PASS"
    elif motion_pass and guided_pass:
        branch = "branch_4_continue_t0b1_boundedness"
        next_slice = "T0b1g_resource_boundedness_investigation"
        reason = "target_20 达到实时因子门槛，但短跑资源有界性门禁未通过"
        status = "PASS"
    elif motion_pass:
        branch = "branch_2_review_projectile_rust_hot_core"
        next_slice = "T0b2_projectile_rust_hot_core_review"
        reason = "target_20 运动通过而制导失败，只评审弹丸积分、制导与碰撞热核"
        status = "PASS"
    elif motion_hotspot_supported:
        branch = "branch_3_author_multirate_authority_rfc"
        next_slice = "T0b2_multirate_authority_rfc"
        reason = "target_20 运动仍未实时，且剖析证据位于 Python 机动、对象与权威调度路径"
        status = "PASS"
    else:
        branch = "insufficient_hotspot_evidence_stop"
        next_slice = "repeat_target_motion_hotspot_diagnosis"
        reason = "target_20 运动未通过，但现有热点证据不足以进入计划分支 3"
        status = "FAIL"
    return {
        "authority_repeatable": authority_repeatable,
        "next_slice": next_slice,
        "reason": reason,
        "resources_bounded_within_short_gate": resources_bounded,
        "selected_branch": branch,
        "status": status,
        "target_guided_all_realtime": guided_pass,
        "target_guided_realtime_factors": target_guided_realtime_factors,
        "target_motion_all_realtime": motion_pass,
        "target_motion_hotspot_supported": motion_hotspot_supported,
        "target_motion_realtime_factors": target_motion_realtime_factors,
    }


def validate_short_decision_result(value: Any) -> None:
    result = _exact_keys(value, _TOP_LEVEL_KEYS, "$")
    if result["interface"] != SHORT_DECISION_INTERFACE:
        raise BenchmarkContractError(
            "decision.interface", "$.interface", str(result["interface"])
        )
    if result["official_performance_runs_executed"] != 0:
        raise BenchmarkContractError(
            "decision.official_scope", "$.official_performance_runs_executed", "必须为 0"
        )
    if result["t0_performance_measured"] is not False:
        raise BenchmarkContractError(
            "decision.official_scope", "$.t0_performance_measured", "必须为 false"
        )
    protocol = _exact_keys(result["protocol"], _PROTOCOL_KEYS, "$.protocol")
    if (
        protocol["warmup_steps"] != SHORT_DECISION_WARMUP_STEPS
        or protocol["measured_steps"] != SHORT_DECISION_MEASURED_STEPS
        or protocol["repetitions"] != SHORT_DECISION_REPETITIONS
        or protocol["measurement_scope"] != "diagnostic_only_not_official_t0"
    ):
        raise BenchmarkContractError(
            "decision.protocol", "$.protocol", "必须为冻结的 10+60 步、三次重复协议"
        )
    cases = result["cases"]
    if not isinstance(cases, list) or len(cases) != SHORT_DECISION_CASE_COUNT:
        raise BenchmarkContractError(
            "decision.case_count", "$.cases", str(len(cases) if isinstance(cases, list) else cases)
        )
    if {item.get("case") for item in cases if isinstance(item, dict)} != _EXPECTED_CASE_IDS:
        raise BenchmarkContractError(
            "decision.case_matrix", "$.cases", "必须为三档乘运动/制导六场景"
        )
    for case_index, raw_case in enumerate(cases):
        case = _exact_keys(raw_case, _CASE_KEYS, f"$.cases[{case_index}]")
        repetitions = case["repetitions"]
        if not isinstance(repetitions, list) or len(repetitions) != SHORT_DECISION_REPETITIONS:
            raise BenchmarkContractError(
                "decision.repetition_count",
                f"$.cases[{case_index}].repetitions",
                str(len(repetitions) if isinstance(repetitions, list) else repetitions),
            )
        for repetition_index, raw_repetition in enumerate(repetitions):
            _exact_keys(
                raw_repetition,
                _REPETITION_KEYS,
                f"$.cases[{case_index}].repetitions[{repetition_index}]",
            )
        if [item["repetition"] for item in repetitions] != [1, 2, 3]:
            raise BenchmarkContractError(
                "decision.repetition_order",
                f"$.cases[{case_index}].repetitions",
                "必须按 1、2、3 排列",
            )
    if result["short_diagnostic_runs_executed"] != SHORT_DECISION_RUN_COUNT:
        raise BenchmarkContractError(
            "decision.run_count",
            "$.short_diagnostic_runs_executed",
            str(result["short_diagnostic_runs_executed"]),
        )
    decision = _exact_keys(result["decision"], _DECISION_KEYS, "$.decision")
    if result["status"] != decision["status"]:
        raise BenchmarkContractError(
            "decision.status_mismatch", "$.status", str(result["status"])
        )
    if result["environment_metadata_sha256"] != canonical_sha256(
        result["environment_metadata"]
    ):
        raise BenchmarkContractError(
            "decision.environment_hash",
            "$.environment_metadata_sha256",
            "共享机器元数据 hash 不匹配",
        )


def run_short_diagnostic_decision(
    root: str | Path,
    benchmark_plan: BenchmarkPlan,
    diagnostic_plan: HotPathDiagnosticPlan,
    *,
    command: str,
) -> dict[str, Any]:
    base = Path(root).resolve()
    if (
        len(diagnostic_plan.cases) != SHORT_DECISION_CASE_COUNT
        or {item.profile for item in diagnostic_plan.cases}
        != {item.id for item in benchmark_plan.profiles}
        or {item.load_stage for item in diagnostic_plan.cases}
        != set(SHORT_DECISION_STAGES)
    ):
        raise BenchmarkContractError(
            "decision.case_matrix", "$.diagnostic_plan.cases", "必须为三档乘运动/制导六场景"
        )
    if benchmark_plan.repetitions != SHORT_DECISION_REPETITIONS:
        raise BenchmarkContractError(
            "decision.plan_repetitions", "$.benchmark_plan.repetitions", str(benchmark_plan.repetitions)
        )

    environment = collect_environment_metadata(base, command=command)
    historical = _historical_call_baselines(base)
    cases: list[dict[str, Any]] = []
    for diagnostic_case in diagnostic_plan.cases:
        case_id = diagnostic_case.id
        if case_id not in historical:
            raise BenchmarkContractError(
                "decision.baseline_case_missing", "$.probe_results", case_id
            )
        profiled = profile_hot_path_case(
            base, benchmark_plan, diagnostic_plan, diagnostic_case
        )
        current_calls = {
            probe_id: int(probe["total_calls"])
            for probe_id, probe in sorted(
                profiled["profiled"]["function_probes"].items()
            )
        }
        historical_comparison = {
            probe_id: {
                "after": current_calls[probe_id],
                "before": before,
                "reduction": before - current_calls[probe_id],
            }
            for probe_id, before in sorted(historical[case_id].items())
        }
        repetitions = []
        for repetition in range(1, SHORT_DECISION_REPETITIONS + 1):
            bundle = build_scenario(
                base,
                benchmark_plan,
                diagnostic_case.profile,
                diagnostic_case.load_stage,
                repetition,
            )
            result = run_headless_baseline(
                bundle,
                command=command,
                warmup_steps=SHORT_DECISION_WARMUP_STEPS,
                measured_steps=SHORT_DECISION_MEASURED_STEPS,
                environment_metadata=environment,
            )
            repetitions.append(_repetition_summary(repetition, result))

        authority_repeatable = (
            len({item["authority_state_sha256"] for item in repetitions}) == 1
            and len({item["authority_event_sha256"] for item in repetitions}) == 1
            and len({item["initial_scene_sha256"] for item in repetitions}) == 1
            and len({item["input_stream_sha256"] for item in repetitions}) == 1
        )
        bounded, bounded_evidence = _bounded_short_run(
            repetitions, environment["ram_bytes"]
        )
        hotspots = profiled["profiled"]["top_hotspots"]
        hotspot_evidence = [
            item["function"]
            for item in hotspots
            if any(
                token in item["function"]
                for token in (
                    "高天荒野舰艇战术机动求解器.py",
                    "高天荒野舰艇统一战术场景.py",
                    "高天荒野舰艇运行时参数编译器.py",
                    "dataclasses.py",
                )
            )
        ]
        cases.append(
            {
                "authority_event_sha256": repetitions[0]["authority_event_sha256"],
                "authority_repeatable": authority_repeatable,
                "authority_state_sha256": repetitions[0]["authority_state_sha256"],
                "case": case_id,
                "current_probe_total_calls": current_calls,
                "historical_call_comparison": historical_comparison,
                "hotspot_evidence": hotspot_evidence,
                "initial_scene_sha256": repetitions[0]["initial_scene_sha256"],
                "input_stream_sha256": repetitions[0]["input_stream_sha256"],
                "load_stage": diagnostic_case.load_stage,
                "profile": diagnostic_case.profile,
                "repetitions": repetitions,
                "resource_bounded_within_short_gate": bounded,
                "top_hotspots": hotspots,
                "worst_across_repetitions": {
                    **bounded_evidence,
                    "fixed_step_maximum_ms": max(
                        item["fixed_step"]["maximum"] for item in repetitions
                    ),
                    "fixed_step_p95_ms": max(
                        item["fixed_step"]["p95_nearest_rank"] for item in repetitions
                    ),
                    "observer_drain_maximum_ms": max(
                        item["observer_drain"]["maximum"] for item in repetitions
                    ),
                    "observer_drain_p95_ms": max(
                        item["observer_drain"]["p95_nearest_rank"] for item in repetitions
                    ),
                    "real_time_factor_minimum": min(
                        item["real_time_factor"] for item in repetitions
                    ),
                    "resident_memory_maximum_bytes": max(
                        item["resident_memory"]["maximum"] for item in repetitions
                    ),
                },
            }
        )

    by_id = {item["case"]: item for item in cases}
    target_motion = by_id[f"{SHORT_DECISION_TARGET_PROFILE}.motion_only"]
    target_guided = by_id[f"{SHORT_DECISION_TARGET_PROFILE}.guided_projectiles"]
    decision = select_decision_branch(
        target_motion_realtime_factors=[
            float(item["real_time_factor"])
            for item in target_motion["repetitions"]
        ],
        target_guided_realtime_factors=[
            float(item["real_time_factor"])
            for item in target_guided["repetitions"]
        ],
        authority_repeatable=all(item["authority_repeatable"] for item in cases),
        resources_bounded=all(
            item["resource_bounded_within_short_gate"] for item in cases
        ),
        motion_hotspot_supported=bool(target_motion["hotspot_evidence"]),
    )
    value = {
        "benchmark_plan_sha256": benchmark_plan.source_sha256,
        "cases": cases,
        "decision": decision,
        "diagnostic_plan_sha256": diagnostic_plan.source_sha256,
        "environment_metadata": environment,
        "environment_metadata_sha256": canonical_sha256(environment),
        "interface": SHORT_DECISION_INTERFACE,
        "official_performance_runs_executed": 0,
        "protocol": {
            "fixed_step_budget_ms": diagnostic_plan.fixed_step_budget_ms,
            "measured_steps": SHORT_DECISION_MEASURED_STEPS,
            "measurement_scope": "diagnostic_only_not_official_t0",
            "memory_growth_absolute_allowance_bytes": MEMORY_GROWTH_ABSOLUTE_ALLOWANCE_BYTES,
            "memory_growth_relative_allowance": MEMORY_GROWTH_RELATIVE_ALLOWANCE,
            "repetitions": SHORT_DECISION_REPETITIONS,
            "warmup_steps": SHORT_DECISION_WARMUP_STEPS,
        },
        "short_diagnostic_runs_executed": len(cases) * SHORT_DECISION_REPETITIONS,
        "status": decision["status"],
        "t0_performance_measured": False,
    }
    validate_short_decision_result(value)
    return value
