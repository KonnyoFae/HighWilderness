"""E0 offline baseline: isolated legacy worlds, replay, and unprofiled timing.

No desktop connection, production patches, historical golden changes, or new kernel.
Run `suite --out <new-directory>`; later `replay --run <directory>` checks the
current implementation against the captured source/resource/input baseline.
"""
from __future__ import annotations

import argparse
import cProfile
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import pstats
import subprocess
import sys
from time import perf_counter
from datetime import datetime, timezone
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.high_wilderness_sidecar.tactical import TacticalService, SCENARIO_ID, INPUT_INTERFACE
from 高天荒野舰艇数据契约 import canonical_sha256
from 高天荒野舰艇定向推进控制桥 import DirectionalPropulsionControlInput

TAPE = ROOT / "contracts/web_bridge/fixtures/realtime-e0-inputs.json"
INTERFACE = "gaotian.realtime-baseline/v1"


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def first_difference(expected, actual, path="$"):
    """No field exclusions, list sorting, float tolerance, or truthy equality."""
    if type(expected) is not type(actual):
        return dict(path=path, expected_type=type(expected).__name__, actual_type=type(actual).__name__)
    if isinstance(expected, dict):
        if expected.keys() != actual.keys():
            return dict(path=path, missing=sorted(expected.keys() - actual.keys()), extra=sorted(actual.keys() - expected.keys()))
        for key in expected:
            difference = first_difference(expected[key], actual[key], f"{path}.{key}")
            if difference:
                return difference
    elif isinstance(expected, list):
        if len(expected) != len(actual):
            return dict(path=path, expected_length=len(expected), actual_length=len(actual))
        for index, (left, right) in enumerate(zip(expected, actual)):
            difference = first_difference(left, right, f"{path}[{index}]")
            if difference:
                return difference
    elif expected != actual:
        return dict(path=path, expected=expected, actual=actual)
    return None


class LegacyWorker:
    """Adapter seam: replace in E1 only after supplying the same observation contract."""
    def __init__(self):
        self.service = s = TacticalService("backend.e0", ROOT)
        s.dispatch(dict(method="tactical.create", params=dict(scenario_id=SCENARIO_ID)))
        # Deterministic transport identity for offline comparison, not a product ID.
        s.scene_id = "scene.e0"
        s.dispatch(dict(method="tactical.set_mode", params=dict(mode="tactical")))
        self.integration_s = self.projection_s = 0.0
        integrate, snapshot = s._integrate, s._snapshot

        def timed_integrate(*args):
            start = perf_counter()
            try:
                return integrate(*args)
            finally:
                self.integration_s += perf_counter() - start

        def timed_snapshot(*args):
            start = perf_counter()
            try:
                return snapshot(*args)
            finally:
                self.projection_s += perf_counter() - start

        # Timing wrappers observe only; no algorithm or validation bypass.
        s._integrate, s._snapshot = timed_integrate, timed_snapshot

    def step(self, item, profiler=None):
        s = self.service
        opening = s.scenario.scene.fixed_step_index
        if item["target_step"] != opening or item["input_seq"] != s.last_input_seq + 1:
            raise ValueError("Input tape boundary/sequence mismatch")
        value = dict(interface=INPUT_INTERFACE, scene_id=s.scene_id, input_seq=item["input_seq"],
                     target_step=opening, command="control", arguments=dict(ship_id="ship.web.blue", control=item["control"]))
        s.dispatch(dict(method="tactical.advance", params=dict(scene_id=s.scene_id, input=value, step_count=60)))
        self.integration_s = self.projection_s = 0.0
        if profiler:
            profiler.enable()
        start = perf_counter()
        try:
            s.advance_one()
        finally:
            elapsed = perf_counter() - start
            if profiler:
                profiler.disable()
        if s.advance_state["status"] == "failed" or s.scenario.scene.fixed_step_index != opening + 1:
            raise RuntimeError(f"Failed atomic step: {s.advance_state}")
        s.pause()
        return dict(step=opening + 1, total_s=elapsed, integration_s=self.integration_s, projection_s=self.projection_s)

    def observe(self):
        s = self.service
        return dict(scene=s.scenario.scene.to_dict(), command=s.command_state.to_dict(),
                    arbitration=None if s.last_resolution is None else s.last_resolution.to_dict(),
                    step_resolution=None if s.last_resolution is None else s.last_resolution.scene_resolution.to_dict(),
                    receipt=dict(last_input_seq=s.last_input_seq, last_input=s.last_input, advance_state=s.advance_state))

    def resources(self):
        s = self.service
        return dict(manifest=s.scenario.manifest, command_tuning=s.command_tuning.to_dict(),
                    static_sha256=s.static_sha256, initial_scene_sha256=canonical_sha256(s.scenario.scene))


