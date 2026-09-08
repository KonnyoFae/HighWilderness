"""Compare current E1 flight session against frozen E0, then alternate old/new timings."""
import argparse
import cProfile
from copy import deepcopy
import gzip
import json
from pathlib import Path
import pstats
import sys
from time import perf_counter
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import tactical_realtime_baseline as baseline
from backend.high_wilderness_sidecar.realtime_flight import RealtimeFlightSession
from backend.high_wilderness_sidecar.tactical import ADVANCE_INTERFACE, render_dynamic
from backend.high_wilderness_sidecar.tactical_control import render_control_state
from 高天荒野舰艇数据契约 import canonical_sha256


class FlightWorker:
    def __init__(self):
        self.session = RealtimeFlightSession(ROOT)
        self.receipt = dict(last_input_seq=0, last_input=None, advance_state=None)
        self._manifest = json.loads(self.session.resources.manifest_json)

    def resources(self):
        return self.session.resources.describe()

    def observe(self):
        # Adapter-only legacy receipt representation, not a new transport protocol.
        return dict(**self.session.observe(), receipt=deepcopy(self.receipt))

    def step(self, item, profiler=None):
        value = dict(interface=baseline.INPUT_INTERFACE, scene_id="scene.e0", input_seq=item["input_seq"],
                     target_step=item["target_step"], command="control",
                     arguments=dict(ship_id="ship.web.blue", control=item["control"]))
        prepared = self.session.accept(dict(value, scene_id=self.session.world.resource_epoch))
        progress = dict(interface=ADVANCE_INTERFACE, status="stopped", input_seq=item["input_seq"],
            input_sha256=canonical_sha256(value), start_step=item["target_step"], step_count=60, executed_steps=1, error=None)
        projection_s = 0.0

        def project(world, result):
            nonlocal projection_s
            start = perf_counter()
            scenario = SimpleNamespace(scene=world.scene, manifest=self._manifest)
            # Same heavyweight compatibility view work as E0, before commit.
            world.scene.to_dict()["interface"]
            render_dynamic(scenario)
            render_control_state(scenario, world.command, self.session.resources.command_tuning,
                                 item["input_seq"], value, result)
            projection_s = perf_counter() - start

        if profiler:
            profiler.enable()
        start = perf_counter()
        try:
            self.session.advance(prepared, project=project)
        finally:
            elapsed = perf_counter() - start
            if profiler:
                profiler.disable()
        self.receipt = dict(last_input_seq=item["input_seq"], last_input=deepcopy(value), advance_state=progress)
        return dict(step=self.session.world.scene.fixed_step_index, total_s=elapsed,
                    integration_s=elapsed - projection_s, projection_s=projection_s)


def check_trace(factory, case, reference, resources):
    worker = factory()
    difference = baseline.first_difference(resources, worker.resources())
    if difference:
        return dict(status="FAIL", case=case["id"], boundary="resources", difference=difference)
    with gzip.open(reference / f"{case['id']}.jsonl.gz", "rt", encoding="utf-8") as stream:
        for index, item in enumerate([None, *case["steps"]]):
            if item is not None:
                worker.step(item)
            line = stream.readline()
            if not line:
                raise RuntimeError("Truncated E0 trace")
            expected = json.loads(line)
            difference = baseline.first_difference(expected, worker.observe())
            if difference:
                return dict(status="FAIL", case=case["id"], boundary=index, difference=difference)
        if stream.readline():
            raise RuntimeError("Extra E0 trace frames")
    return dict(status="PASS", case=case["id"], boundaries=len(case["steps"]) + 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, default=ROOT / "artifacts/t3a-realtime-experiment/e0-20260908-baseline")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    try:
        original = json.loads((args.reference / "result.json").read_text(encoding="utf-8"))
        for name, expected in original["evidence_sha256"].items():
            if baseline.digest(args.reference / name) != expected:
                raise RuntimeError(f"Changed reference evidence: {name}")
        source = baseline.freeze(out)
        tape = baseline.load_tape(args.reference / "inputs.json")
        resources = json.loads((args.reference / "resources.json").read_text(encoding="utf-8"))
        checks = []
        for name, factory in [("legacy", baseline.LegacyWorker), ("flight", FlightWorker)]:
            for case in tape["cases"]:
                check = dict(backend=name, **check_trace(factory, case, args.reference, resources))
                checks.append(check)
                baseline.write_json(out / "comparison.json", checks)
                print(check, flush=True)
                if check["status"] != "PASS":
                    raise RuntimeError("First divergence recorded in comparison.json")
        case = next(c for c in tape["cases"] if c["id"] == "mixed72")
        with gzip.open(args.reference / "mixed72.jsonl.gz", "rt", encoding="utf-8") as stream:
            for line in stream:
                expected_final = json.loads(line)
        runs = []
        for repeat in range(3):
            order = [("legacy", baseline.LegacyWorker), ("flight", FlightWorker)]
            if repeat % 2:
                order.reverse()
            for name, factory in order:
                worker = factory()
                samples = []
                for index, item in enumerate(case["steps"]):
                    sample = worker.step(item)
                    if index >= case["warmup_steps"]:
                        samples.append(sample)
                if baseline.first_difference(expected_final, worker.observe()):
                    raise RuntimeError("Timed result differs from E0")
                row = dict(backend=name, repeat=repeat + 1, **baseline.summary(samples), samples=samples)
                runs.append(row)
                print(f"{name} {repeat + 1}: {row['mean_ms']:.3f} ms/step", flush=True)
        baseline.write_json(out / "timing.json", dict(runs=runs,
            scope="Unprofiled core advance with compatibility projection, acceptance outside timer; Flight receipt shape is a comparison adapter"))
        worker, profile = FlightWorker(), cProfile.Profile()
        for index, item in enumerate(case["steps"]):
            worker.step(item, profile if index >= case["warmup_steps"] else None)
        names = {"_parse_exact_timing_capability", "verify_derived_ship_snapshot_fingerprint", "canonical_sha256", "_validate_state"}
        counts = {name: 0 for name in names}
        parse_rows = []
        for (file, line, name), (_, calls, _, _, _) in pstats.Stats(profile).stats.items():
            if name in counts:
                counts[name] += calls
            if name == "parse" and file.startswith(str(ROOT)):
                parse_rows.append(dict(file=Path(file).name, line=line, calls=calls))
        baseline.write_json(out / "mechanisms.json", dict(measured_steps=60, calls=counts,
            parse_calls=sorted(parse_rows, key=lambda row: -row["calls"]), timing_not_comparable=True))
        if counts["_parse_exact_timing_capability"] or counts["verify_derived_ship_snapshot_fingerprint"]:
            raise RuntimeError("Static work remains in compiled hot path")
        if source != baseline.source_inventory():
            raise RuntimeError("Sources changed during verification")
        baseline.write_json(out / "result.json", dict(status="PASS", scope="Current E1 independent flight path vs frozen E0; not full E1 or real-time acceptance",
            evidence_sha256={p.name: baseline.digest(p) for p in out.iterdir() if p.is_file()}, reference=str(args.reference),
            not_covered=["compact dynamic layout", "internal parse/replay elimination", "damage matrix", "scheduler/long run", "combat", "UI integration"]))
        return 0
    except Exception as error:
        baseline.write_json(out / "failure.json", dict(status="FAIL", error=repr(error)))
        raise


if __name__ == "__main__":
    raise SystemExit(main())
