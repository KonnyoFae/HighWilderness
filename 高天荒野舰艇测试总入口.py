"""分层运行舰艇测试与阶段回归，并统一使用 UTF-8 子进程。"""

from __future__ import annotations

import argparse
from fnmatch import fnmatchcase
import json
from pathlib import Path
import subprocess
import sys
from time import perf_counter
from typing import Sequence


ROOT = Path(__file__).resolve().parent
PATTERNS = ("*测试.py", "*回归.py")
PROFILES = ("quick", "regression", "full")

# 快速档只保留基础编译链与当前推进主链，供日常迭代及时发现结构性回归。
QUICK_SCRIPT_NAMES = frozenset(
    {
        "高天荒野舰艇规范数据与船壳编译器测试.py",
        "高天荒野舰艇舾装编译器测试.py",
        "高天荒野舰艇运行时参数编译器测试.py",
        "高天荒野舰艇战术机动求解器测试.py",
        "高天荒野T0b2d1推进时间内核测试.py",
        "高天荒野T0b2d1r推进状态合同修复测试.py",
        "高天荒野T0b2d2b1定向控制与边界合同测试.py",
        "高天荒野T0b2d3a受控时间边界测试.py",
        "高天荒野T0b2d3b整舰安全判定测试.py",
        "高天荒野T0b2d3c受控场景合同与旧版门禁测试.py",
        "高天荒野T0b2d3d无场景受控推进适配器测试.py",
        "高天荒野T0b2d3e统一场景受控推进与存档测试.py",
        "高天荒野T0b2d4a硬故障状态边界测试.py",
        "高天荒野T0b2d4b硬故障运行时投影测试.py",
        "高天荒野T0b2d4c无场景硬故障适配器测试.py",
        "高天荒野T0b2d4d方向互锁边界测试.py",
        "高天荒野T0b2d4e完整受控推进适配器测试.py",
        "高天荒野T0b2d4f场景存档与迁移合同测试.py",
        "高天荒野T0b2d4g统一场景完整安全与存档测试.py",
    }
)

# 常规回归跳过已经由阶段收口覆盖、但运行成本最高的完整矩阵。
STAGE_HEAVY_SCRIPT_NAMES = frozenset(
    {
        "高天荒野T0b2d2b4场景接线与新黄金测试.py",
        "高天荒野T0b2d3f受控黄金与完整矩阵测试.py",
        "高天荒野T0b2d4h完整安全黄金与全回归测试.py",
    }
)


def discover_scripts(root: Path = ROOT) -> tuple[Path, ...]:
    scripts = {path.resolve() for pattern in PATTERNS for path in root.glob(pattern)}
    scripts.discard(Path(__file__).resolve())
    return tuple(sorted(scripts, key=lambda path: path.name))


def validate_manifests(scripts: Sequence[Path]) -> None:
    discovered_names = {script.name for script in scripts}
    missing_quick = QUICK_SCRIPT_NAMES - discovered_names
    missing_heavy = STAGE_HEAVY_SCRIPT_NAMES - discovered_names
    if missing_quick or missing_heavy:
        details = []
        if missing_quick:
            details.append(f"quick 清单缺失：{', '.join(sorted(missing_quick))}")
        if missing_heavy:
            details.append(f"重型清单缺失：{', '.join(sorted(missing_heavy))}")
        raise RuntimeError("；".join(details))


def select_scripts(
    scripts: Sequence[Path],
    profile: str,
    include_patterns: Sequence[str] = (),
) -> tuple[Path, ...]:
    if profile == "quick":
        selected = tuple(script for script in scripts if script.name in QUICK_SCRIPT_NAMES)
    elif profile == "regression":
        selected = tuple(
            script for script in scripts if script.name not in STAGE_HEAVY_SCRIPT_NAMES
        )
    elif profile == "full":
        selected = tuple(scripts)
    else:
        raise ValueError(f"未知测试档位：{profile}")

    if include_patterns:
        selected = tuple(
            script
            for script in selected
            if any(fnmatchcase(script.name, pattern) for pattern in include_patterns)
        )
    return selected


