"""阶段 I11c：显式医疗、舰间人员转移、弃舰与战略救援边界。"""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
from typing import Any, Iterable

from 高天荒野舰艇人员伤亡 import (
    initialize_crew_casualty_state,
    validate_crew_casualty_capacity,
    validate_instance_crew_casualty_state,
)
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
from 高天荒野舰艇无界面舾装编译器 import DerivedShipSnapshot
from 高天荒野舰艇实例设计状态 import validate_instance_current_design


CREW_RECOVERY_INTERFACE_ID = "gaotian.ship-crew-recovery/v1alpha1"
CREW_RECOVERY_POLICY_ID = (
    "gaotian.crew-recovery/explicit-outcomes-conserved-atomic/v1"
)
RESCUE_MANIFEST_STATUSES = {"awaiting_recovery", "recovered", "lost"}
RESCUE_DISPOSITIONS = {"recovered_to_ship", "lost"}
EPS = 1.0e-8


def _resource_id(value: Any, path: str) -> str:
    if not isinstance(value, str) or not RESOURCE_ID_PATTERN.fullmatch(value):
        raise ContractError("crew_recovery.resource_id", path, str(value))
    return value


def _integer(value: Any, path: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ContractError(
            "crew_recovery.integer",
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
            "crew_recovery.number",
            path,
            f"必须是大于等于 {minimum} 的有限数",
        )
    return float(value)


def _crew_type(value: str, path: str) -> str:
    if not isinstance(value, str) or value not in CREW_TYPES:
        raise ContractError("crew_recovery.crew_type", path, value)
    return value


def _object(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError("crew_recovery.object", path, "必须是对象")
    return value


def _keys(
    value: dict[str, Any],
    path: str,
    required: Iterable[str],
    optional: Iterable[str] = (),
) -> None:
    required_set = set(required)
    allowed = required_set | set(optional)
    missing = sorted(required_set - value.keys())
    extra = sorted(value.keys() - allowed)
    if missing:
        raise ContractError(
            "crew_recovery.missing_keys",
            path,
            f"缺少字段 {missing}",
        )
    if extra:
        raise ContractError(
            "crew_recovery.extra_keys",
            path,
            f"未知字段 {extra}",
        )


def _strategic_time_guard(
    state: ShipCrewCasualtyStateInput,
    strategic_time_s: float,
    path: str,
) -> None:
    previous = state.last_strategic_operation_time_s
    if previous is not None and strategic_time_s + EPS < previous:
        raise ContractError(
            "crew_recovery.strategic_time_reversed",
            path,
            f"人员战略操作时钟不得从 {previous} 倒退到 {strategic_time_s}",
        )


def _state_or_initialize(
    instance: ShipInstanceSnapshotInput,
    *,
    tactical_time_s: float = 0.0,
) -> ShipCrewCasualtyStateInput:
    validate_instance_crew_casualty_state(instance)
    if instance.crew_casualty_state is not None:
        return instance.crew_casualty_state
    return initialize_crew_casualty_state(
        instance,
        tactical_time_s=tactical_time_s,
    )


def _write_statuses(
    instance: ShipInstanceSnapshotInput,
    state: ShipCrewCasualtyStateInput,
    statuses: dict[str, CrewCasualtyStatusInput],
    *,
    tactical_time_s: float | None = None,
    strategic_time_s: float | None = None,
) -> ShipInstanceSnapshotInput:
    normalized = tuple(
        sorted(
            (item for item in statuses.values() if item.total_count > 0),
            key=lambda item: item.crew_type,
        )
    )
    operational = tuple(
        SortieCrewCount(item.crew_type, item.fit_for_duty_count)
        for item in normalized
        if item.fit_for_duty_count > 0
    )
    resulting = replace(
        instance,
        operational_state=replace(instance.operational_state, crew=operational),
        crew_casualty_state=ShipCrewCasualtyStateInput(
            state.tactical_time_s if tactical_time_s is None else tactical_time_s,
            normalized,
            (
                state.last_strategic_operation_time_s
                if strategic_time_s is None
                else strategic_time_s
            ),
        ),
    )
    validate_instance_crew_casualty_state(resulting)
    return resulting


@dataclass(frozen=True)
class CrewMedicalChange:
    crew_type: str
    recovered_count: int
    died_from_wounds_count: int

    def validate(self, path: str = "$") -> None:
        _crew_type(self.crew_type, f"{path}.crew_type")
        recovered = _integer(self.recovered_count, f"{path}.recovered_count")
        died = _integer(
            self.died_from_wounds_count,
            f"{path}.died_from_wounds_count",
        )
        if recovered + died <= 0:
            raise ContractError(
                "crew_recovery.medical_change_empty",
                path,
                "医疗结算必须至少恢复或死亡一名负伤人员",
            )

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "crew_type": self.crew_type,
            "died_from_wounds_count": self.died_from_wounds_count,
            "recovered_count": self.recovered_count,
        }


@dataclass(frozen=True)
class CrewMedicalOutcome:
    operation_id: str
    strategic_time_s: float
    ship_id: str
    changes: tuple[CrewMedicalChange, ...]

    def validate(self, path: str = "$") -> None:
        _resource_id(self.operation_id, f"{path}.operation_id")
        _number(self.strategic_time_s, f"{path}.strategic_time_s")
        _resource_id(self.ship_id, f"{path}.ship_id")
        if not self.changes:
            raise ContractError(
                "crew_recovery.medical_outcome_empty",
                f"{path}.changes",
                "医疗结果不能为空",
            )
        for index, item in enumerate(self.changes):
            item.validate(f"{path}.changes[{index}]")
        if len({item.crew_type for item in self.changes}) != len(self.changes):
            raise ContractError(
                "crew_recovery.crew_type_duplicate",
                f"{path}.changes",
                "同一医疗结果中的人员类别不得重复",
            )

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "changes": [
                item.to_dict()
                for item in sorted(self.changes, key=lambda item: item.crew_type)
            ],
            "operation_id": self.operation_id,
            "ship_id": self.ship_id,
            "strategic_time_s": self.strategic_time_s,
        }


