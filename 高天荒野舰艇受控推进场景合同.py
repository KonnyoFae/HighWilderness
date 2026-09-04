"""d3.3 第一阶段：受控场景的版本、审计记录和旧路径隔离合同。

本模块只冻结可序列化边界，不推进场景，也不把尚未接线的硬故障或方向互锁
伪装成已实现能力。统一场景接线必须在后续阶段显式消费这些记录。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from math import isfinite
import re
from typing import Any

from 高天荒野舰艇数据契约 import ContractError, RESOURCE_ID_PATTERN, canonical_sha256
from 高天荒野舰艇实际推进合同 import ActualActuationRequest
from 高天荒野舰艇战术机动求解器 import ActualTacticalStepDiagnostics, Vec2
from 高天荒野舰艇气动缓存 import DragBreakdown
from 高天荒野舰艇定向推进控制桥 import DirectionalPropulsionControlInput
from 高天荒野舰艇推进通道合同 import (
    DIRECTIONAL_CHANNELS,
    ChannelPropulsionCommand,
    DirectionalPropulsionGovernorState,
    exact_object,
)
from 高天荒野舰艇受控推进时间边界 import (
    GovernedPropulsionTimeResult,
)
from 高天荒野舰艇整舰推进安全判定 import (
    ChannelSafetyEventIntent,
    WholeShipPropulsionSafetyResult,
)
from 高天荒野舰艇受控推进场景版本 import (
    GOVERNED_BOUNDARY_POLICY_ID,
    GOVERNED_CLOSING_RECORD_INTERFACE_ID,
    GOVERNED_DIAGNOSTIC_INTERFACE_ID,
    GOVERNED_DIAGNOSTIC_POLICY_ID,
    GOVERNED_EXECUTION_INTERFACE_ID,
    GOVERNED_OPENING_RECORD_INTERFACE_ID,
    GOVERNED_SAFETY_EVENT_INTERFACE_ID,
    GOVERNED_SCENE_INTERFACE_ID,
    GOVERNED_SCENE_POLICY_ID,
    GOVERNED_SCENE_SAVE_INTERFACE_ID,
    GOVERNED_STEP_INTERFACE_ID,
    GOVERNED_STEP_POLICY_ID,
    GovernedPropulsionExecutionPolicy,
)


PROPULSION_DELIVERY_STATUSES = frozenset(
    {"delivered", "suppressed_falling", "suppressed_exited", "suppressed_uncommanded"}
)


def _require(condition: bool, code: str, path: str, message: str) -> None:
    if not condition:
        raise ContractError(f"governed_scene.{code}", path, message)


def _hash(value: Any, path: str) -> str:
    _require(
        isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None,
        "sha256",
        path,
        "必须是规范 SHA-256",
    )
    return value


def _step(value: Any, path: str) -> int:
    _require(type(value) is int and value >= 0, "fixed_step", path, "必须是非负整数")
    return value


def _resource_id(value: Any, path: str) -> str:
    _require(
        isinstance(value, str) and RESOURCE_ID_PATTERN.fullmatch(value) is not None,
        "resource_id",
        path,
        "资源身份非法",
    )
    return value


def _array(value: Any, path: str) -> list[Any]:
    _require(isinstance(value, list), "array", path, "必须是数组")
    return value


def _finite(value: Any, path: str, minimum: float | None = None, maximum: float | None = None) -> float:
    _require(
        not isinstance(value, bool) and isinstance(value, (int, float)),
        "finite_number",
        path,
        "必须是有限数且不得为布尔值",
    )
    try:
        result = float(value)
    except OverflowError as error:
        raise ContractError("governed_scene.finite_number", path, "数值超出有限浮点范围") from error
    _require(isfinite(result), "finite_number", path, "必须是有限数")
    _require(minimum is None or result >= minimum, "number_range", path, "低于允许下限")
    _require(maximum is None or result <= maximum, "number_range", path, "高于允许上限")
    return result


def _governors(value: Any, path: str) -> tuple[DirectionalPropulsionGovernorState, ...]:
    governors = tuple(
        DirectionalPropulsionGovernorState.parse(item, f"{path}[{index}]")
        for index, item in enumerate(_array(value, path))
    )
    _require(
        tuple(item.command_channel for item in governors) == DIRECTIONAL_CHANNELS,
        "governor_order",
        path,
        "必须按规范顺序完整保存六通道 governor",
    )
    return governors


def _commands(value: Any, path: str) -> tuple[ChannelPropulsionCommand, ...]:
    commands = tuple(
        ChannelPropulsionCommand.parse(item, f"{path}[{index}]")
        for index, item in enumerate(_array(value, path))
    )
    _require(
        tuple(item.command_channel for item in commands) == DIRECTIONAL_CHANNELS,
        "command_order",
        path,
        "必须按规范顺序完整保存六通道命令",
    )
    return commands


@dataclass(frozen=True)
class GovernedPropulsionOpeningRecord:
    ship_id: str
    fixed_step_index: int
    source_propulsion_state_sha256: str
    resulting_propulsion_state_sha256: str
    source_control: DirectionalPropulsionControlInput
    resulting_control: DirectionalPropulsionControlInput
    source_governors: tuple[DirectionalPropulsionGovernorState, ...]
    governor_commands: tuple[ChannelPropulsionCommand, ...]
    engine_results: tuple[GovernedPropulsionTimeResult, ...]

    def __post_init__(self) -> None:
        _resource_id(self.ship_id, "$.ship_id")
        n = _step(self.fixed_step_index, "$.fixed_step_index")
        _hash(self.source_propulsion_state_sha256, "$.source_propulsion_state_sha256")
        _hash(self.resulting_propulsion_state_sha256, "$.resulting_propulsion_state_sha256")
        _require(isinstance(self.source_control, DirectionalPropulsionControlInput), "source_control", "$.source_control", "必须提供严格源控制")
        _require(isinstance(self.resulting_control, DirectionalPropulsionControlInput), "resulting_control", "$.resulting_control", "必须提供严格结果控制")
        DirectionalPropulsionControlInput.parse(self.source_control.to_dict(), "$.source_control")
        DirectionalPropulsionControlInput.parse(self.resulting_control.to_dict(), "$.resulting_control")
        _require(
            isinstance(self.source_governors, tuple)
            and all(isinstance(item, DirectionalPropulsionGovernorState) for item in self.source_governors)
            and tuple(item.command_channel for item in self.source_governors) == DIRECTIONAL_CHANNELS,
            "source_governors",
            "$.source_governors",
            "必须完整保存六通道源 governor",
        )
        _require(
            tuple(item.command for item in self.source_governors) == self.source_control.channel_commands,
            "source_control_chain",
            "$.source_governors",
            "源控制必须与源 governor 命令一致",
        )
        _require(
            all(item.last_evaluated_step_index == n for item in self.source_governors),
            "opening_safety_clock",
            "$.source_governors",
            "开边界只能从已提交同一步安全状态开始",
        )
        _require(
            isinstance(self.governor_commands, tuple)
            and all(isinstance(item, ChannelPropulsionCommand) for item in self.governor_commands)
            and tuple(item.command_channel for item in self.governor_commands) == DIRECTIONAL_CHANNELS,
            "governor_commands",
            "$.governor_commands",
            "必须完整保存六通道结果命令",
        )
        _require(
            self.governor_commands == self.resulting_control.channel_commands,
            "resulting_control_chain",
            "$.governor_commands",
            "结果控制必须与 governor 命令一致",
        )
        _require(
            isinstance(self.engine_results, tuple)
            and all(isinstance(item, GovernedPropulsionTimeResult) for item in self.engine_results),
            "engine_results",
            "$.engine_results",
            "必须保存逐执行器 d3.1 结果",
        )
        actuator_ids = tuple(item.state.actuator_instance_id for item in self.engine_results)
        _require(
            actuator_ids == tuple(sorted(set(actuator_ids)))
            and all(item.preview.fixed_step_index == n for item in self.engine_results),
            "engine_result_order",
            "$.engine_results",
            "逐执行器结果必须唯一、排序且属于开边界",
        )

    @property
    def sort_key(self) -> tuple[int, str]:
        return self.fixed_step_index, self.ship_id

    @property
    def resulting_governors(self) -> tuple[DirectionalPropulsionGovernorState, ...]:
        """开边界只替换命令；安全上限、原因、滞回与 revision 原样保留。"""
        return tuple(
            replace(governor, command=command)
            for governor, command in zip(self.source_governors, self.governor_commands)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "interface": GOVERNED_OPENING_RECORD_INTERFACE_ID,
            "policy": GOVERNED_BOUNDARY_POLICY_ID,
            "boundary_phase": "opening",
            "ship_id": self.ship_id,
            "fixed_step_index": self.fixed_step_index,
            "source_propulsion_state_sha256": self.source_propulsion_state_sha256,
            "resulting_propulsion_state_sha256": self.resulting_propulsion_state_sha256,
            "source_control": self.source_control.to_dict(),
            "resulting_control": self.resulting_control.to_dict(),
            "source_governors": [item.to_dict() for item in self.source_governors],
            "governor_commands": [item.to_dict() for item in self.governor_commands],
            "engine_results": [item.to_dict() for item in self.engine_results],
        }

    @classmethod
    def parse(cls, value: Any, path: str = "$") -> "GovernedPropulsionOpeningRecord":
        obj = exact_object(
            value,
            {
                "interface",
                "policy",
                "boundary_phase",
                "ship_id",
                "fixed_step_index",
                "source_propulsion_state_sha256",
                "resulting_propulsion_state_sha256",
                "source_control",
                "resulting_control",
                "source_governors",
                "governor_commands",
                "engine_results",
            },
            path,
        )
        _require(
            obj["interface"] == GOVERNED_OPENING_RECORD_INTERFACE_ID
            and obj["policy"] == GOVERNED_BOUNDARY_POLICY_ID
            and obj["boundary_phase"] == "opening",
            "opening_interface",
            path,
            "开边界记录版本、策略或阶段不匹配",
        )
        return cls(
            obj["ship_id"],
            obj["fixed_step_index"],
            obj["source_propulsion_state_sha256"],
            obj["resulting_propulsion_state_sha256"],
            DirectionalPropulsionControlInput.parse(obj["source_control"], f"{path}.source_control"),
            DirectionalPropulsionControlInput.parse(obj["resulting_control"], f"{path}.resulting_control"),
            _governors(obj["source_governors"], f"{path}.source_governors"),
            _commands(obj["governor_commands"], f"{path}.governor_commands"),
            tuple(
                GovernedPropulsionTimeResult.parse(item, f"{path}.engine_results[{index}]")
                for index, item in enumerate(_array(obj["engine_results"], f"{path}.engine_results"))
            ),
        )


@dataclass(frozen=True)
class GovernedPropulsionClosingRecord:
    ship_id: str
    fixed_step_index: int
    source_propulsion_state_sha256: str
    resulting_propulsion_state_sha256: str
    runtime_parameters_sha256: str
    motion_state_sha256: str
    propulsion_delivery_status: str
    crew_safety_lock_enabled: bool
    safety_result: WholeShipPropulsionSafetyResult

    def __post_init__(self) -> None:
        _resource_id(self.ship_id, "$.ship_id")
        n = _step(self.fixed_step_index, "$.fixed_step_index")
        for key in (
            "source_propulsion_state_sha256",
            "resulting_propulsion_state_sha256",
            "runtime_parameters_sha256",
            "motion_state_sha256",
        ):
            _hash(getattr(self, key), f"$.{key}")
        _require(
            self.propulsion_delivery_status in PROPULSION_DELIVERY_STATUSES,
            "delivery_status",
            "$.propulsion_delivery_status",
            "推进交付状态非法",
        )
        _require(type(self.crew_safety_lock_enabled) is bool, "crew_lock", "$.crew_safety_lock_enabled", "乘员安全锁必须为布尔值")
        _require(isinstance(self.safety_result, WholeShipPropulsionSafetyResult), "safety_result", "$.safety_result", "必须保存完整 d3.2 结果")
        _require(self.safety_result.fixed_step_index == n, "closing_step", "$.safety_result", "安全结果必须属于当前收边界")

    @property
    def sort_key(self) -> tuple[int, str]:
        return self.fixed_step_index, self.ship_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "interface": GOVERNED_CLOSING_RECORD_INTERFACE_ID,
            "policy": GOVERNED_BOUNDARY_POLICY_ID,
            "boundary_phase": "closing",
            "ship_id": self.ship_id,
            "fixed_step_index": self.fixed_step_index,
            "source_propulsion_state_sha256": self.source_propulsion_state_sha256,
            "resulting_propulsion_state_sha256": self.resulting_propulsion_state_sha256,
            "runtime_parameters_sha256": self.runtime_parameters_sha256,
            "motion_state_sha256": self.motion_state_sha256,
            "propulsion_delivery_status": self.propulsion_delivery_status,
            "crew_safety_lock_enabled": self.crew_safety_lock_enabled,
            "safety_result": self.safety_result.to_dict(),
        }

    @classmethod
    def parse(cls, value: Any, path: str = "$") -> "GovernedPropulsionClosingRecord":
        obj = exact_object(
            value,
            {
                "interface",
                "policy",
                "boundary_phase",
                "ship_id",
                "fixed_step_index",
                "source_propulsion_state_sha256",
                "resulting_propulsion_state_sha256",
                "runtime_parameters_sha256",
                "motion_state_sha256",
                "propulsion_delivery_status",
                "crew_safety_lock_enabled",
                "safety_result",
            },
            path,
        )
        _require(
            obj["interface"] == GOVERNED_CLOSING_RECORD_INTERFACE_ID
            and obj["policy"] == GOVERNED_BOUNDARY_POLICY_ID
            and obj["boundary_phase"] == "closing",
            "closing_interface",
            path,
            "收边界记录版本、策略或阶段不匹配",
        )
        return cls(
            obj["ship_id"],
            obj["fixed_step_index"],
            obj["source_propulsion_state_sha256"],
            obj["resulting_propulsion_state_sha256"],
            obj["runtime_parameters_sha256"],
            obj["motion_state_sha256"],
            obj["propulsion_delivery_status"],
            obj["crew_safety_lock_enabled"],
            WholeShipPropulsionSafetyResult.parse(obj["safety_result"], f"{path}.safety_result"),
        )


@dataclass(frozen=True)
class GovernedScenePropulsionSafetyEvent:
    ship_id: str
    boundary_phase: str
    intent: ChannelSafetyEventIntent

    def __post_init__(self) -> None:
        _resource_id(self.ship_id, "$.ship_id")
        _require(self.boundary_phase == "closing", "safety_event_phase", "$.boundary_phase", "安全事件只能属于收边界")
        _require(isinstance(self.intent, ChannelSafetyEventIntent), "safety_event", "$.intent", "必须包装严格通道安全意图")

    @property
    def sort_key(self) -> tuple[int, int, str, int, str]:
        event = self.intent.event
        return (
            event.fixed_step_index,
            1,
            self.ship_id,
            DIRECTIONAL_CHANNELS.index(self.intent.command_channel),
            event.kind,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "interface": GOVERNED_SAFETY_EVENT_INTERFACE_ID,
            "ship_id": self.ship_id,
            "boundary_phase": self.boundary_phase,
            "intent": self.intent.to_dict(),
        }

    @classmethod
    def parse(cls, value: Any, path: str = "$") -> "GovernedScenePropulsionSafetyEvent":
        obj = exact_object(value, {"interface", "ship_id", "boundary_phase", "intent"}, path)
        _require(obj["interface"] == GOVERNED_SAFETY_EVENT_INTERFACE_ID, "safety_event_interface", path, "安全事件版本不匹配")
        return cls(
            obj["ship_id"],
            obj["boundary_phase"],
            ChannelSafetyEventIntent.parse(obj["intent"], f"{path}.intent"),
        )


def _parse_vector(value: Any, path: str) -> Vec2:
    obj = exact_object(value, {"x", "y"}, path)
    return Vec2(_finite(obj["x"], f"{path}.x"), _finite(obj["y"], f"{path}.y"))


def _parse_drag(value: Any, path: str) -> DragBreakdown:
    keys = {
        "beta_deg",
        "speed_mps",
        "mach",
        "reynolds_number",
        "skin_friction_coefficient",
        "form_area_m2",
        "skin_area_m2",
        "wave_area_m2",
        "equivalent_drag_area_m2",
        "drag_force_n",
    }
    obj = exact_object(value, keys, path)
    return DragBreakdown(**{key: _finite(obj[key], f"{path}.{key}") for key in keys})


def _validate_actual_diagnostic(value: ActualTacticalStepDiagnostics) -> None:
    _require(isinstance(value, ActualTacticalStepDiagnostics), "diagnostic_type", "$.diagnostic", "必须提供实际积分诊断")
    _require(value.resulting_fixed_step_index == value.request.source_fixed_step_index + 1, "diagnostic_step", "$.diagnostic", "诊断步号必须紧接执行请求")
    _finite(value.requested_fuel_units, "$.requested_fuel_units", 0)
    _finite(value.fuel_delivery_fraction, "$.fuel_delivery_fraction", 0, 1)
    _finite(value.structure_ratio, "$.structure_ratio", 0)
    _finite(value.crew_g, "$.crew_g", 1)
    _finite(value.hull_integrity_damage, "$.hull_integrity_damage", 0)
    _finite(value.fuel_units_consumed, "$.fuel_units_consumed", 0)
    _finite(value.active_force_body_n.x, "$.active_force_body_n.x")
    _finite(value.active_force_body_n.y, "$.active_force_body_n.y")
    _finite(value.active_torque_n_m, "$.active_torque_n_m")
    _finite(value.drag_force_world_n.x, "$.drag_force_world_n.x")
    _finite(value.drag_force_world_n.y, "$.drag_force_world_n.y")
    for key, item in value.drag_breakdown.__dict__.items():
        _finite(item, f"$.drag_breakdown.{key}")


@dataclass(frozen=True)
class GovernedActualTacticalStepDiagnostics:
    diagnostic: ActualTacticalStepDiagnostics
    source_propulsion_state_sha256: str
    source_governors_sha256: str

    def __post_init__(self) -> None:
        _validate_actual_diagnostic(self.diagnostic)
        _hash(self.source_propulsion_state_sha256, "$.source_propulsion_state_sha256")
        _hash(self.source_governors_sha256, "$.source_governors_sha256")

    def to_dict(self) -> dict[str, Any]:
        result = self.diagnostic.to_dict()
        result.update(
            interface=GOVERNED_DIAGNOSTIC_INTERFACE_ID,
            policy=GOVERNED_DIAGNOSTIC_POLICY_ID,
            source_propulsion_state_sha256=self.source_propulsion_state_sha256,
            source_governors_sha256=self.source_governors_sha256,
            soft_governor_status="wired",
            hard_fault_status="unwired",
            direction_interlock_status="unwired",
        )
        return result

    @classmethod
    def parse(cls, value: Any, path: str = "$") -> "GovernedActualTacticalStepDiagnostics":
        base_keys = {
            "request",
            "resulting_fixed_step_index",
            "requested_fuel_units",
            "fuel_delivery_fraction",
            "structure_ratio",
            "crew_g",
            "hull_integrity_damage",
            "fuel_units_consumed",
            "active_force_body_n",
            "active_torque_n_m",
            "drag_force_world_n",
            "drag_breakdown",
            "interface",
            "policy",
            "source_propulsion_state_sha256",
            "source_governors_sha256",
            "soft_governor_status",
            "hard_fault_status",
            "direction_interlock_status",
        }
        obj = exact_object(value, base_keys, path)
        _require(obj["interface"] == GOVERNED_DIAGNOSTIC_INTERFACE_ID, "diagnostic_interface", path, "诊断版本不匹配")
        _require(obj["policy"] == GOVERNED_DIAGNOSTIC_POLICY_ID, "diagnostic_policy", path, "诊断策略不匹配")
        _require(obj["soft_governor_status"] == "wired", "diagnostic_soft_status", path, "软 governor 必须明确已接线")
        _require(obj["hard_fault_status"] == "unwired", "diagnostic_hard_status", path, "硬故障必须保持未接线")
        _require(obj["direction_interlock_status"] == "unwired", "diagnostic_interlock_status", path, "方向互锁必须保持未接线")
        request = ActualActuationRequest.parse(obj["request"], f"{path}.request")
        diagnostic = ActualTacticalStepDiagnostics(
            request,
            _step(obj["resulting_fixed_step_index"], f"{path}.resulting_fixed_step_index"),
            obj["requested_fuel_units"],
            obj["fuel_delivery_fraction"],
            obj["structure_ratio"],
            obj["crew_g"],
            obj["hull_integrity_damage"],
            obj["fuel_units_consumed"],
            _parse_vector(obj["active_force_body_n"], f"{path}.active_force_body_n"),
            obj["active_torque_n_m"],
            _parse_vector(obj["drag_force_world_n"], f"{path}.drag_force_world_n"),
            _parse_drag(obj["drag_breakdown"], f"{path}.drag_breakdown"),
        )
        return cls(diagnostic, obj["source_propulsion_state_sha256"], obj["source_governors_sha256"])


@dataclass(frozen=True)
class GovernedSceneSave:
    scene: dict[str, Any]

    def __post_init__(self) -> None:
        _require(isinstance(self.scene, dict), "save_scene", "$.scene", "场景必须是对象")
        _require(self.scene.get("interface") == GOVERNED_SCENE_INTERFACE_ID, "save_scene_interface", "$.scene.interface", "只接受 v6 受控场景")
        _require(self.scene.get("policy") == GOVERNED_SCENE_POLICY_ID, "save_scene_policy", "$.scene.policy", "受控场景策略不匹配")
        GovernedPropulsionExecutionPolicy.parse(self.scene.get("propulsion_governance"), "$.scene.propulsion_governance")
        n = _step(self.scene.get("fixed_step_index"), "$.scene.fixed_step_index")
        ships = _array(self.scene.get("ships"), "$.scene.ships")
        _require(bool(ships), "save_ship_set", "$.scene.ships", "场景必须至少包含一艘舰")
        for index, ship in enumerate(ships):
            _require(isinstance(ship, dict), "save_ship", f"$.scene.ships[{index}]", "舰艇必须是对象")
            propulsion = ship.get("propulsion_state")
            _require(isinstance(propulsion, dict), "save_propulsion", f"$.scene.ships[{index}].propulsion_state", "受控场景必须保存推进状态")
            engines = _array(propulsion.get("engines"), f"$.scene.ships[{index}].propulsion_state.engines")
            _require(not any(engine.get("phase") == "tripped" for engine in engines if isinstance(engine, dict)), "tripped_unwired", f"$.scene.ships[{index}].propulsion_state.engines", "d4 前受控存档拒绝 tripped 状态")
            governors = _governors(propulsion.get("governors"), f"$.scene.ships[{index}].propulsion_state.governors")
            control = DirectionalPropulsionControlInput.parse(ship.get("propulsion_control"), f"$.scene.ships[{index}].propulsion_control")
            _require(tuple(item.command for item in governors) == control.channel_commands, "save_control_chain", f"$.scene.ships[{index}]", "持久控制与 governor 命令不一致")
            lifecycle = ship.get("lifecycle_state")
            _require(isinstance(lifecycle, dict), "save_lifecycle", f"$.scene.ships[{index}].lifecycle_state", "生命周期必须是对象")
            physical_status = lifecycle.get("physical_status")
            if physical_status != "exited":
                _require(all(item.last_evaluated_step_index == n for item in governors), "save_governor_clock", f"$.scene.ships[{index}].propulsion_state.governors", "非退出舰 governor 必须提交到场景边界")

    def to_dict(self) -> dict[str, Any]:
        return {
            "interface": GOVERNED_SCENE_SAVE_INTERFACE_ID,
            "scene": self.scene,
            "scene_sha256": canonical_sha256(self.scene),
        }

    @classmethod
    def parse(cls, value: Any, path: str = "$") -> "GovernedSceneSave":
        obj = exact_object(value, {"interface", "scene", "scene_sha256"}, path)
        _require(obj["interface"] == GOVERNED_SCENE_SAVE_INTERFACE_ID, "save_interface", path, "存档版本不匹配")
        _hash(obj["scene_sha256"], f"{path}.scene_sha256")
        _require(canonical_sha256(obj["scene"]) == obj["scene_sha256"], "save_hash", f"{path}.scene_sha256", "场景内容指纹不匹配")
        return cls(obj["scene"])


_OPTIONAL_STEP_EVENT_KEYS = frozenset(
    {
        "guidance_events",
        "continuous_damage_events",
        "crew_casualty_events",
        "crew_evacuation_events",
        "crew_rescue_manifests",
        "fire_propagation_events",
        "ammunition_cookoff_events",
        "sensor_observation_events",
        "radar_emission_events",
        "fire_control_support_events",
        "generated_guidance_fact_events",
    }
)
_REQUIRED_STEP_KEYS = frozenset(
    {
        "interface",
        "policy",
        "source_scene_sha256",
        "resulting_scene_sha256",
        "source_fixed_step_index",
        "resulting_fixed_step_index",
        "weapon_events",
        "spawned_projectiles",
        "impact_events",
        "expired_events",
        "lifecycle_events",
        "engagement_events",
        "ship_results",
        "propulsion_opening_records",
        "propulsion_closing_records",
        "propulsion_events",
        "propulsion_safety_events",
        "soft_governor_status",
        "hard_fault_status",
        "direction_interlock_status",
    }
)


def validate_governed_scene_step_contract(value: Any, path: str = "$") -> None:
    """验证 v5 结果的版本与新审计包络；完整资源重放由场景接线层负责。"""

    _require(isinstance(value, dict), "step_object", path, "单步结果必须是对象")
    keys = set(value)
    _require(_REQUIRED_STEP_KEYS <= keys <= _REQUIRED_STEP_KEYS | _OPTIONAL_STEP_EVENT_KEYS, "step_keys", path, "单步结果字段缺失或含未知项")
    _require(value["interface"] == GOVERNED_STEP_INTERFACE_ID, "step_interface", path, "单步结果版本不匹配")
    _require(value["policy"] == GOVERNED_STEP_POLICY_ID, "step_policy", path, "单步结果策略不匹配")
    _require(value["soft_governor_status"] == "wired", "step_soft_status", path, "软 governor 必须明确已接线")
    _require(value["hard_fault_status"] == "unwired", "step_hard_status", path, "硬故障必须保持未接线")
    _require(value["direction_interlock_status"] == "unwired", "step_interlock_status", path, "方向互锁必须保持未接线")
    source_step = _step(value["source_fixed_step_index"], f"{path}.source_fixed_step_index")
    resulting_step = _step(value["resulting_fixed_step_index"], f"{path}.resulting_fixed_step_index")
    _require(resulting_step == source_step + 1, "step_sequence", path, "结果步必须紧接源步")
    _hash(value["source_scene_sha256"], f"{path}.source_scene_sha256")
    _hash(value["resulting_scene_sha256"], f"{path}.resulting_scene_sha256")
    for key in (
        "weapon_events",
        "spawned_projectiles",
        "impact_events",
        "expired_events",
        "lifecycle_events",
        "engagement_events",
        "propulsion_events",
        *_OPTIONAL_STEP_EVENT_KEYS,
    ):
        if key in value:
            _array(value[key], f"{path}.{key}")
    openings = tuple(
        GovernedPropulsionOpeningRecord.parse(item, f"{path}.propulsion_opening_records[{index}]")
        for index, item in enumerate(_array(value["propulsion_opening_records"], f"{path}.propulsion_opening_records"))
    )
    closings = tuple(
        GovernedPropulsionClosingRecord.parse(item, f"{path}.propulsion_closing_records[{index}]")
        for index, item in enumerate(_array(value["propulsion_closing_records"], f"{path}.propulsion_closing_records"))
    )
    safety_events = tuple(
        GovernedScenePropulsionSafetyEvent.parse(item, f"{path}.propulsion_safety_events[{index}]")
        for index, item in enumerate(_array(value["propulsion_safety_events"], f"{path}.propulsion_safety_events"))
    )
    _require(tuple(item.sort_key for item in openings) == tuple(sorted(set(item.sort_key for item in openings))), "opening_order", f"{path}.propulsion_opening_records", "开边界记录必须唯一稳定排序")
    _require(tuple(item.sort_key for item in closings) == tuple(sorted(set(item.sort_key for item in closings))), "closing_order", f"{path}.propulsion_closing_records", "收边界记录必须唯一稳定排序")
    _require(all(item.fixed_step_index == source_step for item in openings), "opening_step", f"{path}.propulsion_opening_records", "开边界记录必须属于源步")
    _require(all(item.fixed_step_index == resulting_step for item in closings), "closing_step", f"{path}.propulsion_closing_records", "收边界记录必须属于结果步")
    _require(tuple(item.sort_key for item in safety_events) == tuple(sorted(set(item.sort_key for item in safety_events))), "safety_event_order", f"{path}.propulsion_safety_events", "安全事件必须唯一稳定排序")
    expected_events = tuple(
        GovernedScenePropulsionSafetyEvent(item.ship_id, "closing", intent)
        for item in closings
        for intent in item.safety_result.event_intents
    )
    _require(safety_events == tuple(sorted(expected_events, key=lambda item: item.sort_key)), "safety_event_chain", f"{path}.propulsion_safety_events", "场景安全事件必须逐项对应收边界最终意图")
    ship_results = _array(value["ship_results"], f"{path}.ship_results")
    result_by_ship: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(ship_results):
        item_path = f"{path}.ship_results[{index}]"
        obj = exact_object(
            item,
            {
                "ship_id",
                "resulting_runtime_parameters_sha256",
                "diagnostics",
                "propulsion_aggregation",
                "propulsion_delivery_status",
                "missing_propulsion_channels",
            },
            item_path,
        )
        ship_id = _resource_id(obj["ship_id"], f"{item_path}.ship_id")
        _require(ship_id not in result_by_ship, "ship_result_duplicate", f"{item_path}.ship_id", "逐舰结果不得重复")
        result_by_ship[ship_id] = obj
        _hash(obj["resulting_runtime_parameters_sha256"], f"{item_path}.resulting_runtime_parameters_sha256")
        _require(obj["propulsion_delivery_status"] in PROPULSION_DELIVERY_STATUSES, "ship_delivery", f"{item_path}.propulsion_delivery_status", "推进交付状态非法")
        exited = obj["propulsion_delivery_status"] == "suppressed_exited"
        _require(
            (exited and obj["propulsion_aggregation"] is None)
            or (not exited and isinstance(obj["propulsion_aggregation"], dict)),
            "ship_aggregation",
            f"{item_path}.propulsion_aggregation",
            "只有退出舰省略实际推进聚合",
        )
        missing = _array(obj["missing_propulsion_channels"], f"{item_path}.missing_propulsion_channels")
        _require(tuple(missing) == tuple(channel for channel in DIRECTIONAL_CHANNELS if channel in missing), "missing_channel_order", f"{item_path}.missing_propulsion_channels", "缺失通道必须唯一并按规范顺序排列")
        diagnostics = obj["diagnostics"]
        _require((diagnostics is None) == exited, "ship_diagnostics", f"{item_path}.diagnostics", "只有退出舰省略积分诊断")
        if not exited:
            GovernedActualTacticalStepDiagnostics.parse(diagnostics, f"{item_path}.diagnostics")
    result_ids = tuple(result_by_ship)
    _require(result_ids == tuple(sorted(result_ids)), "ship_result_order", f"{path}.ship_results", "逐舰结果必须按舰艇 id 稳定排序")
    active_ids = tuple(ship_id for ship_id in result_ids if result_by_ship[ship_id]["propulsion_delivery_status"] != "suppressed_exited")
    opening_ids = tuple(item.ship_id for item in openings)
    closing_ids = tuple(item.ship_id for item in closings)
    _require(opening_ids == active_ids == closing_ids, "boundary_ship_set", path, "每个非退出舰必须恰有一条开边界和收边界记录，退出舰必须冻结")
    for opening, closing in zip(openings, closings):
        result = result_by_ship[opening.ship_id]
        diagnostic = GovernedActualTacticalStepDiagnostics.parse(result["diagnostics"], f"{path}.ship_results.{opening.ship_id}.diagnostics")
        _require(opening.resulting_propulsion_state_sha256 == closing.source_propulsion_state_sha256, "boundary_state_chain", path, "开边界结果必须是收边界安全判定源状态")
        _require(diagnostic.source_propulsion_state_sha256 == opening.resulting_propulsion_state_sha256, "diagnostic_state_chain", path, "积分诊断必须绑定开边界后的受控推进状态")
        expected_governors_sha256 = canonical_sha256([item.to_dict() for item in opening.resulting_governors])
        _require(diagnostic.source_governors_sha256 == expected_governors_sha256, "diagnostic_governor_chain", path, "积分诊断必须绑定开边界后的六通道 governor")
        _require(closing.runtime_parameters_sha256 == result["resulting_runtime_parameters_sha256"], "closing_runtime_chain", path, "收边界记录必须绑定逐舰结果运行时")
        _require(closing.propulsion_delivery_status == result["propulsion_delivery_status"], "closing_delivery_chain", path, "收边界记录与逐舰交付状态不一致")
