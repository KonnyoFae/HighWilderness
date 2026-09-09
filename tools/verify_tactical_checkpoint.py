"""E2.3 committed saves, deterministic resumed flight and post-load throughput."""
import argparse
from dataclasses import asdict, replace
import json
from pathlib import Path
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.high_wilderness_sidecar import tactical_checkpoint as checkpoint
from tools import tactical_realtime_baseline as baseline
from tools import verify_tactical_resources_runtime as resources


def build():
    return resources.build(with_command=True)


def rebuild(s):
    return checkpoint.loads(checkpoint.dumps(s), s._seeds, s._profile, direct_ship_id=s._direct,
        allow_test_device_rebuild=s._allow_test_device_rebuild)


def resume_case(case, out):
    a, b = build(), build()
    checkpoints = {0, 1, 16, 59, 60, 119, 600, 631, 690, 721, 751, 781, 811, 841, 871, 2101, 2161, 4200}
    samples = []
    frames = resources.frames(a, case)
    for boundary in range(len(frames)+1):
        if boundary in checkpoints:
            started = perf_counter(); payload = checkpoint.dumps(b); save_ms = (perf_counter()-started)*1000
            started = perf_counter()
            b = checkpoint.loads(payload, b._seeds, b._profile, direct_ship_id=b._direct,
                allow_test_device_rebuild=b._allow_test_device_rebuild)
            load_ms = (perf_counter()-started)*1000
            assert a.world.ships == b.world.ships
            assert a.world.epoch != b.world.epoch and b.last_result is None
            (out/f"{case['name']}-checkpoint-{boundary}.json").write_text(payload, encoding='utf-8')
            samples.append(dict(boundary=boundary, save_ms=save_ms, load_ms=load_ms, bytes=len(payload.encode('utf-8'))))
        if boundary == len(frames):
            break
        frame = frames[boundary]
        resources.step(a, frame)
        rebound = (frame[0], tuple(replace(op, epoch=b.world.epoch) for op in frame[1]),
            tuple(replace(op, epoch=b.world.epoch) for op in frame[2]))
        resources.step(b, rebound)
        assert a.world.ships == b.world.ships and a.last_result == b.last_result, (case['name'], boundary)
    return dict(case=case['name'], compared_boundaries=len(frames)+1, checkpoints=samples)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    out = parser.parse_args().out.resolve(); out.mkdir(parents=True, exist_ok=False)
    try:
        sources = baseline.freeze(out)
        manifest = json.loads((out/'manifest.json').read_text(encoding='utf-8'))
        manifest['build'] = 'E2.3 internal checkpoint + implemented device/resource/command flight, synthetic manual/electric flagship'
        baseline.write_json(out/'manifest.json', manifest)
        sample = build()
        baseline.write_json(out/'resources.json', [asdict(s) for s in sample._seeds])
        cases = [resources.tape(sample._seeds[0], name) for name in ('steady', 'resource_bursts', 'command_loss')]
        cases[-1]['resources'] = [dict(step=2100, phase='closing', kind='mode', target='cic', value='off', sequence=1),
            dict(step=2160, phase='opening', kind='mode', target='cic', value='active', sequence=2)]
        baseline.write_json(out/'inputs.json', cases)
        resumed, timings = [], []
        for case in cases:
            resumed.append(resume_case(case, out))
            # Load at boundary 600 outside timing, then measure precisely the
            # same 3600-step input tape as E2.2b, three times. No IO in hot loop.
            timings.append(resources.verify(case, out, factory=build, rebuild=rebuild))
            print(dict(case=case['name'], checkpoints=len(resumed[-1]['checkpoints']), timing=timings[-1]), flush=True)
        assert sources == baseline.source_inventory()
        baseline.write_json(out/'result.json', dict(status='PASS', stage='E2.3', implemented_E2_scope='PASS',
            full_E2='NOT_PASSED', product_realtime='NOT_PASSED', warmup_steps=600, measured_steps=3600, repeats=3,
            continuation=resumed, workloads=timings,
            scope='Two ships; checkpoint continuation compared to uninterrupted flight. Post-load fixed-step timing excludes save/load/setup/oracle/profile. No product file IO, actual combat/repair, fuel producer, RTS, scheduler/UI or long run.',
            evidence_sha256={p.name:baseline.digest(p) for p in out.iterdir() if p.is_file()}))
    except Exception as error:
        baseline.write_json(out/'failure.json', dict(status='FAIL', error=repr(error))); raise


if __name__ == '__main__': main()
