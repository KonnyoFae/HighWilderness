"""T2a mode ownership, authority immutability and projection units."""
from copy import deepcopy
import unittest

from backend.high_wilderness_sidecar.server import SidecarServer
from backend.high_wilderness_sidecar.tactical import TacticalService
from backend.high_wilderness_sidecar.tactical_scenario import SCENARIO_ID
from 高天荒野舰艇数据契约 import ContractError, canonical_sha256


class TacticalViewportTests(unittest.TestCase):
    def test_mode_ack_is_idempotent_and_does_not_create_or_advance_scene(self):
        service = TacticalService("backend.test")
        switch = lambda mode: service.dispatch(dict(method="tactical.set_mode", params=dict(mode=mode)))
        self.assertEqual(switch("tactical"), dict(mode="tactical", paused=True, scene_id=None))
        scene = service.dispatch(dict(method="tactical.create", params=dict(scenario_id=SCENARIO_ID)))
        before = canonical_sha256(service.scenario.scene)
        first = switch("editor")
        self.assertEqual(first, switch("editor"))
        self.assertEqual(first["scene_id"], scene["scene_id"])
        switch("tactical")
        self.assertEqual(canonical_sha256(service.scenario.scene), before)
        self.assertEqual(service.snapshot()["fixed_step"], 0)
        self.assertEqual(TacticalService("backend.new").mode, "editor")

    def test_invalid_or_editor_scoped_mode_request_cannot_unlock(self):
        service = TacticalService("backend.test")
        service.dispatch(dict(method="tactical.set_mode", params=dict(mode="tactical")))
        for extra, params in [({}, {}), ({}, dict(mode="other")), ({}, dict(mode=["editor"])),
                              ({}, dict(mode="editor", extra=1)), (dict(session_id="editor.1"), dict(mode="editor"))]:
            with self.subTest(params=params), self.assertRaises(ContractError):
                service.dispatch(dict(method="tactical.set_mode", params=params, **extra))
            self.assertEqual(service.mode, "tactical")

    def test_editor_writes_and_file_grants_blocked_but_read_queries_remain_available(self):
        server = SidecarServer("backend.test")
        server.handshake_complete = True
        seen = []
        server.editor.dispatch = lambda req: (seen.append(deepcopy(req)) or {}, None)
        def call(method, params=None):
            return server.execute(dict(interface="gaotian.web-bridge/v1alpha1", kind="request", request_id="req.1",
                backend_instance_id="backend.test", session_id=None, expected_revision=None, method=method, params=params or {}))[0][0]
        self.assertTrue(call("tactical.set_mode", dict(mode="tactical"))["ok"])
        for method in ["editor.create", "editor.open", "editor.command", "editor.undo", "editor.redo", "editor.save",
                       "editor.bind_file", "editor.open_file", "editor.close", "editor.recover"]:
            self.assertEqual(call(method)["error"]["code"], "tactical.editor_locked")
        self.assertEqual(seen, [])
        for method in ["resource.list", "editor.inspect", "editor.preview", "editor.recovery_list"]:
            self.assertTrue(call(method)["ok"])
        self.assertEqual(len(seen), 4)
        self.assertTrue(call("tactical.set_mode", dict(mode="editor"))["ok"])
        self.assertTrue(call("editor.command")["ok"])


if __name__ == "__main__":
    unittest.main()