@dataclass(frozen=True)
class CrewMedicalEvent:
    operation_id: str
    strategic_time_s: float
    ship_id: str
    crew_type: str
    fit_for_duty_before: int
    fit_for_duty_after: int
    wounded_before: int
    wounded_after: int
    dead_before: int
    dead_after: int
    recovered_count: int
    died_from_wounds_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "crew_type": self.crew_type,
            "dead_after": self.dead_after,
            "dead_before": self.dead_before,
            "died_from_wounds_count": self.died_from_wounds_count,
            "fit_for_duty_after": self.fit_for_duty_after,
            "fit_for_duty_before": self.fit_for_duty_before,
            "operation_id": self.operation_id,
            "recovered_count": self.recovered_count,
            "ship_id": self.ship_id,
            "strategic_time_s": self.strategic_time_s,
            "wounded_after": self.wounded_after,
            "wounded_before": self.wounded_before,
        }


@dataclass(frozen=True)
class CrewMedicalResolution:
    source_instance_sha256: str
    resulting_instance: ShipInstanceSnapshotInput
    events: tuple[CrewMedicalEvent, ...]


def apply_crew_medical_outcome(
    instance: ShipInstanceSnapshotInput,
    snapshot: DerivedShipSnapshot,
    outcome: CrewMedicalOutcome,
    *,
    ship_id: str,
) -> CrewMedicalResolution:
    """原子应用上层已经确认的伤员恢复/恶化结果，不推导治疗概率。"""

    source_sha256 = canonical_sha256(instance)
    outcome.validate("$.medical_outcome")
    validate_instance_current_design(snapshot, instance)
    _resource_id(ship_id, "$.ship_id")
    if outcome.ship_id != ship_id:
        raise ContractError(
            "crew_recovery.ship_mismatch",
            "$.medical_outcome.ship_id",
            outcome.ship_id,
        )
    state = _state_or_initialize(instance)
    _strategic_time_guard(
        state,
        outcome.strategic_time_s,
        "$.medical_outcome.strategic_time_s",
    )
    statuses = {item.crew_type: item for item in state.crew_statuses}
    events: list[CrewMedicalEvent] = []
    for change in sorted(outcome.changes, key=lambda item: item.crew_type):
        current = statuses.get(
            change.crew_type,
            CrewCasualtyStatusInput(change.crew_type, 0, 0, 0),
        )
        resolved = change.recovered_count + change.died_from_wounds_count
        if resolved > current.wounded_count:
            raise ContractError(
                "crew_recovery.insufficient_wounded",
                f"$.medical_outcome.changes.{change.crew_type}",
                f"负伤 {current.wounded_count}，请求结算 {resolved}",
            )
        updated = CrewCasualtyStatusInput(
            change.crew_type,
            current.fit_for_duty_count + change.recovered_count,
            current.wounded_count - resolved,
            current.dead_count + change.died_from_wounds_count,
        )
        statuses[change.crew_type] = updated
        events.append(
            CrewMedicalEvent(
                outcome.operation_id,
                outcome.strategic_time_s,
                outcome.ship_id,
                change.crew_type,
                current.fit_for_duty_count,
                updated.fit_for_duty_count,
                current.wounded_count,
                updated.wounded_count,
                current.dead_count,
                updated.dead_count,
                change.recovered_count,
                change.died_from_wounds_count,
            )
        )
    resulting = _write_statuses(
        instance,
        state,
        statuses,
        strategic_time_s=outcome.strategic_time_s,
    )
    validate_crew_casualty_capacity(resulting, dict(snapshot.outfit.crew_capacity))
    return CrewMedicalResolution(source_sha256, resulting, tuple(events))


