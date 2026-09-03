"""T0b.2d1r：版本化推进场景持久状态与 c2b→d1 显式迁移合同。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from 高天荒野舰艇推进通道合同 import (
    DIRECTIONAL_CHANNELS, DIRECTIONAL_STATE_INTERFACE_ID, DIRECTIONAL_EVENT_INTERFACE_ID,
    DirectionalPropulsionGovernorState, OPPOSING_CHANNEL_PAIRS,
)

from 高天荒野舰艇数据契约 import (
    ContractError,
    RESOURCE_ID_PATTERN,
    canonical_sha256,
)
from 高天荒野舰艇推进安全判定器 import (
    HARD_LIMIT_REASON_ORDER,
    SOFT_LIMIT_REASON_ORDER,
    TELEGRAPH_NOTCHES,
    THRUST_OUTPUT_STAGES_PERCENT,
)


C2B_ENGINE_RUNTIME_STATE_INTERFACE_ID = "gaotian.engine-runtime-state/v1alpha1"
ENGINE_RUNTIME_STATE_INTERFACE_ID = "gaotian.engine-runtime-state/v2alpha1"
PROPULSION_GOVERNOR_STATE_INTERFACE_ID = (
    "gaotian.propulsion-governor-state/v1alpha1"
)
C2B_TACTICAL_PROPULSION_STATE_INTERFACE_ID = (
    "gaotian.tactical-propulsion-state/v1alpha1"
)
TACTICAL_PROPULSION_STATE_INTERFACE_ID = (
    "gaotian.tactical-propulsion-state/v2alpha1"
)
PROPULSION_STATE_EVENT_INTERFACE_ID = (
    "gaotian.propulsion-state-event/v1alpha1"
)

ENGINE_PHASES = (
    "off",
    "starting",
    "ready",
    "running",
    "stopping",
    "tripped",
)
PROPULSION_ACTUATOR_CATEGORIES = ("main_engine", "maneuver_thruster")
PROPULSION_COMMAND_CHANNELS = ("forward", "reverse", "left", "right")
PROPULSION_EVENT_KIND_ORDER = (
    "engine_start_requested",
    "engine_start_completed",
    "engine_safety_limit_engaged",
    "engine_safety_limit_changed",
    "engine_safety_limit_released",
    "engine_output_stage_changed",
    "engine_stop_requested",
    "engine_stopped",
    "engine_tripped",
    "engine_reset",
)
PROPULSION_EVENT_REASON_ORDER = (
    *SOFT_LIMIT_REASON_ORDER,
    *HARD_LIMIT_REASON_ORDER,
)
SAFETY_EVENT_KINDS = frozenset(
    {
        "engine_safety_limit_engaged",
        "engine_safety_limit_changed",
        "engine_safety_limit_released",
    }
)


def _exact_object(
    value: Any,
    keys: set[str],
    path: str,
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ContractError("object.keys", path, f"必须恰含 {sorted(keys)}")
    return value


def _resource_id(value: Any, path: str) -> str:
    if not isinstance(value, str) or not RESOURCE_ID_PATTERN.fullmatch(value):
        raise ContractError("resource.id_invalid", path, str(value))
    return value


def _integer(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ContractError("type.integer", path, "必须是非负整数")
    return value


def _optional_integer(value: Any, path: str) -> int | None:
    return None if value is None else _integer(value, path)


def _stage(value: Any, path: str) -> int:
    if type(value) is not int or value not in THRUST_OUTPUT_STAGES_PERCENT:
        raise ContractError(
            "propulsion_state.output_stage",
            path,
            "必须是规范离散推力阶段",
        )
    return value


def _optional_stage(value: Any, path: str) -> int | None:
    return None if value is None else _stage(value, path)


def _optional_enum(
    value: Any,
    allowed: tuple[str, ...],
    code: str,
    path: str,
) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or value not in allowed:
        raise ContractError(code, path, str(value))
    return value


def _ordered_reasons(
    value: Any,
    order: tuple[str, ...],
    path: str,
) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ContractError("type.string_array", path, "必须是字符串数组")
    reasons = tuple(value)
    expected = tuple(item for item in order if item in set(reasons))
    if reasons != expected or len(set(reasons)) != len(reasons):
        raise ContractError(
            "propulsion_state.reason_order",
            path,
            "原因必须按稳定枚举顺序排列且不得重复",
        )
    return reasons


@dataclass(frozen=True)
class EngineRuntimeState:
    actuator_instance_id: str
    actuator_category: str
    phase: str
    commanded_notch: str | None
    target_output_percent: int
    actual_output_percent: int
    ready_at_fixed_step: int | None
    next_transition_step: int | None
    response_started_at_fixed_step: int | None = None
    response_start_output_percent: int | None = None
    interface_id: str = ENGINE_RUNTIME_STATE_INTERFACE_ID

    def __post_init__(self) -> None:
        if self.interface_id not in {
            C2B_ENGINE_RUNTIME_STATE_INTERFACE_ID,
            ENGINE_RUNTIME_STATE_INTERFACE_ID,
        }:
            raise ValueError("engine runtime interface 非法")
        if (
            self.interface_id == C2B_ENGINE_RUNTIME_STATE_INTERFACE_ID
            and (
                self.response_started_at_fixed_step is not None
                or self.response_start_output_percent is not None
            )
        ):
            raise ValueError("c2b engine runtime 不得携带 d1 响应排程锚点")
        if (
            not isinstance(self.actuator_instance_id, str)
            or not RESOURCE_ID_PATTERN.fullmatch(self.actuator_instance_id)
        ):
            raise ValueError("actuator_instance_id 非法")
        if self.actuator_category not in PROPULSION_ACTUATOR_CATEGORIES:
            raise ValueError("actuator_category 非法")
        if self.phase not in ENGINE_PHASES:
            raise ValueError("phase 非法")
        if self.actuator_category == "main_engine":
            if self.commanded_notch not in TELEGRAPH_NOTCHES:
                raise ValueError("主发动机必须保存规范车钟档位")
        elif self.commanded_notch is not None:
            raise ValueError("姿态推进器不得保存主发动机车钟档位")
        for value in (self.target_output_percent, self.actual_output_percent):
            if type(value) is not int or value not in THRUST_OUTPUT_STAGES_PERCENT:
                raise ValueError("输出必须位于规范离散阶段")
        for value in (
            self.ready_at_fixed_step,
            self.next_transition_step,
            self.response_started_at_fixed_step,
        ):
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise ValueError("转换步号必须是非负整数或 None")
        if self.phase in {"off", "tripped"}:
            if (
                self.target_output_percent != 0
                or self.actual_output_percent != 0
                or self.ready_at_fixed_step is not None
                or self.next_transition_step is not None
                or self.response_started_at_fixed_step is not None
                or self.response_start_output_percent is not None
            ):
                raise ValueError("off/tripped 必须归零且不得保留转换排程")
        elif self.phase == "starting":
            if (
                self.actual_output_percent != 0
                or self.target_output_percent == 0
                or self.ready_at_fixed_step is None
                or self.next_transition_step != self.ready_at_fixed_step
                or self.response_started_at_fixed_step is not None
                or self.response_start_output_percent is not None
            ):
                raise ValueError("starting 必须归零、保存启动完成步且不得提前建立响应排程")
        elif self.phase == "ready":
            if (
                self.target_output_percent != 0
                or self.actual_output_percent != 0
                or self.ready_at_fixed_step is None
                or self.next_transition_step is not None
                or self.response_started_at_fixed_step is not None
                or self.response_start_output_percent is not None
            ):
                raise ValueError("ready 必须归零并保存就绪步且无响应排程")
        elif self.phase == "running":
            response_active = (
                self.actual_output_percent != self.target_output_percent
            )
            if (
                self.target_output_percent == 0
                or self.ready_at_fixed_step is None
                or (
                    self.interface_id == C2B_ENGINE_RUNTIME_STATE_INTERFACE_ID
                    and self.actual_output_percent == 0
                )
                or response_active != (self.next_transition_step is not None)
                or (
                    self.interface_id == ENGINE_RUNTIME_STATE_INTERFACE_ID
                    and (
                        response_active
                        != (self.response_started_at_fixed_step is not None)
                        or response_active
                        != (self.response_start_output_percent is not None)
                    )
                )
            ):
                raise ValueError("running 的目标、实际输出与待转换步不一致")
        elif self.phase == "stopping":
            if (
                self.target_output_percent != 0
                or self.actual_output_percent == 0
                or self.ready_at_fixed_step is None
                or self.next_transition_step is None
                or (
                    self.interface_id == ENGINE_RUNTIME_STATE_INTERFACE_ID
                    and self.response_started_at_fixed_step is None
                )
                or (
                    self.interface_id == ENGINE_RUNTIME_STATE_INTERFACE_ID
                    and self.response_start_output_percent is None
                )
            ):
                raise ValueError("stopping 必须向零输出转换并保存待转换步")
        if self.response_start_output_percent is not None:
            if (
                type(self.response_start_output_percent) is not int
                or self.response_start_output_percent
                not in THRUST_OUTPUT_STAGES_PERCENT
            ):
                raise ValueError("响应起点必须位于规范离散阶段")
            if self.response_started_at_fixed_step is None:
                raise ValueError("响应起点与排程步必须同时存在")
            low = min(
                self.response_start_output_percent,
                self.target_output_percent,
            )
            high = max(
                self.response_start_output_percent,
                self.target_output_percent,
            )
            if not low <= self.actual_output_percent <= high:
                raise ValueError("实际输出必须位于响应起点与目标之间")
            if (
                self.next_transition_step is None
                or self.next_transition_step
                <= self.response_started_at_fixed_step
            ):
                raise ValueError("下次转换步必须晚于响应排程起点")

    @classmethod
    def parse(cls, value: Any, path: str) -> "EngineRuntimeState":
        if not isinstance(value, dict):
            raise ContractError("object.keys", path, "发动机状态必须是对象")
        interface = value.get("interface")
        if interface == C2B_ENGINE_RUNTIME_STATE_INTERFACE_ID:
            keys = {
                "actual_output_percent",
                "actuator_category",
                "actuator_instance_id",
                "commanded_notch",
                "interface",
                "next_transition_step",
                "phase",
                "ready_at_fixed_step",
                "target_output_percent",
            }
        elif interface == ENGINE_RUNTIME_STATE_INTERFACE_ID:
            keys = {
                "actual_output_percent",
                "actuator_category",
                "actuator_instance_id",
                "commanded_notch",
                "interface",
                "next_transition_step",
                "phase",
                "ready_at_fixed_step",
                "response_start_output_percent",
                "response_started_at_fixed_step",
                "target_output_percent",
            }
        else:
            raise ContractError(
                "propulsion_state.engine_interface",
                f"{path}.interface",
                str(interface),
            )
        obj = _exact_object(
            value,
            keys,
            path,
        )
        category = obj["actuator_category"]
        if category not in PROPULSION_ACTUATOR_CATEGORIES:
            raise ContractError(
                "propulsion_state.actuator_category",
                f"{path}.actuator_category",
                str(category),
            )
        phase = obj["phase"]
        if phase not in ENGINE_PHASES:
            raise ContractError(
                "propulsion_state.engine_phase",
                f"{path}.phase",
                str(phase),
            )
        notch = _optional_enum(
            obj["commanded_notch"],
            TELEGRAPH_NOTCHES,
            "propulsion_state.commanded_notch",
            f"{path}.commanded_notch",
        )
        try:
            return cls(
                _resource_id(
                    obj["actuator_instance_id"],
                    f"{path}.actuator_instance_id",
                ),
                category,
                phase,
                notch,
                _stage(
                    obj["target_output_percent"],
                    f"{path}.target_output_percent",
                ),
                _stage(
                    obj["actual_output_percent"],
                    f"{path}.actual_output_percent",
                ),
                _optional_integer(
                    obj["ready_at_fixed_step"],
                    f"{path}.ready_at_fixed_step",
                ),
                _optional_integer(
                    obj["next_transition_step"],
                    f"{path}.next_transition_step",
                ),
                (
                    _optional_integer(
                        obj["response_started_at_fixed_step"],
                        f"{path}.response_started_at_fixed_step",
                    )
                    if interface == ENGINE_RUNTIME_STATE_INTERFACE_ID
                    else None
                ),
                (
                    _optional_stage(
                        obj["response_start_output_percent"],
                        f"{path}.response_start_output_percent",
                    )
                    if interface == ENGINE_RUNTIME_STATE_INTERFACE_ID
                    else None
                ),
                interface,
            )
        except ContractError:
            raise
        except ValueError as error:
            raise ContractError(
                "propulsion_state.engine_invariant",
                path,
                str(error),
            ) from error

    def to_dict(self) -> dict[str, Any]:
        result = {
            "actual_output_percent": self.actual_output_percent,
            "actuator_category": self.actuator_category,
            "actuator_instance_id": self.actuator_instance_id,
            "commanded_notch": self.commanded_notch,
            "interface": self.interface_id,
            "next_transition_step": self.next_transition_step,
            "phase": self.phase,
            "ready_at_fixed_step": self.ready_at_fixed_step,
            "target_output_percent": self.target_output_percent,
        }
        if self.interface_id == ENGINE_RUNTIME_STATE_INTERFACE_ID:
            result["response_start_output_percent"] = (
                self.response_start_output_percent
            )
            result["response_started_at_fixed_step"] = (
                self.response_started_at_fixed_step
            )
        return result


def migrate_engine_runtime_state_from_module_mode(
    actuator_instance_id: str,
    actuator_category: str,
    operating_mode: str,
    fixed_step_index: int,
    *,
    interface_id: str = ENGINE_RUNTIME_STATE_INTERFACE_ID,
) -> EngineRuntimeState:
    """冻结旧实例模式到新推进初态的显式映射，不提供解析默认值。"""

    if operating_mode not in {"off", "standby", "active"}:
        raise ContractError(
            "propulsion_state.module_operating_mode",
            "$.operating_mode",
            operating_mode,
        )
    if actuator_category not in PROPULSION_ACTUATOR_CATEGORIES:
        raise ContractError(
            "propulsion_state.actuator_category",
            "$.actuator_category",
            actuator_category,
        )
    _integer(fixed_step_index, "$.fixed_step_index")
    if interface_id not in {
        C2B_ENGINE_RUNTIME_STATE_INTERFACE_ID,
        ENGINE_RUNTIME_STATE_INTERFACE_ID,
    }:
        raise ContractError(
            "propulsion_state.engine_interface",
            "$.interface",
            interface_id,
        )
    active = operating_mode == "active"
    return EngineRuntimeState(
        _resource_id(actuator_instance_id, "$.actuator_instance_id"),
        actuator_category,
        "ready" if active else "off",
        "stop" if actuator_category == "main_engine" else None,
        0,
        0,
        fixed_step_index if active else None,
        None,
        None,
        None,
        interface_id,
    )


@dataclass(frozen=True)
class PropulsionGovernorState:
    command_channel: str
    commanded_notch: str
    safety_ceiling_percent: int
    safety_reasons: tuple[str, ...]
    safety_limited_since_step: int | None
    release_candidate_since_step: int | None
    last_evaluated_step_index: int | None
    safety_revision: int

    def __post_init__(self) -> None:
        if self.command_channel not in PROPULSION_COMMAND_CHANNELS:
            raise ValueError("command_channel 非法")
        if self.commanded_notch not in TELEGRAPH_NOTCHES:
            raise ValueError("commanded_notch 非法")
        if (
            type(self.safety_ceiling_percent) is not int
            or self.safety_ceiling_percent not in THRUST_OUTPUT_STAGES_PERCENT
        ):
            raise ValueError("safety_ceiling_percent 非法")
        expected_reasons = tuple(
            item for item in SOFT_LIMIT_REASON_ORDER if item in set(self.safety_reasons)
        )
        if (
            self.safety_reasons != expected_reasons
            or len(set(self.safety_reasons)) != len(self.safety_reasons)
        ):
            raise ValueError("safety_reasons 未按稳定顺序排列")
        limited = self.safety_ceiling_percent < 100
        if limited != bool(self.safety_reasons):
            raise ValueError("安全上限与原因不一致")
        if limited != (self.safety_limited_since_step is not None):
            raise ValueError("安全上限与介入步不一致")
        if not limited and self.release_candidate_since_step is not None:
            raise ValueError("未受限状态不得保存释放候选步")
        for value in (
            self.safety_limited_since_step,
            self.release_candidate_since_step,
            self.last_evaluated_step_index,
        ):
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise ValueError("governor 步号必须是非负整数或 None")
        if (
            self.release_candidate_since_step is not None
            and self.safety_limited_since_step is not None
            and self.release_candidate_since_step < self.safety_limited_since_step
        ):
            raise ValueError("释放候选步不得早于介入步")
        if (
            isinstance(self.safety_revision, bool)
            or not isinstance(self.safety_revision, int)
            or self.safety_revision < 0
        ):
            raise ValueError("safety_revision 必须是非负整数")

    @classmethod
    def initial(cls, command_channel: str) -> "PropulsionGovernorState":
        return cls(command_channel, "stop", 100, (), None, None, None, 0)

    @classmethod
    def parse(cls, value: Any, path: str) -> "PropulsionGovernorState":
        obj = _exact_object(
            value,
            {
                "command_channel",
                "commanded_notch",
                "interface",
                "last_evaluated_step_index",
                "release_candidate_since_step",
                "safety_ceiling_percent",
                "safety_limited_since_step",
                "safety_reasons",
                "safety_revision",
            },
            path,
        )
        if obj["interface"] != PROPULSION_GOVERNOR_STATE_INTERFACE_ID:
            raise ContractError(
                "propulsion_state.governor_interface",
                f"{path}.interface",
                str(obj["interface"]),
            )
        channel = obj["command_channel"]
        if channel not in PROPULSION_COMMAND_CHANNELS:
            raise ContractError(
                "propulsion_state.command_channel",
                f"{path}.command_channel",
                str(channel),
            )
        notch = obj["commanded_notch"]
        if notch not in TELEGRAPH_NOTCHES:
            raise ContractError(
                "propulsion_state.commanded_notch",
                f"{path}.commanded_notch",
                str(notch),
            )
        reasons = _ordered_reasons(
            obj["safety_reasons"],
            SOFT_LIMIT_REASON_ORDER,
            f"{path}.safety_reasons",
        )
        try:
            return cls(
                channel,
                notch,
                _stage(
                    obj["safety_ceiling_percent"],
                    f"{path}.safety_ceiling_percent",
                ),
                reasons,
                _optional_integer(
                    obj["safety_limited_since_step"],
                    f"{path}.safety_limited_since_step",
                ),
                _optional_integer(
                    obj["release_candidate_since_step"],
                    f"{path}.release_candidate_since_step",
                ),
                _optional_integer(
                    obj["last_evaluated_step_index"],
                    f"{path}.last_evaluated_step_index",
                ),
                _integer(obj["safety_revision"], f"{path}.safety_revision"),
            )
        except ContractError:
            raise
        except ValueError as error:
            raise ContractError(
                "propulsion_state.governor_invariant",
                path,
                str(error),
            ) from error

    def to_dict(self) -> dict[str, Any]:
        return {
            "command_channel": self.command_channel,
            "commanded_notch": self.commanded_notch,
            "interface": PROPULSION_GOVERNOR_STATE_INTERFACE_ID,
            "last_evaluated_step_index": self.last_evaluated_step_index,
            "release_candidate_since_step": self.release_candidate_since_step,
            "safety_ceiling_percent": self.safety_ceiling_percent,
            "safety_limited_since_step": self.safety_limited_since_step,
            "safety_reasons": list(self.safety_reasons),
            "safety_revision": self.safety_revision,
        }


@dataclass(frozen=True)
class TacticalPropulsionState:
    engines: tuple[EngineRuntimeState, ...]
    governors: tuple[PropulsionGovernorState | DirectionalPropulsionGovernorState, ...]
    interface_id: str = TACTICAL_PROPULSION_STATE_INTERFACE_ID

    def __post_init__(self) -> None:
        if self.interface_id not in {
            C2B_TACTICAL_PROPULSION_STATE_INTERFACE_ID,
            TACTICAL_PROPULSION_STATE_INTERFACE_ID,
            DIRECTIONAL_STATE_INTERFACE_ID,
        }:
            raise ValueError("tactical propulsion interface 非法")
        expected_engine_interface = (
            C2B_ENGINE_RUNTIME_STATE_INTERFACE_ID
            if self.interface_id == C2B_TACTICAL_PROPULSION_STATE_INTERFACE_ID
            else ENGINE_RUNTIME_STATE_INTERFACE_ID
        )
        if any(
            engine.interface_id != expected_engine_interface
            for engine in self.engines
        ):
            raise ValueError("推进聚合状态与发动机状态 interface 不匹配")
        engine_ids = tuple(item.actuator_instance_id for item in self.engines)
        if not engine_ids:
            raise ValueError("推进状态必须至少包含一个执行器")
        if engine_ids != tuple(sorted(engine_ids)) or len(set(engine_ids)) != len(
            engine_ids
        ):
            raise ValueError("执行器必须按 id 排序且不得重复")
        directional = self.interface_id == DIRECTIONAL_STATE_INTERFACE_ID
        expected_governor = DirectionalPropulsionGovernorState if directional else PropulsionGovernorState
        if any(type(item) is not expected_governor for item in self.governors):
            raise ValueError("推进组合状态与 governor 版本不匹配")
        channels = tuple(item.command_channel for item in self.governors)
        if channels != (DIRECTIONAL_CHANNELS if directional else PROPULSION_COMMAND_CHANNELS):
            raise ValueError("governor 必须恰按稳定顺序覆盖当前版本的全部通道")
        if directional:
            requests = {item.command_channel: item.command.requested_percent for item in self.governors}
            if any(requests[a] and requests[b] for a, b in OPPOSING_CHANNEL_PAIRS):
                raise ValueError("定向状态不得保存同轴对向请求")

    @property
    def source_sha256(self) -> str:
        return canonical_sha256(self)

    @classmethod
    def parse(cls, value: Any, path: str) -> "TacticalPropulsionState":
        obj = _exact_object(value, {"engines", "governors", "interface"}, path)
        interface = obj["interface"]
        if interface not in {
            C2B_TACTICAL_PROPULSION_STATE_INTERFACE_ID,
            TACTICAL_PROPULSION_STATE_INTERFACE_ID,
            DIRECTIONAL_STATE_INTERFACE_ID,
        }:
            raise ContractError(
                "propulsion_state.interface",
                f"{path}.interface",
                str(interface),
            )
        if not isinstance(obj["engines"], list) or not isinstance(
            obj["governors"], list
        ):
            raise ContractError("type.array", path, "engines/governors 必须是数组")
        engines = tuple(
            EngineRuntimeState.parse(item, f"{path}.engines[{index}]")
            for index, item in enumerate(obj["engines"])
        )
        governor_type = DirectionalPropulsionGovernorState if interface == DIRECTIONAL_STATE_INTERFACE_ID else PropulsionGovernorState
        governors = tuple(
            governor_type.parse(item, f"{path}.governors[{index}]")
            for index, item in enumerate(obj["governors"])
        )
        try:
            return cls(engines, governors, interface)
        except ContractError:
            raise
        except ValueError as error:
            raise ContractError(
                "propulsion_state.collection_invariant",
                path,
                str(error),
            ) from error

    def to_dict(self) -> dict[str, Any]:
        return {
            "engines": [item.to_dict() for item in self.engines],
            "governors": [item.to_dict() for item in self.governors],
            "interface": self.interface_id,
        }


def migrate_tactical_propulsion_state_c2b_to_d1(
    state: TacticalPropulsionState,
) -> TacticalPropulsionState:
    """把严格 c2b 状态升级为 d1 形状；不得猜测缺失的运行中排程。"""

    if state.interface_id != C2B_TACTICAL_PROPULSION_STATE_INTERFACE_ID:
        raise ContractError(
            "propulsion_state.c2b_migration_source_interface",
            "$.interface",
            state.interface_id,
        )
    migrated_engines: list[EngineRuntimeState] = []
    for engine in state.engines:
        if engine.phase in {"running", "stopping"}:
            raise ContractError(
                "propulsion_state.c2b_migration_ambiguous_schedule",
                f"$.engines.{engine.actuator_instance_id}",
                "c2b 运行中状态没有足够信息恢复 d1 响应排程锚点",
            )
        migrated_engines.append(
            EngineRuntimeState(
                engine.actuator_instance_id,
                engine.actuator_category,
                engine.phase,
                engine.commanded_notch,
                engine.target_output_percent,
                engine.actual_output_percent,
                engine.ready_at_fixed_step,
                engine.next_transition_step,
                None,
                None,
                ENGINE_RUNTIME_STATE_INTERFACE_ID,
            )
        )
    return TacticalPropulsionState(
        tuple(migrated_engines),
        state.governors,
        TACTICAL_PROPULSION_STATE_INTERFACE_ID,
    )


@dataclass(frozen=True)
class PropulsionStateEvent:
    fixed_step_index: int
    actuator_instance_id: str
    command_channel: str | None
    kind: str
    previous_phase: str | None
    resulting_phase: str | None
    previous_stage_percent: int | None
    resulting_stage_percent: int | None
    reasons: tuple[str, ...]
    interface_id: str = PROPULSION_STATE_EVENT_INTERFACE_ID

    def __post_init__(self) -> None:
        if self.interface_id not in (PROPULSION_STATE_EVENT_INTERFACE_ID, DIRECTIONAL_EVENT_INTERFACE_ID):
            raise ValueError("推进事件 interface 非法")
        if (
            isinstance(self.fixed_step_index, bool)
            or not isinstance(self.fixed_step_index, int)
            or self.fixed_step_index < 0
        ):
            raise ValueError("fixed_step_index 非法")
        if (
            not isinstance(self.actuator_instance_id, str)
            or not RESOURCE_ID_PATTERN.fullmatch(self.actuator_instance_id)
        ):
            raise ValueError("actuator_instance_id 非法")
        if self.command_channel is not None and self.command_channel not in (
            DIRECTIONAL_CHANNELS if self.interface_id == DIRECTIONAL_EVENT_INTERFACE_ID else PROPULSION_COMMAND_CHANNELS
        ):
            raise ValueError("command_channel 非法")
        if self.kind not in PROPULSION_EVENT_KIND_ORDER:
            raise ValueError("kind 非法")
        phases_present = self.previous_phase is not None or self.resulting_phase is not None
        if phases_present and (
            self.previous_phase not in ENGINE_PHASES
            or self.resulting_phase not in ENGINE_PHASES
            or self.previous_phase == self.resulting_phase
        ):
            raise ValueError("事件阶段必须成对存在且发生变化")
        stages_present = (
            self.previous_stage_percent is not None
            or self.resulting_stage_percent is not None
        )
        if stages_present and (
            type(self.previous_stage_percent) is not int
            or type(self.resulting_stage_percent) is not int
            or self.previous_stage_percent not in THRUST_OUTPUT_STAGES_PERCENT
            or self.resulting_stage_percent not in THRUST_OUTPUT_STAGES_PERCENT
            or self.previous_stage_percent == self.resulting_stage_percent
        ):
            raise ValueError("事件输出阶段必须成对存在且发生变化")
        if not phases_present and not stages_present:
            raise ValueError("事件必须至少记录阶段或输出变化")
        expected_reasons = tuple(
            item for item in PROPULSION_EVENT_REASON_ORDER if item in set(self.reasons)
        )
        if self.reasons != expected_reasons or len(set(self.reasons)) != len(
            self.reasons
        ):
            raise ValueError("事件原因未按稳定顺序排列")
        if self.kind in SAFETY_EVENT_KINDS:
            if self.command_channel is None or phases_present:
                raise ValueError("安全事件必须绑定命令通道且不得声明 phase 变化")
        elif self.kind == "engine_output_stage_changed":
            if not stages_present or phases_present or self.reasons:
                raise ValueError("输出阶段事件只能记录无原因的输出变化")
        elif not phases_present:
            raise ValueError("发动机生命周期事件必须记录 phase 变化")

    @property
    def sort_key(self) -> tuple[int, str, int]:
        return (
            self.fixed_step_index,
            self.actuator_instance_id,
            PROPULSION_EVENT_KIND_ORDER.index(self.kind),
        )

    @classmethod
    def parse(cls, value: Any, path: str) -> "PropulsionStateEvent":
        obj = _exact_object(
            value,
            {
                "actuator_instance_id",
                "command_channel",
                "fixed_step_index",
                "interface",
                "kind",
                "previous_phase",
                "previous_stage_percent",
                "reasons",
                "resulting_phase",
                "resulting_stage_percent",
            },
            path,
        )
        if obj["interface"] not in (PROPULSION_STATE_EVENT_INTERFACE_ID, DIRECTIONAL_EVENT_INTERFACE_ID):
            raise ContractError(
                "propulsion_event.interface",
                f"{path}.interface",
                str(obj["interface"]),
            )
        kind = obj["kind"]
        if kind not in PROPULSION_EVENT_KIND_ORDER:
            raise ContractError(
                "propulsion_event.kind",
                f"{path}.kind",
                str(kind),
            )
        try:
            return cls(
                _integer(obj["fixed_step_index"], f"{path}.fixed_step_index"),
                _resource_id(
                    obj["actuator_instance_id"],
                    f"{path}.actuator_instance_id",
                ),
                _optional_enum(
                    obj["command_channel"],
                    DIRECTIONAL_CHANNELS if obj["interface"] == DIRECTIONAL_EVENT_INTERFACE_ID else PROPULSION_COMMAND_CHANNELS,
                    "propulsion_state.command_channel",
                    f"{path}.command_channel",
                ),
                kind,
                _optional_enum(
                    obj["previous_phase"],
                    ENGINE_PHASES,
                    "propulsion_state.engine_phase",
                    f"{path}.previous_phase",
                ),
                _optional_enum(
                    obj["resulting_phase"],
                    ENGINE_PHASES,
                    "propulsion_state.engine_phase",
                    f"{path}.resulting_phase",
                ),
                _optional_stage(
                    obj["previous_stage_percent"],
                    f"{path}.previous_stage_percent",
                ),
                _optional_stage(
                    obj["resulting_stage_percent"],
                    f"{path}.resulting_stage_percent",
                ),
                _ordered_reasons(
                    obj["reasons"],
                    PROPULSION_EVENT_REASON_ORDER,
                    f"{path}.reasons",
                ),
                obj["interface"],
            )
        except ContractError:
            raise
        except ValueError as error:
            raise ContractError(
                "propulsion_event.invariant",
                path,
                str(error),
            ) from error

    def to_dict(self) -> dict[str, Any]:
        return {
            "actuator_instance_id": self.actuator_instance_id,
            "command_channel": self.command_channel,
            "fixed_step_index": self.fixed_step_index,
            "interface": self.interface_id,
            "kind": self.kind,
            "previous_phase": self.previous_phase,
            "previous_stage_percent": self.previous_stage_percent,
            "reasons": list(self.reasons),
            "resulting_phase": self.resulting_phase,
            "resulting_stage_percent": self.resulting_stage_percent,
        }
