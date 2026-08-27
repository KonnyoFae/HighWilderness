"""阶段 I11b：显式来源人员伤亡账本与原子结算。"""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
from typing import Any, Iterable

from 高天荒野舰艇数据契约 import (
    CREW_TYPES,
    ContractError,
    CrewCasualtyStatusInput,
    RESOURCE_ID_PATTERN,
    ShipCrewCasualtyStateInput,
    ShipInstanceSnapshotInput,
    SortieCrewCount,
    canonical_sha256,
)


CREW_CASUALTY_INTERFACE_ID = "gaotian.ship-crew-casualties/v1alpha1"
CREW_CASUALTY_POLICY_ID = (
    "gaotian.crew-casualties/explicit-source-facts-step-boundary/v1"
)
CREW_CASUALTY_SOURCE_KINDS = {
    "projectile_impact",
    "fire_damage",
    "secondary_explosion",
}
EPS = 1.0e-8


def _resource_id(value: Any, path: str) -> str:
    if not isinstance(value, str) or not RESOURCE_ID_PATTERN.fullmatch(value):
        raise ContractError("crew_casualty.resource_id", path, str(value))
    return value


def _integer(value: Any, path: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ContractError(
            "crew_casualty.integer",
            path,
            f"必须是大于等于 {minimum} 的整数",
        )
    return value


def _number(value: Any, path: str, minimum: float = 0.0) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) < minimum
    ):
        raise ContractError(
            "crew_casualty.number",
            path,
            f"必须是大于等于 {minimum} 的有限数",
        )
    return float(value)


@dataclass(frozen=True)
class CrewCasualtyBreakdown:
    crew_type: str
    wounded_count: int
    dead_count: int

    def validate(self, path: str = "$") -> None:
        if self.crew_type not in CREW_TYPES:
            raise ContractError(
                "crew_casualty.crew_type",
                f"{path}.crew_type",
                self.crew_type,
            )
        wounded = _integer(self.wounded_count, f"{path}.wounded_count")
        dead = _integer(self.dead_count, f"{path}.dead_count")
        if wounded + dead <= 0:
            raise ContractError(
                "crew_casualty.empty_breakdown",
                path,
                "每项伤亡必须至少包含一名负伤或死亡人员",
            )

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "crew_type": self.crew_type,
            "dead_count": self.dead_count,
            "wounded_count": self.wounded_count,
        }


@dataclass(frozen=True)
class CrewCasualtyOutcome:
    outcome_id: str
    source_kind: str
    source_id: str
    source_tactical_time_s: float
    target_ship_id: str
    target_module_instance_id: str | None
    casualties: tuple[CrewCasualtyBreakdown, ...]

    @property
    def source_key(self) -> tuple[str, str]:
        return self.source_kind, self.source_id

    def validate(self, path: str = "$") -> None:
        _resource_id(self.outcome_id, f"{path}.outcome_id")
        if self.source_kind not in CREW_CASUALTY_SOURCE_KINDS:
            raise ContractError(
                "crew_casualty.source_kind",
                f"{path}.source_kind",
                self.source_kind,
            )
        _resource_id(self.source_id, f"{path}.source_id")
        _number(
            self.source_tactical_time_s,
            f"{path}.source_tactical_time_s",
        )
        _resource_id(self.target_ship_id, f"{path}.target_ship_id")
        if self.target_module_instance_id is not None:
            _resource_id(
                self.target_module_instance_id,
                f"{path}.target_module_instance_id",
            )
        if not self.casualties:
            raise ContractError(
                "crew_casualty.outcome_empty",
                f"{path}.casualties",
                "伤亡结果不能为空",
            )
        for index, item in enumerate(self.casualties):
            item.validate(f"{path}.casualties[{index}]")
        if len({item.crew_type for item in self.casualties}) != len(
            self.casualties
        ):
            raise ContractError(
                "crew_casualty.crew_type_duplicate",
                f"{path}.casualties",
                "同一伤亡结果中的人员类别不得重复",
            )

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "casualties": [
                item.to_dict()
                for item in sorted(self.casualties, key=lambda item: item.crew_type)
            ],
            "outcome_id": self.outcome_id,
            "source_id": self.source_id,
            "source_kind": self.source_kind,
            "source_tactical_time_s": self.source_tactical_time_s,
            "target_module_instance_id": self.target_module_instance_id,
            "target_ship_id": self.target_ship_id,
        }