@dataclass(frozen=True)
class CrewTransferCount:
    crew_type: str
    fit_for_duty_count: int
    wounded_count: int

    def validate(self, path: str = "$") -> None:
        _crew_type(self.crew_type, f"{path}.crew_type")
        fit = _integer(self.fit_for_duty_count, f"{path}.fit_for_duty_count")
        wounded = _integer(self.wounded_count, f"{path}.wounded_count")
        if fit + wounded <= 0:
            raise ContractError(
                "crew_recovery.transfer_empty",
                path,
                "舰间转移项必须至少包含一名活人",
            )

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "crew_type": self.crew_type,
            "fit_for_duty_count": self.fit_for_duty_count,
            "wounded_count": self.wounded_count,
        }


@dataclass(frozen=True)
class CrewTransferDirective:
    operation_id: str
    strategic_time_s: float
    source_ship_id: str
    target_ship_id: str
    transfers: tuple[CrewTransferCount, ...]

    def validate(self, path: str = "$") -> None:
        _resource_id(self.operation_id, f"{path}.operation_id")
        _number(self.strategic_time_s, f"{path}.strategic_time_s")
        _resource_id(self.source_ship_id, f"{path}.source_ship_id")
        _resource_id(self.target_ship_id, f"{path}.target_ship_id")
        if self.source_ship_id == self.target_ship_id:
            raise ContractError(
                "crew_recovery.transfer_same_ship",
                path,
                "人员转移的来源舰与目标舰必须不同",
            )
        if not self.transfers:
            raise ContractError(
                "crew_recovery.transfer_directive_empty",
                f"{path}.transfers",
                "人员转移清单不能为空",
            )
        for index, item in enumerate(self.transfers):
            item.validate(f"{path}.transfers[{index}]")
        if len({item.crew_type for item in self.transfers}) != len(self.transfers):
            raise ContractError(
                "crew_recovery.crew_type_duplicate",
                f"{path}.transfers",
                "同一转移清单中的人员类别不得重复",
            )

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "operation_id": self.operation_id,
            "source_ship_id": self.source_ship_id,
            "strategic_time_s": self.strategic_time_s,
            "target_ship_id": self.target_ship_id,
            "transfers": [
                item.to_dict()
                for item in sorted(self.transfers, key=lambda item: item.crew_type)
            ],
        }


@dataclass(frozen=True)
class CrewTransferEvent:
    operation_id: str
    strategic_time_s: float
    source_ship_id: str
    target_ship_id: str
    crew_type: str
    fit_for_duty_count: int
    wounded_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "crew_type": self.crew_type,
            "fit_for_duty_count": self.fit_for_duty_count,
            "operation_id": self.operation_id,
            "source_ship_id": self.source_ship_id,
            "strategic_time_s": self.strategic_time_s,
            "target_ship_id": self.target_ship_id,
            "wounded_count": self.wounded_count,
        }


@dataclass(frozen=True)
class CrewTransferResolution:
    source_instance_sha256: str
    target_instance_sha256: str
    resulting_source_instance: ShipInstanceSnapshotInput
    resulting_target_instance: ShipInstanceSnapshotInput
    events: tuple[CrewTransferEvent, ...]


