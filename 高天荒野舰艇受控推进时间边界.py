"""d3.1：无场景副作用的候选/提交边界；安全判定由调用方负责。

复用 d1 的精确排程原语，保留旧公开入口的先提交语义。新入口仅提供受控提交，
不把单执行器授权误作整舰安全结论，不计算载荷、油耗、软保护或硬故障。
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from 高天荒野舰艇数据契约 import ContractError, ModuleCapability, canonical_sha256
from 高天荒野舰艇推进安全判定器 import (
    THRUST_OUTPUT_STAGES_PERCENT, telegraph_notch_percent,
)
from 高天荒野舰艇推进状态合同 import (
    ENGINE_RUNTIME_STATE_INTERFACE_ID, EngineRuntimeState, PropulsionStateEvent,
)
from 高天荒野舰艇推进时间内核 import (
    PropulsionTimeBoundaryResult, PropulsionTimeCommand,
    _apply_command, _commit_due_transition, _parse_exact_timing_capability,
    validate_committed_propulsion_time_state,
)
from 高天荒野舰艇推进通道合同 import exact_object, strict_stage


GOVERNED_TIME_PREVIEW_INTERFACE_ID = "gaotian.governed-propulsion-time-preview/v1alpha1"
GOVERNED_TIME_RESULT_INTERFACE_ID = "gaotian.governed-propulsion-time-result/v1alpha1"
GOVERNED_TIME_POLICY_ID = "gaotian.propulsion-time/preview-authorize-effective-target/v1"


def _step(value: Any) -> int:
    if type(value) is not int or value < 0:
        raise ContractError("governed_time.fixed_step", "$.fixed_step_index", "必须为非负整数")
    return value


def _engine(value: Any, path: str) -> EngineRuntimeState:
    if not isinstance(value, EngineRuntimeState):
        raise ContractError("governed_time.state_type", path, "必须是 engine v2 状态")
    parsed = EngineRuntimeState.parse(value.to_dict(), path)
    if parsed.interface_id != ENGINE_RUNTIME_STATE_INTERFACE_ID:
        raise ContractError("governed_time.state_interface", path, "不得隐式使用 c2b engine 状态")
    if parsed.actuator_category == "main_engine" and (
        parsed.target_output_percent > telegraph_notch_percent(parsed.commanded_notch)
    ):
        raise ContractError("governed_time.state_target", path, "有效目标不得高于持久原车钟")
    return parsed


def _command(value: Any, path: str) -> PropulsionTimeCommand:
    obj = exact_object(value, {"commanded_notch", "target_output_percent"}, path)
    try:
        return PropulsionTimeCommand(**obj)
    except (TypeError, ValueError) as error:
        raise ContractError("governed_time.command", path, str(error)) from error


def _events(value: Any, path: str) -> tuple[PropulsionStateEvent, ...]:
    if not isinstance(value, list):
        raise ContractError("type.array", path, "事件必须为数组")
    return tuple(PropulsionStateEvent.parse(item, f"{path}[{index}]") for index, item in enumerate(value))


def _same_engine(source: EngineRuntimeState, resulting: EngineRuntimeState) -> None:
    if (source.actuator_instance_id, source.actuator_category) != (
        resulting.actuator_instance_id, resulting.actuator_category
    ):
        raise ContractError("governed_time.engine_identity", "$.state", "候选与结果必须属于同一执行器")


def _time_result(boundary: int, before: EngineRuntimeState, after: EngineRuntimeState,
    events: tuple[PropulsionStateEvent, ...],
) -> None:
    _same_engine(before, after)
    if not isinstance(events, tuple) or any(not isinstance(event, PropulsionStateEvent) for event in events):
        raise ContractError("governed_time.event_type", "$.events", "必须使用不可变的严格事件序列")
    try:
        PropulsionTimeBoundaryResult(boundary, after, events)
    except ValueError as error:
        raise ContractError("governed_time.events", "$.events", str(error)) from error
    if len({e.sort_key for e in events}) != len(events):
        raise ContractError("governed_time.event_duplicate", "$.events", "同一边界事件不得重复")
    stage_events = tuple(e for e in events if e.kind == "engine_output_stage_changed")
    changed = before.actual_output_percent != after.actual_output_percent
    if changed != bool(stage_events) or (changed and (
        abs(THRUST_OUTPUT_STAGES_PERCENT.index(before.actual_output_percent)
            - THRUST_OUTPUT_STAGES_PERCENT.index(after.actual_output_percent)) != 1
        or stage_events[0].previous_stage_percent != before.actual_output_percent
        or stage_events[0].resulting_stage_percent != after.actual_output_percent
    )):
        raise ContractError("governed_time.stage_chain", "$.events", "实际阶段必须对应一个相邻阶段事件")


@dataclass(frozen=True)
class GovernedPropulsionTimePreview:
    fixed_step_index: int
    capability_sha256: str
    source_state: EngineRuntimeState
    command: PropulsionTimeCommand
    candidate_state: EngineRuntimeState
    candidate_events: tuple[PropulsionStateEvent, ...]

    def __post_init__(self) -> None:
        _step(self.fixed_step_index)
        if not isinstance(self.capability_sha256, str) or re.fullmatch(r"[0-9a-f]{64}", self.capability_sha256) is None:
            raise ContractError("governed_time.capability_hash", "$.capability_sha256", "必须是规范 SHA-256")
        _engine(self.source_state, "$.source_state")
        _engine(self.candidate_state, "$.candidate_state")
        if not isinstance(self.command, PropulsionTimeCommand):
            raise ContractError("governed_time.command_type", "$.command", "必须显式传入原命令")
        self.command.target_for(self.source_state.actuator_category)
        _time_result(self.fixed_step_index, self.source_state, self.candidate_state, self.candidate_events)

    @property
    def has_upstage_candidate(self) -> bool:
        return self.candidate_state.actual_output_percent > self.source_state.actual_output_percent

    def to_dict(self) -> dict[str, Any]:
        return {"interface": GOVERNED_TIME_PREVIEW_INTERFACE_ID, "policy": GOVERNED_TIME_POLICY_ID,
            "fixed_step_index": self.fixed_step_index, "capability_sha256": self.capability_sha256,
            "source_state": self.source_state.to_dict(),
            "command": {"commanded_notch": self.command.commanded_notch,
                        "target_output_percent": self.command.target_output_percent},
            "candidate_state": self.candidate_state.to_dict(),
            "candidate_events": [event.to_dict() for event in self.candidate_events]}

    @classmethod
    def parse(cls, value: Any, path: str = "$") -> "GovernedPropulsionTimePreview":
        obj = exact_object(value, {"interface", "policy", "fixed_step_index", "capability_sha256",
            "source_state", "command", "candidate_state", "candidate_events"}, path)
        if obj["interface"] != GOVERNED_TIME_PREVIEW_INTERFACE_ID or obj["policy"] != GOVERNED_TIME_POLICY_ID:
            raise ContractError("governed_time.preview_interface", path, "预览版本或策略不匹配")
        return cls(obj["fixed_step_index"], obj["capability_sha256"],
            EngineRuntimeState.parse(obj["source_state"], f"{path}.source_state"),
            _command(obj["command"], f"{path}.command"),
            EngineRuntimeState.parse(obj["candidate_state"], f"{path}.candidate_state"),
            _events(obj["candidate_events"], f"{path}.candidate_events"))


@dataclass(frozen=True)
class GovernedPropulsionTimeResult:
    preview: GovernedPropulsionTimePreview
    effective_target_percent: int
    allow_upstage: bool
    state: EngineRuntimeState
    events: tuple[PropulsionStateEvent, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.preview, GovernedPropulsionTimePreview):
            raise ContractError("governed_time.preview_type", "$.preview", "必须提供严格预览")
        _validate_authorization(self.preview, self.effective_target_percent, self.allow_upstage)
        _engine(self.state, "$.state")
        notch, _ = self.preview.command.target_for(self.state.actuator_category)
        if (self.state.commanded_notch, self.state.target_output_percent) != (notch, self.effective_target_percent):
            raise ContractError("governed_time.result_target", "$.state", "结果必须保留原命令及精确有效目标")
        _time_result(self.preview.fixed_step_index, self.preview.source_state, self.state, self.events)
        expected_actual = (self.preview.source_state if self.upstage_rejected else self.preview.candidate_state).actual_output_percent
        if self.state.actual_output_percent != expected_actual:
            raise ContractError("governed_time.authorization_result", "$.state", "结果未遵守候选授权")

    @property
    def upstage_rejected(self) -> bool:
        return self.preview.has_upstage_candidate and not self.allow_upstage

    def to_dict(self) -> dict[str, Any]:
        return {"interface": GOVERNED_TIME_RESULT_INTERFACE_ID, "policy": GOVERNED_TIME_POLICY_ID,
            "preview": self.preview.to_dict(), "preview_sha256": canonical_sha256(self.preview),
            "effective_target_percent": self.effective_target_percent, "allow_upstage": self.allow_upstage,
            "upstage_rejected": self.upstage_rejected, "state": self.state.to_dict(),
            "events": [event.to_dict() for event in self.events]}

    @classmethod
    def parse(cls, value: Any, path: str = "$") -> "GovernedPropulsionTimeResult":
        obj = exact_object(value, {"interface", "policy", "preview", "preview_sha256",
            "effective_target_percent", "allow_upstage", "upstage_rejected", "state", "events"}, path)
        if obj["interface"] != GOVERNED_TIME_RESULT_INTERFACE_ID or obj["policy"] != GOVERNED_TIME_POLICY_ID:
            raise ContractError("governed_time.result_interface", path, "提交结果版本或策略不匹配")
        preview = GovernedPropulsionTimePreview.parse(obj["preview"], f"{path}.preview")
        if obj["preview_sha256"] != canonical_sha256(preview):
            raise ContractError("governed_time.preview_hash", path, "预览指纹不匹配")
        result = cls(preview, obj["effective_target_percent"], obj["allow_upstage"],
            EngineRuntimeState.parse(obj["state"], f"{path}.state"), _events(obj["events"], f"{path}.events"))
        if type(obj["upstage_rejected"]) is not bool or obj["upstage_rejected"] != result.upstage_rejected:
            raise ContractError("governed_time.rejection_flag", path, "否决标志与授权不一致")
        return result


def _validate_authorization(preview: GovernedPropulsionTimePreview, target: int, allow: bool) -> None:
    if not strict_stage(target) or type(allow) is not bool:
        raise ContractError("governed_time.authorization_type", "$.authorization", "有效目标必须为整数阶段，授权必须为布尔值")
    _, requested = preview.command.target_for(preview.source_state.actuator_category)
    if target > requested:
        raise ContractError("governed_time.target_above_request", "$.effective_target_percent", "有效目标不得高于原命令")
    if not allow and (not preview.has_upstage_candidate or target > preview.source_state.actual_output_percent):
        raise ContractError("governed_time.invalid_veto", "$.allow_upstage", "只能否决升阶，并须将目标限制在当前阶段或以下")
    if allow and preview.has_upstage_candidate and target < preview.candidate_state.actual_output_percent:
        raise ContractError("governed_time.upstage_above_target", "$.effective_target_percent", "不得授权超过有效目标的升阶")


def preview_governed_propulsion_time_boundary(state: EngineRuntimeState, capability: ModuleCapability,
    fixed_step_index: int, command: PropulsionTimeCommand,
) -> GovernedPropulsionTimePreview:
    """只生成到期候选，不提交升阶；新命令与有效目标在授权提交时应用。"""
    boundary = _step(fixed_step_index)
    state = _engine(state, "$.state")
    if not isinstance(command, PropulsionTimeCommand):
        raise ContractError("governed_time.command_type", "$.command", "必须提供原命令")
    command.target_for(state.actuator_category)
    timing = _parse_exact_timing_capability(capability, state.actuator_category)
    # 允许当前边界恰好到期，不允许漏过期或伪造精确排程。
    validation_boundary = boundary - 1 if state.next_transition_step == boundary and boundary > 0 else boundary
    validate_committed_propulsion_time_state(state, capability, validation_boundary)
    candidate, events = _commit_due_transition(state, timing, boundary)
    return GovernedPropulsionTimePreview(boundary, canonical_sha256(capability), state, command,
        candidate, tuple(sorted(events, key=lambda event: event.sort_key)))


def commit_governed_propulsion_time_boundary(preview: GovernedPropulsionTimePreview,
    capability: ModuleCapability, *, current_state: EngineRuntimeState, fixed_step_index: int,
    effective_target_percent: int, allow_upstage: bool,
) -> GovernedPropulsionTimeResult:
    """调用方完成整舰安全判定后提交；不得跨状态/边界/能力使用预览。"""
    if not isinstance(preview, GovernedPropulsionTimePreview):
        raise ContractError("governed_time.preview_type", "$.preview", "必须提供严格预览")
    if _step(fixed_step_index) != preview.fixed_step_index or _engine(current_state, "$.current_state") != preview.source_state:
        raise ContractError("governed_time.stale_preview", "$.preview", "当前状态或当前边界已改变")
    expected = preview_governed_propulsion_time_boundary(current_state, capability, fixed_step_index, preview.command)
    if expected != preview:
        raise ContractError("governed_time.preview_replay", "$.preview", "能力指纹或候选与精确重放不一致")
    _validate_authorization(preview, effective_target_percent, allow_upstage)
    timing = _parse_exact_timing_capability(capability, current_state.actuator_category)
    committed = preview.candidate_state if allow_upstage else preview.source_state
    events = list(preview.candidate_events) if allow_upstage else []
    notch, _ = preview.command.target_for(current_state.actuator_category)
    resulting, command_events = _apply_command(committed, timing, fixed_step_index, notch, effective_target_percent)
    events.extend(command_events)
    events.sort(key=lambda event: event.sort_key)
    validate_committed_propulsion_time_state(resulting, capability, fixed_step_index)
    return GovernedPropulsionTimeResult(preview, effective_target_percent, allow_upstage, resulting, tuple(events))


def validate_governed_propulsion_time_result(result: GovernedPropulsionTimeResult,
    capability: ModuleCapability,
) -> None:
    """严格解析后带精确资源重放；不信任只改 hash 的伪造排程。"""
    if not isinstance(result, GovernedPropulsionTimeResult):
        raise ContractError("governed_time.result_type", "$", "必须提供严格提交结果")
    parsed = GovernedPropulsionTimeResult.parse(result.to_dict())
    expected = commit_governed_propulsion_time_boundary(parsed.preview, capability,
        current_state=parsed.preview.source_state, fixed_step_index=parsed.preview.fixed_step_index,
        effective_target_percent=parsed.effective_target_percent, allow_upstage=parsed.allow_upstage)
    if parsed != expected:
        raise ContractError("governed_time.result_replay", "$", "提交结果与精确重放不一致")
