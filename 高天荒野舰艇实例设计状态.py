"""《高天荒野》阶段 I3：已建成舰艇的自持设计快照与确定性改装迁移。"""

from __future__ import annotations

from dataclasses import replace

from 高天荒野舰艇出航配置编译器 import (
    compile_ship_operational_state,
    validate_ship_ammunition_state,
)
from 高天荒野舰艇数据契约 import (
    ContractError,
    MagazineAmmunitionStateInput,
    OutfitPlanInput,
    ResourceReference,
    RuntimeModuleStateInput,
    ShipAmmunitionStateInput,
    ShipDesignStateInput,
    ShipInstanceSnapshotInput,
    WeaponReadyAmmunitionStateInput,
    WeaponTimelineClockInput,
    canonical_sha256,
)
from 高天荒野舰艇无界面舾装编译器 import DerivedShipSnapshot


SHIP_DESIGN_STATE_INTERFACE_ID = "gaotian.ship-instance-design-state/v1alpha1"
EPS = 1.0e-8


def _snapshot_outfit_reference(snapshot: DerivedShipSnapshot) -> ResourceReference:
    plan = snapshot.outfit.normalized_plan
    return ResourceReference(plan.id, plan.version)


def current_design_sha256(instance: ShipInstanceSnapshotInput) -> str:
    """兼容旧实例地取得此刻生效的派生设计哈希。"""

    if instance.design_state is None:
        return instance.derived_ship_snapshot_sha256
    return instance.design_state.current_derived_ship_snapshot_sha256


def validate_instance_current_design(
    snapshot: DerivedShipSnapshot,
    instance: ShipInstanceSnapshotInput,
) -> None:
    """验证传入快照确实是实例当前设计；旧存档保持原校验语义。"""

    state = instance.design_state
    if state is None:
        expected = _snapshot_outfit_reference(snapshot)
        if instance.outfit_plan != expected:
            raise ContractError(
                "runtime.outfit_reference_mismatch",
                "$.outfit_plan",
                f"实例绑定 {instance.outfit_plan}，设计态来自 {expected}",
            )
        if instance.derived_ship_snapshot_sha256 != snapshot.source_sha256:
            raise ContractError(
                "runtime.derived_snapshot_hash_mismatch",
                "$.derived_ship_snapshot_sha256",
                "实例引用的设计态派生快照已经变化",
            )
        return

    if state.construction_hull_blueprint_sha256 != snapshot.hull.source_sha256:
        raise ContractError(
            "runtime.construction_hull_snapshot_mismatch",
            "$.design_state.construction_hull_blueprint_sha256",
            "当前设计没有使用该舰建造时冻结的精确船壳快照",
        )
    expected_outfit = _snapshot_outfit_reference(snapshot)
    current_plan = state.current_outfit_plan
    current_reference = ResourceReference(current_plan.id, current_plan.version)
    if current_reference != expected_outfit:
        raise ContractError(
            "runtime.current_outfit_reference_mismatch",
            "$.design_state.current_outfit_plan",
            f"实例当前舾装为 {current_reference}，设计态来自 {expected_outfit}",
        )
    if state.current_outfit_plan_sha256 != snapshot.outfit.source_sha256:
        raise ContractError(
            "runtime.current_outfit_hash_mismatch",
            "$.design_state.current_outfit_plan_sha256",
            "实例内嵌的当前舾装快照与编译输入不一致",
        )
    if state.current_derived_ship_snapshot_sha256 != snapshot.source_sha256:
        raise ContractError(
            "runtime.current_derived_snapshot_hash_mismatch",
            "$.design_state.current_derived_ship_snapshot_sha256",
            "实例当前派生设计快照已经变化",
        )