def transfer_crew_between_ships(
    source_instance: ShipInstanceSnapshotInput,
    source_snapshot: DerivedShipSnapshot,
    target_instance: ShipInstanceSnapshotInput,
    target_snapshot: DerivedShipSnapshot,
    directive: CrewTransferDirective,
    *,
    source_ship_id: str,
    target_ship_id: str,
) -> CrewTransferResolution:
    """在战略边界原子完成一次舰间活人转移；死亡人员不进入本接口。"""

    directive.validate("$.transfer_directive")
    validate_instance_current_design(source_snapshot, source_instance)
    validate_instance_current_design(target_snapshot, target_instance)
    _resource_id(source_ship_id, "$.source_ship_id")
    _resource_id(target_ship_id, "$.target_ship_id")
    if (
        directive.source_ship_id != source_ship_id
        or directive.target_ship_id != target_ship_id
    ):
        raise ContractError(
            "crew_recovery.transfer_ship_mismatch",
            "$.transfer_directive",
            "转移指令与调用方绑定的来源舰/目标舰不一致",
        )
    source_state = _state_or_initialize(source_instance)
    target_state = _state_or_initialize(target_instance)
    _strategic_time_guard(
        source_state,
        directive.strategic_time_s,
        "$.transfer_directive.strategic_time_s",
    )
    _strategic_time_guard(
        target_state,
        directive.strategic_time_s,
        "$.transfer_directive.strategic_time_s",
    )
    validate_crew_casualty_capacity(
        source_instance,
        dict(source_snapshot.outfit.crew_capacity),
    )
    validate_crew_casualty_capacity(
        target_instance,
        dict(target_snapshot.outfit.crew_capacity),
    )
    source_statuses = {item.crew_type: item for item in source_state.crew_statuses}
    target_statuses = {item.crew_type: item for item in target_state.crew_statuses}
    events: list[CrewTransferEvent] = []
    for item in sorted(directive.transfers, key=lambda value: value.crew_type):
        source_current = source_statuses.get(
            item.crew_type,
            CrewCasualtyStatusInput(item.crew_type, 0, 0, 0),
        )
        if item.fit_for_duty_count > source_current.fit_for_duty_count:
            raise ContractError(
                "crew_recovery.insufficient_fit_crew",
                f"$.transfer_directive.transfers.{item.crew_type}",
                f"可执勤 {source_current.fit_for_duty_count}，请求转移 {item.fit_for_duty_count}",
            )
        if item.wounded_count > source_current.wounded_count:
            raise ContractError(
                "crew_recovery.insufficient_wounded",
                f"$.transfer_directive.transfers.{item.crew_type}",
                f"负伤 {source_current.wounded_count}，请求转移 {item.wounded_count}",
            )
        target_current = target_statuses.get(
            item.crew_type,
            CrewCasualtyStatusInput(item.crew_type, 0, 0, 0),
        )
        source_statuses[item.crew_type] = CrewCasualtyStatusInput(
            item.crew_type,
            source_current.fit_for_duty_count - item.fit_for_duty_count,
            source_current.wounded_count - item.wounded_count,
            source_current.dead_count,
        )
        target_statuses[item.crew_type] = CrewCasualtyStatusInput(
            item.crew_type,
            target_current.fit_for_duty_count + item.fit_for_duty_count,
            target_current.wounded_count + item.wounded_count,
            target_current.dead_count,
        )
        events.append(
            CrewTransferEvent(
                directive.operation_id,
                directive.strategic_time_s,
                directive.source_ship_id,
                directive.target_ship_id,
                item.crew_type,
                item.fit_for_duty_count,
                item.wounded_count,
            )
        )
    resulting_source = _write_statuses(
        source_instance,
        source_state,
        source_statuses,
        strategic_time_s=directive.strategic_time_s,
    )
    resulting_target = _write_statuses(
        target_instance,
        target_state,
        target_statuses,
        strategic_time_s=directive.strategic_time_s,
    )
    validate_crew_casualty_capacity(
        resulting_source,
        dict(source_snapshot.outfit.crew_capacity),
    )
    validate_crew_casualty_capacity(
        resulting_target,
        dict(target_snapshot.outfit.crew_capacity),
    )
    return CrewTransferResolution(
        canonical_sha256(source_instance),
        canonical_sha256(target_instance),
        resulting_source,
        resulting_target,
        tuple(events),
    )


@dataclass(frozen=True)
class CrewEvacuationCount:
    crew_type: str
    evacuated_fit_for_duty_count: int
    evacuated_wounded_count: int
    newly_dead_fit_for_duty_count: int
    newly_dead_wounded_count: int

    def validate(self, path: str = "$") -> None:
        _crew_type(self.crew_type, f"{path}.crew_type")
        fit = _integer(
            self.evacuated_fit_for_duty_count,
            f"{path}.evacuated_fit_for_duty_count",
        )
        wounded = _integer(
            self.evacuated_wounded_count,
            f"{path}.evacuated_wounded_count",
        )
        dead_fit = _integer(
            self.newly_dead_fit_for_duty_count,
            f"{path}.newly_dead_fit_for_duty_count",
        )
        dead_wounded = _integer(
            self.newly_dead_wounded_count,
            f"{path}.newly_dead_wounded_count",
        )
        if fit + wounded + dead_fit + dead_wounded <= 0:
            raise ContractError(
                "crew_recovery.evacuation_count_empty",
                path,
                "弃舰结果项不能为空",
            )

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "crew_type": self.crew_type,
            "evacuated_fit_for_duty_count": self.evacuated_fit_for_duty_count,
            "evacuated_wounded_count": self.evacuated_wounded_count,
            "newly_dead_fit_for_duty_count": self.newly_dead_fit_for_duty_count,
            "newly_dead_wounded_count": self.newly_dead_wounded_count,
        }


