"""Test-only observation around the real sidecar; no alternate simulation loop.

Queue high-water marks are captured under Queue's existing mutex. The domain
owner samples post-pump state and its own process memory once per second. No
world history, full reference replay, profiler or production API is added.
"""
import argparse
import ctypes
from dataclasses import asdict
import json
import os
from pathlib import Path
from queue import Queue
import sys
from time import monotonic, perf_counter_ns

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.high_wilderness_sidecar import server
from backend.high_wilderness_sidecar.realtime_view import RealtimeViewService


def process_memory():
    if os.name == 'nt':
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in ('PeakWorkingSetSize', 'WorkingSetSize',
                'QuotaPeakPagedPoolUsage', 'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage',
                'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage', 'PrivateUsage')]
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        psapi = ctypes.WinDLL('psapi', use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        value = Counters(); value.cb = ctypes.sizeof(value)
        if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(value), value.cb):
            raise ctypes.WinError(ctypes.get_last_error())
        return dict(rss_bytes=value.WorkingSetSize, private_bytes=value.PrivateUsage)
    fields = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
    return dict(rss_bytes=int(fields['VmRSS'].split()[0])*1024, private_bytes=None)


class MeasuredQueue(Queue):
    registry = []

    def __init__(self, maxsize=0):
        super().__init__(maxsize)
        self.peak = 0
        self.registry.append(self)

    def _put(self, item):
        super()._put(item)
        self.peak = max(self.peak, self._qsize())


class ObservedView(RealtimeViewService):
    output = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.started = monotonic()
        self.next_sample = self.started
        self.peaks = dict(pending_inputs=0, retained_receipts=0, reliable_events=0, debt_quanta=0)
        self.tick_count = self.tick_ns = self.tick_max_ns = 0

    def tick(self):
        begin = perf_counter_ns()
        super().tick()
        elapsed = perf_counter_ns()-begin
        self.tick_count += 1; self.tick_ns += elapsed
        self.tick_max_ns = max(self.tick_max_ns, elapsed)
        self.observe()

    def dispatch(self, *args, **kwargs):
        result = super().dispatch(*args, **kwargs)
        self.observe()
        return result

    def observe(self):
        if self.scheduler is None:
            return
        status = asdict(self.scheduler.status)
        for field in self.peaks:
            self.peaks[field] = max(self.peaks[field], status[field])
        now = monotonic()
        if now < self.next_sample:
            return
        self.next_sample = now+1
        row = dict(elapsed_s=now-self.started, status=status, peaks=self.peaks,
            queues=[dict(capacity=q.maxsize, peak=q.peak, current=q.qsize()) for q in MeasuredQueue.registry],
            memory=process_memory(), view_cache_entries=int(self.latest is not None),
            view_step=self.latest['fixed_step'], tick_count=self.tick_count,
            tick_total_ns=self.tick_ns, tick_max_ns=self.tick_max_ns)
        self.output.write(json.dumps(row, allow_nan=False)+'\n'); self.output.flush()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    # Injection is confined to this test child. Actual serve(), framing, leases,
    # worker scheduling, fixed steps, projection and acknowledgements all run.
    server.Queue = MeasuredQueue
    server.RealtimeViewService = ObservedView
    with (args.out/'probe.jsonl').open('x', encoding='utf-8') as stream:
        ObservedView.output = stream
        instance = server.SidecarServer('backend.e3clongrun', recovery_dir=args.out/'recovery')
        raise SystemExit(instance.serve(sys.stdin.buffer, sys.stdout.buffer))


if __name__ == '__main__':
    main()
