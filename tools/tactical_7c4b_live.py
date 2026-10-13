"""Real page fixture with explicit fixed/adaptive policy and bounded speed log."""
import argparse
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch
from backend.high_wilderness_sidecar.realtime_view import RealtimeViewService
from backend.high_wilderness_sidecar.tactical_speed import SpeedPolicy
from tools import tactical_7b_live as live
from tools.profile_tactical_7c4b import provenance
from tools.tactical_7b_fixture import write


def serve(source,out,mode):
    out.mkdir(parents=True,exist_ok=True);write(out/'source.json',provenance())
    original_init=RealtimeViewService.__init__;original_tick=RealtimeViewService.tick
    active=[None];changes=[];previous=[None];last_status=[None]
    def init(self,*args,**kwargs):
        kwargs['speed_policy']=SpeedPolicy(mode=mode);original_init(self,*args,**kwargs);active[0]=self
    def tick(self):
        result=original_tick(self)
        if self.scheduler is not None:
            last_status[0]=self.scheduler.status;speed=last_status[0].speed
            if previous[0]!=speed.revision and len(changes)<4096:
                changes.append(dict(wall_s=speed.active_wall_s,step=self.scheduler.status.fixed_step,rate=speed.effective));previous[0]=speed.revision
        return result
    with patch.object(RealtimeViewService,'__init__',init),patch.object(RealtimeViewService,'tick',tick):
        try:return live.serve(source,out)
        finally:
            s=active[0]
            if s and s.scheduler:
                # serve has joined the worker; read only its final metrics here.
                # The full guarded status was captured on the authority owner.
                q=s.scheduler;write(out/'speed.json',dict(policy=asdict(q._speed.policy),status=asdict(last_status[0]) if last_status[0] else None,
                    speed=asdict(q._speed.status()),changes=changes,
                    rate_wall_s={str(k/1000):v/1e9 for k,v in sorted(q._speed.rate_wall_ns.items())}))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('source',type=Path);p.add_argument('out',type=Path);p.add_argument('--fixed',action='store_true');a=p.parse_args()
    raise SystemExit(serve(a.source,a.out,'fixed' if a.fixed else 'adaptive'))
