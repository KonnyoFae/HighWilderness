"""运行阶段 I 教程舰基准包首切片并核对确定性报告。"""

from __future__ import annotations

import json
from pathlib import Path

from 高天荒野舰艇阶段I教程基准包测试 import build_result


ROOT = Path(__file__).resolve().parent
SAVED_REPORT = ROOT / "舰艇数据" / "报告" / "阶段I教程舰基准门禁接口.v1.json"


def build_report() -> dict[str, object]:
    return {
        "balance_status": "technical_surrogate_not_formal_balance",
        "bootstrap": build_result(),
        "report": "gaotian.stage-i-tutorial-baseline-gate-regression/v1",
        "schema": "gaotian.ship-calibration/v1alpha1",
        "status": "PASS",
    }


def main() -> None:
    report = build_report()
    if json.loads(SAVED_REPORT.read_text(encoding="utf-8")) != report:
        raise RuntimeError(f"已保存阶段 I 教程舰基准报告已过期：{SAVED_REPORT}")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

