"""Partial layout stays usable through invalid drafts; compiled geometry remains authoritative."""
from copy import deepcopy
import unittest
from backend.high_wilderness_sidecar.sessions import EditorService
from backend.high_wilderness_sidecar.outfits import document


class OutfitCanvasTests(unittest.TestCase):
    def setUp(self):
        self.service = EditorService("canvas-test")
        self.source = deepcopy(next(s for d, s in self.service.index.resources.values()
            if d["kind"] == "OutfitPlan" and d["id"].endswith("conventional_crewed")))

    def preview(self):
        p = self.service.preview(self.source)
        return p, p["model"]["layout"]

    def test_legal_geometry_matches_compiler_for_all_four_mounts(self):
        preview, layout = self.preview()
        self.assertTrue(preview["valid"])
        compiled = document(self.source, self.service.index).compile()
        self.assertEqual(layout["modules"], [m.to_dict() for m in compiled.instances])
        self.assertEqual({m["placement_kind"] for m in layout["modules"]}, {"grid", "side", "hosted"})
        self.assertTrue(any(m["top_cells"] for m in layout["modules"]))
        self.assertEqual(layout["conflicts"], [])
        self.assertEqual(layout["errors"], [])

    def test_empty_outfit_keeps_hull_and_installation_cells(self):
        self.source["modules"] = []
        preview, layout = self.preview()
        self.assertFalse(preview["valid"])
        self.assertNotIn("derived", preview["model"])
        self.assertEqual(layout["modules"], [])
        self.assertTrue(layout["hull"]["decks"])
        self.assertTrue(layout["decks"][0]["internal_cells"])

    def test_unavailable_grid_module_does_not_hide_other_modules(self):
        cargo = next(m for m in self.source["modules"] if m["id"] == "cargo_hold")
        cargo["placement"]["anchor_half_cell"] = [1000, 1000]
        preview, layout = self.preview()
        self.assertFalse(preview["valid"])
        self.assertIn("cargo_hold", [e["instance_id"] for e in layout["errors"]])
        self.assertIn("cic", [m["id"] for m in layout["modules"]])
        self.assertEqual(preview["model"]["canonical_resource"]["modules"], self.source["modules"])

    def test_overlap_reports_both_modules_and_retains_both_shapes(self):
        cargo = next(m for m in self.source["modules"] if m["id"] == "cargo_hold")
        duplicate = deepcopy(cargo)
        duplicate["id"] = "duplicate_cargo"
        self.source["modules"].append(duplicate)
        preview, layout = self.preview()
        self.assertFalse(preview["valid"])
        self.assertTrue(any(set(c["instance_ids"]) == {"cargo_hold", "duplicate_cargo"} for c in layout["conflicts"]))
        self.assertIn("duplicate_cargo", [m["id"] for m in layout["modules"]])

    def test_missing_host_keeps_remaining_layout(self):
        next(m for m in self.source["modules"] if m["id"] == "remote_core")["placement"]["host_instance_id"] = "missing"
        preview, layout = self.preview()
        self.assertFalse(preview["valid"])
        self.assertIn("remote_core", [e["instance_id"] for e in layout["errors"]])
        self.assertGreater(len(layout["modules"]), 1)

    def test_snapshot_command_undo_and_redo_keep_current_layout(self):
        key = next(d["key"] for d, s in self.service.index.resources.values() if s == self.source)
        state = self.service.dispatch(dict(method="editor.open", params={"resource_key": key}, session_id=None, expected_revision=None))[0]
        def call(method, params):
            return self.service.dispatch(dict(method=method, params=params, session_id=state["session_id"], expected_revision=state["revision"]))[0]
        original = deepcopy(state["preview"]["model"]["layout"])
        state = call("editor.command", dict(command="outfit.move_grid", arguments=dict(instance_id="cargo_hold", deck_id="deck.0", anchor_half_cell=[1000, 1000], rotation_deg=0)))
        self.assertFalse(state["preview"]["valid"])
        self.assertTrue(state["preview"]["model"]["layout"]["errors"])
        state = call("editor.undo", {})
        self.assertEqual(state["preview"]["model"]["layout"], original)
        state = call("editor.redo", {})
        self.assertTrue(state["preview"]["model"]["layout"]["errors"])


if __name__ == "__main__":
    unittest.main()