def embed_initial_design_state(
    snapshot: DerivedShipSnapshot,
    instance: ShipInstanceSnapshotInput,
) -> ShipInstanceSnapshotInput:
    """为旧实例或新造舰嵌入完整船壳和当前舾装；不改写历史来源字段。"""

    if instance.design_state is not None:
        validate_instance_current_design(snapshot, instance)
        return instance
    validate_instance_current_design(snapshot, instance)
    state = ShipDesignStateInput(
        construction_hull_blueprint=snapshot.hull.normalized_blueprint,
        construction_hull_blueprint_sha256=snapshot.hull.source_sha256,
        current_outfit_plan=snapshot.outfit.normalized_plan,
        current_outfit_plan_sha256=snapshot.outfit.source_sha256,
        current_derived_ship_snapshot_sha256=snapshot.source_sha256,
        revision=1,
    )
    return replace(instance, design_state=state)


def reconstruct_hull_blueprint_from_ship(
    instance: ShipInstanceSnapshotInput,
):
    if instance.design_state is None:
        raise ContractError(
            "instance.embedded_design_state_missing",
            "$.design_state",
            "旧实例尚未嵌入建造时船壳快照",
        )
    return instance.design_state.construction_hull_blueprint


def reconstruct_current_outfit_plan_from_ship(
    instance: ShipInstanceSnapshotInput,
) -> OutfitPlanInput:
    if instance.design_state is None:
        raise ContractError(
            "instance.embedded_design_state_missing",
            "$.design_state",
            "旧实例尚未嵌入当前舾装快照",
        )
    return instance.design_state.current_outfit_plan


def _module_sets(snapshot: DerivedShipSnapshot) -> dict[str, object]:
    return {item.id: item for item in snapshot.outfit.instances}


def _unchanged_module_ids(
    source_snapshot: DerivedShipSnapshot,
    target_snapshot: DerivedShipSnapshot,
) -> set[str]:
    source = _module_sets(source_snapshot)
    target = _module_sets(target_snapshot)
    return {
        instance_id
        for instance_id in source.keys() & target.keys()
        if source[instance_id].to_dict() == target[instance_id].to_dict()
    }


def _transition_ammunition_state(
    source_snapshot: DerivedShipSnapshot,
    target_snapshot: DerivedShipSnapshot,
    source_state: ShipAmmunitionStateInput | None,
    unchanged_ids: set[str],
) -> ShipAmmunitionStateInput | None:
    if source_state is None:
        return None
    validate_ship_ammunition_state(
        source_snapshot,
        source_state,
        namespace="refit_source",
        path_prefix="$.ammunition_state",
    )
    source_magazines = {item.instance_id: item for item in source_state.magazines}
    source_weapons = {item.instance_id: item for item in source_state.weapons}
    for instance_id, state in source_magazines.items():
        if instance_id not in unchanged_ids and state.inventory:
            raise ContractError(
                "refit.ammunition_must_be_unloaded",
                f"$.ammunition_state.magazines.{instance_id}",
                "拆卸或更换弹药库前必须先卸空其中弹药",
            )
    for instance_id, state in source_weapons.items():
        if instance_id not in unchanged_ids and state.ready_rounds > 0:
            raise ContractError(
                "refit.ammunition_must_be_unloaded",
                f"$.ammunition_state.weapons.{instance_id}",
                "拆卸或更换武器前必须先清空待发弹",
            )

    target_magazines = tuple(
        source_magazines[item.id]
        if item.id in unchanged_ids and item.id in source_magazines
        else MagazineAmmunitionStateInput(item.id, ())
        for item in target_snapshot.outfit.instances
        if item.prototype.category == "ammunition_magazine"
    )
    target_weapons = tuple(
        source_weapons[item.id]
        if item.id in unchanged_ids and item.id in source_weapons
        else WeaponReadyAmmunitionStateInput(item.id, None, 0)
        for item in target_snapshot.outfit.instances
        if item.prototype.category == "weapon"
    )
    result = ShipAmmunitionStateInput(target_magazines, target_weapons)
    validate_ship_ammunition_state(
        target_snapshot,
        result,
        namespace="refit_target",
        path_prefix="$.ammunition_state",
    )
    return result


