"""Record deterministic real paused-control projections for frontend-only tests."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.high_wilderness_sidecar.tactical import TacticalService, SCENARIO_ID, INPUT_INTERFACE
from 高天荒野舰艇数据契约 import canonical_sha256
from 高天荒野舰艇定向推进控制桥 import directional_control
from 高天荒野舰艇推进通道合同 import ChannelPropulsionCommand

service = TacticalService("fixture.1", ROOT)
service.dispatch(dict(method="tactical.create", params=dict(scenario_id=SCENARIO_ID)))
service.scene_id = "scene.fixture"
target = ROOT / "apps/desktop/src/tactical/testing"
(target / "snapshot.fixture.json").write_text(json.dumps(service.snapshot(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
service.dispatch(dict(method="tactical.set_mode", params=dict(mode="tactical")))
forward = ChannelPropulsionCommand("translation.forward", "full", None)
controls = [directional_control((forward,)), directional_control((forward, ChannelPropulsionCommand("yaw.counterclockwise", None, 25))),
    directional_control(automatic_brake=True), directional_control((forward,))]
frames = []
for control in controls:
    value = dict(interface=INPUT_INTERFACE, scene_id=service.scene_id, input_seq=service.last_input_seq + 1,
        target_step=service.scenario.scene.fixed_step_index, command="control",
        arguments=dict(ship_id="ship.web.blue", control=control.to_dict()))
    result = service.dispatch(dict(method="tactical.step", params=dict(scene_id=service.scene_id, input=value)))
    frames.append(dict(input=value, sha256=canonical_sha256(value), result=result))
(target / "step.fixture.json").write_text(json.dumps(frames, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"Recorded {len(frames)} real v7 steps")