@dataclass(frozen=True)
class CrewEvacuationOutcome:
    operation_id: str
    rescue_manifest_id: str
    tactical_time_s: float
    ship_id: str
    counts: tuple[CrewEvacuationCount, ...]

    def validate(self, path: str = "$") -> None:
        _resource_id(self.operation_id, f"{path}.operation_id")
        _resource_id(self.rescue_manifest_id, f"{path}.rescue_manifest_id")
        _number(self.tactical_time_s, f"{path}.tactical_time_s")
        _resource_id(self.ship_id, f"{path}.ship_id")
        if not self.counts:
            raise ContractError(
                "crew_recovery.evacuation_outcome_empty",
                f"{path}.counts",
                "弃舰结果不能为空",
            )
        for index, item in enumerate(self.counts):
            item.validate(f"{path}.counts[{index}]")
        if len({item.crew_type for item in self.counts}) != len(self.counts):
            raise ContractError(
                "crew_recovery.crew_type_duplicate",
                f"{path}.counts",
                "同一弃舰结果中的人员类别不得重复",
            )

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "counts": [
                item.to_dict()
                for item in sorted(self.counts, key=lambda item: item.crew_type)
            ],
            "operation_id": self.operation_id,
            "rescue_manifest_id": self.rescue_manifest_id,
            "ship_id": self.ship_id,
            "tactical_time_s": self.tactical_time_s,
        }


@dataclass(frozen=True)
class CrewRescueCount:
    crew_type: str
    fit_for_duty_count: int
    wounded_count: int

    def validate(self, path: str = "$") -> None:
        _crew_type(self.crew_type, f"{path}.crew_type")
        fit = _integer(self.fit_for_duty_count, f"{path}.fit_for_duty_count")
        wounded = _integer(self.wounded_count, f"{path}.wounded_count")
        if fit + wounded <= 0:
            raise ContractError(
                "crew_recovery.rescue_count_empty",
                path,
                "待救援清单项必须至少包含一名幸存者",
            )

    @classmethod
    def parse(cls, value: Any, path: str = "$") -> "CrewRescueCount":
        obj = _object(value, path)
        _keys(
            obj,
            path,
            ("crew_type", "fit_for_duty_count", "wounded_count"),
        )
        result = cls(
            obj["crew_type"],
            obj["fit_for_duty_count"],
            obj["wounded_count"],
        )
        result.validate(path)
        return result

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "crew_type": self.crew_type,
            "fit_for_duty_count": self.fit_for_duty_count,
            "wounded_count": self.wounded_count,
        }