def _transition_weapon_timeline_state(
    source_snapshot: DerivedShipSnapshot,
    target_snapshot: DerivedShipSnapshot,
    source_instance: ShipInstanceSnapshotInput,
    unchanged_ids: set[str],
):
    state = source_instance.weapon_timeline_state
    if state is None:
        return None
    source_weapons = {
        item.id
        for item in source_snapshot.outfit.instances
        if item.prototype.category == "weapon"
    }
    target_weapons = {
        item.id
        for item in target_snapshot.outfit.instances
        if item.prototype.category == "weapon"
    }
    affected_active = sorted(
        item.id
        for item in state.sequences
        if item.weapon_instance_id not in unchanged_ids
    )
    if affected_active:
        raise ContractError(
            "refit.weapon_sequence_must_be_cancelled",
            "$.weapon_timeline_state.sequences",
            f"改装涉及的武器仍有活动序列，必须先取消：{affected_active}",
        )
    clocks = {item.instance_id: item for item in state.clocks}
    if set(clocks) != source_weapons:
        raise ContractError(
            "refit.weapon_timeline_clock_set_mismatch",
            "$.weapon_timeline_state.clocks",
            "来源武器时钟与来源舾装不一致",
        )
    target_clocks = tuple(
        clocks[instance_id]
        if instance_id in source_weapons and instance_id in unchanged_ids
        else WeaponTimelineClockInput(instance_id, state.tactical_time_s)
        for instance_id in sorted(target_weapons)
    )
    return replace(
        state,
        clocks=target_clocks,
        sequences=tuple(
            item
            for item in state.sequences
            if item.weapon_instance_id in target_weapons
        ),
    )


def transition_current_design(
    source_snapshot: DerivedShipSnapshot,
    source_instance: ShipInstanceSnapshotInput,
    target_snapshot: DerivedShipSnapshot,
) -> ShipInstanceSnapshotInput:
    """在固定建造船壳上原子迁移模块、弹药状态与当前舾装快照。"""

    validate_instance_current_design(source_snapshot, source_instance)
    continuous_damage = source_instance.continuous_damage_state
    if continuous_damage is not None and continuous_damage.fire_incidents:
        raise ContractError(
            "refit.active_fire",
            "$.continuous_damage_state.fire_incidents",
            "存在活动火灾时不能开始改装迁移",
        )
    if source_snapshot.hull.source_sha256 != target_snapshot.hull.source_sha256:
        raise ContractError(
            "refit.hull_snapshot_mismatch",
            "$.target_design",
            "改装不得改变已建成舰艇冻结的精确船壳几何",
        )
    embedded = embed_initial_design_state(source_snapshot, source_instance)
    unchanged_ids = _unchanged_module_ids(source_snapshot, target_snapshot)
    source_states = {item.instance_id: item for item in embedded.module_states}
    source_modules = _module_sets(source_snapshot)
    if set(source_states) != set(source_modules):
        raise ContractError(
            "refit.module_state_set_mismatch",
            "$.module_states",
            "来源实例模块状态与来源设计不一致",
        )
    target_states = tuple(
        source_states[item.id]
        if item.id in unchanged_ids
        else RuntimeModuleStateInput(
            item.id,
            item.prototype.durability_points,
            item.prototype.default_operating_mode,
        )
        for item in target_snapshot.outfit.instances
    )
    target_ammunition = _transition_ammunition_state(
        source_snapshot,
        target_snapshot,
        embedded.ammunition_state,
        unchanged_ids,
    )
    target_weapon_timeline = _transition_weapon_timeline_state(
        source_snapshot,
        target_snapshot,
        embedded,
        unchanged_ids,
    )
    compile_ship_operational_state(target_snapshot, embedded.operational_state)
    old_state = embedded.design_state
    assert old_state is not None
    new_state = replace(
        old_state,
        current_outfit_plan=target_snapshot.outfit.normalized_plan,
        current_outfit_plan_sha256=target_snapshot.outfit.source_sha256,
        current_derived_ship_snapshot_sha256=target_snapshot.source_sha256,
        revision=old_state.revision + 1,
    )
    result = replace(
        embedded,
        module_states=target_states,
        ammunition_state=target_ammunition,
        weapon_timeline_state=target_weapon_timeline,
        design_state=new_state,
    )
    validate_instance_current_design(target_snapshot, result)
    return result
