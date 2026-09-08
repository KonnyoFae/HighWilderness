"""Small paused-control projection; never exposes or edits the authoritative save."""
from copy import deepcopy
from 高天荒野舰艇数据契约 import ContractError, canonical_sha256
from 高天荒野舰艇定向直控仲裁 import prepare_directional_direct_command, INTERFACE

CONTROL_STATE_INTERFACE = "gaotian.tactical-paused-control-state/v1alpha1"
DIRECT_SHIP_ID = "ship.web.blue"


def render_control_state(scenario, state, tuning, last_input_seq, last_input, last_resolution):
    available, reason = True, None
    try:
        prepare_directional_direct_command(scenario.scene, state, tuning, DIRECT_SHIP_ID)
    except ContractError as error:
        available, reason = False, error.message
    ship = next(s for s in scenario.scene.ships if s.ship_id == DIRECT_SHIP_ID)
    governors = ship.propulsion_state.governors
    applied = None if last_resolution is None else next(
        r for r in last_resolution.scene_resolution.ship_results if r.ship_id == DIRECT_SHIP_ID)
    return dict(interface=CONTROL_STATE_INTERFACE, adapter_interface=INTERFACE, direct_ship_id=DIRECT_SHIP_ID,
        available=available, unavailable_reason=reason, last_input_seq=last_input_seq,
        last_input_sha256=None if last_input is None else canonical_sha256(last_input),
        last_executed_step=None if last_input is None else last_input["target_step"],
        requested_control=None if last_input is None else deepcopy(last_input["arguments"]["control"]),
        delivery_status=None if applied is None else applied.propulsion_delivery_status,
        missing_channels=[] if applied is None else list(applied.missing_propulsion_channels),
        command_state_sha256=canonical_sha256(state), tuning_sha256=tuning.source_sha256,
        channels=[dict(channel=g.command.command_channel, requested_percent=g.command.requested_percent,
            safety_ceiling_percent=g.safety_ceiling_percent, safety_reasons=list(g.safety_reasons)) for g in governors],
        engines=[dict(id=e.actuator_instance_id, actual_percent=e.actual_output_percent,
            target_percent=e.target_output_percent, phase=e.phase) for e in ship.propulsion_state.engines],
        fuel_units=ship.combat_state.instance.operational_state.fuel_units,
        fuel_policy=scenario.manifest["tactical_fuel"]["policy"],
        last_arbitration=None if last_resolution is None else last_resolution.to_dict())
