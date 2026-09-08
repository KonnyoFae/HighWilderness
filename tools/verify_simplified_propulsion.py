"""E1c.1 resource evidence and unchanged E1b flight replay; no speed claim."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import tactical_realtime_baseline as baseline
from tools.verify_realtime_flight import FlightWorker, check_trace
from backend.high_wilderness_sidecar.realtime_flight import RealtimeFlightSession


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--reference", type=Path,
        default=ROOT / "artifacts/t3a-realtime-experiment/e0-20260908-baseline")
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    try:
        original = json.loads((args.reference / "result.json").read_text(encoding="utf-8"))
        for name, expected in original["evidence_sha256"].items():
            if baseline.digest(args.reference / name) != expected:
                raise RuntimeError(f"Changed reference: {name}")
        sources = baseline.freeze(out)
        session = RealtimeFlightSession(ROOT)
        tables = session.resources.simplified_propulsion
        baseline.write_json(out / "contributions.json", [c.to_dict() for c in tables])
        tape = baseline.load_tape(args.reference / "inputs.json")
        resources = json.loads((args.reference / "resources.json").read_text(encoding="utf-8"))
        checks = []
        for case in tape["cases"]:
            check = check_trace(FlightWorker, case, args.reference, resources)
            checks.append(check)
            baseline.write_json(out / "comparison.json", checks)
            print(check, flush=True)
            if check["status"] != "PASS":
                raise RuntimeError("Existing flight behavior changed")
        if sources != baseline.source_inventory():
            raise RuntimeError("Sources changed during verification")
        baseline.write_json(out / "result.json", dict(status="PASS",
            scope="E1c.1 static contribution export and unchanged E1b flight replay only",
            not_covered=["event deltas", "simplified flight", "safety wiring", "damage integration",
                         "new-policy save/load", "real-time performance", "desktop integration"],
            evidence_sha256={p.name: baseline.digest(p) for p in out.iterdir() if p.is_file()}))
        return 0
    except Exception as error:
        baseline.write_json(out / "failure.json", dict(status="FAIL", error=repr(error)))
        raise


if __name__ == "__main__":
    raise SystemExit(main())
