from __future__ import annotations

from contextlib import redirect_stderr
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = ROOT / "高天荒野舰艇测试总入口.py"
SPEC = importlib.util.spec_from_file_location("ship_test_runner", RUNNER_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"无法载入测试入口：{RUNNER_PATH}")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class ShipTestRunnerTests(unittest.TestCase):
    def test_real_profile_manifests_match_discovery(self) -> None:
        scripts = runner.discover_scripts()

        runner.validate_manifests(scripts)

        self.assertEqual(runner.select_scripts(scripts, "full"), scripts)
        self.assertEqual(
            {script.name for script in runner.select_scripts(scripts, "quick")},
            runner.QUICK_SCRIPT_NAMES,
        )
        self.assertEqual(
            len(runner.select_scripts(scripts, "regression")),
            len(scripts) - len(runner.STAGE_HEAVY_SCRIPT_NAMES),
        )

    def test_profile_then_include_filter_is_deterministic(self) -> None:
        quick_name = sorted(runner.QUICK_SCRIPT_NAMES)[0]
        heavy_name = sorted(runner.STAGE_HEAVY_SCRIPT_NAMES)[0]
        scripts = (
            Path(quick_name),
            Path(heavy_name),
            Path("普通测试.py"),
        )

        self.assertEqual(
            runner.select_scripts(scripts, "quick"),
            (Path(quick_name),),
        )
        self.assertEqual(
            runner.select_scripts(scripts, "regression"),
            (Path(quick_name), Path("普通测试.py")),
        )
        self.assertEqual(
            runner.select_scripts(scripts, "full", ("*普通*",)),
            (Path("普通测试.py"),),
        )

    def test_fail_fast_reports_unexecuted_scripts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            scripts = (
                root / "01_pass.py",
                root / "02_fail.py",
                root / "03_not_run.py",
            )
            scripts[0].write_text("raise SystemExit(0)\n", encoding="utf-8")
            scripts[1].write_text("raise SystemExit(7)\n", encoding="utf-8")
            scripts[2].write_text("raise SystemExit(0)\n", encoding="utf-8")

            with redirect_stderr(io.StringIO()):
                results, not_run_count = runner.execute_scripts(
                    scripts,
                    fail_fast=True,
                    cwd=root,
                )

        self.assertEqual([result["status"] for result in results], ["PASS", "FAIL"])
        self.assertEqual(not_run_count, 1)


if __name__ == "__main__":
    unittest.main()
