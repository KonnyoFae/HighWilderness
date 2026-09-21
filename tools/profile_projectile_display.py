"""D0/D1 repeatable A5 scenes; isolated stores, no live battle or inventory."""
import argparse
from collections import defaultdict
from contextlib import ExitStack
from hashlib import sha256
import json
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

from tools.verify_hull_armor_joint import workload, ResourceIndex, ROOT
from tools.profile_tactical_fire_control import stats
from backend.high_wilderness_sidecar import realtime_view as realtime, tactical_presentation as presentation
from backend.high_wilderness_sidecar.projectile_stream import INTERFACE


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--stream', action='store_true')
    parser.add_argument('--replay', action='store_true', help='Capture paired frontend inputs separately from performance runs')
    args=parser.parse_args();args.out.mkdir(parents=True,exist_ok=True)
    measurements=defaultdict(list);packets=[];peak=None;cursors={};replay=[];replay_cursor=None;replay_service=None;raw_histories={}
    source_paths=[ROOT/'backend/high_wilderness_sidecar'/name for name in
        ('tactical_presentation.py','shell_presentation.py','projectile_stream.py','realtime_view.py','tactical_gunnery.py')]
    hashes={str(p.relative_to(ROOT)):sha256(p.read_bytes()).hexdigest() for p in source_paths}
    read=realtime.RealtimeViewService.read
    def inspect(service,known=None,**kwargs):
        nonlocal peak,replay_cursor,replay_service
        start=perf_counter()
        if args.stream and known is not None:
            value=read(service,known,display={'interface':INTERFACE,
                'after_sequence':cursors.get(service)})
            cursors[service]=value['view']['projectile_stream']['sequence']
        else:value=read(service,known,**kwargs)
        measurements['read'].append(perf_counter()-start)
        if known is not None:
            wire=json.dumps(value,ensure_ascii=False).encode('utf-8')
            view=value['view'];g=view.get('gunnery',{})
            projectile=dict(projectiles=g.get('projectiles',[]),presentation=view.get('presentation'),stream=view.get('projectile_stream'))
            row=dict(step=view['fixed_step'],bytes=len(wire),projectile_bytes=len(json.dumps(projectile,ensure_ascii=False).encode('utf-8')),
                active=len(g.get('projectiles',[])))
            packets.append(row)
            if peak is None or row['bytes']>peak[0]:peak=(row['bytes'],value)
            if args.replay and row['active']>=100 and len(replay)<8 and (replay_service is None or replay_service is service):
                replay_service=service
                old=read(service,service.digest,legacy=True)
                raw=raw_histories[id(service.presentation)]
                old['view']['presentation']=raw.view()
                for projectile in old['view']['gunnery']['projectiles']:
                    projectile.pop('shell_samples',None)
                    projectile.update(raw.launch(projectile['id']))
                new=read(service,service.digest,display={'interface':INTERFACE,'after_sequence':replay_cursor})
                replay_cursor=new['view']['projectile_stream']['sequence']
                replay.append(dict(legacy=old,stream=new))
                if len(replay)==1:replay[0]['geometry']=service.geometry
        return value
    def instrument(stack,owner,name,key):
        original=getattr(owner,name)
        def measured(*a,**kw):
            start=perf_counter()
            try:return original(*a,**kw)
            finally:measurements[key].append(perf_counter()-start)
        stack.enter_context(patch.object(owner,name,measured))
    with ExitStack() as stack:
        if args.replay:
            original_record=presentation.FlightHistory.record
            def capture_raw(history,*a,**kw):
                raw=raw_histories.setdefault(id(history),presentation.FlightHistory())
                original_record(raw,*a,**kw)
                return original_record(history,*a,**kw)
            stack.enter_context(patch.object(presentation.FlightHistory,'record',capture_raw))
        instrument(stack,presentation.FlightHistory,'record','history_record')
        instrument(stack,presentation,'display_path','path_simplify')
        instrument(stack,realtime,'deepcopy','view_deepcopy')
        instrument(stack,realtime.RealtimeViewService,'publish','publish')
        stack.enter_context(patch.object(realtime.RealtimeViewService,'read',inspect))
        cases=workload(ResourceIndex(ROOT),7200)
    result=dict(scope='Two isolated A5 29-gun real battles; end naturally, not a 120-second sustained test. Nested timings are not additive.',
        mode='stream' if args.stream else 'legacy',source_hashes=hashes,
        timings={k:dict(calls=len(v),total_ms=sum(v)*1000,**stats(v)) for k,v in measurements.items()},
        bytes=dict(total=sum(p['bytes'] for p in packets),projectile_total=sum(p['projectile_bytes'] for p in packets),peak=max(p['bytes'] for p in packets)),
        cases=cases,publications=packets)
    (args.out/'report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    (args.out/'peak-packet.json').write_text(json.dumps(peak[1],ensure_ascii=False),encoding='utf-8')
    if replay:(args.out/'frontend-replay.json').write_text(json.dumps(replay,ensure_ascii=False),encoding='utf-8')
    print(json.dumps(dict(mode=result['mode'],bytes=result['bytes'],timings=result['timings']),ensure_ascii=False))


if __name__=='__main__':main()
