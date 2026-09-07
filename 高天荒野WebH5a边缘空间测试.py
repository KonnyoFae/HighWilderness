"""H5a analytic geometry, legacy equivalence, and real editor state transitions."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from backend.high_wilderness_sidecar.sessions import EditorService, ResourceIndex
from 高天荒野舰艇编辑器领域层 import HullEditorDocument
from 高天荒野舰艇边缘空间 import build_deck_edge_space
from 高天荒野舰艇无界面船壳编译器 import (
    generate_strict_internal_cells, point_inside_polygon, polygon_area,
)


class EdgeSpaceTests(unittest.TestCase):
    def space(self, vertices):
        cells = generate_strict_internal_cells(tuple(vertices))
        return build_deck_edge_space([dict(id="r", vertices_m=vertices)], cells), cells

    def test_aligned_rectangle_zero_and_empty_rows(self):
        value, cells = self.space([[-7.5, -7.5], [7.5, -7.5], [7.5, 7.5], [-7.5, 7.5]])
        self.assertEqual(len(cells), 9)
        self.assertEqual(value["pieces"], [])
        self.assertEqual(value["gross_volume_m3"], 0)
        value, cells = self.space([[-7.5, -40], [7.5, -40], [7.5, 40], [-7.5, 40]])
        self.assertEqual(len(cells), 45)
        self.assertEqual(value["area_m2"], 75)
        self.assertEqual(value["gross_volume_m3"], 375)

    def test_diagonal_and_no_complete_cells(self):
        value, cells = self.space([[-7.5, -7.5], [7.5, -7.5], [12.5, 7.5], [-12.5, 7.5]])
        self.assertEqual(len(cells), 9)
        self.assertAlmostEqual(value["area_m2"], 75)
        value, cells = self.space([[0, 0], [2.5, 0], [0, 2.5]])
        self.assertFalse(cells)
        self.assertEqual(value["area_m2"], 3.125)

    def test_concave_scan_bands_preserve_empty_notch_and_disjoint_pieces(self):
        vertices = [[-10, -10], [10, -10], [10, 10], [5, 10], [5, 0], [-5, 0], [-5, 10], [-10, 10]]
        value, cells = self.space(vertices)
        self.assertAlmostEqual(value["area_m2"], 300 - len(cells) * 25)
        self.assertEqual(value, self.space(vertices)[0])
        # Independent point membership checks the actual partition, not only its total area.
        for ix in range(29):
            for iy in range(29):
                point = (-10.123 + ix * 0.731, -10.317 + iy * 0.727)
                occupied = any(abs(point[0] - x * 5) < 2.5 and abs(point[1] - y * 5) < 2.5 for x, y in cells)
                expected = point_inside_polygon(point, tuple(vertices)) and not occupied
                count = sum(point_inside_polygon(point, tuple(p["vertices_m"])) for p in value["pieces"])
                self.assertEqual(count, int(expected), point)

    def test_multideck_fixtures_and_legacy_preview_are_unchanged(self):
        index = ResourceIndex()
        expected_areas = {"minimum_legal": [75], "conventional_crewed": [775, 50], "unmanned_flagship": [75]}
        for descriptor, source in index.resources.values():
            if descriptor["kind"] != "HullBlueprint":
                continue
            document = HullEditorDocument(source, index.registry)
            text = document.canonical_text()
            legacy = document.preview().to_dict()
            new = document.preview(include_edge_space=True).to_dict()
            self.assertEqual(new["model"]["view_interface"], "gaotian.hull-editor-view/v2alpha1")
            self.assertEqual(document.canonical_text(), text)
            areas = expected_areas[descriptor["id"].split(".")[-1]]
            for deck, area in zip(new["model"]["decks"], areas):
                edge = deck.pop("edge_space")
                self.assertAlmostEqual(edge["area_m2"], area)
                self.assertAlmostEqual(sum(polygon_area(p["vertices_m"]) for p in edge["pieces"]), area)
                self.assertTrue({p["region_id"] for p in edge["pieces"]} <= {r["id"] for r in deck["regions"]})
            new["model"]["view_interface"] = legacy["model"]["view_interface"]
            self.assertEqual(new, legacy)

    def test_armor_does_not_change_gross_space(self):
        index = ResourceIndex()
        source = deepcopy(next(s for d, s in index.resources.values() if d["id"].endswith("minimum_legal") and d["kind"] == "HullBlueprint"))
        before = HullEditorDocument(source, index.registry).preview(include_edge_space=True).to_dict()
        for armor in source["decks"][0]["regions"][0]["edge_armor"]:
            armor["thickness_m"] = 0.1
        after = HullEditorDocument(source, index.registry).preview(include_edge_space=True).to_dict()
        self.assertEqual(before["model"]["decks"][0]["edge_space"], after["model"]["decks"][0]["edge_space"])
        self.assertNotEqual(before["model"]["derived"]["hull_mass_kg"], after["model"]["derived"]["hull_mass_kg"])

    def test_history_recovery_and_file_reload_rebuild_the_new_view(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            service = EditorService("h5a", recovery_dir=root / "recovery")
            state = None

            def call(method, params=None, unscoped=False):
                return service.dispatch(dict(method=method, params=params or {},
                    session_id=None if unscoped else state["session_id"],
                    expected_revision=None if unscoped else state["revision"]))[0]

            key = next(d["key"] for d, _ in service.index.resources.values()
                       if d["kind"] == "HullBlueprint" and d["id"].endswith("minimum_legal"))
            state = call("editor.open", {"resource_key": key}, True)
            original = deepcopy(state["preview"])
            state = call("editor.command", {"command": "hull.move_vertex", "arguments": {
                "deck_id": "deck.0", "region_id": "deck.0.region.0", "vertex_index": 2, "point_m": [10, 40]}})
            self.assertFalse(state["preview"]["valid"])
            self.assertNotIn("decks", state["preview"]["model"])
            self.assertEqual(state["last_valid_preview"], original)
            service = EditorService("h5a-restarted", recovery_dir=root / "recovery")
            record = call("editor.recovery_list", unscoped=True)["records"][0]
            state = call("editor.recover", {"recovery_key": record["key"]}, True)
            self.assertFalse(state["preview"]["valid"])
            self.assertEqual(state["last_valid_preview"], original)
            state = call("editor.undo")
            self.assertEqual(state["preview"], original)
            handle = call("editor.bind_file", {"host_path": str(root / "hull.json"), "mode": "save"})["destination_handle"]
            state = call("editor.save", {"destination_handle": handle, "new_version": False})
            call("editor.close", {"discard_changes": False})
            handle = call("editor.bind_file", {"host_path": str(root / "hull.json"), "mode": "open"}, True)["destination_handle"]
            state = call("editor.open_file", {"destination_handle": handle}, True)
            self.assertEqual(state["preview"], original)
            self.assertFalse(state["dirty"])


if __name__ == "__main__":
    unittest.main()
