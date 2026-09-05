"""d4.7：具名 v7 构建、完整安全资源门和严格存档重载。"""

from dataclasses import dataclass, replace
from math import isfinite
from pathlib import Path
from typing import Any, Iterable

from 高天荒野舰艇数据契约 import ContractError, canonical_sha256
from 高天荒野舰艇推进固定步接线 import ActualScenePropulsionContext
from 高天荒野舰艇受控推进场景 import build_known_governed_scene
from 高天荒野舰艇完整受控推进场景版本 import FullyGovernedPropulsionExecutionPolicy
from 高天荒野舰艇完整受控推进场景合同 import (
    FullyGovernedSceneSave, validate_fully_governed_scene_step_sources,
)
from 高天荒野舰艇受控推进完整安全适配器 import (
    _validate_state as validate_complete_propulsion_state,
    validate_fully_governed_propulsion_opening, validate_fully_governed_propulsion_closing,
)
from 高天荒野舰艇推进场景构建器 import DirectionalSceneResourceBundle
from 高天荒野舰艇推进安全判定器 import PropulsionSafetyProfile
from 高天荒野舰艇推进通道合同 import TRANSLATION_CHANNELS, exact_object
from 高天荒野舰艇定向推进控制桥 import (
    DirectionalPropulsionControlInput, directional_control, automatic_linear_brake_control,
)
from 高天荒野舰艇统一战术场景 import (
    TacticalSceneState, TacticalSceneShipBinding, TacticalSceneStepResolution,
    prepare_tactical_scene_bindings, validate_tactical_scene_propulsion_profile,
)


def _require(ok, code, detail):
    if not ok:
        raise ContractError('full_scene.' + code, '$', detail)


def validate_fully_governed_scene_context(state: TacticalSceneState, context: ActualScenePropulsionContext) -> None:
    _require(isinstance(state.propulsion_governance, FullyGovernedPropulsionExecutionPolicy), 'policy_required', '必须提供 v7 完整治理身份')
    _require(isinstance(context, ActualScenePropulsionContext) and state.propulsion_execution is not None,
             'context_required', 'v7 推进和加载必须提供精确资源上下文')
    context.__post_init__()
    FullyGovernedPropulsionExecutionPolicy.parse(state.propulsion_governance.to_dict())
    _require(state.propulsion_execution == context.execution, 'execution_lineage', '场景资源包身份不一致')
    validate_tactical_scene_propulsion_profile(state, context.safety_profile)
    _require(tuple(s.ship_id for s in state.ships) == tuple(s.ship_id for s in context.ships), 'resource_ship_set', '资源舰艇集合必须精确一致')
    for ship in state.ships:
        resources = context.ship(ship.ship_id)
        _require((ship.derived_snapshot_sha256, ship.sortie_configuration_sha256) ==
                 (resources.aggregation_context.snapshot.source_sha256, resources.sortie_configuration_sha256),
                 'resource_mismatch', '舰艇快照或出航来源不一致')
        clocks = {g.last_evaluated_step_index for g in ship.propulsion_state.governors}
        _require(len(clocks) == 1 and None not in clocks, 'governor_clock', '必须保留唯一已提交安全时钟')
        clock = next(iter(clocks))
        _require(clock <= state.fixed_step_index and (ship.lifecycle_state.physical_status == 'exited' or clock == state.fixed_step_index),
                 'governor_clock', '活动舰安全时钟必须等于场景边界，退出舰不得来自未来')
        validate_complete_propulsion_state(resources.aggregation_context, ship.propulsion_state, ship.propulsion_control,
                                          governor_clock=clock, engine_boundary=clock)


def select_fully_governed_propulsion_control(context, state, previous, requested, *, velocity_body, command_available, fixed_step_index):
    """选择原始持久命令；对向降推及放行唯一交给 d4 互锁决定。"""
    _require(type(fixed_step_index) is int and fixed_step_index >= 0, 'control_step', '固定步必须为非负整数')
    _require(type(command_available) is bool and isinstance(velocity_body, tuple) and len(velocity_body) == 2 and
             all(type(x) in (int, float) and isfinite(x) for x in velocity_body), 'control_input', '命令可用性与体轴速度非法')
    validate_complete_propulsion_state(context, state, previous, governor_clock=fixed_step_index, engine_boundary=fixed_step_index)
    control = previous if requested is None else requested
    _require(isinstance(control, DirectionalPropulsionControlInput), 'control_type', '只接受定向离散控制')
    DirectionalPropulsionControlInput.parse(control.to_dict())
    available = {channel for binding in context.bindings for channel in binding.command_channels}
    if not command_available:
        return directional_control(), ()
    if control.automatic_brake:
        selected = automatic_linear_brake_control(lateral_velocity_body_mps=velocity_body[0],
            longitudinal_velocity_body_mps=velocity_body[1],
            available_translation_channels=tuple(c for c in TRANSLATION_CHANNELS if c in available),
            overg_requested=control.overg_requested)
        return selected.control, selected.unavailable_channels
    return control, tuple(c.command_channel for c in control.channel_commands if c.requested_percent and c.command_channel not in available)


