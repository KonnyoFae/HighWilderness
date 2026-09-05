"""d4.6：v7 完整受控场景的轻量标记；不导入推进算法。"""

from dataclasses import dataclass
from typing import Any

from 高天荒野舰艇数据契约 import ContractError
from 高天荒野舰艇受控推进场景版本 import (
    GOVERNED_TIME_POLICY_ID, GOVERNED_SAFETY_POLICY_ID,
)

FULL_SCENE_INTERFACE_ID = "gaotian.tactical-scene-timeline/v7alpha1"
FULL_SCENE_POLICY_ID = "gaotian.tactical-scene/fully-governed-closing-boundary/v1"
FULL_STEP_INTERFACE_ID = "gaotian.tactical-scene-step-resolution/v6alpha1"
FULL_STEP_POLICY_ID = "gaotian.tactical-scene-step/hard-interlock-open-integrate-soft-close/v1"
FULL_SAVE_INTERFACE_ID = "gaotian.governed-propulsion-scene-save/v2alpha1"
FULL_DIAGNOSTIC_INTERFACE_ID = "gaotian.actual-propulsion-step-diagnostics/v3alpha1"
FULL_DIAGNOSTIC_POLICY_ID = "gaotian.tactical-dynamics/fully-governed-opening-output/v1"
FULL_EXECUTION_INTERFACE_ID = "gaotian.governed-propulsion-execution/v2alpha1"
FULL_BOUNDARY_POLICY_ID = "gaotian.governed-propulsion/hard-interlock-time-delivery-soft/v1"
FULL_EVENT_POLICY_ID = "gaotian.propulsion-events/boundary-hard-time-soft-ship-local-order/v1"
FULL_COMMAND_INTERFACE_ID = "gaotian.tactical-scene-hard-fault-command-batch/v1alpha1"
FULL_MIGRATION_INTERFACE_ID = "gaotian.governed-propulsion-save-migration/v1alpha1"
FULL_MIGRATION_ID = "gaotian.migration/d3-save-v1-to-d4-save-v2-committed-unambiguous/v1"


@dataclass(frozen=True)
class FullyGovernedPropulsionExecutionPolicy:
    """标记选择的权威语义；不表示当前构建已开放场景推进入口。"""

    def to_dict(self) -> dict[str, Any]:
        return {
            "interface": FULL_EXECUTION_INTERFACE_ID,
            "time_policy": GOVERNED_TIME_POLICY_ID,
            "safety_policy": GOVERNED_SAFETY_POLICY_ID,
            "boundary_policy": FULL_BOUNDARY_POLICY_ID,
            "event_policy": FULL_EVENT_POLICY_ID,
            "hard_fact_sampling": "opening_runtime",
            "command_ownership": "one_shot_exact_scene_and_step",
            "soft_governor_status": "wired",
            "hard_fault_status": "wired",
            "direction_interlock_status": "wired",
        }

    @classmethod
    def parse(cls, value: Any, path: str = "$") -> "FullyGovernedPropulsionExecutionPolicy":
        expected = cls().to_dict()
        if not isinstance(value, dict) or set(value) != set(expected):
            raise ContractError("full_scene.execution_keys", path, "必须完整保存 d4 治理标记")
        if value != expected:
            raise ContractError("full_scene.execution_policy", path, "d4 治理策略不匹配")
        return cls()
