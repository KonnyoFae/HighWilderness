"""Recheck files saved through the H4 desktop UI; this does not drive the UI."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.high_wilderness_sidecar.sessions import ResourceIndex
from 高天荒野舰艇编辑器领域层 import HullEditorDocument


def digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def verify(directory: Path) -> dict:
    index = ResourceIndex()
    cases = []
    sources = {}
    for slug, suffix in (("conventional", "conventional_crewed"),
                         ("minimum", "minimum_legal"),
                         ("unmanned", "unmanned_flagship")):
        resource_id = "gtw.hull.fixture.stage_f." + suffix
        descriptor, source = next((d, s) for d, s in index.resources.values()
                                  if d["id"] == resource_id)
        sources[slug] = source
        path = directory / (slug + ".json")
        expected = HullEditorDocument(source, index.registry).preview().to_dict()
        actual = HullEditorDocument.load(path, index.registry).preview().to_dict()
        require(actual["valid"] and actual == expected,
                f"{slug}: complete authoritative preview differs from source")
        model = actual["model"]
        cases.append({
            "case": slug, "status": "PASS", "artifact": path.name,
            "resource_id": descriptor["id"], "version": descriptor["version"],
            "file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "source_sha256": model["source_sha256"],
            "full_preview_sha256": digest(actual),
            "compared": ["canonical_resource", "decks_and_installation_space",
                         "complete_derived_including_aerodynamics_and_rcs", "source_sha256"],
            "deck_count": len(model["decks"]),
            "hull_mass_kg": model["derived"]["hull_mass_kg"],
        })

    # Independent expected geometry for the observed UI operation sequence:
    # move (7.5, 40) to (10, 40), armor its outgoing bow edge, reflect right side.
    expected_source = deepcopy(sources["minimum"])
    region = expected_source["decks"][0]["regions"][0]
    armor = deepcopy(region["edge_armor"][0])
    region["vertices_m"] = [[-10, 40], [-7.5, -40], [0, -40],
                            [7.5, -40], [10, 40], [0, 40]]
    region["edge_armor"] = [deepcopy(armor) for _ in range(6)]
    for edge in region["edge_armor"][4:]:
        edge["thickness_m"] = 0.1
    expected = HullEditorDocument(expected_source, index.registry).preview().to_dict()
    edited_path = directory / "edited.json"
    actual = HullEditorDocument.load(edited_path, index.registry).preview().to_dict()
    require(actual["valid"] and actual == expected,
            "edited: mirrored geometry or corresponding edge armor differs")
    derived = actual["model"]["derived"]
    require(derived["base_armor_mass_kg"] == 78500.0, "bow armor mass mismatch")
    require(derived["structure_mass_kg"] == 1099000.0, "trapezoid structure mass mismatch")
    return {
        "report": "gaotian.web-h4-saved-artifact-verification/v1",
        "status": "PASS", "scope": "saved_files_recompiled_by_authoritative_domain",
        "ui_evidence": "contracts/web_bridge/h4-hull-ui-acceptance.md",
        "ui_actions_rerun_by_this_script": False,
        "unmodified_roundtrips": cases,
        "edited_roundtrip": {
            "status": "PASS", "artifact": edited_path.name,
            "file_sha256": hashlib.sha256(edited_path.read_bytes()).hexdigest(),
            "source_sha256": actual["model"]["source_sha256"],
            "full_preview_sha256": digest(actual), "region_count": 1,
            "vertex_count": 6, "bow_armor_thickness_m": 0.1,
            "base_armor_mass_kg": derived["base_armor_mass_kg"],
            "hull_mass_kg": derived["hull_mass_kg"],
        },
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, default=ROOT / "artifacts/h4")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = verify(args.artifacts)
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    print(text)
