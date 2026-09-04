"""d3.3 第三阶段：具名 v6 初态、受控存档与单步结果资源门。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable

from 高天荒野舰艇数据契约 import ContractError, canonical_sha256
from 高天荒野舰艇持续毁伤 import continuous_damage_automatic_events
from 高天荒野舰艇推进固定步接线 import ActualScenePropulsionContext
from 高天荒野舰艇推进通道合同 import exact_object
from 高天荒野舰艇实际推进场景 import build_known_actual_scene
from 高天荒野舰艇受控推进场景合同 import (
    GovernedSceneSave,
    validate_governed_scene_step_contract,
)
from 高天荒野舰艇受控推进场景版本 import (
    GOVERNED_SCENE_INTERFACE_ID,
    GovernedPropulsionExecutionPolicy,
)
from 高天荒野舰艇受控推进无场景适配器 import (
    initialize_governed_propulsion_state,
)
from 高天荒野舰艇推进场景构建器 import DirectionalSceneResourceBundle
from 高天荒野舰艇推进安全判定器 import PropulsionSafetyProfile
from 高天荒野舰艇运行时参数编译器 import RUNTIME_CACHE_VALIDATION_STRICT
from 高天荒野舰艇统一战术场景 import (
    TacticalSceneShipBinding,
    TacticalSceneState,
    TacticalSceneStepResolution,
    prepare_tactical_scene_bindings,
    validate_governed_scene_context,
)
from 高天荒野舰艇战术机动求解器 import build_tactical_ship_model


@dataclass(frozen=True)
class GovernedSceneSession:
    resource_bundle: DirectionalSceneResourceBundle
    scene: TacticalSceneState
    bindings: tuple[TacticalSceneShipBinding, ...]
    propulsion_context: ActualScenePropulsionContext


def build_known_governed_scene(
    root: str | Path,
    scene_id: str,
    source_scene: TacticalSceneState,
    source_bindings: Iterable[TacticalSceneShipBinding],
    safety_profile: PropulsionSafetyProfile,
) -> GovernedSceneSession:
    """只从指纹锁定的十二个 T0 初态建立 governor 已提交的 v6 边界 0。"""
    actual = build_known_actual_scene(
        root,
        scene_id,
        source_scene,
        source_bindings,
        safety_profile,
    )
    if actual.scene.fixed_step_index != 0:
        raise ContractError(
            "governed_scene.initial_step",
            "$.fixed_step_index",
            "受控初态只能由具名边界 0 建立",
        )
    binding_by_id = {binding.ship_id: binding for binding in actual.bindings}
    ships = []
    for ship in actual.scene.ships:
        if ship.lifecycle_state.physical_status == "exited":
            raise ContractError(
                "governed_scene.initial_exited",
                f"$.ships.{ship.ship_id}.lifecycle_state",
                "具名初态不得包含已退出舰",
            )
        binding = binding_by_id[ship.ship_id]
        automatic_events = tuple(
            sorted(
                set(binding.active_automatic_events)
                | set(continuous_damage_automatic_events(ship.combat_state.instance))
            )
        )
        runtime = binding.runtime_cache.resolve(
            binding.snapshot,
            binding.sortie,
            ship.combat_state.instance,
            active_automatic_events=automatic_events,
            validation_mode=RUNTIME_CACHE_VALIDATION_STRICT,
        ).runtime
        model = build_tactical_ship_model(runtime, binding.snapshot)
        initialized = initialize_governed_propulsion_state(
            actual.propulsion_context.ship(ship.ship_id).aggregation_context,
            ship.propulsion_state,
            ship.propulsion_control,
            actual.propulsion_context.safety_profile,
            model,
            ship.motion_state,
            crew_safety_lock_enabled=runtime.crew_safety_lock_enabled,
        )
        ships.append(replace(ship, propulsion_state=initialized.state))
    scene = replace(
        actual.scene,
        ships=tuple(ships),
        propulsion_governance=GovernedPropulsionExecutionPolicy(),
    )
    scene = TacticalSceneState.parse(scene.to_dict())
    validate_governed_scene_context(scene, actual.propulsion_context)
    bindings = prepare_tactical_scene_bindings(scene, actual.bindings)
    return GovernedSceneSession(
        actual.resource_bundle,
        scene,
        bindings,
        actual.propulsion_context,
    )


def save_governed_scene(
    scene: TacticalSceneState,
    context: ActualScenePropulsionContext,
) -> dict[str, Any]:
    """保存完整已提交的 v6 状态；不序列化 runtime、模型或缓存。"""
    validate_governed_scene_context(scene, context)
    parsed = TacticalSceneState.parse(scene.to_dict())
    return GovernedSceneSave(parsed.to_dict()).to_dict()


def load_governed_scene_save(
    value: Any,
    *,
    root: str | Path,
    scene_id: str,
    source_scene: TacticalSceneState,
    source_bindings: Iterable[TacticalSceneShipBinding],
    safety_profile: PropulsionSafetyProfile,
) -> GovernedSceneSession:
    """从具名初态重建全部资源和缓存，再加载严格 v6 当前状态。"""
    saved = GovernedSceneSave.parse(value)
    scene = TacticalSceneState.parse(saved.scene)
    if scene.to_dict()["interface"] != GOVERNED_SCENE_INTERFACE_ID:
        raise ContractError(
            "governed_scene.save_version",
            "$.scene.interface",
            "只接受受控 v6 存档",
        )
    rebuilt = build_known_governed_scene(
        root,
        scene_id,
        source_scene,
        source_bindings,
        safety_profile,
    )
    validate_governed_scene_context(scene, rebuilt.propulsion_context)
    bindings = prepare_tactical_scene_bindings(scene, rebuilt.bindings)
    return replace(rebuilt, scene=scene, bindings=bindings)


def validate_governed_scene_step_payload(
    value: Any,
    source_scene: TacticalSceneState,
    resolution: TacticalSceneStepResolution,
    context: ActualScenePropulsionContext,
) -> None:
    """核对完整基础结果、v5 审计合同和首尾受控资源血缘。"""
    expected = resolution.to_dict()
    exact_object(value, set(expected), "$")
    validate_governed_scene_step_contract(value)
    validate_governed_scene_context(source_scene, context)
    validate_governed_scene_context(resolution.resulting_scene, context)
    if (
        resolution.source_scene_sha256 != canonical_sha256(source_scene)
        or resolution.resulting_scene.fixed_step_index
        != source_scene.fixed_step_index + 1
    ):
        raise ContractError(
            "governed_scene.result_source",
            "$",
            "结果源场景或步号不匹配",
        )
    source_ships = {ship.ship_id: ship for ship in source_scene.ships}
    resulting_ships = {
        ship.ship_id: ship for ship in resolution.resulting_scene.ships
    }
    for opening in resolution.propulsion_opening_records:
        source_ship = source_ships[opening.ship_id]
        resulting_ship = resulting_ships[opening.ship_id]
        if (
            opening.source_propulsion_state_sha256
            != canonical_sha256(source_ship.propulsion_state)
            or opening.source_control != source_ship.propulsion_control
            or opening.resulting_control != resulting_ship.propulsion_control
        ):
            raise ContractError(
                "governed_scene.opening_scene_chain",
                f"$.propulsion_opening_records.{opening.ship_id}",
                "开边界记录未精确绑定源状态或结果控制",
            )
    for closing in resolution.propulsion_closing_records:
        resulting_ship = resulting_ships[closing.ship_id]
        if (
            closing.resulting_propulsion_state_sha256
            != canonical_sha256(resulting_ship.propulsion_state)
            or closing.motion_state_sha256
            != canonical_sha256(resulting_ship.motion_state)
        ):
            raise ContractError(
                "governed_scene.closing_scene_chain",
                f"$.propulsion_closing_records.{closing.ship_id}",
                "收边界记录未精确绑定结果推进状态或最终运动",
            )
    if canonical_sha256(value) != canonical_sha256(expected):
        raise ContractError(
            "governed_scene.result_payload",
            "$",
            "结果内容与完整受控结果不一致",
        )
