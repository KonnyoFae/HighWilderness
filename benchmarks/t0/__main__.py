"""T0 命令行：计划、场景、无界面基线与热路径诊断。"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Sequence

from 高天荒野舰艇数据契约 import ContractError

from .contracts import BenchmarkContractError, load_benchmark_plan
from .diagnostics import (
    load_hot_path_diagnostic_plan,
    run_hot_path_diagnostic,
    verify_authority_step_golden,
)
from .decision import run_short_diagnostic_decision
from .fixture_audit import audit_fixture_capacity
from .headless import (
    DEFAULT_MEMORY_SAMPLE_RATE_HZ,
    HEADLESS_INTERFACE,
    HEADLESS_MEASUREMENT_POLICY,
    REAL_TIME_FACTOR_SCOPE,
    run_headless_baseline,
)
from .matrix import expand_matrix, matrix_summary
from .metadata import collect_environment_metadata, file_sha256 as canonical_file_sha256
from .scenario import build_scenario


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PLAN = ROOT / "contracts" / "web_bridge" / "t0-benchmark-plan.v1.json"
DEFAULT_DIAGNOSTIC_PLAN = ROOT / "contracts" / "web_bridge" / "t0-hot-path-diagnostic.v1.json"
DEFAULT_GOLDEN = ROOT / "contracts" / "web_bridge" / "t0-authority-step-golden.v1.json"


def _write(value: Any, output: Path | None) -> None:
    serialized = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if output is None:
        sys.stdout.write(serialized)
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(serialized, encoding="utf-8")
    print(output)


def build_preflight_manifest(root: Path, plan_path: Path, *, command: str) -> dict[str, Any]:
    plan = load_benchmark_plan(plan_path)
    runs = expand_matrix(plan)
    fixture_audit = audit_fixture_capacity(root, plan)
    return {
        "environment_metadata": collect_environment_metadata(root, command=command),
        "fixture_audit": fixture_audit,
        "gates": [
            {
                "id": gate.id,
                "reason": "T0a 仅完成合同与负载审计，尚未执行性能运行",
                "status": "NOT_COVERED",
            }
            for gate in plan.gates
        ],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "interface": "gaotian.tactical-performance-result/v1",
        "matrix": {
            **matrix_summary(plan, runs),
            "runs": [item.to_dict() for item in runs],
        },
        "non_goals": [
            "不测 PixiJS 或 GPU 帧率",
            "不冻结 T1 表现字段",
            "不修改 W1 生产 sidecar 能力白名单",
            "不把准备期审计当作正式性能通过",
        ],
        "plan_sha256": plan.source_sha256,
        "runs": [],
        "status": "NOT_COVERED",
        "t0_performance_measured": False,
        "t1_density_recommendation": None,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="《高天荒野》T0 战术基准工具")
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--diagnostic-plan", type=Path, default=DEFAULT_DIAGNOSTIC_PLAN)
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    subcommands = parser.add_subparsers(dest="action", required=True)
    validate = subcommands.add_parser("validate-plan", help="严格校验基准计划")
    validate.add_argument("--output", type=Path)
    matrix = subcommands.add_parser("expand-matrix", help="展开 252 次正式运行矩阵")
    matrix.add_argument("--output", type=Path)
    audit = subcommands.add_parser("audit", help="生成 T0a 准备期审计清单")
    audit.add_argument("--output", type=Path)
    scenario = subcommands.add_parser("scenario-manifest", help="生成 T0b 确定性场景清单")
    scenario.add_argument("--profile", required=True)
    scenario.add_argument("--stage", required=True)
    scenario.add_argument("--repetition", type=int, default=1)
    scenario.add_argument("--output", type=Path)
    headless = subcommands.add_parser("headless", help="运行一个 T0b 无界面基线")
    headless.add_argument("--profile", required=True)
    headless.add_argument("--stage", required=True)
    headless.add_argument("--repetition", type=int, default=1)
    headless.add_argument("--warmup-steps", type=int)
    headless.add_argument("--measured-steps", type=int)
    headless.add_argument("--resume", action="store_true")
    headless.add_argument("--output", type=Path)
    verify = subcommands.add_parser("verify-golden", help="复核 T0b.1a 十二场景权威单步黄金结果")
    verify.add_argument("--output", type=Path)
    diagnose = subcommands.add_parser("diagnose-hot-path", help="运行默认关闭的 T0b.1a 短程热路径诊断")
    diagnose.add_argument("--profile")
    diagnose.add_argument("--stage")
    diagnose.add_argument("--output", type=Path)
    decide = subcommands.add_parser(
        "decide-short-diagnostic", help="执行 T0b.1f 的 10+60 步三重复决策门"
    )
    decide.add_argument("--output", type=Path)
    return parser


def _resume_matches(
    path: Path,
    bundle: Any,
    *,
    warmup_steps: int,
    measured_steps: int,
) -> bool:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        result = value["run_result"]
        metadata = result["metadata"]
        execution = value["execution"]
        run_spec = result["run_spec"]
        metrics = result["metrics"]
        expected_memory_interval = max(
            1,
            round(bundle.plan.fixed_step_hz / DEFAULT_MEMORY_SAMPLE_RATE_HZ),
        )
        return (
            value["interface"] == HEADLESS_INTERFACE
            and value["initial_scene_sha256"] == bundle.to_manifest()["initial_scene_sha256"]
            and run_spec["input_stream_sha256"] == bundle.input_stream_sha256
            and metadata["fixture_resource_hashes"] == bundle.fixture_resource_hashes
            and execution["warmup_steps"] == warmup_steps
            and execution["measured_steps"] == measured_steps
            and execution["measurement_policy"] == HEADLESS_MEASUREMENT_POLICY
            and metadata["measurement_policy"] == HEADLESS_MEASUREMENT_POLICY
            and execution["real_time_factor_scope"] == REAL_TIME_FACTOR_SCOPE
            and metadata["real_time_factor_scope"] == REAL_TIME_FACTOR_SCOPE
            and execution["resident_memory_sample_interval_steps"]
            == expected_memory_interval
            and metadata["resident_memory_sample_interval_steps"]
            == expected_memory_interval
            and execution["resident_memory_sample_count"]
            == metadata["resident_memory_sample_count"]
            == metrics["resident_memory"]["sample_count"]
            and metrics["fixed_step"]["sample_count"] == measured_steps
            and metrics["observer_drain"]["sample_count"] == measured_steps
            and metrics["real_time_factor"]["sample_count"] == 1
            and abs(
                execution["measured_wall_s"]
                - execution["authoritative_advance_wall_s"]
                - execution["observer_drain_wall_s"]
            )
            <= 1.0e-9
        )
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return False


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    raw_arguments = list(sys.argv[1:] if argv is None else argv)
    arguments = parser.parse_args(raw_arguments)
    plan_path = arguments.plan.resolve()
    try:
        plan = load_benchmark_plan(plan_path)
        if arguments.action == "validate-plan":
            value = {
                "interface": "gaotian.t0-plan-validation/v1",
                "plan_sha256": plan.source_sha256,
                "status": "PASS",
            }
        elif arguments.action == "expand-matrix":
            runs = expand_matrix(plan)
            value = {
                "interface": "gaotian.t0-run-matrix/v1",
                "plan_sha256": plan.source_sha256,
                "runs": [item.to_dict() for item in runs],
                "summary": matrix_summary(plan, runs),
            }
        elif arguments.action == "audit":
            command = "python -X utf8 -m benchmarks.t0 " + subprocess.list2cmdline(
                raw_arguments
            )
            value = build_preflight_manifest(ROOT, plan_path, command=command)
        elif arguments.action == "verify-golden":
            golden_path = arguments.golden.resolve()
            verified = verify_authority_step_golden(ROOT, plan, golden_path)
            value = {
                "case_count": len(verified["cases"]),
                "golden_sha256": canonical_file_sha256(golden_path),
                "interface": "gaotian.t0-authority-golden-verification/v1",
                "plan_sha256": plan.source_sha256,
                "status": "PASS",
            }
        elif arguments.action == "diagnose-hot-path":
            diagnostic_plan = load_hot_path_diagnostic_plan(arguments.diagnostic_plan)
            command = "python -X utf8 -m benchmarks.t0 " + subprocess.list2cmdline(
                raw_arguments
            )
            value = run_hot_path_diagnostic(
                ROOT,
                plan,
                diagnostic_plan,
                command=command,
                profile=arguments.profile,
                load_stage=arguments.stage,
            )
        elif arguments.action == "decide-short-diagnostic":
            diagnostic_plan = load_hot_path_diagnostic_plan(arguments.diagnostic_plan)
            command = "python -X utf8 -m benchmarks.t0 " + subprocess.list2cmdline(
                raw_arguments
            )
            value = run_short_diagnostic_decision(
                ROOT,
                plan,
                diagnostic_plan,
                command=command,
            )
        else:
            bundle = build_scenario(
                ROOT,
                plan,
                arguments.profile,
                arguments.stage,
                arguments.repetition,
            )
            if arguments.action == "scenario-manifest":
                value = bundle.to_manifest()
            else:
                warmup = (
                    plan.warmup_steps
                    if arguments.warmup_steps is None
                    else arguments.warmup_steps
                )
                measured = (
                    plan.measured_steps
                    if arguments.measured_steps is None
                    else arguments.measured_steps
                )
                if (
                    arguments.resume
                    and arguments.output is not None
                    and arguments.output.exists()
                    and _resume_matches(
                        arguments.output,
                        bundle,
                        warmup_steps=warmup,
                        measured_steps=measured,
                    )
                ):
                    print(arguments.output)
                    return 0
                command = "python -X utf8 -m benchmarks.t0 " + subprocess.list2cmdline(
                    raw_arguments
                )
                environment = collect_environment_metadata(ROOT, command=command)
                value = run_headless_baseline(
                    bundle,
                    command=command,
                    warmup_steps=arguments.warmup_steps,
                    measured_steps=arguments.measured_steps,
                    environment_metadata=environment,
                )
        _write(value, arguments.output)
        return 0
    except (BenchmarkContractError, ContractError) as error:
        payload = (
            error.to_dict()
            if isinstance(error, BenchmarkContractError)
            else {
                "code": error.code,
                "message": error.message,
                "path": error.path,
            }
        )
        sys.stderr.write(
            json.dumps(
                {"error": payload, "status": "FAIL"},
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n"
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
