"""Recompile UI-saved O4 files. This verifier never drives or certifies UI actions."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.high_wilderness_sidecar.sessions import EditorService
from backend.high_wilderness_sidecar import outfit_documents
from 高天荒野舰艇数据契约 import canonical_sha256

TECHNICAL_SHIPS = {
    "minimum": "minimum_legal", "conventional": "conventional_crewed", "unmanned": "unmanned_flagship",
}


def compare_file(service, path, expected_source, expected_binding=None):
    raw = path.read_bytes()
    source, binding = outfit_documents.unpack(json.loads(raw.decode("utf-8-sig")), service.index)
    expected = service.preview(expected_source, expected_binding)
    actual = service.preview(source, binding)
    if not actual["valid"] or not expected["valid"]:
        raise ValueError(f"{path.name}: invalid outfit")
    if source != expected_source or binding != expected_binding or actual != expected:
        raise ValueError(f"{path.name}: resource, binding or complete preview differs")
    return {
        "artifact": path.name, "status": "PASS", "file_sha256": hashlib.sha256(raw).hexdigest(),
        "canonical_sha256": canonical_sha256(source), "preview_sha256": canonical_sha256(actual),
        "module_count": len(source["modules"]), "deck_count": len(actual["model"]["layout"]["hull"]["decks"]),
        "compared": ["canonical_resource", "hull_binding", "module_instances_and_placements", "all_occupancy_layers",
                     "weapon_groups_and_horizontal_arcs", "complete_derived", "diagnostics"],
    }


def verify(directory):
    with TemporaryDirectory() as temp:
        service = EditorService("o4-saved-file-verifier", recovery_dir=Path(temp))
        results = []
        for slug, suffix in TECHNICAL_SHIPS.items():
            path = directory / f"{slug}.json"
            source = next(s for d, s in service.index.resources.values()
                          if d["kind"] == "OutfitPlan" and d["id"].endswith(suffix))
            if not path.exists():
                results.append(dict(artifact=path.name, status="NOT_COVERED", reason="UI-saved file not present"))
                continue
            try:
                results.append(compare_file(service, path, source))
            except Exception as exc:
                results.append(dict(artifact=path.name, status="FAIL", reason=str(exc)))
        status = "FAIL" if any(r["status"] == "FAIL" for r in results) else (
            "PASS" if all(r["status"] == "PASS" for r in results) else "NOT_COVERED")
        return {"report": "gaotian.web-o4-saved-artifact-verification/v1", "status": status,
                "scope": "saved_files_recompiled_by_authoritative_service",
                "ui_actions_rerun_by_this_script": False, "o4_ui_gate_certified_by_this_script": False,
                "cases": results}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, default=ROOT / "artifacts/o4")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = verify(args.artifacts)
    encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded)
    sys.exit(0 if report["status"] == "PASS" else 1)
