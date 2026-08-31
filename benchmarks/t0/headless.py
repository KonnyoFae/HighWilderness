"""T0b 无界面权威基线运行器。"""

from __future__ import annotations

import hashlib
from pathlib import Path
from time import perf_counter_ns
from typing import Any, Iterable, Mapping

from 高天荒野舰艇数据契约 import canonical_sha256

from .contracts import BenchmarkContractError, canonical_json_bytes
from .matrix import expand_matrix
from .metadata import collect_environment_metadata, process_resident_memory_bytes
from .metrics import summarize_samples
from .scenario import (
    SceneEntityCounter,
    T0ScenarioBundle,
    advance_scenario_step,
    scene_entity_counts,
)


HEADLESS_INTERFACE = "gaotian.t0-headless-baseline/v2"
HEADLESS_MEASUREMENT_POLICY = "gaotian.t0-headless-measurement-boundary/v1"
REAL_TIME_FACTOR_SCOPE = (
    "simulated_time_divided_by_wall_time_including_observer_drain"
)
DEFAULT_MEMORY_SAMPLE_RATE_HZ = 1
EVENT_STREAM_DOMAIN = b"gaotian.t0-authority-event-stream/v1\0"
ALWAYS_EMITTED_EVENT_KEYS = frozenset(
    {
        "engagement_events",
        "expired_events",
        "impact_events",
        "lifecycle_events",
        "spawned_projectiles",
        "weapon_events",
    }
)
EVENT_KEYS = frozenset(
    {
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
    }
)


def _event_payload(resolution: Any) -> dict[str, list[dict[str, Any]]]:
    """只物化权威事件域，保持场景 resolution 既有 JSON 省略规则。"""

    payload: dict[str, list[dict[str, Any]]] = {}
    for key in sorted(EVENT_KEYS):
        items = getattr(resolution, key)
        if key in ALWAYS_EMITTED_EVENT_KEYS or items:
            payload[key] = [item.to_dict() for item in items]
    return payload


def _event_digest(previous: bytes, resolution: Any) -> bytes:
    encoded = canonical_json_bytes(_event_payload(resolution))
    return hashlib.sha256(previous + len(encoded).to_bytes(8, "big") + encoded).digest()


def authority_event_stream_sha256(resolutions: Iterable[Any]) -> str:
    """按 T0 事件域规则计算一个或多个连续固定步的滚动权威事件 hash。"""

    digest = hashlib.sha256(EVENT_STREAM_DOMAIN).digest()
    for resolution in resolutions:
        digest = _event_digest(digest, resolution)
    return digest.hex()


def _count_summary(samples: dict[str, list[int]]) -> dict[str, dict[str, float | int | None]]:
    result = {}
    for key, values in sorted(samples.items()):
        summary = summarize_samples(values).to_dict()
        result[key] = {
            **summary,
            "minimum": min(values) if values else None,
        }
    return result


def _load_coverage(
    bundle: T0ScenarioBundle,
    actual: dict[str, Any],
    *,
    official_duration: bool,
) -> tuple[str, list[str]]:
    reasons = []
    counts = actual["measured_entity_counts"]
    if counts["active_ships"]["minimum"] < bundle.profile.ships:
        reasons.append("活动舰数量低于档位目标")
    if bundle.load_stage != "motion_only":
        if counts["ordinary_projectiles"]["minimum"] < bundle.profile.ordinary_projectiles_target:
            reasons.append("普通弹丸未在完整测量窗口维持目标数量")
        if actual["weapon_events_per_second"] < bundle.profile.weapon_events_per_second_target:
            reasons.append("合法武器动作事件率低于档位目标")
    if bundle.load_stage in {"guided_projectiles", "scripted_damage_and_recompile"}:
        if counts["guided_projectiles"]["minimum"] < bundle.profile.guided_projectiles_target:
            reasons.append("制导弹丸未在完整测量窗口维持目标数量")
    if bundle.load_stage == "scripted_damage_and_recompile":
        if actual["continuous_damage_event_count"] < 1:
            reasons.append("测量窗口没有持续毁伤事件")
    if not official_duration:
        reasons.append("诊断短跑未使用计划规定的预热和测量步数")
    return ("PASS" if not reasons else "NOT_COVERED"), reasons


