"""W2b file authority, durable drafts and failure-safe save tests."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import unittest

from backend.high_wilderness_sidecar.sessions import EditorService
from backend.high_wilderness_sidecar.storage import atomic_write, fingerprint
from 高天荒野舰艇数据契约 import ContractError, canonical_sha256


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name).resolve()
        self.recovery = self.directory / "recovery"
        self.service = EditorService("backend.storage", recovery_dir=self.recovery)
        key = next(key for key, (entry, _) in self.service.index.resources.items() if entry["editable"])
        self.state = self.call("editor.open", {"resource_key": key}, unscoped=True)
        self.path = self.directory / "用户船壳.json"

    def call(self, method, params=None, unscoped=False):
        state = None if unscoped else getattr(self, "state", None)
        return self.service.dispatch({"method": method, "params": params or {},
            "session_id": state["session_id"] if state else None,
            "expected_revision": state["revision"] if state else None})[0]

    def bind(self, path=None, mode="save"):
        return self.call("editor.bind_file", {"host_path": str(path or self.path), "mode": mode}, unscoped=mode == "open")["destination_handle"]

    def rename(self, name="已修改的舰体"):
        self.state = self.call("editor.command", {"command": "hull.rename", "arguments": {"name": name}})

    def save(self, token=None, new=False):
        self.state = self.call("editor.save", {"destination_handle": token, "new_version": new})

    def test_save_reopen_and_unchanged_canonical_resource(self):
        original = self.state["draft_sha256"]
        self.save(self.bind())
        self.assertEqual(canonical_sha256(json.loads(self.path.read_text(encoding="utf-8"))), original)
        self.assertFalse(self.state["dirty"])
        self.assertEqual(self.state["revision"], 0)
        token = self.bind(mode="open")
        opened = self.call("editor.open_file", {"destination_handle": token}, unscoped=True)
        self.assertEqual(opened["draft_sha256"], original)
        self.assertTrue(opened["can_save_current"])

    def test_save_new_version_and_undo_tracks_saved_baseline(self):
        version = self.state["draft"]["version"]
        self.rename()
        self.save(self.bind(), new=True)
        self.assertEqual(self.state["draft"]["version"], version + 1)
        self.assertFalse(self.state["dirty"])
        self.state = self.call("editor.undo")
        self.assertTrue(self.state["dirty"])
        self.assertEqual(self.state["draft"]["version"], version)

    def test_external_change_conflicts_even_after_reselecting_current_path(self):
        self.save(self.bind())
        self.rename()
        self.path.write_text('{"external": true}', encoding="utf-8")
        external = self.path.read_bytes()
        with self.assertRaisesRegex(ContractError, "file_conflict"):
            self.save()
        with self.assertRaisesRegex(ContractError, "file_conflict"):
            self.save(self.bind())
        self.assertEqual(self.path.read_bytes(), external)
        self.assertTrue(self.state["dirty"])

    def test_failed_replace_preserves_file_and_dirty_draft(self):
        self.save(self.bind())
        original = self.path.read_bytes()
        self.rename()
        with patch("backend.high_wilderness_sidecar.storage.os.replace", side_effect=OSError("disk failure")):
            with self.assertRaisesRegex(ContractError, "file_write_failed"):
                self.save()
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.directory.glob(".*.tmp")), [])
        self.assertTrue(self.call("editor.inspect")["dirty"])

    def test_illegal_draft_is_recoverable_but_cannot_overwrite_legal_file(self):
        self.save(self.bind())
        original = self.path.read_bytes()
        self.state = self.call("editor.command", {"command": "hull.set_structure_material", "arguments": {
            "deck_id": self.state["draft"]["decks"][0]["id"], "material": {"id": "missing.material", "version": 1}}})
        with self.assertRaises(ContractError):
            self.save()
        self.assertEqual(self.path.read_bytes(), original)
        self.service = EditorService("backend.restarted", recovery_dir=self.recovery)
        records = self.call("editor.recovery_list", unscoped=True)["records"]
        self.state = self.call("editor.recover", {"recovery_key": records[0]["key"]}, unscoped=True)
        self.assertFalse(self.state["preview"]["valid"])
        self.assertTrue(self.state["last_valid_preview"]["valid"])
        self.assertFalse(self.state["can_save_current"])
        self.state = self.call("editor.undo")
        self.assertTrue(self.state["preview"]["valid"])

    def test_restart_recovery_retains_history_and_requires_new_file_authority(self):
        self.rename("恢复甲")
        self.rename("恢复乙")
        wanted = self.state["draft_sha256"]
        self.service = EditorService("backend.restarted", recovery_dir=self.recovery)
        record = self.call("editor.recovery_list", unscoped=True)["records"][0]
        self.state = self.call("editor.recover", {"recovery_key": record["key"]}, unscoped=True)
        self.assertEqual(self.state["draft_sha256"], wanted)
        self.assertTrue(self.state["recovered"])
        self.assertFalse(self.state["can_save_current"])
        self.state = self.call("editor.undo")
        self.assertEqual(self.state["draft"]["name"], "恢复甲")
        self.state = self.call("editor.redo")
        self.assertEqual(self.state["draft_sha256"], wanted)
        self.call("editor.close", {"discard_changes": True})
        self.assertEqual(self.call("editor.recovery_list", unscoped=True)["records"], [])

    def test_recovery_write_failure_does_not_acknowledge_lost_edit(self):
        before = self.call("editor.inspect")
        with patch.object(self.service.store, "save_recovery", side_effect=OSError("full")):
            with self.assertRaisesRegex(ContractError, "recovery_write_failed"):
                self.rename()
        self.assertEqual(self.call("editor.inspect"), before)

    def test_corrupt_recovery_is_identified_and_never_loaded(self):
        self.rename()
        file = next(self.recovery.glob("*.json"))
        record = json.loads(file.read_text(encoding="utf-8"))
        record["payload"]["draft"]["name"] = "篡改"
        file.write_text(json.dumps(record), encoding="utf-8")
        self.service = EditorService("backend.restarted", recovery_dir=self.recovery)
        listing = self.call("editor.recovery_list", unscoped=True)["records"]
        self.assertFalse(listing[0]["valid_record"])
        with self.assertRaisesRegex(ContractError, "recovery_invalid"):
            self.call("editor.recover", {"recovery_key": file.stem}, unscoped=True)

    def test_grants_are_scoped_one_shot_and_cannot_write_pack(self):
        token = self.bind()
        self.rename()
        with self.assertRaisesRegex(ContractError, "file_grant_expired"):
            self.save(token)
        with self.assertRaisesRegex(ContractError, "file_grant_expired"):
            self.save(token)
        with self.assertRaisesRegex(ContractError, "read_only_resource"):
            self.bind(self.service.root / "舰艇数据" / "禁止写入.json")
        with self.assertRaisesRegex(ContractError, "file_grant_expired"):
            self.save("../../file.json")

    def test_saved_file_stays_saved_when_recovery_cleanup_fails(self):
        self.rename()
        token = self.bind()
        with patch.object(self.service.store, "remove_recovery", side_effect=OSError("locked")):
            self.save(token)
        self.assertFalse(self.state["dirty"])
        self.assertIn("文件已保存", self.state["recovery_warning"])
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["name"], self.state["draft"]["name"])

    def test_new_version_cannot_overwrite_its_source_file(self):
        self.save(self.bind())
        original = self.path.read_bytes()
        with self.assertRaisesRegex(ContractError, "new_version_destination"):
            self.save(self.bind(), new=True)
        self.assertEqual(self.path.read_bytes(), original)

    def test_file_created_after_selection_is_not_overwritten(self):
        token = self.bind()
        self.path.write_text("external", encoding="utf-8")
        with self.assertRaisesRegex(ContractError, "file_conflict"):
            self.save(token)
        self.assertEqual(self.path.read_text(), "external")


if __name__ == "__main__":
    unittest.main(verbosity=2)
