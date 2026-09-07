"""O1/O2 real catalog, command, persistence and process integration checks."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from backend.high_wilderness_sidecar.sessions import EditorService
from backend.high_wilderness_sidecar.outfits import document, module_catalog
from 高天荒野舰艇数据契约 import ContractError, ModulePrototypeCatalog, canonical_sha256
from 高天荒野舰艇编辑器领域层 import OutfitEditorDocument
from 高天荒野WebSidecar生命周期测试 import spawn, hello, request, write, read_line, finish


class OutfitSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.service = EditorService("outfit-test", recovery_dir=self.root / "recovery")
        self.state = None
        self.open("conventional_crewed")

    def call(self, method, params=None, unscoped=False):
        state = None if unscoped else self.state
        return self.service.dispatch(dict(method=method, params=params or {},
            session_id=state["session_id"] if state else None,
            expected_revision=state["revision"] if state else None))[0]

    def open(self, suffix):
        key = next(d["key"] for d, _ in self.service.index.resources.values()
                   if d["kind"] == "OutfitPlan" and d["id"].endswith(suffix))
        self.state = self.call("editor.open", {"resource_key": key}, True)

    def command(self, operation, **args):
        self.state = self.call("editor.command", dict(command="outfit." + operation, arguments=args))

    def test_catalog_all_thirteen_categories_exact_content_and_four_mounts(self):
        result = self.call("resource.list", unscoped=True)
        self.assertEqual(result["interface"], "gaotian.editor-resource-index/v2alpha1")
        options = result["module_options"]
        self.assertEqual(len(options), 21)
        self.assertEqual(len({o["prototype"]["category"] for o in options}), 13)
        for option in options:
            self.assertEqual(canonical_sha256(option["prototype"]), option["sha256"])
            origin = self.service.index.resources[option["catalog"]["key"]][1]
            parsed = ModulePrototypeCatalog.parse(origin)
            self.assertIn(option["prototype"], [m.to_dict() for m in parsed.modules])
        geometry = [o["prototype"]["installation"] for o in options]
        for key in ["internal_footprint_half_cells", "top_footprint_half_cells", "side_mount_length_steps", "host_slot"]:
            self.assertTrue(any(g[key] for g in geometry))

    def test_four_installation_remove_replace_and_grid_rotation_single_revision(self):
        original = deepcopy(self.state["preview"])
        for name in ["cargo_hold", "weapon_upper_port", "thruster_port_fore", "remote_core"]:
            module = deepcopy(next(m for m in self.state["draft"]["modules"] if m["id"] == name))
            self.command("remove", instance_id=name)
            placement = module["placement"]
            kind = placement.pop("kind")
            self.command("place_" + kind, instance_id=name, prototype=module["prototype"], **placement)
            self.assertEqual(self.state["preview"], original, name)
        revision = self.state["revision"]
        self.command("move_grid", instance_id="cargo_hold", deck_id="deck.0", anchor_half_cell=[2, 6], rotation_deg=90)
        self.assertEqual(self.state["revision"], revision + 1)
        self.assertTrue(self.state["preview"]["valid"])
        self.state = self.call("editor.undo")
        self.assertEqual(self.state["preview"], original)
        self.state = self.call("editor.redo")
        self.assertEqual(next(m for m in self.state["draft"]["modules"] if m["id"] == "cargo_hold")["placement"]["rotation_deg"], 90)

    def test_side_and_rehost_failure_atomic_and_noop(self):
        before = self.call("editor.inspect")
        for command, args in [
            ("move_side", dict(instance_id="thruster_port_fore", deck_id="deck.0", region_id="deck.0.region.0", edge_index=6, start_slot_index=999, rotation_deg=180)),
            ("rehost", dict(instance_id="remote_core", host_instance_id="missing")),
            ("rehost", dict(instance_id="remote_core", host_instance_id="cargo_hold")),
        ]:
            with self.assertRaises(ContractError):
                self.command(command, **args)
            self.assertEqual(self.call("editor.inspect"), before)
        self.command("rehost", instance_id="remote_core", host_instance_id="cic")
        self.assertEqual(self.state, before)
        self.command("move_side", instance_id="thruster_port_fore", deck_id="deck.0", region_id="deck.0.region.0", edge_index=6, start_slot_index=6, rotation_deg=180)
        self.assertTrue(self.state["preview"]["valid"])
        self.assertEqual(self.state["revision"], 1)
        self.state = self.call("editor.undo")
        self.assertEqual(self.state["preview"], before["preview"])

    def test_domain_rehost_success_and_failure_leave_complete_source(self):
        # Isolated synthetic slot provider; no resource pack or production rule is changed.
        catalog_source = module_catalog(self.service.index).to_dict()
        next(m for m in catalog_source["modules"] if m["category"] == "cargo_hold")["installation"]["provided_slots"] = ["cic_internal"]
        base = document(self.state["draft"], self.service.index)
        doc = OutfitEditorDocument(base.source_dict(), base._hull, ModulePrototypeCatalog.parse(catalog_source), base._coating_catalog)
        doc.rehost("remote_core", "cargo_hold")
        self.assertTrue(doc.preview().valid)
        before = doc.source_dict()
        with self.assertRaises(ContractError):
            doc.rehost("remote_core", "remote_core")
        self.assertEqual(doc.source_dict(), before)

    def test_malformed_commands_do_not_mutate_and_invalid_layout_can_undo(self):
        before = self.call("editor.inspect")
        for args in [dict(instance_id="cargo_hold", deck_id="deck.0", anchor_half_cell=[True, 0], rotation_deg=0),
                     dict(instance_id="cargo_hold", deck_id="deck.0", anchor_half_cell=[0.5, 0], rotation_deg=0),
                     dict(instance_id="cargo_hold", deck_id="deck.0", anchor_half_cell=[2**53, 0], rotation_deg=0),
                     dict(instance_id="cargo_hold", deck_id="deck.0", anchor_half_cell=[0, 0], rotation_deg=True)]:
            with self.assertRaises(ContractError):
                self.command("move_grid", **args)
            self.assertEqual(self.call("editor.inspect"), before)
        with self.assertRaises(ContractError):
            self.command("place_hosted", instance_id="new", prototype=dict(id="missing", version=1), host_instance_id="cic")
        self.assertEqual(self.call("editor.inspect"), before)
        self.command("move_grid", instance_id="cargo_hold", deck_id="deck.0", anchor_half_cell=[0, 0], rotation_deg=0)
        self.assertFalse(self.state["preview"]["valid"])
        self.assertEqual(self.state["last_valid_preview"], before["preview"])
        self.assertNotIn("derived", self.state["preview"]["model"])
        self.state = self.call("editor.undo")
        self.assertEqual(self.state["preview"], before["preview"])

    def test_three_fixtures_file_roundtrip_and_invalid_restart_recovery(self):
        for suffix in ["minimum_legal", "conventional_crewed", "unmanned_flagship"]:
            self.call("editor.close", {"discard_changes": False})
            self.open(suffix)
            original = deepcopy(self.state["preview"])
            token = self.call("editor.bind_file", dict(host_path=str(self.root / (suffix + ".json")), mode="save"))["destination_handle"]
            self.state = self.call("editor.save", dict(destination_handle=token, new_version=False))
            self.call("editor.close", {"discard_changes": False})
            token = self.call("editor.bind_file", dict(host_path=str(self.root / (suffix + ".json")), mode="open"), True)["destination_handle"]
            self.state = self.call("editor.open_file", dict(destination_handle=token), True)
            self.assertEqual(self.state["preview"], original)
            self.assertEqual(self.state["resource"]["kind"], "OutfitPlan")
            self.assertFalse(self.state["dirty"])
        self.command("remove", instance_id="cic")
        self.assertFalse(self.state["preview"]["valid"])
        with self.assertRaises(ContractError):
            self.call("editor.save", dict(destination_handle=None, new_version=False))
        invalid = deepcopy(self.state["preview"])
        self.service = EditorService("outfit-restart", recovery_dir=self.root / "recovery")
        record = self.call("editor.recovery_list", unscoped=True)["records"][0]
        self.state = self.call("editor.recover", {"recovery_key": record["key"]}, True)
        self.assertEqual(self.state["preview"], invalid)
        self.assertEqual(self.state["last_valid_preview"], original)
        self.state = self.call("editor.undo")
        self.assertEqual(self.state["preview"], original)

    def test_dependency_change_rejects_save_and_recovery(self):
        self.command("rename", name="变更后的舾装")
        changed = deepcopy(self.service.index)
        key = next(k for k, (d, _) in changed.resources.items() if d["kind"] == "ModulePrototypeCatalog")
        changed.resources[key][0]["sha256"] = "0" * 64
        token = self.call("editor.bind_file", dict(host_path=str(self.root / "outfit.json"), mode="save"))["destination_handle"]
        with patch("backend.high_wilderness_sidecar.sessions.ResourceIndex", return_value=changed):
            with self.assertRaisesRegex(ContractError, "save_dependencies_changed"):
                self.call("editor.save", dict(destination_handle=token, new_version=False))
        self.assertFalse((self.root / "outfit.json").exists())
        self.service = EditorService("changed", recovery_dir=self.root / "recovery")
        self.service._index = changed
        record = self.call("editor.recovery_list", unscoped=True)["records"][0]
        with self.assertRaisesRegex(ContractError, "recovery_dependencies_changed"):
            self.call("editor.recover", {"recovery_key": record["key"]}, True)

    def test_real_process_opens_outfit_and_commits_then_undoes(self):
        proc = spawn()
        try:
            write(proc, hello())
            read_line(proc.stdout)
            read_line(proc.stdout)
            write(proc, request("req.2", "editor.open", {"resource_key": self.state["resource"]["key"]}))
            result = read_line(proc.stdout)
            self.assertTrue(result["ok"])
            state = result["result"]
            write(proc, request("req.3", "editor.command", dict(command="outfit.rename", arguments=dict(name="真实舾装进程")), session_id=state["session_id"], expected_revision=0))
            self.assertEqual(read_line(proc.stdout)["result"]["revision"], 1)
            write(proc, request("req.4", "editor.undo", {}, session_id=state["session_id"], expected_revision=1))
            self.assertEqual(read_line(proc.stdout)["result"]["preview"], state["preview"])
            write(proc, request("req.5", "system.shutdown", {"reason": "user_exit"}))
            self.assertTrue(read_line(proc.stdout)["ok"])
            self.assertEqual(finish(proc, 0), "")
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                stream.close()


if __name__ == "__main__":
    unittest.main()