def load_tape(path):
    tape = json.loads(Path(path).read_text(encoding="utf-8"))
    if tape["interface"] != "gaotian.realtime-e0-inputs/v1":
        raise ValueError("Unsupported tape")
    for case in tape["cases"]:
        if not 0 <= case["warmup_steps"] < len(case["steps"]):
            raise ValueError("Empty measured sequence")
        for index, step in enumerate(case["steps"]):
            if step["target_step"] != index or step["input_seq"] != index + 1:
                raise ValueError("Non-contiguous input tape")
            DirectionalPropulsionControlInput.parse(step["control"])
    return tape


def source_inventory():
    # Archive executable source and resource inputs, including untracked source.
    # Reports, old saves, node_modules and native build artifacts are excluded.
    files = set(ROOT.glob("*.py"))
    files.update((ROOT / "backend").rglob("*.py"))
    files.update((ROOT / "tools").glob("*.py"))
    for folder in ("材料", "涂料", "模块/测试夹具", "舾装方案夹具", "船壳蓝图夹具", "出航配置夹具", "标定"):
        files.update((ROOT / "舰艇数据" / folder).rglob("*.json"))
    files.add(TAPE)
    return {p.relative_to(ROOT).as_posix(): digest(p) for p in sorted(files)}


def command_output(args):
    result = subprocess.run(args, cwd=ROOT, capture_output=True, encoding="utf-8", errors="replace")
    return dict(returncode=result.returncode, stdout=result.stdout.strip(), stderr=result.stderr.strip())


