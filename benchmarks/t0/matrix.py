"""将 T0 计划确定性展开为可断点执行的正式运行矩阵。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any

from .contracts import BenchmarkContractError, BenchmarkPlan, BenchmarkProfile, canonical_sha256


INPUT_INTERFACE = "gaotian.tactical-benchmark-input/v1"
SCENARIO_GENERATOR_INTERFACE = "gaotian.t0-deterministic-scenario/v1"
SCENARIO_GENERATOR_POLICY = {
    "initial_projectiles": "explicit_contract_state_far_field",
    "movement": "deterministic_low_thrust",
    "weapon_actions": "legal_ammunition_and_timeline",
}


@dataclass(frozen=True)
class BenchmarkRunSpec:
    run_id: str
    profile_id: str
    load_stage: str
    mode: str
    snapshot_rate_hz: int | None
    repetition: int
    input_seed: int
    input_stream_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_seed": self.input_seed,
            "input_stream_sha256": self.input_stream_sha256,
            "load_stage": self.load_stage,
            "mode": self.mode,
            "profile": self.profile_id,
            "repetition": self.repetition,
            "run_id": self.run_id,
            "snapshot_rate_hz": self.snapshot_rate_hz,
        }


def _input_seed(profile_id: str, load_stage: str) -> int:
    material = f"{INPUT_INTERFACE}\0{profile_id}\0{load_stage}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(material).digest()[:8], "big")


def build_input_descriptor(
    plan: BenchmarkPlan,
    profile: BenchmarkProfile,
    load_stage: str,
    repetition: int,
) -> dict[str, Any]:
    """描述尚待 T0b 物化的权威输入；不包含传输模式和快照频率。"""

    if load_stage not in plan.load_stages:
        raise BenchmarkContractError("matrix.load_stage", "$.load_stage", load_stage)
    if repetition < 1 or repetition > plan.repetitions:
        raise BenchmarkContractError("matrix.repetition", "$.repetition", str(repetition))
    return {
        "fixed_step_hz": plan.fixed_step_hz,
        "generator_interface": SCENARIO_GENERATOR_INTERFACE,
        "generator_policy": dict(SCENARIO_GENERATOR_POLICY),
        "input_seed": _input_seed(profile.id, load_stage),
        "interface": INPUT_INTERFACE,
        "load_stage": load_stage,
        "measured_steps": plan.measured_steps,
        "profile": profile.to_dict(),
        "warmup_steps": plan.warmup_steps,
    }


def _rate_variants(plan: BenchmarkPlan, mode: str) -> tuple[int | None, ...]:
    if mode == "headless_baseline":
        return (None,)
    return tuple(plan.snapshot_rates_hz)


def expand_matrix(plan: BenchmarkPlan) -> tuple[BenchmarkRunSpec, ...]:
    runs: list[BenchmarkRunSpec] = []
    for profile in plan.profiles:
        for stage in plan.load_stages:
            for repetition in range(1, plan.repetitions + 1):
                descriptor = build_input_descriptor(plan, profile, stage, repetition)
                input_hash = canonical_sha256(descriptor)
                seed = descriptor["input_seed"]
                for mode in plan.modes:
                    for rate in _rate_variants(plan, mode):
                        rate_label = "none" if rate is None else str(rate)
                        run_id = (
                            f"{profile.id}.{stage}.r{repetition}."
                            f"{mode}.snapshot_{rate_label}hz"
                        )
                        runs.append(
                            BenchmarkRunSpec(
                                run_id,
                                profile.id,
                                stage,
                                mode,
                                rate,
                                repetition,
                                seed,
                                input_hash,
                            )
                        )
    validate_matrix(plan, tuple(runs))
    return tuple(runs)


def validate_matrix(plan: BenchmarkPlan, runs: tuple[BenchmarkRunSpec, ...]) -> None:
    expected_variants = 1 + (len(plan.modes) - 1) * len(plan.snapshot_rates_hz)
    expected_groups = len(plan.profiles) * len(plan.load_stages) * plan.repetitions
    expected_runs = expected_groups * expected_variants
    if len(runs) != expected_runs:
        raise BenchmarkContractError(
            "matrix.run_count", "$.runs", f"预期 {expected_runs}，实际 {len(runs)}"
        )
    if len({item.run_id for item in runs}) != len(runs):
        raise BenchmarkContractError("matrix.run_id_duplicate", "$.runs", "run_id 不得重复")

    groups: dict[tuple[str, str, int], list[BenchmarkRunSpec]] = {}
    for run in runs:
        groups.setdefault((run.profile_id, run.load_stage, run.repetition), []).append(run)
        if run.mode == "headless_baseline" and run.snapshot_rate_hz is not None:
            raise BenchmarkContractError(
                "matrix.headless_rate", f"$.runs.{run.run_id}", "无界面基线不得有快照频率"
            )
        if run.mode != "headless_baseline" and run.snapshot_rate_hz not in plan.snapshot_rates_hz:
            raise BenchmarkContractError(
                "matrix.transport_rate", f"$.runs.{run.run_id}", "传输模式必须使用计划快照频率"
            )
    if len(groups) != expected_groups:
        raise BenchmarkContractError("matrix.group_count", "$.runs", str(len(groups)))
    for key, group in groups.items():
        if len(group) != expected_variants:
            raise BenchmarkContractError("matrix.variant_count", f"$.runs.{key}", str(len(group)))
        if len({item.input_stream_sha256 for item in group}) != 1:
            raise BenchmarkContractError(
                "matrix.input_divergence", f"$.runs.{key}", "同一逻辑运行的传输变体必须共用输入 hash"
            )
    repeated_inputs: dict[tuple[str, str], set[str]] = {}
    for run in runs:
        repeated_inputs.setdefault((run.profile_id, run.load_stage), set()).add(
            run.input_stream_sha256
        )
    divergent = sorted(key for key, hashes in repeated_inputs.items() if len(hashes) != 1)
    if divergent:
        raise BenchmarkContractError(
            "matrix.repetition_input_divergence",
            "$.runs",
            f"三次重复必须复用完全相同的输入流：{divergent}",
        )


def matrix_summary(plan: BenchmarkPlan, runs: tuple[BenchmarkRunSpec, ...]) -> dict[str, Any]:
    return {
        "comparison_group_count": len(plan.profiles) * len(plan.load_stages) * plan.repetitions,
        "input_group_count": len(plan.profiles) * len(plan.load_stages),
        "mode_rate_variant_count": 1 + (len(plan.modes) - 1) * len(plan.snapshot_rates_hz),
        "official_run_count": len(runs),
        "profiles": len(plan.profiles),
        "repetitions": plan.repetitions,
        "stages": len(plan.load_stages),
        "unique_input_stream_count": len(plan.profiles) * len(plan.load_stages),
    }
