"""P1b capability reuse: frozen method oracle, then separate paired timing.

Both variants use the current P1a manual-chaff rule, damage and EW pipeline.
The reference replaces only controller computation and disables the new scope.
"""
import argparse
import ast
import json
from dataclasses import replace
from contextlib import contextmanager, ExitStack, nullcontext
from hashlib import sha256
from pathlib import Path
import subprocess
from unittest.mock import patch

from backend.high_wilderness_sidecar import tactical_observation as obs, realtime_view, server
from backend.high_wilderness_sidecar.persistent_ship import canonical_sha256
from tools import profile_tactical_7c as baseline
from tools.profile_tactical_7c4 import PublicOracle, request
from tools.profile_tactical_7b import fingerprint
from tools.tactical_7b_fixture import ROOT, write

REFERENCE_HEAD='aa301d3c409fd3a96690ad292ca43b8032805cd7'
REFERENCE_PATH=ROOT/'tools/tactical_p1b_reference.py'
reference_globals=dict(obs.__dict__)
exec(compile(REFERENCE_PATH.read_text(encoding='utf-8'),str(REFERENCE_PATH),'exec'),reference_globals)
PUBLIC_ORACLE=None
WIRE_TIMING=False


def validate_reference():
    source=subprocess.check_output(['git','show',REFERENCE_HEAD+':backend/high_wilderness_sidecar/tactical_observation.py'],cwd=ROOT).decode('utf-8')
    tree=ast.parse(source)
    original={'_Inputs':next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='_Inputs')}
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='ObservationRuntime')
    original['controllers']=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='controllers')
    frozen={n.name:n for n in ast.parse(REFERENCE_PATH.read_text(encoding='utf-8')).body}
    assert {k:ast.dump(v) for k,v in original.items()}=={k:ast.dump(v) for k,v in frozen.items()}


@contextmanager
def implementation(legacy=False,counters=None):
    methods={name:getattr(obs.ObservationRuntime,name) for name in ('controllers','sources','defense_sources','ready','effect')}
    if legacy:methods['controllers']=reference_globals['controllers']
    hypot=obs.hypot;original_read=realtime_view.RealtimeViewService.read
    def measured(name,fn):
        def call(*args,**kw):
            counters[name]+=1
            return fn(*args,**kw)
        return call
    def read(self,*args,**kw):
        packet=original_read(self,*args,**kw)
        if PUBLIC_ORACLE is not None:PUBLIC_ORACLE.record(legacy,packet,self)
        if WIRE_TIMING:server.realtime_response(request(),packet,wire=True)
        return packet
    with ExitStack() as stack:
        if legacy:
            stack.enter_context(patch.object(obs,'_Inputs',reference_globals['_Inputs']))
            stack.enter_context(patch.object(obs.ObservationRuntime,'capability_stage',lambda *a:nullcontext()))
        for name,fn in methods.items():
            stack.enter_context(patch.object(obs.ObservationRuntime,name,measured(name,fn) if counters is not None else fn))
        if counters is not None:
            wrapped=measured('observation_hypot',hypot)
            stack.enter_context(patch.object(obs,'hypot',wrapped))
            stack.enter_context(patch.dict(reference_globals,hypot=wrapped))
        if PUBLIC_ORACLE is not None or WIRE_TIMING:
            stack.enter_context(patch.object(realtime_view.RealtimeViewService,'read',read))
        yield


def provenance():
    value=fingerprint()
    value.update(reference_head=REFERENCE_HEAD,reference_ast_verified=True,
        comparison='Pre-P1b controller methods vs stage reuse; P1a chaff rule identical in both',
        tools={p.relative_to(ROOT).as_posix():sha256(p.read_bytes()).hexdigest()
            for p in (ROOT/'tools').glob('*.py') if any(k in p.name for k in ('7b','7c','p1b','chaff'))},
        read_timing_scope='Read plus response validation/encoding; excludes physical transport and UI')
    return value