@dataclass(frozen=True)
class CrewRescueManifest:
    manifest_id: str
    source_operation_id: str
    source_ship_id: str
    source_tactical_time_s: float
    survivors: tuple[CrewRescueCount, ...]
    status: str = "awaiting_recovery"
    resolution_operation_id: str | None = None
    resolution_strategic_time_s: float | None = None
    destination_ship_id: str | None = None

    def validate(self, path: str = "$") -> None:
        _resource_id(self.manifest_id, f"{path}.manifest_id")
        _resource_id(self.source_operation_id, f"{path}.source_operation_id")
        _resource_id(self.source_ship_id, f"{path}.source_ship_id")
        _number(self.source_tactical_time_s, f"{path}.source_tactical_time_s")
        if not self.survivors:
            raise ContractError(
                "crew_recovery.manifest_empty",
                f"{path}.survivors",
                "没有幸存者时不得生成待救援清单",
            )
        for index, item in enumerate(self.survivors):
            item.validate(f"{path}.survivors[{index}]")
        if len({item.crew_type for item in self.survivors}) != len(self.survivors):
            raise ContractError(
                "crew_recovery.crew_type_duplicate",
                f"{path}.survivors",
                "待救援清单中的人员类别不得重复",
            )
        if (
            not isinstance(self.status, str)
            or self.status not in RESCUE_MANIFEST_STATUSES
        ):
            raise ContractError(
                "crew_recovery.manifest_status",
                f"{path}.status",
                self.status,
            )
        if self.status == "awaiting_recovery":
            if any(
                item is not None
                for item in (
                    self.resolution_operation_id,
                    self.resolution_strategic_time_s,
                    self.destination_ship_id,
                )
            ):
                raise ContractError(
                    "crew_recovery.manifest_resolution_unexpected",
                    path,
                    "待救援清单不得提前携带结算信息",
                )
            return
        if self.resolution_operation_id is None or self.resolution_strategic_time_s is None:
            raise ContractError(
                "crew_recovery.manifest_resolution_missing",
                path,
                "已结算清单必须记录结算操作与战略时刻",
            )
        _resource_id(
            self.resolution_operation_id,
            f"{path}.resolution_operation_id",
        )
        _number(
            self.resolution_strategic_time_s,
            f"{path}.resolution_strategic_time_s",
        )
        if self.status == "recovered":
            if self.destination_ship_id is None:
                raise ContractError(
                    "crew_recovery.destination_missing",
                    f"{path}.destination_ship_id",
                    "获救清单必须记录接收舰",
                )
            _resource_id(self.destination_ship_id, f"{path}.destination_ship_id")
        elif self.destination_ship_id is not None:
            raise ContractError(
                "crew_recovery.destination_unexpected",
                f"{path}.destination_ship_id",
                "失踪结算不得记录接收舰",
            )

    @classmethod
    def parse(cls, value: Any, path: str = "$") -> "CrewRescueManifest":
        obj = _object(value, path)
        _keys(
            obj,
            path,
            (
                "interface",
                "manifest_id",
                "policy",
                "source_operation_id",
                "source_ship_id",
                "source_tactical_time_s",
                "status",
                "survivors",
            ),
            (
                "resolution_operation_id",
                "resolution_strategic_time_s",
                "destination_ship_id",
            ),
        )
        if obj["interface"] != CREW_RECOVERY_INTERFACE_ID:
            raise ContractError(
                "crew_recovery.interface",
                f"{path}.interface",
                obj["interface"],
            )
        if obj["policy"] != CREW_RECOVERY_POLICY_ID:
            raise ContractError(
                "crew_recovery.policy",
                f"{path}.policy",
                obj["policy"],
            )
        if not isinstance(obj["survivors"], list):
            raise ContractError(
                "crew_recovery.array",
                f"{path}.survivors",
                "必须是数组",
            )
        result = cls(
            obj["manifest_id"],
            obj["source_operation_id"],
            obj["source_ship_id"],
            obj["source_tactical_time_s"],
            tuple(
                CrewRescueCount.parse(item, f"{path}.survivors[{index}]")
                for index, item in enumerate(obj["survivors"])
            ),
            obj["status"],
            obj.get("resolution_operation_id"),
            obj.get("resolution_strategic_time_s"),
            obj.get("destination_ship_id"),
        )
        result.validate(path)
        return result

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        result: dict[str, Any] = {
            "interface": CREW_RECOVERY_INTERFACE_ID,
            "manifest_id": self.manifest_id,
            "policy": CREW_RECOVERY_POLICY_ID,
            "source_operation_id": self.source_operation_id,
            "source_ship_id": self.source_ship_id,
            "source_tactical_time_s": self.source_tactical_time_s,
            "status": self.status,
            "survivors": [
                item.to_dict()
                for item in sorted(self.survivors, key=lambda item: item.crew_type)
            ],
        }
        if self.resolution_operation_id is not None:
            result["resolution_operation_id"] = self.resolution_operation_id
            result["resolution_strategic_time_s"] = self.resolution_strategic_time_s
        if self.destination_ship_id is not None:
            result["destination_ship_id"] = self.destination_ship_id
        return result


@dataclass(frozen=True)
class CrewEvacuationEvent:
    operation_id: str
    rescue_manifest_id: str | None
    tactical_time_s: float
    ship_id: str
    crew_type: str
    evacuated_fit_for_duty_count: int
    evacuated_wounded_count: int
    newly_dead_fit_for_duty_count: int
    newly_dead_wounded_count: int

    def to_dict(self) -> dict[str, Any]:
        result = {
            "crew_type": self.crew_type,
            "evacuated_fit_for_duty_count": self.evacuated_fit_for_duty_count,
            "evacuated_wounded_count": self.evacuated_wounded_count,
            "newly_dead_fit_for_duty_count": self.newly_dead_fit_for_duty_count,
            "newly_dead_wounded_count": self.newly_dead_wounded_count,
            "operation_id": self.operation_id,
            "ship_id": self.ship_id,
            "tactical_time_s": self.tactical_time_s,
        }
        if self.rescue_manifest_id is not None:
            result["rescue_manifest_id"] = self.rescue_manifest_id
        return result


@dataclass(frozen=True)
class CrewEvacuationResolution:
    source_instance_sha256: str
    resulting_instance: ShipInstanceSnapshotInput
    events: tuple[CrewEvacuationEvent, ...]
    rescue_manifest: CrewRescueManifest | None