def freeze(out):
    inventory = source_inventory()
    with zipfile.ZipFile(out / "source-resources.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for name in inventory:
            archive.write(ROOT / name, name)
    patch = subprocess.run(["git", "diff", "--binary", "HEAD", "--", "*.py"], cwd=ROOT, capture_output=True, check=True).stdout
    (out / "tracked-python.patch").write_bytes(patch)
    metadata = dict(interface=INTERFACE, captured_at_utc=datetime.now(timezone.utc).isoformat(),
                    git_head=command_output(["git", "rev-parse", "HEAD"]),
                    worktree_status=command_output(["git", "-c", "core.quotepath=false", "status", "--short"]),
                    source_scope="Root/backend/tools Python and fixture resource JSON; archive includes untracked source; not a desktop build backup",
                    files_sha256=inventory, source_set_sha256=canonical_sha256(inventory),
                    archive_sha256=digest(out / "source-resources.zip"), patch_sha256=digest(out / "tracked-python.patch"),
                    python=sys.version, executable=sys.executable, platform=platform.platform(),
                    processor=platform.processor(), logical_cpu_count=os.cpu_count(),
                    power_plan=command_output(["powercfg", "/getactivescheme"]) if os.name == "nt" else None,
                    build="Python legacy worker, no debugger/profiler in timing runs; no Rust/UI in measurement",
                    environment_limits="CPU model/physical cores and competing background load not controlled; no GUI session was altered",
                    random_policy="No random draws in the two-ship flight tapes; combat stochastic inputs NOT_COVERED")
    write_json(out / "manifest.json", metadata)
    return inventory


def record(out, tape):
    resource_reference = None
    for case in tape["cases"]:
        worker = LegacyWorker()
        resource = worker.resources()
        if resource_reference is None:
            resource_reference = resource
            write_json(out / "resources.json", resource)
        if first_difference(resource_reference, resource):
            raise RuntimeError("Resource construction differs between fresh worlds")
        with gzip.open(out / f"{case['id']}.jsonl.gz", "wt", encoding="utf-8") as stream:
            for item in [None, *case["steps"]]:
                if item is not None:
                    worker.step(item)
                stream.write(json.dumps(worker.observe(), ensure_ascii=False, allow_nan=False) + "\n")
        print(f"recorded {case['id']}: {len(case['steps'])} steps", flush=True)


def replay(run, tape):
    reports = []
    resource = json.loads((run / "resources.json").read_text(encoding="utf-8"))
    for case in tape["cases"]:
        worker = LegacyWorker()
        difference = first_difference(resource, worker.resources())
        if difference:
            return dict(status="FAIL", case=case["id"], boundary="resources", difference=difference)
        with gzip.open(run / f"{case['id']}.jsonl.gz", "rt", encoding="utf-8") as stream:
            for index, item in enumerate([None, *case["steps"]]):
                if item is not None:
                    worker.step(item)
                line = stream.readline()
                if not line:
                    return dict(status="FAIL", case=case["id"], boundary=index, error="truncated trace")
                difference = first_difference(json.loads(line), worker.observe())
                if difference:
                    return dict(status="FAIL", case=case["id"], boundary=index, difference=difference)
            if stream.readline():
                return dict(status="FAIL", case=case["id"], error="extra trace frames")
        reports.append(dict(case=case["id"], matched_boundaries=len(case["steps"]) + 1))
        print(f"replay PASS {case['id']}", flush=True)
    return dict(status="PASS", comparison="Exact complete legacy observations including ordered events and receipts", cases=reports)


def summary(samples):
    values = sorted(s["total_s"] for s in samples)
    percentile = lambda fraction: values[max(0, math.ceil(len(values) * fraction) - 1)] * 1000
    return dict(steps=len(values), wall_s=sum(values), mean_ms=sum(values) / len(values) * 1000,
                p95_ms=percentile(.95), p99_ms=percentile(.99), max_ms=max(values) * 1000,
                realtime_factor=(len(values) / 60) / sum(values),
                integration_s=sum(s["integration_s"] for s in samples), projection_s=sum(s["projection_s"] for s in samples))


def measure(run, case, repeats=3):
    with gzip.open(run / f"{case['id']}.jsonl.gz", "rt", encoding="utf-8") as stream:
        for line in stream:
            expected_final = json.loads(line)
    runs = []
    for repeat in range(repeats):
        worker = LegacyWorker()
        samples = []
        for index, item in enumerate(case["steps"]):
            sample = worker.step(item)
            if index >= case["warmup_steps"]:
                samples.append(sample)
        difference = first_difference(expected_final, worker.observe())
        if difference:
            raise RuntimeError(f"Timed final state mismatch: {difference}")
        row = dict(repeat=repeat + 1, **summary(samples), final_state_match=True, samples=samples)
        runs.append(row)
        print(f"timing {repeat + 1}: {row['mean_ms']:.3f} ms/step, P95 {row['p95_ms']:.3f}", flush=True)
    return dict(scope="12 warmup + 60 measured legacy advance_one steps; accept/pause, comparison, I/O outside timer",
                percentile="nearest rank", runs=runs)


def mechanisms(case):
    worker, profile = LegacyWorker(), cProfile.Profile()
    for index, item in enumerate(case["steps"]):
        worker.step(item, profile if index >= case["warmup_steps"] else None)
    names = {"canonical_sha256", "canonical_json", "_parse_exact_timing_capability",
             "verify_derived_ship_snapshot_fingerprint", "_validate_runtime_cache_hit", "validate_continuous_damage_state",
             "_synchronize_state_to_scene", "_validate_internal_state"}
    rows = [dict(file=Path(file).name, line=line, function=name, calls=calls)
            for (file, line, name), (_, calls, _, _, _) in pstats.Stats(profile).stats.items() if name in names]
    return dict(scope="Separate cProfile count run; its time is not performance evidence", measured_steps=len(case["steps"]) - case["warmup_steps"], rows=rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    suite = sub.add_parser("suite")
    suite.add_argument("--out", type=Path, required=True)
    check = sub.add_parser("replay")
    check.add_argument("--run", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "replay":
        tape = load_tape(args.run / "inputs.json")
        result = replay(args.run, tape)
        result["current_source_set_sha256"] = canonical_sha256(source_inventory())
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "PASS" else 1
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    try:
        tape = load_tape(TAPE)
        write_json(out / "inputs.json", tape)
        inventory = freeze(out)
        record(out, tape)
        result = replay(out, tape)
        write_json(out / "replay.json", result)
        if result["status"] != "PASS":
            raise RuntimeError(result)
        mixed = next(c for c in tape["cases"] if c["id"] == "mixed72")
        write_json(out / "timing.json", measure(out, mixed))
        write_json(out / "mechanisms.json", mechanisms(mixed))
        if source_inventory() != inventory:
            raise RuntimeError("Source/resources changed during capture; discard this run as baseline")
        evidence = {p.name: digest(p) for p in out.iterdir() if p.is_file()}
        write_json(out / "result.json", dict(interface=INTERFACE, status="PASS", source_unchanged=True,
            evidence_sha256=evidence, scope="E0 baseline replay and short measurements only", new_kernel="NOT_IMPLEMENTED",
            not_covered=["damage injection", "combat", "60-second throughput", "long run", "UI/IPC", "save/reload", "large fleets"]))
    except Exception as error:
        write_json(out / "failure.json", dict(status="FAIL", error=repr(error)))
        raise
    print(f"E0 baseline PASS: {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
