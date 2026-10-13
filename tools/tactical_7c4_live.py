"""Opt-in real-path diagnostics. Never imported by production or paired timing."""
import argparse
from collections import deque
from contextlib import ExitStack
from hashlib import sha256
from pathlib import Path
from queue import Queue
from threading import Lock
from time import perf_counter
from unittest.mock import patch

from backend.high_wilderness_sidecar import server, realtime_view, tactical_scheduler
from tools import tactical_7b_live as live, profile_tactical_7c4 as comparison
from tools.profile_tactical_fire_control import stats
from tools.profile_tactical_7b import fingerprint
from tools.tactical_7b_fixture import ROOT, write


class Recorder:
    def __init__(self, limit=2048):
        self.limit=limit;self.active=False;self.values={};self.lock=Lock()

    def add(self, key, seconds):
        if not self.active:return
        with self.lock:
            row=self.values.setdefault(key,dict(count=0,total_s=0.,max_s=0.,samples=deque(maxlen=self.limit)))
            row['count']+=1;row['total_s']+=seconds;row['max_s']=max(row['max_s'],seconds);row['samples'].append(seconds)

    def report(self):
        with self.lock:
            return {key:dict(count=row['count'],total_s=row['total_s'],mean_ms=row['total_s']/row['count']*1000,
                max_ms=row['max_s']*1000,retained_samples=len(row['samples']),recent=stats(row['samples'])) for key,row in self.values.items()}

    def timed(self, fn, key):
        def call(*args, **kwargs):
            start=perf_counter()
            try:return fn(*args,**kwargs)
            finally:self.add(key,perf_counter()-start)
        return call


class TimedQueue(Queue):
    """Keep Queue's bounds, blocking, task accounting and sentinel semantics."""
    def __init__(self, maxsize, recorder, label):
        super().__init__(maxsize);self.recorder=recorder;self.label=label

    def _put(self, item):
        super()._put((perf_counter() if self.recorder.active else None,item))

    def _get(self):
        start,item=super()._get()
        if start is not None:self.recorder.add(self.label+'.residence',perf_counter()-start)
        return item

    def put(self, item, block=True, timeout=None):
        start=perf_counter()
        try:return super().put(item,block,timeout)
        finally:self.recorder.add(self.label+'.put',perf_counter()-start)


class TimedOutput:
    def __init__(self, stream, recorder):
        self.write=recorder.timed(stream.write,'stdio.write')
        self.flush=recorder.timed(stream.flush,'stdio.flush')


def serve(source, out, legacy=False):
    comparison.references();recorder=Recorder();out.mkdir(parents=True,exist_ok=True)
    provenance=fingerprint();provenance.update(reference_head=comparison.REFERENCE_HEAD,variant='reference' if legacy else 'candidate',
        tools_7c={p.relative_to(ROOT).as_posix():sha256(p.read_bytes()).hexdigest() for p in (ROOT/'tools').glob('*7c*') if p.is_file()})
    write(out/'source.json',provenance)
    with comparison.implementation(legacy),ExitStack() as stack:
        execute=server.SidecarServer.execute;original_serve=server.SidecarServer.serve
        def measured_execute(self, message, **kwargs):
            # Exclude setup/provision/deploy costs; retain the running interval
            # and the reads which deliver its final pause or failure to the UI.
            if message['method']=='tactical.realtime.resume':recorder.active=True
            return recorder.timed(execute,'execute.'+message['method'])(self,message,**kwargs)
        def measured_serve(self, input_stream, output_stream):
            return original_serve(self,input_stream,TimedOutput(output_stream,recorder))
        labels=iter(('input_queue','output_queue'))
        def queue(maxsize):return TimedQueue(maxsize,recorder,next(labels))
        encode=recorder.timed(server.encode_message,'response.encode')
        stack.enter_context(patch.object(server,'Queue',queue))
        stack.enter_context(patch.object(server,'encode_message',encode))
        # Frozen methods use a separate namespace; patch the diagnostic hooks
        # there too so reference and candidate account for the same boundaries.
        stack.enter_context(patch.dict(comparison.REFERENCES['server'],Queue=queue,encode_message=encode))
        stack.enter_context(patch.object(server.SidecarServer,'execute',measured_execute))
        stack.enter_context(patch.object(server.SidecarServer,'serve',measured_serve))
        for owner,name,label in ((tactical_scheduler.TacticalScheduler,'pump','scheduler.pump'),
            (realtime_view.RealtimeViewService,'publish','view.publish'),(realtime_view.RealtimeViewService,'read','view.read')):
            stack.enter_context(patch.object(owner,name,recorder.timed(getattr(owner,name),label)))
        try:return live.serve(source,out)
        finally:
            write(out/'path.json',dict(metrics=recorder.report(),sample_limit=recorder.limit,
                scope='Instrumented real sidecar. Starts at resume; nested timings must not be added together. Queue residence excludes blocked put; put includes it. stdio excludes browser delivery. Recent percentiles retain at most 2048 samples; count/mean/max cover all recorded samples.'))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('source',type=Path);parser.add_argument('out',type=Path)
    parser.add_argument('--legacy',action='store_true');args=parser.parse_args()
    raise SystemExit(serve(args.source,args.out,args.legacy))
