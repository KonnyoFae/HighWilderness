"""O3c group partition, compatibility, atomic edits and portable persistence."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from backend.high_wilderness_sidecar.sessions import EditorService
from backend.high_wilderness_sidecar.outfits import document, module_catalog
from backend.high_wilderness_sidecar import outfit_documents
from 高天荒野舰艇数据契约 import ContractError, ModulePrototypeCatalog, OutfitPlanInput, canonical_sha256, OUTFIT_PLAN_V2_SCHEMA_ID
from 高天荒野舰艇武器组 import resolve_weapon_group, weapon_groups


class WeaponGroupTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.service = EditorService("groups", recovery_dir=self.root / "recovery")
        self.state = None
        key = next(d["key"] for d, s in self.service.index.resources.values() if d["kind"] == "OutfitPlan" and d["id"].endswith("conventional_crewed"))
        self.state = self.call("editor.open", {"resource_key": key})

    def call(self, method, params=None, unscoped=False):
        state = None if unscoped else self.state
        return self.service.dispatch(dict(method=method, params=params or {}, session_id=state["session_id"] if state else None,
            expected_revision=state["revision"] if state else None))[0]

    def command(self, operation, **args):
        self.state = self.call("editor.command", dict(command="outfit." + operation, arguments=args))

    def controls(self):
        return self.state["preview"]["model"]["weapon_control"]

    def add_weapon(self, id="weapon_second", anchor=None):
        self.command("place_grid", instance_id=id, prototype=dict(id="gtw.module.fixture.weapon", version=1),
            deck_id="deck.1", anchor_half_cell=anchor or [2, -4], rotation_deg=0)

    def split(self):
        self.add_weapon()
        group = deepcopy(self.controls()["groups"][0])
        other = dict(deepcopy(group), id="group.second", name="第二炮组", weapon_instance_ids=["weapon_second"])
        group["weapon_instance_ids"] = ["weapon_upper_port"]
        self.command("set_weapon_groups", groups=[group, other])

    def test_default_group_is_nonmutating_and_v1_roundtrip_unchanged(self):
        source = deepcopy(self.state["draft"])
        old_preview = document(source, self.service.index).preview().to_dict()
        self.assertNotIn("weapon_groups", source)
        self.assertEqual(OutfitPlanInput.parse(source).to_dict(), source)
        self.assertEqual(self.controls()["grouping"], "automatic")
        self.assertEqual(self.controls()["groups"][0]["weapon_instance_ids"], ["weapon_upper_port"])
        self.assertEqual(document(source, self.service.index).preview().to_dict(), old_preview)
        self.assertEqual(self.state["draft"], source)
        for schema in [[], {}, "future"]:
            with self.assertRaises(ContractError):
                OutfitPlanInput.parse(dict(source, schema=schema))
        with self.assertRaises(ContractError):
            OutfitPlanInput.parse(dict(source, schema=OUTFIT_PLAN_V2_SCHEMA_ID))

    def test_split_upgrade_single_revision_undo_redo_and_resolve(self):
        self.add_weapon()
        before = deepcopy(self.state)
        group = deepcopy(self.controls()["groups"][0])
        other = dict(deepcopy(group), id="group.second", name="第二炮组", weapon_instance_ids=["weapon_second"])
        group["weapon_instance_ids"] = ["weapon_upper_port"]
        self.command("set_weapon_groups", groups=[group, other])
        self.assertTrue(self.state["preview"]["valid"], self.state["preview"]["diagnostics"])
        self.assertEqual(self.state["draft"]["schema"], OUTFIT_PLAN_V2_SCHEMA_ID)
        self.assertEqual(self.state["revision"], before["revision"] + 1)
        self.assertEqual(resolve_weapon_group(OutfitPlanInput.parse(self.state["draft"]), module_catalog(self.service.index), "group.second"), ("weapon_second",))
        after = deepcopy(self.state["preview"])
        self.state = self.call("editor.undo")
        self.assertEqual(self.state["draft_sha256"], before["draft_sha256"])
        self.assertNotIn("weapon_groups", self.state["draft"])
        self.state = self.call("editor.redo")
        self.assertEqual(self.state["preview"], after)

    def test_long_catalog_name_does_not_break_legacy_default_view(self):
        catalog = module_catalog(self.service.index).to_dict()
        next(m for m in catalog["modules"] if m["id"] == "gtw.module.fixture.weapon")["name"] = "炮" * 100
        plan = OutfitPlanInput.parse(self.state["draft"])
        groups = weapon_groups(plan, ModulePrototypeCatalog.parse(catalog))
        self.assertEqual(groups[0].name, "炮" * 80)
        self.assertIsNone(plan.weapon_groups)

    def test_bad_partitions_and_names_are_atomic(self):
        self.split()
        before = self.call("editor.inspect")
        good = self.controls()["groups"]
        cases = [[], good[:1]]
        for key, value in [("weapon_instance_ids", []), ("weapon_instance_ids", ["missing"]), ("weapon_instance_ids", ["cic"]),
                           ("weapon_instance_ids", ["weapon_second", "weapon_second"]), ("name", " "), ("name", "x" * 81),
                           ("prototype", dict(id="gtw.module.fixture.weapon", version=99)), ("prototype", dict(id="gtw.module.fixture.cic", version=1))]:
            bad = deepcopy(good)
            bad[0][key] = value
            cases.append(bad)
        bad = deepcopy(good); bad[1]["id"] = bad[0]["id"]; cases.append(bad)
        bad = deepcopy(good); bad[1]["weapon_instance_ids"] = bad[0]["weapon_instance_ids"]; cases.append(bad)
        for groups in cases:
            with self.assertRaises(ContractError):
                self.command("set_weapon_groups", groups=groups)
            self.assertEqual(self.call("editor.inspect"), before)

    def test_add_and_remove_reconcile_without_orphans(self):
        self.split()
        self.add_weapon("weapon_third", [2, 4])
        groups = self.controls()["groups"]
        self.assertEqual(len(groups), 2)
        self.assertEqual(sorted(m for g in groups for m in g["weapon_instance_ids"]), ["weapon_second", "weapon_third", "weapon_upper_port"])
        self.command("remove", instance_id="weapon_upper_port")
        self.assertTrue(all("weapon_upper_port" not in g["weapon_instance_ids"] for g in self.controls()["groups"]))
        self.command("remove", instance_id="weapon_third")
        self.command("remove", instance_id="weapon_second")
        self.assertEqual(self.controls()["groups"], [])
        self.state = self.call("editor.undo")
        self.assertEqual(self.controls()["groups"][0]["weapon_instance_ids"], ["weapon_second"])

    def test_invalid_placement_and_conflict_never_show_clear_arc(self):
        self.add_weapon(anchor=[-2, -4])
        self.assertFalse(self.state["preview"]["valid"])
        self.assertEqual({a["status"] for a in self.controls()["arcs"]}, {"placement_invalid"})
        self.command("move_grid", instance_id="weapon_second", deck_id="deck.1", anchor_half_cell=[1000, 1000], rotation_deg=0)
        self.assertEqual(next(a for a in self.controls()["arcs"] if a["instance_id"] == "weapon_second")["status"], "placement_invalid")
        groups = deepcopy(self.controls()["groups"]); groups[0]["name"] = "非法安装仍可分组"
        self.command("set_weapon_groups", groups=groups)
        self.assertFalse(self.state["preview"]["valid"])
        self.assertEqual(self.controls()["grouping"], "explicit")

    def test_upper_deck_arc_policy_is_conservative(self):
        doc = document(self.state["draft"], self.service.index)
        layout = doc.layout_preview()
        weapon = next(m for m in layout["modules"] if m["id"] == "weapon_upper_port")
        self.assertEqual(self.controls()["arcs"][0]["blocked_intervals_deg"], [])
        weapon["base_deck_level"] = 0  # Isolated preview policy input, not a legal placement claim.
        self.assertEqual(doc.weapon_control_preview(layout)["arcs"][0]["blocked_intervals_deg"], [[0.0, 360.0]])

    def test_save_reopen_and_portable_binding_preserve_groups(self):
        self.split()
        for bound in [False, True]:
            source = deepcopy(self.state["draft"])
            if bound:
                hull = next(s for d, s in self.service.index.resources.values() if d["kind"] == "HullBlueprint" and d["id"] == source["hull_blueprint"]["id"])
                binding = outfit_documents.bind(hull, self.service.index)
                path = self.root / "portable.json"
                path.write_text(outfit_documents.encode(source, binding), encoding="utf-8")
                token = self.call("editor.bind_file", dict(host_path=str(path), mode="open"), True)["destination_handle"]
                self.state = self.call("editor.open_file", dict(destination_handle=token), True)
            path = self.root / ("bound-saved.json" if bound else "saved.json")
            token = self.call("editor.bind_file", dict(host_path=str(path), mode="save"))["destination_handle"]
            self.state = self.call("editor.save", dict(destination_handle=token, new_version=bound))
            saved = deepcopy(self.state["preview"])
            self.call("editor.close", dict(discard_changes=False))
            token = self.call("editor.bind_file", dict(host_path=str(path), mode="open"), True)["destination_handle"]
            self.state = self.call("editor.open_file", dict(destination_handle=token), True)
            self.assertEqual(self.state["preview"], saved)
            self.assertEqual(self.state["draft"]["weapon_groups"], source["weapon_groups"])
            if bound:
                self.assertEqual(self.state["hull_binding"], binding)
                self.assertEqual(self.state["draft"]["version"], source["version"] + 1)

    def test_restart_recovery_and_tampered_history(self):
        self.split()
        expected = deepcopy(self.state["preview"])
        self.command("rename", name="恢复武器组")
        self.service = EditorService("restart", recovery_dir=self.root / "recovery")
        record = self.call("editor.recovery_list", unscoped=True)["records"][0]
        self.state = self.call("editor.recover", {"recovery_key": record["key"]}, True)
        self.state = self.call("editor.undo")
        self.assertEqual(self.state["preview"], expected)
        self.state = self.call("editor.redo")
        path = self.service.store.recovery_path(record["key"])
        value = json.loads(path.read_text(encoding="utf-8"))
        value["payload"]["undo"][-1]["weapon_groups"][0]["weapon_instance_ids"] = ["cic"]
        value["sha256"] = canonical_sha256(value["payload"])
        path.write_text(json.dumps(value), encoding="utf-8")
        self.service = EditorService("tampered", recovery_dir=self.root / "recovery")
        with self.assertRaises(ContractError):
            self.call("editor.recover", {"recovery_key": record["key"]}, True)
        self.assertFalse(self.service.sessions)


if __name__ == "__main__":
    unittest.main()