def apply_crew_evacuation_outcome(
    instance: ShipInstanceSnapshotInput,
    outcome: CrewEvacuationOutcome,
    *,
    ship_id: str,
    physical_status: str,
    target_tactical_time_s: float,
) -> CrewEvacuationResolution:
    """只允许坠落舰在固定步边界显式分配全部在舰活人的去向。"""

    source_sha256 = canonical_sha256(instance)
    outcome.validate("$.evacuation_outcome")
    _resource_id(ship_id, "$.ship_id")
    if outcome.ship_id != ship_id:
        raise ContractError(
            "crew_recovery.ship_mismatch",
            "$.evacuation_outcome.ship_id",
            outcome.ship_id,
        )
    target_time = _number(target_tactical_time_s, "$.target_tactical_time_s")
    if physical_status != "falling":
        raise ContractError(
            "crew_recovery.ship_not_falling",
            "$.physical_status",
            "只有失控坠落舰可进入弃舰结算",
        )
    if abs(outcome.tactical_time_s - target_time) > EPS:
        raise ContractError(
            "crew_recovery.evacuation_boundary_mismatch",
            "$.evacuation_outcome.tactical_time_s",
            "弃舰结果必须位于当前固定步边界",
        )
    state = _state_or_initialize(instance, tactical_time_s=target_time)
    if target_time + EPS < state.tactical_time_s:
        raise ContractError(
            "crew_recovery.tactical_time_reversed",
            "$.target_tactical_time_s",
            "弃舰结算不得倒退人员战术时钟",
        )
    counts = {item.crew_type: item for item in outcome.counts}
    statuses = {item.crew_type: item for item in state.crew_statuses}
    living_types = {
        item.crew_type
        for item in state.crew_statuses
        if item.fit_for_duty_count + item.wounded_count > 0
    }
    if set(counts) != living_types:
        raise ContractError(
            "crew_recovery.evacuation_partition_types",
            "$.evacuation_outcome.counts",
            f"弃舰结果必须覆盖且只覆盖全部在舰活人类别：{sorted(living_types)}",
        )
    events: list[CrewEvacuationEvent] = []
    survivors: list[CrewRescueCount] = []
    for crew_type in sorted(living_types):
        current = statuses[crew_type]
        item = counts[crew_type]
        if (
            item.evacuated_fit_for_duty_count
            + item.newly_dead_fit_for_duty_count
            != current.fit_for_duty_count
        ):
            raise ContractError(
                "crew_recovery.evacuation_fit_not_conserved",
                f"$.evacuation_outcome.counts.{crew_type}",
                "撤离的可执勤人员与新增死亡必须守恒",
            )
        if (
            item.evacuated_wounded_count + item.newly_dead_wounded_count
            != current.wounded_count
        ):
            raise ContractError(
                "crew_recovery.evacuation_wounded_not_conserved",
                f"$.evacuation_outcome.counts.{crew_type}",
                "撤离的负伤人员与新增死亡必须守恒",
            )
        statuses[crew_type] = CrewCasualtyStatusInput(
            crew_type,
            0,
            0,
            current.dead_count
            + item.newly_dead_fit_for_duty_count
            + item.newly_dead_wounded_count,
        )
        if item.evacuated_fit_for_duty_count + item.evacuated_wounded_count > 0:
            survivors.append(
                CrewRescueCount(
                    crew_type,
                    item.evacuated_fit_for_duty_count,
                    item.evacuated_wounded_count,
                )
            )
        events.append(
            CrewEvacuationEvent(
                outcome.operation_id,
                (
                    outcome.rescue_manifest_id
                    if item.evacuated_fit_for_duty_count
                    + item.evacuated_wounded_count
                    > 0
                    else None
                ),
                target_time,
                outcome.ship_id,
                crew_type,
                item.evacuated_fit_for_duty_count,
                item.evacuated_wounded_count,
                item.newly_dead_fit_for_duty_count,
                item.newly_dead_wounded_count,
            )
        )
    resulting = _write_statuses(
        instance,
        state,
        statuses,
        tactical_time_s=target_time,
    )
    manifest = (
        None
        if not survivors
        else CrewRescueManifest(
            outcome.rescue_manifest_id,
            outcome.operation_id,
            outcome.ship_id,
            target_time,
            tuple(survivors),
        )
    )
    if manifest is not None:
        manifest.validate("$.rescue_manifest")
    return CrewEvacuationResolution(
        source_sha256,
        resulting,
        tuple(events),
        manifest,
    )


