"""d2b.4：已知完整资源链的显式启用和带精确来源的场景存档。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable

from 高天荒野舰艇数据契约 import ContractError, canonical_sha256
from 高天荒野舰艇推进安全判定器 import PropulsionSafetyProfile
from 高天荒野舰艇实际推进聚合器 import compile_actual_propulsion_context
from 高天荒野舰艇推进固定步接线 import (
    ACTUAL_SCENE_INTERFACE_ID, ActualPropulsionExecution, ActualShipPropulsionResources,
    ActualScenePropulsionContext, ActualPropulsionBoundaryRecord, validate_boundary_replay,
)
from 高天荒野舰艇推进通道合同 import exact_object
from 高天荒野舰艇定向推进控制桥 import directional_control
from 高天荒野舰艇推进场景构建器 import DirectionalSceneResourceBundle, build_known_directional_scene
from 高天荒野舰艇统一战术场景 import (
    TacticalSceneState, TacticalSceneShipBinding, TacticalSceneStepResolution,
    prepare_tactical_scene_bindings, validate_actual_scene_context,
    validate_tactical_scene_propulsion_profile,
)

ACTUAL_SCENE_SAVE_INTERFACE_ID = "gaotian.actual-propulsion-scene-save/v1alpha1"


@dataclass(frozen=True)
class ActualSceneSession:
    resource_bundle: DirectionalSceneResourceBundle
    scene: TacticalSceneState
    bindings: tuple[TacticalSceneShipBinding, ...]
    propulsion_context: ActualScenePropulsionContext


def build_known_actual_scene(root: str | Path, scene_id: str, source_scene: TacticalSceneState,
    source_bindings: Iterable[TacticalSceneShipBinding], safety_profile: PropulsionSafetyProfile,
) -> ActualSceneSession:
    """从指纹锁定 d1 初态正式重建全部资源，再显式生成可运行 v5；不是任意存档迁移。"""
    bundle = build_known_directional_scene(root, scene_id, source_scene, source_bindings)
    validate_tactical_scene_propulsion_profile(bundle.scene, safety_profile)
    profiles = {p.profile_key: p for p in bundle.profiles}
    execution = ActualPropulsionExecution(bundle.scene_id, canonical_sha256(bundle))
    resources = tuple(ActualShipPropulsionResources(
        compile_actual_propulsion_context(scene_id, s.binding.ship_id, s.binding.snapshot,
            profiles[s.profile_key].catalog, s.actuators), s.binding.sortie.source_sha256,
    ) for s in bundle.ships)
    context = ActualScenePropulsionContext(execution, safety_profile, resources)
    scene = replace(bundle.scene, propulsion_execution=execution,
        ships=tuple(replace(ship, propulsion_control=directional_control()) for ship in bundle.scene.ships))
    scene = TacticalSceneState.parse(scene.to_dict())
    validate_actual_scene_context(scene, context)
    # 返回独立绑定，避免运行中缓存污染初态资源包的可审计指纹。
    bindings = prepare_tactical_scene_bindings(scene, bundle.bindings)
    return ActualSceneSession(bundle, scene, bindings, context)


def save_actual_scene(scene: TacticalSceneState, context: ActualScenePropulsionContext) -> dict[str, Any]:
    validate_actual_scene_context(scene, context)
    TacticalSceneState.parse(scene.to_dict())
    return {"interface": ACTUAL_SCENE_SAVE_INTERFACE_ID, "scene": scene.to_dict(),
            "scene_sha256": canonical_sha256(scene)}


def load_actual_scene_save(value: Any, *, root: str | Path, scene_id: str,
    source_scene: TacticalSceneState, source_bindings: Iterable[TacticalSceneShipBinding],
    safety_profile: PropulsionSafetyProfile,
) -> ActualSceneSession:
    """从明确的初态来源重建上下文/缓存，再加载严格 v5 当前状态；不信任序列化缓存。"""
    obj = exact_object(value, {"interface", "scene", "scene_sha256"}, "$")
    if obj["interface"] != ACTUAL_SCENE_SAVE_INTERFACE_ID or canonical_sha256(obj["scene"]) != obj["scene_sha256"]:
        raise ContractError("actual_scene.save_interface_hash", "$", "存档版本或场景指纹错误")
    scene = TacticalSceneState.parse(obj["scene"])
    if scene.to_dict()["interface"] != ACTUAL_SCENE_INTERFACE_ID:
        raise ContractError("actual_scene.save_version", "$.scene", "不接受合同专用或历史场景")
    rebuilt = build_known_actual_scene(root, scene_id, source_scene, source_bindings, safety_profile)
    validate_actual_scene_context(scene, rebuilt.propulsion_context)
    bindings = prepare_tactical_scene_bindings(scene, rebuilt.bindings)
    return replace(rebuilt, scene=scene, bindings=bindings)


def validate_actual_scene_step_payload(value: Any, source_scene: TacticalSceneState,
    resolution: TacticalSceneStepResolution, context: ActualScenePropulsionContext,
) -> None:
    """结果存储 hash 而非整套资源；严格核对完整基础结果，并重新验证首尾内核状态链。"""
    expected = resolution.to_dict()
    exact_object(value, set(expected), "$")
    validate_actual_scene_context(source_scene, context)
    validate_actual_scene_context(resolution.resulting_scene, context)
    if resolution.source_scene_sha256 != canonical_sha256(source_scene) or (
        resolution.resulting_scene.fixed_step_index != source_scene.fixed_step_index + 1
    ):
        raise ContractError("actual_scene.result_source", "$", "结果源场景或步号不匹配")
    if not isinstance(value["propulsion_boundaries"], list):
        raise ContractError("type.array", "$.propulsion_boundaries", "必须是数组")
    records = tuple(ActualPropulsionBoundaryRecord.parse(r) for r in value["propulsion_boundaries"])
    validate_boundary_replay({s.ship_id: s.propulsion_state for s in source_scene.ships},
        {s.ship_id: s.propulsion_state for s in resolution.resulting_scene.ships}, context,
        source_scene.fixed_step_index, records)
    if canonical_sha256(value) != canonical_sha256(expected):
        raise ContractError("actual_scene.result_payload", "$", "结果内容与完整基础结果不一致")
