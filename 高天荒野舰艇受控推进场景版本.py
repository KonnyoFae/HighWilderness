"""v6 受控场景的轻量版本标记；旧统一场景可导入但不加载 d3.1/d3.2 实现。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from 高天荒野舰艇数据契约 import ContractError


GOVERNED_SCENE_INTERFACE_ID = "gaotian.tactical-scene-timeline/v6alpha1"
GOVERNED_SCENE_POLICY_ID = "gaotian.tactical-scene/actual-propulsion-soft-governed-closing-boundary/v1"
GOVERNED_STEP_INTERFACE_ID = "gaotian.tactical-scene-step-resolution/v5alpha1"
GOVERNED_STEP_POLICY_ID = "gaotian.tactical-scene-step/open-command-integrate-close-safety/v1"
GOVERNED_SCENE_SAVE_INTERFACE_ID = "gaotian.governed-propulsion-scene-save/v1alpha1"
GOVERNED_DIAGNOSTIC_INTERFACE_ID = "gaotian.actual-propulsion-step-diagnostics/v2alpha1"
GOVERNED_DIAGNOSTIC_POLICY_ID = "gaotian.tactical-dynamics/governed-source-actual-output/v1"
GOVERNED_EXECUTION_INTERFACE_ID = "gaotian.governed-propulsion-execution/v1alpha1"
GOVERNED_TIME_POLICY_ID = "gaotian.propulsion-time/preview-authorize-effective-target/v1"
GOVERNED_SAFETY_POLICY_ID = "gaotian.propulsion-safety/joint-upstage-batch-bounded-cap-path/v1"
GOVERNED_BOUNDARY_POLICY_ID = "gaotian.governed-propulsion/command-open-safety-close/v1"
GOVERNED_OPENING_RECORD_INTERFACE_ID = "gaotian.governed-propulsion-opening-record/v1alpha1"
GOVERNED_CLOSING_RECORD_INTERFACE_ID = "gaotian.governed-propulsion-closing-record/v1alpha1"
GOVERNED_SAFETY_EVENT_INTERFACE_ID = "gaotian.tactical-scene-propulsion-safety-event/v1alpha1"


def _require(condition: bool, code: str, path: str, message: str) -> None:
    if not condition:
        raise ContractError(f"governed_scene.{code}", path, message)


@dataclass(frozen=True)
class GovernedPropulsionExecutionPolicy:
    """场景中不可根据 governor 当前状态推断的显式治理标记。"""

    time_policy: str = GOVERNED_TIME_POLICY_ID
    safety_policy: str = GOVERNED_SAFETY_POLICY_ID
    boundary_policy: str = GOVERNED_BOUNDARY_POLICY_ID

    def __post_init__(self) -> None:
        _require(self.time_policy == GOVERNED_TIME_POLICY_ID, "time_policy", "$.time_policy", "时间策略不匹配")
        _require(self.safety_policy == GOVERNED_SAFETY_POLICY_ID, "safety_policy", "$.safety_policy", "安全策略不匹配")
        _require(self.boundary_policy == GOVERNED_BOUNDARY_POLICY_ID, "boundary_policy", "$.boundary_policy", "边界所有权不匹配")

    def to_dict(self) -> dict[str, Any]:
        return {
            "interface": GOVERNED_EXECUTION_INTERFACE_ID,
            "time_policy": self.time_policy,
            "safety_policy": self.safety_policy,
            "boundary_policy": self.boundary_policy,
            "soft_governor_status": "wired",
            "hard_fault_status": "unwired",
            "direction_interlock_status": "unwired",
        }

    @classmethod
    def parse(cls, value: Any, path: str = "$") -> "GovernedPropulsionExecutionPolicy":
        keys = {
            "interface",
            "time_policy",
            "safety_policy",
            "boundary_policy",
            "soft_governor_status",
            "hard_fault_status",
            "direction_interlock_status",
        }
        if not isinstance(value, dict) or set(value) != keys:
            raise ContractError("object.keys", path, f"必须恰含 {sorted(keys)}")
        _require(value["interface"] == GOVERNED_EXECUTION_INTERFACE_ID, "execution_interface", path, "治理标记版本不匹配")
        _require(value["soft_governor_status"] == "wired", "soft_status", path, "软 governor 必须明确已接线")
        _require(value["hard_fault_status"] == "unwired", "hard_status", path, "d4 前硬故障必须保持未接线")
        _require(value["direction_interlock_status"] == "unwired", "interlock_status", path, "d4 前方向互锁必须保持未接线")
        return cls(value["time_policy"], value["safety_policy"], value["boundary_policy"])
