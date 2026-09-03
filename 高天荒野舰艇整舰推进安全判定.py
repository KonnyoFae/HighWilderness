"""d3.2：完整实际向量的纯软保护判定；不导入或修改统一场景。"""

from __future__ import annotations

from dataclasses import dataclass, replace
import re
from typing import Any, Callable

from 高天荒野舰艇数据契约 import ContractError, ModuleCapability, RESOURCE_ID_PATTERN, canonical_sha256
from 高天荒野舰艇实际推进合同 import finite_number, fixed_step_index as require_step
from 高天荒野舰艇推进安全判定器 import (
    PropulsionSafetyProfile, PropulsionHardAvailability, PropulsionLoadSample, PropulsionSafetyEventIntent,
    SOFT_LIMIT_REASON_ORDER, THRUST_OUTPUT_STAGES_PERCENT,
    _soft_reasons, _release_safe, _active_reasons,
)
from 高天荒野舰艇推进通道合同 import (
    DIRECTIONAL_CHANNELS, TRANSLATION_CHANNELS, OPPOSING_CHANNEL_PAIRS,
    DirectionalPropulsionGovernorState, exact_object, strict_stage,
)
from 高天荒野舰艇定向推进控制桥 import DirectionalPropulsionControlInput
from 高天荒野舰艇受控推进时间边界 import (
    GovernedPropulsionTimePreview, GovernedPropulsionTimeResult,
    preview_governed_propulsion_time_boundary, commit_governed_propulsion_time_boundary,
)

VECTOR_INTERFACE_ID = "gaotian.propulsion-output-vector/v1alpha1"
VECTOR_SAMPLE_INTERFACE_ID = "gaotian.whole-ship-propulsion-load-sample/v1alpha1"
VECTOR_SAFETY_RESULT_INTERFACE_ID = "gaotian.whole-ship-propulsion-safety-result/v1alpha1"
VECTOR_SAFETY_POLICY_ID = "gaotian.propulsion-safety/joint-upstage-batch-bounded-cap-path/v1"
CHANNEL_SAFETY_INTENT_INTERFACE_ID = "gaotian.directional-propulsion-safety-intent/v1alpha1"


def _require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise ContractError(f"vector_safety.{code}", "$", message)


def _hash(value: Any) -> None:
    _require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None,
        "hash", "必须为规范 SHA-256")


def _array(value: Any) -> list:
    _require(isinstance(value, list), "array", "必须为数组")
    return value


def _reasons(*groups: tuple[str, ...]) -> tuple[str, ...]:
    combined = set().union(*groups)
    return tuple(r for r in SOFT_LIMIT_REASON_ORDER if r in combined)


def _valid_reasons(value: tuple[str, ...]) -> None:
    _require(isinstance(value, tuple) and all(isinstance(r, str) for r in value), "reasons", "原因必须是不可变字符串序列")
    _require(value == _reasons(value), "reasons", "原因必须唯一且按规范顺序排列")


@dataclass(frozen=True)
class PropulsionOutputVector:
    outputs: tuple[tuple[str, int], ...]

    def __post_init__(self) -> None:
        _require(isinstance(self.outputs, tuple) and bool(self.outputs), "vector", "向量必须非空且不可变")
        for item in self.outputs:
            _require(isinstance(item, tuple) and len(item) == 2, "vector_entry", "向量项必须为身份/阶段")
            key, percent = item
            _require(isinstance(key, str) and RESOURCE_ID_PATTERN.fullmatch(key) is not None and strict_stage(percent),
                "vector_entry", "向量项须含合法身份和整数离散阶段")
        keys = tuple(key for key, _ in self.outputs)
        _require(keys == tuple(sorted(set(keys))), "vector_order", "执行器必须唯一且稳定排序")

    def to_dict(self) -> dict[str, Any]:
        return {"interface": VECTOR_INTERFACE_ID,
            "outputs": [{"actuator_instance_id": key, "output_percent": p} for key, p in self.outputs]}

    @classmethod
    def parse(cls, value: Any, path: str = "$") -> "PropulsionOutputVector":
        obj = exact_object(value, {"interface", "outputs"}, path)
        _require(obj["interface"] == VECTOR_INTERFACE_ID, "vector_interface", "向量版本不匹配")
        entries = [exact_object(x, {"actuator_instance_id", "output_percent"}, path) for x in _array(obj["outputs"])]
        return cls(tuple((x["actuator_instance_id"], x["output_percent"]) for x in entries))


