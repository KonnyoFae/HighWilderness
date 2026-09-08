"""T1a fresh v7 sample, render projection, input contract and sidecar boundaries."""
from copy import deepcopy
from pathlib import Path
from queue import Queue
from threading import Thread, Event
import json
import unittest

from backend.high_wilderness_sidecar.tactical import (
    TacticalService, TACTICAL_CAPABILITIES, INPUT_INTERFACE, validate_control_input,
)
from backend.high_wilderness_sidecar.tactical_scenario import build_two_ship_scenario, SCENARIO_ID
from backend.high_wilderness_sidecar.server import SidecarServer
from 高天荒野舰艇数据契约 import ContractError, canonical_sha256
from 高天荒野舰艇定向推进控制桥 import directional_control
from 高天荒野舰艇推进通道合同 import ChannelPropulsionCommand
from 高天荒野舰艇完整受控推进场景 import validate_fully_governed_scene_context
from 高天荒野Web桥接协议 import BRIDGE_INTERFACE, encode_message

ROOT = Path(__file__).resolve().parent


class TacticalTests(unittest.TestCase):
    def create(self, service=None):
        service = service or TacticalService("backend.test", ROOT)
        return service, service.dispatch(dict(method="tactical.create", params={"scenario_id": SCENARIO_ID}))

    def test_new_v7_sample_has_explicit_resources_and_real_geometry(self):
        service, view = self.create()
        self.assertEqual(view["authority_interface"], "gaotian.tactical-scene-timeline/v7alpha1")
        self.assertEqual(len(view["ships"]), 2)
        self.assertEqual({s["side_id"] for s in view["static"]["ships"]}, {"side.blue", "side.red"})
        self.assertEqual([len(s["modules"]) for s in view["static"]["ships"]], [18, 18])
        self.assertEqual([len(s["decks"]) for s in view["static"]["ships"]], [2, 2])
        self.assertEqual(view["fixed_step_s"], 1 / 60)
        self.assertTrue(view["paused"])
        self.assertEqual(view["events"], [])
        for static, binding, ship in zip(view["static"]["ships"], service.scenario.bindings, service.scenario.scene.ships):
            self.assertEqual(static["derived_snapshot_sha256"], binding.snapshot.source_sha256)
            self.assertEqual(static["modules"][0]["anchor_m"], list(binding.snapshot.outfit.instances[0].anchor_m))
            self.assertEqual(ship.combat_state.instance.operational_state.fuel_units, 800)
            self.assertTrue(ship.combat_state.instance.ammunition_state.magazines)
            self.assertIsNotNone(ship.combat_state.instance.continuous_damage_state)
        validate_fully_governed_scene_context(service.scenario.scene, service.scenario.propulsion_context)

    def test_repeated_reads_do_not_mutate_authority_or_repeat_static(self):
        service, first = self.create()
        before = canonical_sha256(service.scenario.scene)
        args = dict(method="tactical.inspect", params=dict(scene_id=first["scene_id"], known_static_sha256=first["static_sha256"]))
        second = service.dispatch(args)
        self.assertIsNone(second["static"])
        self.assertEqual(second["ships"], first["ships"])
        self.assertEqual(service.dispatch(args), second)
        self.assertEqual(canonical_sha256(service.scenario.scene), before)
        first["static"]["ships"].clear()
        args["params"]["known_static_sha256"] = "0" * 64
        self.assertEqual(len(service.dispatch(args)["static"]["ships"]), 2)

    def test_close_recreate_restart_reject_old_scene(self):
        service, view = self.create()
        with self.assertRaisesRegex(ContractError, "scene_active"):
            self.create(service)
        old = view["scene_id"]
        self.assertTrue(service.dispatch(dict(method="tactical.close", params={"scene_id": old}))["closed"])
        _, second = self.create(service)
        self.assertNotEqual(old, second["scene_id"])
        for current in (service, TacticalService("backend.new", ROOT)):
            with self.assertRaisesRegex(ContractError, "scene_missing"):
                current.dispatch(dict(method="tactical.inspect", params=dict(scene_id=old, known_static_sha256=None)))

    def test_read_can_reconnect_after_a_lost_create_response(self):
        service, created = self.create()
        found = service.dispatch(dict(method="tactical.inspect", params=dict(scene_id=None, known_static_sha256=None)))
        self.assertEqual(found, created)
        with self.assertRaises(ContractError):
            service.dispatch(dict(method="tactical.close", params=dict(scene_id=None)))

    def test_negative_arguments_do_not_replace_scene(self):
        service, view = self.create()
        for request in (
            dict(method="tactical.close", params={"scene_id": view["scene_id"], "extra": True}),
            dict(method="tactical.inspect", params=dict(scene_id=view["scene_id"], known_static_sha256=3)),
            dict(method="tactical.step", params={}),
            dict(method="tactical.close", params={"scene_id": view["scene_id"]}, session_id="editor.1", expected_revision=0),
        ):
            with self.assertRaises(ContractError):
                service.dispatch(request)
            self.assertEqual(service.snapshot(), view)
        with self.assertRaisesRegex(ContractError, "scenario_unknown"):
            TacticalService("test").dispatch(dict(method="tactical.create", params={"scenario_id": "functional_6.motion_only"}))

    def test_real_step_is_deterministic_and_honors_propulsion_request(self):
        first, second = (build_two_ship_scenario(ROOT) for _ in range(2))
        before = canonical_sha256(first.scene)
        self.assertEqual(before, canonical_sha256(second.scene))
        controls = {"ship.web.blue": directional_control((ChannelPropulsionCommand("translation.forward", "full", None),))}
        a, b = first.step(controls), second.step(controls)
        self.assertEqual(canonical_sha256(a.resulting_scene), canonical_sha256(b.resulting_scene))
        self.assertNotEqual(canonical_sha256(first.scene), before)
        self.assertEqual(first.scene.fixed_step_index, 1)
        self.assertAlmostEqual(first.scene.tactical_time_s, 1 / 60)
        self.assertEqual(first.scene.ships[0].propulsion_control, controls["ship.web.blue"])
        validate_fully_governed_scene_context(first.scene, first.propulsion_context)

    def test_input_identity_sequence_step_and_strict_control(self):
        source = dict(interface=INPUT_INTERFACE, scene_id="scene.test", input_seq=1, target_step=0,
                      command="control", arguments=dict(ship_id="ship.web.blue", control=directional_control().to_dict()))
        validate = lambda v: validate_control_input(v, scene_id="scene.test", current_step=0, last_input_seq=0,
                                                   ship_ids=("ship.web.blue", "ship.web.red"))
        self.assertEqual(validate(source), directional_control())
        for key, value in (("scene_id", "scene.old"), ("input_seq", 0), ("input_seq", True),
                           ("target_step", -1), ("target_step", 1), ("command", "fire"), ("interface", "old")):
            with self.subTest(key=key, value=value), self.assertRaises(ContractError):
                validate(dict(source, **{key: value}))
        bad = deepcopy(source)
        bad["arguments"]["ship_id"] = "other"
        with self.assertRaises(ContractError): validate(bad)
        bad = deepcopy(source)
        bad["arguments"]["control"]["extra"] = True
        with self.assertRaises(ContractError): validate(bad)

    def test_sidecar_capability_and_existing_envelope_contract(self):
        server = SidecarServer("backend.test")
        def call(number, method, params):
            request = dict(interface=BRIDGE_INTERFACE, kind="request", backend_instance_id="backend.test",
                           request_id=f"req.{number}", method=method, params=params, session_id=None, expected_revision=None)
            outputs, stop = server.handle(request)
            for output in outputs: encode_message(output)
            self.assertFalse(stop)
            return outputs[0]
        hello = call(1, "system.hello", dict(client_name="test", client_version="1", supported_interfaces=[BRIDGE_INTERFACE],
                                             required_capabilities=list(TACTICAL_CAPABILITIES)))
        self.assertTrue(set(TACTICAL_CAPABILITIES) <= set(hello["result"]["capabilities"]))
        created = call(2, "tactical.create", {"scenario_id": SCENARIO_ID})
        self.assertTrue(created["ok"])
        self.assertIsNone(created["session_id"])
        self.assertIsNone(created["revision"])
        self.assertLess(len(encode_message(created)), 128 * 1024)
        self.assertTrue(call(3, "system.ping", {"nonce": "test.ping"})["ok"])
        self.assertEqual(call(4, "tactical.step", {})["error"]["code"], "tactical.invalid_arguments")

    def test_tactical_work_shares_bounded_queue_without_blocking_heartbeat(self):
        from 高天荒野WebSidecar生命周期测试 import hello, request, INSTANCE_ID
        incoming, outgoing = Queue(), Queue()
        entered, release = Event(), Event()
        class Input:
            def read1(self, size): return incoming.get(timeout=10)
        class Output:
            def write(self, data): outgoing.put(json.loads(data))
            def flush(self): pass
        server = SidecarServer(INSTANCE_ID)
        original = server.tactical.dispatch
        def blocked(message):
            entered.set()
            self.assertTrue(release.wait(10))
            return original(message)
        server.tactical.dispatch = blocked
        codes = []
        worker = Thread(target=lambda: codes.append(server.serve(Input(), Output())), daemon=True)
        worker.start()
        try:
            incoming.put(encode_message(hello()))
            self.assertTrue(outgoing.get(timeout=5)["ok"])
            outgoing.get(timeout=5)
            incoming.put(encode_message(request("req.2", "tactical.create", {"scenario_id": SCENARIO_ID})))
            self.assertTrue(entered.wait(5))
            for n in range(3, 12): incoming.put(encode_message(request(f"req.{n}", "resource.list", {})))
            self.assertEqual(outgoing.get(timeout=5)["error"]["code"], "bridge.busy")
            incoming.put(encode_message(request("req.12", "system.ping", {"nonce": "ping.tactical.busy"})))
            self.assertEqual(outgoing.get(timeout=5)["result"], {"nonce": "ping.tactical.busy"})
        finally:
            release.set()
            incoming.put(b"")
            worker.join(10)
        self.assertFalse(worker.is_alive())
        self.assertEqual(codes, [0])
        accepted = [outgoing.get(timeout=5) for _ in range(9)]
        self.assertEqual([x["request_id"] for x in accepted], [f"req.{n}" for n in range(2, 11)])
        self.assertTrue(all(x["ok"] for x in accepted))


if __name__ == "__main__":
    unittest.main()
