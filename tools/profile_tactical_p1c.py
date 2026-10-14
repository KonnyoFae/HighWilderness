"""P1c exact collision comparison; diagnostics separate from paired timing."""
import argparse
import ast
from contextlib import contextmanager, ExitStack
from hashlib import sha256
from pathlib import Path
import subprocess
from unittest.mock import patch

from backend.high_wilderness_sidecar import tactical_damage as damage, realtime_view, server
from backend.high_wilderness_sidecar.persistent_ship import canonical_sha256
from tools import profile_tactical_7c as baseline
from tools.profile_tactical_7c4 import PublicOracle, request
from tools.profile_tactical_7b import fingerprint
from tools.tactical_7b_fixture import ROOT, write

REFERENCE_HEAD='ad3870cabef6a4aa0c6d8e6fd8f67601e11ae02c'
REFERENCE_PATH=ROOT/'tools/tactical_p1c_reference.py'
reference_globals=dict(damage.__dict__)
exec(compile(REFERENCE_PATH.read_text(encoding='utf-8'),str(REFERENCE_PATH),'exec'),reference_globals)
PUBLIC_ORACLE=None
WIRE_TIMING=False


def validate_reference():
    source=subprocess.check_output(['git','show',REFERENCE_HEAD+':backend/high_wilderness_sidecar/tactical_damage.py'],cwd=ROOT).decode('utf-8')
    cls=next(n for n in ast.parse(source).body if isinstance(n,ast.ClassDef) and n.name=='DamageKernel')
    original=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='advance')
    frozen=next(n for n in ast.parse(REFERENCE_PATH.read_text(encoding='utf-8')).body if isinstance(n,ast.FunctionDef))
    assert ast.dump(original)==ast.dump(frozen), 'Frozen collision method differs from committed source'


@contextmanager
def implementation(legacy=False,counters=None):
    original_read=realtime_view.RealtimeViewService.read
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
        if legacy:stack.enter_context(patch.object(damage.DamageKernel,'advance',reference_globals['advance']))
        if counters is not None:
            for name in ('layer_breaks','ship_layer_boundary','layer_at','flight_segment','local_path','segment','_segment_aabb_entry_fraction'):
                fn=measured(name,getattr(damage,name))
                stack.enter_context(patch.object(damage,name,fn))
                stack.enter_context(patch.dict(reference_globals,{name:fn}))
            for name in ('contacts_on_path','choose_deck'):
                stack.enter_context(patch.object(damage.DamageKernel,name,measured(name,getattr(damage.DamageKernel,name))))
        if PUBLIC_ORACLE is not None or WIRE_TIMING:
            stack.enter_context(patch.object(realtime_view.RealtimeViewService,'read',read))
        yield


def provenance():
    value=fingerprint()
    value.update(reference_head=REFERENCE_HEAD,reference_ast_verified=True,
        comparison='Committed P1b collision method vs qualification filtering and per-projectile layer breaks; all other rules identical',
        tools={p.relative_to(ROOT).as_posix():sha256(p.read_bytes()).hexdigest()
            for p in (ROOT/'tools').glob('*.py') if any(k in p.name for k in ('7b','7c','p1b','p1c','chaff'))},
        read_timing_scope='Read plus response validation/encoding; excludes physical transport and UI')
    return value


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
        if args.mode=='verify-chaff':
            from tools import profile_tactical_p1b as prior
            with patch.object(prior,'implementation',implementation):prior.verify_chaff(args.source,args.out)
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
            for pair in range(args.pairs):
                for legacy in ((True,False) if pair%2==0 else (False,True)):
                    for mode in ('fixed','adaptive'):
                        from backend.high_wilderness_sidecar import tactical_point_defense as pd,missile_flight as mf
                        from backend.high_wilderness_sidecar import tactical_ballistics as ballistics,tactical_drag_prediction as drag
                        for cached in (pd.trajectory,mf.segment,ballistics.reference_range,drag.bands):cached.cache_clear()
                        with implementation(legacy):
                            run(args.source,args.out,f'{pair+1}-{"reference" if legacy else "candidate"}-{mode}',
                                SpeedPolicy(mode=mode),verify=False,steps=args.steps,wall_limit=300)


if __name__=='__main__':main()