@dataclass(frozen=True)
class WholeShipPropulsionLoadSample:
    load_context_sha256: str
    vector: PropulsionOutputVector
    structure_ratio: float
    crew_g: float

    def __post_init__(self) -> None:
        _hash(self.load_context_sha256)
        _require(isinstance(self.vector, PropulsionOutputVector), "sample_vector", "载荷样本必须绑定完整向量")
        object.__setattr__(self, "structure_ratio", finite_number(self.structure_ratio, "$.structure_ratio", 0))
        object.__setattr__(self, "crew_g", finite_number(self.crew_g, "$.crew_g", 0))

    @property
    def metrics(self) -> PropulsionLoadSample:
        # 仅复用 c1 的载荷阈值函数；标量标签 0 不参与物理或向量推导。
        return PropulsionLoadSample(0, self.structure_ratio, self.crew_g)

    def to_dict(self) -> dict[str, Any]:
        return {"interface": VECTOR_SAMPLE_INTERFACE_ID, "load_context_sha256": self.load_context_sha256,
            "vector": self.vector.to_dict(), "structure_ratio": self.structure_ratio, "crew_g": self.crew_g}

    @classmethod
    def parse(cls, value: Any, path: str = "$") -> "WholeShipPropulsionLoadSample":
        obj = exact_object(value, {"interface", "load_context_sha256", "vector", "structure_ratio", "crew_g"}, path)
        _require(obj["interface"] == VECTOR_SAMPLE_INTERFACE_ID, "sample_interface", "载荷版本不匹配")
        return cls(obj["load_context_sha256"], PropulsionOutputVector.parse(obj["vector"], path), obj["structure_ratio"], obj["crew_g"])


@dataclass(frozen=True)
class WholeShipActuatorBoundary:
    command_channel: str
    capability: ModuleCapability
    preview: GovernedPropulsionTimePreview
    hard_availability: PropulsionHardAvailability

    def __post_init__(self) -> None:
        _require(self.command_channel in DIRECTIONAL_CHANNELS and isinstance(self.preview, GovernedPropulsionTimePreview),
            "actuator", "必须提供定向通道和严格时间预览")
        expected_category = "main_engine" if self.command_channel in TRANSLATION_CHANNELS else "maneuver_thruster"
        _require(self.preview.source_state.actuator_category == expected_category, "actuator_channel", "执行器类别与通道不符")
        _require(isinstance(self.hard_availability, PropulsionHardAvailability)
            and strict_stage(self.hard_availability.ceiling_percent), "hard_stage", "必须显式提供整数硬上限")
        try:
            PropulsionHardAvailability(self.hard_availability.ceiling_percent, self.hard_availability.reasons)
        except ValueError as error:
            raise ContractError("vector_safety.hard_reasons", "$", str(error)) from error
        if self.preview.source_state.phase == "tripped":
            _require(self.hard_availability.ceiling_percent == 0 and "engine_tripped" in self.hard_availability.reasons,
                "tripped_availability", "已跳闸执行器须显式声明零硬上限及跳闸原因")
        expected = preview_governed_propulsion_time_boundary(self.preview.source_state, self.capability,
            self.preview.fixed_step_index, self.preview.command)
        _require(expected == self.preview, "preview_replay", "预览与精确 capability 不匹配")

    @property
    def actuator_instance_id(self) -> str:
        return self.preview.source_state.actuator_instance_id

    def to_dict(self) -> dict[str, Any]:
        return {"command_channel": self.command_channel, "capability": self.capability.to_dict(),
            "preview": self.preview.to_dict(), "hard_availability": self.hard_availability.to_dict()}