def run_script(script: Path, cwd: Path = ROOT) -> dict[str, object]:
    started_at = perf_counter()
    completed = subprocess.run(
        (sys.executable, "-X", "utf8", str(script)),
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    duration_ms = round((perf_counter() - started_at) * 1000, 3)
    result: dict[str, object] = {
        "duration_ms": duration_ms,
        "exit_code": completed.returncode,
        "script": script.name,
        "status": "PASS" if completed.returncode == 0 else "FAIL",
    }
    if completed.returncode != 0:
        result["stderr"] = completed.stderr
        result["stdout"] = completed.stdout
    return result


def execute_scripts(
    scripts: Sequence[Path],
    *,
    fail_fast: bool = False,
    cwd: Path = ROOT,
) -> tuple[list[dict[str, object]], int]:
    results: list[dict[str, object]] = []
    total = len(scripts)
    for index, script in enumerate(scripts, start=1):
        print(f"[{index}/{total}] START {script.name}", file=sys.stderr, flush=True)
        result = run_script(script, cwd=cwd)
        results.append(result)
        duration_s = float(result["duration_ms"]) / 1000.0
        print(
            f"[{index}/{total}] {result['status']}  {duration_s:.3f}s {script.name}",
            file=sys.stderr,
            flush=True,
        )
        if fail_fast and result["status"] == "FAIL":
            break
    return results, total - len(results)


def _non_negative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("必须为非负整数")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile",
        choices=PROFILES,
        default="full",
        help="测试档位；默认 full，保持原有全量验收行为",
    )
    parser.add_argument(
        "--include",
        action="append",
        default=[],
        metavar="GLOB",
        help="在所选档位内按文件名筛选，可重复指定",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="首次失败后停止后续测试",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="只列出将运行的测试，不执行",
    )
    parser.add_argument(
        "--slowest",
        type=_non_negative_int,
        default=10,
        metavar="N",
        help="汇总最慢的 N 项；0 表示不汇总，默认 10",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    scripts = discover_scripts()
    validate_manifests(scripts)
    selected = select_scripts(scripts, args.profile, args.include)
    if not selected:
        parser.error("当前档位与筛选条件没有匹配到测试")

    if args.list:
        report = {
            "discovered_count": len(scripts),
            "include_patterns": args.include,
            "interface": "gaotian.ship-test-runner/v2",
            "profile": args.profile,
            "scripts": [script.name for script in selected],
            "selected_count": len(selected),
            "status": "LISTED",
        }
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return

    started_at = perf_counter()
    results, not_run_count = execute_scripts(
        selected,
        fail_fast=args.fail_fast,
    )
    elapsed_ms = round((perf_counter() - started_at) * 1000, 3)
    failures = [result for result in results if result["status"] == "FAIL"]
    passed_count = len(results) - len(failures)
    slowest = sorted(
        (
            {
                "duration_ms": result["duration_ms"],
                "script": result["script"],
            }
            for result in results
        ),
        key=lambda result: float(result["duration_ms"]),
        reverse=True,
    )[: args.slowest]
    report = {
        "discovered_count": len(scripts),
        "elapsed_ms": elapsed_ms,
        "executed_count": len(results),
        "fail_fast": args.fail_fast,
        "failed": failures,
        "failed_count": len(failures),
        "include_patterns": args.include,
        "interface": "gaotian.ship-test-runner/v2",
        "not_run_count": not_run_count,
        "omitted_count": len(scripts) - len(selected),
        "passed_count": passed_count,
        "profile": args.profile,
        "results": results,
        "selected_count": len(selected),
        "slowest": slowest,
        "status": "PASS" if not failures else "FAIL",
        "total_count": len(selected),
        "utf8_mode": True,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