@dataclass(frozen=True)
class CrewCasualtyEvent:
    ship_id: str
    tactical_time_s: float
    outcome_id: str
    source_kind: str
    source_id: str
    crew_type: str
    fit_for_duty_before: int
    fit_for_duty_after: int
    wounded_before: int
    wounded_after: int
    dead_before: int
    dead_after: int
    newly_wounded_count: int
    newly_dead_count: int
    target_module_instance_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "crew_type": self.crew_type,
            "dead_after": self.dead_after,
            "dead_before": self.dead_before,
            "fit_for_duty_after": self.fit_for_duty_after,
            "fit_for_duty_before": self.fit_for_duty_before,
            "newly_dead_count": self.newly_dead_count,
            "newly_wounded_count": self.newly_wounded_count,
            "outcome_id": self.outcome_id,
            "ship_id": self.ship_id,
            "source_id": self.source_id,
            "source_kind": self.source_kind,
            "tactical_time_s": self.tactical_time_s,
            "wounded_after": self.wounded_after,
            "wounded_before": self.wounded_before,
        }
        if self.target_module_instance_id is not None:
            result["target_module_instance_id"] = self.target_module_instance_id
        return result


@dataclass(frozen=True)
class CrewCasualtyResolution:
    source_instance_sha256: str
    resulting_instance: ShipInstanceSnapshotInput
    events: tuple[CrewCasualtyEvent, ...]


def initialize_crew_casualty_state(
    instance: ShipInstanceSnapshotInput,
    *,
    tactical_time_s: float,
) -> ShipCrewCasualtyStateInput:
    """从当前可执勤人员池建立零伤亡账本。"""

    _number(tactical_time_s, "$.tactical_time_s")
    return ShipCrewCasualtyStateInput(
        tactical_time_s,
        tuple(
            CrewCasualtyStatusInput(item.crew_type, item.count, 0, 0)
            for item in instance.operational_state.crew
        ),
    )


def validate_instance_crew_casualty_state(
    instance: ShipInstanceSnapshotInput,
) -> None:
    state = instance.crew_casualty_state
    if state is None:
        return
    _number(state.tactical_time_s, "$.crew_casualty_state.tactical_time_s")
    if state.last_strategic_operation_time_s is not None:
        _number(
            state.last_strategic_operation_time_s,
            "$.crew_casualty_state.last_strategic_operation_time_s",
        )
    if len({item.crew_type for item in state.crew_statuses}) != len(
        state.crew_statuses
    ):
        raise ContractError(
            "crew_casualty.crew_type_duplicate",
            "$.crew_casualty_state.crew_statuses",
            "人员类别不得重复",
        )
    fit_for_duty: dict[str, int] = {}
    for index, item in enumerate(state.crew_statuses):
        path = f"$.crew_casualty_state.crew_statuses[{index}]"
        if item.crew_type not in CREW_TYPES:
            raise ContractError(
                "crew_casualty.crew_type",
                f"{path}.crew_type",
                item.crew_type,
            )
        fit = _integer(item.fit_for_duty_count, f"{path}.fit_for_duty_count")
        wounded = _integer(item.wounded_count, f"{path}.wounded_count")
        dead = _integer(item.dead_count, f"{path}.dead_count")
        if fit + wounded + dead <= 0:
            raise ContractError(
                "crew_casualty.empty_status",
                path,
                "人员伤亡账本不得保留全零类别",
            )
        if fit > 0:
            fit_for_duty[item.crew_type] = fit
    operational = {
        item.crew_type: item.count for item in instance.operational_state.crew
    }
    if operational != fit_for_duty:
        raise ContractError(
            "crew_casualty.fit_mismatch",
            "$.crew_casualty_state.crew_statuses",
            "可执勤人数必须与 operational_state.crew 完全一致",
        )


def persons_aboard_count(instance: ShipInstanceSnapshotInput) -> int:
    """返回会继续触发乘员安全锁的在舰活人数量。"""

    validate_instance_crew_casualty_state(instance)
    state = instance.crew_casualty_state
    if state is None:
        return sum(item.count for item in instance.operational_state.crew)
    return sum(
        item.fit_for_duty_count + item.wounded_count
        for item in state.crew_statuses
    )


def validate_crew_casualty_capacity(
    instance: ShipInstanceSnapshotInput,
    crew_capacity: dict[str, int],
) -> None:
    """校验可执勤与负伤人员仍能被当前设计的人员舱容纳。"""

    validate_instance_crew_casualty_state(instance)
    state = instance.crew_casualty_state
    if state is None:
        return
    for item in state.crew_statuses:
        alive = item.fit_for_duty_count + item.wounded_count
        capacity = crew_capacity.get(item.crew_type, 0)
        if alive > capacity:
            raise ContractError(
                "crew_casualty.capacity_exceeded",
                f"$.crew_casualty_state.crew_statuses.{item.crew_type}",
                f"在舰活人 {alive}，当前设计容量 {capacity}",
            )