@dataclass(frozen=True)
class ChannelSafetyEventIntent:
    command_channel: str
    event: PropulsionSafetyEventIntent

    def __post_init__(self) -> None:
        _require(self.command_channel in DIRECTIONAL_CHANNELS and isinstance(self.event, PropulsionSafetyEventIntent),
            "event_channel", "安全意图必须属于一个定向通道")
        require_step(self.event.fixed_step_index)
        _require(strict_stage(self.event.previous_ceiling_percent) and strict_stage(self.event.resulting_ceiling_percent),
            "event_stage", "事件必须使用整数阶段")
        _valid_reasons(self.event.reasons)
        before, after = self.event.previous_ceiling_percent, self.event.resulting_ceiling_percent
        kind = self.event.kind
        _require(bool(self.event.reasons) and (
            (kind == "engine_safety_limit_engaged" and before == 100 and after < 100)
            or (kind == "engine_safety_limit_changed" and before < 100 and after < 100)
            or (kind == "engine_safety_limit_released" and before < 100 and after == 100)),
            "event_transition", "事件种类必须匹配限幅前后状态并保留原因")

    def to_dict(self) -> dict[str, Any]:
        return {"interface": CHANNEL_SAFETY_INTENT_INTERFACE_ID, "command_channel": self.command_channel,
            "event": self.event.to_dict()}

    @classmethod
    def parse(cls, value: Any, path: str = "$") -> "ChannelSafetyEventIntent":
        obj = exact_object(value, {"interface", "command_channel", "event"}, path)
        _require(obj["interface"] == CHANNEL_SAFETY_INTENT_INTERFACE_ID, "event_interface", "事件意图版本不匹配")
        event = dict(exact_object(obj["event"], {"kind", "fixed_step_index", "previous_ceiling_percent",
            "resulting_ceiling_percent", "reasons"}, path))
        event["reasons"] = tuple(_array(event["reasons"]))
        _valid_reasons(event["reasons"])
        try:
            return cls(obj["command_channel"], PropulsionSafetyEventIntent(**event))
        except ValueError as error:
            raise ContractError("vector_safety.event", path, str(error)) from error