def _run_spec(bundle: T0ScenarioBundle) -> dict[str, Any]:
    matches = [
        item
        for item in expand_matrix(bundle.plan)
        if item.profile_id == bundle.profile.id
        and item.load_stage == bundle.load_stage
        and item.repetition == bundle.repetition
        and item.mode == "headless_baseline"
    ]
    if len(matches) != 1:
        raise BenchmarkContractError("headless.run_spec", "$.matrix", str(len(matches)))
    run = matches[0]
    if run.input_stream_sha256 != bundle.input_stream_sha256:
        raise BenchmarkContractError(
            "headless.input_hash_mismatch",
            "$.input_stream_sha256",
            f"{run.input_stream_sha256} != {bundle.input_stream_sha256}",
        )
    return run.to_dict()


def _measurement_environment(
    bundle: T0ScenarioBundle,
    command: str,
    supplied: Mapping[str, object] | None,
) -> dict[str, object]:
    environment = (
        collect_environment_metadata(bundle.root, command=command)
        if supplied is None
        else dict(supplied)
    )
    required = {
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
    if set(environment) != required:
        raise BenchmarkContractError(
            "headless.environment_keys",
            "$.environment_metadata",
            f"必须恰含 {sorted(required)}",
        )
    if environment["command"] != command:
        raise BenchmarkContractError(
            "headless.environment_command",
            "$.environment_metadata.command",
            f"{environment['command']} != {command}",
        )
    return environment


def _memory_sample_due(
    measured_step_number: int,
    measured_steps: int,
    interval_steps: int,
) -> bool:
    return (
        measured_step_number % interval_steps == 0
        or measured_step_number == measured_steps
    )


def run_headless_baseline(
    bundle: T0ScenarioBundle,
    *,
    command: str,
    warmup_steps: int | None = None,
    measured_steps: int | None = None,
    environment_metadata: Mapping[str, object] | None = None,
    memory_sample_interval_steps: int | None = None,
) -> dict[str, Any]:
    warmup = bundle.plan.warmup_steps if warmup_steps is None else warmup_steps
    measured = bundle.plan.measured_steps if measured_steps is None else measured_steps
    if isinstance(warmup, bool) or not isinstance(warmup, int) or warmup < 0:
        raise BenchmarkContractError("headless.warmup_steps", "$.warmup_steps", str(warmup))
    if isinstance(measured, bool) or not isinstance(measured, int) or measured < 1:
        raise BenchmarkContractError("headless.measured_steps", "$.measured_steps", str(measured))
    memory_interval = (
        max(1, round(bundle.plan.fixed_step_hz / DEFAULT_MEMORY_SAMPLE_RATE_HZ))
        if memory_sample_interval_steps is None
        else memory_sample_interval_steps
    )
    if (
        isinstance(memory_interval, bool)
        or not isinstance(memory_interval, int)
        or memory_interval < 1
    ):
        raise BenchmarkContractError(
            "headless.memory_sample_interval_steps",
            "$.memory_sample_interval_steps",
            str(memory_interval),
        )
    official_duration = (
        warmup == bundle.plan.warmup_steps
        and measured == bundle.plan.measured_steps
    )
    environment = _measurement_environment(
        bundle,
        command,
        environment_metadata,
    )
    initial_scene_sha = canonical_sha256(bundle.initial_scene)
    run_spec = _run_spec(bundle)
    scene = bundle.initial_scene
    initial_entity_counts = scene_entity_counts(scene)
    entity_counter = SceneEntityCounter.from_scene(scene)
    event_digest = hashlib.sha256(EVENT_STREAM_DOMAIN).digest()
    totals = {
        "continuous_damage_event_count": 0,
        "expired_projectile_count": 0,
        "impact_event_count": 0,
        "spawned_projectile_count": 0,
        "weapon_event_count": 0,
    }

    for _ in range(warmup):
        resolution = advance_scenario_step(bundle, scene)
        event_digest = _event_digest(event_digest, resolution)
        scene = resolution.resulting_scene
        entity_counter.advance(resolution)

    fixed_step_ms: list[float] = []
    observer_drain_ms: list[float] = []
    resident_memory: list[float] = []
    entity_samples: dict[str, list[int]] = {}
    authoritative_advance_ns = 0
    simulated_measured_s = measured / bundle.plan.fixed_step_hz
    measured_wall_start = perf_counter_ns()
    for measured_index in range(measured):
        authority_start = perf_counter_ns()
        resolution = advance_scenario_step(bundle, scene)
        authority_end = perf_counter_ns()
        authority_elapsed_ns = authority_end - authority_start
        authoritative_advance_ns += authority_elapsed_ns
        fixed_step_ms.append(authority_elapsed_ns / 1_000_000.0)

        event_digest = _event_digest(event_digest, resolution)
        scene = resolution.resulting_scene
        counts = entity_counter.advance(resolution)
        for key, value in counts.items():
            entity_samples.setdefault(key, []).append(value)
        totals["continuous_damage_event_count"] += len(resolution.continuous_damage_events)
        totals["expired_projectile_count"] += len(resolution.expired_events)
        totals["impact_event_count"] += len(resolution.impact_events)
        totals["spawned_projectile_count"] += len(resolution.spawned_projectiles)
        totals["weapon_event_count"] += len(resolution.weapon_events)
        if _memory_sample_due(measured_index + 1, measured, memory_interval):
            memory = process_resident_memory_bytes()
            if memory is not None:
                resident_memory.append(float(memory))
        observer_drain_ms.append(
            (perf_counter_ns() - authority_end) / 1_000_000.0
        )

    authority_state_sha = canonical_sha256(scene)
    measured_counts = _count_summary(entity_samples)
    actual = {
        **totals,
        "initial_entity_counts": initial_entity_counts,
        "measured_entity_counts": measured_counts,
        "weapon_events_per_second": totals["weapon_event_count"] / simulated_measured_s,
    }
    load_status, reasons = _load_coverage(
        bundle, actual, official_duration=official_duration
    )
    measured_wall_s = (perf_counter_ns() - measured_wall_start) / 1_000_000_000.0
    authoritative_advance_wall_s = authoritative_advance_ns / 1_000_000_000.0
    observer_drain_wall_s = max(
        0.0,
        measured_wall_s - authoritative_advance_wall_s,
    )
    real_time_factor = simulated_measured_s / measured_wall_s
    metadata = {
        "actual_entity_counts": actual,
        "coalesced_frame_count": 0,
        "command": command,
        "commit": environment["commit"],
        "cpu": environment["cpu"],
        "dirty_diff_sha256": environment["dirty_diff_sha256"],
        "fixture_resource_hashes": bundle.fixture_resource_hashes,
        "frame_bytes_max": None,
        "input_stream_sha256": bundle.input_stream_sha256,
        "measurement_policy": HEADLESS_MEASUREMENT_POLICY,
        "node": environment["node"],
        "os": environment["os"],
        "power_mode": environment["power_mode"],
        "profile": bundle.profile.id,
        "python": environment["python"],
        "ram_bytes": environment["ram_bytes"],
        "real_time_factor_scope": REAL_TIME_FACTOR_SCOPE,
        "rejected_request_count": 0,
        "resident_memory_sample_count": len(resident_memory),
        "resident_memory_sample_interval_steps": memory_interval,
        "rust": environment["rust"],
        "webview2": environment["webview2"],
    }
    metrics = {
        "fixed_step": summarize_samples(fixed_step_ms).to_dict(),
        "frame_bytes": None,
        "host_webview_round_trip": None,
        "observer_drain": summarize_samples(observer_drain_ms).to_dict(),
        "projectile_world": None,
        "queue_depth": None,
        "real_time_factor": summarize_samples([real_time_factor]).to_dict(),
        "resident_memory": summarize_samples(resident_memory).to_dict(),
        "runtime_recompile": None,
        "serialization": None,
    }
    reason = None if not reasons else "；".join(reasons)
    return {
        "authority_event_sha256": event_digest.hex(),
        "authority_state_sha256": authority_state_sha,
        "execution": {
            "authoritative_advance_wall_s": authoritative_advance_wall_s,
            "measured_steps": measured,
            "measured_wall_s": measured_wall_s,
            "measurement_policy": HEADLESS_MEASUREMENT_POLICY,
            "observer_drain_wall_s": observer_drain_wall_s,
            "official_duration": official_duration,
            "resident_memory_sample_count": len(resident_memory),
            "resident_memory_sample_interval_steps": memory_interval,
            "real_time_factor_scope": REAL_TIME_FACTOR_SCOPE,
            "simulated_measured_s": simulated_measured_s,
            "warmup_steps": warmup,
        },
        "initial_scene_sha256": initial_scene_sha,
        "interface": HEADLESS_INTERFACE,
        "headless_baseline_measured": official_duration,
        "load_coverage": {
            "reasons": reasons,
            "status": load_status,
        },
        "run_result": {
            "authority_event_sha256": event_digest.hex(),
            "authority_state_sha256": authority_state_sha,
            "metadata": metadata,
            "metrics": metrics,
            "reason": reason,
            "run_spec": run_spec,
            "status": load_status,
        },
        "t0_performance_measured": False,
    }
