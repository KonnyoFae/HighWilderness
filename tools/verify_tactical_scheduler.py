"""E3a deterministic scheduler replay, throughput and short real-clock probe."""
import argparse
from collections import deque
import cProfile
from dataclasses import asdict
import json
from pathlib import Path
import pstats
import sys
from time import monotonic_ns, perf_counter, sleep

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.high_wilderness_sidecar.tactical_scheduler import TacticalScheduler, ScheduledControl, DomainBatch, QUANTA
from tools import tactical_realtime_baseline as baseline
from tools import verify_tactical_resources_runtime as resources
from tools.verify_simplified_flight_gate import summary
from 高天荒野舰艇定向推进控制桥 import DirectionalPropulsionControlInput


class Clock:
    now = 0
    def __call__(self): return self.now


class Harness:
    def __init__(self, case, *, project=None, real_clock=False):
        self.session = resources.build(with_command=True)
        self.frames = resources.frames(self.session, case)
        self.clock = monotonic_ns if real_clock else Clock()
        def domain(world):
            frame = self.frames[world.fixed_step]
            return DomainBatch(resource_operations=frame[1], device_operations=frame[2])
        self.q = TacticalScheduler(self.session, clock=self.clock, domain_inputs=domain, project=project)
        self.controls = [(v['step'], v['value']) for v in case['controls']]
        self.cursor, self.outstanding = 0, set()
        self.peak_pending = self.peak_receipts = self.peak_events = 0
        self.executed = 0
        self.q.resume()

    def enqueue(self):
        status = self.q.status
        while self.cursor < len(self.controls) and self.controls[self.cursor][0] <= status.fixed_step+6:
            step, control = self.controls[self.cursor]
            assert step >= status.fixed_step
            req = ScheduledControl(status.epoch, status.generation, self.q.status.highest_input_sequence+1,
                self.session._direct, step, DirectionalPropulsionControlInput.parse(control))
            self.q.submit(req)
            self.outstanding.add(req.sequence)
            self.cursor += 1

    def service(self):
        status = self.q.status
        self.peak_pending = max(self.peak_pending, status.pending_inputs)
        self.peak_receipts = max(self.peak_receipts, status.retained_receipts)
        self.peak_events = max(self.peak_events, status.reliable_events)
        assert status.running, status
        for sequence in tuple(self.outstanding):
            receipt = self.q.query(status.epoch, sequence)
            if receipt.status != 'accepted':
                assert receipt.status == 'executed', receipt
                self.executed += 1
                self.q.acknowledge_input(status.epoch, sequence)
                self.outstanding.remove(sequence)
        events = self.q.read_events(status.epoch, after_sequence=status.acknowledged_event_sequence)
        if events: self.q.acknowledge_events(status.epoch, events[-1].sequence)
        return tuple(e.result for e in events)

    def tick(self):
        self.enqueue()
        self.clock.now = ((self.q.world.fixed_step+1)*QUANTA+59)//60
        assert self.q.pump() == 1
        self.service()


def correctness(case, out):
    oracle = resources.build(with_command=True)
    frames = resources.frames(oracle, case)
    expected_events = deque()
    def project(world, result):
        resources.step(oracle, frames[oracle.world.fixed_step])
        assert world.ships == oracle.world.ships and result == oracle.last_result
        if result.events: expected_events.append(result)
    h = Harness(case, project=project)
    i = 0
    while h.q.world.fixed_step < 4200:
        h.enqueue()
        h.clock.now = min(70*QUANTA, h.clock.now+(7, 21, 11, 40, 3)[i % 5]*1_000_000)
        h.q.pump()
        for result in h.service(): assert result == expected_events.popleft()
        i += 1
    assert not expected_events and not h.outstanding
    assert h.q.status.debt_quanta == 0
    baseline.write_json(out/f"{case['name']}-final.json", dict(ships=[asdict(s) for s in h.q.world.ships], last=asdict(oracle.last_result)))
    return h.q.world.ships, dict(compared_boundaries=4201, jitter_pumps=i, executed_inputs=h.executed,
        peak_pending=h.peak_pending, peak_receipts=h.peak_receipts, peak_events=h.peak_events)


