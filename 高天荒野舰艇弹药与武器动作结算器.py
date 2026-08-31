"""《高天荒野》阶段 I：舰艇当前弹药、待发弹、开火与装填最小闭环。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from math import isfinite
from typing import Any

from 高天荒野舰艇出航配置编译器 import (
    CompiledSortieState,
    validate_ship_ammunition_state,
)
from 高天荒野舰艇数据契约 import (
    AmmunitionInventoryEntryInput,
    ContractError,
    MagazineAmmunitionStateInput,
    ShipAmmunitionStateInput,
    ShipInstanceSnapshotInput,
    WeaponReadyAmmunitionStateInput,
    canonical_sha256,
)
from 高天荒野舰艇无界面舾装编译器 import (
    CompiledModuleInstance,
    DerivedShipSnapshot,
)
from 高天荒野舰艇运行时参数编译器 import (
    RUNTIME_CACHE_VALIDATION_STRICT,
    RuntimeShipParameters,
    RuntimeShipParametersCache,
    compile_runtime_ship_parameters,
)


AMMUNITION_ACTION_INTERFACE_ID = "gaotian.ammunition-actions/v1alpha1"
AMMUNITION_POOL_POLICY_ID = "gaotian.ammunition-pool/physical-magazines-ship-shared-feed/v1"
READY_ROUND_ACCOUNTING_POLICY_ID = "gaotian.ready-round/one-inventory-unit-per-round/v1"
WEAPON_ACTION_WAKE_EVENT = "ship.weapon_fire_requested"
FIRE_CONTROL_WAKE_EVENT = "ship.fire_control_required"
EPS = 1.0e-8


@dataclass(frozen=True)
class WeaponFireRequest:
    id: str
    weapon_instance_id: str
    munition_id: str
    rounds: int
    target_domain: str
    target_distance_m: float
    fire_control_instance_id: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "fire_control_instance_id": self.fire_control_instance_id,
            "id": self.id,
            "munition_id": self.munition_id,
            "rounds": self.rounds,
            "target_distance_m": self.target_distance_m,
            "target_domain": self.target_domain,
            "weapon_instance_id": self.weapon_instance_id,
        }


@dataclass(frozen=True)
class WeaponReloadRequest:
    id: str
    weapon_instance_id: str
    munition_id: str
    rounds: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "munition_id": self.munition_id,
            "rounds": self.rounds,
            "weapon_instance_id": self.weapon_instance_id,
        }


@dataclass(frozen=True)
class MagazineWithdrawal:
    magazine_instance_id: str
    munition_id: str
    units: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "magazine_instance_id": self.magazine_instance_id,
            "munition_id": self.munition_id,
            "units": self.units,
        }


@dataclass(frozen=True)
class WeaponActionResolution:
    action_id: str
    action_kind: str
    weapon_instance_id: str
    munition_id: str
    rounds: int
    fire_control_instance_id: str | None
    active_automatic_events: tuple[str, ...]
    function_efficiencies: tuple[tuple[str, float], ...]
    magazine_withdrawals: tuple[MagazineWithdrawal, ...]
    source_instance_sha256: str
    resulting_instance: ShipInstanceSnapshotInput
    runtime: RuntimeShipParameters

    @property
    def resulting_instance_sha256(self) -> str:
        return canonical_sha256(self.resulting_instance)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": {
                "fire_control_instance_id": self.fire_control_instance_id,
                "id": self.action_id,
                "kind": self.action_kind,
                "munition_id": self.munition_id,
                "rounds": self.rounds,
                "weapon_instance_id": self.weapon_instance_id,
            },
            "active_automatic_events": list(self.active_automatic_events),
            "function_efficiencies": dict(self.function_efficiencies),
            "interface": AMMUNITION_ACTION_INTERFACE_ID,
            "magazine_withdrawals": [
                item.to_dict() for item in self.magazine_withdrawals
            ],
            "policies": {
                "ammunition_pool": AMMUNITION_POOL_POLICY_ID,
                "ready_round_accounting": READY_ROUND_ACCOUNTING_POLICY_ID,
            },
            "resulting_ammunition_state": (
                None
                if self.resulting_instance.ammunition_state is None
                else self.resulting_instance.ammunition_state.to_dict()
            ),
            "resulting_instance_sha256": self.resulting_instance_sha256,
            "source_instance_sha256": self.source_instance_sha256,
        }


def _require_positive_rounds(rounds: int, path: str) -> None:
    if isinstance(rounds, bool) or not isinstance(rounds, int) or rounds < 1:
        raise ContractError("weapon_action.rounds", path, "发数必须是正整数")


def _modules(snapshot: DerivedShipSnapshot) -> dict[str, CompiledModuleInstance]:
    return {item.id: item for item in snapshot.outfit.instances}


def _require_module(
    snapshot: DerivedShipSnapshot,
    instance_id: str,
    category: str,
    path: str,
) -> CompiledModuleInstance:
    module = _modules(snapshot).get(instance_id)
    if module is None or module.prototype.category != category:
        raise ContractError(
            f"weapon_action.{category}_instance_invalid",
            path,
            f"必须指定本舰舾装中的 {category} 模块实例",
        )
    return module


def _require_ammunition_state(
    snapshot: DerivedShipSnapshot,
    instance: ShipInstanceSnapshotInput,
) -> ShipAmmunitionStateInput:
    state = instance.ammunition_state
    if state is None:
        raise ContractError(
            "weapon_action.ammunition_state_missing",
            "$.ammunition_state",
            "旧版无弹药状态实例不能开火或装填；需先建立完整弹药初态",
        )
    validate_ship_ammunition_state(
        snapshot,
        state,
        namespace="weapon_action",
        path_prefix="$.ammunition_state",
    )
    return state


def _require_ship_control(runtime: RuntimeShipParameters) -> None:
    if not runtime.cic_control_available:
        raise ContractError(
            "weapon_action.cic_control_unavailable",
            "$.module_states",
            "CIC 当前无法提供基本控制，不能执行武器动作",
        )


def _weapon_state(
    state: ShipAmmunitionStateInput, instance_id: str
) -> WeaponReadyAmmunitionStateInput:
    return next(item for item in state.weapons if item.instance_id == instance_id)


def _replace_weapon_state(
    state: ShipAmmunitionStateInput,
    updated: WeaponReadyAmmunitionStateInput,
) -> ShipAmmunitionStateInput:
    return replace(
        state,
        weapons=tuple(
            updated if item.instance_id == updated.instance_id else item
            for item in state.weapons
        ),
    )


def _replace_magazine_states(
    state: ShipAmmunitionStateInput,
    updated: dict[str, MagazineAmmunitionStateInput],
) -> ShipAmmunitionStateInput:
    return replace(
        state,
        magazines=tuple(updated.get(item.instance_id, item) for item in state.magazines),
    )


def _require_weapon_compatibility(
    weapon: CompiledModuleInstance,
    munition_id: str,
    *,
    target_domain: str | None = None,
    target_distance_m: float | None = None,
) -> dict[str, Any]:
    capability = weapon.prototype.capability.to_dict()
    if munition_id not in set(capability["compatible_munition_ids"]):
        raise ContractError(
            "weapon_action.munition_incompatible",
            "$.munition_id",
            f"武器 {weapon.id} 不兼容 {munition_id}",
        )
    if target_domain is not None and target_domain not in set(
        capability["engagement_domains"]
    ):
        raise ContractError(
            "weapon_action.target_domain_unsupported",
            "$.target_domain",
            f"武器 {weapon.id} 不能攻击 {target_domain}",
        )
    if target_distance_m is not None:
        if (
            isinstance(target_distance_m, bool)
            or not isinstance(target_distance_m, (int, float))
            or not isfinite(float(target_distance_m))
            or target_distance_m < 0.0
        ):
            raise ContractError(
                "weapon_action.target_distance",
                "$.target_distance_m",
                "目标距离必须是非负有限数",
            )
        if not (
            float(capability["minimum_range_m"]) - EPS
            <= target_distance_m
            <= float(capability["maximum_range_m"]) + EPS
        ):
            raise ContractError(
                "weapon_action.target_out_of_range",
                "$.target_distance_m",
                f"目标距离 {target_distance_m}m 超出武器射程",
            )
    return capability


def _require_fire_control(
    snapshot: DerivedShipSnapshot,
    runtime: RuntimeShipParameters,
    requirement: str,
    instance_id: str | None,
    target_distance_m: float,
) -> tuple[str | None, tuple[tuple[str, float], ...]]:
    if requirement == "none":
        return None, ()
    if instance_id is None:
        raise ContractError(
            "weapon_action.fire_control_required",
            "$.fire_control_instance_id",
            f"该武器需要 {requirement} 火控能力",
        )
    module = _require_module(
        snapshot, instance_id, "fire_control", "$.fire_control_instance_id"
    )
    capability = module.prototype.capability.to_dict()
    if requirement not in set(capability["supported_requirements"]):
        raise ContractError(
            "weapon_action.fire_control_requirement_unsupported",
            "$.fire_control_instance_id",
            f"火控 {instance_id} 不支持 {requirement}",
        )
    if target_distance_m > float(capability["maximum_lock_range_m"]) + EPS:
        raise ContractError(
            "weapon_action.fire_control_target_out_of_range",
            "$.target_distance_m",
            f"目标距离 {target_distance_m}m 超出火控锁定距离",
        )
    function_id = (
        "fire_control.solution"
        if requirement == "solution"
        else "fire_control.guidance"
    )
    efficiency = runtime.module(instance_id).function_efficiency(function_id)
    if efficiency <= EPS:
        raise ContractError(
            "weapon_action.fire_control_unavailable",
            "$.fire_control_instance_id",
            f"火控子功能 {function_id} 当前不可用",
        )
    return instance_id, ((function_id, efficiency),)


def resolve_weapon_fire(
    snapshot: DerivedShipSnapshot,
    sortie: CompiledSortieState,
    instance: ShipInstanceSnapshotInput,
    request: WeaponFireRequest,
    *,
    runtime_cache: RuntimeShipParametersCache | None = None,
    runtime_validation_mode: str = RUNTIME_CACHE_VALIDATION_STRICT,
) -> WeaponActionResolution:
    """以当前待发弹完成一次原子开火；不在此层计算弹道和命中。"""

    _require_positive_rounds(request.rounds, "$.rounds")
    state = _require_ammunition_state(snapshot, instance)
    weapon = _require_module(
        snapshot, request.weapon_instance_id, "weapon", "$.weapon_instance_id"
    )
    capability = _require_weapon_compatibility(
        weapon,
        request.munition_id,
        target_domain=request.target_domain,
        target_distance_m=request.target_distance_m,
    )
    events = [WEAPON_ACTION_WAKE_EVENT]
    if capability["fire_control_requirement"] != "none":
        events.append(FIRE_CONTROL_WAKE_EVENT)
    normalized_events = tuple(sorted(events))
    runtime = (
        compile_runtime_ship_parameters(
            snapshot,
            sortie,
            instance,
            active_automatic_events=normalized_events,
        )
        if runtime_cache is None
        else runtime_cache.resolve(
            snapshot,
            sortie,
            instance,
            active_automatic_events=normalized_events,
            validation_mode=runtime_validation_mode,
        ).runtime
    )
    _require_ship_control(runtime)
    weapon_runtime = runtime.module(request.weapon_instance_id)
    aim_efficiency = weapon_runtime.function_efficiency("weapon.aim")
    fire_efficiency = weapon_runtime.function_efficiency("weapon.fire")
    if aim_efficiency <= EPS or fire_efficiency <= EPS:
        raise ContractError(
            "weapon_action.weapon_fire_unavailable",
            "$.weapon_instance_id",
            "武器的瞄准或击发子功能当前不可用",
        )
    fire_control_id, fire_control_efficiencies = _require_fire_control(
        snapshot,
        runtime,
        str(capability["fire_control_requirement"]),
        request.fire_control_instance_id,
        request.target_distance_m,
    )
    ready = _weapon_state(state, request.weapon_instance_id)
    if ready.munition_id != request.munition_id or ready.ready_rounds < request.rounds:
        raise ContractError(
            "weapon_action.ready_rounds_insufficient",
            "$.munition_id",
            f"当前待发弹为 {ready.munition_id} × {ready.ready_rounds}",
        )
    remaining = ready.ready_rounds - request.rounds
    updated_ready = replace(
        ready,
        munition_id=request.munition_id if remaining else None,
        ready_rounds=remaining,
    )
    updated_state = _replace_weapon_state(state, updated_ready)
    updated_instance = replace(instance, ammunition_state=updated_state)
    efficiencies = (
        ("weapon.aim", aim_efficiency),
        ("weapon.fire", fire_efficiency),
    ) + fire_control_efficiencies
    return WeaponActionResolution(
        request.id,
        "fire",
        request.weapon_instance_id,
        request.munition_id,
        request.rounds,
        fire_control_id,
        normalized_events,
        tuple(sorted(efficiencies)),
        (),
        canonical_sha256(instance),
        updated_instance,
        runtime,
    )


def resolve_weapon_reload(
    snapshot: DerivedShipSnapshot,
    sortie: CompiledSortieState,
    instance: ShipInstanceSnapshotInput,
    request: WeaponReloadRequest,
    *,
    runtime_cache: RuntimeShipParametersCache | None = None,
    runtime_validation_mode: str = RUNTIME_CACHE_VALIDATION_STRICT,
) -> WeaponActionResolution:
    """从全舰可用弹药库确定性取弹，原子填入武器待发位置。"""

    _require_positive_rounds(request.rounds, "$.rounds")
    state = _require_ammunition_state(snapshot, instance)
    weapon = _require_module(
        snapshot, request.weapon_instance_id, "weapon", "$.weapon_instance_id"
    )
    capability = _require_weapon_compatibility(weapon, request.munition_id)
    runtime = (
        compile_runtime_ship_parameters(
            snapshot,
            sortie,
            instance,
            active_automatic_events=(WEAPON_ACTION_WAKE_EVENT,),
        )
        if runtime_cache is None
        else runtime_cache.resolve(
            snapshot,
            sortie,
            instance,
            active_automatic_events=(WEAPON_ACTION_WAKE_EVENT,),
            validation_mode=runtime_validation_mode,
        ).runtime
    )
    _require_ship_control(runtime)
    reload_efficiency = runtime.module(request.weapon_instance_id).function_efficiency(
        "weapon.reload"
    )
    if reload_efficiency <= EPS:
        raise ContractError(
            "weapon_action.weapon_reload_unavailable",
            "$.weapon_instance_id",
            "武器装填子功能当前不可用",
        )
    ready = _weapon_state(state, request.weapon_instance_id)
    if ready.munition_id not in {None, request.munition_id}:
        raise ContractError(
            "weapon_action.ready_munition_conflict",
            "$.munition_id",
            f"待发位置仍装有 {ready.munition_id}",
        )
    ready_capacity = int(capability["ready_round_capacity"])
    if ready.ready_rounds + request.rounds > ready_capacity:
        raise ContractError(
            "weapon_action.ready_capacity_exceeded",
            "$.rounds",
            f"待发容量 {ready_capacity}，当前已有 {ready.ready_rounds} 发",
        )

    remaining = request.rounds
    updated_magazines: dict[str, MagazineAmmunitionStateInput] = {}
    withdrawals: list[MagazineWithdrawal] = []
    feed_efficiencies: list[tuple[str, float]] = []
    for magazine_state in state.magazines:
        if remaining == 0:
            break
        magazine = _require_module(
            snapshot,
            magazine_state.instance_id,
            "ammunition_magazine",
            f"$.ammunition_state.magazines.{magazine_state.instance_id}",
        )
        magazine_capability = magazine.prototype.capability.to_dict()
        if request.munition_id not in set(
            magazine_capability["compatible_munition_ids"]
        ):
            continue
        magazine_runtime = runtime.module(magazine_state.instance_id)
        feed = magazine_runtime.function_efficiency("ammunition.feed")
        inventory_access = magazine_runtime.function_efficiency(
            "ammunition.inventory"
        )
        if feed <= EPS or inventory_access <= EPS:
            continue
        current_inventory = {
            item.munition_id: item.units for item in magazine_state.inventory
        }
        available = current_inventory.get(request.munition_id, 0)
        taken = min(available, remaining)
        if taken == 0:
            continue
        current_inventory[request.munition_id] -= taken
        updated_magazines[magazine_state.instance_id] = replace(
            magazine_state,
            inventory=tuple(
                AmmunitionInventoryEntryInput(munition_id, units)
                for munition_id, units in sorted(current_inventory.items())
                if units > 0
            ),
        )
        withdrawals.append(
            MagazineWithdrawal(
                magazine_state.instance_id, request.munition_id, taken
            )
        )
        feed_efficiencies.extend(
            (
                (f"{magazine_state.instance_id}.ammunition.feed", feed),
                (
                    f"{magazine_state.instance_id}.ammunition.inventory",
                    inventory_access,
                ),
            )
        )
        remaining -= taken
    if remaining:
        raise ContractError(
            "weapon_action.shared_ammunition_insufficient",
            "$.munition_id",
            f"可用弹药库还缺少 {remaining} 单位 {request.munition_id}",
        )

    state_with_magazines = _replace_magazine_states(state, updated_magazines)
    updated_ready = replace(
        ready,
        munition_id=request.munition_id,
        ready_rounds=ready.ready_rounds + request.rounds,
    )
    updated_state = _replace_weapon_state(state_with_magazines, updated_ready)
    updated_instance = replace(instance, ammunition_state=updated_state)
    return WeaponActionResolution(
        request.id,
        "reload",
        request.weapon_instance_id,
        request.munition_id,
        request.rounds,
        None,
        (WEAPON_ACTION_WAKE_EVENT,),
        tuple(sorted((("weapon.reload", reload_efficiency), *feed_efficiencies))),
        tuple(withdrawals),
        canonical_sha256(instance),
        updated_instance,
        runtime,
    )
