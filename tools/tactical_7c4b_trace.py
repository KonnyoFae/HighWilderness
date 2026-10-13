"""Opt-in command/publication trace; never imported by the production sidecar."""
import argparse
from pathlib import Path
from time import perf_counter
from unittest.mock import patch
from contextlib import ExitStack

from backend.high_wilderness_sidecar.server import SidecarServer
from backend.high_wilderness_sidecar.realtime_view import RealtimeViewService
from backend.high_wilderness_sidecar.tactical_scheduler import TacticalScheduler
from tools import tactical_7c4b_live
from tools.tactical_7b_fixture import write


def serve(source, out):
    events=[];active=False;dropped=0;current_request=None
    def record(kind, **fields):
        nonlocal dropped
        if not active:return
        if len(events)<20000:events.append(dict(kind=kind,at_ms=perf_counter()*1000,**fields))
        else:dropped+=1
    execute=SidecarServer.execute
    def traced_execute(self, message, **kwargs):
        nonlocal active,current_request
        method=message['method']
        if method=='tactical.realtime.resume':active=True
        start=perf_counter()*1000;current_request=message['request_id']
        try:return execute(self,message,**kwargs)
        finally:
            record('request',request_id=message['request_id'],method=method,start_ms=start)
            current_request=None
    submit=TacticalScheduler.submit
    def traced_submit(self,value):
        try:result=submit(self,value)
        except Exception:
            record('rejected',sequence=value.get('sequence'),target_step=value.get('target_step'),at_step=self._committed.fixed_step)
            raise
        record('accepted',sequence=result.sequence,target_step=result.target_step,at_step=result.accepted_at_step)
        return result
    resolve=TacticalScheduler._resolve
    def traced_resolve(self,sequence,status,reason=None):
        result=resolve(self,sequence,status,reason)
        record('resolved',sequence=sequence,status=status,step=self._committed.fixed_step)
        return result
    publish=RealtimeViewService.publish
    def traced_publish(self):
        start=perf_counter()*1000;result=publish(self)
        record('publication',start_ms=start,step=self.latest['fixed_step'],paused=self.latest['paused'])
        return result
    read=RealtimeViewService.read
    def traced_read(self,*args,**kwargs):
        start=perf_counter()*1000;result=read(self,*args,**kwargs)
        record('read',request_id=current_request,start_ms=start,view_step=result['view']['fixed_step'],authority_step=result['status']['fixed_step'],
            receipts=[dict(sequence=r['sequence'],status=r['status']) for r in result['receipts']])
        return result
    with ExitStack() as stack:
        for owner,name,fn in ((SidecarServer,'execute',traced_execute),(TacticalScheduler,'submit',traced_submit),
            (TacticalScheduler,'_resolve',traced_resolve),(RealtimeViewService,'publish',traced_publish),(RealtimeViewService,'read',traced_read)):
            stack.enter_context(patch.object(owner,name,fn))
        try:return tactical_7c4b_live.serve(source,out,'adaptive')
        finally:write(out/'trace.json',dict(events=events,dropped=dropped,
            scope='Diagnostic monotonic wall milliseconds in the Python process. Request start/end brackets correlate request IDs with Node; browser timestamps have a separate origin. Instrumentation adds overhead.'))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('source',type=Path);parser.add_argument('out',type=Path)
    args=parser.parse_args();raise SystemExit(serve(args.source,args.out))