@dataclass(frozen=True)
class WholeShipPropulsionSafetyResult:
    fixed_step_index: int
    input_sha256: str
    load_context_sha256: str
    current_vector: PropulsionOutputVector
    committed_vector: PropulsionOutputVector
    governors: tuple[DirectionalPropulsionGovernorState, ...]
    engine_results: tuple[GovernedPropulsionTimeResult, ...]
    load_samples: tuple[WholeShipPropulsionLoadSample, ...]
    downstage_search_caps: tuple[int, ...]
    safe_downstage_found: bool | None
    remaining_soft_reasons: tuple[str, ...]
    event_intents: tuple[ChannelSafetyEventIntent, ...]

    def __post_init__(self) -> None:
        require_step(self.fixed_step_index)
        _hash(self.input_sha256)
        _hash(self.load_context_sha256)
        _require(isinstance(self.current_vector, PropulsionOutputVector) and isinstance(self.committed_vector, PropulsionOutputVector),
            "result_vector", "结果必须使用严格向量")
        _require(isinstance(self.governors, tuple) and all(isinstance(g, DirectionalPropulsionGovernorState) for g in self.governors)
            and tuple(g.command_channel for g in self.governors) == DIRECTIONAL_CHANNELS, "governors", "必须按序保存六通道 governor")
        _require(all(g.last_evaluated_step_index == self.fixed_step_index for g in self.governors), "result_step", "governor 必须仅提交当前边界")
        _require(all(all(t is None or t <= self.fixed_step_index for t in
            (g.safety_limited_since_step, g.release_candidate_since_step)) for g in self.governors),
            "result_history", "结果不得包含未来限制历史")
        _require(isinstance(self.engine_results, tuple) and all(isinstance(r, GovernedPropulsionTimeResult) for r in self.engine_results),
            "engine_results", "必须提供严格时间结果")
        _require(self.current_vector.outputs == tuple((r.state.actuator_instance_id, r.preview.source_state.actual_output_percent) for r in self.engine_results)
            and self.committed_vector.outputs == tuple((r.state.actuator_instance_id, r.state.actual_output_percent) for r in self.engine_results)
            and all(r.preview.fixed_step_index == self.fixed_step_index for r in self.engine_results), "engine_vector_chain", "向量必须对应完整源/结果引擎链")
        _require(isinstance(self.load_samples, tuple) and bool(self.load_samples)
            and len(self.load_samples) <= 25
            and all(isinstance(s, WholeShipPropulsionLoadSample) for s in self.load_samples), "samples", "必须记录 1 至 25 个载荷样本")
        keys = tuple(s.vector for s in self.load_samples)
        ids = tuple(key for key, _ in self.current_vector.outputs)
        _require(len(keys) == len(set(keys)) and keys[0] == self.current_vector and self.committed_vector in keys
            and all(s.load_context_sha256 == self.load_context_sha256 and tuple(k for k, _ in s.vector.outputs) == ids for s in self.load_samples),
            "sample_chain", "采样必须属于本上下文、覆盖当前/提交向量且不得重复")
        _require(isinstance(self.downstage_search_caps, tuple) and len(self.downstage_search_caps) <= 22
            and all(strict_stage(x) for x in self.downstage_search_caps)
            and self.downstage_search_caps == tuple(sorted(set(self.downstage_search_caps), reverse=True)), "search_caps", "搜索必须降序且不超过 22 个阶段")
        _require((self.safe_downstage_found is None and not self.downstage_search_caps) or
            (type(self.safe_downstage_found) is bool and bool(self.downstage_search_caps)), "search_status", "搜索状态必须对应实际搜索")
        _valid_reasons(self.remaining_soft_reasons)
        _require(isinstance(self.event_intents, tuple) and all(isinstance(e, ChannelSafetyEventIntent) for e in self.event_intents),
            "events", "必须使用严格通道安全意图")
        channels = tuple(e.command_channel for e in self.event_intents)
        _require(channels == tuple(c for c in DIRECTIONAL_CHANNELS if c in channels)
            and all(e.event.fixed_step_index == self.fixed_step_index for e in self.event_intents), "event_order", "每通道每边界至多一个最终事件")

    def to_dict(self) -> dict[str, Any]:
        return {"interface": VECTOR_SAFETY_RESULT_INTERFACE_ID, "policy": VECTOR_SAFETY_POLICY_ID,
            "fixed_step_index": self.fixed_step_index, "input_sha256": self.input_sha256,
            "load_context_sha256": self.load_context_sha256, "current_vector": self.current_vector.to_dict(),
            "committed_vector": self.committed_vector.to_dict(), "governors": [g.to_dict() for g in self.governors],
            "engine_results": [r.to_dict() for r in self.engine_results], "load_samples": [s.to_dict() for s in self.load_samples],
            "downstage_search_caps": list(self.downstage_search_caps), "safe_downstage_found": self.safe_downstage_found,
            "remaining_soft_reasons": list(self.remaining_soft_reasons), "event_intents": [e.to_dict() for e in self.event_intents]}

    @classmethod
    def parse(cls, value: Any, path: str = "$") -> "WholeShipPropulsionSafetyResult":
        obj = exact_object(value, {"interface", "policy", "fixed_step_index", "input_sha256", "load_context_sha256",
            "current_vector", "committed_vector", "governors", "engine_results", "load_samples",
            "downstage_search_caps", "safe_downstage_found", "remaining_soft_reasons", "event_intents"}, path)
        _require(obj["interface"] == VECTOR_SAFETY_RESULT_INTERFACE_ID and obj["policy"] == VECTOR_SAFETY_POLICY_ID,
            "result_interface", "整舰安全结果版本或策略不匹配")
        return cls(obj["fixed_step_index"], obj["input_sha256"], obj["load_context_sha256"],
            PropulsionOutputVector.parse(obj["current_vector"], path), PropulsionOutputVector.parse(obj["committed_vector"], path),
            tuple(DirectionalPropulsionGovernorState.parse(g, path) for g in _array(obj["governors"])),
            tuple(GovernedPropulsionTimeResult.parse(r, path) for r in _array(obj["engine_results"])),
            tuple(WholeShipPropulsionLoadSample.parse(s, path) for s in _array(obj["load_samples"])),
            tuple(_array(obj["downstage_search_caps"])), obj["safe_downstage_found"],
            tuple(_array(obj["remaining_soft_reasons"])), tuple(ChannelSafetyEventIntent.parse(e, path) for e in _array(obj["event_intents"])))


