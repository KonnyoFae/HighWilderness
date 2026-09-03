"""实际执行量的下层合同；不依赖运行时、控制桥或场景。"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
import re
from typing import Any

from 高天荒野舰艇数据契约 import ContractError

ACTUAL_ACTUATION_REQUEST_INTERFACE_ID = "gaotian.actual-actuation-request/v1alpha1"
ACTUAL_INTEGRATION_POLICY_ID = "gaotian.tactical-dynamics/actual-output-unprotected/v1"


def finite_number(value: Any, path: str, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError("actual_propulsion.finite_number", path, "必须是有限数且不得为布尔值")
    try:
        result = float(value)
    except OverflowError as error:
        raise ContractError("actual_propulsion.finite_number", path, "数值超出有限浮点范围") from error
    if not isfinite(result):
        raise ContractError("actual_propulsion.finite_number", path, "必须是有限数")
    if minimum is not None and result < minimum:
        raise ContractError("actual_propulsion.number_range", path, "数值低于允许下限")
    return result


def fixed_step_index(value: Any) -> int:
    if type(value) is not int or value < 0:
        raise ContractError("actual_propulsion.fixed_step_index", "$.source_fixed_step_index", "必须是非负整数")
    return value


@dataclass(frozen=True)
class ActualActuationRequest:
    """已聚合但尚未做末步燃料交付的物理请求，不证明软保护已生效。"""

    derived_snapshot_sha256: str
    runtime_parameters_sha256: str
    source_fixed_step_index: int
    force_body_n: tuple[float, float]
    torque_n_m: float
    fuel_units_per_s: float

    def __post_init__(self) -> None:
        for key in ("derived_snapshot_sha256", "runtime_parameters_sha256"):
            value = getattr(self, key)
            if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
                raise ContractError("actual_propulsion.sha256", f"$.{key}", "必须是规范 SHA-256")
        fixed_step_index(self.source_fixed_step_index)
        if not isinstance(self.force_body_n, tuple) or len(self.force_body_n) != 2:
            raise ContractError("actual_propulsion.vector", "$.force_body_n", "必须是二维向量")
        object.__setattr__(self, "force_body_n", tuple(finite_number(x, "$.force_body_n") for x in self.force_body_n))
        object.__setattr__(self, "torque_n_m", finite_number(self.torque_n_m, "$.torque_n_m"))
        object.__setattr__(self, "fuel_units_per_s", finite_number(self.fuel_units_per_s, "$.fuel_units_per_s", 0))

    def to_dict(self) -> dict[str, Any]:
        return {
            "interface": ACTUAL_ACTUATION_REQUEST_INTERFACE_ID,
            "derived_snapshot_sha256": self.derived_snapshot_sha256,
            "runtime_parameters_sha256": self.runtime_parameters_sha256,
            "source_fixed_step_index": self.source_fixed_step_index,
            "force_body_n": list(self.force_body_n),
            "torque_n_m": self.torque_n_m,
            "fuel_units_per_s": self.fuel_units_per_s,
        }

    @classmethod
    def parse(cls, value: Any, path: str = "$") -> "ActualActuationRequest":
        keys = {"interface", "derived_snapshot_sha256", "runtime_parameters_sha256",
                "source_fixed_step_index", "force_body_n", "torque_n_m", "fuel_units_per_s"}
        if not isinstance(value, dict) or set(value) != keys:
            raise ContractError("object.keys", path, "实际执行量请求字段必须完整且无未知项")
        if value["interface"] != ACTUAL_ACTUATION_REQUEST_INTERFACE_ID:
            raise ContractError("actual_propulsion.interface", path, "未知实际执行量版本")
        if not isinstance(value["force_body_n"], list):
            raise ContractError("type.array", f"{path}.force_body_n", "必须是数组")
        return cls(value["derived_snapshot_sha256"], value["runtime_parameters_sha256"],
                   value["source_fixed_step_index"], tuple(value["force_body_n"]),
                   value["torque_n_m"], value["fuel_units_per_s"])
