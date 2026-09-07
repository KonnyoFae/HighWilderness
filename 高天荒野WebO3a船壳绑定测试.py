"""O3a: host-granted custom hull, immutable portable binding and draft recovery."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import unittest

from backend.high_wilderness_sidecar.sessions import EditorService
from backend.high_wilderness_sidecar import outfit_documents as documents
from 高天荒野舰艇数据契约 import ContractError, canonical_sha256


class HullBindingTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.service = EditorService("binding", recovery_dir=self.root / "recovery")
        self.state = None
        self.hull = deepcopy(next(s for d, s in self.service.index.resources.values()
            if d["kind"] == "HullBlueprint" and d["id"].endswith("minimum_legal")))
        self.hull["id"] = "user.hull.binding"
        self.hull["name"] = "玩家自建船壳"
        self.hull["decks"][0]["id"] = "user.deck.base"
        self.hull_path = self.root / "玩家船壳.json"
        self.write(self.hull_path, self.hull)

    @staticmethod
    def write(path, value):
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def call(self, method, params=None, unscoped=False):
        state = None if unscoped else self.state
        return self.service.dispatch(dict(method=method, params=params or {}, session_id=state["session_id"] if state else None,
            expected_revision=state["revision"] if state else None))[0]

    def grant(self, path, mode="open"):
        return self.call("editor.bind_file", dict(host_path=str(path), mode=mode), mode == "open")["destination_handle"]

    def create(self):
        self.state = self.call("editor.create", dict(kind="OutfitPlan", resource_id="user.outfit.binding", name="自建舾装", hull_handle=self.grant(self.hull_path)), True)

    def install_minimum(self):
        self.state = self.call("editor.command", dict(command="outfit.place_grid", arguments=dict(instance_id="cic", prototype=dict(id="gtw.module.fixture.cic", version=1),
            deck_id="user.deck.base", anchor_half_cell=[0, 0], rotation_deg=0)))
        tank = next(o["prototype"] for o in self.service.index.listing(include_modules=True)["module_options"] if o["prototype"]["category"] == "lift_fuel_tank")
        self.state = self.call("editor.command", dict(command="outfit.place_grid", arguments=dict(instance_id="lift", prototype={k: tank[k] for k in ("id", "version")},
            deck_id="user.deck.base", anchor_half_cell=[0, 4], rotation_deg=0)))

    def save(self, path, new_version=False):
        self.state = self.call("editor.save", dict(destination_handle=self.grant(path, "save"), new_version=new_version))

    def test_blank_custom_hull_and_cic_undo_redo(self):
        self.create()
        self.assertEqual(self.state["interface"], "gaotian.editor-session/v3alpha1")
        self.assertEqual(self.state["draft"]["modules"], [])
        self.assertTrue(self.state["dirty"])
        self.assertFalse(self.state["preview"]["valid"])
        self.assertIsNone(self.state["last_valid_preview"])
        self.assertEqual(self.state["hull_binding"]["hull_sha256"], canonical_sha256(self.hull))
        with self.assertRaises(ContractError):
            self.save(self.root / "invalid.json")
        self.assertFalse((self.root / "invalid.json").exists())
        self.install_minimum()
        self.assertTrue(self.state["preview"]["valid"])
        self.assertEqual(self.state["revision"], 2)
        self.state = self.call("editor.undo")
        self.assertFalse(self.state["preview"]["valid"])
        self.state = self.call("editor.redo")
        self.assertTrue(self.state["preview"]["valid"])

    def test_new_outfit_rejects_wrong_file_type_with_open_guidance(self):
        binding = documents.bind(self.hull, self.service.index)
        outfit = documents.blank("user.outfit.existing", "已有舾装", binding)
        portable = json.loads(documents.encode(outfit, binding))
        for source in (outfit, portable, {"report": "不是船壳"}, []):
            with self.subTest(source_type=type(source).__name__):
                self.write(self.hull_path, source)
                with self.assertRaises(ContractError) as raised:
                    self.create()
                self.assertEqual(raised.exception.code, "editor.hull_file_required")
                if source in (outfit, portable):
                    self.assertIn("打开文件", str(raised.exception))
                self.assertFalse(self.service.sessions)

    def test_actual_hull_missing_fields_still_report_schema_error(self):
        malformed = deepcopy(self.hull)
        del malformed["grid"]
        self.write(self.hull_path, malformed)
        with self.assertRaises(ContractError) as raised:
            self.create()
        self.assertEqual(raised.exception.code, "object.missing_keys")
        self.assertIn("grid", str(raised.exception))
        self.assertFalse(self.service.sessions)

    def test_portable_file_reopen_after_original_hull_moves_and_changes(self):
        self.create()
        self.install_minimum()
        original = deepcopy(self.state["preview"])
        binding = deepcopy(self.state["hull_binding"])
        changed = deepcopy(self.hull)
        changed["name"] = "外部另行修改"
        self.write(self.hull_path, changed)
        path = self.root / "舾装文档.json"
        self.save(path)
        value = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(value["interface"], documents.DOCUMENT_INTERFACE)
        self.assertEqual(value["hull_binding"], binding)
        self.assertNotIn(str(self.hull_path), path.read_text(encoding="utf-8"))
        self.hull_path.rename(self.root / "已移动船壳.json")
        self.call("editor.close", {"discard_changes": False})
        self.service = EditorService("restarted", recovery_dir=self.root / "recovery")
        self.state = None
        self.state = self.call("editor.open_file", dict(destination_handle=self.grant(path)), True)
        self.assertEqual(self.state["preview"], original)
        self.assertEqual(self.state["hull_binding"], binding)
        self.assertFalse(self.state["dirty"])
        self.save(self.root / "舾装新版本.json", True)
        self.assertEqual(self.state["draft"]["version"], 2)
        self.assertEqual(self.state["hull_binding"], binding)

    def test_blank_recovery_and_history_rebuild_without_original_file(self):
        self.create()
        blank = deepcopy(self.state)
        self.service = EditorService("blank-restart", recovery_dir=self.root / "recovery")
        self.state = None
        record = self.call("editor.recovery_list", unscoped=True)["records"][0]
        self.state = self.call("editor.recover", {"recovery_key": record["key"]}, True)
        self.assertEqual(self.state["preview"], blank["preview"])
        self.install_minimum()
        legal = deepcopy(self.state["preview"])
        self.state = self.call("editor.undo")
        self.hull_path.rename(self.root / "原船壳移走.json")
        self.service = EditorService("history-restart", recovery_dir=self.root / "recovery")
        self.state = None
        record = self.call("editor.recovery_list", unscoped=True)["records"][0]
        self.state = self.call("editor.recover", {"recovery_key": record["key"]}, True)
        self.assertFalse(self.state["preview"]["valid"])
        self.assertEqual(self.state["last_valid_preview"], legal)
        self.state = self.call("editor.redo")
        self.assertEqual(self.state["preview"], legal)

    def test_file_grant_race_replay_and_invalid_hull_do_not_create_sessions(self):
        token = self.grant(self.hull_path)
        changed = deepcopy(self.hull)
        changed["name"] = "已改变"
        self.write(self.hull_path, changed)
        params = dict(kind="OutfitPlan", resource_id="user.outfit.binding", name="test", hull_handle=token)
        with self.assertRaisesRegex(ContractError, "file_conflict"):
            self.call("editor.create", params, True)
        with self.assertRaisesRegex(ContractError, "file_grant_expired"):
            self.call("editor.create", params, True)
        self.assertFalse(self.service.sessions)
        changed["decks"] = []
        self.write(self.hull_path, changed)
        with self.assertRaises(ContractError):
            self.create()
        self.assertFalse(self.service.sessions)

    def test_embedded_content_reference_and_version_validation(self):
        self.create()
        self.install_minimum()
        path = self.root / "good.json"
        self.save(path)
        self.call("editor.close", {"discard_changes": False})
        self.state = None
        good = json.loads(path.read_text(encoding="utf-8"))
        corruptions = []
        bad = deepcopy(good); bad["hull_binding"]["hull"]["name"] = "改坏"; corruptions.append(bad)
        bad = deepcopy(good); bad["outfit"]["hull_blueprint"]["version"] = 99; corruptions.append(bad)
        bad = deepcopy(good); bad["interface"] = "future"; corruptions.append(bad)
        bad = deepcopy(good); bad["hull_binding"]["path"] = "do-not-read.json"; corruptions.append(bad)
        bad = deepcopy(good); bad["hull_binding"]["catalog_dependencies_sha256"] = "0" * 64; corruptions.append(bad)
        for bad in corruptions:
            self.write(self.root / "bad.json", bad)
            with self.assertRaises(ContractError):
                self.call("editor.open_file", {"destination_handle": self.grant(self.root / "bad.json")}, True)
            self.assertFalse(self.service.sessions)

    def test_same_identity_pack_hull_does_not_override_explicit_snapshot(self):
        pack = deepcopy(next(s for d, s in self.service.index.resources.values()
            if d["kind"] == "HullBlueprint" and d["id"].endswith("minimum_legal")))
        pack["name"] = "同版本不同内容的玩家副本"
        self.write(self.hull_path, pack)
        self.create()
        self.assertEqual(self.state["hull_binding"]["hull"]["name"], pack["name"])
        self.assertEqual(self.state["hull_binding"]["hull_sha256"], canonical_sha256(pack))

    def test_save_conflicts_and_no_overwrite_of_hull(self):
        self.create()
        self.install_minimum()
        before = self.hull_path.read_bytes()
        with self.assertRaisesRegex(ContractError, "hull_destination_conflict"):
            self.save(self.hull_path)
        self.assertEqual(self.hull_path.read_bytes(), before)
        path = self.root / "outfit.json"
        self.save(path)
        self.write(path, {"external": True})
        with self.assertRaisesRegex(ContractError, "file_conflict"):
            self.call("editor.save", dict(destination_handle=None, new_version=False))
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"external": True})

    def test_catalog_and_recovery_history_mismatch_rejected(self):
        self.create()
        self.install_minimum()
        changed = deepcopy(self.service.index)
        next(d for d, _ in changed.resources.values() if d["kind"] == "ModulePrototypeCatalog")["sha256"] = "0" * 64
        with patch("backend.high_wilderness_sidecar.sessions.ResourceIndex", return_value=changed):
            with self.assertRaisesRegex(ContractError, "save_dependencies_changed"):
                self.save(self.root / "changed.json")
        record_path = next((self.root / "recovery").glob("*.json"))
        record = json.loads(record_path.read_text(encoding="utf-8"))
        self.assertEqual(record["interface"], "gaotian.editor-recovery/v3alpha1")
        record["payload"]["undo"][0]["hull_blueprint"]["id"] = "other.hull"
        record["sha256"] = canonical_sha256(record["payload"])
        self.write(record_path, record)
        self.service = EditorService("restarted", recovery_dir=self.root / "recovery")
        self.state = None
        with self.assertRaisesRegex(ContractError, "hull_binding_mismatch"):
            self.call("editor.recover", {"recovery_key": record_path.stem}, True)
        self.assertFalse(self.service.sessions)


if __name__ == "__main__":
    unittest.main()