def _intent(before: DirectionalPropulsionGovernorState, after: DirectionalPropulsionGovernorState,
    step: int,
) -> ChannelSafetyEventIntent | None:
    was, now = before.safety_ceiling_percent < 100, after.safety_ceiling_percent < 100
    if not was and now:
        kind, reasons = "engine_safety_limit_engaged", after.safety_reasons
    elif was and not now:
        kind, reasons = "engine_safety_limit_released", before.safety_reasons
    elif was and now and (before.safety_ceiling_percent, before.safety_reasons) != (after.safety_ceiling_percent, after.safety_reasons):
        kind, reasons = "engine_safety_limit_changed", after.safety_reasons
    else:
        return None
    return ChannelSafetyEventIntent(after.command_channel, PropulsionSafetyEventIntent(kind, step,
        before.safety_ceiling_percent, after.safety_ceiling_percent, reasons))


def evaluate_whole_ship_propulsion_safety(profile: PropulsionSafetyProfile,
    previous_governors: tuple[DirectionalPropulsionGovernorState, ...], controls: DirectionalPropulsionControlInput,
    actuators: tuple[WholeShipActuatorBoundary, ...], *, fixed_step_index: int, load_context_sha256: str,
    load_evaluator: Callable[[PropulsionOutputVector], WholeShipPropulsionLoadSample],
    crew_safety_lock_enabled: bool,
) -> WholeShipPropulsionSafetyResult:
    """按一次权威边界判定完整向量；回调须绑定同一只读物理上下文。"""
    n = require_step(fixed_step_index)
    _hash(load_context_sha256)
    _require(isinstance(profile, PropulsionSafetyProfile) and canonical_sha256(profile) == profile.source_sha256,
        "profile", "必须提供精确安全配置")
    _require(isinstance(controls, DirectionalPropulsionControlInput) and type(crew_safety_lock_enabled) is bool
        and callable(load_evaluator), "input", "控制、乘员锁或采样器非法")
    DirectionalPropulsionControlInput.parse(controls.to_dict())
    _require(isinstance(previous_governors, tuple) and all(isinstance(g, DirectionalPropulsionGovernorState) for g in previous_governors)
        and tuple(g.command_channel for g in previous_governors) == DIRECTIONAL_CHANNELS, "governors", "需按序提供六通道历史")
    previous = {g.command_channel: g for g in previous_governors}
    clocks = {g.last_evaluated_step_index for g in previous_governors}
    _require(len(clocks) == 1, "history_clock", "通道历史必须属于同一权威边界")
    for g in previous_governors:
        DirectionalPropulsionGovernorState.parse(g.to_dict(), "$.governors")
        if g.last_evaluated_step_index is None:
            _require(replace(g, command=DirectionalPropulsionGovernorState.initial(g.command_channel).command)
                == DirectionalPropulsionGovernorState.initial(g.command_channel), "history_initial", "未求值状态不得携带限制历史")
        else:
            _require(g.last_evaluated_step_index < n and all(t is None or t <= g.last_evaluated_step_index
                for t in (g.safety_limited_since_step, g.release_candidate_since_step)), "history_step", "不得重复、倒退或携带未来历史")
    _require(isinstance(actuators, tuple) and bool(actuators) and all(isinstance(a, WholeShipActuatorBoundary) for a in actuators),
        "actuator_set", "必须提供完整不可变执行器集合")
    ids = tuple(a.actuator_instance_id for a in actuators)
    _require(ids == tuple(sorted(set(ids))), "actuator_order", "执行器须唯一且稳定排序")
    commands = {c.command_channel: c for c in controls.channel_commands}
    active_channels = {c.command_channel for c in controls.channel_commands if c.requested_percent}
    for a in actuators:
        a.__post_init__()
        p, g, c = a.preview, previous[a.command_channel], commands[a.command_channel]
        _require(p.fixed_step_index == n and (p.command.commanded_notch, p.command.target_output_percent)
            == (c.commanded_notch, c.target_output_percent), "preview_command", "预览必须对应当前边界与原命令")
        _require(p.source_state.commanded_notch == g.command.commanded_notch
            and p.source_state.target_output_percent <= min(g.command.requested_percent, g.safety_ceiling_percent),
            "source_governor", "源引擎目标与既有 governor 不一致")
        if p.source_state.actual_output_percent or p.source_state.target_output_percent:
            active_channels.add(a.command_channel)
    _require(not any(a in active_channels and b in active_channels for a, b in OPPOSING_CHANNEL_PAIRS),
        "direction_interlock_unwired", "对向或未停车换向仍待 d4")
    input_hash = canonical_sha256({"profile": profile.to_dict(), "governors": [g.to_dict() for g in previous_governors],
        "controls": controls.to_dict(), "actuators": [a.to_dict() for a in actuators], "fixed_step_index": n,
        "load_context_sha256": load_context_sha256, "crew_safety_lock_enabled": crew_safety_lock_enabled})
    current = PropulsionOutputVector(tuple((a.actuator_instance_id, a.preview.source_state.actual_output_percent) for a in actuators))
    cached: dict[PropulsionOutputVector, WholeShipPropulsionLoadSample] = {}
    def sample(vector: PropulsionOutputVector) -> WholeShipPropulsionLoadSample:
        if vector not in cached:
            value = load_evaluator(vector)
            _require(isinstance(value, WholeShipPropulsionLoadSample) and value.vector == vector
                and value.load_context_sha256 == load_context_sha256, "sample_source", "回调样本必须匹配本次完整向量与上下文")
            cached[vector] = value
        return cached[vector]
    def soft(value: WholeShipPropulsionLoadSample) -> tuple[str, ...]:
        return _soft_reasons(value.metrics, profile, overg=controls.overg_requested,
            crew_safety_lock_enabled=crew_safety_lock_enabled)
    current_sample = sample(current)
    current_reasons = soft(current_sample)
    governors = {}
    for old in previous_governors:
        reasons = _active_reasons(old.safety_reasons, overg=controls.overg_requested,
            crew_safety_lock_enabled=crew_safety_lock_enabled)
        ceiling, since, candidate = old.safety_ceiling_percent, old.safety_limited_since_step, old.release_candidate_since_step
        if not reasons:
            ceiling, since, candidate = 100, None, None
        elif current_reasons or not _release_safe(current_sample.metrics, reasons, profile):
            candidate = None
        else:
            candidate = candidate if old.last_evaluated_step_index == n - 1 and candidate is not None else n
            if n - candidate + 1 >= profile.release_hold_steps:
                ceiling, reasons, since, candidate = 100, (), None, None
        governors[old.command_channel] = replace(old, command=commands[old.command_channel],
            safety_ceiling_percent=ceiling, safety_reasons=reasons,
            safety_limited_since_step=since, release_candidate_since_step=candidate)

    def limit(channel: str, ceiling: int, reasons: tuple[str, ...]) -> None:
        g = governors[channel]
        ceiling = min(g.safety_ceiling_percent, ceiling)
        if ceiling < 100:
            governors[channel] = replace(g, safety_ceiling_percent=ceiling,
                safety_reasons=_reasons(g.safety_reasons, reasons),
                safety_limited_since_step=g.safety_limited_since_step if g.safety_limited_since_step is not None else n,
                release_candidate_since_step=None)
    def effective(a: WholeShipActuatorBoundary) -> int:
        return min(commands[a.command_channel].requested_percent, governors[a.command_channel].safety_ceiling_percent,
            a.hard_availability.ceiling_percent)
    rising = tuple(a for a in actuators if a.preview.has_upstage_candidate)
    if current_reasons:
        for a in rising:
            limit(a.command_channel, a.preview.source_state.actual_output_percent, current_reasons)
    eligible = {a.actuator_instance_id for a in rising if not current_reasons
        and a.preview.candidate_state.actual_output_percent <= effective(a)}
    def vector_for(allowed: set[str]) -> PropulsionOutputVector:
        return PropulsionOutputVector(tuple((a.actuator_instance_id,
            a.preview.candidate_state.actual_output_percent if not a.preview.has_upstage_candidate or a.actuator_instance_id in allowed
            else a.preview.source_state.actual_output_percent) for a in actuators))
    committed = vector_for(eligible)
    remaining = soft(sample(committed))
    if remaining and eligible:
        for a in rising:
            if a.actuator_instance_id in eligible:
                limit(a.command_channel, a.preview.source_state.actual_output_percent, remaining)
        eligible.clear()
        committed = vector_for(eligible)
        remaining = soft(sample(committed))

    search_caps, safe_found = [], None
    if remaining:
        values = dict(committed.outputs)
        # max 只限制搜索上限，不替代任一执行器的真实阶段。
        maximum = max(values.values())
        selected, safe_found = 0, False
        for cap in reversed(THRUST_OUTPUT_STAGES_PERCENT):
            if cap > maximum:
                continue
            search_caps.append(cap)
            projected = PropulsionOutputVector(tuple((a.actuator_instance_id,
                min(values[a.actuator_instance_id], effective(a), cap)) for a in actuators))
            if not soft(sample(projected)):
                selected, safe_found = cap, True
                break
        for channel in DIRECTIONAL_CHANNELS:
            if channel in active_channels:
                limit(channel, min(selected, commands[channel].requested_percent), remaining)

    final_governors, intents = [], []
    for channel in DIRECTIONAL_CHANNELS:
        old, new = previous[channel], governors[channel]
        fields = ("safety_ceiling_percent", "safety_reasons", "safety_limited_since_step", "release_candidate_since_step")
        changed = tuple(getattr(old, x) for x in fields) != tuple(getattr(new, x) for x in fields)
        new = replace(new, last_evaluated_step_index=n, safety_revision=old.safety_revision + int(changed))
        governors[channel] = new
        final_governors.append(new)
        event = _intent(old, new, n)
        if event is not None:
            intents.append(event)
    results = tuple(commit_governed_propulsion_time_boundary(a.preview, a.capability,
        current_state=a.preview.source_state, fixed_step_index=n, effective_target_percent=effective(a),
        allow_upstage=not a.preview.has_upstage_candidate or a.actuator_instance_id in eligible) for a in actuators)
    _require(committed.outputs == tuple((r.state.actuator_instance_id, r.state.actual_output_percent) for r in results),
        "commit_mismatch", "时间提交不得改变已判定实际向量")
    return WholeShipPropulsionSafetyResult(n, input_hash, load_context_sha256, current, committed,
        tuple(final_governors), results, tuple(cached.values()), tuple(search_caps), safe_found, remaining, tuple(intents))


def validate_whole_ship_safety_result(value: Any, profile: PropulsionSafetyProfile,
    previous_governors: tuple[DirectionalPropulsionGovernorState, ...], controls: DirectionalPropulsionControlInput,
    actuators: tuple[WholeShipActuatorBoundary, ...], *, fixed_step_index: int, load_context_sha256: str,
    load_evaluator: Callable[[PropulsionOutputVector], WholeShipPropulsionLoadSample], crew_safety_lock_enabled: bool,
) -> None:
    parsed = WholeShipPropulsionSafetyResult.parse(value)
    expected = evaluate_whole_ship_propulsion_safety(profile, previous_governors, controls, actuators,
        fixed_step_index=fixed_step_index, load_context_sha256=load_context_sha256,
        load_evaluator=load_evaluator, crew_safety_lock_enabled=crew_safety_lock_enabled)
    _require(parsed == expected, "result_replay", "结果与精确输入及完整载荷重放不符")
