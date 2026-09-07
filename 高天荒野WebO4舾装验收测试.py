"""O4 service acceptance sequences; these tests do not count as desktop UI acceptance."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from backend.high_wilderness_sidecar.sessions import EditorService
from backend.high_wilderness_sidecar.outfits import module_catalog
from 高天荒野舰艇数据契约 import ContractError
from 高天荒野舰艇无界面舾装编译器 import effective_external_rcs_m2
from tools.verify_outfit_ui_roundtrip import TECHNICAL_SHIPS, compare_file, verify


class OutfitAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.service = EditorService("o4", recovery_dir=self.root / "recovery")
        self.state = None

    def call(self, method, params=None, unscoped=False):
        s = None if unscoped else self.state
        return self.service.dispatch(dict(method=method, params=params or {}, session_id=s["session_id"] if s else None,
                                          expected_revision=s["revision"] if s else None))[0]

    def open_ship(self, suffix="conventional_crewed"):
        key = next(d["key"] for d, _ in self.service.index.resources.values()
                   if d["kind"] == "OutfitPlan" and d["id"].endswith(suffix))
        self.state = self.call("editor.open", {"resource_key": key}, True)

    def command(self, command_name, **args):
        self.state = self.call("editor.command", dict(command="outfit." + command_name, arguments=args))

    def save(self, name):
        path = self.root / name
        token = self.call("editor.bind_file", dict(host_path=str(path), mode="save"))["destination_handle"]
        self.state = self.call("editor.save", dict(destination_handle=token, new_version=False))
        return path

    def reopen(self, path):
        self.call("editor.close", {"discard_changes": False})
        self.state = None
        token = self.call("editor.bind_file", dict(host_path=str(path), mode="open"))["destination_handle"]
        self.state = self.call("editor.open_file", dict(destination_handle=token))

    def test_three_ships_save_reopen_exact_full_preview(self):
        for slug, suffix in TECHNICAL_SHIPS.items():
            with self.subTest(ship=slug):
                self.open_ship(suffix)
                before = deepcopy(self.state)
                path = self.save(slug + ".json")
                self.reopen(path)
                self.assertEqual(self.state["draft"], before["draft"])
                self.assertEqual(self.state["preview"], before["preview"])
                self.assertFalse(self.state["dirty"])
                self.call("editor.close", {"discard_changes": False})
                self.state = None
        report = verify(self.root)
        self.assertEqual(report["status"], "PASS")
        self.assertFalse(report["o4_ui_gate_certified_by_this_script"])
        # A valid but changed design must fail the unmodified roundtrip gate.
        path = self.root / "minimum.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["name"] += "改动"
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(verify(self.root)["status"], "FAIL")

    def test_missing_ui_artifacts_do_not_pass(self):
        self.assertEqual(verify(self.root)["status"], "NOT_COVERED")

    def test_external_rcs_defaults_cover_all_external_prototypes(self):
        external = []
        for prototype in module_catalog(self.service.index).modules:
            geometry = prototype.installation
            if geometry.top_footprint_half_cells or geometry.side_external_footprint_half_cells:
                external.append(prototype.reference.id)
                self.assertEqual(effective_external_rcs_m2(replace(prototype, base_external_rcs_m2=None)), 1.0)
                self.assertEqual(effective_external_rcs_m2(replace(prototype, base_external_rcs_m2=7.5)), 7.5)
                self.assertEqual(effective_external_rcs_m2(replace(prototype, base_external_rcs_m2=0.0)), 0.0)
            else:
                self.assertEqual(effective_external_rcs_m2(prototype), 0.0)
        self.assertEqual(len(external), 6)

    def test_default_external_rcs_survives_three_ship_save_and_reopen(self):
        for slug, suffix in TECHNICAL_SHIPS.items():
            with self.subTest(ship=slug):
                self.open_ship(suffix)
                preview = deepcopy(self.state["preview"])
                rcs = preview["model"]["derived"]["rcs"]
                external_count = sum(bool(m["top_cells"] or m["placement_kind"] == "side")
                                     for m in preview["model"]["layout"]["modules"])
                self.assertGreater(external_count, 0)
                self.assertEqual(rcs["known_external_rcs_m2"], float(external_count))
                self.assertEqual(rcs["unresolved_external_rcs_instances"], [])
                self.assertNotIn("outfit.external_rcs_unresolved", [d["code"] for d in preview["diagnostics"]])
                self.reopen(self.save(slug + "-default-rcs.json"))
                self.assertEqual(self.state["preview"], preview)
                self.call("editor.close", {"discard_changes": False})
                self.state = None

    def test_four_mounts_remove_replace_save_reopen_geometry(self):
        self.open_ship()
        before = deepcopy(self.state["preview"])
        for name in ("cargo_hold", "sensor_upper_starboard", "thruster_port_fore", "remote_core"):
            with self.subTest(module=name):
                module = deepcopy(next(m for m in self.state["draft"]["modules"] if m["id"] == name))
                self.command("remove", instance_id=name)
                placement = module["placement"]
                kind = placement.pop("kind")
                self.command("place_" + kind, instance_id=name, prototype=module["prototype"], **placement)
                self.assertEqual(self.state["preview"], before)
                path = self.save(name + ".json")
                self.reopen(path)
                self.assertEqual(self.state["preview"], before)

    def test_invalid_overlap_save_rejected_repair_and_restart_history(self):
        self.open_ship()
        original = deepcopy(self.state["preview"])
        self.command("move_grid", instance_id="cargo_hold", deck_id="deck.0", anchor_half_cell=[-2, 4], rotation_deg=0)
        self.assertFalse(self.state["preview"]["valid"])
        self.assertTrue(any(set(c["instance_ids"]) == {"cargo_hold", "generator"}
                            for c in self.state["preview"]["model"]["layout"]["conflicts"]))
        with self.assertRaises(ContractError):
            self.save("invalid.json")
        self.assertFalse((self.root / "invalid.json").exists())
        self.command("move_grid", instance_id="cargo_hold", deck_id="deck.0", anchor_half_cell=[2, 6], rotation_deg=90)
        repaired = deepcopy(self.state["preview"])
        self.assertTrue(repaired["valid"])
        self.service = EditorService("o4-restart", recovery_dir=self.root / "recovery")
        self.state = None
        records = self.call("editor.recovery_list")["records"]
        self.assertEqual(len(records), 1)
        self.state = self.call("editor.recover", {"recovery_key": records[0]["key"]})
        self.assertEqual(self.state["preview"], repaired)
        self.state = self.call("editor.undo")
        self.assertFalse(self.state["preview"]["valid"])
        self.state = self.call("editor.undo")
        self.assertEqual(self.state["preview"], original)
        self.state = self.call("editor.redo")
        self.state = self.call("editor.redo")
        path = self.save("repaired.json")
        self.reopen(path)
        self.assertEqual(self.state["preview"], repaired)

    def test_side_move_group_and_cross_deck_weapon_persist_together(self):
        self.open_ship()
        self.command("move_side", instance_id="thruster_port_fore", deck_id="deck.0", region_id="deck.0.region.0",
                     edge_index=6, start_slot_index=6, rotation_deg=180)
        self.command("place_grid", instance_id="second_weapon", prototype=dict(id="gtw.module.fixture.weapon", version=1),
                     deck_id="deck.1", anchor_half_cell=[2, -4], rotation_deg=0)
        groups = deepcopy(self.state["preview"]["model"]["weapon_control"]["groups"])
        extra = dict(deepcopy(groups[0]), id="o4.second", name="后部炮组", weapon_instance_ids=["second_weapon"])
        groups[0]["weapon_instance_ids"] = ["weapon_upper_port"]
        self.command("set_weapon_groups", groups=groups + [extra])
        expected = deepcopy(self.state["preview"])
        self.assertTrue(expected["valid"])
        self.assertEqual({a["base_deck_level"] for a in expected["model"]["weapon_control"]["arcs"]}, {1})
        self.state = self.call("editor.undo")
        self.assertEqual(self.state["preview"]["model"]["weapon_control"]["grouping"], "automatic")
        self.state = self.call("editor.redo")
        path = self.save("grouped.json")
        self.reopen(path)
        self.assertEqual(self.state["preview"], expected)
        compare_file(self.service, path, self.state["draft"])

    def open_legacy_draft(self, *, bad_side=False, orphan=False):
        source = deepcopy(next(s for d, s in self.service.index.resources.values()
                               if d["kind"] == "OutfitPlan" and d["id"].endswith("conventional_crewed")))
        if bad_side:
            source["modules"].append(dict(id="bad_side", prototype=dict(id="gtw.module.fixture.maneuver_thruster", version=1),
                placement=dict(kind="side", deck_id="deck.0", region_id="deck.0.region.0", edge_index=6, start_slot_index=8, rotation_deg=0)))
        if orphan:
            next(m for m in source["modules"] if m["id"] == "remote_core")["placement"]["host_instance_id"] = "old_cic"
        # Old versions allowed these edits into recovery, not into saved legal files.
        self.open_ship()
        self.command("rename", name="旧版恢复记录夹具")
        record = self.service.store.list_recovery()[0]
        payload = self.service.store.read_recovery(record["key"])
        payload["draft"] = source
        self.service.store.save_recovery(record["key"], payload)
        self.service = EditorService("legacy-restart", recovery_dir=self.root / "recovery")
        self.state = None
        self.state = self.call("editor.recover", dict(recovery_key=record["key"]))

    def test_bad_side_placement_is_atomic_and_valid_side_still_works(self):
        self.open_ship()
        before = deepcopy(self.state)
        args = dict(instance_id="new_side", prototype=dict(id="gtw.module.fixture.maneuver_thruster", version=1),
                    deck_id="deck.0", region_id="deck.0.region.0", edge_index=6, start_slot_index=8, rotation_deg=0)
        with self.assertRaises(ContractError) as raised:
            self.command("place_side", **args)
        self.assertEqual(raised.exception.code, "outfit.side_clearance_hull_conflict")
        self.assertEqual(self.call("editor.inspect"), before)
        self.assertEqual(self.call("editor.recovery_list", unscoped=True)["records"], [])
        self.command("place_side", **dict(args, rotation_deg=180))
        self.assertTrue(self.state["preview"]["valid"])
        before = deepcopy(self.state)
        with self.assertRaises(ContractError):
            self.command("place_side", **dict(args, instance_id="overlap", rotation_deg=180))
        self.assertEqual(self.call("editor.inspect"), before)

    def test_invalid_old_side_does_not_block_other_side_move_or_its_own_repair(self):
        self.open_legacy_draft(bad_side=True)
        self.assertFalse(self.state["preview"]["valid"])
        self.command("move_side", instance_id="thruster_port_fore", deck_id="deck.0", region_id="deck.0.region.0",
                     edge_index=6, start_slot_index=6, rotation_deg=180)
        self.assertEqual([e["instance_id"] for e in self.state["preview"]["model"]["layout"]["errors"]], ["bad_side"])
        self.command("move_side", instance_id="bad_side", deck_id="deck.0", region_id="deck.0.region.0",
                     edge_index=6, start_slot_index=8, rotation_deg=180)
        expected = deepcopy(self.state["preview"])
        self.assertTrue(expected["valid"])
        path = self.save("fixed-side.json")
        self.reopen(path)
        self.assertEqual(self.state["preview"], expected)

    def test_cic_removal_removes_hosted_children_and_undo_restores_both(self):
        self.open_ship()
        before = deepcopy(self.state)
        cic = deepcopy(next(m for m in before["draft"]["modules"] if m["id"] == "cic"))
        core = deepcopy(next(m for m in before["draft"]["modules"] if m["id"] == "remote_core"))
        self.command("remove", instance_id="cic")
        self.assertEqual(len(self.state["draft"]["modules"]), 16)
        self.assertFalse(any(m["id"] in {"cic", "remote_core"} for m in self.state["draft"]["modules"]))
        self.assertEqual(self.state["revision"], before["revision"] + 1)
        self.state = self.call("editor.undo")
        self.assertEqual(self.state["preview"], before["preview"])
        self.state = self.call("editor.redo")
        placement = dict(cic["placement"]); placement.pop("kind")
        self.command("place_grid", instance_id="cic.0", prototype=cic["prototype"], **placement)
        self.command("place_hosted", instance_id="remote_core.0", prototype=core["prototype"], host_instance_id="cic.0")
        expected = deepcopy(self.state["preview"])
        self.assertTrue(expected["valid"])
        path = self.save("new-cic.json")
        self.reopen(path)
        self.assertEqual(self.state["preview"], expected)

    def test_old_orphan_can_rehost_while_unrelated_side_is_invalid(self):
        self.open_legacy_draft(bad_side=True, orphan=True)
        self.command("rehost", instance_id="remote_core", host_instance_id="cic")
        errors = self.state["preview"]["model"]["layout"]["errors"]
        self.assertEqual([e["instance_id"] for e in errors], ["bad_side"])
        before = deepcopy(self.state)
        with self.assertRaises(ContractError):
            self.command("place_hosted", instance_id="duplicate_core", prototype=dict(id="gtw.module.fixture.remote_core", version=1), host_instance_id="cic")
        self.assertEqual(self.call("editor.inspect"), before)

    def test_custom_blank_hull_allows_side_install_before_ship_is_ready(self):
        hull = deepcopy(next(s for d, s in self.service.index.resources.values()
                             if d["kind"] == "HullBlueprint" and d["id"].endswith("minimum_legal")))
        hull["id"] = "user.hull.o4.side"
        path = self.root / "custom-hull.json"
        path.write_text(json.dumps(hull), encoding="utf-8")
        token = self.call("editor.bind_file", dict(host_path=str(path), mode="open"))["destination_handle"]
        self.state = self.call("editor.create", dict(kind="OutfitPlan", resource_id="user.outfit.o4.side", name="自建侧挂验收", hull_handle=token))
        source = next(s for d, s in self.service.index.resources.values() if d["kind"] == "OutfitPlan" and d["id"].endswith("minimum_legal"))
        # Place all four port/starboard thrusters first, before CIC, lift and other readiness requirements.
        for m in sorted(source["modules"], key=lambda m: m["placement"]["kind"] != "side"):
            placement = dict(m["placement"]); kind = placement.pop("kind")
            self.command("place_" + kind, instance_id=m["id"], prototype=m["prototype"], **placement)
        expected = deepcopy(self.state["preview"])
        self.assertTrue(expected["valid"])
        self.assertEqual(len([m for m in self.state["draft"]["modules"] if m["placement"]["kind"] == "side"]), 4)
        path = self.save("custom-outfit.json")
        self.reopen(path)
        self.assertEqual(self.state["preview"], expected)

    def test_recovery_record_creation_update_and_clear_lifecycle(self):
        self.open_ship()
        records = self.service.store.list_recovery
        self.assertEqual(records(), [])
        self.command("move_grid", instance_id="cargo_hold", deck_id="deck.0", anchor_half_cell=[-2, 4], rotation_deg=0)
        self.assertFalse(self.state["preview"]["valid"])
        self.assertEqual(records()[0]["revision"], self.state["revision"])
        key = records()[0]["key"]
        self.assertEqual(self.call("editor.recovery_list", unscoped=True)["records"], [])
        for n in range(3):
            self.command("rename", name=f"同一草稿第 {n} 次修改")
            self.assertEqual(len(records()), 1)
            self.assertEqual(records()[0]["key"], key)
        for _ in range(3):
            self.state = self.call("editor.undo")
        self.state = self.call("editor.undo")
        self.assertEqual(records(), [])
        self.command("move_grid", instance_id="cargo_hold", deck_id="deck.0", anchor_half_cell=[2, 6], rotation_deg=0)
        self.assertEqual(len(records()), 1)
        self.save("recovery-lifecycle.json")
        self.assertEqual(records(), [])
        self.state = self.call("editor.undo")
        self.assertEqual(len(records()), 1)
        self.call("editor.close", {"discard_changes": True})
        self.state = None
        self.assertEqual(records(), [])


if __name__ == "__main__":
    unittest.main()
