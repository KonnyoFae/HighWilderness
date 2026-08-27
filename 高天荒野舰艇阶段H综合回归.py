"""运行阶段 H 编辑器领域层与 L3 往返测试并核对确定性报告。"""

from __future__ import annotations

import json
from pathlib import Path

from 高天荒野舰艇阶段H编辑器领域层测试 import build_result


ROOT = Path(__file__).resolve().parent
SAVED_REPORT = ROOT / "舰艇数据" / "报告" / "阶段H编辑器与L3往返接口.v1.json"


def build_report() -> dict[str, object]:
    return {
        "balance_status": "prototype_unbalanced",
        "editor": build_result(),
        "fixture_level": "contract_fixture",
        "report": "gaotian.stage-h-editor-l3-round-trip-regression/v1",
        "schema": "gaotian.ship/v1alpha1",
        "status": "PASS",
    }


def main() -> None:
    report = build_report()
    if json.loads(SAVED_REPORT.read_text(encoding="utf-8")) != report:
        raise RuntimeError(f"已保存阶段 H 报告已过期：{SAVED_REPORT}")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

