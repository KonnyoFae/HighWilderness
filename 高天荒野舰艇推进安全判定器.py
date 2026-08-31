"""T0b.2c1：未接线的离散推进安全 governor 纯规则内核。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from math import isfinite
from typing import Any, Callable


PROPULSION_SAFETY_INTERFACE_ID = (
    "gaotian.propulsion-safety-governor/v1alpha1"
)
EPS = 1.0e-9

THRUST_OUTPUT_STAGES_PERCENT = (
    0,
    2,
    *range(5, 101, 5),
)
TELEGRAPH_NOTCH_PERCENT = (
    ("stop", 0),
    ("dead_slow", 2),
    ("quarter", 25),
    ("half", 50),
    ("three_quarter", 75),
    ("full", 100),
)
TELEGRAPH_NOTCHES = tuple(item[0] for item in TELEGRAPH_NOTCH_PERCENT)

SOFT_LIMIT_REASON_ORDER = (
    "structure_limit",
    "crew_limit",
)
HARD_LIMIT_REASON_ORDER = (
    "fuel_unavailable",
    "power_unavailable",
    "crew_unavailable",
    "actuator_destroyed",
    "host_destroyed",
    "engine_tripped",
    "direction_interlock",
)
SAFETY_EVENT_KINDS = (
    "engine_safety_limit_engaged",
    "engine_safety_limit_changed",
    "engine_safety_limit_released",
)
SAFETY_ACTIONS = (
    "hold",
    "allow_upstage",
    "schedule_downstage",
)


def _require_nonnegative_step(value: int | None, name: str) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} 必须是非负整数或 None")


def _require_finite_nonnegative(value: float, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} 必须是有限非负数")
    if not isfinite(float(value)) or value < 0.0:
        raise ValueError(f"{name} 必须是有限非负数")


def _require_stage(value: int, name: str) -> None:
    if isinstance(value, bool) or value not in THRUST_OUTPUT_STAGES_PERCENT:
        raise ValueError(f"{name} 必须是规范推力阶段")


def _require_ordered_reasons(
    reasons: tuple[str, ...],
    order: tuple[str, ...],
    name: str,
) -> None:
    expected = tuple(item for item in order if item in set(reasons))
    if reasons != expected or len(set(reasons)) != len(reasons):
        raise ValueError(f"{name} 必须使用稳定顺序、不得重复且不得包含未知原因")


def telegraph_notch_percent(notch: str) -> int:
    for item, percent in TELEGRAPH_NOTCH_PERCENT:
        if item == notch:
            return percent
    raise ValueError(f"未知车钟档位：{notch}")


def adjacent_output_stage_percent(current: int, target: int) -> int:
    """返回从当前阶段向目标移动一格后的权威输出阶段。"""

    _require_stage(current, "current")
    _require_stage(target, "target")
    current_index = THRUST_OUTPUT_STAGES_PERCENT.index(current)
    target_index = THRUST_OUTPUT_STAGES_PERCENT.index(target)
    if current_index == target_index:
        return current
    return THRUST_OUTPUT_STAGES_PERCENT[
        current_index + (1 if target_index > current_index else -1)
    ]


@dataclass(frozen=True)
class PropulsionSafetyProfile:
    id: str
    structure_engage_ratio: float
    structure_release_ratio: float
    crew_engage_g: float
    crew_release_g: float
    release_hold_steps: int

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id:
            raise ValueError("安全配置 id 不得为空")
        for value, name in (
            (self.structure_engage_ratio, "structure_engage_ratio"),
            (self.structure_release_ratio, "structure_release_ratio"),
            (self.crew_engage_g, "crew_engage_g"),
            (self.crew_release_g, "crew_release_g"),
        ):
            _require_finite_nonnegative(value, name)
            if value <= 0.0:
                raise ValueError(f"{name} 必须大于 0")
        if self.structure_release_ratio >= self.structure_engage_ratio:
            raise ValueError("结构释放阈值必须位于介入阈值更安全的一侧")
        if self.crew_release_g >= self.crew_engage_g:
            raise ValueError("乘员释放阈值必须位于介入阈值更安全的一侧")
        if (
            isinstance(self.release_hold_steps, bool)
            or not isinstance(self.release_hold_steps, int)
            or self.release_hold_steps < 1
        ):
            raise ValueError("release_hold_steps 必须是正整数")

    def to_dict(self) -> dict[str, Any]:
        return {
            "crew_engage_g": self.crew_engage_g,
            "crew_release_g": self.crew_release_g,
            "id": self.id,
            "release_hold_steps": self.release_hold_steps,
            "structure_engage_ratio": self.structure_engage_ratio,
            "structure_release_ratio": self.structure_release_ratio,
        }


@dataclass(frozen=True)
class PropulsionLoadSample:
    output_percent: int
    structure_ratio: float
    crew_g: float

    def __post_init__(self) -> None:
        _require_stage(self.output_percent, "output_percent")
        _require_finite_nonnegative(self.structure_ratio, "structure_ratio")
        _require_finite_nonnegative(self.crew_g, "crew_g")

    def to_dict(self) -> dict[str, Any]:
        return {
            "crew_g": self.crew_g,
            "output_percent": self.output_percent,
            "structure_ratio": self.structure_ratio,
        }


@dataclass(frozen=True)
class PropulsionHardAvailability:
    ceiling_percent: int = 100
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_stage(self.ceiling_percent, "hard ceiling_percent")
        _require_ordered_reasons(
            self.reasons,
            HARD_LIMIT_REASON_ORDER,
            "hard reasons",
        )
        if (self.ceiling_percent < 100) != bool(self.reasons):
            raise ValueError("硬上限低于 100% 时必须给出原因，100% 时不得给出原因")

    def to_dict(self) -> dict[str, Any]:
        return {
            "ceiling_percent": self.ceiling_percent,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class PropulsionGovernorDraftState:
    commanded_notch: str
    safety_ceiling_percent: int = 100
    safety_reasons: tuple[str, ...] = ()
    safety_limited_since_step: int | None = None
    release_candidate_since_step: int | None = None
    last_evaluated_step_index: int | None = None
    safety_revision: int = 0

    def __post_init__(self) -> None:
        telegraph_notch_percent(self.commanded_notch)
        _require_stage(self.safety_ceiling_percent, "safety_ceiling_percent")
        _require_ordered_reasons(
            self.safety_reasons,
            SOFT_LIMIT_REASON_ORDER,
            "safety_reasons",
        )
        limited = self.safety_ceiling_percent < 100
        if limited != bool(self.safety_reasons):
            raise ValueError("软安全上限低于 100% 时必须给出原因，100% 时不得给出原因")
        if limited != (self.safety_limited_since_step is not None):
            raise ValueError("软安全限制状态与介入步号不一致")
        if not limited and self.release_candidate_since_step is not None:
            raise ValueError("未受限状态不得保存释放候选步号")
        for value, name in (
            (self.safety_limited_since_step, "safety_limited_since_step"),
            (self.release_candidate_since_step, "release_candidate_since_step"),
            (self.last_evaluated_step_index, "last_evaluated_step_index"),
        ):
            _require_nonnegative_step(value, name)
        if (
            self.release_candidate_since_step is not None
            and self.safety_limited_since_step is not None
            and self.release_candidate_since_step
            < self.safety_limited_since_step
        ):
            raise ValueError("释放候选步不得早于安全限制介入步")
        if (
            isinstance(self.safety_revision, bool)
            or not isinstance(self.safety_revision, int)
            or self.safety_revision < 0
        ):
            raise ValueError("safety_revision 必须是非负整数")

    @property
    def limited(self) -> bool:
        return self.safety_ceiling_percent < 100

    def to_dict(self) -> dict[str, Any]:
        return {
            "commanded_notch": self.commanded_notch,
            "last_evaluated_step_index": self.last_evaluated_step_index,
            "release_candidate_since_step": self.release_candidate_since_step,
            "safety_ceiling_percent": self.safety_ceiling_percent,
            "safety_limited_since_step": self.safety_limited_since_step,
            "safety_reasons": list(self.safety_reasons),
            "safety_revision": self.safety_revision,
        }


@dataclass(frozen=True)
class PropulsionSafetyEventIntent:
    kind: str
    fixed_step_index: int
    previous_ceiling_percent: int
    resulting_ceiling_percent: int
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.kind not in SAFETY_EVENT_KINDS:
            raise ValueError(f"未知安全事件意图：{self.kind}")
        _require_nonnegative_step(self.fixed_step_index, "fixed_step_index")
        _require_stage(self.previous_ceiling_percent, "previous_ceiling_percent")
        _require_stage(self.resulting_ceiling_percent, "resulting_ceiling_percent")
        _require_ordered_reasons(
            self.reasons,
            SOFT_LIMIT_REASON_ORDER,
            "event reasons",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "fixed_step_index": self.fixed_step_index,
            "kind": self.kind,
            "previous_ceiling_percent": self.previous_ceiling_percent,
            "reasons": list(self.reasons),
            "resulting_ceiling_percent": self.resulting_ceiling_percent,
        }


@dataclass(frozen=True)
class PropulsionSafetyDecision:
    current_output_percent: int
    authorized_output_percent: int
    effective_target_percent: int
    action: str
    hard_availability: PropulsionHardAvailability
    evaluated_output_percents: tuple[int, ...]
    resulting_state: PropulsionGovernorDraftState
    event_intents: tuple[PropulsionSafetyEventIntent, ...]

    def __post_init__(self) -> None:
        for value, name in (
            (self.current_output_percent, "current_output_percent"),
            (self.authorized_output_percent, "authorized_output_percent"),
            (self.effective_target_percent, "effective_target_percent"),
        ):
            _require_stage(value, name)
        if self.action not in SAFETY_ACTIONS:
            raise ValueError(f"未知安全动作：{self.action}")
        if not self.evaluated_output_percents:
            raise ValueError("每次判定至少必须求值当前阶段")
        if len(set(self.evaluated_output_percents)) != len(
            self.evaluated_output_percents
        ):
            raise ValueError("同一判定不得重复求值相同阶段")
        for value in self.evaluated_output_percents:
            _require_stage(value, "evaluated_output_percents")
        if len(self.event_intents) > 1:
            raise ValueError("单次安全判定最多生成一个状态转换意图")

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "authorized_output_percent": self.authorized_output_percent,
            "current_output_percent": self.current_output_percent,
            "effective_target_percent": self.effective_target_percent,
            "evaluated_output_percents": list(self.evaluated_output_percents),
            "event_intents": [item.to_dict() for item in self.event_intents],
            "hard_availability": self.hard_availability.to_dict(),
            "interface": PROPULSION_SAFETY_INTERFACE_ID,
            "resulting_state": self.resulting_state.to_dict(),
        }


LoadEvaluator = Callable[[int], PropulsionLoadSample]


def _soft_reasons(
    sample: PropulsionLoadSample,
    profile: PropulsionSafetyProfile,
    *,
    overg: bool,
    crew_safety_lock_enabled: bool,
) -> tuple[str, ...]:
    if overg:
        return ()
    reasons = []
    if sample.structure_ratio > profile.structure_engage_ratio + EPS:
        reasons.append("structure_limit")
    if (
        crew_safety_lock_enabled
        and sample.crew_g > profile.crew_engage_g + EPS
    ):
        reasons.append("crew_limit")
    return tuple(reasons)


def _release_safe(
    sample: PropulsionLoadSample,
    reasons: tuple[str, ...],
    profile: PropulsionSafetyProfile,
) -> bool:
    return all(
        (
            reason == "structure_limit"
            and sample.structure_ratio
            <= profile.structure_release_ratio + EPS
        )
        or (
            reason == "crew_limit"
            and sample.crew_g <= profile.crew_release_g + EPS
        )
        for reason in reasons
    )


def _active_reasons(
    reasons: tuple[str, ...],
    *,
    overg: bool,
    crew_safety_lock_enabled: bool,
) -> tuple[str, ...]:
    if overg:
        return ()
    return tuple(
        reason
        for reason in reasons
        if reason != "crew_limit" or crew_safety_lock_enabled
    )


def _ordered_soft_union(*groups: tuple[str, ...]) -> tuple[str, ...]:
    combined = set().union(*groups)
    return tuple(item for item in SOFT_LIMIT_REASON_ORDER if item in combined)


def _event_intent(
    previous: PropulsionGovernorDraftState,
    resulting: PropulsionGovernorDraftState,
    fixed_step_index: int,
) -> tuple[PropulsionSafetyEventIntent, ...]:
    previous_limited = previous.limited
    resulting_limited = resulting.limited
    if not previous_limited and resulting_limited:
        kind = "engine_safety_limit_engaged"
        reasons = resulting.safety_reasons
    elif previous_limited and not resulting_limited:
        kind = "engine_safety_limit_released"
        reasons = previous.safety_reasons
    elif previous_limited and resulting_limited and (
        previous.safety_ceiling_percent != resulting.safety_ceiling_percent
        or previous.safety_reasons != resulting.safety_reasons
    ):
        kind = "engine_safety_limit_changed"
        reasons = resulting.safety_reasons
    else:
        return ()
    return (
        PropulsionSafetyEventIntent(
            kind,
            fixed_step_index,
            previous.safety_ceiling_percent,
            resulting.safety_ceiling_percent,
            reasons,
        ),
    )


def evaluate_propulsion_safety(
    profile: PropulsionSafetyProfile,
    state: PropulsionGovernorDraftState,
    *,
    current_output_percent: int,
    hard_availability: PropulsionHardAvailability,
    load_evaluator: LoadEvaluator,
    fixed_step_index: int,
    overg: bool = False,
    crew_safety_lock_enabled: bool = True,
) -> PropulsionSafetyDecision:
    """按需评估一个方向通道；回调必须返回该候选下的整舰合成载荷。"""

    _require_stage(current_output_percent, "current_output_percent")
    _require_nonnegative_step(fixed_step_index, "fixed_step_index")
    if not isinstance(overg, bool) or not isinstance(
        crew_safety_lock_enabled, bool
    ):
        raise ValueError("overg 与 crew_safety_lock_enabled 必须是布尔值")
    if (
        state.last_evaluated_step_index is not None
        and fixed_step_index <= state.last_evaluated_step_index
    ):
        raise ValueError("安全判定步号必须严格递增")

    samples: dict[int, PropulsionLoadSample] = {}
    evaluated: list[int] = []

    def sample_at(output_percent: int) -> PropulsionLoadSample:
        _require_stage(output_percent, "load_evaluator output_percent")
        cached = samples.get(output_percent)
        if cached is not None:
            return cached
        value = load_evaluator(output_percent)
        if not isinstance(value, PropulsionLoadSample):
            raise ValueError("load_evaluator 必须返回 PropulsionLoadSample")
        if value.output_percent != output_percent:
            raise ValueError("load_evaluator 返回了错误的输出阶段")
        samples[output_percent] = value
        evaluated.append(output_percent)
        return value

    current_sample = sample_at(current_output_percent)
    current_reasons = _soft_reasons(
        current_sample,
        profile,
        overg=overg,
        crew_safety_lock_enabled=crew_safety_lock_enabled,
    )
    ceiling = state.safety_ceiling_percent
    reasons = state.safety_reasons
    limited_since = state.safety_limited_since_step
    release_candidate = state.release_candidate_since_step

    if current_reasons:
        reasons = _ordered_soft_union(reasons, current_reasons)
        maximum_candidate = min(ceiling, current_output_percent)
        candidates = tuple(
            reversed(
                tuple(
                    item
                    for item in THRUST_OUTPUT_STAGES_PERCENT
                    if item < current_output_percent
                    and item <= maximum_candidate
                )
            )
        )
        safe_ceiling = 0
        for candidate in candidates:
            if not _soft_reasons(
                sample_at(candidate),
                profile,
                overg=overg,
                crew_safety_lock_enabled=crew_safety_lock_enabled,
            ):
                safe_ceiling = candidate
                break
        ceiling = min(ceiling, safe_ceiling)
        limited_since = (
            fixed_step_index if limited_since is None else limited_since
        )
        release_candidate = None
    elif state.limited:
        reasons = _active_reasons(
            reasons,
            overg=overg,
            crew_safety_lock_enabled=crew_safety_lock_enabled,
        )
        if not reasons:
            ceiling = 100
            limited_since = None
            release_candidate = None
        elif _release_safe(current_sample, reasons, profile):
            consecutive = (
                state.last_evaluated_step_index == fixed_step_index - 1
                and release_candidate is not None
            )
            release_candidate = (
                release_candidate if consecutive else fixed_step_index
            )
            if (
                fixed_step_index - release_candidate + 1
                >= profile.release_hold_steps
            ):
                ceiling = 100
                reasons = ()
                limited_since = None
                release_candidate = None
        else:
            release_candidate = None
    else:
        unrestricted_target = min(
            telegraph_notch_percent(state.commanded_notch),
            hard_availability.ceiling_percent,
        )
        if unrestricted_target > current_output_percent:
            candidate = adjacent_output_stage_percent(
                current_output_percent,
                unrestricted_target,
            )
            candidate_reasons = _soft_reasons(
                sample_at(candidate),
                profile,
                overg=overg,
                crew_safety_lock_enabled=crew_safety_lock_enabled,
            )
            if candidate_reasons:
                ceiling = current_output_percent
                reasons = candidate_reasons
                limited_since = fixed_step_index
                release_candidate = None

    safety_semantics_before = (
        state.safety_ceiling_percent,
        state.safety_reasons,
        state.safety_limited_since_step,
        state.release_candidate_since_step,
    )
    safety_semantics_after = (
        ceiling,
        reasons,
        limited_since,
        release_candidate,
    )
    resulting = replace(
        state,
        safety_ceiling_percent=ceiling,
        safety_reasons=reasons,
        safety_limited_since_step=limited_since,
        release_candidate_since_step=release_candidate,
        last_evaluated_step_index=fixed_step_index,
        safety_revision=(
            state.safety_revision + 1
            if safety_semantics_before != safety_semantics_after
            else state.safety_revision
        ),
    )
    effective_target = min(
        telegraph_notch_percent(resulting.commanded_notch),
        resulting.safety_ceiling_percent,
        hard_availability.ceiling_percent,
    )
    authorized = adjacent_output_stage_percent(
        current_output_percent,
        effective_target,
    )
    action = (
        "allow_upstage"
        if authorized > current_output_percent
        else (
            "schedule_downstage"
            if authorized < current_output_percent
            else "hold"
        )
    )
    return PropulsionSafetyDecision(
        current_output_percent,
        authorized,
        effective_target,
        action,
        hard_availability,
        tuple(evaluated),
        resulting,
        _event_intent(state, resulting, fixed_step_index),
    )
