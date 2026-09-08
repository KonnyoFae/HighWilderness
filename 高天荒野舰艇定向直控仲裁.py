"""T3a explicit I9-to-v7 adapter for one player flagship; legacy I9 remains unchanged."""
from dataclasses import dataclass, replace

from 高天荒野舰艇数据契约 import ContractError, canonical_sha256
from 高天荒野舰艇战术舰队指挥 import (
    TacticalFleetCommandState, TacticalFleetCommandEvent, TacticalCommandTuningProfile,
    _validate_command_state_shape, _synchronize_state_to_scene,
)
from 高天荒野舰艇定向推进控制桥 import DirectionalPropulsionControlInput, directional_control
from 高天荒野舰艇完整受控推进场景 import validate_fully_governed_scene_context
from 高天荒野舰艇统一战术场景 import TacticalSceneStepResolution, advance_tactical_scene_step

INTERFACE = "gaotian.directional-direct-command-step/v1alpha1"


def prepare_directional_direct_command(scene, state, tuning, ship_id):
    """Reuse I9's exact scene binding, role and lifecycle arbitration, without mutating it."""
    _validate_command_state_shape(state)
    if state.tuning_profile != tuning.reference or state.tuning_profile_sha256 != tuning.source_sha256:
        raise ContractError("tactical_command.tuning_mismatch", "$.tuning", "指挥配置指纹不匹配")
    synchronized, events = _synchronize_state_to_scene(state, scene, require_source_match=True,
        waypoint_tolerance_m=tuning.waypoint_tolerance_m)
    if synchronized.mode != "normal":
        raise ContractError("tactical_command.final_mobilization_direct", "$.direct_control", "最终动员不能直接操纵")
    if synchronized.phase != "active":
        raise ContractError("tactical_command.direct_after_loss", "$.direct_control", "旗舰已失去直控权，不能继续操纵")
    if ship_id != synchronized.direct_control_ship_id:
        raise ContractError("tactical_command.direct_ship_mismatch", "$.ship_id", "只能操纵本场景指定的蓝方旗舰")
    # RTS order compilation is not silently converted to directional propulsion.
    if len(synchronized.assignments) != 1 or any(o.status == "active" for o in synchronized.orders):
        raise ContractError("tactical_command.directional_scope", "$.assignments", "当前定向直控适配仅接受无 RTS 指令的单旗舰")
    ship = next(s for s in scene.ships if s.ship_id == ship_id)
    if ship.lifecycle_state.physical_status != "operational" or ship.lifecycle_state.command_status != "scene_command":
        raise ContractError("tactical_command.direct_unavailable", "$.ship_id", "旗舰当前不能接收操纵指令")
    return synchronized, events


@dataclass(frozen=True)
class DirectionalDirectCommandResolution:
    source_command_state_sha256: str
    ship_id: str
    requested_control: DirectionalPropulsionControlInput
    scene_resolution: TacticalSceneStepResolution
    resulting_command_state: TacticalFleetCommandState
    command_events: tuple[TacticalFleetCommandEvent, ...]

    def to_dict(self):
        return dict(interface=INTERFACE, ship_id=self.ship_id, source="player_direct",
            source_command_state_sha256=self.source_command_state_sha256,
            resulting_command_state_sha256=canonical_sha256(self.resulting_command_state),
            source_scene_sha256=self.scene_resolution.source_scene_sha256,
            resulting_scene_sha256=canonical_sha256(self.scene_resolution.resulting_scene),
            requested_control=self.requested_control.to_dict(), command_events=[e.to_dict() for e in self.command_events])


def advance_directional_direct_scene_step(scene, command_state, bindings, timing_catalog, projectile_catalog,
        material_registry, tuning: TacticalCommandTuningProfile, *, ship_id: str,
        control: DirectionalPropulsionControlInput, propulsion_context, continuous_damage_profile,
        binding_validation_mode="strict"):
    validate_fully_governed_scene_context(scene, propulsion_context)
    synchronized, before = prepare_directional_direct_command(scene, command_state, tuning, ship_id)
    if not isinstance(control, DirectionalPropulsionControlInput):
        raise ContractError("tactical_command.directional_control", "$.control", "必须使用定向推进控制")
    binding_tuple = tuple(bindings)
    if len(binding_tuple) != len(scene.ships) or {b.ship_id for b in binding_tuple} != {s.ship_id for s in scene.ships}:
        raise ContractError("tactical_command.binding_set", "$.bindings", "绑定必须恰好覆盖场景舰艇")
    # T3a has no NPC/autopilot input route. Explicit neutral inputs prevent a stale
    # persisted directional command on another ship from becoming a hidden bypass.
    controls = {s.ship_id: control if s.ship_id == ship_id else directional_control() for s in scene.ships}
    resolution = advance_tactical_scene_step(scene, binding_tuple, timing_catalog, projectile_catalog, material_registry,
        propulsion_context=propulsion_context, propulsion_controls=controls,
        continuous_damage_profile=continuous_damage_profile, binding_validation_mode=binding_validation_mode)
    resulting_state = replace(synchronized, source_scene_sha256=canonical_sha256(resolution.resulting_scene),
        last_scene_step_index=resolution.resulting_scene.fixed_step_index)
    resulting_state, after = _synchronize_state_to_scene(resulting_state, resolution.resulting_scene,
        require_source_match=True, waypoint_tolerance_m=tuning.waypoint_tolerance_m)
    return DirectionalDirectCommandResolution(canonical_sha256(command_state), ship_id, control,
        resolution, resulting_state, (*before, *after))
