"""Real sidecar scheduling with frozen 7b launch orders and passive timers only."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys
from time import perf_counter
from tools.tactical_7b_fixture import commands,write
from backend.high_wilderness_sidecar import battle_preparation as bp,tactical_test_scene as scene
from tools.profile_tactical_7b import counts
from tools.profile_tactical_fire_control import stats
from backend.high_wilderness_sidecar.server import SidecarServer
from backend.high_wilderness_sidecar.realtime_view import RealtimeViewService


def memory():
    import ctypes
    from ctypes import wintypes as w
    class Counters(ctypes.Structure):
        _fields_=[('cb',w.DWORD),('PageFaultCount',w.DWORD)]+[(key,ctypes.c_size_t) for key in (
            'PeakWorkingSetSize','WorkingSetSize','QuotaPeakPagedPoolUsage','QuotaPagedPoolUsage',
            'QuotaPeakNonPagedPoolUsage','QuotaNonPagedPoolUsage','PagefileUsage','PeakPagefileUsage','PrivateUsage')]
    c=Counters();c.cb=ctypes.sizeof(c)
    kernel=ctypes.WinDLL('kernel32');kernel.GetCurrentProcess.restype=w.HANDLE
    get=ctypes.WinDLL('psapi').GetProcessMemoryInfo;get.argtypes=[w.HANDLE,ctypes.POINTER(Counters),w.DWORD]
    if not get(kernel.GetCurrentProcess(),ctypes.byref(c),c.cb):raise ctypes.WinError()
    return dict(working_set=c.WorkingSetSize,private_bytes=c.PrivateUsage,peak_working_set=c.PeakWorkingSetSize)


def serve(source,out):
    frozen=json.loads((source/'fixture.json').read_text(encoding='utf-8'))
    out.mkdir(parents=True,exist_ok=True)
    # A fresh copy per run keeps frozen inputs and user saves untouched.
    if (out/'store').exists():raise ValueError('Choose a fresh live output directory')
    setup=SidecarServer('backend.7b.restore',settlement_dir=out/'store');p=setup.preparation;p.provision()
    for row in frozen['ships']:
        d=bp.restore_design(row['archive'],setup.editor.index);r=bp.validate_record(row['record'],d)
        p.store.create_ship(d,r['state']['instance_id'])
        with p.store.connection() as db:p.store._write_ship(db,r)
    layout=scene.fresh();layout.update(revision=1,distance_mode='manual',distance_m=6000.)
    for side in layout['sides']:
        req=next(s for s in frozen['requests']['fixed']['sides'] if s['side_id']=='side.'+side['id'])
        side['flagship_instance_id']=req['flagship_instance_id']
        origin=next(m['deployment'] for m in req['ships'] if m['instance_id']==req['flagship_instance_id'])
        side['ships']=[dict(instance_id=m['instance_id'],x_m=m['deployment']['x_m']-origin['x_m'],
            y_m=m['deployment']['y_m']-origin['y_m'],heading_rad=m['deployment']['heading_rad']) for m in req['ships']]
    scene.save(p,layout,0)
    original=RealtimeViewService._attach
    def attach(self,battle,geometry,key=None):
        costs={k:[] for k in ('step','publish','read')};started=[None];peak_debt=[0];baseline=memory()
        def meter(owner,name,label):
            fn=getattr(owner,name)
            def timed(*a,**kw):
                if label=='step':commands(battle,frozen)
                start=perf_counter()
                try:return fn(*a,**kw)
                finally:costs[label].append(perf_counter()-start)
            setattr(owner,name,timed)
        meter(battle,'step','step');meter(self,'publish','publish');meter(self,'read','read')
        result=original(self,battle,geometry,key);tick=self.tick;written=[False]
        def observed_tick():
            if self.running and started[0] is None:started[0]=perf_counter()
            tick();status=self.scheduler.status;peak_debt[0]=max(peak_debt[0],status.debt_quanta/1e9)
            if started[0] is not None and not status.running and not written[0]:
                written[0]=True
                write(out/'backend.json',dict(case=frozen['case'],wall_s=perf_counter()-started[0],
                    simulated_s=status.fixed_step/60,status=asdict(status),peak_debt_steps=peak_debt[0],
                    costs={k:stats(v) for k,v in costs.items()},load=counts(battle),error=self.error,
                    memory_before=baseline,memory_end=memory(),scope='Real scheduler/sidecar. Headless browser bridge, not native Tauri.'))
        self.tick=observed_tick
        return result
    RealtimeViewService._attach=attach
    return SidecarServer('backend.e3bbrowser',settlement_dir=out/'store').serve(sys.stdin.buffer,sys.stdout.buffer)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('source',type=Path);p.add_argument('out',type=Path);a=p.parse_args()
    raise SystemExit(serve(a.source,a.out))