def measure(case, expected, out):
    runs = []
    for repeat in range(3):
        h = Harness(case)
        for _ in range(600): h.tick()
        samples = []
        start = perf_counter()
        for _ in range(3600):
            t = perf_counter(); h.tick(); samples.append((perf_counter()-t)*1000)
        wall = perf_counter()-start
        assert h.q.world.ships == expected
        run = dict(repeat=repeat+1, **summary(samples, wall), samples_ms=samples)
        runs.append(run)
        assert run['passed'], run
    baseline.write_json(out/f"{case['name']}-timing.json", runs)
    h = Harness(case)
    for _ in range(600): h.tick()
    profile = cProfile.Profile()
    with profile:
        for _ in range(3600): h.tick()
    names = ('canonical_sha256', 'compile_runtime_ship_parameters', 'aggregate_actuators',
        'verify_derived_ship_snapshot_fingerprint', '_parse_exact_timing_capability', '_allocate_power', '_manual_staffing')
    counts = dict.fromkeys(names, 0)
    for (file, line, name), (_, calls, *_) in pstats.Stats(profile).stats.items():
        if name in counts: counts[name] += calls
        if name == 'parse':
            assert Path(file).name in ('tactical_scheduler.py', '高天荒野舰艇定向推进控制桥.py', '高天荒野舰艇推进通道合同.py')
    assert not any(counts[n] for n in names[:-2])
    if case['name'] == 'steady': assert counts['_allocate_power'] == counts['_manual_staffing'] == 0
    baseline.write_json(out/f"{case['name']}-mechanisms.json", counts)
    return dict(worst_mean_ms=max(r['mean_ms'] for r in runs), worst_p95_ms=max(r['p95_ms'] for r in runs),
        worst_p99_ms=max(r['p99_ms'] for r in runs), maximum_ms=max(r['maximum_ms'] for r in runs), mechanisms=counts)


def wall_probe(out, seconds):
    sample = resources.build(with_command=True)
    case = resources.tape(sample._seeds[0], 'maneuvers')
    h = Harness(case, real_clock=True)
    start = monotonic_ns()
    samples = []
    while monotonic_ns()-start < seconds*QUANTA:
        h.enqueue(); h.q.pump(); h.service()
        status = h.q.status
        samples.append(dict(elapsed_ns=monotonic_ns()-start, step=status.fixed_step, debt_quanta=status.debt_quanta))
        sleep(.001)  # host event-loop stand-in, never part of the authority
    pause_start = monotonic_ns(); h.q.pause(); pause_ms = (monotonic_ns()-pause_start)/1e6
    elapsed = (monotonic_ns()-start)/QUANTA
    completed = h.q.world.fixed_step
    assert abs(completed/60-elapsed) < .1
    assert pause_ms < 250
    assert max(s['debt_quanta'] for s in samples)/QUANTA < 15
    # Full reference runs only after wall timing, never contends with it.
    oracle = resources.build(with_command=True)
    frames = resources.frames(oracle, case)
    for f in frames[:completed]: resources.step(oracle, f)
    assert oracle.world.ships == h.q.world.ships
    baseline.write_json(out/'wall-clock-samples.json', samples)
    baseline.write_json(out/'wall-clock-inputs.json', case)
    return dict(wall_seconds=elapsed, completed_steps=completed, simulated_seconds=completed/60,
        simulation_lag_ms=(elapsed-completed/60)*1000, maximum_observed_debt_steps=max(s['debt_quanta'] for s in samples)/QUANTA,
        pause_call_ms=pause_ms, final_matches_direct_reference=True,
        scope='Short local host pump with 1 ms sleeps and actual monotonic clock; no IPC/UI or long-run memory claim')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--wall-seconds', type=int, default=10)
    args = parser.parse_args()
    assert 5 <= args.wall_seconds <= 60
    out = args.out.resolve(); out.mkdir(parents=True, exist_ok=False)
    try:
        sources = baseline.freeze(out)
        manifest = json.loads((out/'manifest.json').read_text(encoding='utf-8'))
        manifest['build'] = 'E3a fixed-step scheduler + bounded control receipts/reliable events; synthetic manual/electric two-ship fixture'
        baseline.write_json(out/'manifest.json', manifest)
        sample = resources.build(with_command=True)
        baseline.write_json(out/'resources.json', [asdict(s) for s in sample._seeds])
        cases = [resources.tape(sample._seeds[0], n) for n in ('steady', 'resource_bursts', 'command_loss')]
        cases[-1]['resources'] = [dict(step=2100, phase='closing', kind='mode', target='cic', value='off', sequence=1),
            dict(step=2160, phase='opening', kind='mode', target='cic', value='active', sequence=2)]
        baseline.write_json(out/'inputs.json', cases)
        results = []
        for case in cases:
            expected, replay = correctness(case, out)
            result = dict(case=case['name'], replay=replay, timing=measure(case, expected, out))
            results.append(result); print(result, flush=True)
        wall = wall_probe(out, args.wall_seconds); print(wall, flush=True)
        assert sources == baseline.source_inventory()
        baseline.write_json(out/'result.json', dict(status='PASS', stage='E3a', full_E3='NOT_PASSED', product_realtime='NOT_PASSED',
            workloads=results, wall_probe=wall, warmup_steps=600, measured_steps=3600, repeats=3,
            scope='Deterministic jitter replay includes every committed ship state/StepResult and reliable event delivery. Timed fake-clock host ticks include queue submission, pump, receipt queries/acks and event reads/acks. Setup, oracle, profile and real-clock throttling excluded from throughput.',
            evidence_sha256={p.name:baseline.digest(p) for p in out.iterdir() if p.is_file()}))
    except Exception as error:
        baseline.write_json(out/'failure.json', dict(status='FAIL', error=repr(error))); raise


if __name__ == '__main__': main()
