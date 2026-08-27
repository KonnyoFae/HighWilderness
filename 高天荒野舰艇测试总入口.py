"""以 UTF-8 子进程运行全部舰艇测试与阶段回归，避免 Windows 默认代码页干扰。"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parent
PATTERNS = ("*测试.py", "*回归.py")


def discover_scripts() -> tuple[Path, ...]:
    scripts = {path for pattern in PATTERNS for path in ROOT.glob(pattern)}
    scripts.discard(Path(__file__).resolve())
    return tuple(sorted(scripts, key=lambda path: path.name))


def main() -> None:
    scripts = discover_scripts()
    failures: list[dict[str, object]] = []
    for script in scripts:
        completed = subprocess.run(
            (sys.executable, "-X", "utf8", str(script)),
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if completed.returncode != 0:
            failures.append(
                {
                    "exit_code": completed.returncode,
                    "script": script.name,
                    "stderr": completed.stderr,
                    "stdout": completed.stdout,
                }
            )
    report = {
        "failed": failures,
        "failed_count": len(failures),
        "interface": "gaotian.ship-test-runner/v1",
        "passed_count": len(scripts) - len(failures),
        "status": "PASS" if not failures else "FAIL",
        "total_count": len(scripts),
        "utf8_mode": True,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