def apply_crew_casualty_outcomes(
    instance: ShipInstanceSnapshotInput,
    outcomes: Iterable[CrewCasualtyOutcome],
    *,
    ship_id: str,
    target_tactical_time_s: float,
) -> CrewCasualtyResolution:
    """在固定步边界原子应用已经由上层确认的伤亡事实。"""

    source_sha256 = canonical_sha256(instance)
    _resource_id(ship_id, "$.ship_id")
    target_time = _number(target_tactical_time_s, "$.target_tactical_time_s")
    validate_instance_crew_casualty_state(instance)
    state = instance.crew_casualty_state
    items = tuple(outcomes)
    for index, item in enumerate(items):
        item.validate(f"$.outcomes[{index}]")
        if item.target_ship_id != ship_id:
            raise ContractError(
                "crew_casualty.ship_mismatch",
                f"$.outcomes[{index}].target_ship_id",
                item.target_ship_id,
            )
        if item.source_tactical_time_s > target_time + EPS:
            raise ContractError(
                "crew_casualty.source_in_future",
                f"$.outcomes[{index}].source_tactical_time_s",
                "伤亡来源时刻不得晚于目标边界",
            )
    if len({item.outcome_id for item in items}) != len(items):
        raise ContractError(
            "crew_casualty.outcome_duplicate",
            "$.outcomes",
            "同一固定步的伤亡结果 id 不得重复",
        )
    if len({item.source_key for item in items}) != len(items):
        raise ContractError(
            "crew_casualty.source_duplicate",
            "$.outcomes",
            "同一来源事件不得重复结算伤亡",
        )
    if state is not None and target_time + EPS < state.tactical_time_s:
        raise ContractError(
            "crew_casualty.time_reversed",
            "$.target_tactical_time_s",
            "人员伤亡账本时钟不得倒退",
        )
    if not items and state is None:
        return CrewCasualtyResolution(source_sha256, instance, ())
    if state is None:
        state = initialize_crew_casualty_state(
            instance,
            tactical_time_s=(
                min(item.source_tactical_time_s for item in items)
                if items
                else target_time
            ),
        )
    for index, item in enumerate(items):
        if item.source_tactical_time_s + EPS < state.tactical_time_s:
            raise ContractError(
                "crew_casualty.source_before_state",
                f"$.outcomes[{index}].source_tactical_time_s",
                "伤亡来源不得早于当前人员账本时钟",
            )

    statuses = {item.crew_type: item for item in state.crew_statuses}
    events: list[CrewCasualtyEvent] = []
    for outcome in sorted(
        items,
        key=lambda item: (item.source_tactical_time_s, item.outcome_id),
    ):
        for casualty in sorted(outcome.casualties, key=lambda item: item.crew_type):
            current = statuses.get(
                casualty.crew_type,
                CrewCasualtyStatusInput(casualty.crew_type, 0, 0, 0),
            )
            removed = casualty.wounded_count + casualty.dead_count
            if removed > current.fit_for_duty_count:
                raise ContractError(
                    "crew_casualty.insufficient_fit_crew",
                    f"$.outcomes.{outcome.outcome_id}.casualties.{casualty.crew_type}",
                    f"可执勤 {current.fit_for_duty_count}，请求伤亡 {removed}",
                )
            updated = CrewCasualtyStatusInput(
                current.crew_type,
                current.fit_for_duty_count - removed,
                current.wounded_count + casualty.wounded_count,
                current.dead_count + casualty.dead_count,
            )
            statuses[casualty.crew_type] = updated
            events.append(
                CrewCasualtyEvent(
                    ship_id,
                    outcome.source_tactical_time_s,
                    outcome.outcome_id,
                    outcome.source_kind,
                    outcome.source_id,
                    casualty.crew_type,
                    current.fit_for_duty_count,
                    updated.fit_for_duty_count,
                    current.wounded_count,
                    updated.wounded_count,
                    current.dead_count,
                    updated.dead_count,
                    casualty.wounded_count,
                    casualty.dead_count,
                    outcome.target_module_instance_id,
                )
            )

    normalized_statuses = tuple(
        sorted(
            (item for item in statuses.values() if item.total_count > 0),
            key=lambda item: item.crew_type,
        )
    )
    operational_crew = tuple(
        SortieCrewCount(item.crew_type, item.fit_for_duty_count)
        for item in normalized_statuses
        if item.fit_for_duty_count > 0
    )
    resulting = replace(
        instance,
        operational_state=replace(
            instance.operational_state,
            crew=operational_crew,
        ),
        crew_casualty_state=ShipCrewCasualtyStateInput(
            target_time,
            normalized_statuses,
            state.last_strategic_operation_time_s,
        ),
    )
    validate_instance_crew_casualty_state(resulting)
    return CrewCasualtyResolution(
        source_sha256,
        resulting,
        tuple(events),
    )
