"""T0 基准计划的严格解析与稳定序列化合同。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from math import isfinite
from pathlib import Path
from typing import Any, Mapping


PLAN_INTERFACE = "gaotian.tactical-performance-plan/v1"
EXPECTED_MODES = (
    "headless_baseline",
    "full_authoritative_json",
    "experimental_render_full",
    "experimental_render_delta",
)
EXPECTED_LOAD_STAGES = (
    "motion_only",
    "ordinary_projectiles",
    "guided_projectiles",
    "scripted_damage_and_recompile",
)
EXPECTED_MEASUREMENTS = (
    "fixed_step",
    "projectile_world",
    "runtime_recompile",
    "serialization",
    "host_webview_round_trip",
    "frame_bytes",
    "queue_depth",
    "resident_memory",
    "real_time_factor",
)
EXPECTED_METADATA = (
    "commit",
    "dirty_diff_sha256",
    "os",
    "cpu",
    "ram_bytes",
    "python",
    "rust",
    "node",
    "webview2",
    "power_mode",
    "profile",
    "fixture_resource_hashes",
    "input_stream_sha256",
    "command",
    "actual_entity_counts",
    "frame_bytes_max",
    "coalesced_frame_count",
    "rejected_request_count",
)
PLAN_KEYS = {
    "interface",
    "status",
    "fixture_level",
    "dependencies",
    "fixed_step_hz",
    "warmup_steps",
    "measured_steps",
    "repetitions",
    "snapshot_rates_hz",
    "modes",
    "profiles",
    "load_stages",
    "measurements",
    "required_metadata",
    "aggregation",
    "gates",
}


class BenchmarkContractError(ValueError):
    """带稳定 code/path 的 T0 输入合同错误。"""

    def __init__(self, code: str, path: str, message: str) -> None:
        super().__init__(f"{code} at {path}: {message}")
        self.code = code
        self.path = path
        self.message = message

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "path": self.path}


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _object(value: Any, path: str, keys: set[str]) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise BenchmarkContractError("type.object", path, "必须是对象")
    actual = set(value)
    if actual != keys:
        missing = sorted(keys - actual)
        unknown = sorted(actual - keys)
        raise BenchmarkContractError(
            "object.keys",
            path,
            f"字段不匹配；缺少 {missing}，未知 {unknown}",
        )
    return value


def _string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value:
        raise BenchmarkContractError("type.string", path, "必须是非空字符串")
    return value


def _positive_int(value: Any, path: str, *, allow_zero: bool = False) -> int:
    minimum = 0 if allow_zero else 1
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        qualifier = "非负" if allow_zero else "正"
        raise BenchmarkContractError("type.integer", path, f"必须是{qualifier}整数")
    return value


def _positive_number(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BenchmarkContractError("type.number", path, "必须是正有限数")
    result = float(value)
    if not isfinite(result) or result <= 0.0:
        raise BenchmarkContractError("value.positive", path, "必须是正有限数")
    return result


def _unique_strings(value: Any, path: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise BenchmarkContractError("type.array", path, "必须是非空字符串数组")
    result = tuple(_string(item, f"{path}[{index}]") for index, item in enumerate(value))
    if len(set(result)) != len(result):
        raise BenchmarkContractError("array.duplicate", path, "不得包含重复项")
    return result


@dataclass(frozen=True)
class BenchmarkProfile:
    id: str
    ships: int
    composition: tuple[tuple[str, int], ...]
    ordinary_projectiles_target: int
    guided_projectiles_target: int
    weapon_events_per_second_target: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "composition": dict(self.composition),
            "guided_projectiles_target": self.guided_projectiles_target,
            "id": self.id,
            "ordinary_projectiles_target": self.ordinary_projectiles_target,
            "ships": self.ships,
            "weapon_events_per_second_target": self.weapon_events_per_second_target,
        }


@dataclass(frozen=True)
class MeasurementDefinition:
    id: str
    unit: str
    scope: str

    def to_dict(self) -> dict[str, str]:
        return {"id": self.id, "scope": self.scope, "unit": self.unit}


@dataclass(frozen=True)
class GateDefinition:
    id: str
    criterion: str

    def to_dict(self) -> dict[str, str]:
        return {"criterion": self.criterion, "id": self.id}


@dataclass(frozen=True)
class BenchmarkPlan:
    source_path: Path
    source_sha256: str
    interface: str
    status: str
    fixture_level: str
    dependencies: tuple[str, ...]
    fixed_step_hz: int
    warmup_steps: int
    measured_steps: int
    repetitions: int
    snapshot_rates_hz: tuple[int, ...]
    modes: tuple[str, ...]
    profiles: tuple[BenchmarkProfile, ...]
    load_stages: tuple[str, ...]
    measurements: tuple[MeasurementDefinition, ...]
    required_metadata: tuple[str, ...]
    aggregation: Mapping[str, Any]
    gates: tuple[GateDefinition, ...]
    raw: Mapping[str, Any]

    def profile(self, profile_id: str) -> BenchmarkProfile:
        try:
            return next(item for item in self.profiles if item.id == profile_id)
        except StopIteration as error:
            raise BenchmarkContractError(
                "plan.profile_missing", "$.profiles", profile_id
            ) from error


def parse_benchmark_plan(value: Any, *, source_path: str | Path = "<memory>") -> BenchmarkPlan:
    raw = _object(value, "$", PLAN_KEYS)
    if raw["interface"] != PLAN_INTERFACE:
        raise BenchmarkContractError("plan.interface", "$.interface", str(raw["interface"]))
    if raw["status"] != "specified_not_measured":
        raise BenchmarkContractError("plan.status", "$.status", str(raw["status"]))
    fixture_level = _string(raw["fixture_level"], "$.fixture_level")
    dependencies = _unique_strings(raw["dependencies"], "$.dependencies")
    if dependencies != ("W1",):
        raise BenchmarkContractError("plan.dependencies", "$.dependencies", "T0 必须只依赖 W1")
    fixed_step_hz = _positive_int(raw["fixed_step_hz"], "$.fixed_step_hz")
    if fixed_step_hz != 60:
        raise BenchmarkContractError("plan.fixed_step", "$.fixed_step_hz", "必须固定为 60 Hz")
    warmup_steps = _positive_int(raw["warmup_steps"], "$.warmup_steps", allow_zero=True)
    measured_steps = _positive_int(raw["measured_steps"], "$.measured_steps")
    repetitions = _positive_int(raw["repetitions"], "$.repetitions")

    rates_raw = raw["snapshot_rates_hz"]
    if not isinstance(rates_raw, list) or not rates_raw:
        raise BenchmarkContractError("type.array", "$.snapshot_rates_hz", "必须是非空数组")
    rates = tuple(
        _positive_int(item, f"$.snapshot_rates_hz[{index}]")
        for index, item in enumerate(rates_raw)
    )
    if len(set(rates)) != len(rates) or tuple(sorted(rates)) != rates:
        raise BenchmarkContractError(
            "plan.snapshot_rates", "$.snapshot_rates_hz", "必须严格升序且不重复"
        )

    modes = _unique_strings(raw["modes"], "$.modes")
    if modes != EXPECTED_MODES:
        raise BenchmarkContractError("plan.modes", "$.modes", f"必须为 {EXPECTED_MODES}")
    stages = _unique_strings(raw["load_stages"], "$.load_stages")
    if stages != EXPECTED_LOAD_STAGES:
        raise BenchmarkContractError(
            "plan.load_stages", "$.load_stages", f"必须为 {EXPECTED_LOAD_STAGES}"
        )

    profiles_raw = raw["profiles"]
    if not isinstance(profiles_raw, list) or not profiles_raw:
        raise BenchmarkContractError("type.array", "$.profiles", "必须是非空数组")
    profiles: list[BenchmarkProfile] = []
    profile_keys = {
        "id",
        "ships",
        "composition",
        "ordinary_projectiles_target",
        "guided_projectiles_target",
        "weapon_events_per_second_target",
    }
    for index, item in enumerate(profiles_raw):
        path = f"$.profiles[{index}]"
        parsed = _object(item, path, profile_keys)
        composition_raw = parsed["composition"]
        if not isinstance(composition_raw, dict) or not composition_raw:
            raise BenchmarkContractError("type.object", f"{path}.composition", "必须是非空对象")
        composition = tuple(
            sorted(
                (
                    _string(key, f"{path}.composition.<key>"),
                    _positive_int(count, f"{path}.composition.{key}", allow_zero=True),
                )
                for key, count in composition_raw.items()
            )
        )
        ships = _positive_int(parsed["ships"], f"{path}.ships")
        if sum(count for _, count in composition) != ships:
            raise BenchmarkContractError(
                "plan.composition_total", f"{path}.composition", "编成合计必须等于 ships"
            )
        profiles.append(
            BenchmarkProfile(
                _string(parsed["id"], f"{path}.id"),
                ships,
                composition,
                _positive_int(
                    parsed["ordinary_projectiles_target"],
                    f"{path}.ordinary_projectiles_target",
                    allow_zero=True,
                ),
                _positive_int(
                    parsed["guided_projectiles_target"],
                    f"{path}.guided_projectiles_target",
                    allow_zero=True,
                ),
                _positive_int(
                    parsed["weapon_events_per_second_target"],
                    f"{path}.weapon_events_per_second_target",
                    allow_zero=True,
                ),
            )
        )
    if len({item.id for item in profiles}) != len(profiles):
        raise BenchmarkContractError("plan.profile_duplicate", "$.profiles", "档位 id 不得重复")

    measurements_raw = raw["measurements"]
    if not isinstance(measurements_raw, list) or not measurements_raw:
        raise BenchmarkContractError("type.array", "$.measurements", "必须是非空数组")
    measurements = tuple(
        MeasurementDefinition(
            _string(_object(item, f"$.measurements[{index}]", {"id", "unit", "scope"})["id"], f"$.measurements[{index}].id"),
            _string(item["unit"], f"$.measurements[{index}].unit"),
            _string(item["scope"], f"$.measurements[{index}].scope"),
        )
        for index, item in enumerate(measurements_raw)
    )
    if tuple(item.id for item in measurements) != EXPECTED_MEASUREMENTS:
        raise BenchmarkContractError(
            "plan.measurements", "$.measurements", f"必须为 {EXPECTED_MEASUREMENTS}"
        )

    required_metadata = _unique_strings(raw["required_metadata"], "$.required_metadata")
    if required_metadata != EXPECTED_METADATA:
        raise BenchmarkContractError(
            "plan.required_metadata", "$.required_metadata", f"必须为 {EXPECTED_METADATA}"
        )

    aggregation = _object(
        raw["aggregation"],
        "$.aggregation",
        {"per_run", "percentile", "across_runs"},
    )
    if aggregation["per_run"] != ["sample_count", "mean", "p95_nearest_rank", "maximum"]:
        raise BenchmarkContractError("plan.aggregation", "$.aggregation.per_run", "统计字段不匹配")
    if aggregation["percentile"] != "sorted_samples[ceil(0.95*n)-1]":
        raise BenchmarkContractError("plan.aggregation", "$.aggregation.percentile", "P95 必须使用最近秩")

    gates_raw = raw["gates"]
    if not isinstance(gates_raw, list) or not gates_raw:
        raise BenchmarkContractError("type.array", "$.gates", "必须是非空数组")
    gates = tuple(
        GateDefinition(
            _string(_object(item, f"$.gates[{index}]", {"id", "criterion"})["id"], f"$.gates[{index}].id"),
            _string(item["criterion"], f"$.gates[{index}].criterion"),
        )
        for index, item in enumerate(gates_raw)
    )
    if tuple(item.id for item in gates) != tuple(f"T0-G0{index}" for index in range(1, 6)):
        raise BenchmarkContractError("plan.gates", "$.gates", "必须恰含 T0-G01 至 T0-G05")

    raw_copy = json.loads(json.dumps(raw, ensure_ascii=False))
    source = Path(source_path)
    return BenchmarkPlan(
        source,
        canonical_sha256(raw_copy),
        PLAN_INTERFACE,
        raw["status"],
        fixture_level,
        dependencies,
        fixed_step_hz,
        warmup_steps,
        measured_steps,
        repetitions,
        rates,
        modes,
        tuple(profiles),
        stages,
        measurements,
        required_metadata,
        aggregation,
        gates,
        raw_copy,
    )


def load_benchmark_plan(path: str | Path) -> BenchmarkPlan:
    source = Path(path)
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise BenchmarkContractError("plan.read", str(source), str(error)) from error
    return parse_benchmark_plan(raw, source_path=source)