def verify_chaff(source,out):
    """Use P1a's frozen ships, with an explicit release/expiry command tape."""
    from tempfile import TemporaryDirectory
    from backend.high_wilderness_sidecar import battle_preparation as bp,tactical_encounter as encounter
    from backend.high_wilderness_sidecar.sessions import ResourceIndex
    from tools.tactical_7c_oracle import snapshot,difference,digests
    from tools.tactical_chaff_fixture import CHAFF,RADAR
    from backend.high_wilderness_sidecar.tactical_encounter import INTERFACE
    frozen=json.loads((source/'fixture.json').read_text(encoding='utf-8'))
    sides=[]
    for side in frozen['scene']['sides']:
        key=side['id']
        sides.append(dict(side_id='side.'+key,fleet_id='fleet.chaff.'+key,
            flagship_instance_id=side['flagship_instance_id'],ships=[dict(instance_id=m['instance_id'],revision=next(
                row['record']['state']['revision'] for row in frozen['ships'] if row['record']['state']['instance_id']==m['instance_id']),
                deployment=dict(x_m=m['x_m'],y_m=m['y_m']+(5000. if key=='enemy' else 0.),heading_rad=m['heading_rad'])) for m in side['ships']]))
    req=dict(interface=INTERFACE,encounter_id='encounter.chaff',world_id='world.chaff',world_revision=0,player_side_id='side.player',sides=sides)
    inputs=dict(ships=frozen['ships'],requests={'fixed':req},commands=[{'step':95,'kind':'manual_chaff'},{'step':96,'kind':'force_expiry_boundary'}])
    write(out/'inputs.json',inputs)
    tape=[];checkpoints=[]
    with TemporaryDirectory(prefix='hw-p1b-chaff-') as temp:
        runs=[]
        for legacy in (True,False):
            with implementation(legacy):
                s=realtime_view.RealtimeViewService('backend.chaff.compare',settlement_dir=Path(temp)/str(legacy))
                index=ResourceIndex(ROOT);rows=[]
                # P1a saved the new-record inputs before store persistence (revision
                # zero), as does its unit fixture. Rebuild through that same entry.
                for side in req['sides']:
                    for member in side['ships']:
                        row=next(r for r in inputs['ships'] if r['record']['state']['instance_id']==member['instance_id'])
                        d=bp.restore_design(row['archive'],index)
                        rows.append((side,member,d,bp.validate_record(row['record'],d)))
                template,scenario,_=s._template()
                b,_,_=encounter.build(req,rows,template,scenario);b.enemy_fire=False;runs.append(b)
        for step in range(200):
            states=[]
            for variant,b in enumerate(runs):
                with implementation(variant==0):
                    if step==95:b.ew.submit(dict(epoch=b.session.world.epoch,generation=0,sequence=1,
                        ship_id=b.session.world.ships[b._direct_index].ship_id,module_id=CHAFF,bearing_deg=0.))
                    if step==96:b.ew.effects=tuple(replace(e,expires=b.session.world.fixed_step+1) for e in b.ew.effects)
                    b.step();states.append(snapshot(b))
                assert b.observation._stage_inputs is None
            if error:=difference(*states):
                write(out/'mismatch.json',dict(step=step+1,difference=error));raise AssertionError(error)
            tape.append(digests(states[0]))
            if step in (94,95,96,199):
                b=runs[1];n=b._direct_index;quality=next(s.quality for g,s in zip(b.guns,b.states) if g.ship_index==n)
                used=next(d['used'] for d in b.observation.frame.devices if d['ship_index']==n and d['module_id']==RADAR)
                checkpoints.append(dict(step=step+1,quality=quality,radar_used=used,shots=b.ew.states[n,CHAFF].shots))
        assert [r['quality'] for r in checkpoints]==['normal','degraded','degraded','normal']
        assert checkpoints[1]['radar_used']==0 and checkpoints[2]['radar_used']>0
        assert checkpoints[-1]['shots']==1
    write(out/'expanded-tape.json',tape)
    write(out/'verification.json',dict(steps=200,sections=list(tape[-1]),source_sha256=canonical_sha256(frozen),
        inputs_sha256=canonical_sha256(inputs),tape_sha256=canonical_sha256(tape),checkpoints=checkpoints,
        scope='Same P1a manual release in both variants; shortened expiry explicitly injected only to test the boundary.'))
    print(json.dumps(dict(chaff_matched_steps=200,sections=len(tape[-1]),checkpoints=checkpoints)),flush=True)


def main():
    global PUBLIC_ORACLE,WIRE_TIMING
    parser=argparse.ArgumentParser()
    parser.add_argument('mode',choices=('verify','verify-chaff','measure','interleaved','paced'))
    parser.add_argument('source',type=Path);parser.add_argument('out',type=Path)
    parser.add_argument('--steps',type=int,default=900);parser.add_argument('--pairs',type=int,default=3)
    args=parser.parse_args()
    if args.out.exists():raise ValueError('Choose a fresh output directory')
    if args.pairs<1:raise ValueError('At least one pair is required')
    validate_reference();args.out.mkdir(parents=True);write(args.out/'source.json',provenance())
    with patch.object(baseline,'implementation',implementation):
        if args.mode=='verify-chaff':verify_chaff(args.source,args.out)
        elif args.mode=='verify':
            PUBLIC_ORACLE=PublicOracle(args.out)
            baseline.verify(args.source,args.out,args.steps)
            assert not PUBLIC_ORACLE.pending
            write(args.out/'publications.json',dict(matched_reads=len(PUBLIC_ORACLE.tape),sha256=canonical_sha256(PUBLIC_ORACLE.tape),tape=PUBLIC_ORACLE.tape))
        elif args.mode=='measure':
            WIRE_TIMING=True
            baseline.measure(args.source,args.out,args.pairs)
        elif args.mode=='interleaved':baseline.interleaved(args.source,args.out)
        else:
            from tools.profile_tactical_7c4b import run
            from backend.high_wilderness_sidecar.tactical_speed import SpeedPolicy
            # Existing paced runner includes read/encode and debt accounting.
            for pair in range(args.pairs):
                for legacy in ((True,False) if pair%2==0 else (False,True)):
                    for mode in ('fixed','adaptive'):
                        from backend.high_wilderness_sidecar import tactical_point_defense as pd,missile_flight as mf
                        from backend.high_wilderness_sidecar import tactical_ballistics as ballistics,tactical_drag_prediction as drag
                        for cached in (pd.trajectory,mf.segment,ballistics.reference_range,drag.bands):cached.cache_clear()
                        with implementation(legacy):
                            run(args.source,args.out,f'{pair+1}-{"reference" if legacy else "candidate"}-{mode}',
                                SpeedPolicy(mode=mode),verify=False,steps=args.steps)


if __name__=='__main__':main()