@dataclass(frozen=True)
class FullyGovernedSceneSession:
    resource_bundle: DirectionalSceneResourceBundle
    scene: TacticalSceneState
    bindings: tuple[TacticalSceneShipBinding, ...]
    propulsion_context: ActualScenePropulsionContext


def build_known_fully_governed_scene(root: str | Path, scene_id: str, source_scene: TacticalSceneState,
        source_bindings: Iterable[TacticalSceneShipBinding], safety_profile: PropulsionSafetyProfile) -> FullyGovernedSceneSession:
    """复用具名完整资源编译与边界零软安全初始化，不迁移历史运行中状态。"""
    base = build_known_governed_scene(root, scene_id, source_scene, source_bindings, safety_profile)
    scene = replace(base.scene, propulsion_governance=FullyGovernedPropulsionExecutionPolicy())
    scene = TacticalSceneState.parse(scene.to_dict())
    validate_fully_governed_scene_context(scene, base.propulsion_context)
    return FullyGovernedSceneSession(base.resource_bundle, scene, base.bindings, base.propulsion_context)


def save_fully_governed_scene(scene: TacticalSceneState, context: ActualScenePropulsionContext) -> dict[str, Any]:
    validate_fully_governed_scene_context(scene, context)
    return FullyGovernedSceneSave(scene.to_dict()).to_dict()


def load_fully_governed_scene_save(value: Any, *, root: str | Path, scene_id: str, source_scene: TacticalSceneState,
        source_bindings: Iterable[TacticalSceneShipBinding], safety_profile: PropulsionSafetyProfile) -> FullyGovernedSceneSession:
    saved = FullyGovernedSceneSave.parse(value)
    scene = TacticalSceneState.parse(saved.scene)
    rebuilt = build_known_fully_governed_scene(root, scene_id, source_scene, source_bindings, safety_profile)
    validate_fully_governed_scene_context(scene, rebuilt.propulsion_context)
    # prepare 会建立新绑定和缓存，不复用存档中的 token、runtime 或模型。
    bindings = prepare_tactical_scene_bindings(scene, rebuilt.bindings)
    return replace(rebuilt, scene=scene, bindings=bindings)


def validate_fully_governed_scene_step_payload(value: Any, source_scene: TacticalSceneState,
        resolution: TacticalSceneStepResolution, context: ActualScenePropulsionContext) -> None:
    """核对场景两端、全部实际输出，以及每舰最终运行时上的收边界重算。"""
    expected = resolution.to_dict()
    exact_object(value, set(expected), '$')
    validate_fully_governed_scene_step_sources(value, source_scene.to_dict(), resolution.resulting_scene.to_dict())
    validate_fully_governed_scene_context(source_scene, context)
    validate_fully_governed_scene_context(resolution.resulting_scene, context)
    _require(canonical_sha256(value) == canonical_sha256(expected), 'result_payload', '完整结果被改写')
    # 证据中的开边界 runtime 来自真实统一场景边界（可包含本步武器资源结算），
    # 不能简单用 S_n 的实例替代。它们留在内存结果里，存档不序列化缓存或 runtime。
    witnesses = resolution.fully_governed_runtime_witnesses
    _require(tuple(s for s, _, _ in witnesses) == tuple(s for s, _ in resolution.fully_governed_openings),
             'runtime_witness_set', '必须逐舰保留开边界 runtime 与运动见证')
    from 高天荒野舰艇受控推进完整安全适配器 import integrate_fully_governed_propulsion_interval
    from 高天荒野舰艇战术机动求解器 import build_tactical_ship_model
    sources = {s.ship_id: s for s in source_scene.ships}
    targets = {s.ship_id: s for s in resolution.resulting_scene.ships}
    closings = dict(resolution.fully_governed_closings)
    rows = {r.ship_id: r for r in resolution.ship_results}
    for (ship_id, opening), (_, runtime, motion) in zip(resolution.fully_governed_openings, witnesses):
        aggregation_context = context.ship(ship_id).aggregation_context
        model = build_tactical_ship_model(runtime, aggregation_context.snapshot)
        validate_fully_governed_propulsion_opening(opening, aggregation_context, runtime,
            sources[ship_id].propulsion_state, opening.hard_fault_opening.command)
        interval = integrate_fully_governed_propulsion_interval(aggregation_context, runtime, model, motion, opening,
            propulsion_delivery_status=rows[ship_id].propulsion_delivery_status)
        _require(interval.aggregation == rows[ship_id].propulsion_aggregation and
                 interval.diagnostics.diagnostic == rows[ship_id].diagnostics.base.diagnostic,
                 'interval_replay', '实际交付必须通过资源与开边界运行时重算')
        final_runtime = rows[ship_id].resulting_runtime
        final_model = build_tactical_ship_model(final_runtime, aggregation_context.snapshot)
        validate_fully_governed_propulsion_closing(closings[ship_id], aggregation_context, context.safety_profile,
            final_runtime, final_model, targets[ship_id].motion_state)