@dataclass(frozen=True)
class CrewRescueDispositionOutcome:
    operation_id: str
    strategic_time_s: float
    disposition: str
    target_ship_id: str | None = None

    def validate(self, path: str = "$") -> None:
        _resource_id(self.operation_id, f"{path}.operation_id")
        _number(self.strategic_time_s, f"{path}.strategic_time_s")
        if (
            not isinstance(self.disposition, str)
            or self.disposition not in RESCUE_DISPOSITIONS
        ):
            raise ContractError(
                "crew_recovery.rescue_disposition",
                f"{path}.disposition",
                self.disposition,
            )
        if self.disposition == "recovered_to_ship":
            if self.target_ship_id is None:
                raise ContractError(
                    "crew_recovery.destination_missing",
                    f"{path}.target_ship_id",
                    "获救结果必须提供接收舰",
                )
            _resource_id(self.target_ship_id, f"{path}.target_ship_id")
        elif self.target_ship_id is not None:
            raise ContractError(
                "crew_recovery.destination_unexpected",
                f"{path}.target_ship_id",
                "失踪结果不得提供接收舰",
            )

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        result: dict[str, Any] = {
            "disposition": self.disposition,
            "operation_id": self.operation_id,
            "strategic_time_s": self.strategic_time_s,
        }
        if self.target_ship_id is not None:
            result["target_ship_id"] = self.target_ship_id
        return result


@dataclass(frozen=True)
class CrewRescueEvent:
    operation_id: str
    strategic_time_s: float
    manifest_id: str
    disposition: str
    survivor_count: int
    target_ship_id: str | None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "disposition": self.disposition,
            "manifest_id": self.manifest_id,
            "operation_id": self.operation_id,
            "strategic_time_s": self.strategic_time_s,
            "survivor_count": self.survivor_count,
        }
        if self.target_ship_id is not None:
            result["target_ship_id"] = self.target_ship_id
        return result


@dataclass(frozen=True)
class CrewRescueResolution:
    source_target_instance_sha256: str | None
    resulting_target_instance: ShipInstanceSnapshotInput | None
    resulting_manifest: CrewRescueManifest
    event: CrewRescueEvent


def resolve_crew_rescue_manifest(
    manifest: CrewRescueManifest,
    outcome: CrewRescueDispositionOutcome,
    *,
    target_instance: ShipInstanceSnapshotInput | None = None,
    target_snapshot: DerivedShipSnapshot | None = None,
) -> CrewRescueResolution:
    """把待救援清单显式结算为整批获救或失踪，不推导搜救概率。"""

    manifest.validate("$.manifest")
    outcome.validate("$.rescue_outcome")
    if manifest.status != "awaiting_recovery":
        raise ContractError(
            "crew_recovery.manifest_already_resolved",
            "$.manifest.status",
            manifest.status,
        )
    survivor_count = sum(
        item.fit_for_duty_count + item.wounded_count
        for item in manifest.survivors
    )
    source_target_sha256: str | None = None
    resulting_target: ShipInstanceSnapshotInput | None = None
    if outcome.disposition == "lost":
        if target_instance is not None or target_snapshot is not None:
            raise ContractError(
                "crew_recovery.target_unexpected",
                "$.target_instance",
                "失踪结算不得提供接收舰实例或快照",
            )
        status = "lost"
    else:
        if target_instance is None or target_snapshot is None:
            raise ContractError(
                "crew_recovery.target_required",
                "$.target_instance",
                "获救结算必须同时提供接收舰实例与派生快照",
            )
        assert outcome.target_ship_id is not None
        validate_instance_current_design(target_snapshot, target_instance)
        target_state = _state_or_initialize(target_instance)
        _strategic_time_guard(
            target_state,
            outcome.strategic_time_s,
            "$.rescue_outcome.strategic_time_s",
        )
        target_statuses = {
            item.crew_type: item for item in target_state.crew_statuses
        }
        for item in manifest.survivors:
            current = target_statuses.get(
                item.crew_type,
                CrewCasualtyStatusInput(item.crew_type, 0, 0, 0),
            )
            target_statuses[item.crew_type] = CrewCasualtyStatusInput(
                item.crew_type,
                current.fit_for_duty_count + item.fit_for_duty_count,
                current.wounded_count + item.wounded_count,
                current.dead_count,
            )
        resulting_target = _write_statuses(
            target_instance,
            target_state,
            target_statuses,
            strategic_time_s=outcome.strategic_time_s,
        )
        validate_crew_casualty_capacity(
            resulting_target,
            dict(target_snapshot.outfit.crew_capacity),
        )
        source_target_sha256 = canonical_sha256(target_instance)
        status = "recovered"
    resulting_manifest = replace(
        manifest,
        status=status,
        resolution_operation_id=outcome.operation_id,
        resolution_strategic_time_s=outcome.strategic_time_s,
        destination_ship_id=outcome.target_ship_id,
    )
    resulting_manifest.validate("$.resulting_manifest")
    event = CrewRescueEvent(
        outcome.operation_id,
        outcome.strategic_time_s,
        manifest.manifest_id,
        outcome.disposition,
        survivor_count,
        outcome.target_ship_id,
    )
    return CrewRescueResolution(
        source_target_sha256,
        resulting_target,
        resulting_manifest,
        event,
    )
